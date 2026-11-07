import contextlib
import hashlib
import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import maccleaner as mc  # noqa: E402
from mclib import registry  # noqa: E402
from mclib.features import privacy as PV  # noqa: E402


def write(path, size=10):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"x" * size)


def make_tcc(path, rows, schema="new"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    con = sqlite3.connect(path)
    if schema == "new":
        con.execute("CREATE TABLE access (service TEXT, client TEXT, client_type INTEGER, auth_value INTEGER, "
                    "auth_reason INTEGER, last_modified INTEGER)")
        con.executemany("INSERT INTO access VALUES (?, ?, ?, ?, 0, ?)", rows)
    else:
        con.execute("CREATE TABLE access (service TEXT, client TEXT, client_type INTEGER, allowed INTEGER)")
        con.executemany("INSERT INTO access VALUES (?, ?, ?, ?)", [r[:4] for r in rows])
    con.commit()
    con.close()


ROWS = [("kTCCServiceCamera", "com.example.video", 0, 2, 1700000000),
        ("kTCCServiceMicrophone", "com.example.video", 0, 0, 1700000000),
        ("kTCCServiceScreenCapture", "com.apple.Terminal", 0, 2, 1700000000),
        ("kTCCServiceSystemPolicyAllFiles", "com.crowdstrike.falcon.Agent", 0, 2, 1700000000),
        ("kTCCServiceAccessibility", "/usr/local/bin/tool", 1, 2, 1700000000)]


def sha(p):
    with open(p, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


class Home:
    def __init__(self, d):
        self.home = d
        sup = os.path.join(d, "Library", "Application Support")
        chrome = os.path.join(sup, "Google", "Chrome")
        write(os.path.join(chrome, "Default", "Preferences"))
        write(os.path.join(chrome, "Default", "History"), 100)
        write(os.path.join(chrome, "Default", "History-journal"), 5)
        write(os.path.join(chrome, "Default", "Network", "Cookies"), 50)
        write(os.path.join(chrome, "Default", "Sessions", "Session_1"), 7)
        write(os.path.join(chrome, "Default", "Sessions", "Tabs_1"), 3)
        write(os.path.join(chrome, "Profile 1", "Preferences"))
        write(os.path.join(chrome, "Profile 1", "Cookies"), 20)          # bản cũ: Cookies nằm ở gốc profile
        write(os.path.join(chrome, "System Profile", "Preferences"))     # không phải profile người dùng
        write(os.path.join(chrome, "Crashpad", "settings.dat"))          # không có Preferences → bỏ qua
        write(os.path.join(sup, "Firefox", "Profiles", "abc.default", "places.sqlite"), 30)
        write(os.path.join(sup, "com.apple.sharedfilelist", "com.apple.LSSharedFileList.RecentDocuments.sfl3"), 9)
        write(os.path.join(sup, "com.apple.sharedfilelist", "com.apple.LSSharedFileList.ApplicationRecentDocuments",
                           "com.example.editor.sfl3"), 4)
        self.safari = os.path.join(d, "Library", "Safari")
        write(os.path.join(self.safari, "History.db"), 40)
        self.tcc = PV.tcc_path(d)
        make_tcc(self.tcc, ROWS)


class BrowserData(unittest.TestCase):
    def test_locations_and_sizes(self):
        with tempfile.TemporaryDirectory() as d:
            h = Home(d)
            items = PV.browser_data(h.home)
            by = {(e["browser"], e["profile"], e["kind"]): e for e in items}
            self.assertEqual(by[("Google Chrome", "Default", "browser-history")]["size"], 105)   # + journal
            self.assertEqual(by[("Google Chrome", "Default", "browser-cookies")]["path"],
                             os.path.join(d, "Library/Application Support/Google/Chrome/Default/Network/Cookies"))
            self.assertEqual(by[("Google Chrome", "Default", "browser-session")]["size"], 10)
            self.assertEqual(by[("Google Chrome", "Profile 1", "browser-cookies")]["size"], 20)
            self.assertNotIn(("Google Chrome", "System Profile", "browser-history"), by)
            self.assertEqual(by[("Firefox", "abc.default", "browser-history")]["size"], 30)
            self.assertEqual(by[("Safari", "Default", "browser-history")]["size"], 40)
            for e in items:
                registry.check_entry(e)
                self.assertEqual((e["safety"], e["delete_mode"], e["needsFDA"]), ("inventory", "list-only", False))

    @unittest.skipIf(os.geteuid() == 0, "root đọc được mọi thứ")
    def test_blocked_read_is_needsFDA(self):
        with tempfile.TemporaryDirectory() as d:
            h = Home(d)
            os.chmod(h.safari, 0)
            try:
                items = [e for e in PV.browser_data(h.home) if e["browser"] == "Safari"]
            finally:
                os.chmod(h.safari, 0o755)
            hist = next(e for e in items if e["kind"] == "browser-history")
            self.assertEqual((hist["needsFDA"], hist["size"]), (True, None))
            self.assertIn("Full Disk Access", hist["reason"])

    def test_nothing_is_ever_deleted(self):
        with tempfile.TemporaryDirectory() as d:
            h = Home(d)
            before = sorted(os.path.join(r, f) for r, _, fs in os.walk(d) for f in fs)
            g = PV.gather(h.home)
            self.assertEqual(sorted(os.path.join(r, f) for r, _, fs in os.walk(d) for f in fs), before)
            self.assertTrue(all(e["delete_mode"] == "list-only" for e in g["items"]))

    def test_recent_items(self):
        with tempfile.TemporaryDirectory() as d:
            Home(d)
            items = PV.recent_items(d)
            self.assertEqual(len(items), 2)
            self.assertEqual(sorted(e["lists"] for e in items), [1, 1])
            self.assertEqual(sum(e["size"] for e in items), 13)
            self.assertEqual(PV.recent_items(os.path.join(d, "none")), [])


class Tcc(unittest.TestCase):
    def test_read_only_and_flags(self):
        with tempfile.TemporaryDirectory() as d:
            h = Home(d)
            before = sha(h.tcc)
            perms, fda, err = PV.read_tcc(h.tcc)
            self.assertEqual(sha(h.tcc), before)
            self.assertEqual(sorted(os.listdir(os.path.dirname(h.tcc))), ["TCC.db"])   # không tạo -wal/-shm/-journal
            self.assertEqual((fda, err, len(perms)), (False, None, 5))
            by = {(p["service"], p["client"]): p for p in perms}
            cam = by[("Camera", "com.example.video")]
            self.assertEqual((cam["auth"], cam["resettable"], cam["client_type"]), ("allowed", True, "bundle"))
            self.assertEqual(cam["last_modified"][:4], "2023")
            self.assertEqual(by[("Microphone", "com.example.video")]["auth"], "denied")
            self.assertFalse(by[("ScreenCapture", "com.apple.Terminal")]["resettable"])
            self.assertIn("bảo mật", by[("SystemPolicyAllFiles", "com.crowdstrike.falcon.Agent")]["protected"])
            self.assertFalse(by[("Accessibility", "/usr/local/bin/tool")]["resettable"])   # client là đường dẫn

    def test_old_schema(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "TCC.db")
            make_tcc(p, ROWS, schema="old")
            perms, fda, err = PV.read_tcc(p)
            self.assertEqual(perms[0]["auth"], "allowed")
            self.assertIsNone(perms[0]["last_modified"])

    @unittest.skipIf(os.geteuid() == 0, "root đọc được mọi thứ")
    def test_unreadable_is_needsFDA(self):
        with tempfile.TemporaryDirectory() as d:
            h = Home(d)
            os.chmod(h.tcc, 0)
            try:
                perms, fda, err = PV.read_tcc(h.tcc)
            finally:
                os.chmod(h.tcc, 0o644)
            self.assertEqual((perms, fda), ([], True))
            g = PV.gather(os.path.join(d, "nohome"))
            self.assertEqual(g["permissions"], [])

    def test_collect_is_json(self):
        with tempfile.TemporaryDirectory() as d:
            Home(d)
            res = registry.collect_all(registry.ReportContext(home=d), [("privacy", PV)])["privacy"]
            self.assertNotIn("error", res)
            json.dumps(res)
            s = res["summary"]
            self.assertEqual((s["permissions"], s["resettable"], s["tcc_needsFDA"]), (5, 2, False))
            self.assertEqual(s["by_kind"]["tcc-db"], 1)
            tcc = next(e for e in res["items"] if e["kind"] == "tcc-db")
            self.assertEqual((tcc["safety"], tcc["delete_mode"]), ("protected", "list-only"))


class Reset(unittest.TestCase):
    def run_reset(self, service, bid, yes=False):
        calls, out = [], []
        with tempfile.TemporaryDirectory() as d:
            rc = PV.reset(service, bid, yes=yes, run=lambda cmd, timeout=30: calls.append(cmd) or (0, "Successfully reset", ""),
                          data=d, out=out.append)
            logs = os.listdir(os.path.join(d, "logs")) if os.path.isdir(os.path.join(d, "logs")) else []
        return rc, calls, "\n".join(out), logs

    def test_preview_runs_nothing(self):
        rc, calls, out, logs = self.run_reset("Camera", "com.example.video")
        self.assertEqual((rc, calls, logs), (0, [], []))
        self.assertIn("KHÔNG hoàn tác", out)

    def test_yes_runs_tccutil_and_logs(self):
        rc, calls, out, logs = self.run_reset("kTCCServiceCamera", "com.example.video", yes=True)
        self.assertEqual(rc, 0, out)
        self.assertEqual(calls, [["/usr/bin/tccutil", "reset", "Camera", "com.example.video"]])
        self.assertEqual(len(logs), 1)
        self.assertEqual(self.run_reset("microphone", "com.example.video", yes=True)[1][0][2], "Microphone")

    def test_refusals(self):
        for svc, bid in (("Camera", "com.apple.Safari"), ("Camera", "com.crowdstrike.falcon.Agent"),
                         ("Camera", "com.jamf.management.Agent"), ("NotAService", "com.example.video"),
                         ("Camera", "/usr/local/bin/tool"), ("Camera", "nodots"), ("Camera", "com.example;rm")):
            rc, calls, out, logs = self.run_reset(svc, bid, yes=True)
            self.assertEqual((rc, calls), (2, []), (svc, bid, out))

    def test_cli(self):
        old = PV.reset
        seen = []
        PV.reset = lambda *a, **k: seen.append((a, k)) or 0
        try:
            self.assertEqual(mc.main(["privacy", "reset", "Camera", "com.example.video"]), 0)
            self.assertEqual(seen, [(("Camera", "com.example.video"), {"yes": False})])
            with contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(mc.main(["privacy", "reset", "Camera"]), 1)
            self.assertIn("cần", out.getvalue())
        finally:
            PV.reset = old
        with tempfile.TemporaryDirectory() as d:
            Home(d)
            old_g = PV.gather
            PV.gather = lambda home=None: old_g(d)
            try:
                with contextlib.redirect_stdout(io.StringIO()) as out:
                    self.assertEqual(mc.main(["privacy", "list", "--json"]), 0)
                self.assertEqual(json.loads(out.getvalue())["summary"]["permissions"], 5)
                with contextlib.redirect_stdout(io.StringIO()) as out:
                    self.assertEqual(mc.main(["privacy"]), 0)
                self.assertIn("Quyền của app", out.getvalue())
            finally:
                PV.gather = old_g


if __name__ == "__main__":
    unittest.main()
