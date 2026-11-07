import contextlib
import io
import json
import os
import plistlib
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import maccleaner as mc  # noqa: E402
from mclib import featclean, registry, safety as S  # noqa: E402
from mclib.features import junk  # noqa: E402

TMUTIL = """Snapshots for disk /:
com.apple.TimeMachine.2026-11-01-101500.local
com.apple.TimeMachine.2026-11-02-090000.local
"""


def write(path, data=b"x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


def fake_run(cmd, timeout=60):
    if cmd[0].endswith("plutil"):
        return f"{cmd[-1]}: OK\n" if "openstep" in cmd[-1] else f"{cmd[-1]}: Unexpected character\n"
    if cmd[0].endswith("tmutil"):
        return TMUTIL
    raise AssertionError(f"lệnh không mong đợi: {cmd}")


def fake_du(paths, log=None):
    return {p: 100 for p in paths}


def build_home(h):
    """Home giả: trình duyệt, app Electron, app nhạy cảm, log container, plist, bản sao lưu iOS, firmware, model, dev cache."""
    asup = os.path.join(h, "Library", "Application Support")
    chrome = os.path.join(asup, "Google", "Chrome", "Default")
    write(os.path.join(chrome, "Preferences"), b"{}")
    for sub in ("Code Cache/js/a", "GPUCache/data_0", "Service Worker/CacheStorage/x", "Cookies", "History",
                "Login Data", "Bookmarks"):
        write(os.path.join(chrome, sub))
    write(os.path.join(h, "Library", "Caches", "Google", "Chrome", "Default", "Cache", "Cache_Data", "f"))
    app = os.path.join(asup, "SomeEditor")
    for sub in ("Local State", "Cache/Cache_Data/f", "GPUCache/d", "CachedData/v8", "User/settings.json"):
        write(os.path.join(app, sub))
    os.makedirs(os.path.join(app, "DawnCache"))  # rỗng → bỏ
    wallet = os.path.join(asup, "ExampleWallet")
    for sub in ("Local State", "Cache/f"):
        write(os.path.join(wallet, sub))
    write(os.path.join(h, "Library", "Containers", "com.example.app", "Data", "Library", "Logs", "a.log"))
    write(os.path.join(h, "Library", "Group Containers", "ABCDE12345.com.example.group", "Library", "Logs", "b.log"))
    pref = os.path.join(h, "Library", "Preferences")
    os.makedirs(pref)
    with open(os.path.join(pref, "com.example.good.plist"), "wb") as f:
        plistlib.dump({"a": 1}, f)
    write(os.path.join(pref, "com.example.bad.plist"), b"\x00\x01not a plist")
    write(os.path.join(pref, "com.example.empty.plist"), b"")
    write(os.path.join(pref, "com.example.openstep.plist"), b"{ a = 1; }")
    write(os.path.join(pref, "ByHost", "com.example.host.0000-1111.plist"), b"garbage")
    bk = os.path.join(asup, "MobileSync", "Backup", "00000000-TESTDEVICE")
    os.makedirs(bk)
    with open(os.path.join(bk, "Info.plist"), "wb") as f:
        plistlib.dump({"Device Name": "Test Phone", "Product Type": "iPhone0,0", "Product Version": "1.0"}, f)
    write(os.path.join(h, "Library", "iTunes", "iPhone Software Updates", "Test_Restore.ipsw"))
    write(os.path.join(h, ".ollama", "models", "blobs", "sha256-x"))
    write(os.path.join(h, ".bun", "install", "cache", "pkg"))


class Parsers(unittest.TestCase):
    def test_snapshots(self):
        snaps = junk.parse_snapshots(TMUTIL)
        self.assertEqual([s["date"] for s in snaps], ["2026-11-01-101500", "2026-11-02-090000"])
        self.assertEqual(junk.parse_snapshots(""), [])
        self.assertEqual(junk.parse_snapshots("Snapshots for disk /:\n"), [])

    def test_pref_broken(self):
        with tempfile.TemporaryDirectory() as d:
            good, bad, empty, old = (os.path.join(d, n + ".plist") for n in ("good", "bad", "empty", "openstep"))
            with open(good, "wb") as f:
                plistlib.dump({"k": "v"}, f, fmt=plistlib.FMT_BINARY)
            write(bad, b"\xffjunk")
            write(empty, b"")
            write(old, b"{ a = 1; }")
            self.assertIsNone(junk.pref_broken(good, fake_run))
            self.assertIsNone(junk.pref_broken(old, fake_run))  # plutil đọc được → không hỏng
            self.assertIsNone(junk.pref_broken(os.path.join(d, "missing.plist"), fake_run))  # không đọc được ≠ hỏng
            self.assertIn("plutil", junk.pref_broken(bad, fake_run))
            self.assertIn("rỗng", junk.pref_broken(empty, fake_run))


class Gather(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.h = self.tmp.name
        build_home(self.h)

    def tearDown(self):
        self.tmp.cleanup()

    def gather(self, **kw):
        kw.setdefault("running", set())
        return junk.gather(self.h, run=fake_run, du=fake_du, **kw)

    def test_groups_safety_and_no_private_browser_data(self):
        summary, items = self.gather()
        json.dumps({"summary": summary, "items": items})
        for e in items:
            registry.check_entry(e)
            self.assertEqual(e["feature"], "junk")
        by = {}
        for e in items:
            by.setdefault(e["kind"], []).append(e)
        rel = lambda e: os.path.relpath(e["path"], self.h)  # noqa: E731
        self.assertEqual(sorted(by), ["ai-model", "app-cache", "broken-pref", "browser-cache", "container-log",
                                      "dev-cache", "firmware", "ios-backup"])
        paths = [rel(e) for e in items]
        for private in ("Cookies", "History", "Login Data", "Bookmarks", "Preferences/com.example.good", "settings.json"):
            self.assertFalse(any(private in p for p in paths), private)
        self.assertFalse(any("ExampleWallet" in p for p in paths))  # tên nhạy cảm → bỏ qua
        self.assertFalse(any(p.endswith("DawnCache") for p in paths))  # rỗng
        bc = {rel(e): e for e in by["browser-cache"]}
        self.assertEqual(bc["Library/Application Support/Google/Chrome/Default/Code Cache"]["safety"], "safe")
        self.assertEqual(bc["Library/Application Support/Google/Chrome/Default/Service Worker/CacheStorage"]["safety"], "review")
        self.assertIn("Library/Caches/Google/Chrome/Default/Cache", bc)
        self.assertEqual(sorted(os.path.basename(e["path"]) for e in by["app-cache"]), ["Cache", "CachedData", "GPUCache"])
        self.assertEqual(sorted(e["name"] for e in by["broken-pref"]),
                         ["com.example.bad.plist", "com.example.empty.plist", "com.example.host.0000-1111.plist"])
        self.assertEqual(len(by["container-log"]), 2)
        for kind, safety, mode in (("ios-backup", "inventory", "list-only"), ("ai-model", "inventory", "list-only"),
                                   ("firmware", "review", "trash"), ("broken-pref", "review", "trash"),
                                   ("container-log", "safe", "trash"), ("dev-cache", "review", "trash")):
            for e in by[kind]:
                self.assertEqual((e["safety"], e["delete_mode"]), (safety, mode), kind)
        self.assertEqual(by["ios-backup"][0]["device"], "Test Phone")
        self.assertEqual([s["delete_command"] for s in summary["snapshots"]],
                         ["tmutil deletelocalsnapshots 2026-11-01-101500", "tmutil deletelocalsnapshots 2026-11-02-090000"])
        self.assertEqual(summary["kinds"]["firmware"], dict(count=1, size=100, cleanable=100))

    def test_running_app_is_list_only(self):
        summary, items = self.gather(running={"com.google.chrome", "someeditor"})
        for e in items:
            if e["kind"] in ("browser-cache", "app-cache") and ("Chrome" in e["path"] or "SomeEditor" in e["path"]):
                self.assertEqual((e["safety"], e["delete_mode"]), ("inventory", "list-only"), e["path"])
                self.assertIn("đang chạy", e["reason"])
        self.assertGreater(summary["skipped_running"], 0)
        self.assertTrue(next(b for b in summary["browsers"] if b["name"] == "Google Chrome")["running"])

    def test_skips_paths_already_in_base_scan(self):
        base = [dict(category="caches", path=os.path.join(self.h, "Library", "Caches", "Google")),
                dict(category="junk", path=os.path.join(self.h, "Library", "Preferences", "com.example.bad.plist")),
                dict(category="git", path=self.h)]  # repo ở home không được che mọi thứ
        summary, items = self.gather(base_items=base)
        paths = [e["path"] for e in items]
        self.assertFalse(any("/Library/Caches/Google/" in p for p in paths))
        self.assertFalse(any(p.endswith("com.example.bad.plist") for p in paths))
        self.assertTrue(any("Application Support/Google/Chrome" in p for p in paths))
        self.assertEqual(summary["deduped"], 2)

    def test_collect_on_real_context_is_json(self):
        res = junk.collect(registry.ReportContext(home=self.h))
        json.dumps(res)
        for e in res["items"]:
            registry.check_entry(e)

    def test_cli_json(self):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(mc.main(["junk", "--json"]), 0)
        self.assertEqual(set(json.loads(out.getvalue())), {"summary", "items"})


class SafetyExceptions(unittest.TestCase):
    """Ngoại lệ hẹp trong is_protected cho kind của junk; entry() phải truyền kind."""
    def test_is_protected(self):
        a = S.HOME + "/Library/Application Support/"
        self.assertFalse(S.is_protected(a + "SomeApp/Code Cache", "app-cache"))
        self.assertFalse(S.is_protected(a + "Google/Chrome/Default/Service Worker/CacheStorage", "browser-cache"))
        self.assertTrue(S.is_protected(a + "SomeApp/Code Cache"))  # không có kind → vẫn bảo vệ
        self.assertTrue(S.is_protected(a + "Code Cache", "app-cache"))  # chính thư mục app
        self.assertTrue(S.is_protected(a + "Cache", "app-cache"))
        self.assertTrue(S.is_protected(a + "SomeApp/Cookies", "app-cache"))
        self.assertTrue(S.is_protected(a + "ExampleWallet/Cache", "app-cache"))
        self.assertTrue(S.is_protected(a + "SomeApp/Code Cache", "app-leftover-x"))
        it = S.HOME + "/Library/iTunes/iPhone Software Updates/"
        self.assertFalse(S.is_protected(it + "x.ipsw", "firmware"))
        self.assertTrue(S.is_protected(it + "x.ipsw"))
        self.assertTrue(S.is_protected(S.HOME + "/Library/iTunes/iTunes Library.itl", "firmware"))
        self.assertFalse(S.is_protected(S.HOME + "/.bun/install/cache"))
        self.assertTrue(S.is_protected(S.HOME + "/.bun/bin"))

    def test_entry_passes_kind(self):
        p = S.HOME + "/Library/Application Support/SomeApp/GPUCache"
        e = registry.entry("junk", p, safety="safe", delete_mode="trash", kind="app-cache")
        self.assertEqual((e["safety"], e["delete_mode"]), ("safe", "trash"))
        e = registry.entry("junk", p, safety="safe", delete_mode="trash")
        self.assertEqual((e["safety"], e["delete_mode"]), ("protected", "list-only"))


def fe(path, kind="app-cache", safety="safe", mode="trash", **kw):
    return registry.entry("junk", path, safety=safety, delete_mode=mode, kind=kind, size=10, **kw)


class FeatureSelect(unittest.TestCase):
    def setUp(self):
        self.items = [fe("/x/a"), fe("/x/b", safety="review"), fe("/x/c", kind="ios-backup", safety="inventory", mode="list-only"),
                      fe("/x/d", kind="container-log"), fe("/x/e", safety="review", mode="permanent")]
        self.feats = {"junk": {"items": self.items}, "status": {"summary": {}, "items": []}, "broken": {"error": "x"}}
        self.ids = [e["id"] for e in self.items]

    def names(self, chosen):
        return [os.path.basename(e["path"]) for e in chosen]

    def test_select(self):
        sel = featclean.select
        self.assertEqual(self.names(sel(self.feats)), ["a", "d"])
        self.assertEqual(self.names(sel(self.feats, categories=["junk"], include_review=True)), ["a", "b", "d", "e"])
        self.assertEqual(self.names(sel(self.feats, categories=["junk:container-log"])), ["d"])
        self.assertEqual(self.names(sel(self.feats, categories=["caches"])), [])
        self.assertEqual(self.names(sel(self.feats, ids=[self.ids[2]])), ["c"])  # chọn đích danh: có, để báo lý do
        self.assertEqual(self.names(sel(None)), [])


class FeatureClean(unittest.TestCase):
    """Dọn thật chỉ trong home giả (HOME, vùng cho phép, Thùng rác đều trỏ vào thư mục tạm)."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.h = self.tmp.name
        self.old = S.HOME, list(S.ALLOWED_ROOTS), S.TRASH_BIN, S.DATA
        S.HOME, S.ALLOWED_ROOTS, S.TRASH_BIN, S.DATA = self.h, [self.h], os.path.join(self.h, "no-trash-bin"), self.h
        os.mkdir(os.path.join(self.h, ".Trash"))
        self.p = {}
        for n in ("trash", "listonly", "perm", "busy", "review"):
            self.p[n] = os.path.join(self.h, "Library", "Caches", "com.example." + n)
            write(os.path.join(self.p[n], "f"))
        self.log = os.path.join(self.h, "logs", "clean-test.jsonl")

    def tearDown(self):
        S.HOME, S.ALLOWED_ROOTS, S.TRASH_BIN, S.DATA = self.old
        self.tmp.cleanup()

    def entries(self):
        return [fe(self.p["trash"], kind="container-log"),
                fe(self.p["listonly"], kind="ios-backup", safety="inventory", mode="list-only"),
                fe(self.p["perm"], safety="review", mode="permanent"),
                fe(self.p["busy"], owners=["com.example.busy"]),
                fe(self.p["review"], safety="review")]

    def test_dry_run_touches_nothing(self):
        res = featclean.clean(self.entries(), yes=False, log=lambda *_: None, running=set())
        self.assertTrue(all(os.path.isdir(p) for p in self.p.values()))
        st = {os.path.basename(r["path"]): r["status"] for r in res["results"]}
        self.assertEqual(st["com.example.trash"], "dry-run")
        self.assertEqual(st["com.example.listonly"], "skip")
        self.assertEqual(st["com.example.perm"], "skip")
        self.assertIsNone(res["log"])

    def test_yes_trash_permanent_and_guards(self):
        out = featclean.clean(self.entries(), yes=True, log=lambda *_: None, force=True, log_path=self.log,
                              running={"com.example.busy.helper"})
        st = {os.path.basename(r["path"]): (r["status"], r["reason"]) for r in out["results"]}
        self.assertEqual(st["com.example.trash"][0], "ok")
        self.assertFalse(os.path.exists(self.p["trash"]))
        self.assertTrue(os.path.isdir(os.path.join(self.h, ".Trash", "com.example.trash")))  # vào Thùng rác
        self.assertEqual(st["com.example.listonly"][0], "skip")  # list-only: kể cả --force
        self.assertTrue(os.path.isdir(self.p["listonly"]))
        self.assertIn("--permanent", st["com.example.perm"][1])  # thiếu cờ
        self.assertTrue(os.path.isdir(self.p["perm"]))
        self.assertIn("đang chạy", st["com.example.busy"][1])
        self.assertTrue(os.path.isdir(self.p["busy"]))
        with open(self.log, encoding="utf-8") as f:
            self.assertEqual(len(f.read().splitlines()), 5)
        out = featclean.clean([fe(self.p["perm"], safety="review", mode="permanent")], yes=True, log=lambda *_: None,
                              permanent=True, log_path=self.log, running=set())
        self.assertEqual(out["results"][0]["status"], "ok")
        self.assertFalse(os.path.exists(self.p["perm"]))
        self.assertFalse(os.path.exists(os.path.join(self.h, ".Trash", "com.example.perm")))  # xoá hẳn, không qua Thùng rác

    def test_validate_still_applies(self):
        e = fe(os.path.join(self.h, ".ssh", "id_test"))  # entry() đã hạ xuống protected (HOME đã trỏ vào home giả)
        self.assertEqual(e["safety"], "protected")
        forged = dict(fe(os.path.join(self.h, "Library", "Application Support", "X", "data")), safety="safe",
                      delete_mode="trash")  # report bị sửa tay: validate vẫn chặn
        write(os.path.join(forged["path"], "f"))
        res = featclean.clean([e, forged], yes=True, log=lambda *_: None, log_path=self.log, running=set())
        self.assertEqual([r["status"] for r in res["results"]], ["skip", "skip"])
        self.assertTrue(os.path.isdir(forged["path"]))

    def test_lock_alive(self):
        d = os.path.join(self.h, "app")
        os.mkdir(d)
        self.assertFalse(featclean.lock_alive(d))
        os.symlink(f"host-{os.getpid()}", os.path.join(d, "SingletonLock"))
        self.assertTrue(featclean.lock_alive(d))
        self.assertTrue(featclean.owner_running(dict(lock_dir=d), set()))

    def test_cli_clean_wires_features(self):
        rep = dict(items=[], features={"junk": {"items": self.entries()}})
        rp = os.path.join(self.h, "report.json")
        with open(rp, "w", encoding="utf-8") as f:
            json.dump(rep, f)
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(mc.main(["clean", "--report", rp, "--category", "junk"]), 0)
        self.assertIn("dry-run", out.getvalue())
        self.assertTrue(os.path.isdir(self.p["trash"]))  # xem trước: không chạm gì
        with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(mc.main(["clean", "--report", rp, "--category", "junk:container-log", "--yes", "--json"]), 0)
        res = json.loads(out.getvalue())
        self.assertEqual([r["status"] for r in res["results"]], ["ok"])
        self.assertFalse(os.path.exists(self.p["trash"]))
        self.assertTrue(res["log"].startswith(os.path.join(self.h, "logs")))
        ids = ",".join(e["id"] for e in self.entries()[1:3])
        with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()):
            mc.main(["clean", "--report", rp, "--ids", ids, "--force", "--yes", "--json"])
        self.assertEqual([r["status"] for r in json.loads(out.getvalue())["results"]], ["skip", "skip"])
        self.assertTrue(os.path.isdir(self.p["listonly"]) and os.path.isdir(self.p["perm"]))


if __name__ == "__main__":
    unittest.main()
