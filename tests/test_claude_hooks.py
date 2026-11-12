import json
import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, ".claude", "hooks", "check_tool.py")
PAYLOAD = json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"}})


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

    def test_settings_has_only_empty_hooks(self):
        with open(os.path.join(ROOT, ".claude", "settings.json")) as fh:
            settings = json.load(fh)
        self.assertEqual(set(settings), {"hooks"})
        self.assertEqual(settings["hooks"], {})

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


if __name__ == "__main__":
    unittest.main()
