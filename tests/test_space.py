import contextlib
import io
import json
import os
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import maccleaner as mc  # noqa: E402
from mclib import registry  # noqa: E402
from mclib.features import _fs, space  # noqa: E402

TMP = "/tmp" if os.path.isdir("/tmp") else None  # TMPDIR mặc định của macOS nằm dưới /private/var → bị chặn cứng


def mk(path, size=4096, age_days=0):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"x" * size)
    if age_days:
        t = time.time() - age_days * 86400
        os.utime(path, (t, t))
    return path


def tmpdir():
    return tempfile.TemporaryDirectory(dir=TMP)


class Parse(unittest.TestCase):
    def test_size_and_age(self):
        self.assertEqual(_fs.parse_size("500M"), 500 * 2 ** 20)
        self.assertEqual(_fs.parse_size("1.5G"), int(1.5 * 2 ** 30))
        self.assertEqual(_fs.parse_size("200MB"), 200 * 2 ** 20)
        self.assertEqual(_fs.parse_size("64k"), 65536)
        self.assertEqual(_fs.parse_size("10"), 10)
        self.assertEqual((_fs.parse_age("180d"), _fs.parse_age("2w"), _fs.parse_age("6m"), _fs.parse_age("0")),
                         (180, 14, 180, 0))
        for bad in ("abc", "5X", ""):
            with self.assertRaises(ValueError):
                _fs.parse_size(bad)
        with self.assertRaises(ValueError):
            _fs.parse_age("3h")


class Tree(unittest.TestCase):
    def test_sizes_symlinks_hardlinks_depth_top(self):
        with tmpdir() as d, tmpdir() as outside:
            mk(os.path.join(d, "a", "x"), 8192)
            mk(os.path.join(d, "a", "deep", "y"), 8192)
            big = mk(os.path.join(d, "b", "big"), 64 * 1024)
            os.link(big, os.path.join(d, "b", "big-link"))      # hardlink: đếm một lần
            mk(os.path.join(outside, "huge"), 256 * 1024)
            os.symlink(outside, os.path.join(d, "to-outside"))  # không theo symlink
            res = space.build_tree(d, depth=1, top=10)
            t = res["tree"]
            kids = {c["name"]: c for c in t["children"]}
            self.assertLess(t["size"], 256 * 1024)
            self.assertGreaterEqual(kids["b"]["size"], 64 * 1024)
            self.assertLess(kids["b"]["size"], 2 * 64 * 1024)
            self.assertEqual(kids["a"]["files"], 2)
            self.assertNotIn("children", kids["a"])               # depth=1: không hiện cấp 2
            self.assertEqual(kids["to-outside"]["kind"], "file")  # symlink là một mục nhỏ, không phải cây
            self.assertEqual(res["nodes"], 1 + len(t["children"]))
            self.assertFalse(res["needsFDA"])
            top1 = space.build_tree(d, depth=1, top=1)["tree"]["children"]
            self.assertEqual([c["kind"] for c in top1], ["dir", "other"])
            self.assertEqual(top1[1]["count"], 2)
            self.assertEqual(sum(c["size"] for c in top1), t["size"] - os.lstat(res["root"]).st_blocks * 512)

    @unittest.skipIf(os.geteuid() == 0, "root đọc được mọi thứ")
    def test_unreadable_dir_is_skipped_and_flagged(self):
        with tmpdir() as d:
            mk(os.path.join(d, "ok", "f"))
            locked = os.path.join(d, "locked")
            mk(os.path.join(locked, "secret"))
            os.chmod(locked, 0)
            try:
                res = space.build_tree(d, depth=2)
            finally:
                os.chmod(locked, 0o755)
            self.assertTrue(res["needsFDA"])
            self.assertEqual(res["deniedCount"], 1)
            self.assertTrue(res["denied"][0].endswith("/locked"))

    def test_other_volume_and_system_skips(self):
        w = _fs.Walk("/Users")
        self.assertTrue(w.skip_dir("/Users/x", types.SimpleNamespace(st_dev=-12345)))
        self.assertEqual(w.other_volume, 1)
        dev = types.SimpleNamespace(st_dev=next(iter(w.devs)))
        self.assertTrue(_fs.Walk("/").skip_dir("/System/Volumes/Data", types.SimpleNamespace(st_dev=os.lstat("/").st_dev)))
        self.assertFalse(w.skip_dir("/Users/x", dev))

    def test_timeout_marks_partial(self):
        with tmpdir() as d:
            mk(os.path.join(d, "a", "f"))
            with mock.patch.object(_fs.Walk, "expired", lambda self: setattr(self, "partial", True) or True):
                self.assertTrue(space.build_tree(d)["partial"])

    def test_cut_and_collect(self):
        with tmpdir() as d:
            mk(os.path.join(d, "a", "b", "c", "f"))
            res = space.build_tree(d, depth=3)
            c = space.cut(res["tree"], 1)
            self.assertEqual(c["children"][0]["children"], [])
            self.assertTrue(c["children"][0]["truncated"])
            out = space.collect(registry.ReportContext(home=d))
            json.dumps(out)
            self.assertEqual(out["items"], [])
            self.assertEqual(out["summary"]["top"][0]["name"], "a")
            self.assertEqual(out["summary"]["top"][0]["children"][0]["name"], "b")

    def test_cli_writes_tree_file_when_large(self):
        with tmpdir() as d, tmpdir() as out:
            for i in range(5):
                mk(os.path.join(d, f"d{i}", "f"))
            with mock.patch.object(space, "TREE_INLINE_MAX", 3), contextlib.redirect_stdout(io.StringIO()) as o:
                self.assertEqual(mc.main(["analyze", d, "--depth", "2", "--json", "--out", out]), 0)
            res = json.loads(o.getvalue())
            self.assertEqual(os.path.dirname(os.path.realpath(res["treeFile"])), os.path.realpath(out))
            self.assertTrue(os.path.basename(res["treeFile"]).startswith("space-"))
            self.assertEqual(res["tree"]["children"][0]["children"], [])  # stdout chỉ cấp 1
            with open(res["treeFile"], encoding="utf-8") as f:
                self.assertEqual(len(json.load(f)["tree"]["children"][0]["children"]), 1)
            with contextlib.redirect_stdout(io.StringIO()) as o:
                self.assertEqual(mc.main(["analyze", d, "--depth", "1"]), 0)
            self.assertIn("d0/", o.getvalue())
            with contextlib.redirect_stdout(io.StringIO()) as o:
                self.assertEqual(mc.main(["analyze", os.path.join(d, "missing")]), 1)


class Large(unittest.TestCase):
    def test_finds_big_old_files_and_skips_protected_places(self):
        with tmpdir() as d:
            home = os.path.realpath(d)
            old = mk(os.path.join(home, "Downloads", "old.bin"), 8192, age_days=400)
            mk(os.path.join(home, "Downloads", "new.bin"), 8192)
            mk(os.path.join(home, "Downloads", "small.bin"), 100, age_days=400)
            for p in ("Apps/X.app/Contents/big", ".hidden/big", "Library/Containers/c/big",
                      "Library/Mobile Documents/com~apple~CloudDocs/big", "Pictures/P.photoslibrary/originals/big"):
                mk(os.path.join(home, p), 8192, age_days=400)
            res = space.find_large([home], min_size=4096, older_days=180, home=home)
            self.assertEqual([e["path"] for e in res["items"]], [old])
            e = registry.check_entry(res["items"][0])
            self.assertEqual((e["safety"], e["delete_mode"], e["kind"]), ("review", "trash", "large-old"))
            self.assertGreaterEqual(e["lastUsed"], 0)
            self.assertEqual(res["summary"]["found"], 1)
            json.dumps(res)
            # quét thẳng trong ~/Library thì được, trừ kho Mail/Messages/Photos và cloud
            inlib = space.find_large([os.path.join(home, "Library")], 4096, 180, home=home)
            self.assertEqual([os.path.basename(os.path.dirname(e["path"])) for e in inlib["items"]], ["c"])
            cloud = space.find_large([os.path.join(home, "Library", "Mobile Documents")], 4096, 0, home=home)
            self.assertEqual(cloud["items"], [])
            self.assertIn("cloud", cloud["summary"]["skipped"][0]["reason"])

    def test_cli(self):
        with tmpdir() as d:
            mk(os.path.join(d, "f.bin"), 8192, age_days=30)
            with contextlib.redirect_stdout(io.StringIO()) as o:
                self.assertEqual(mc.main(["large", d, "--min", "4K", "--older", "10d", "--json"]), 0)
            self.assertEqual(len(json.loads(o.getvalue())["items"]), 1)
            with contextlib.redirect_stdout(io.StringIO()) as o:
                self.assertEqual(mc.main(["large", d, "--min", "4K", "--older", "60d"]), 0)
            self.assertIn("0 file", o.getvalue())
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(mc.main(["large", d, "--min", "lots"]), 2)


if __name__ == "__main__":
    unittest.main()
