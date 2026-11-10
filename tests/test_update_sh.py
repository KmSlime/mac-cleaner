"""update.sh: a local file:// release, a temp HOME (so the Trash is a temp dir too) — no network, no real app touched."""
import hashlib
import os
import plistlib
import shutil
import subprocess
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO, "update.sh")
NEXT = "99.0.0"
ZIP = f"MacCleaner-{NEXT}-macos-universal.zip"


def make_app(path, version, bundle_id="local.maccleaner", sign=False):
    os.makedirs(os.path.join(path, "Contents", "MacOS"))
    os.makedirs(os.path.join(path, "Contents", "Resources"))
    with open(os.path.join(path, "Contents", "Info.plist"), "wb") as f:
        plistlib.dump({"CFBundleIdentifier": bundle_id, "CFBundleShortVersionString": version,
                       "CFBundleExecutable": "MacCleaner", "CFBundlePackageType": "APPL"}, f)
    shutil.copy("/usr/bin/true", os.path.join(path, "Contents", "MacOS", "MacCleaner"))
    with open(os.path.join(path, "Contents", "Resources", "maccleaner.py"), "w") as f:
        f.write(f"# {version}\n")
    if sign:
        subprocess.run(["codesign", "--force", "--sign", "-", path], check=True, capture_output=True)


def version_of(app):
    with open(os.path.join(app, "Contents", "Info.plist"), "rb") as f:
        return plistlib.load(f)["CFBundleShortVersionString"]


@unittest.skipUnless(shutil.which("codesign") and shutil.which("ditto"), "needs macOS codesign + ditto")
class UpdateScript(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fix = tempfile.mkdtemp(dir="/tmp", prefix="mc-upd-fix.")
        cls.addClassCleanup(shutil.rmtree, cls.fix, True)
        cls.releases = {}
        for kind in ("good", "tampered", "foreign"):
            src = os.path.join(cls.fix, kind, "src", "MacCleaner.app")
            make_app(src, NEXT, bundle_id="com.example.other" if kind == "foreign" else "local.maccleaner", sign=True)
            if kind == "tampered":   # changed after signing: the signature no longer matches
                with open(os.path.join(src, "Contents", "Resources", "maccleaner.py"), "a") as f:
                    f.write("# injected\n")
            out = os.path.join(cls.fix, kind, "releases", "download", "v" + NEXT)
            os.makedirs(out)
            subprocess.run(["ditto", "-c", "-k", "--keepParent", src, os.path.join(out, ZIP)], check=True)
            with open(os.path.join(out, ZIP), "rb") as f:
                digest = hashlib.sha256(f.read()).hexdigest()
            with open(os.path.join(out, ZIP + ".sha256"), "w") as f:
                f.write(f"{digest}  {ZIP}\n")
            cls.releases[kind] = os.path.join(cls.fix, kind, "releases")

    def setUp(self):
        self.home = tempfile.mkdtemp(dir="/tmp", prefix="mc-upd-home.")
        self.addCleanup(shutil.rmtree, self.home, True)
        self.app = os.path.join(self.home, "Applications", "MacCleaner.app")

    def run_update(self, *args, release="good", tag="v" + NEXT):
        env = dict(os.environ, HOME=self.home, MACCLEANER_RELEASES="file://" + self.releases[release], MACCLEANER_TAG=tag)
        return subprocess.run(["bash", SCRIPT, *args], env=env, capture_output=True, text=True, timeout=60)

    def trash(self):
        d = os.path.join(self.home, ".Trash")
        return sorted(os.listdir(d)) if os.path.isdir(d) else []

    def test_an_old_app_without_self_update_is_replaced_and_kept_in_the_trash(self):
        make_app(self.app, "1.2.0")
        r = self.run_update()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(version_of(self.app), NEXT)
        self.assertIn(f"1.2.0 → {NEXT}", r.stdout)
        self.assertEqual(len(self.trash()), 1)
        self.assertEqual(version_of(os.path.join(self.home, ".Trash", self.trash()[0])), "1.2.0")
        self.assertEqual(subprocess.run(["codesign", "--verify", "--deep", "--strict", self.app]).returncode, 0)

    def test_no_app_yet_installs_into_home_applications(self):
        r = self.run_update()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(version_of(self.app), NEXT)
        self.assertEqual(self.trash(), [])

    def test_names_the_schedules_that_run_mac_cleaner(self):
        r = self.run_update()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("lịch", r.stdout)   # no LaunchAgents at all
        agents = os.path.join(self.home, "Library", "LaunchAgents")
        os.makedirs(agents)
        for label, args, fmt in (("local.maccleaner.clean", ["/usr/bin/python3", "/old/maccleaner.py", "clean"], plistlib.FMT_BINARY),
                                 ("com.example.backup", ["/usr/bin/rsync", "-a", "/a", "/b"], plistlib.FMT_XML)):
            with open(os.path.join(agents, label + ".plist"), "wb") as f:
                plistlib.dump({"Label": label, "ProgramArguments": args, "StartInterval": 3600}, f, fmt=fmt)
        shutil.rmtree(self.app)
        make_app(self.app, "1.2.0")
        r = self.run_update()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("lịch chạy Mac Cleaner: local.maccleaner.clean.", r.stdout)
        self.assertNotIn("com.example.backup", r.stdout)

    def test_app_at_a_given_path(self):
        other = os.path.join(self.home, "Tools", "MacCleaner.app")
        make_app(other, "1.5.0")
        r = self.run_update(other)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(version_of(other), NEXT)
        self.assertFalse(os.path.exists(self.app))

    def test_same_or_newer_version_changes_nothing(self):
        for installed in (NEXT, "100.1.0"):
            with self.subTest(installed=installed):
                shutil.rmtree(self.app, ignore_errors=True)
                make_app(self.app, installed)
                r = self.run_update()
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertEqual(version_of(self.app), installed)
                self.assertEqual(self.trash(), [])

    def test_a_failed_check_leaves_the_installed_app_alone(self):
        cases = {"sha": dict(release="good"), "signature": dict(release="tampered"), "bundle id": dict(release="foreign"),
                 "tag": dict(tag="v1.0; rm -rf x"), "missing release": dict(tag="v98.0.0")}
        for name, kw in cases.items():
            with self.subTest(check=name):
                shutil.rmtree(self.app, ignore_errors=True)
                make_app(self.app, "1.2.0")
                sha = os.path.join(self.releases["good"], "download", "v" + NEXT, ZIP + ".sha256")
                with open(sha) as f:
                    keep = f.read()
                try:
                    if name == "sha":
                        with open(sha, "w") as f:
                            f.write("0" * 64 + f"  {ZIP}\n")
                    r = self.run_update(**kw)
                finally:
                    with open(sha, "w") as f:
                        f.write(keep)
                self.assertNotEqual(r.returncode, 0, name)
                self.assertIn("✗", r.stderr)
                self.assertEqual(version_of(self.app), "1.2.0")
                self.assertEqual(self.trash(), [])

    def test_refuses_an_app_that_is_not_mac_cleaner_or_a_relative_path(self):
        other = os.path.join(self.home, "Applications", "Other.app")
        make_app(other, "3.0.0", bundle_id="com.example.other")
        for arg in (other, "MacCleaner.app"):
            with self.subTest(arg=arg):
                r = self.run_update(arg)
                self.assertNotEqual(r.returncode, 0)
        self.assertEqual(version_of(other), "3.0.0")
        self.assertEqual(self.trash(), [])


if __name__ == "__main__":
    unittest.main()
