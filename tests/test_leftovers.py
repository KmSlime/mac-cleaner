"""Leftover classes a scan used to miss: Trash-renamed folders put back next to the original, containers named by UUID,
caches of running apps whose folder carries the app or vendor name, stale macOS installers, system-level leftovers,
macOS-managed assets, what waits in the Trash — and the backup-name false positive."""
import contextlib
import io
import json
import os
import plistlib
import sys
import tempfile
import time
import unittest
from importlib.machinery import SourceFileLoader
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import maccleaner as mc  # noqa: E402
from mclib import featclean, registry, safety as S  # noqa: E402
from mclib.features import junk  # noqa: E402

cl = SourceFileLoader("classify_leftovers", os.path.join(HERE, "..", "scripts", "classify.py")).load_module()
OLD = time.time() - 400 * 86400
GB = 2 ** 30


def write(path, data=b"x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


def age(path, ts=OLD):
    os.utime(path, (ts, ts))


@contextlib.contextmanager
def fake_home():
    with tempfile.TemporaryDirectory() as d:
        h = os.path.realpath(d)
        with mock.patch.object(mc, "HOME", h), mock.patch.object(S, "HOME", h):
            yield h


class BackupNames(unittest.TestCase):
    def test_backup_inside_a_word_is_not_a_backup(self):
        with tempfile.TemporaryDirectory() as d:
            notes = ["make-backup().md", "make-backupURL().md", "make-backupInvalid().md", "make-backupDirectory.md",
                     "list_backups().md", "photo.original.png", "clip.preview.mov"]
            real = ["db-backup.sql", "db-backup-2.sql", "conf.bak", "conf.bak.1", "IMG -BACKUP.png", "x_backup 2.zip"]
            for i, n in enumerate(notes + real):
                p = os.path.join(d, n)
                write(p)
                age(p, time.time() - 100 * (i + 1))
            keep, old = mc.group_backups([os.path.join(d, n) for n in notes + real])
            names = sorted(os.path.basename(p) for p in keep + old)
            self.assertEqual(names, sorted(real))
            self.assertEqual(sorted(os.path.basename(p) for p in old), ["conf.bak.1", "db-backup-2.sql"])


class RunningAppCaches(unittest.TestCase):
    def test_folder_named_after_app_or_vendor(self):
        c = S.HOME + "/Library/Caches/"
        edge = {"com.microsoft.edgemac", "microsoft edge"}
        self.assertTrue(S.cache_owner_running(c + "Microsoft Edge", edge))            # app name, not the bundle id
        self.assertTrue(S.cache_owner_running(c + "Microsoft", edge))                 # vendor label of the bundle id
        self.assertTrue(S.cache_owner_running(c + "Google", {"com.google.chrome"}))
        self.assertTrue(S.cache_owner_running(c + "Google", {"google chrome"}))       # first word of the app name
        self.assertTrue(S.cache_owner_running(c + "com.microsoft.edgemac.helper", edge))
        self.assertFalse(S.cache_owner_running(c + "Mozilla", edge))
        self.assertFalse(S.cache_owner_running(c + "Microsoft Edge Dev", edge))
        self.assertFalse(S.cache_owner_running(c + "edge", edge))
        self.assertFalse(S.cache_owner_running(S.HOME + "/Library/Logs/Microsoft Edge", edge))

    def test_running_names_from_ps(self):
        with tempfile.TemporaryDirectory() as d:
            app = os.path.join(d, "Microsoft Edge.app")
            write(os.path.join(app, "Contents", "Info.plist"),
                  plistlib.dumps({"CFBundleIdentifier": "com.microsoft.edgemac", "CFBundleName": "Edge"}))
            ps = f"{app}/Contents/MacOS/Microsoft Edge\n/usr/sbin/cfprefsd\n"
            self.assertEqual(S.running_names(lambda cmd, timeout=10: ps), {"microsoft edge", "com.microsoft.edgemac", "edge"})
            self.assertEqual(featclean.running_names(lambda cmd, timeout=10: ps), S.running_names(lambda cmd, timeout=10: ps))

    def test_clean_skips_the_app_named_cache_of_a_running_app(self):
        with fake_home() as h, tempfile.TemporaryDirectory() as data:
            p = os.path.join(h, "Library", "Caches", "Microsoft Edge")
            write(os.path.join(p, "Default", "Cache", "f"))
            it = S.item("caches", "user-cache", "Microsoft Edge", p, safety="safe", action="trash", size=1)
            with mock.patch.object(S, "ALLOWED_ROOTS", [h]), mock.patch.object(S, "DATA", data), \
                    mock.patch.object(S, "running_bundle_ids", return_value={"com.microsoft.edgemac"}), \
                    mock.patch.object(S, "running_names", return_value={"microsoft edge", "com.microsoft.edgemac"}), \
                    mock.patch.object(S, "to_trash", side_effect=AssertionError("must not trash")):
                res = S.clean({}, [it], yes=True, log=lambda *_: None)
            self.assertEqual(res["results"][0]["status"], "skip")
            self.assertIn("đang chạy", res["results"][0]["reason"])
            self.assertTrue(os.path.isdir(p))

    def test_scan_lists_caches_of_running_apps_without_proposing_them(self):
        with fake_home() as h:
            lib = os.path.join(h, "Library")
            for rel in ("Caches/Microsoft Edge/f", "Caches/com.example.idle/f", "Caches/Google/Chrome/f",
                        "Containers/com.example.live/Data/Library/Caches/f", "Logs/Microsoft Edge/f"):
                write(os.path.join(lib, rel))
            with mock.patch.object(mc, "run", return_value=""):
                items = {os.path.relpath(i["path"], lib): i for i in mc.scan_caches(
                    running={"com.microsoft.edgemac", "microsoft edge", "com.google.chrome", "com.example.live"})}
            for rel in ("Caches/Microsoft Edge", "Caches/Google", "Containers/com.example.live/Data/Library/Caches"):
                self.assertEqual((items[rel]["safety"], items[rel]["action"], items[rel].get("running")), ("info", None, True), rel)
                self.assertIn("app đang chạy", items[rel]["note"])
            self.assertEqual((items["Caches/com.example.idle"]["safety"], items["Caches/com.example.idle"]["action"]), ("safe", "trash"))
            self.assertEqual(items["Logs/Microsoft Edge"]["safety"], "safe")  # a log is not live data


class TrashRenamed(unittest.TestCase):
    def test_stem(self):
        self.assertEqual(mc.trash_stem("Caches 11-16-47-123"), "Caches")
        self.assertEqual(mc.trash_stem("node_modules 11-16-48-390"), "node_modules")
        for n in ("Caches", "Notes 2026-11-21", "build 11-16-47", "report 11.16.47", " 11-16-47-123"):
            self.assertIsNone(mc.trash_stem(n), n)

    def test_put_back_build_artifacts_are_reported_not_walked(self):
        with fake_home() as h:
            proj = os.path.join(h, "Documents", "proj")
            write(os.path.join(proj, "package.json"), b"{}")
            for rel in ("node_modules/a/package.json", "node_modules 11-16-48-390/a/node_modules/x", "build/out",
                        "build 11-16-47-773/out", "Pods 11-16-47-630/P/f", "notes 11-16-47-123/keep.md"):
                write(os.path.join(proj, rel))
            write(os.path.join(h, "Documents", "plain", "build 11-16-47-999", "f"))  # no project marker: not an artifact
            repos, artifacts, installers = mc.walk_home([h])
            rel = sorted(os.path.relpath(p, proj) for p in artifacts)
            self.assertEqual(rel, ["Pods 11-16-47-630", "build", "build 11-16-47-773", "node_modules", "node_modules 11-16-48-390"])
            with mock.patch.object(mc, "spotlight_has_app", return_value=None):
                items = {os.path.relpath(i["path"], proj): i for i in mc.scan_junk(artifacts, [], [], [])
                         if i["kind"] in ("trash-renamed", "build-artifact")}
            for n in ("Pods 11-16-47-630", "build 11-16-47-773", "node_modules 11-16-48-390"):
                self.assertEqual((items[n]["kind"], items[n]["safety"], items[n]["action"], items[n]["owner"]),
                                 ("trash-renamed", "review", "trash", "proj"), n)
                self.assertIn("đưa trở lại", items[n]["note"])
            self.assertEqual(items["node_modules"]["kind"], "build-artifact")

    def test_renamed_folders_inside_containers(self):
        with fake_home() as h:
            lib = os.path.join(h, "Library")
            c = os.path.join(lib, "Containers", "com.example.app", "Data", "Library")
            g = os.path.join(lib, "Group Containers", "group.com.example", "Library")
            for p in (c + "/Caches/f", c + "/Caches 11-16-47-123/big", c + "/Preferences/p.plist", g + "/Logs 11-16-47-456/l"):
                write(p)
            write(c + "/Notes 11-16-47-789")  # a file, not a folder
            with mock.patch.object(mc, "spotlight_has_app", return_value=None):
                items = [i for i in mc.scan_junk([], [], [], []) if i["kind"] == "trash-renamed"]
            self.assertEqual(sorted((os.path.relpath(i["path"], lib), i["owner"], i["safety"], i["action"]) for i in items),
                             [("Containers/com.example.app/Data/Library/Caches 11-16-47-123", "com.example.app", "review", "trash"),
                              ("Group Containers/group.com.example/Library/Logs 11-16-47-456", "group.com.example", "review", "trash")])
            with mock.patch.object(S, "ALLOWED_ROOTS", [h]):
                self.assertIsNone(S.validate(items[0]))  # the user may pick it; default `clean` (safe only) leaves it
            self.assertEqual(S.select(items), [])


class UuidContainers(unittest.TestCase):
    UUID = "11111111-2222-4333-8444-555555555555"

    def scan(self, h, installed=(), spotlight=False):
        with mock.patch.object(mc, "spotlight_has_app", return_value=spotlight):
            return [i for i in mc.scan_junk([], [], [], list(installed)) if i["kind"] == "app-leftover"]

    def container(self, h, name, bid=None, ts=OLD):
        c = os.path.join(h, "Library", "Containers", name)
        write(os.path.join(c, "Data", "Documents", "save.dat"))
        if bid:
            write(os.path.join(c, ".com.apple.containermanagerd.metadata.plist"), plistlib.dumps({"MCMMetadataIdentifier": bid}))
        age(c, ts)
        return c

    def test_orphan_found_through_container_metadata(self):
        with fake_home() as h:
            c = self.container(h, self.UUID, "com.gone.emulator")
            self.container(h, "22222222-2222-4333-8444-555555555555")                       # no metadata: unknown owner → keep
            self.container(h, "33333333-2222-4333-8444-555555555555", "com.gone.fresh", time.time())  # written recently → keep
            self.container(h, "44444444-2222-4333-8444-555555555555", "com.apple.thing")    # Apple's → keep
            items = self.scan(h)
            self.assertEqual([(i["path"], i["owner"], i["safety"], i["action"]) for i in items], [(c, "com.gone.emulator", "review", "trash")])
            self.assertIn("UUID", items[0]["note"])
            self.assertIn("bạn tự quyết", items[0]["note"])
            self.assertEqual(self.scan(h, installed=["com.gone.emulator"]), [])   # the app is still installed
            self.assertEqual(self.scan(h, spotlight=True), [])                    # Spotlight sees it somewhere else
            self.assertEqual(self.scan(h, spotlight=None), [])                    # Spotlight could not answer → keep


class TrashWaiting(unittest.TestCase):
    def test_state_and_report_line(self):
        with fake_home() as h:
            self.assertEqual(mc.trash_state(0), (True, "trống"))                 # no ~/.Trash yet
            os.mkdir(os.path.join(h, ".Trash"))
            self.assertEqual(mc.trash_state(0), (True, "trống"))
            ok, note = mc.trash_state(3 * GB)
            self.assertTrue(ok)
            self.assertIn("3.0 GB", note)
            self.assertIn("đổ Thùng rác", note)
            with mock.patch.object(mc.os, "listdir", side_effect=PermissionError):
                ok, note = mc.trash_state(None)
            self.assertFalse(ok)
            self.assertIn("Full Disk Access", note)
        rep = dict(generatedAt=0, disk=dict(used=1, total=2, free=1), seconds=1, items=[], trash=dict(size=3 * GB, measured=True))
        self.assertIn("Thùng rác đang giữ 3.0 GB", mc.to_markdown(rep))
        self.assertNotIn("Thùng rác", mc.to_markdown(dict(rep, trash=dict(size=0, measured=True))))
        self.assertIn("không đo được", mc.to_markdown(dict(rep, trash=dict(size=None, measured=False))))

    def test_real_clean_reports_what_waits_in_the_trash(self):
        with fake_home() as h, tempfile.TemporaryDirectory() as data:
            p = os.path.join(h, "Library", "Caches", "com.example.idle")
            write(os.path.join(p, "f"))
            write(os.path.join(h, ".Trash", "old"), os.urandom(3 * 2 ** 20))
            rp = os.path.join(data, "report.json")
            with open(rp, "w", encoding="utf-8") as f:
                json.dump(dict(items=[S.item("caches", "user-cache", "idle", p, safety="safe", action="trash", size=1)]), f)
            moved = []
            with mock.patch.object(S, "ALLOWED_ROOTS", [h]), mock.patch.object(S, "DATA", data), \
                    mock.patch.object(S, "running_bundle_ids", return_value=set()), mock.patch.object(S, "running_names", return_value=set()), \
                    mock.patch.object(S, "to_trash", side_effect=lambda q: moved.append(q)), \
                    contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(mc.main(["clean", "--report", rp, "--yes"]), 0)
            self.assertEqual(moved, [p])
            self.assertIn("Thùng rác đang giữ 3.0 MB", out.getvalue())
            self.assertIn("đổ Thùng rác", out.getvalue())
            os.remove(os.path.join(h, ".Trash", "old"))  # a near-empty Trash (.DS_Store) is not worth a line
            write(os.path.join(h, ".Trash", ".DS_Store"), b"x" * 4096)
            with mock.patch.object(S, "ALLOWED_ROOTS", [h]), mock.patch.object(S, "DATA", data), \
                    mock.patch.object(S, "running_bundle_ids", return_value=set()), mock.patch.object(S, "running_names", return_value=set()), \
                    mock.patch.object(S, "to_trash", return_value=None), contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(mc.main(["clean", "--report", rp, "--yes"]), 0)
            self.assertNotIn("Thùng rác đang giữ", out.getvalue())


class SystemLevel(unittest.TestCase):
    """Fake volume root, /Applications, /Library/Application Support, launchd folders and asset store."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.r = os.path.realpath(self.tmp.name)
        r = self.r
        self.sys = dict(volumes=(r + "/vol", r + "/missing"), apps=r + "/Applications", support=r + "/Support",
                        launchd=(r + "/LaunchDaemons", r + "/LaunchAgents"), assets=r + "/AssetsV2")
        self.home = r + "/home"
        os.makedirs(self.home)
        for rel in ("vol/macOS Install Data/UpdateBundle/payload", "vol/.adobeTemp/t", "vol/.fseventsd/x", "vol/.freshTemp/t",
                    "vol/Users/x", "Applications/Install macOS Test.app/Contents/SharedSupport/big",
                    "Support/Adobe/Uninstall/CORE.app/Contents/x", "Support/Mozilla/x", "Support/Microsoft/x",
                    "Support/VendorX/x", "Support/Apple/x", "Support/com.apple.TCC/x", "Support/CrowdStrike/x",
                    "Support/Elsewhere/x", "Support/Unknown/x", "Support/Tiny/x", "Support/Plugged/Plug-Ins/p.bundle/x",
                    "LaunchDaemons/com.vendorx.daemon.plist", "AssetsV2/com_apple_MobileAsset_Big/a", "AssetsV2/com_apple_MobileAsset_Small/a"):
            write(os.path.join(r, rel))
        write(os.path.join(r, "Applications/Install macOS Test.app/Contents/Info.plist"), plistlib.dumps({"CFBundleShortVersionString": "12.6"}))
        for top in ("vol/macOS Install Data", "vol/.adobeTemp", "vol/.fseventsd", "Support"):
            for d, dirs, files in os.walk(os.path.join(r, top), topdown=False):
                for n in files + dirs:
                    age(os.path.join(d, n))
            age(os.path.join(r, top))
        age(os.path.join(r, "Support/Mozilla/x"), time.time())  # still written → in use
        self.sizes = {"Tiny": 10 * 2 ** 20, "com_apple_MobileAsset_Small": 100 * 2 ** 20, "com_apple_MobileAsset_Big": 9 * GB,
                      "macOS Install Data": 11 * GB}
        self.base = [dict(category="applications", bundle_id="com.microsoft.edgemac", name="Microsoft Edge", path="/Applications/Microsoft Edge.app"),
                     dict(category="plugins", bundle_id="", name="p", path=r + "/Support/Plugged/Plug-Ins/p.bundle")]

    def tearDown(self):
        self.tmp.cleanup()

    def du(self, paths, log=None):
        return {p: self.sizes.get(os.path.basename(p), 3 * GB) for p in paths}

    def find_apps(self, key):
        return {"adobe": [self.r + "/Support/Adobe/Uninstall/CORE.app"],  # only its own uninstaller, inside the folder
                "elsewhere": ["/Volumes/Games/Elsewhere.app"], "unknown": None}.get(key, [])

    def gather(self, **kw):
        kw.setdefault("du", self.du)
        summary, items = junk.gather(self.home, self.base, running=set(), run=lambda cmd, timeout=60: "",
                                     system=self.sys, find_apps=self.find_apps, **kw)
        return summary, {os.path.relpath(e["path"], self.r): e for e in items}

    def test_reports_each_class_as_list_only(self):
        summary, by = self.gather()
        self.assertEqual(sorted(by), ["Applications/Install macOS Test.app", "AssetsV2/com_apple_MobileAsset_Big", "Support/Adobe",
                                      "Support/Plugged", "vol/.adobeTemp", "vol/macOS Install Data"])
        for rel, e in by.items():
            registry.check_entry(e)
            self.assertEqual((e["safety"], e["delete_mode"]), ("inventory", "list-only"), rel)
        self.assertEqual({rel: e["kind"] for rel, e in by.items()},
                         {"Applications/Install macOS Test.app": "os-installer", "vol/macOS Install Data": "os-installer",
                          "Support/Adobe": "system-leftover", "Support/Plugged": "system-leftover", "vol/.adobeTemp": "system-leftover",
                          "AssetsV2/com_apple_MobileAsset_Big": "os-assets"})
        stale = by["vol/macOS Install Data"]
        self.assertTrue(stale["stale"] and stale["needs_admin"])
        self.assertIn("quyền admin", stale["reason"])
        self.assertIn("12.6", by["Applications/Install macOS Test.app"]["name"])
        self.assertEqual(by["AssetsV2/com_apple_MobileAsset_Big"]["name"], "Big")
        self.assertEqual(summary["kinds"]["os-installer"], dict(count=2, size=14 * GB, cleanable=0))
        json.dumps(summary)

    def test_fresh_update_is_not_called_stale(self):
        p = os.path.join(self.r, "vol/macOS Install Data/UpdateBundle")
        age(p, time.time() - 86400)
        e = self.gather()[1]["vol/macOS Install Data"]
        self.assertFalse(e["stale"])
        self.assertIn("có thể đang chờ cài", e["reason"])

    def test_root_owned_tree_that_cannot_be_measured_is_still_reported(self):
        p = os.path.join(self.r, "vol/macOS Install Data/UpdateBundle")
        os.chmod(p, 0)
        try:
            e = self.gather(du=lambda paths, log=None: {q: 4096 for q in paths})[1]["vol/macOS Install Data"]
        finally:
            os.chmod(p, 0o755)
        self.assertTrue(e["unreadable"])
        self.assertIn("không đo hết được", e["reason"])

    def test_residue_and_system_off_are_not_reported(self):
        self.sizes["macOS Install Data"] = 200 * 1024  # what macOS leaves behind after the payload is gone
        self.assertNotIn("vol/macOS Install Data", self.gather()[1])
        summary, items = junk.gather(self.home, self.base, running=set(), run=lambda cmd, timeout=60: "", du=self.du)
        self.assertEqual(items, [])  # system=None: the system-level detectors do not run

    def test_clean_never_takes_them(self):
        _, by = self.gather()
        chosen = featclean.select({"junk": {"items": list(by.values())}}, None, ["junk"], True)
        self.assertEqual(chosen, [])
        res = featclean.clean(list(by.values()), yes=True, log=lambda *_: None, force=True, running=set(),
                              log_path=os.path.join(self.r, "logs", "clean.jsonl"))
        self.assertEqual({r["status"] for r in res["results"]}, {"skip"})
        self.assertTrue(os.path.isdir(os.path.join(self.r, "Support/Adobe")))


class Classify(unittest.TestCase):
    def test_decide_table_and_trash_line(self):
        home = cl.HOME
        feats = {"junk": {"summary": {"kinds": {"os-installer": dict(count=1, size=11 * GB, cleanable=0)}},
                          "items": [dict(kind="os-installer", path="/System/Volumes/Data/macOS Install Data", size=11 * GB, reason="cần quyền admin"),
                                    dict(kind="system-leftover", path="/Library/Application Support/Adobe", size=3 * GB, reason="hãng đã gỡ"),
                                    dict(kind="browser-cache", path=home + "/Library/Caches/x", size=GB, reason="cache")]}}
        rows = cl.decide_rows(feats)
        self.assertEqual([(r["kind"], r["size"]) for r in rows], [("os-installer", 11 * GB), ("system-leftover", 3 * GB)])
        meta = dict(generated="now", sources=["report.json"], disk=dict(free=40 * GB, total=228 * GB), decide=rows,
                    trash=dict(size=5 * GB, measured=True), junk_kinds=feats["junk"]["summary"]["kinds"])
        report_rows = [dict(path=home + "/Library/Caches/a", size=GB, cls=cl.XOA, why="cache", effect="tự tạo lại", source="report", id="a" * 10,
                            category="caches", kind="user-cache")]
        md = cl.render(report_rows, meta, 25)
        self.assertIn("**Thùng rác đang giữ 5.0 GB**", md)
        self.assertIn("## CẦN BẠN QUYẾT — 14.0 GB · 2 mục", md)
        self.assertIn("| 11.0 GB | `/System/Volumes/Data/macOS Install Data` | bộ cài / gói cập nhật macOS còn nằm lại | cần quyền admin |", md)
        self.assertNotIn("junk:os-installer", md)  # not repeated in the disk-eater table
        self.assertIn("không đo được", cl.trash_line(dict(trash=dict(size=None, measured=False))))
        self.assertEqual(cl.trash_line(dict(trash=dict(size=0, measured=True))), "")
        self.assertEqual(cl.trash_line({}), "")

    def test_trash_renamed_lands_in_can_nhac(self):
        with tempfile.TemporaryDirectory() as d:
            it = dict(id="b" * 10, category="junk", kind="trash-renamed", path="/x/Pods 11-16-47-630", size=GB, safety="review",
                      action="trash", note="bản “Pods” từng vào Thùng rác")
            rp = os.path.join(d, "r.json")
            with open(rp, "w", encoding="utf-8") as f:
                json.dump(dict(items=[it]), f)
            rows, _ = cl.classify_report(rp, [])
            self.assertEqual((rows[0]["cls"], rows[0]["why"]), (cl.CAN, cl.REPORT_CAN["trash-renamed"]))


if __name__ == "__main__":
    unittest.main()
