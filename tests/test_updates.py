"""Test liệt kê cập nhật: appcast mẫu, bundle giả, lệnh giả — không dùng mạng."""
import contextlib
import io
import json
import os
import plistlib
import sys
import tempfile
import unittest
import urllib.error
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)
import maccleaner as mc  # noqa: E402
from mclib import registry  # noqa: E402
from mclib.features import updates as UP  # noqa: E402

APPCAST = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0" xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle">
  <channel>
    <title>Demo</title>
    <item>
      <title>2.1.0</title>
      <sparkle:version>210</sparkle:version>
      <sparkle:shortVersionString>2.1.0</sparkle:shortVersionString>
      <enclosure url="https://example.com/Demo-2.1.0.zip" length="1" type="application/octet-stream"/>
    </item>
    <item>
      <title>3.0 beta</title>
      <sparkle:channel>beta</sparkle:channel>
      <enclosure url="https://example.com/Demo-3.0b.zip" sparkle:version="300" sparkle:shortVersionString="3.0b1"/>
    </item>
    <item>
      <title>Windows build</title>
      <enclosure url="https://example.com/Demo.exe" sparkle:os="windows" sparkle:version="999"
                 sparkle:shortVersionString="9.9"/>
    </item>
    <item>
      <title>2.0.5</title>
      <enclosure url="https://example.com/Demo-2.0.5.zip" sparkle:version="205" sparkle:shortVersionString="2.0.5"/>
    </item>
  </channel>
</rss>
"""

BREW = json.dumps({"formulae": [], "casks": [
    {"name": "demo-cask", "installed_versions": ["1.0"], "current_version": "1.2"}]})

SWU_NEW = """Software Update Tool

Finding available software
Software Update found the following new or updated software:
* Label: Demo Browser 2.0-2.0
\tTitle: Demo Browser, Version: 2.0, Size: 1000KiB, Recommended: YES,
* Label: macOS Demo 99.1-ABC
\tTitle: macOS Demo 99.1, Version: 99.1, Size: 9999KiB, Recommended: YES, Action: restart,
"""
SWU_OLD = """Software Update found the following new or updated software:
   * Demo Update-1.1
\tDemo Update (1.1), 1234K [recommended] [restart]
"""


def write_app(root, name, info):
    p = os.path.join(root, name + ".app", "Contents", "Info.plist")
    os.makedirs(os.path.dirname(p))
    with open(p, "wb") as f:
        plistlib.dump(info, f)
    return os.path.realpath(os.path.join(root, name + ".app"))


def no_network(*a, **k):
    raise AssertionError("không được dùng mạng")


def fake_run(brew=BREW, swu="No new software available.\n"):
    def run(cmd, timeout, env=None):
        if cmd[0].endswith("brew"):
            return 0, brew, ""
        if cmd[0].endswith("softwareupdate"):
            return 0, swu, ""
        return None, "", "?"
    return run


class Parse(unittest.TestCase):
    def test_appcast_picks_newest_stable_mac_item(self):
        best = UP.parse_appcast(APPCAST)
        self.assertEqual((best["version"], best["build"]), ("2.1.0", "210"))
        self.assertEqual(best["url"], "https://example.com/Demo-2.1.0.zip")

    def test_appcast_rejects_entities(self):
        with self.assertRaises(ValueError):
            UP.parse_appcast('<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><rss>&a;</rss>')

    def test_newer(self):
        self.assertTrue(UP.newer("1.10", "1.9"))
        self.assertTrue(UP.newer("2.0.1", "2.0"))
        self.assertFalse(UP.newer("2.0", "2.0.0"))
        self.assertFalse(UP.newer("1.0", "1.0.1"))
        self.assertFalse(UP.newer("", "1.0"))

    def test_brew_json(self):
        rows = UP.parse_brew(BREW)
        self.assertEqual([(r["app"], r["current"], r["latest"], r["source"]) for r in rows],
                         [("demo-cask", "1.0", "1.2", "brew")])

    def test_softwareupdate_formats(self):
        rows = UP.parse_softwareupdate(SWU_NEW, "99.0")
        self.assertEqual([(r["app"], r["latest"], r["current"]) for r in rows],
                         [("Demo Browser", "2.0", ""), ("macOS Demo 99.1", "99.1", "99.0")])
        self.assertTrue(rows[1]["restart"] and rows[1]["recommended"])
        old = UP.parse_softwareupdate(SWU_OLD)
        self.assertEqual([(r["app"], r["latest"], r["restart"]) for r in old], [("Demo Update", "1.1", True)])
        self.assertEqual(UP.parse_softwareupdate("No new software available."), [])


class Sparkle(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = self._td.name
        self.demo = write_app(self.root, "Demo", dict(CFBundleIdentifier="com.example.demo", CFBundleName="Demo",
                                                      CFBundleShortVersionString="2.0.5", CFBundleVersion="205",
                                                      SUFeedURL="https://example.com/appcast.xml"))
        write_app(self.root, "Local", dict(CFBundleIdentifier="com.example.local", SUFeedURL="file:///etc/passwd"))
        write_app(self.root, "Plain", dict(CFBundleIdentifier="com.example.plain"))

    def tearDown(self):
        self._td.cleanup()

    def test_only_http_feeds_are_listed(self):
        apps = UP.sparkle_apps([self.root])
        self.assertEqual([a["path"] for a in apps], [self.demo])

    def test_update_found(self):
        app = UP.sparkle_apps([self.root])[0]
        row = UP.check_sparkle(app, fetch=lambda url, t: APPCAST)
        self.assertEqual((row["status"], row["current"], row["latest"]), ("update", "2.0.5", "2.1.0"))

    def test_network_error_is_recorded_not_raised(self):
        app = UP.sparkle_apps([self.root])[0]

        def boom(url, t):
            raise urllib.error.URLError("timed out")
        row = UP.check_sparkle(app, fetch=boom)
        self.assertEqual(row["status"], "error")
        self.assertIn("timed out", row["error"])
        bad = UP.check_sparkle(app, fetch=lambda u, t: "<rss><broken")
        self.assertEqual(bad["status"], "error")

    def test_fetch_refuses_non_http(self):
        with self.assertRaises(ValueError):
            UP.fetch("file:///etc/passwd")

    def test_offline_uses_no_network(self):
        g = UP.gather(offline=True, roots=[self.root], fetch=no_network, run=fake_run(swu=None), brew="/x/brew")
        self.assertTrue(g["offline"])
        self.assertEqual([(r["source"], r["status"]) for r in g["updates"]], [("sparkle", "offline"), ("brew", "update")])

    def test_online_gathers_all_sources(self):
        g = UP.gather(roots=[self.root], fetch=lambda u, t: APPCAST, run=fake_run(swu=SWU_NEW), brew="/x/brew")
        self.assertEqual(sorted(r["source"] for r in g["updates"] if r["status"] == "update"),
                         ["brew", "macos", "macos", "sparkle"])
        self.assertEqual(g["errors"], [])

    def test_brew_and_softwareupdate_errors_dont_break(self):
        def run(cmd, timeout, env=None):
            return None, "", "quá 1s"
        g = UP.gather(roots=[self.root], fetch=lambda u, t: APPCAST, run=run, brew="/x/brew")
        self.assertEqual(len(g["errors"]), 2)
        self.assertEqual([r["status"] for r in g["updates"]], ["update"])

    def test_cli_offline_json_and_collect(self):
        with mock.patch.object(UP, "app_roots", return_value=[self.root]), \
                mock.patch.object(UP, "brew_path", return_value=None), \
                mock.patch.object(UP, "fetch", no_network), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(mc.main(["updates", "--offline", "--json"]), 0)
            g = json.loads(out.getvalue())
            self.assertEqual(g["updates"][0]["status"], "offline")
            res = UP.collect(registry.ReportContext(home=self.root))
        json.dumps(res)
        for e in res["items"]:
            registry.check_entry(e)
        self.assertTrue(res["summary"]["offline"])


if __name__ == "__main__":
    unittest.main()
