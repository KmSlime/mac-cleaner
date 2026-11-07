import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)
import maccleaner as mc  # noqa: E402
from mclib import registry, safety  # noqa: E402
from mclib.features import status  # noqa: E402

VM_STAT = """Mach Virtual Memory Statistics: (page size of 16384 bytes)
Pages free:                                     100.
Pages active:                                   200.
Pages inactive:                                 300.
Pages speculative:                               10.
Pages wired down:                               400.
"Translation faults":                      521432791.
Pages occupied by compressor:                    50.
"""


class Discovery(unittest.TestCase):
    def test_builtin_status_is_found(self):
        self.assertIn("status", [n for n, _ in registry.discover()])

    def test_bad_modules_are_skipped_not_fatal(self):
        with tempfile.TemporaryDirectory() as d:
            pkg = "mcf_test_pkg_" + str(os.getpid())
            os.mkdir(os.path.join(d, pkg))
            files = {"__init__.py": "", "good.py": "def collect(ctx):\n    return {}\n",
                     "broken.py": "raise RuntimeError('boom')\n", "clash.py": "NAME = 'scan'\ndef collect(ctx): return {}\n",
                     "empty.py": "X = 1\n", "_private.py": "def collect(ctx): return {}\n"}
            for n, src in files.items():
                with open(os.path.join(d, pkg, n), "w") as f:
                    f.write(src)
            sys.path.insert(0, d)
            try:
                with contextlib.redirect_stderr(io.StringIO()) as err:
                    found = registry.discover(pkg)
            finally:
                sys.path.remove(d)
            self.assertEqual([n for n, _ in found], ["good"])
            self.assertIn("broken", err.getvalue())


class Collect(unittest.TestCase):
    def test_errors_are_isolated_per_feature(self):
        def boom(ctx):
            raise OSError("x")
        mods = [("ok", types.SimpleNamespace(collect=lambda ctx: {"items": [registry.entry("ok", "/Applications/A.app")]})),
                ("boom", types.SimpleNamespace(collect=boom)),
                ("notdict", types.SimpleNamespace(collect=lambda ctx: [1])),
                ("badentry", types.SimpleNamespace(collect=lambda ctx: {"items": [{"id": "1", "path": "/x"}]})),
                ("cli-only", types.SimpleNamespace(register=lambda sub: None))]
        out = registry.collect_all(registry.ReportContext(), mods)
        self.assertEqual(sorted(out), ["badentry", "boom", "notdict", "ok"])
        self.assertNotIn("error", out["ok"])
        self.assertEqual(out["ok"]["items"][0]["delete_mode"], "list-only")
        for n in ("boom", "notdict", "badentry"):
            self.assertIn("error", out[n], n)


class EntryContract(unittest.TestCase):
    def test_rules(self):
        h = safety.HOME
        e = registry.entry("f", h + "/.ssh/id_x", safety="safe", delete_mode="trash", size=1)
        self.assertEqual((e["safety"], e["delete_mode"]), ("protected", "list-only"))
        e = registry.entry("f", "/System/Library/X", safety="review", delete_mode="trash", reason="r")
        self.assertEqual((e["safety"], e["delete_mode"]), ("protected", "list-only"))
        self.assertTrue(e["reason"].startswith("r · "))
        e = registry.entry("f", h + "/Library/Caches/com.example.cache", safety="safe", delete_mode="trash")
        self.assertEqual((e["safety"], e["delete_mode"]), ("safe", "trash"))
        self.assertEqual(set(e) >= {"id", "path", "size", "safety", "delete_mode", "reason"}, True)
        self.assertEqual(registry.entry("f", "/System/Library/X")["safety"], "inventory")  # chỉ liệt kê: giữ nguyên
        for kw in (dict(safety="info"), dict(delete_mode="rm"), dict(safety="safe", delete_mode="permanent")):
            with self.assertRaises(ValueError, msg=kw):
                registry.entry("f", h + "/Library/Caches/x", **kw)
        with self.assertRaises(ValueError):
            registry.entry("f", "relative/path")
        with self.assertRaises(ValueError):
            registry.check_entry(dict(id="1", path="/x", safety="protected", delete_mode="trash", reason=""))


class StatusFeature(unittest.TestCase):
    def test_parsers(self):
        vm = status.parse_vm_stat(VM_STAT)
        self.assertEqual((vm["page_size"], vm["pages_wired_down"], vm["pages_occupied_by_compressor"]), (16384, 400, 50))
        self.assertEqual(status.parse_boottime("{ sec = 1700000000, usec = 5 } Tue Nov 14"), 1700000000)
        self.assertIsNone(status.parse_boottime(""))

    def test_gather_with_fake_commands(self):
        answers = {"hw.memsize": "17179869184\n", "kern.boottime": "{ sec = 1000, usec = 0 }\n"}
        fake = lambda cmd, timeout=60: VM_STAT if cmd[0].endswith("vm_stat") else answers[cmd[-1]]  # noqa: E731
        info = status.gather(run=fake, now=1000 + 90061)
        self.assertEqual(info["uptime_seconds"], 90061)
        self.assertEqual(info["memory"]["total"], 17179869184)
        self.assertEqual(info["memory"]["used_estimate"], (200 + 400 + 50) * 16384)
        self.assertEqual(status._human(90061), "1 ngày 1 giờ 1 phút")

    def test_collect_is_json(self):
        res = status.collect(registry.ReportContext())
        json.dumps(res)
        self.assertEqual(res["items"], [])
        self.assertEqual(set(res["summary"]), {"disk", "memory", "boot_time", "uptime_seconds", "load_avg",
                                               "cpu", "battery", "net", "devices", "alerts"})  # monitor (§3)

    def test_cli_json(self):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(mc.main(["status", "--json"]), 0)
        self.assertIn("disk", json.loads(out.getvalue()))


class ScanIntegration(unittest.TestCase):
    """scan gọi collect() của tính năng và ghi report["features"]; định dạng cũ giữ nguyên."""
    def test_scan_report_has_features(self):
        names = ["scan_apps", "scan_plugins", "scan_extensions", "walk_home", "scan_git", "scan_caches",
                 "find_backups", "scan_junk"]
        old = {n: getattr(mc, n) for n in names}
        try:
            for n in names:
                setattr(mc, n, (lambda *a, **k: ([], [], [])) if n == "walk_home" else (lambda *a, **k: []))
            rep = mc.scan(log=lambda *_: None, features=[("status", status)])
        finally:
            for n, f in old.items():
                setattr(mc, n, f)
        self.assertEqual(set(rep) >= {"generatedAt", "host", "home", "disk", "seconds", "items", "features"}, True)
        self.assertIn("summary", rep["features"]["status"])
        with tempfile.TemporaryDirectory() as d:
            stem = mc.write_reports(rep, d)
            with open(stem + ".json", encoding="utf-8") as f:
                self.assertIn("status", json.load(f)["features"])


class IsolatedMode(unittest.TestCase):
    def test_help_under_python_isolated_lists_features(self):
        r = subprocess.run([sys.executable, "-I", os.path.join(REPO, "maccleaner.py"), "--help"],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("status", r.stdout)


if __name__ == "__main__":
    unittest.main()
