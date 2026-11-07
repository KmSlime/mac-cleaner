import contextlib
import io
import json
import os
import plistlib
import stat
import sys
import tempfile
import types
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import maccleaner as mc  # noqa: E402
from mclib import registry, safety  # noqa: E402
from mclib.features import _fs, cloud  # noqa: E402


def put(path, data=b"x" * 4096):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    return path


def fake_home(d):
    home = os.path.realpath(d)
    put(os.path.join(home, "Library", "Mobile Documents", "com~apple~CloudDocs", "doc.txt"), b"y" * 8192)
    with open(os.path.join(home, "Library", "Mobile Documents", "com~apple~CloudDocs", ".gone.pdf.icloud"), "wb") as f:
        plistlib.dump({"NSURLNameKey": "gone.pdf", "NSURLFileSizeKey": 123456}, f)
    put(os.path.join(home, "Library", "CloudStorage", "OneDrive-Personal", "a.bin"))
    put(os.path.join(home, "Library", "CloudStorage", "GoogleDrive-someone", "My Drive", "b.bin"))
    os.makedirs(os.path.join(home, "Library", "CloudStorage", ".hidden"))
    return home


class Providers(unittest.TestCase):
    def test_provider_names(self):
        self.assertEqual(cloud.provider_of("OneDrive-Personal"), "OneDrive")
        self.assertEqual(cloud.provider_of("GoogleDrive-someone"), "Google Drive")
        self.assertEqual(cloud.provider_of("Dropbox"), "Dropbox")
        self.assertEqual(cloud.provider_of("Box-Box"), "Box")
        self.assertEqual(cloud.provider_of("NewThing-acc"), "NewThing")

    def test_find_roots(self):
        with tempfile.TemporaryDirectory() as d:
            home = fake_home(d)
            self.assertEqual([p for p, _ in cloud.find_roots(home)], ["iCloud Drive", "Google Drive", "OneDrive"])
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(cloud.find_roots(d), [])


class Measure(unittest.TestCase):
    def test_local_and_placeholder(self):
        with tempfile.TemporaryDirectory() as d:
            home = fake_home(d)
            m = cloud.measure(os.path.join(home, "Library", "Mobile Documents"))
            self.assertEqual(m["fileCount"], 2)
            self.assertEqual(m["cloudOnlyFiles"], 1)
            self.assertEqual(m["cloudOnlyBytes"], 123456)
            self.assertGreaterEqual(m["localBytes"], 8192)
            self.assertTrue(m["complete"])
            self.assertEqual(os.path.basename(m["largestLocal"][0]["path"]), "doc.txt")

    def test_dataless_flags_with_fake_stat(self):
        dev = os.lstat("/").st_dev

        def st(mode, flags=0, size=0, blocks=0):
            return types.SimpleNamespace(st_mode=mode, st_flags=flags, st_size=size, st_blocks=blocks, st_dev=dev,
                                         st_nlink=1, st_ino=0)
        tree = {"/r": [("evicted.mov", st(stat.S_IFREG, _fs.SF_DATALESS, 10 ** 9)),
                       ("here.mov", st(stat.S_IFREG, 0, 5000, 16)),
                       ("remote-dir", st(stat.S_IFDIR, _fs.SF_DATALESS))]}

        def scandir(self, path):
            return [(types.SimpleNamespace(name=n, path=path + "/" + n), s) for n, s in tree.get(path, [])]
        with mock.patch.object(_fs.Walk, "scandir", scandir), mock.patch.object(_fs, "allowed_devs", lambda r: {dev}):
            m = cloud.measure("/r")
        self.assertEqual((m["cloudOnlyFiles"], m["cloudOnlyBytesSeen"], m["localBytes"]), (1, 10 ** 9, 16 * 512))
        self.assertEqual(m["unknownDirs"], 1)
        self.assertFalse(m["complete"])
        self.assertIsNone(m["cloudOnlyBytes"])  # không chắc → không xác định
        self.assertIn("không xác định", m["note"])


class Report(unittest.TestCase):
    def test_gather_items_are_list_only(self):
        with tempfile.TemporaryDirectory() as d:
            home = fake_home(d)
            res = cloud.collect(registry.ReportContext(home=home))
            json.dumps(res)
            self.assertEqual(res["summary"]["providers"], ["Google Drive", "OneDrive", "iCloud Drive"])
            self.assertEqual(res["summary"]["cloudOnlyBytes"], 123456)
            self.assertTrue(res["items"])
            for e in res["items"]:
                registry.check_entry(e)
                self.assertEqual(e["delete_mode"], "list-only")
                self.assertIn(e["safety"], ("inventory", "protected"))
            roots = [r for r in res["summary"]["roots"] if r["provider"] == "OneDrive"]
            self.assertEqual(len(roots[0]["files"]), 1)

    def test_cli(self):
        with tempfile.TemporaryDirectory() as d:
            home = fake_home(d)
            with mock.patch.object(safety, "HOME", home), contextlib.redirect_stdout(io.StringIO()) as o:
                self.assertEqual(mc.main(["cloud", "--json"]), 0)
            self.assertEqual(len(json.loads(o.getvalue())["summary"]["roots"]), 3)
            with mock.patch.object(safety, "HOME", home), contextlib.redirect_stdout(io.StringIO()) as o:
                self.assertEqual(mc.main(["cloud"]), 0)
            self.assertIn("app Swift", o.getvalue())
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(safety, "HOME", d), contextlib.redirect_stdout(io.StringIO()) as o:
                self.assertEqual(mc.main(["cloud"]), 0)
            self.assertIn("Không thấy", o.getvalue())


if __name__ == "__main__":
    unittest.main()
