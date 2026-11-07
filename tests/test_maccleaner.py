import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import maccleaner as mc  # noqa: E402


class BackupGrouping(unittest.TestCase):
    def test_keeps_newest_per_group_and_skips_noise(self):
        with tempfile.TemporaryDirectory() as d:
            files = {"a.json.bak.0": 100, "a.json.bak.1": 200, "a.json.bak.2": 300, "solo.orig": 50,
                     "game.sav": 10, ".bak": 10}
            for n, age in files.items():
                p = os.path.join(d, n)
                open(p, "w").close()
                os.utime(p, (time.time() - age, time.time() - age))
            keep, old = mc.group_backups([os.path.join(d, n) for n in files])
            names = lambda ps: sorted(os.path.basename(p) for p in ps)
            self.assertEqual(names(old), ["a.json.bak.1", "a.json.bak.2"])
            self.assertEqual(names(keep), ["a.json.bak.0", "solo.orig"])


class BackupSkip(unittest.TestCase):
    def test_editor_extensions_skipped(self):
        self.assertTrue(mc.BACKUP_SKIP_RE.search("/Users/u/.someeditor/extensions/x-1.0/a.bak"))
        self.assertFalse(mc.BACKUP_SKIP_RE.search("/Users/u/Documents/a.bak"))


class Versions(unittest.TestCase):
    def test_old_versions_numeric_not_lexical(self):
        old = mc.old_versions({"x": [("1.9.0", "/e/x-1.9.0"), ("1.10.0", "/e/x-1.10.0"), ("1.2.0", "/e/x-1.2.0")],
                               "y": [("2.0", "/e/y-2.0")]})
        self.assertEqual(old, {"/e/x-1.9.0", "/e/x-1.2.0"})


class Pluginkit(unittest.TestCase):
    def test_parse_parent_and_owner(self):
        text = """+    com.foo.App.Share(2.1)
	            Path = /Applications/Foo.app/Contents/PlugIns/Share.appex
	   Parent Bundle = /Applications/Foo.app
	    Display Name = Foo Share
     com.apple.x(1.0)
	            Path = /System/Library/Frameworks/X.framework/PlugIns/X.appex
"""
        p = mc.parse_pluginkit(text)
        self.assertEqual([x["id"] for x in p], ["com.foo.App.Share", "com.apple.x"])
        self.assertEqual(mc.owner_of(p[0]["path"], p[0].get("parent", "")), "Foo")
        self.assertEqual(mc.owner_of(p[1]["path"]), "macOS")


class CleanSafety(unittest.TestCase):
    def it(self, path, **kw):
        return dict(dict(id="x", path=path, safety="safe", action="trash", size=1), **kw)

    def test_refuses_unsafe_items(self):
        h = mc.HOME
        cases = {
            "inventory": self.it(h + "/Library/Caches/foo", safety="info", action=None),
            "protected secret": self.it(h + "/.ssh/id_x"),
            "hidden tool data": self.it(h + "/.sometool/models/blob"),
            "library app data": self.it(h + "/Library/Application Support/Some App/data.bak"),
            "outside": self.it("/usr/local/bin/thing"),
            "too shallow": self.it(h + "/Documents"),
            "traversal": self.it(h + "/Library/../Documents/x"),
            "bad command": self.it(h + "/Library/x/y", action=["rm", "-rf", "/"]),
            "missing": self.it(h + "/Library/Caches/__mc_does_not_exist__"),
        }
        for name, it in cases.items():
            self.assertIsNotNone(mc.validate(it), name)

    def test_accepts_existing_cache_and_simctl(self):
        with tempfile.TemporaryDirectory(dir=os.path.join(mc.HOME, "Library/Caches")) as d:
            self.assertIsNone(mc.validate(self.it(d)))
        self.assertIsNone(mc.validate(self.it(mc.HOME + "/Library/Developer/CoreSimulator/Devices/U",
                                              action=["xcrun", "simctl", "delete", "U"])))

    def test_item_marks_protected_paths(self):
        i = mc.item("junk", "backup-old", "x", mc.HOME + "/Library/Some Synced Folder/doc.bak", safety="safe", action="trash")
        self.assertEqual((i["safety"], i["action"]), ("protected", None))

    def test_protection_is_rule_based(self):
        h = mc.HOME
        protected = [h + "/Library/Some Synced Folder/a.bak", h + "/Library/Application Support/X/y",
                     h + "/.sometool/data", h + "/.gradle/daemon/8", h + "/Library/Keychains/k", h + "/.ssh/k"]
        cleanable = [h + "/Library/Caches/foo", h + "/Library/Logs/foo", h + "/Library/Containers/c/Data/Library/Caches",
                     h + "/Library/Group Containers/g", h + "/Library/Preferences/p.plist",
                     h + "/Library/Developer/Xcode/DerivedData/x", h + "/.cache/foo", h + "/.npm/_cacache",
                     h + "/.gradle/caches/x", h + "/Documents/proj/node_modules", h + "/Downloads/a.dmg"]
        for p in protected:
            self.assertTrue(mc.is_protected(p), p)
        for p in cleanable:
            self.assertFalse(mc.is_protected(p), p)

    def test_select_defaults_to_safe_only(self):
        items = [dict(id="a", category="caches", safety="safe"), dict(id="b", category="junk", safety="review"),
                 dict(id="c", category="git", safety="info")]
        self.assertEqual([i["id"] for i in mc.select(items)], ["a"])
        self.assertEqual([i["id"] for i in mc.select(items, include_review=True)], ["a", "b"])
        self.assertEqual([i["id"] for i in mc.select(items, ids=["c"])], ["c"])

    def test_dry_run_touches_nothing(self):
        with tempfile.TemporaryDirectory(dir=os.path.join(mc.HOME, "Library/Caches")) as d:
            res = mc.clean({}, [self.it(d)], yes=False, log=lambda *_: None)
            self.assertTrue(os.path.isdir(d))
            self.assertEqual(res["results"][0]["status"], "dry-run")


class ForceDelete(unittest.TestCase):
    """Xoá đích danh mục bất kỳ trên dashboard (--force): bỏ mức an toàn, vẫn chặn cứng SIP/thư mục gốc."""
    def it(self, path, **kw):
        return dict(dict(id="x", path=path, kind="repo", safety="info", action=None, size=1), **kw)

    def test_inventory_needs_force(self):
        with tempfile.TemporaryDirectory(dir=mc.HOME) as d:
            self.assertIsNotNone(mc.validate(self.it(d)))
            self.assertIsNone(mc.validate(self.it(d), force=True))
            self.assertEqual(mc.action_of(self.it(d), force=True), "trash")

    def test_force_still_hard_denies(self):
        h = mc.HOME
        for p in ["/System/Library/Foo.appex", "/usr/bin/git", "/bin/ls", "/Applications", "/opt",
                  h, h + "/Documents", h + "/Library", mc.ROOT, os.path.dirname(mc.ROOT) if mc.ROOT.count("/") > 2 else mc.ROOT]:
            self.assertIsNotNone(mc.validate(self.it(p), force=True), p)
        bad = self.it(h + "/Library/x/y", safety="review", action=["rm", "-rf", "/"])
        self.assertIsNotNone(mc.validate(bad, force=True))
        self.assertIsNotNone(mc.validate(self.it(h + "/Library/Caches/__mc_does_not_exist__"), force=True))
        self.assertIsNotNone(mc.validate(self.it(h + "/Library/../Documents/x"), force=True))

    def test_usr_local_and_protected_allowed_with_force(self):
        self.assertIsNone(mc.hard_deny("/usr/local/bin/thing"))
        self.assertIsNone(mc.hard_deny(mc.HOME + "/.ssh"))

    def test_trash_item_means_empty_trash(self):
        t = self.it(os.path.join(mc.HOME, ".Trash"), kind="trash")
        self.assertIsNone(mc.validate(t, force=True))
        self.assertEqual(mc.action_of(t, force=True), "empty-trash")
        self.assertIsNotNone(mc.validate(dict(t, path="/tmp/x"), force=True))
        self.assertIsNone(mc.action_of(t))

    def test_force_dry_run_touches_nothing(self):
        with tempfile.TemporaryDirectory(dir=mc.HOME) as d:
            res = mc.clean({}, [self.it(d)], yes=False, log=lambda *_: None, force=True)
            self.assertTrue(os.path.isdir(d))
            self.assertEqual(res["results"][0]["status"], "dry-run")

    def test_cli_force_requires_ids(self):
        with self.assertRaises(SystemExit):
            mc.main(["clean", "--force", "--report", os.devnull])


class TrashFallback(unittest.TestCase):
    def test_moves_into_home_trash_without_trash_binary(self):
        S = mc.safety  # to_trash nằm ở mclib/safety.py: vá biến của module đó (mc.to_trash là alias)
        old = S.HOME, S.TRASH_BIN
        with tempfile.TemporaryDirectory() as h:
            try:
                S.HOME, S.TRASH_BIN = h, os.path.join(h, "no-trash-bin")
                os.mkdir(os.path.join(h, ".Trash"))
                for _ in range(2):  # trùng tên trong Thùng rác → thêm hậu tố giờ
                    f = os.path.join(h, "a.txt")
                    open(f, "w").close()
                    self.assertIsNone(mc.to_trash(f))
                    self.assertFalse(os.path.exists(f))
                self.assertEqual(len(os.listdir(os.path.join(h, ".Trash"))), 2)
            finally:
                S.HOME, S.TRASH_BIN = old


class Leftovers(unittest.TestCase):
    def test_leftover_kinds_in_library_are_cleanable(self):
        h = mc.HOME
        self.assertFalse(mc.is_protected(h + "/Library/Application Support/com.gone.app", "app-leftover"))
        self.assertFalse(mc.is_protected(h + "/Library/LaunchAgents/com.gone.agent.plist", "agent-leftover"))
        self.assertTrue(mc.is_protected(h + "/Library/Application Support/com.gone.app"))

    def test_agent_program_detects_missing_binary(self):
        import plistlib
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "com.gone.agent.plist")
            with open(p, "wb") as f:
                plistlib.dump({"Label": "com.gone.agent", "ProgramArguments": [d + "/missing/bin", "--x"]}, f)
            self.assertEqual(mc.agent_program(p), d + "/missing/bin")
            with open(p, "wb") as f:
                plistlib.dump({"Label": "x", "ProgramArguments": ["sh", "-c", "true"]}, f)
            self.assertIsNone(mc.agent_program(p))


class SafetyRails(unittest.TestCase):
    """Rào an toàn học từ cách Mole vận hành (viết lại, không chép)."""
    def test_hard_deny_roots_and_case_aliases(self):
        for p in ["/SYSTEM/Library/x", "/etc/hosts", "/private/etc/hosts", "/var/db/x", "/Library/Keychains/k",
                  "/Library/Extensions/x.kext", "/Users/Shared", "/Users/someoneelse", "/Library/Application Support",
                  "/opt/homebrew", "/usr/local", "/Volumes",
                  mc.HOME + "/Library/Caches/com.crowdstrike.falcon", "/Library/Application Support/com.jamf.x"]:
            self.assertIsNotNone(mc.hard_deny(p), p)
        for p in ["/usr/local/bin/thing", "/opt/homebrew/Cellar/x", "/Applications/Foo.app", mc.HOME + "/Library/Caches/x",
                  "/Library/Application Support/SomeVendor"]:
            self.assertIsNone(mc.hard_deny(p), p)

    def test_live_data_caches_are_protected(self):
        c = mc.HOME + "/Library/Caches/"
        for p in [c + "pypoetry", c + "pypoetry/virtualenvs/x-py3.12", c + "com.apple.FontRegistry",
                  c + "com.apple.Spotlight", c + "org.R-project.R/R/renv"]:
            self.assertIsNotNone(mc.guard_reason(p), p)
            self.assertEqual(mc.item("caches", "user-cache", "x", p, safety="safe", action="trash")["safety"], "protected")
            self.assertIsNotNone(mc.validate(dict(id="x", path=p, safety="safe", action="trash")), p)
        self.assertIsNone(mc.guard_reason(c + "com.spotify.client"))

    def test_running_app_cache_detection(self):
        r = {"com.microsoft.vscode"}
        self.assertTrue(mc.cache_owner_running(mc.HOME + "/Library/Caches/com.microsoft.VSCode.ShipIt", r))
        self.assertTrue(mc.cache_owner_running(mc.HOME + "/Library/Containers/com.microsoft.VSCode/Data/Library/Caches", r))
        self.assertFalse(mc.cache_owner_running(mc.HOME + "/Library/Caches/com.microsoft.VSCodeInsiders", r))
        self.assertFalse(mc.cache_owner_running(mc.HOME + "/Library/Logs/com.microsoft.VSCode", r))


class OrphanDetection(unittest.TestCase):
    """Nhận diện dữ liệu app đã gỡ: nghi ngờ thì giữ."""
    def test_rules(self):
        old_home, old_spot = mc.HOME, mc.spotlight_has_app
        with tempfile.TemporaryDirectory() as h:
            try:
                mc.HOME = h
                sup = os.path.join(h, "Library", "Application Support")
                old = time.time() - 90 * 86400
                names = {"com.gone.app": old, "com.gone.recent": time.time(), "com.bitwarden.desktop": old,
                         "com.installed.app": old, "com.steam.game": old, "com.spotlight.broken": old,
                         "groups.com.apple.podcasts": old}
                for n, t in names.items():
                    os.makedirs(os.path.join(sup, n))
                    os.utime(os.path.join(sup, n), (t, t))
                answers = {"com.steam.game": True, "com.spotlight.broken": None}
                mc.spotlight_has_app = lambda bid: answers.get(bid, False)
                items = mc.scan_junk([], [], [], ["com.installed.app"])
                found = sorted(i["name"] for i in items if i["kind"] == "app-leftover")
                self.assertEqual(found, ["com.gone.app"])
            finally:
                mc.HOME, mc.spotlight_has_app = old_home, old_spot

    def test_recent_build_artifact_not_suggested(self):
        with tempfile.TemporaryDirectory() as d:
            fresh, stale = os.path.join(d, "a", "node_modules"), os.path.join(d, "b", "node_modules")
            os.makedirs(fresh); os.makedirs(stale)
            t = time.time() - 30 * 86400
            os.utime(stale, (t, t))
            items = {i["path"]: i for i in mc.scan_junk([fresh, stale], [], [], []) if i["kind"] == "build-artifact"}
            self.assertEqual((items[fresh]["safety"], items[stale]["safety"]), ("info", "review"))


class HomeWalk(unittest.TestCase):
    def test_finds_repos_artifacts_installers(self):
        with tempfile.TemporaryDirectory() as root:
            for p in ["Documents/proj/.git", "Documents/proj/node_modules/x", "Documents/proj/build",
                      "Documents/plain/build", ".config/tool/node_modules", ".config/repo/.git",
                      ".sometool/repo/.git", ".sometool/proj/node_modules", "Downloads"]:
                os.makedirs(os.path.join(root, p))
            open(os.path.join(root, "Documents/proj/package.json"), "w").close()
            open(os.path.join(root, "Downloads/App.dmg"), "w").close()
            repos, arts, inst = mc.walk_home([root])
            rel = lambda ps: sorted(os.path.relpath(p, root) for p in ps)
            self.assertEqual(rel(repos), [".config/repo", "Documents/proj"])  # thư mục ẩn khác ở gốc home: bỏ qua
            self.assertEqual(rel(arts), ["Documents/proj/build", "Documents/proj/node_modules"])
            self.assertEqual(rel(inst), ["Downloads/App.dmg"])


class AIModelCache(unittest.TestCase):
    def test_model_dirs_in_dot_cache_are_list_only(self):
        old = mc.HOME
        with tempfile.TemporaryDirectory(dir="/tmp") as h:
            try:
                mc.HOME = h
                for n in ("huggingface", "uv"):
                    os.makedirs(os.path.join(h, ".cache", n, "x"))
                got = {i["name"]: i for i in mc.scan_caches() if i["path"].startswith(os.path.join(h, ".cache") + "/")}
            finally:
                mc.HOME = old
        self.assertEqual((got["huggingface"]["kind"], got["huggingface"]["safety"], got["huggingface"]["action"]),
                         ("ai-model", "info", None))
        self.assertEqual((got["uv"]["safety"], got["uv"]["action"]), ("review", "trash"))


if __name__ == "__main__":
    unittest.main()
