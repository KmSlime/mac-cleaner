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
from mclib import registry  # noqa: E402
from mclib.features import startup  # noqa: E402

LAUNCHCTL_LIST = "PID\tStatus\tLabel\n123\t0\tcom.example.running\n-\t0\tcom.example.loaded\n-\t-9\tcom.apple.x\n"
PRINT_DISABLED = '\tdisabled services = {\n\t\t"com.example.off" => disabled\n\t\t"com.example.on" => enabled\n\t}\n'


def btm_sample(user_plist, app_path):
    return f"""========================
 Records for UID 501 : 00000000-0000-0000-0000-000000000000
========================

 ServiceManagement migrated: true

 Items:

 #1:
                 UUID: 11111111-1111-1111-1111-111111111111
                 Name: Example Corp
       Developer Name: Example Corp
                 Type: developer (0x20)
                Flags: [  ] (0)
          Disposition: [disabled, allowed, not notified] (0x2)
           Identifier: Example Corp
                  URL: (null)
           Generation: 0
  Embedded Item Identifiers:
    #1: 2.com.example.helper

 #2:
                 UUID: 22222222-2222-2222-2222-222222222222
                 Name: Example
       Developer Name: Example Corp
      Team Identifier: ABCDE12345
                 Type: legacy agent (0x10008)
                Flags: [ legacy ] (0x1)
          Disposition: [enabled, allowed, notified] (0xb)
           Identifier: 8.com.example.running
                  URL: file://{user_plist.replace(' ', '%20')}
      Executable Path: /nonexistent/example
           Generation: 1
    Parent Identifier: Example Corp

 #3:
                 UUID: 33333333-3333-3333-3333-333333333333
                 Name: Example App
       Developer Name: Example Corp
                 Type: app (0x2)
                Flags: [  ] (0)
          Disposition: [enabled, allowed, notified] (0xb)
           Identifier: 2.com.example.app
                  URL: file://{app_path}/
    Bundle Identifier: com.example.app
           Generation: 0
    Parent Identifier: Example Corp

 #4:
                 UUID: 44444444-4444-4444-4444-444444444444
                 Name: Helper
       Developer Name: Example Corp
                 Type: agent (0x8)
                Flags: [  ] (0)
          Disposition: [disabled, allowed, notified] (0xa)
           Identifier: 8.com.example.helper
                  URL: (null)
      Executable Path: Contents/MacOS/helper
           Generation: 0
    Parent Identifier: 2.com.example.app

 #5:
                 UUID: 55555555-5555-5555-5555-555555555555
                 Name: Preview
                 Type: quicklook (0x800)
          Disposition: [enabled, allowed, notified] (0xb)
           Identifier: com.example.ql
                  URL: (null)
"""


class Fake:
    """Lệnh giả: ghi lại lệnh đã gọi, trả lời theo tiền tố."""
    def __init__(self, answers=None):
        self.calls, self.answers = [], answers or {}

    def __call__(self, cmd, timeout=30):
        self.calls.append(list(cmd))
        for k, v in self.answers.items():
            if " ".join(cmd).startswith(k):
                return v
        return 113, "", "Could not find service"


def write_plist(path, data):
    with open(path, "wb") as f:
        plistlib.dump(data, f)


class Env:
    def __init__(self, d):
        self.home = os.path.join(d, "home")
        self.ua = os.path.join(self.home, "Library", "LaunchAgents")
        self.sys_agents, self.sys_daemons = os.path.join(d, "SysAgents"), os.path.join(d, "SysDaemons")
        self.data = os.path.join(d, "data")
        for p in (self.ua, self.sys_agents, self.sys_daemons):
            os.makedirs(p)
        self.app = os.path.join(d, "Apps", "Example.app")
        os.makedirs(os.path.join(self.app, "Contents", "MacOS"))
        self.prog = os.path.join(self.app, "Contents", "MacOS", "helper")
        open(self.prog, "w").close()
        self.system_dirs = ((self.sys_agents, "agent"), (self.sys_daemons, "daemon"))
        P = lambda n: os.path.join(self.ua, n)  # noqa: E731
        write_plist(P("com.example.running.plist"), {"Label": "com.example.running", "ProgramArguments": [self.prog, "-x"]})
        write_plist(P("com.example.broken.plist"), {"Label": "com.example.broken", "Program": "/nonexistent/gone"})
        write_plist(P("com.apple.fake.plist"), {"Label": "com.apple.fake", "Program": self.prog})
        write_plist(P("com.crowdstrike.fake.plist"), {"Label": "com.crowdstrike.fake", "Program": self.prog})
        write_plist(P("com.example.off.plist.disabled"), {"Label": "com.example.off", "Program": self.prog})
        write_plist(P("nolabel.plist"), {"Program": self.prog})
        write_plist(os.path.join(self.sys_daemons, "com.example.daemon.plist"),
                    {"Label": "com.example.daemon", "Program": "/nonexistent/daemon"})
        self.run = Fake({"/bin/launchctl list": (0, LAUNCHCTL_LIST, ""),
                         "/bin/launchctl print-disabled": (0, PRINT_DISABLED, ""),
                         "/bin/launchctl print system/com.example.daemon": (0, "state = running", ""),
                         "/usr/bin/sfltool dumpbtm": (0, btm_sample(P("com.example.running.plist"), self.app), ""),
                         "/bin/launchctl bootout": (0, "", ""), "/bin/launchctl bootstrap": (0, "", "")})

    def gather(self):
        return startup.gather(home=self.home, run=self.run, uid=501, system_dirs=self.system_dirs)

    def change(self, action, label, yes=False):
        out = []
        rc = startup.change(action, label, yes=yes, home=self.home, run=self.run, uid=501,
                            system_dirs=self.system_dirs, data=self.data, out=out.append)
        return rc, "\n".join(out)


class Parsers(unittest.TestCase):
    def test_launchctl(self):
        got = startup.parse_launchctl_list(LAUNCHCTL_LIST)
        self.assertEqual(got["com.example.running"], {"pid": 123, "status": "0"})
        self.assertIsNone(got["com.example.loaded"]["pid"])
        self.assertNotIn("Label", got)
        self.assertEqual(startup.parse_print_disabled(PRINT_DISABLED), {"com.example.off": True, "com.example.on": False})

    def test_btm(self):
        items = startup.parse_btm(btm_sample("/x/Library/LaunchAgents/a b.plist", "/Apps/Example.app"))
        self.assertEqual([i["type"] for i in items], ["developer", "legacy agent", "app", "agent", "quicklook"])
        dev, legacy, app, agent, _ = items
        self.assertEqual(dev["embedded"], ["2.com.example.helper"])
        self.assertEqual((legacy["uid"], legacy["enabled"], legacy["allowed"]), (501, True, True))
        self.assertEqual(legacy["path"], "/x/Library/LaunchAgents/a b.plist")
        self.assertEqual(legacy["disposition"], ["enabled", "allowed", "notified"])
        self.assertEqual((app["path"], app["bundle_id"]), ("/Apps/Example.app", "com.example.app"))
        self.assertEqual((agent["enabled"], agent["executable"], agent["url"]), (False, "Contents/MacOS/helper", None))
        self.assertEqual(startup.parse_btm(""), [])

    def test_refusal_is_by_label_not_program(self):
        self.assertIsNone(startup.refusal({"label": "com.example.py", "program": "/usr/bin/python3"}))
        self.assertIsNotNone(startup.refusal({"label": "com.apple.x", "program": "/opt/x"}))
        self.assertIsNotNone(startup.refusal({"label": "x", "bundle_ids": ["com.apple.y"]}))
        self.assertIsNotNone(startup.refusal({"label": "com.example.x", "program": "/Applications/Falcon.app/x",
                                              "bundle_ids": ["com.crowdstrike.falcon.App"]}))
        self.assertIsNotNone(startup.refusal({"label": "io.kandji.agent"}))

    def test_plist_program(self):
        self.assertEqual(startup.plist_program({"ProgramArguments": ["/a/b", "x"]}), ("/a/b", ["/a/b", "x"]))
        self.assertEqual(startup.plist_program({"Program": "relative"})[0], None)
        self.assertEqual(startup.app_of("/Applications/Foo Bar.app/Contents/MacOS/x"), "Foo Bar")


class Gather(unittest.TestCase):
    def test_inventory(self):
        with tempfile.TemporaryDirectory() as d:
            env = Env(d)
            g = env.gather()
            by = {r["label"]: r for r in g["records"] if r["label"]}
            r = by["com.example.running"]
            self.assertEqual((r["loaded"], r["pid"], r["broken"], r["can_disable"]), (True, 123, False, True))
            self.assertEqual(r["btm"]["type"], "legacy agent")     # BTM gộp vào plist, không thành mục riêng
            self.assertEqual(r["owner"], "Example")
            self.assertTrue(by["com.example.broken"]["broken"])
            self.assertIsNotNone(by["com.apple.fake"]["refusal"])
            self.assertIn("bảo mật", by["com.crowdstrike.fake"]["refusal"])
            self.assertFalse(by["com.crowdstrike.fake"]["can_disable"])
            off = by["com.example.off"]
            self.assertEqual((off["renamed_disabled"], off["loaded"], off["can_disable"]), (True, False, False))
            dmn = by["com.example.daemon"]
            self.assertEqual((dmn["scope"], dmn["kind"], dmn["loaded"], dmn["broken"], dmn["can_disable"]),
                             ("system", "daemon", True, True, False))
            kinds = sorted(r["kind"] for r in g["records"] if r["kind"] in ("login-item", "btm"))
            self.assertEqual(kinds, ["btm", "login-item"])
            helper = next(r for r in g["records"] if r["kind"] == "btm")
            self.assertEqual(helper["program"], env.prog)           # đường dẫn tương đối giải theo app cha
            self.assertEqual(helper["label"], "com.example.helper")  # bỏ tiền tố loại "8." của BTM
            self.assertEqual(helper["btm"]["enabled"], False)
            self.assertEqual(g["btm_skipped"], 2)                   # developer + quicklook
            self.assertEqual(g["errors"], [])
            self.assertTrue(any("print system/com.example.daemon" in " ".join(c) for c in env.run.calls))

    def test_entries_follow_contract(self):
        with tempfile.TemporaryDirectory() as d:
            env = Env(d)
            g = env.gather()
            es = startup.entries(g)
            for e in es:
                registry.check_entry(e)
                self.assertEqual(e["delete_mode"], "list-only")
            json.dumps({"summary": startup.summarize(g), "items": es})
            by = {e["label"]: e for e in es if e["label"]}
            self.assertEqual(by["com.example.broken"]["safety"], "review")
            self.assertIn("Thùng rác", by["com.example.broken"]["reason"])
            self.assertEqual(by["com.crowdstrike.fake"]["safety"], "protected")
            self.assertEqual(by["com.example.daemon"]["safety"], "review")   # hỏng nhưng hệ thống: vẫn chỉ liệt kê
            self.assertIn("admin", by["com.example.daemon"]["reason"])
            s = startup.summarize(g)
            self.assertEqual((s["user_agents"], s["system_daemons"], s["broken"], s["disabled"]), (6, 1, 2, 1))

    def test_missing_tools_are_reported(self):
        with tempfile.TemporaryDirectory() as d:
            env = Env(d)
            g = startup.gather(home=env.home, run=lambda cmd, timeout=30: (None, "", "no tool"), uid=501,
                               system_dirs=env.system_dirs)
            self.assertTrue(any("launchctl" in e for e in g["errors"]))
            self.assertTrue(any("sfltool" in e for e in g["errors"]))
            self.assertTrue(all(r["loaded"] in (None, False) for r in g["records"]))


class Change(unittest.TestCase):
    def test_disable_preview_touches_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            env = Env(d)
            before = sorted(os.listdir(env.ua))
            rc, out = env.change("disable", "com.example.running")
            self.assertEqual(rc, 0)
            self.assertIn("Xem trước", out)
            self.assertEqual(sorted(os.listdir(env.ua)), before)
            self.assertFalse(any("bootout" in c for c in env.run.calls))
            self.assertFalse(os.path.exists(env.data))

    def test_disable_then_enable(self):
        with tempfile.TemporaryDirectory() as d:
            env = Env(d)
            src = os.path.join(env.ua, "com.example.running.plist")
            rc, out = env.change("disable", "com.example.running", yes=True)
            self.assertEqual(rc, 0, out)
            self.assertFalse(os.path.exists(src))
            self.assertTrue(os.path.exists(src + ".disabled"))
            self.assertIn(["/bin/launchctl", "bootout", "gui/501", src], env.run.calls)
            self.assertEqual(len(os.listdir(os.path.join(env.data, "logs"))), 1)
            rc, out = env.change("disable", "com.example.running", yes=True)
            self.assertEqual(rc, 0)
            self.assertIn("sẵn", out)
            rc, out = env.change("enable", "com.example.running", yes=True)
            self.assertEqual(rc, 0, out)
            self.assertTrue(os.path.exists(src))
            self.assertIn(["/bin/launchctl", "bootstrap", "gui/501", src], env.run.calls)

    def test_bootout_not_loaded_is_ok_but_real_error_stops(self):
        with tempfile.TemporaryDirectory() as d:
            env = Env(d)
            src = os.path.join(env.ua, "com.example.running.plist")
            env.run.answers["/bin/launchctl bootout"] = (3, "", "Boot-out failed: 3: No such process")
            self.assertEqual(env.change("disable", "com.example.running", yes=True)[0], 0)
            os.rename(src + ".disabled", src)
            env.run.answers["/bin/launchctl bootout"] = (1, "", "Operation not permitted")
            rc, out = env.change("disable", "com.example.running", yes=True)
            self.assertEqual(rc, 1)
            self.assertTrue(os.path.exists(src))          # lỗi thật → không đổi tên

    def test_refusals(self):
        with tempfile.TemporaryDirectory() as d:
            env = Env(d)
            before = sorted(os.listdir(env.ua))
            self.assertEqual(env.change("disable", "com.apple.fake", yes=True)[0], 2)
            self.assertEqual(env.change("disable", "com.crowdstrike.fake", yes=True)[0], 2)
            rc, out = env.change("disable", "com.example.daemon", yes=True)
            self.assertEqual(rc, 2)
            self.assertIn("admin", out)
            self.assertEqual(env.change("disable", "no.such.label", yes=True)[0], 1)
            open(os.path.join(env.ua, "com.example.broken.plist.disabled"), "w").close()   # đích đã có → không ghi đè
            self.assertEqual(env.change("disable", "com.example.broken", yes=True)[0], 2)
            self.assertEqual(sorted(os.listdir(env.ua)), sorted(before + ["com.example.broken.plist.disabled"]))
            self.assertFalse(any(c[1] in ("bootout", "bootstrap") for c in env.run.calls))

    def test_broken_agent_gets_trash_hint(self):
        with tempfile.TemporaryDirectory() as d:
            env = Env(d)
            rc, out = env.change("disable", "com.example.broken")
            self.assertEqual(rc, 0)
            self.assertIn("Thùng rác", out)


class Cli(unittest.TestCase):
    def test_list_json_and_disable_preview(self):
        with tempfile.TemporaryDirectory() as d:
            env = Env(d)
            old_gather, old_change = startup.gather, startup.change
            calls = []
            startup.gather = lambda **k: old_gather(home=env.home, run=env.run, uid=501, system_dirs=env.system_dirs)
            startup.change = lambda *a, **k: calls.append((a, k)) or 0
            try:
                with contextlib.redirect_stdout(io.StringIO()) as out:
                    self.assertEqual(mc.main(["startup", "list", "--json"]), 0)
                res = json.loads(out.getvalue())
                self.assertEqual(res["summary"]["broken"], 2)
                with contextlib.redirect_stdout(io.StringIO()) as out:
                    self.assertEqual(mc.main(["startup"]), 0)
                self.assertIn("LaunchAgents của bạn", out.getvalue())
                self.assertEqual(mc.main(["startup", "disable", "com.example.running"]), 0)
                self.assertEqual(calls[-1], (("disable", "com.example.running"), {"yes": False}))
            finally:
                startup.gather, startup.change = old_gather, old_change


if __name__ == "__main__":
    unittest.main()
