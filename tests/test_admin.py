"""admin grants: fake sudo/osascript only — no dialog, nothing written outside the temp dir."""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import maccleaner as mc  # noqa: E402
from mclib.features import admin as A  # noqa: E402
from mclib.features import maintain as M  # noqa: E402


class Machine:
    """A fake sudoers file: osascript "installs" what the root script would write, `sudo -n -l` lists it back."""

    def __init__(self, tmp, dialog_rc=0):
        self.path, self.calls, self.dialog_rc = os.path.join(tmp, "mac-cleaner"), [], dialog_rc
        self.lines = []

    def call(self, cmd, timeout=60):
        self.calls.append(cmd)
        if cmd[0] == A.OSASCRIPT:
            if self.dialog_rc:
                return self.dialog_rc, "execution error: User canceled. (-128)"
            script = cmd[2]
            self.lines = [ln for ln in A.sudoers_lines(set(A.IDS), A.user_name())[1:] if ln in script]
            if self.lines:
                open(self.path, "w").close()
            elif os.path.exists(self.path):
                os.remove(self.path)
            return 0, ""
        if cmd[:3] == [A.SUDO, "-n", "-l"]:
            return 0, "User x may run the following commands on host:\n    (ALL) ALL\n" + "".join(
                "    (root) NOPASSWD: " + ln.split("NOPASSWD: ", 1)[1] + "\n" for ln in self.lines)
        raise AssertionError(cmd)


class Rules(unittest.TestCase):
    def test_only_fixed_absolute_commands_under_sip(self):
        for g in A.GRANTS:
            for c in g["commands"]:
                self.assertTrue(c[0].startswith(("/usr/bin/", "/usr/sbin/")), c)
                self.assertFalse(any(ch in " ".join(c) for ch in "*?[]<>\\,:="), c)  # no wildcard, no placeholder
        self.assertEqual(A.rule(["/usr/sbin/purge"]), '/usr/sbin/purge ""')  # bare path would allow any argument

    def test_maintenance_grants_match_the_maintain_tasks(self):
        for g in A.GRANTS[1:]:
            self.assertEqual([["sudo"] + c for c in g["commands"]], M.find(g["id"])["commands"], g["id"])
        for never in ("tm-snapshots-delete", "periodic"):  # free argument / runs user-writable scripts as root
            self.assertNotIn(never, A.IDS)

    def test_generated_file_passes_visudo(self):
        if not os.access(A.VISUDO, os.X_OK):
            self.skipTest("no visudo")
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "f")
            with open(p, "w") as f:
                f.write("\n".join(A.sudoers_lines(set(A.IDS), A.user_name())) + "\n")
            os.chmod(p, 0o440)
            r = subprocess.run([A.VISUDO, "-cf", p], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_root_script_checks_before_installing(self):
        s = A.root_script(A.sudoers_lines({"btm-read"}, "me"), "/x/y")
        self.assertLess(s.index("visudo -cf"), s.index("/usr/bin/install -m 0440 -o root -g wheel"))
        self.assertIn("'me ALL=(root) NOPASSWD: /usr/bin/sfltool dumpbtm'", s)
        self.assertEqual(subprocess.run(["/bin/sh", "-n", "-c", s]).returncode, 0)
        self.assertEqual(A.root_script(A.sudoers_lines(set(), "me"), "/x/y"), "/bin/rm -f /x/y")


class GrantRevoke(unittest.TestCase):
    def test_no_grant_file_means_sudo_is_never_started(self):
        with tempfile.TemporaryDirectory() as d:
            m = Machine(d)
            self.assertEqual(A.granted(m.call, m.path), set())
            self.assertEqual(m.calls, [])

    def test_preview_changes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            m = Machine(d)
            res = A.change("grant", ["btm-read"], call=m.call, path=m.path)
            self.assertEqual((res["status"], res["granted"], m.calls), ("preview", ["btm-read"], []))
            self.assertEqual(len(res["lines"]), 1)

    def test_grant_then_revoke_keeps_the_other_grants(self):
        with tempfile.TemporaryDirectory() as d:
            m = Machine(d)
            A.change("grant", ["btm-read", "dns-flush"], yes=True, call=m.call, path=m.path)
            self.assertEqual(A.granted(m.call, m.path), {"btm-read", "dns-flush"})
            self.assertEqual([g["id"] for g in A.listing(m.call, m.path) if g["granted"]], ["btm-read", "dns-flush"])
            A.change("revoke", ["dns-flush"], yes=True, call=m.call, path=m.path)
            self.assertEqual(A.granted(m.call, m.path), {"btm-read"})
            A.change("revoke", ["all"], yes=True, call=m.call, path=m.path)
            self.assertFalse(os.path.exists(m.path))
            n = len(m.calls)
            A.change("revoke", ["all"], yes=True, call=m.call, path=m.path)  # nothing to do → no dialog
            self.assertEqual(len(m.calls), n)

    def test_cancel_and_unknown_id(self):
        with tempfile.TemporaryDirectory() as d:
            m = Machine(d, dialog_rc=1)
            with self.assertRaises(A.AdminError) as cm:
                A.change("grant", ["all"], yes=True, call=m.call, path=m.path)
            self.assertIn("huỷ", str(cm.exception))
            for bad in (["periodic"], ["btm-read", "/usr/bin/true"]):
                with self.assertRaises(A.AdminError):
                    A.change("grant", bad, yes=True, call=m.call, path=m.path)
            self.assertEqual(len(m.calls), 1)

    def test_cli(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(mc.main(["admin", "grant", "btm-read", "--json"]), 0)  # preview: no dialog
        res = json.loads(out.getvalue())
        self.assertEqual((res["status"], res["ids"]), ("preview", ["btm-read"]))


if __name__ == "__main__":
    unittest.main()
