"""Test gỡ app: chỉ dùng bundle giả trong thư mục tạm, không gỡ / không chuyển gì vào Thùng rác thật."""
import contextlib
import datetime
import io
import json
import os
import plistlib
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)
import maccleaner as mc  # noqa: E402
from mclib import registry, safety  # noqa: E402
from mclib.features import uninstall as U  # noqa: E402

TEAM = "TEAM123456"
PLAN = U.plan  # bản gốc (test CLI thay U.plan bằng bản gọi PLAN với thư mục tạm)


def write_plist(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        plistlib.dump(data, f)


def make_app(root, name, bid, version="1.0", build="100", extra=None):
    app = os.path.join(root, name + ".app")
    write_plist(os.path.join(app, "Contents", "Info.plist"),
                dict(CFBundleIdentifier=bid, CFBundleName=name, CFBundleShortVersionString=version,
                     CFBundleVersion=build, **(extra or {})))
    return app


def touch(path, data=b"x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    return path


def mkdir(path):
    os.makedirs(path, exist_ok=True)
    touch(os.path.join(path, "f"))
    return path


def fake_run(pkg=None, mdfind=""):
    def run(cmd, timeout=60):
        if cmd[0].endswith("pkgutil"):
            return f"volume: /\npath: x\n\npkgid: {pkg}\n" if pkg else ""
        if cmd[0].endswith("mdfind"):
            return mdfind
        if cmd[0].endswith("sfltool"):
            return ""
        if cmd[0].endswith("mdls"):
            return ""
        return ""
    return run


def fake_codesign(teams):
    """teams: {đường dẫn app: team id}."""
    def run_err(cmd, timeout=15):
        t = teams.get(cmd[-1])
        return f"Identifier=x\nTeamIdentifier={t}\n" if t else "TeamIdentifier=not set\n"
    return run_err


class Fixture(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.home = os.path.realpath(self._td.name)
        self.apps = os.path.join(self.home, "Applications")
        self.roots = [self.apps]
        self.demo = make_app(self.apps, "Demo", "com.example.demo")
        ext = os.path.join(self.demo, "Contents", "PlugIns", "DemoShare.appex")
        write_plist(os.path.join(ext, "Contents", "Info.plist"), dict(CFBundleIdentifier="com.example.demo.share"))
        self.pro = make_app(self.apps, "Other", "com.example.demo.pro")
        L = os.path.join(self.home, "Library")
        self.L = L
        self.mine = [
            mkdir(f"{L}/Application Support/com.example.demo"),
            mkdir(f"{L}/Application Support/Demo"),
            mkdir(f"{L}/Caches/com.example.demo"),
            mkdir(f"{L}/Caches/com.example.demo.ShipIt"),
            touch(f"{L}/Preferences/com.example.demo.plist"),
            touch(f"{L}/Preferences/ByHost/com.example.demo.0000-1111.plist"),
            mkdir(f"{L}/Saved Application State/com.example.demo.savedState"),
            mkdir(f"{L}/Containers/com.example.demo.share"),
            mkdir(f"{L}/Group Containers/group.com.example.demo"),
            mkdir(f"{L}/HTTPStorages/com.example.demo"),
            mkdir(f"{L}/WebKit/com.example.demo"),
            mkdir(f"{L}/Logs/Demo"),
            touch(f"{L}/Cookies/com.example.demo.binarycookies"),
        ]
        self.agent = f"{L}/LaunchAgents/com.vendor.updater.plist"
        write_plist(self.agent, dict(Label="org.example.mc-test-only.updater",
                                     ProgramArguments=[self.demo + "/Contents/MacOS/updater"]))
        self.mine.append(self.agent)
        self.shared_group = mkdir(f"{L}/Group Containers/{TEAM}.com.example.shared")
        self.not_mine = [
            mkdir(f"{L}/Caches/com.example.demo.pro"),           # của app Other (bundle id dài hơn)
            touch(f"{L}/Preferences/com.example.demonstration.plist"),
            mkdir(f"{L}/Application Support/com.apple.demo"),
            mkdir(f"{L}/Caches/com.example.other"),
            touch(f"{L}/Keychains/login.keychain-db"),
        ]

    def tearDown(self):
        self._td.cleanup()

    def plan(self, query=None, teams=None, **kw):
        teams = {self.demo: TEAM} if teams is None else teams
        kw.setdefault("run", fake_run())
        return PLAN(query or "Demo", home=self.home, roots=self.roots, run_err=fake_codesign(teams),
                      spotlight=False, btm=False, system=False, ps=kw.pop("ps", []),
                      running_ids=kw.pop("running_ids", set()), **kw)


class BtmNeverPrompts(Fixture):
    def test_real_run_reads_btm_with_grant_and_without_prompting(self):
        from unittest import mock
        calls = []
        fr = fake_run()
        with mock.patch.object(U.S, "run", fr), \
                mock.patch.object(U.admin, "installed", return_value=True), \
                mock.patch.object(U.startup, "dumpbtm", side_effect=lambda **kw: (calls.append(kw), (0, "", ""))[1]):
            PLAN("Demo", home=self.home, roots=self.roots, run=fr, run_err=fake_codesign({self.demo: TEAM}),
                 spotlight=False, btm=True, system=False, ps=[], running_ids=set())
            PLAN("Demo", home=self.home, roots=self.roots, run=fr, run_err=fake_codesign({self.demo: TEAM}),
                 spotlight=False, btm=True, system=False, ps=[], running_ids=set(), interactive=True)
        self.assertEqual([(k["sudo"], k["interactive"]) for k in calls], [(True, False), (True, True)])


class FindAndRelated(Fixture):
    def test_find_by_name_bundle_id_and_path(self):
        for q in ("Demo", "demo.app", "com.example.demo", self.demo):
            self.assertEqual(U.find_apps(q, self.roots, spotlight=False), [self.demo], q)
        self.assertEqual(U.find_apps("Nope", self.roots, spotlight=False), [])

    def test_bundled_plugins_are_read(self):
        a = U.read_app(self.demo)
        self.assertEqual(U.bids_of(a), ["com.example.demo", "com.example.demo.share"])

    def test_related_finds_exactly_the_apps_files(self):
        pl = self.plan()
        self.assertIsNone(pl["error"])
        self.assertIsNone(pl["blocked"])
        paths = [e["path"] for e in pl["items"]]
        self.assertEqual(paths[0], self.demo)
        for p in self.mine:
            self.assertIn(p, paths)
        for p in self.not_mine:
            self.assertNotIn(p, paths)
        for e in pl["items"]:
            registry.check_entry(e)
        go = {e["path"] for e in pl["items"] if e["delete_mode"] == "trash"}
        self.assertTrue(set(self.mine) <= go, set(self.mine) - go)
        by = {e["path"]: e for e in pl["items"]}
        self.assertEqual(by[f"{self.L}/Caches/com.example.demo"]["safety"], "safe")
        self.assertEqual(by[f"{self.L}/Application Support/com.example.demo"]["safety"], "review")
        self.assertEqual([e["path"] for e in pl["bundled"]], [self.demo + "/Contents/PlugIns/DemoShare.appex"])
        self.assertTrue(all(e["delete_mode"] == "list-only" for e in pl["bundled"]))

    def test_team_group_container_kept_when_vendor_has_other_app(self):
        pl = self.plan(teams={self.demo: TEAM, self.pro: TEAM})
        g = next(e for e in pl["items"] if e["path"] == self.shared_group)
        self.assertEqual((g["safety"], g["delete_mode"]), ("inventory", "list-only"))
        pl = self.plan(teams={self.demo: TEAM, self.pro: "OTHER00000"})
        g = next(e for e in pl["items"] if e["path"] == self.shared_group)
        self.assertEqual(g["delete_mode"], "trash")
        pl = self.plan(teams={})                                       # không rõ team → không đụng nhóm team
        self.assertNotIn(self.shared_group, [e["path"] for e in pl["items"]])

    def test_second_copy_of_same_app_keeps_data(self):
        make_app(os.path.join(self.home, "Applications", "Sub"), "Demo Copy", "com.example.demo")
        pl = self.plan(self.demo)
        self.assertEqual(pl["items"][0]["delete_mode"], "trash")
        self.assertTrue(all(e["delete_mode"] == "list-only" for e in pl["items"][1:]))
        self.assertTrue(pl["notes"])

    def test_ambiguous_name_lists_candidates(self):
        make_app(os.path.join(self.home, "Applications", "Sub"), "Demo", "com.example.demo2")
        pl = self.plan("Demo")
        self.assertTrue(pl["error"])
        self.assertEqual(len(pl["candidates"]), 2)


class Refusals(Fixture):
    def assert_blocked(self, pl, level):
        self.assertEqual(pl["blocked"]["level"], level)
        self.assertTrue(all(e["delete_mode"] == "list-only" for e in pl["items"]))
        self.assertEqual(U.execute(pl, home=self.home, roots=self.roots, ps=[], running_ids=set())["status"], "refused")

    def test_apple_app_refused(self):
        make_app(self.apps, "Notes2", "com.apple.notes2")
        self.assert_blocked(self.plan("Notes2"), "deny")

    def test_security_agent_refused(self):
        make_app(self.apps, "Falcon", "com.crowdstrike.falcon.App")
        self.assert_blocked(self.plan("Falcon"), "deny")

    def test_running_app_refused(self):
        self.assert_blocked(self.plan(ps=[self.demo + "/Contents/MacOS/Demo"]), "running")
        self.assert_blocked(self.plan(running_ids={"com.example.demo.share"}), "running")

    def test_pkg_installed_app_only_guides(self):
        pl = self.plan(run=fake_run(pkg="com.example.demo.pkg"))
        self.assert_blocked(pl, "admin")
        self.assertTrue(any("pkgutil --files com.example.demo.pkg" in g for g in pl["guide"]))

    def test_root_owned_app_only_guides(self):
        with mock.patch.object(U, "_owner", return_value=0):
            pl = self.plan()
        self.assert_blocked(pl, "admin")
        self.assertTrue(pl["guide"])

    def test_sensitive_app_data_never_trashed(self):
        make_app(self.apps, "Vault", "com.example.wallet")
        mkdir(f"{self.L}/Application Support/com.example.wallet")
        pl = self.plan("Vault")
        self.assertEqual(pl["items"][0]["delete_mode"], "trash")
        self.assertTrue(all(e["safety"] == "protected" for e in pl["items"][1:]))


class Execute(Fixture):
    def run_cli(self, *argv):
        with mock.patch.object(U, "app_roots", return_value=self.roots), \
                mock.patch.object(U, "plan", side_effect=lambda q, spotlight=True, **kw: self.plan(q)), \
                mock.patch.object(safety, "HOME", self.home), \
                mock.patch.object(safety, "ALLOWED_ROOTS", [self.home]), \
                mock.patch.object(safety, "DATA", os.path.join(self.home, "data")), \
                mock.patch.object(safety, "running_bundle_ids", return_value=set()), \
                mock.patch.object(U, "ps_lines", return_value=[]), \
                mock.patch.object(U, "pkg_ids", return_value=[]), \
                mock.patch.object(safety, "to_trash", side_effect=self.trashed.append) as tt, \
                contextlib.redirect_stdout(io.StringIO()) as out:
            rc = mc.main(["uninstall", *argv])
        return rc, out.getvalue(), tt

    def setUp(self):
        super().setUp()
        self.trashed = []

    def test_preview_touches_nothing(self):
        rc, out, tt = self.run_cli("Demo")
        self.assertEqual(rc, 0)
        tt.assert_not_called()
        self.assertIn("--yes", out)
        for p in self.mine + [self.demo]:
            self.assertTrue(os.path.exists(p), p)
        self.assertFalse(os.path.exists(os.path.join(self.home, "data", "logs")))

    def test_yes_trashes_app_first_then_related_and_logs(self):
        rc, out, tt = self.run_cli("Demo", "--yes", "--json")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.trashed[0], self.demo)
        self.assertEqual(set(self.trashed[1:]), set(self.mine) | {self.shared_group})
        for p in self.not_mine:
            self.assertNotIn(p, self.trashed)
        res = json.loads(out)["result"]
        self.assertEqual(res["status"], "ok")
        logs = os.listdir(os.path.join(self.home, "data", "logs"))
        self.assertTrue(logs and all(n.startswith("clean-") and n.endswith(".jsonl") for n in logs))

    def test_app_trash_failure_keeps_related(self):
        def fail(p):
            self.trashed.append(p)
            return "permission denied"
        with mock.patch.object(safety, "to_trash", side_effect=fail):
            pl = self.plan()
            with mock.patch.object(safety, "DATA", os.path.join(self.home, "data")), \
                    mock.patch.object(safety, "running_bundle_ids", return_value=set()):
                res = U.execute(pl, home=self.home, roots=self.roots, log=lambda m: None, run=fake_run(), ps=[],
                                running_ids=set())
        self.assertEqual(res["status"], "error")
        self.assertEqual(self.trashed, [self.demo])

    def test_app_outside_allowed_roots_refused(self):
        pl = self.plan()
        res = U.execute(pl, home="/nonexistent-home", roots=["/nonexistent-root"], log=lambda m: None,
                        run=fake_run(), ps=[], running_ids=set())
        self.assertEqual(res["status"], "refused")


class Btm(unittest.TestCase):
    SAMPLE = """========================
 Records for UID 501 : 0000
========================

 ServiceManagement migrated: true

 #1:
                 UUID: 1111
                 Name: Demo
       Developer Name: Example Inc
      Team Identifier: TEAM123456
                 Type: app (0x2)
          Disposition: [enabled, allowed, notified] (0xb)
           Identifier: 2.com.example.demo
                  URL: file:///Applications/Demo%20App.app/
           Generation: 1
  Embedded Item Identifiers:
    #1: 16.com.example.demo.helper

 #2:
                 UUID: 2222
                 Name: Example Inc
                 Type: developer (0x20)
           Identifier: Example Inc
                  URL: (null)

 #3:
                 UUID: 3333
                 Name: helper
                 Type: agent (0x8)
           Identifier: 16.com.example.demo.helper
                  URL: Contents/Library/LoginItems/Helper.app
    Parent Identifier: 2.com.example.demo

 #4:
                 Name: Unrelated
                 Type: app (0x2)
           Identifier: 2.com.example.other
                  URL: file:///Applications/Other.app/
"""

    def test_parse_and_match(self):
        recs = U.parse_btm(self.SAMPLE)
        self.assertEqual(len(recs), 4)
        self.assertEqual(recs[0]["uid"], 501)
        self.assertIsNone(recs[1]["url"])
        app = dict(path="/Applications/Demo App.app", bundle_id="com.example.demo", bundled=[], names=["Demo"],
                   name="Demo")
        self.assertEqual([r["name"] for r in U.btm_for(app, recs)], ["Demo", "helper"])


class Inventory(Fixture):
    def test_collect_marks_stale_apps_review(self):
        now = time.time()
        old = datetime.datetime.fromtimestamp(now - 200 * 86400, datetime.timezone.utc)
        mdls = "\0".join([old.strftime("%Y-%m-%d %H:%M:%S +0000"), "(null)"])

        def run(cmd, timeout=60):
            return mdls if cmd[0].endswith("mdls") else ""
        items = U.inventory(self.home, self.roots, run=run, run_err=fake_codesign({self.demo: TEAM}), now=now,
                            ps=[], running_ids=set())
        by = {e["path"]: e for e in items}
        d, o = by[self.demo], by[self.pro]
        for e in items:
            registry.check_entry(e)
        json.dumps(items)
        self.assertTrue(d["stale"])
        self.assertEqual((d["safety"], d["delete_mode"]), ("review", "trash"))
        self.assertEqual(d["idleDays"], 200)
        self.assertGreater(d["relatedSize"], 0)
        self.assertIn(f"{self.L}/Caches/com.example.demo", [r["path"] for r in d["related"]])
        self.assertIsNone(o["lastUsed"])
        self.assertEqual((o["safety"], o["delete_mode"]), ("inventory", "list-only"))

    def test_collect_contract(self):
        ctx = registry.ReportContext(home=self.home)
        ctx.run = fake_run()
        with mock.patch.object(U, "app_roots", return_value=self.roots):
            res = U.collect(ctx)
        json.dumps(res)
        self.assertEqual(res["summary"]["apps"], 2)
        for e in res["items"]:
            registry.check_entry(e)

    def test_parse_mdls_raw(self):
        vals = U.parse_mdls_raw("2026-01-02 03:04:05 +0000\0(null)", 2)
        self.assertEqual(vals[0].year, 2026)
        self.assertIsNone(vals[1])
        self.assertEqual(U.parse_mdls_raw("garbage", 2), [None, None])


class SafetyKind(unittest.TestCase):
    def test_uninstall_kind_opens_only_app_data_dirs(self):
        lib = os.path.join(safety.HOME, "Library")
        p = os.path.join(lib, "Saved Application State", "com.example.demo.savedState")
        self.assertTrue(safety.is_protected(p))
        self.assertFalse(safety.is_protected(p, "app-uninstall"))
        self.assertTrue(safety.is_protected(os.path.join(lib, "Keychains", "x"), "app-uninstall"))
        self.assertTrue(safety.is_protected(os.path.join(lib, "Mobile Documents", "x"), "app-uninstall"))
        self.assertTrue(safety.is_protected(os.path.join(lib, "Saved Application State", "x"), "app-leftover"))


class AppPathGuard(unittest.TestCase):
    def test_rejects_dotdot_and_symlinked_parent(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as d:
            d = os.path.realpath(d)
            root, other = os.path.join(d, "Apps"), os.path.join(d, "Other")
            os.makedirs(os.path.join(root, "Ok.app")); os.makedirs(os.path.join(other, "Bad.app"))
            os.symlink(other, os.path.join(root, "link"))
            home = os.path.join(d, "home"); os.makedirs(home)
            self.assertTrue(U._app_path_ok(os.path.join(root, "Ok.app"), [root], home))
            self.assertFalse(U._app_path_ok(root + "/../Other/Bad.app", [root], home))
            self.assertFalse(U._app_path_ok(os.path.join(root, "link", "Bad.app"), [root], home))


if __name__ == "__main__":
    unittest.main()
