import contextlib
import io
import json
import os
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import maccleaner as mc  # noqa: E402
from mclib import registry  # noqa: E402
from mclib.features import dupes  # noqa: E402

TMP = "/tmp" if os.path.isdir("/tmp") else None  # TMPDIR mặc định của macOS nằm dưới /private/var → bị chặn cứng
BIG = 3 * dupes.CHUNK


def put(path, data, age_days=0):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    if age_days:
        t = time.time() - age_days * 86400
        os.utime(path, (t, t))
    return path


class Hash(unittest.TestCase):
    def test_partial_hash_only_reads_head_and_tail(self):
        with tempfile.TemporaryDirectory(dir=TMP) as d:
            a = put(os.path.join(d, "a"), b"a" * BIG)
            b = put(os.path.join(d, "b"), b"a" * dupes.CHUNK + b"b" * dupes.CHUNK + b"a" * dupes.CHUNK)
            self.assertEqual(dupes.file_hash(a, BIG, True), dupes.file_hash(b, BIG, True))
            self.assertNotEqual(dupes.file_hash(a, BIG, False), dupes.file_hash(b, BIG, False))


class Find(unittest.TestCase):
    def test_groups_keep_one_and_skip_excluded(self):
        with tempfile.TemporaryDirectory(dir=TMP) as d:
            home = os.path.realpath(d)
            same = b"z" * BIG
            orig = put(os.path.join(home, "Documents", "report.pdf"), same, age_days=100)
            dl = put(os.path.join(home, "Downloads", "report.pdf"), same, age_days=200)  # cũ hơn nhưng ở Downloads
            cp = put(os.path.join(home, "Documents", "report copy.pdf"), same, age_days=300)
            link = os.path.join(home, "Documents", "hardlink.pdf")
            os.link(orig, link)                                                       # cùng inode: không phải bản trùng
            # giống phần đầu/cuối nhưng khác giữa → không trùng
            put(os.path.join(home, "Documents", "near.pdf"), b"z" * dupes.CHUNK + b"q" * dupes.CHUNK + b"z" * dupes.CHUNK)
            small1 = put(os.path.join(home, "Desktop", "s1.txt"), b"hello" * 100, age_days=10)
            small2 = put(os.path.join(home, "Desktop", "s2.txt"), b"hello" * 100)
            for p in ("proj/.git/objects/x", "A.app/Contents/x", "P.photoslibrary/x", "proj/node_modules/m/x",
                      "Library/Mobile Documents/x", "Library/Mail/x", "Library/Caches/x"):
                put(os.path.join(home, p), same)
            res = dupes.find_dupes([home, os.path.join(home, "Documents")], min_size=100, home=home)
            json.dumps(res)
            groups = {d["size"]: d for d in res["duplicates"]}
            self.assertEqual(sorted(groups), [500, BIG])
            g = groups[BIG]
            self.assertIn(g["keep"], (orig, link))  # một trong hai tên của cùng inode, tuỳ thứ tự duyệt
            self.assertEqual(sorted(g["files"]), sorted([g["keep"], dl, cp]))
            self.assertEqual(groups[500]["keep"], small1)  # cũ nhất
            self.assertIn(small2, groups[500]["files"])
            for gid in {e["group"] for e in res["items"]}:
                members = [registry.check_entry(e) for e in res["items"] if e["group"] == gid]
                keeps = [e for e in members if e["keep"]]
                self.assertEqual(len(keeps), 1)
                self.assertEqual((keeps[0]["safety"], keeps[0]["delete_mode"]), ("inventory", "list-only"))
                self.assertTrue(all((e["safety"], e["delete_mode"]) == ("review", "trash") for e in members if not e["keep"]))
            self.assertEqual(res["summary"]["groups"], 2)
            self.assertEqual(res["summary"]["duplicateFiles"], 3)
            # --prefer thắng vị trí "chính"
            pref = dupes.find_dupes([home], min_size=BIG, prefer=[os.path.join(home, "Downloads")], home=home)
            self.assertEqual(pref["duplicates"][0]["keep"], dl)

    def test_never_trash_whole_group_even_if_keeper_protected(self):
        with tempfile.TemporaryDirectory(dir=TMP) as d:
            home = os.path.realpath(d)
            put(os.path.join(home, "a", "x"), b"1" * 4096)
            put(os.path.join(home, "b", "x"), b"1" * 4096)
            res = dupes.find_dupes([home], min_size=1, home=home)
            modes = [e["delete_mode"] for e in res["items"]]
            self.assertEqual(sorted(modes), ["list-only", "trash"])

    def test_cli_and_images_note(self):
        with tempfile.TemporaryDirectory(dir=TMP) as d:
            put(os.path.join(d, "a"), b"1" * 4096)
            put(os.path.join(d, "b"), b"1" * 4096)
            with contextlib.redirect_stdout(io.StringIO()) as o:
                self.assertEqual(mc.main(["dupes", d, "--min-size", "1K", "--json", "--images"]), 0)
            res = json.loads(o.getvalue())
            self.assertEqual(res["summary"]["groups"], 1)
            self.assertTrue(res["images"]["blocked"])
            with contextlib.redirect_stdout(io.StringIO()) as o:
                self.assertEqual(mc.main(["dupes", d, "--min-size", "1K", "--images"]), 0)
            self.assertIn("Vision", o.getvalue())
            self.assertIn("GIỮ", o.getvalue())
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(mc.main(["dupes", d, "--min-size", "x"]), 2)


if __name__ == "__main__":
    unittest.main()
