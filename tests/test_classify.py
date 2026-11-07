import json
import os
import stat
import sys
import tempfile
import unittest
from importlib.machinery import SourceFileLoader

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
cl = SourceFileLoader("classify", os.path.join(HERE, "..", "scripts", "classify.py")).load_module()
import maccleaner as mc  # noqa: E402


def mk(path, size=4096):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"x" * size)


class Rules(unittest.TestCase):
    def test_build_artifacts_inside_user_data_are_deletable(self):
        r = cl.match_rule(os.path.join(cl.HOME, "Documents", "proj", "node_modules"), [])
        self.assertEqual(r[0], cl.XOA)
        self.assertEqual(cl.match_rule(os.path.join(cl.HOME, "Documents", "proj", "src"), [])[0], cl.GIU)

    def test_containers_descend_and_specific_rules_do_not(self):
        self.assertTrue(cl.match_rule(cl.HOME + "/Documents", [])[3])
        self.assertFalse(cl.match_rule(cl.HOME + "/Library/Caches", [])[3])

    def test_keep_wins_and_system_is_kept(self):
        p = cl.HOME + "/Library/Caches/x"
        self.assertEqual(cl.match_rule(p, [cl.HOME + "/Library/Caches"])[0], cl.GIU)
        self.assertEqual(cl.match_rule("/System/Library", [])[0], cl.GIU)
        self.assertEqual(cl.match_rule("/opt/homebrew/Cellar", [])[0], cl.GIU)

    def test_trash_is_consider_with_permanent_warning(self):
        r = cl.match_rule(cl.HOME + "/.Trash", [])
        self.assertEqual(r[0], cl.CAN)
        self.assertIn("VĨNH VIỄN", r[2])


class Scan(unittest.TestCase):
    def test_boundary_scan_classifies_tree(self):
        root = tempfile.mkdtemp()
        mk(os.path.join(root, "app", "node_modules", "a.js"), 3 * 2 ** 20)
        mk(os.path.join(root, "app", "src", "main.js"), 2 * 2 ** 20)
        rows, st = cl.scan_fs([root], [], min_mb=1, depth=4)
        by = {os.path.relpath(r["path"], root): r["cls"] for r in rows}
        self.assertEqual(by.get("app/node_modules"), cl.XOA)
        self.assertNotIn("app/node_modules/a.js", by)
        self.assertGreaterEqual(st["bytes"], 5 * 2 ** 20)

    def test_report_mapping(self):
        rep = {"items": [dict(id="1", path="/a", size=1, safety="safe", kind="user-cache", note=""),
                         dict(id="2", path="/b", size=1, safety="review", kind="build-artifact", note=""),
                         dict(id="3", path="/c", size=1, safety="info", kind="app", note="")]}
        f = os.path.join(tempfile.mkdtemp(), "r.json")
        json.dump(rep, open(f, "w"))
        rows, _ = cl.classify_report(f, [])
        self.assertEqual([r["cls"] for r in rows], [cl.XOA, cl.CAN, cl.GIU])
        self.assertIn("build", rows[1]["why"])


class PostScanHook(unittest.TestCase):
    def test_runs_executables_in_order_and_survives_failure(self):
        d = tempfile.mkdtemp()
        for name, body in (("20-b", "echo second $1"), ("10-a", "echo first $1"), ("30-bad", "exit 3"), ("README", "x")):
            p = os.path.join(d, name)
            open(p, "w").write("#!/bin/bash\n" + body + "\n")
            if name != "README":
                os.chmod(p, os.stat(p).st_mode | stat.S_IXUSR)
        out = tempfile.TemporaryFile("w+")
        mc.run_post_scan_hooks("/r.json", out=out, hook_dir=d)
        out.seek(0)
        text = out.read()
        self.assertLess(text.index("first /r.json"), text.index("second /r.json"))
        self.assertIn("30-bad] thoát mã 3", text)


if __name__ == "__main__":
    unittest.main()
