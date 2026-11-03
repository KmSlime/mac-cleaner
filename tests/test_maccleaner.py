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


if __name__ == "__main__":
    unittest.main()
