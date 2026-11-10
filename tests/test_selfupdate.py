"""self-update: fake GitHub, fake commands, temp dirs only — no network, no launchd, no real Trash."""
import contextlib
import hashlib
import io
import json
import os
import plistlib
import shutil
import sys
import tempfile
import unittest
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)
import maccleaner as mc  # noqa: E402
import mclib  # noqa: E402
from mclib.features import selfupdate as SU  # noqa: E402

NEXT = "99.0.0"   # always newer than the real version
ZIP = f"MacCleaner-{NEXT}-macos-universal.zip"


class Resp(io.BytesIO):
    def __init__(self, data, url):
        super().__init__(data)
        self._url = url

    def geturl(self):
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def app_zip(version=NEXT, bundle_id=SU.BUNDLE_ID):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("MacCleaner.app/Contents/Info.plist",
                   plistlib.dumps({"CFBundleIdentifier": bundle_id, "CFBundleShortVersionString": version}))
        z.writestr("MacCleaner.app/Contents/Resources/maccleaner.py", "# new\n")
    return buf.getvalue()


class Fake:
    """Env with a scripted GitHub and recorded commands."""

    def __init__(self, tmp, kind="other", tag="v" + NEXT, blob=None, sha=None, redirect=None):
        self.calls, self.requests, self.trashed = [], [], []
        self.status_out = ""        # `git status --porcelain` output
        self.fail = None            # substring of a command that should fail
        self.blob = app_zip() if blob is None else blob
        self.sha = hashlib.sha256(self.blob).hexdigest() if sha is None else sha
        self.tag, self.redirect = tag, redirect
        self.home, self.data = os.path.join(tmp, "home"), os.path.join(tmp, "data")
        self.root = {"other": os.path.join(tmp, "loose"), "source": os.path.join(tmp, "clone"),
                     "app": os.path.join(tmp, "Apps", "MacCleaner.app", "Contents", "Resources")}[kind]
        os.makedirs(self.root)
        if kind == "source":
            os.makedirs(os.path.join(self.root, ".git"))
        if kind == "app":
            with open(os.path.join(self.root, "old.txt"), "w") as f:
                f.write("old")
        self.env = SU.Env(root=self.root, data=self.data, home=self.home, opener=self.opener, call=self.call,
                          trash=self.trash, running=lambda: set())

    def opener(self, req, timeout=None):
        url = req.full_url
        self.requests.append(url)
        if url == SU.API:
            base = SU.DOWNLOAD_PREFIX + self.tag + "/"
            return Resp(json.dumps({"tag_name": self.tag, "assets": [
                {"name": ZIP, "browser_download_url": base + ZIP},
                {"name": ZIP + ".sha256", "browser_download_url": base + ZIP + ".sha256"}]}).encode(), url)
        final = self.redirect or "https://objects.githubusercontent.com/x"
        return Resp((self.sha + "  " + ZIP + "\n").encode() if url.endswith(".sha256") else self.blob, final)

    def call(self, cmd, timeout=60, cwd=None):
        self.calls.append(cmd)
        if self.fail and self.fail in " ".join(cmd):
            return 1, "boom"
        if cmd[0] == SU.DITTO:   # real extraction of the fake zip, into the temp dir
            with zipfile.ZipFile(cmd[3]) as z:
                z.extractall(cmd[4])
        if cmd[0] == SU.GIT and "status" in cmd:
            return 0, self.status_out
        return 0, ""

    def trash(self, path):
        self.trashed.append(path)
        shutil.rmtree(path)
        return None


class Version(unittest.TestCase):
    def test_one_version_everywhere(self):
        with open(os.path.join(REPO, ".claude-plugin", "plugin.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["version"], mclib.__version__)
        with open(os.path.join(REPO, "scripts", "make-app.sh"), encoding="utf-8") as f:
            self.assertIn("mclib/__init__.py", f.read())  # the app version is read from mclib, not typed again

    def test_install_kind(self):
        with tempfile.TemporaryDirectory() as d:
            for kind in ("other", "source", "app"):
                self.assertEqual(SU.install_kind(Fake(os.path.join(d, kind), kind).root), kind)


class Check(unittest.TestCase):
    def test_new_release_found_and_remembered(self):
        with tempfile.TemporaryDirectory() as d:
            f = Fake(d)
            last, rel = SU.check(f.env, now=123)
            self.assertEqual((last["latest"], last["update"], last["checked_at"]), (NEXT, True, 123))
            self.assertEqual(rel["tag"], "v" + NEXT)
            self.assertEqual(SU.status(f.env)["last"]["latest"], NEXT)

    def test_same_or_older_release_is_not_an_update(self):
        with tempfile.TemporaryDirectory() as d:
            for tag in ("v" + mclib.__version__, "v0.0.1"):
                self.assertFalse(SU.check(Fake(os.path.join(d, tag), tag=tag).env)[0]["update"])

    def test_bad_tag_and_foreign_host_are_errors(self):
        with tempfile.TemporaryDirectory() as d:
            last, rel = SU.check(Fake(os.path.join(d, "a"), tag="v1.0.0; rm -rf ~").env)
            self.assertIn("vX.Y.Z", last["error"])
            self.assertIsNone(rel)
        self.assertFalse(SU._host_ok("http://github.com/x"))
        self.assertFalse(SU._host_ok("https://github.com.evil.example/x"))
        self.assertTrue(SU._host_ok("https://release-assets.githubusercontent.com/x"))


class ApplyApp(unittest.TestCase):
    def test_preview_downloads_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            f = Fake(d, "app")
            _, rel = SU.check(f.env)
            res = SU.apply(f.env, rel, yes=False, log=lambda m: None)
            self.assertEqual(res["status"], "preview")
            self.assertEqual(f.requests, [SU.API])
            self.assertEqual(f.trashed, [])

    def test_replaces_app_after_all_checks(self):
        with tempfile.TemporaryDirectory() as d:
            f = Fake(d, "app")
            _, rel = SU.check(f.env)
            res = SU.apply(f.env, rel, yes=True, log=lambda m: None)
            app = os.path.dirname(os.path.dirname(f.root))
            self.assertEqual((res["status"], res["restart"]), ("ok", True))
            self.assertEqual(f.trashed, [app])
            self.assertTrue(os.path.exists(os.path.join(f.root, "maccleaner.py")))
            self.assertFalse(os.path.exists(os.path.join(f.root, "old.txt")))
            self.assertTrue(any(c[0] == SU.CODESIGN for c in f.calls))

    def test_rejected_downloads_leave_the_old_app(self):
        cases = {"sha": dict(sha="0" * 64), "bundle id": dict(blob=app_zip(bundle_id="com.evil")),
                 "version": dict(blob=app_zip(version="1.0.0")), "host": dict(redirect="https://evil.example/x")}
        for name, kw in cases.items():
            with tempfile.TemporaryDirectory() as d:
                f = Fake(d, "app", **kw)
                _, rel = SU.check(f.env)
                with self.assertRaises(SU.UpdateError, msg=name):
                    SU.apply(f.env, rel, yes=True, log=lambda m: None)
                self.assertEqual(f.trashed, [], name)
                self.assertTrue(os.path.exists(os.path.join(f.root, "old.txt")), name)

    def test_bad_signature_leaves_the_old_app(self):
        with tempfile.TemporaryDirectory() as d:
            f = Fake(d, "app")
            f.fail = "codesign"
            _, rel = SU.check(f.env)
            with self.assertRaises(SU.UpdateError):
                SU.apply(f.env, rel, yes=True, log=lambda m: None)
            self.assertEqual(f.trashed, [])


class ApplySource(unittest.TestCase):
    def test_preview_then_real_run(self):
        with tempfile.TemporaryDirectory() as d:
            f = Fake(d, "source")
            _, rel = SU.check(f.env)
            self.assertEqual(SU.apply(f.env, rel, log=lambda m: None)["status"], "preview")
            self.assertEqual(len(f.calls), 1)  # only `git status`
            SU.apply(f.env, rel, yes=True, log=lambda m: None)
            self.assertIn([SU.GIT, "-C", f.root, "merge", "--ff-only", "refs/tags/v" + NEXT], f.calls)
            self.assertEqual(f.calls[-1], ["/bin/bash", os.path.join(f.root, "install.sh")])

    def test_dirty_tree_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as d:
            f = Fake(d, "source")
            f.status_out = " M maccleaner.py"
            _, rel = SU.check(f.env)
            with self.assertRaises(SU.UpdateError):
                SU.apply(f.env, rel, yes=True, log=lambda m: None)
            self.assertEqual(len(f.calls), 1)

    def test_other_install_kind_only_points_to_the_release(self):
        with tempfile.TemporaryDirectory() as d:
            f = Fake(d)
            _, rel = SU.check(f.env)
            with self.assertRaises(SU.UpdateError) as cm:
                SU.apply(f.env, rel, yes=True)
            self.assertIn(rel["url"], str(cm.exception))


class Schedule(unittest.TestCase):
    def test_agent_calendar(self):
        with tempfile.TemporaryDirectory() as d:
            env = Fake(d).env
            self.assertEqual(SU.build_agent(env, "daily", "7:05")["StartCalendarInterval"], {"Hour": 7, "Minute": 5})
            self.assertEqual(SU.build_agent(env, "weekly", "23:59")["StartCalendarInterval"], {"Hour": 23, "Minute": 59, "Weekday": 1})
            for every, at in (("hourly", "09:00"), ("daily", "24:00"), ("daily", "9"), ("daily", "09:60")):
                with self.assertRaises(SU.UpdateError):
                    SU.build_agent(env, every, at)

    def test_without_consent_nothing_is_written(self):
        with tempfile.TemporaryDirectory() as d:
            f = Fake(d)
            res = SU.auto_on(f.env, "weekly", "08:30", True, yes=False)
            self.assertEqual(res["status"], "preview")
            self.assertEqual((f.calls, os.path.exists(SU.agent_path(f.env)), SU.status(f.env)["auto"]), ([], False, False))

    def test_on_then_off(self):
        with tempfile.TemporaryDirectory() as d:
            f = Fake(d)
            SU.auto_on(f.env, "weekly", "08:30", True, yes=True)
            with open(SU.agent_path(f.env), "rb") as fh:
                agent = plistlib.load(fh)
            self.assertEqual(agent["ProgramArguments"][-3:], [os.path.join(f.root, "maccleaner.py"), "self-update", "run"])
            self.assertEqual(agent["EnvironmentVariables"]["MACCLEANER_DATA"], f.data)
            st = SU.status(f.env)
            self.assertEqual((st["auto"], st["every"], st["at"], st["install"], st["agent"]), (True, "weekly", "08:30", True, True))
            self.assertEqual([c[1] for c in f.calls], ["bootout", "bootstrap"])
            SU.auto_off(f.env)
            st = SU.status(f.env)
            self.assertEqual((st["auto"], st["agent"]), (False, False))

    def test_launchd_refusal_rolls_back(self):
        with tempfile.TemporaryDirectory() as d:
            f = Fake(d)
            f.fail = "bootstrap"
            with self.assertRaises(SU.UpdateError):
                SU.auto_on(f.env, yes=True)
            self.assertEqual((os.path.exists(SU.agent_path(f.env)), SU.status(f.env)["auto"]), (False, False))


class Run(unittest.TestCase):
    def test_off_means_no_request(self):
        with tempfile.TemporaryDirectory() as d:
            f = Fake(d, "app")
            self.assertEqual(SU.run(f.env, log=lambda m: None), 0)
            self.assertEqual((f.requests, f.calls), ([], []))

    def test_notify_only_unless_install_was_chosen(self):
        with tempfile.TemporaryDirectory() as d:
            f = Fake(d, "app")
            SU.auto_on(f.env, yes=True)
            self.assertEqual(SU.run(f.env, log=lambda m: None), 0)
            self.assertEqual(f.trashed, [])
            self.assertEqual(f.calls[-1][0], SU.OSASCRIPT)

    def test_installs_when_chosen_but_not_under_a_running_app(self):
        with tempfile.TemporaryDirectory() as d:
            f = Fake(d, "app")
            SU.auto_on(f.env, install=True, yes=True)
            f.env.running = lambda: {SU.BUNDLE_ID}
            SU.run(f.env, log=lambda m: None)
            self.assertEqual(f.trashed, [])
            f.env.running = lambda: set()
            self.assertEqual(SU.run(f.env, log=lambda m: None), 0)
            self.assertEqual(len(f.trashed), 1)


class Cli(unittest.TestCase):
    def test_status_json_uses_no_network(self):
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as d, contextlib.redirect_stdout(out):
            env = Fake(d).env
            args = type("A", (), {"action": "status", "json": True})()
            self.assertEqual(SU.cli(args, env), 0)
        st = json.loads(out.getvalue())
        self.assertEqual((st["version"], st["auto"], st["last"]), (mclib.__version__, False, None))

    def test_registered_as_a_subcommand(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit):
            mc.main(["self-update", "auto", "sometimes"])
        self.assertIn("invalid choice", err.getvalue())


if __name__ == "__main__":
    unittest.main()
