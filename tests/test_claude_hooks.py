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
        {
            "matcher": "Bash",
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


class LiveCommands(unittest.TestCase):
    """The hook only receives these commands as JSON; nothing here runs them."""

    BLOCKED = (
        # every way to name iwb
        "./iwb tick", "iwb tick", "python3 iwb tick", "python3 -B ./iwb analyze",
        "/Users/x/issue-worker-bots/iwb intake",
        # live subcommands
        "./iwb code", "./iwb approve", "./iwb analyze --limit 1",
        # tick and queue drains ignore --dry-run
        "./iwb tick --dry-run", "./iwb tick --from-queue /tmp/q",
        "./iwb code --from-queue /tmp/q --dry-run",
        # chains, spaced and unspaced
        "cd x && ./iwb tick", "./iwb tick; echo done", "./iwb tick;echo done",
        "echo a&&./iwb tick", "echo a|./iwb tick", "true||./iwb tick", "./iwb tick &",
        "(./iwb tick)",
        # command substitution
        "echo $(./iwb tick)", "echo `./iwb tick` ",
        # newlines separate; a comment ends at its newline
        "echo ok\n./iwb tick", "./iwb analyze --dry-run\n./iwb analyze",
        "echo ok # comment\n./iwb tick",
        # a commented --dry-run does not count
        "./iwb analyze # --dry-run",
        # line continuations
        "./iwb analyze \\\n  --limit 1", "./iwb ana\\\nlyze",
        # a redirection target is not an argument
        "./iwb analyze > --dry-run", "./iwb analyze >--dry-run", "./iwb analyze 2> --dry-run",
        # 2>&1 is a redirection, not a & separator
        "2>&1 ./iwb tick",
        # prefixes and wrappers
        "FOO=1 ./iwb tick", "env FOO=1 ./iwb tick", "nohup ./iwb tick", "sudo ./iwb code",
        # nested shells
        "bash -c './iwb tick'", 'sh -c "cd x && ./iwb tick"', "eval ./iwb tick",
        # installer
        "bash install-intake.sh", "./install-intake.sh",
        # launchd changes
        "launchctl load x.plist", "launchctl unload x.plist",
        "launchctl bootstrap gui/501 x.plist",
        "launchctl bootout gui/501/com.kennylhilljr.issue-intake",
        "launchctl kickstart -k gui/501/x", "launchctl enable gui/501/x",
        # one live part in a dry-run chain
        "./iwb analyze --dry-run && ./iwb code",
        # unparsable: fail closed
        'echo "unbalanced',
    )

    # Stricter than the issue's table: shell features that hide what iwb receives.
    BLOCKED_EXTRA = (
        "./iwb code --job /tmp/job.json --dry-run",
        "./iwb $(echo tick)", "./iwb `echo tick`", "./iwb $SUB --dry-run",
        "./iwb code --dry-run --from{-,-}queue q",
        "echo a#; ./iwb tick", "./iwb analyze --dry-run#x",
        "cat <(./iwb tick)", "echo ok;>x ./iwb tick",
        "nice -n 10 ./iwb tick", "sudo -u root ./iwb tick", "gtimeout 600 ./iwb tick",
        "echo tick | xargs ./iwb", "bash -lc './iwb tick'", ". ./install-intake.sh",
        "python3 -W ignore ./iwb tick",
        "bash -c \"bash -c 'bash -c \\\"bash -c ls\\\"'\"",        "{ ./iwb tick; }", "! ./iwb tick", "if true; then ./iwb tick; fi",
        "for i in 1; do ./iwb tick; done", "while true; do ./iwb tick; done",
        "f() { ./iwb tick; }", "function f { ./iwb tick; }",
        "env -S './iwb tick'", "env -S'./iwb tick'",
        "cmd=./iwb; $cmd tick", "./iw? tick", "./i*b tick",
        "echo './iwb tick' | bash", "bash <<< './iwb tick'", "source <(echo ./iwb tick)",
        "find . -exec ./iwb tick \\;", "watch ./iwb tick", "watch -n 5 './iwb tick'",
        "arch -arm64 ./iwb tick", "ksh -c './iwb tick'",
        "launchctl asuser 501 launchctl load x", "launchctl submit -l x -- ./iwb tick",
        "launchctl remove x", "launchctl start x", "launchctl stop x",
        "python3 -Wignore::DeprecationWarning iwb tick", "python3 -W ignore iwb tick",        'out="$(./iwb tick)"', 'echo "$(./iwb analyze)"', 'echo "`./iwb tick`"',
        'echo "result: $(cd x && ./iwb tick)"', 'FOO="$(./iwb tick)" true',
        'echo "$(bash install-intake.sh)"', 'echo "$(launchctl load x)"',
        'echo hi > "$(./iwb tick)"',
        "cat install-intake.sh | bash -", "bash -s < install-intake.sh",
        "stdbuf -oL ./iwb tick", "setsid ./iwb tick", "taskpolicy -b ./iwb tick",
        "unbuffer ./iwb tick", "script -q /dev/null ./iwb tick", "builtin exec ./iwb tick",
        "sudo -nu kenny ./iwb tick", "sudo --user kenny ./iwb tick",
        "/usr/bin/time -o out ./iwb tick", "env --unset X ./iwb tick", "env --chdir /tmp ./iwb tick",
        "bash --rcfile F -c './iwb tick'", "python3 --check-hash-based-pycs always iwb tick",
        "python3 -m cProfile iwb tick", "python3 -m pdb iwb tick", "echo 'load x' | launchctl",
    )

    ALLOWED = (
        # help
        "./iwb --help", "./iwb -h", "./iwb",
        # dry runs
        "./iwb analyze --dry-run", "./iwb intake --dry-run --limit 2",
        "./iwb approve --dry-run", "./iwb code --dry-run", './iwb analyze "--dry-run"',
        # redirections around a dry run
        "./iwb analyze 2>&1 --dry-run", "./iwb analyze --dry-run 2>&1 | tail -5",
        "./iwb analyze --dry-run > /tmp/out.txt",
        # dry runs in chains, continuations, comments
        "./iwb analyze --dry-run \\\n  --limit 1", "cd x && ./iwb analyze --dry-run",
        "./iwb analyze --dry-run; ./iwb code --dry-run", "./iwb analyze --dry-run # note",
        # quoted text, including a quoted newline
        'grep "iwb tick" README.md', "grep -n 'iwb tick' README.md", "echo './iwb tick'",
        "echo 'x | ./iwb tick'", "echo \"a\n./iwb tick\"",
        # quoted or escaped separators and redirections
        'echo ";" ./iwb tick', "echo \\; ./iwb tick", 'echo "a > b" ./iwb tick',
        # comments
        "echo ok # ./iwb tick", "echo ok # don't",
        # normal work on the file
        "python3 -m unittest discover -s tests", "python3 -B -m unittest tests.test_iwb",
        "git diff iwb", "git log -- iwb", "sed -n 1,40p iwb",
        # reading is fine
        "cat install-intake.sh", "launchctl list", "launchctl print gui/501/x", "ls launchd/",        # stricter rules leave ordinary commands alone
        "find . -name '*.py' -exec grep -l iwb {} +", "launchctl help",
        "python3 -Wignore::DeprecationWarning -m unittest discover -s tests",
        "if true; then echo ok; fi", "env FOO=1 git status", "for f in tests/*.py; do wc -l $f; done",        '[ "$1" = approve ]', 'if [ "$m" = code ]; then echo ok; fi', '[[ $x = tick ]]',
        'echo "$(git status --short)"', "echo $((1 + 2))",
        "python3 - <<'EOF'\nprint(1)\nEOF",
    )

    def setUp(self):
        self.cwd = tempfile.mkdtemp(dir="/tmp")

    def tearDown(self):
        shutil.rmtree(self.cwd, ignore_errors=True)

    def call(self, tool, tool_input):
        return subprocess.run(
            [sys.executable, SCRIPT, "--mode", "pre"],
            cwd=self.cwd,
            input=json.dumps({"tool_name": tool, "tool_input": tool_input}),
            capture_output=True,
            text=True,
        )

    def bash(self, command):
        return self.call("Bash", {"command": command})

    def test_live_commands_blocked(self):
        for command in self.BLOCKED + self.BLOCKED_EXTRA:
            with self.subTest(command=command):
                result = self.bash(command)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertTrue(result.stderr.startswith("Blocked:"), result.stderr)

    def test_safe_commands_allowed(self):
        for command in self.ALLOWED:
            with self.subTest(command=command):
                result = self.bash(command)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stderr, "")

    def test_messages_name_the_rule(self):
        cases = (
            ("./iwb tick", "iwb"),
            ("echo ok # comment\n./iwb tick", "iwb"),
            ("./iwb analyze", "iwb"),
            ("bash install-intake.sh", "install-intake.sh"),
            ("./install-intake.sh", "install-intake.sh"),
            ("launchctl load x.plist", "launchctl"),
            ("launchctl kickstart -k gui/501/x", "launchctl"),
            ('echo "unbalanced', "could not be parsed"),
        )
        for command, rule in cases:
            with self.subTest(command=command):
                result = self.bash(command)
                self.assertEqual(result.returncode, 2)
                self.assertTrue(result.stderr.startswith("Blocked:"), result.stderr)
                self.assertIn(rule, result.stderr)

    def test_only_bash_is_checked(self):
        result = self.call("Write", {"file_path": "notes.txt", "command": "./iwb tick"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")

    def test_missing_command_allowed(self):
        self.assertEqual(self.call("Bash", {}).returncode, 0)


if __name__ == "__main__":
    unittest.main()
