import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, ".claude", "hooks", "check_tool.py")
PAYLOAD = json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"}})
PRE_COMMAND = 'python3 "$CLAUDE_PROJECT_DIR/.claude/hooks/check_tool.py" --mode pre'
EXPECTED_HOOKS = {
    "PreToolUse": [
        {
            "matcher": "Write|Edit|MultiEdit",
            "hooks": [{"type": "command", "command": PRE_COMMAND}],
        },
    ],
}


def _in_git_work_tree():
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
    except OSError:
        return False
    return result.returncode == 0


GIT_OK = _in_git_work_tree()


class Skeleton(unittest.TestCase):
    def check_ignore(self, path):
        return subprocess.run(
            ["git", "check-ignore", "--no-index", path],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )

    @unittest.skipUnless(GIT_OK, "git work tree unavailable")
    def test_committed_paths_not_ignored(self):
        for path in (".claude/settings.json", ".claude/hooks/check_tool.py"):
            with self.subTest(path=path):
                result = self.check_ignore(path)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, "")

    @unittest.skipUnless(GIT_OK, "git work tree unavailable")
    def test_local_paths_still_ignored(self):
        for path in (".claude/settings.local.json", ".claude/.cc-writes/probe"):
            with self.subTest(path=path):
                result = self.check_ignore(path)
                self.assertEqual(result.returncode, 0)
                self.assertEqual(result.stdout, path + "\n")

    def test_settings_hooks(self):
        with open(os.path.join(ROOT, ".claude", "settings.json")) as fh:
            settings = json.load(fh)
        self.assertEqual(set(settings), {"hooks"})
        self.assertEqual(settings["hooks"], EXPECTED_HOOKS)

    def run_script(self, *args):
        return subprocess.run(
            [sys.executable, SCRIPT] + list(args),
            cwd=ROOT,
            input=PAYLOAD,
            capture_output=True,
            text=True,
        )

    def test_valid_modes_exit_zero(self):
        for mode in ("pre", "post"):
            with self.subTest(mode=mode):
                self.assertEqual(self.run_script("--mode", mode).returncode, 0)

    def test_invalid_mode_exits_two(self):
        self.assertEqual(self.run_script("--mode", "bogus").returncode, 2)


class SecretPaths(unittest.TestCase):
    BLOCKED = (
        ".env",
        ".env.local",
        "config/.env.production",
        "/abs/repo/server.pem",
        "keys/id.key",
        "secrets/token.txt",
        "a/secrets/b/c.json",
    )
    ALLOWED = (
        "docs/env.md",
        "tests/test_keys.py",
        "src/keyboard.py",
        "secrets.md",
        "my_secrets/x.txt",
        "notes/pem.txt",
        "iwb",
    )
    EDIT_TOOLS = ("Write", "Edit", "MultiEdit")

    def setUp(self):
        self.cwd = tempfile.mkdtemp(dir="/tmp")

    def tearDown(self):
        shutil.rmtree(self.cwd, ignore_errors=True)

    def run_hook(self, stdin):
        return subprocess.run(
            [sys.executable, SCRIPT, "--mode", "pre"],
            cwd=self.cwd,
            input=stdin,
            capture_output=True,
            text=True,
        )

    def call(self, tool, tool_input):
        return self.run_hook(json.dumps({"tool_name": tool, "tool_input": tool_input}))

    def test_secret_paths_blocked(self):
        for tool in self.EDIT_TOOLS:
            for path in self.BLOCKED:
                with self.subTest(tool=tool, path=path):
                    result = self.call(tool, {"file_path": path})
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertIn("secret", result.stderr)
                    self.assertIn("Keychain", result.stderr)
                    self.assertIn("prbot", result.stderr)

    def test_ordinary_paths_allowed(self):
        for tool in self.EDIT_TOOLS:
            for path in self.ALLOWED:
                with self.subTest(tool=tool, path=path):
                    result = self.call(tool, {"file_path": path})
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stderr, "")

    def test_other_tools_allowed(self):
        cases = (
            ("Read", {"file_path": ".env"}),
            ("Bash", {"command": "cat .env"}),
        )
        for tool, tool_input in cases:
            with self.subTest(tool=tool):
                self.assertEqual(self.call(tool, tool_input).returncode, 0)

    def test_missing_file_path_allowed(self):
        self.assertEqual(self.call("Write", {}).returncode, 0)

    def test_invalid_json_allowed(self):
        self.assertEqual(self.run_hook("not json {").returncode, 0)


if __name__ == "__main__":
    unittest.main()
