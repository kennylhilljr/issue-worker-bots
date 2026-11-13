import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, ".claude", "hooks", "check_tool.py")
PAYLOAD = json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"}})
PRE_COMMAND = 'python3 "$CLAUDE_PROJECT_DIR/.claude/hooks/check_tool.py" --mode pre'
POST_COMMAND = 'python3 "$CLAUDE_PROJECT_DIR/.claude/hooks/check_tool.py" --mode post'
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
    "PostToolUse": [
        {
            "matcher": "Write|Edit|MultiEdit",
            "hooks": [{"type": "command", "command": POST_COMMAND, "timeout": 660}],
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
        "bash -euo pipefail -c './iwb tick'", "bash -eo pipefail -c './iwb tick'",
        "bash -euo pipefail install-intake.sh", "env - ./iwb tick", "env -iS './iwb tick'",
        "python3 -m trace --ignore-dir /usr iwb tick",
        "python3 -m trace --trace iwb tick", "python3 -m trace --count iwb tick",
        "python3 -m cProfile -- iwb tick",
        "python3 -m trace -c iwb tick", "python3 -m trace -s iwb tick",
        "python3 -m trace --ignore-d /usr iwb tick", "python3 -m cProfile --out x iwb tick",
        "python3 -m cProfile ./i*b tick", "python3 -m cProfile -o x ./iw? tick",
        'python3 -m cProfile -o "$O" install-intake.sh', 'python3 -m cProfile -o "$O" launchctl load x',
        "python3 -m cProfile ./i*b tick iwb", "python3 -m cProfile ${X} tick iwb",
        "python3 -m cProfile ./i*b code --dry-run x/iwb",
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
        "python3 - <<'EOF'\nprint(1)\nEOF",        "bash -euo pipefail -c 'echo ok'",
        "python3 -m cProfile -o out.prof app.py", "python3 -m pdb tests/test_iwb.py",
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


FAKE_PYTHON = """#!{python}
import json, os, sys, time
here = os.path.dirname(os.path.abspath(__file__))
tmpdir = os.environ.get("TMPDIR", "")
with open(os.path.join(here, "calls.log"), "a") as log:
    log.write(json.dumps({{"argv": sys.argv[1:], "cwd": os.getcwd(), "tmpdir": tmpdir,
                          "tmpdir_exists": os.path.isdir(tmpdir)}}) + "\\n")
if not os.path.isdir(tmpdir):
    sys.exit(97)
print("FAKE-STDOUT")
print("FAKE-STDERR", file=sys.stderr)
sys.stdout.flush()
sys.stderr.flush()
time.sleep(float(os.environ.get("FAKE_SLEEP", "0")))
sys.exit(int(os.environ.get("FAKE_RC", "0")))
"""


def load_hook_module():
    spec = importlib.util.spec_from_file_location("check_tool_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PostEdit(unittest.TestCase):
    """A fake python3 on PATH stands in for the suite; the real suite never runs here."""

    RUN_PATHS = ("iwb", "tests/test_iwb.py", os.path.join(ROOT, "tests", "x.py"))
    QUIET_PATHS = ("README.md", "docs/x.md", ".claude/settings.json", "/elsewhere/tests/x.py")
    EDIT_TOOLS = ("Write", "Edit", "MultiEdit")

    def setUp(self):
        self.fake_dir = tempfile.mkdtemp(dir="/tmp")
        self.cwd = tempfile.mkdtemp(dir="/tmp")
        self.addCleanup(shutil.rmtree, self.fake_dir, True)
        self.addCleanup(shutil.rmtree, self.cwd, True)
        self.fake = os.path.join(self.fake_dir, "python3")
        with open(self.fake, "w") as fh:
            fh.write(FAKE_PYTHON.format(python=sys.executable))
        os.chmod(self.fake, 0o755)
        self.log = os.path.join(self.fake_dir, "calls.log")

    def calls(self):
        if not os.path.exists(self.log):
            return []
        with open(self.log) as fh:
            return [json.loads(line) for line in fh if line.strip()]

    def reset_log(self):
        if os.path.exists(self.log):
            os.remove(self.log)

    def env(self, path=None, **extra):
        env = {k: v for k, v in os.environ.items() if k not in ("FAKE_RC", "FAKE_SLEEP")}
        env["PATH"] = path if path is not None else self.fake_dir + os.pathsep + os.environ.get("PATH", "")
        env.update(extra)
        return env

    def run_hook(self, tool, tool_input, env=None):
        return subprocess.run(
            [sys.executable, SCRIPT, "--mode", "post"],
            cwd=self.cwd,
            input=json.dumps({"tool_name": tool, "tool_input": tool_input}),
            capture_output=True,
            text=True,
            env=env if env is not None else self.env(),
        )

    def assert_one_run(self):
        calls = self.calls()
        self.assertEqual(len(calls), 1, calls)
        call = calls[0]
        self.assertEqual(call["argv"], ["-m", "unittest", "discover", "-s", "tests"])
        self.assertEqual(os.path.realpath(call["cwd"]), os.path.realpath(ROOT))
        self.assertTrue(call["tmpdir"].startswith("/tmp/"), call["tmpdir"])
        self.assertTrue(call["tmpdir_exists"])
        self.assertFalse(os.path.exists(call["tmpdir"]), "TMPDIR folder left behind")
        return call

    def test_edits_to_iwb_and_tests_run_the_suite(self):
        for tool in self.EDIT_TOOLS:
            for path in self.RUN_PATHS:
                with self.subTest(tool=tool, path=path):
                    self.reset_log()
                    result = self.run_hook(tool, {"file_path": path})
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stderr, "")
                    self.assert_one_run()

    def test_failing_suite_is_reported(self):
        for path in self.RUN_PATHS:
            with self.subTest(path=path):
                self.reset_log()
                result = self.run_hook("Edit", {"file_path": path}, env=self.env(FAKE_RC="1"))
                self.assertEqual(result.returncode, 2)
                self.assertIn("Tests failed", result.stderr)
                self.assertIn(os.path.basename(path), result.stderr)
                self.assertIn("FAKE-STDOUT", result.stderr)
                self.assertIn("FAKE-STDERR", result.stderr)
                self.assert_one_run()

    def test_failure_message_names_the_edited_path(self):
        result = self.run_hook("Write", {"file_path": "tests/test_iwb.py"}, env=self.env(FAKE_RC="1"))
        self.assertEqual(result.returncode, 2)
        self.assertIn("Tests failed after editing tests/test_iwb.py", result.stderr)

    def test_other_edits_and_tools_run_nothing(self):
        cases = [(tool, {"file_path": path}) for tool in self.EDIT_TOOLS for path in self.QUIET_PATHS]
        cases.append(("Bash", {"command": "./iwb --help"}))
        for tool, tool_input in cases:
            with self.subTest(tool=tool, tool_input=tool_input):
                result = self.run_hook(tool, tool_input, env=self.env(FAKE_RC="1"))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stderr, "")
                self.assertEqual(self.calls(), [])

    def test_missing_python3_is_reported(self):
        empty = tempfile.mkdtemp(dir="/tmp")
        self.addCleanup(shutil.rmtree, empty, True)
        result = self.run_hook("Edit", {"file_path": "iwb"}, env=self.env(path=empty))
        self.assertEqual(result.returncode, 2)
        self.assertIn("could not run", result.stderr)

    def test_timeout_is_reported_and_cleaned_up(self):
        module = load_hook_module()
        env = self.env(FAKE_SLEEP="30")
        with mock.patch.dict(os.environ, env, clear=True):
            code, message = module.run_tests("iwb", timeout=1)
        self.assertEqual(code, 2)
        self.assertIn("timed out", message)
        self.assertIn("iwb", message)
        self.assert_one_run()


if __name__ == "__main__":
    unittest.main()
