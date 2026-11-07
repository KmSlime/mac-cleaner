import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import maccleaner as mc  # noqa: E402
from mclib.features import status as st  # noqa: E402

TOP = """Processes: 600 total
CPU usage: 50.0% user, 20.0% sys, 30.0% idle
Processes: 600 total
CPU usage: 3.5% user, 1.5% sys, 95.0% idle
"""
PMSET_AC = "Now drawing from 'AC Power'\n -InternalBattery-0 (id=1)\t80%; AC attached; not charging present: true\n"
PMSET_BATT = ("Now drawing from 'Battery Power'\n -InternalBattery-0 (id=1)\t8%; discharging; 0:25 remaining present: true\n")
PMSET_DESKTOP = "Now drawing from 'AC Power'\n"
IOREG = """+-o AppleSmartBattery  <class AppleSmartBattery>
    {
      "CurrentCapacity" = 80
      "BatteryData" = {"Serial"="SECRETSERIAL","CycleCount"=1}
      "MaxCapacity" = 100
      "Temperature" = 3129
      "IsCharging" = No
      "DesignCapacity" = 5000
      "CycleCount" = 1200
      "AppleRawMaxCapacity" = 3500
      "Serial" = "SECRETSERIAL"
    }
"""
NETSTAT = """Name       Mtu   Network       Address            Ipkts Ierrs     Ibytes    Opkts Oerrs     Obytes  Coll
lo0        16384 <Link#1>                        100     0     5000     100     0     5000     0
lo0        16384 127           localhost         100     -     5000     100     -     5000     -
en0        1500  <Link#11>   aa:bb:cc:dd:ee:ff   10     0     1000      20     0      200     0
en0        1500  192.168.1     192.168.1.2        10     -     1000      20     -      200     -
utun0      1380  <Link#20>                          5     0      300       6     0       60     0
gif0*      1280  <Link#2>                           0     0        0       0     0        0     0
"""
USB = {"SPUSBHostDataType": [{"_name": "USB31Bus", "_items": [
    {"_name": "USB3.1 Hub", "_items": [{"_name": "Keyboard", "USBDeviceKeyVendorName": "Acme",
                                        "USBDeviceKeySerialNumber": "SECRET"}]},
    {"_name": "Flash Drive", "USBDeviceKeyVendorName": "Acme"}]}]}
BT = {"SPBluetoothDataType": [{"controller_properties": {}, "device_connected": [{"Mouse": {"device_minorType": "Mouse"}}],
                               "device_not_connected": [{"Old": {}}]}]}


class Parsers(unittest.TestCase):
    def test_cpu_uses_last_sample(self):
        self.assertEqual(st.parse_top_cpu(TOP), dict(user=3.5, sys=1.5, idle=95.0, busy=5.0))
        self.assertIsNone(st.parse_top_cpu(""))

    def test_swap(self):
        sw = st.parse_swap("total = 2048.00M  used = 1024.00M  free = 1024.00M  (encrypted)")
        self.assertEqual(sw, dict(total=2048 * 1024 ** 2, used=1024 * 1024 ** 2, free=1024 * 1024 ** 2))
        self.assertIsNone(st.parse_swap(""))

    def test_pmset(self):
        self.assertEqual(st.parse_pmset_batt(PMSET_AC),
                         dict(source="AC Power", percent=80, state="AC attached", remaining=None))
        self.assertEqual(st.parse_pmset_batt(PMSET_BATT)["remaining"], "0:25")
        self.assertIsNone(st.parse_pmset_batt(PMSET_DESKTOP))
        self.assertIsNone(st.parse_pmset_batt(""))

    def test_ioreg_skips_nested_and_serial(self):
        b = st.parse_ioreg_battery(IOREG)
        self.assertEqual(b["CycleCount"], 1200)
        self.assertEqual(b["health_percent"], 70.0)
        self.assertEqual(b["temperature_c"], 31.3)
        self.assertIs(b["IsCharging"], False)
        self.assertNotIn("SECRETSERIAL", json.dumps(b))
        self.assertIsNone(st.parse_ioreg_battery(""))

    def test_netstat(self):
        self.assertEqual(st.parse_netstat(NETSTAT), {"en0": {"in": 1000, "out": 200}, "utun0": {"in": 300, "out": 60},
                                                     "gif0": {"in": 0, "out": 0}})
        self.assertEqual(st.parse_netstat(""), {})

    def test_devices(self):
        self.assertEqual(st.parse_usb(USB), [dict(name="Keyboard", vendor="Acme"), dict(name="Flash Drive", vendor="Acme")])
        self.assertNotIn("SECRET", json.dumps(st.parse_usb(USB)))
        self.assertEqual(st.parse_usb({"SPUSBDataType": USB["SPUSBHostDataType"]})[0]["name"], "Keyboard")  # macOS cũ
        self.assertEqual(st.parse_usb({}), [])
        self.assertEqual(st.parse_bluetooth(BT), [dict(name="Mouse", type="Mouse")])
        self.assertEqual(st.parse_bluetooth({}), [])

    def test_volumes_skip_non_mounts(self):
        with tempfile.TemporaryDirectory() as d:
            os.mkdir(os.path.join(d, "NotAMount"))
            os.symlink("/", os.path.join(d, "Macintosh HD"))
            self.assertEqual(st.volumes(d), [])
            self.assertEqual(st.volumes(os.path.join(d, "missing")), [])


class Alerts(unittest.TestCase):
    def info(self, **kw):
        base = dict(disk=dict(total=100 * 1024 ** 3, used=50 * 1024 ** 3, free=50 * 1024 ** 3),
                    memory=dict(total=16, pressure="normal", swap=dict(used=1)), cpu=dict(busy=10.0),
                    battery=dict(source="AC Power", percent=90, state="charged", health_percent=95, CycleCount=10),
                    uptime_seconds=3600)
        base.update(kw)
        return base

    def keys(self, info):
        return {(a["level"], a["key"]) for a in st.alerts(info)}

    def test_quiet_when_healthy(self):
        self.assertEqual(st.alerts(self.info()), [])

    def test_thresholds(self):
        self.assertEqual(self.keys(self.info(disk=dict(total=100, used=97, free=3))), {("critical", "disk")})
        self.assertEqual(self.keys(self.info(disk=dict(total=1000 * 1024 ** 3, used=0, free=8 * 1024 ** 3))),
                         {("critical", "disk")})
        self.assertEqual(self.keys(self.info(disk=dict(total=100 * 1024 ** 3, used=0, free=9 * 1024 ** 3))),
                         {("warn", "disk")})
        k = self.keys(self.info(memory=dict(total=16, pressure="critical", swap=dict(used=9)), cpu=dict(busy=95.0),
                                uptime_seconds=40 * 86400))
        self.assertEqual(k, {("critical", "memory"), ("warn", "swap"), ("warn", "cpu"), ("info", "uptime")})
        k = self.keys(self.info(battery=dict(source="Battery Power", percent=8, state="discharging",
                                             health_percent=70, CycleCount=1200)))
        self.assertEqual(k, {("critical", "battery"), ("warn", "battery-health"), ("info", "battery-cycles")})
        self.assertNotIn(("warn", "battery"), self.keys(self.info(battery=dict(source="AC Power", percent=15))))
        self.assertEqual(st.alerts(self.info(battery=None, disk={}, memory={}, cpu={})), [])


class Monitor(unittest.TestCase):
    def fake(self, outputs):
        def run(cmd, timeout=60):
            key = " ".join(cmd)
            for k, v in outputs.items():
                if k in key:
                    return v
            return ""
        return run

    def test_monitor_with_fake_commands(self):
        out = {"vm_stat": "", "hw.memsize": "17179869184", "kern.boottime": "{ sec = 1000, usec = 0 }",
               "pressure_level": "4", "swapusage": "total = 0.00M  used = 0.00M  free = 0.00M", "top": TOP,
               "hw.ncpu": "8", "pmset": PMSET_BATT, "ioreg": IOREG, "netstat": NETSTAT,
               "SPUSBHostDataType": json.dumps(USB), "SPBluetoothDataType": json.dumps(BT)}
        with tempfile.TemporaryDirectory() as d:
            info = st.monitor(run=self.fake(out), now=1000 + 60, volumes_root=d)
        json.dumps(info)
        self.assertEqual(info["memory"]["pressure"], "critical")
        self.assertEqual(info["cpu"]["cores"], 8)
        self.assertEqual(info["battery"]["percent"], 8)
        self.assertEqual((info["net"]["bytes_in"], info["net"]["bytes_out"]), (1300, 260))
        self.assertNotIn("gif0", info["net"]["interfaces"])
        self.assertEqual([u["name"] for u in info["devices"]["usb"]], ["Keyboard", "Flash Drive"])
        self.assertIn(("critical", "memory"), {(a["level"], a["key"]) for a in info["alerts"]})
        self.assertIn(("critical", "battery"), {(a["level"], a["key"]) for a in info["alerts"]})

    def test_monitor_survives_missing_commands(self):
        with tempfile.TemporaryDirectory() as d:
            info = st.monitor(run=lambda cmd, timeout=60: "", now=0, volumes_root=d)
        json.dumps(info)
        self.assertIsNone(info["battery"])
        self.assertIsNone(info["memory"]["pressure"])
        self.assertEqual(info["devices"], dict(volumes=[], usb=[], bluetooth=[]))
        self.assertEqual(info["net"]["bytes_in"], 0)

    def test_cli_text_and_json(self):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(mc.main(["status"]), 0)
        self.assertIn("Ổ đĩa", out.getvalue())
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(mc.main(["status", "--json"]), 0)
        self.assertTrue({"cpu", "battery", "net", "devices", "alerts"} <= set(json.loads(out.getvalue())))


if __name__ == "__main__":
    unittest.main()
