"""Hook PreToolUse confirm-cleanup.sh: lệnh đổi trạng thái máy (--yes) → "ask"; lệnh xem trước → đi qua."""
import json
import os
import subprocess
import unittest

HOOK = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hooks", "confirm-cleanup.sh")


def decide(cmd):
    out = subprocess.run(["bash", HOOK], input=json.dumps({"tool_input": {"command": cmd}}),
                         capture_output=True, text=True, timeout=10).stdout.strip()
    return json.loads(out)["hookSpecificOutput"]["permissionDecision"] if out else None


class ConfirmCleanupHook(unittest.TestCase):
    def test_state_changing_commands_ask(self):
        for c in ["mac-cleaner clean --ids a --yes", "mac-cleaner clean --yes --ids a",
                  "python3 maccleaner.py clean --category junk --permanent --yes",
                  "mac-cleaner uninstall --yes com.example.app", "mac-cleaner startup --yes disable com.example.agent",
                  "mac-cleaner startup enable com.example.agent --yes", "mac-cleaner privacy reset Camera com.example.app --yes",
                  "mac-cleaner maintain --yes run quicklook-cache", "mo clean", "ls ~/.Trash"]:
            self.assertEqual(decide(c), "ask", c)

    def test_preview_commands_pass(self):
        for c in ["mac-cleaner scan", "mac-cleaner clean --ids a,b", "mac-cleaner uninstall com.example.app",
                  "mac-cleaner startup list --yes", "mac-cleaner privacy list --yes", "mac-cleaner maintain run quicklook-cache",
                  "mac-cleaner status; echo --yes", "mo clean --dry-run"]:
            self.assertIsNone(decide(c), c)


if __name__ == "__main__":
    unittest.main()
