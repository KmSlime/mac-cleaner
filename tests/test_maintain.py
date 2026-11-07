import contextlib
import io
import json
import os
import subprocess
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import maccleaner as mc  # noqa: E402
from mclib import registry  # noqa: E402
from mclib.features import maintain as M  # noqa: E402

HELP = {"qlmanage": "-r cache\tReset thumbnail disk cache", "lsregister": "-gc  Garbage collect", "tmutil": "listlocalsnapshots",
        "atsutil": "-removeUser"}
fake_help = lambda cmd, timeout=20: HELP.get(os.path.basename(cmd[0]), "")  # noqa: E731
all_bins = lambda name: name  # noqa: E731 — mọi lệnh "có trên máy"


class Recorder:
    def __init__(self, rc=0):
        self.calls, self.rc = [], rc

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        return types.SimpleNamespace(returncode=self.rc, stdout="ok", stderr="")


class Contract(unittest.TestCase):
    def test_runnable_never_root_and_root_tasks_print_sudo(self):
        names = [t["task"] for t in M.TASKS]
        self.assertEqual(len(names), len(set(names)))
        for t in M.TASKS:
            flat = [x for c in t["commands"] for x in c]
            if t["runnable"]:
                self.assertFalse(t["needsRoot"], t["task"])
                self.assertNotIn("sudo", flat, t["task"])
            if t["needsRoot"]:
                self.assertFalse(t["runnable"], t["task"])
                self.assertTrue(all(c[0] == "sudo" for c in t["commands"]), t["task"])
            self.assertNotIn("-delete", flat)  # lsregister -delete cần khởi động lại → không dùng

    def test_available_probes_binary_and_option(self):
        t = M.find("quicklook-cache")
        self.assertEqual(M.available(t, fake_help, all_bins), (True, ""))
        self.assertFalse(M.available(t, lambda c, timeout=20: "Usage: qlmanage -h", all_bins)[0])  # tuỳ chọn đã bị bỏ
        self.assertFalse(M.available(t, fake_help, lambda n: None)[0])
        lst = M.tasks(fake_help, lambda n: None)
        self.assertFalse(any(x["runnable"] or x["available"] for x in lst))
        self.assertTrue(all("unavailable" in x for x in lst))


class Run(unittest.TestCase):
    def run_task(self, name, yes=False, rc=0):
        rec, logs = Recorder(rc), []
        code = M.run_task(name, yes, log=logs.append, runner=rec, run=fake_help, which=all_bins)
        return code, rec.calls, "\n".join(logs)

    def test_unknown_root_and_print_only_never_execute(self):
        self.assertEqual(self.run_task("nope", True)[:2], (2, []))
        code, calls, out = self.run_task("dns-flush", True)
        self.assertEqual((code, calls), (1, []))
        self.assertIn("sudo /usr/bin/dscacheutil -flushcache", out)
        code, calls, out = self.run_task("font-cache-user", True)
        self.assertEqual((code, calls), (1, []))
        self.assertIn("atsutil databases -removeUser", out)

    def test_preview_then_yes(self):
        code, calls, out = self.run_task("quicklook-cache")
        self.assertEqual((code, calls), (0, []))
        self.assertIn("--yes", out)
        code, calls, _ = self.run_task("quicklook-cache", True)
        self.assertEqual(code, 0)
        self.assertEqual(calls, [["/usr/bin/qlmanage", "-r", "cache"], ["/usr/bin/qlmanage", "-r"]])
        self.assertEqual(self.run_task("tm-snapshots", True, rc=3)[0], 3)

    def test_runner_error(self):
        def boom(cmd, **kw):
            raise subprocess.TimeoutExpired(cmd, 1)
        self.assertEqual(M.run_task("quicklook-cache", True, log=lambda *_: None, runner=boom, run=fake_help,
                                    which=all_bins), 1)


class Cli(unittest.TestCase):
    def test_collect_and_list_json(self):
        res = M.collect(registry.ReportContext())
        json.dumps(res)
        self.assertEqual(res["items"], [])
        self.assertEqual({t["task"] for t in res["summary"]["maintenance"]}, {t["task"] for t in M.TASKS})
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(mc.main(["maintain", "list", "--json"]), 0)
        lst = json.loads(out.getvalue())
        self.assertTrue(all({"task", "needsRoot", "runnable", "command"} <= set(t) for t in lst))

    def test_cli_run_root_task_only_prints(self):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(mc.main(["maintain", "run", "purge-ram", "--yes"]), 1)
        self.assertIn("sudo /usr/sbin/purge", out.getvalue())
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(mc.main(["maintain", "run"]), 2)


if __name__ == "__main__":
    unittest.main()
