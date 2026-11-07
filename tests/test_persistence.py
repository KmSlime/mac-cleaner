import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import maccleaner as mc  # noqa: E402
from mclib import registry  # noqa: E402
from mclib.features import persistence as P  # noqa: E402

CS_UNSIGNED = "/x/tool: code object is not signed at all\n"
CS_ADHOC = """Executable=/x/tool
Identifier=tool
Format=Mach-O thin (arm64)
CodeDirectory v=20400 size=1898 flags=0x2(adhoc) hashes=53+2 location=embedded
Signature=adhoc
Info.plist=not bound
TeamIdentifier=not set
"""
CS_DEVID = """Executable=/Applications/Example.app/Contents/MacOS/Example
Identifier=com.example.app
Format=app bundle with Mach-O universal (x86_64 arm64)
CodeDirectory v=20500 size=1000 flags=0x10000(runtime) hashes=20+7 location=embedded
Signature size=9000
Authority=Developer ID Application: Example Corp (ABCDE12345)
Authority=Developer ID Certification Authority
Authority=Apple Root CA
TeamIdentifier=ABCDE12345
"""
CS_APPLE = """Format=app bundle with Mach-O universal (x86_64 arm64e)
CodeDirectory v=20400 size=3757 flags=0x0(none) hashes=107+7 location=embedded
Platform identifier=26
Authority=Software Signing
Authority=Apple Code Signing Certification Authority
Authority=Apple Root CA
TeamIdentifier=not set
"""
CRONTAB = """# chú thích
SHELL=/bin/sh
MAILTO=""
*/5 * * * * /nonexistent/bin/backup --quiet
@reboot /bin/sh ~/nonexistent-bin/start.sh
0 3 * * 1 cd /tmp && echo hi
broken line
"""


def fake_run(codesign_out, notarized_rc=0, spctl=(0, "x: accepted\nsource=Notarized Developer ID\n"), quarantine_rc=1):
    calls = []

    def run(cmd, timeout=30):
        calls.append(list(cmd))
        if cmd[0] == P.CODESIGN and cmd[1] == "-dv":
            return 0 if "not signed" not in codesign_out else 1, "", codesign_out
        if cmd[0] == P.CODESIGN and cmd[1] == "--verify":
            return notarized_rc, "", ""
        if cmd[0] == P.SPCTL:
            return spctl[0], "", spctl[1]
        if cmd[0] == P.XATTR:
            return (0, "0081;00000000;Browser;\n", "") if quarantine_rc == 0 else (1, "", "No such xattr: com.apple.quarantine")
        if cmd[0] == P.CRONTAB:
            return 0, CRONTAB, ""
        return 1, "", ""
    run.calls = calls
    return run


class Parsers(unittest.TestCase):
    def test_codesign(self):
        self.assertEqual(P.parse_codesign(CS_UNSIGNED)["signed"], False)
        a = P.parse_codesign(CS_ADHOC)
        self.assertEqual((a["signed"], a["adhoc"], a["team"], a["apple"]), (True, True, None, False))
        d = P.parse_codesign(CS_DEVID)
        self.assertEqual((d["signed"], d["adhoc"], d["team"], d["apple"]), (True, False, "ABCDE12345", False))
        self.assertEqual(len(d["authorities"]), 3)
        self.assertTrue(P.parse_codesign(CS_APPLE)["apple"])
        self.assertIsNotNone(P.parse_codesign("/x: No such file or directory")["error"])

    def test_spctl(self):
        self.assertEqual(P.parse_spctl(0, "/a: accepted\nsource=Notarized Developer ID\n"),
                         {"accepted": True, "source": "Notarized Developer ID"})
        self.assertEqual(P.parse_spctl(3, "/a: rejected\nsource=Unnotarized Developer ID\n")["accepted"], False)
        self.assertIsNone(P.parse_spctl(None, "")["accepted"])

    def test_crontab(self):
        jobs = P.parse_crontab(CRONTAB)
        self.assertEqual([j["schedule"] for j in jobs], ["*/5 * * * *", "@reboot", "0 3 * * 1"])
        self.assertEqual(jobs[0]["program"], "/nonexistent/bin/backup")
        self.assertEqual(jobs[1]["program"], os.path.expanduser("~/nonexistent-bin/start.sh"))   # script của sh
        self.assertIsNone(jobs[2]["program"])                                               # lệnh shell, không có file
        self.assertEqual(P.parse_crontab(""), [])

    def test_locations_and_scripts(self):
        self.assertEqual(P.location_flags("/tmp/x/run"), ["temp-location"])
        self.assertEqual(P.location_flags("/private/var/folders/ab/T/run"), ["temp-location"])
        self.assertEqual(P.location_flags("/Users/u/.hidden/run"), ["hidden-location"])
        self.assertEqual(P.location_flags("/Users/u/bin/.run"), [])           # tên ẩn: cờ riêng hidden-executable
        self.assertEqual(P.location_flags("/Applications/A.app/Contents/MacOS/A"), [])
        self.assertEqual(P.script_target("/bin/bash", ["/bin/bash", "-c", "/opt/x/run.sh"]), "/opt/x/run.sh")
        self.assertEqual(P.script_target("/usr/local/bin/tool", ["/usr/local/bin/tool", "/a"]), "/usr/local/bin/tool")
        self.assertEqual(P.app_bundle("/Applications/A.app/Contents/MacOS/A"), "/Applications/A.app")


class CodeChecks(unittest.TestCase):
    def test_flags_per_signature(self):
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "tool")
            open(f, "w").close()
            c = P.check_code(f, fake_run(CS_UNSIGNED))
            self.assertEqual(P.code_flags(c), ["unsigned"])
            self.assertEqual(c["quarantine"], False)
            self.assertEqual(P.code_flags(P.check_code(f, fake_run(CS_ADHOC))), ["adhoc"])
            run = fake_run(CS_DEVID, quarantine_rc=0)
            off = P.check_code(f, run)                               # mặc định offline: không spctl / verify
            self.assertEqual((P.code_flags(off), off["notarized"], off["quarantine"]), ([], None, True))
            self.assertFalse(any(c[0] == P.SPCTL or c[1] == "--verify" for c in run.calls))
            ok = P.check_code(f, fake_run(CS_DEVID, quarantine_rc=0), gatekeeper=True)
            self.assertEqual((P.code_flags(ok), ok["notarized"], ok["gatekeeper"], ok["quarantine"]), ([], True, True, True))
            bad = P.check_code(f, fake_run(CS_DEVID, notarized_rc=3, spctl=(3, "x: rejected\nsource=Unnotarized Developer ID\n")),
                               gatekeeper=True)
            self.assertEqual(P.code_flags(bad), ["not-notarized", "gatekeeper-rejected"])
            app = os.path.join(d, "Example.app")
            exe = os.path.join(app, "Contents", "MacOS", "Example")
            os.makedirs(os.path.dirname(exe))
            open(exe, "w").close()
            self.assertIsNone(P.check_code(exe, fake_run(CS_DEVID))["notarized"])
            open(os.path.join(app, "Contents", "CodeResources"), "w").close()     # vé notarize đính kèm
            self.assertTrue(P.check_code(exe, fake_run(CS_DEVID))["notarized"])
            self.assertEqual(P.code_flags(P.check_code(os.path.join(d, "gone"), fake_run(CS_DEVID))), ["missing-program"])
            run = fake_run(CS_DEVID)
            c = P.check_code("/usr/bin/true", run)                   # vùng SIP: không gọi codesign
            self.assertEqual((c["apple"], run.calls), (True, []))

    def test_hidden_executables(self):
        with tempfile.TemporaryDirectory() as d:
            mk = lambda rel, mode: (os.makedirs(os.path.dirname(os.path.join(d, rel)), exist_ok=True),  # noqa: E731
                                    open(os.path.join(d, rel), "w").close(), os.chmod(os.path.join(d, rel), mode))
            mk("Vendor/.agent", 0o755)              # ẩn + thực thi → gắn cờ
            mk("Vendor/.hidden/run", 0o755)         # trong thư mục ẩn → gắn cờ
            mk("Vendor/.config", 0o644)             # ẩn nhưng không thực thi
            mk("Vendor/tool", 0o755)                # thực thi nhưng không ẩn
            mk("Vendor/a/b/.deep", 0o755)           # sâu hơn giới hạn
            mk("com.apple.x/.agent", 0o755)         # của Apple: bỏ qua
            found, denied = P.hidden_executables(d)
            self.assertEqual(sorted(os.path.relpath(p, d) for p in found), ["Vendor/.agent", "Vendor/.hidden/run"])
            self.assertEqual(denied, 0)
            self.assertEqual(P.hidden_executables(os.path.join(d, "none")), ([], 1))


class Gather(unittest.TestCase):
    def setUp(self):
        self._tmp = P.TEMP_PREFIXES
        P.TEMP_PREFIXES = ("/nonexistent-temp/",)   # thư mục tạm của test nằm trong /tmp: tắt cờ temp ở đây

    def tearDown(self):
        P.TEMP_PREFIXES = self._tmp

    def make(self, d):
        prog = os.path.join(d, "Apps", "Example.app", "Contents", "MacOS", "Example")
        os.makedirs(os.path.dirname(prog))
        open(prog, "w").close()
        sup = os.path.join(d, "AppSupport")
        os.makedirs(os.path.join(sup, "Vendor"))
        hidden = os.path.join(sup, "Vendor", ".agent")
        open(hidden, "w").close()
        os.chmod(hidden, 0o755)
        sd = dict(errors=[], records=[
            dict(kind="agent", scope="user", label="com.example.a", plist=os.path.join(d, "a.plist"), program=prog,
                 args=[prog], owner="Example", refusal=None),
            dict(kind="login-item", scope="user", label="com.example.app", plist=None, path=prog, program=prog,
                 args=[], owner="Example", refusal=None),
            dict(kind="agent", scope="user", label="com.example.gone", plist=os.path.join(d, "g.plist"),
                 program=os.path.join(d, "gone"), args=[], owner="", refusal=None),
            dict(kind="agent", scope="system", label="com.apple.x", plist="/Library/LaunchAgents/com.apple.x.plist",
                 program="/System/Library/x", args=[], owner="", refusal="mục của Apple/macOS — không đụng"),
            dict(kind="agent", scope="user", label="com.example.rel", plist=os.path.join(d, "r.plist"), program=None,
                 args=["relative"], owner="", refusal=None)])
        return sd, sup, prog, hidden

    def test_gather_and_entries(self):
        with tempfile.TemporaryDirectory() as d:
            sd, sup, prog, hidden = self.make(d)
            run = fake_run(CS_DEVID)
            g = P.gather(home=d, run=run, startup_data=sd, app_support=sup, gatekeeper=True)
            self.assertEqual(g["cron_jobs"], 3)
            items = P.entries(g)
            for e in items:
                registry.check_entry(e)
                self.assertEqual(e["delete_mode"], "list-only")
            by = {e["path"]: e for e in items}
            self.assertEqual(by[prog]["kind"], "agent:user,login-item:user")      # cùng chương trình → gộp
            self.assertEqual((by[prog]["flags"], by[prog]["notarized"], by[prog]["safety"]), ([], True, "inventory"))
            self.assertEqual(by[os.path.join(d, "gone")]["flags"], ["missing-program"])
            self.assertEqual(by[os.path.join(d, "gone")]["safety"], "review")
            self.assertIn("hidden-executable", by[hidden]["flags"])
            self.assertIn(os.path.join(d, "r.plist"), by)                          # chương trình tương đối: path = plist
            self.assertIn("/nonexistent/bin/backup", by)                           # cron
            self.assertTrue(any(p.startswith(P.CRON_TABS + "/") for p in by))      # cron không có file → crontab
            self.assertIn("/System/Library/x", by)
            prog_codesign = [c for c in run.calls if c[0] == P.CODESIGN and c[1] == "-dv" and c[-1] == prog]
            self.assertEqual(len(prog_codesign), 1)                                # có cache theo đường dẫn
            self.assertFalse(any("/System/Library/x" in c for c in run.calls))    # mục Apple: không gọi lệnh
            s = P.summarize(g, items, browser_extensions=4)
            self.assertEqual(s["browser_extensions"]["count"], 4)
            self.assertEqual(s["flags"]["missing-program"], 3)                     # agent mất + 2 job cron mất
            self.assertIn("Gatekeeper", s["advice"])
            self.assertTrue(s["gatekeeper_checked"])
            g2 = P.gather(home=d, run=fake_run(CS_DEVID), startup_data=sd, app_support=sup)
            s2 = P.summarize(g2, P.entries(g2))
            self.assertEqual((s2["gatekeeper_checked"], s2["notarized_unknown"]), (False, 2))   # chương trình + file ẩn
            json.dumps({"summary": s, "items": items})

    def test_collect_and_cli(self):
        with tempfile.TemporaryDirectory() as d:
            sd, sup, _, _ = self.make(d)
            old = P.gather
            P.gather = lambda home=None, **k: old(home=d, run=fake_run(CS_UNSIGNED), startup_data=sd, app_support=sup)
            try:
                ctx = registry.ReportContext(items=[{"category": "extensions", "kind": "browser"}], home=d)
                res = registry.collect_all(ctx, [("audit", P)])["audit"]
                self.assertNotIn("error", res)
                self.assertEqual(res["summary"]["browser_extensions"]["count"], 1)
                self.assertGreater(res["summary"]["flags"]["unsigned"], 0)
                with contextlib.redirect_stdout(io.StringIO()) as out:
                    self.assertEqual(mc.main(["audit", "--json"]), 0)
                self.assertIn("items", json.loads(out.getvalue()))
                with contextlib.redirect_stdout(io.StringIO()) as out:
                    self.assertEqual(mc.main(["audit"]), 0)
                self.assertIn("không phải quét virus", out.getvalue())
            finally:
                P.gather = old


if __name__ == "__main__":
    unittest.main()
