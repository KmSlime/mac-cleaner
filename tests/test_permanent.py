"""clean --mode permanent: kiểm như Thùng rác + rào riêng, chuyển vào ~/.Trash rồi xoá đúng bản đó.
Mọi test chạy trong home giả (thư mục tạm dưới /tmp) — không chạm home hay Thùng rác thật."""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import maccleaner as mc  # noqa: E402

S = mc.safety


class FakeHome(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="mc-perm-", dir="/tmp")  # /tmp: không thuộc DENY_TREES như /var/folders
        self.old = S.HOME, S.ALLOWED_ROOTS, S.DATA
        S.HOME, S.ALLOWED_ROOTS, S.DATA = self.home, [self.home, "/Library/Caches", "/Library/Logs"], os.path.join(self.home, "data")
        os.makedirs(os.path.join(self.home, ".Trash"))

    def tearDown(self):
        S.HOME, S.ALLOWED_ROOTS, S.DATA = self.old
        shutil.rmtree(self.home, ignore_errors=True)

    def mk(self, rel, kind="user-cache", safety="safe", action="trash", category="caches"):
        p = os.path.join(self.home, rel)
        os.makedirs(p, exist_ok=True)
        with open(os.path.join(p, "blob"), "w") as f:
            f.write("x" * 10)
        return S.item(category, kind, os.path.basename(p), p, safety=safety, action=action, size=10)

    def trash(self):
        return os.listdir(os.path.join(self.home, ".Trash"))


class PermanentReason(FakeHome):
    def test_accepts_cleanable_item_in_home(self):
        it = self.mk("Library/Caches/Foo")
        self.assertIsNone(S.validate(it))
        self.assertIsNone(S.permanent_reason(it))

    def test_refuses_non_cleanable_or_tampered(self):
        it = self.mk("Library/Caches/Foo")
        cases = {
            "info": dict(it, safety="info"),
            "simctl": dict(it, action=["xcrun", "simctl", "delete", "U"]),
            "trash bin": dict(it, kind="trash"),
            "id mismatch": dict(it, id="0123456789"),
            "outside home": S.item("caches", "system-cache", "Bar", "/Library/Caches/Bar", safety="safe", action="trash"),
            "shallow": S.item("caches", "user-cache", "Library", os.path.join(self.home, "Library"), safety="safe", action="trash"),
        }
        for name, c in cases.items():
            self.assertIsNotNone(S.permanent_reason(c), name)

    def test_refuses_parent_symlinked_outside_home(self):
        outside = tempfile.mkdtemp(prefix="mc-outside-", dir="/tmp")
        try:
            os.makedirs(os.path.join(outside, "Foo"))
            os.makedirs(os.path.join(self.home, "Library"), exist_ok=True)
            os.symlink(outside, os.path.join(self.home, "Library", "Caches"))
            p = os.path.join(self.home, "Library", "Caches", "Foo")
            it = S.item("caches", "user-cache", "Foo", p, safety="safe", action="trash")
            self.assertIn("symlink", S.permanent_reason(it))
        finally:
            shutil.rmtree(outside, ignore_errors=True)


class PermanentClean(FakeHome):
    def test_dry_run_touches_nothing(self):
        it = self.mk("Library/Caches/Foo")
        res = S.clean({}, [it], yes=False, log=lambda *_: None, mode="permanent")
        self.assertEqual(res["results"][0]["status"], "dry-run")
        self.assertEqual(res["mode"], "permanent")
        self.assertTrue(os.path.isdir(it["path"]))
        self.assertEqual(self.trash(), [])

    def test_deletes_only_the_trashed_copy(self):
        it = self.mk("Library/Caches/Foo")
        keep = self.mk("Library/Caches/Keep")
        with mock.patch.object(S, "running_bundle_ids", return_value=set()):
            res = S.clean({}, [it], yes=True, log=lambda *_: None, mode="permanent")
        self.assertEqual([r["status"] for r in res["results"]], ["ok"])
        self.assertFalse(os.path.exists(it["path"]))
        self.assertTrue(os.path.isdir(keep["path"]))
        self.assertEqual(self.trash(), [])  # bản trong Thùng rác cũng đã xoá hẳn
        with open(res["log"], encoding="utf-8") as f:
            self.assertEqual(json.loads(f.readline())["mode"], "permanent")

    def test_outside_home_is_skipped_not_deleted(self):
        it = S.item("caches", "system-cache", "Bar", "/Library/Caches/__mc_perm_test__", safety="safe", action="trash")
        with mock.patch.object(S, "running_bundle_ids", return_value=set()):
            res = S.clean({}, [it], yes=True, log=lambda *_: None, mode="permanent")
        self.assertEqual(res["results"][0]["status"], "skip")

    def test_rename_failure_leaves_source(self):
        it = self.mk("Library/Caches/Foo")
        with mock.patch.object(S.os, "rename", side_effect=OSError(18, "Cross-device link")):
            err = S.trash_then_delete(it["path"])
        self.assertIn("không chuyển được", err)
        self.assertTrue(os.path.isdir(it["path"]))

    def test_remove_failure_keeps_copy_in_trash(self):
        it = self.mk("Library/Caches/Foo")
        with mock.patch.object(S, "remove_tree", return_value="Permission denied"):
            err = S.trash_then_delete(it["path"])
        self.assertIn("xoá hẳn lỗi", err)
        self.assertFalse(os.path.exists(it["path"]))
        self.assertEqual(len(self.trash()), 1)

    def test_force_and_permanent_are_exclusive(self):
        with self.assertRaises(ValueError):
            S.clean({}, [], mode="permanent", force=True)
        with self.assertRaises(ValueError):
            S.clean({}, [], mode="nope")

    def test_cli_permanent_requires_ids_and_no_force(self):
        for argv in (["clean", "--mode", "permanent", "--report", os.devnull],
                     ["clean", "--mode", "permanent", "--ids", "0123456789", "--force", "--report", os.devnull]):
            with self.assertRaises(SystemExit):
                mc.main(argv)

    def test_cli_permanent_end_to_end_with_report(self):
        it = self.mk("Library/Caches/Foo")
        feat = dict(id="f000000001", feature="junk", kind="app-cache", path=os.path.join(self.home, "x"), size=1,
                    safety="safe", delete_mode="trash")
        rep = os.path.join(self.home, "report.json")
        with open(rep, "w") as f:
            json.dump(dict(items=[it], features={"junk": {"items": [feat]}}), f)
        out = []
        with mock.patch.object(S, "running_bundle_ids", return_value=set()), \
                mock.patch("builtins.print", side_effect=lambda *a, **k: out.append(a[0] if a else "")):
            mc.main(["clean", "--report", rep, "--ids", f"{it['id']},f000000001", "--mode", "permanent", "--yes", "--json"])
        res = json.loads(out[-1])
        self.assertEqual({r["id"]: r["status"] for r in res["results"]}, {it["id"]: "ok", "f000000001": "skip"})
        self.assertFalse(os.path.exists(it["path"]))


if __name__ == "__main__":
    unittest.main()
