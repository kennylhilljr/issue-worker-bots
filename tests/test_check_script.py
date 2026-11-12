"""Tests for scripts/check.py. They never run the real suite recursively."""
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check.py"


def load_check():
    spec = importlib.util.spec_from_file_location("iwb_check_script", str(SCRIPT))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeRepo:
    """A temporary copy of the script plus a minimal iwb and tests/ tree."""

    def __init__(self, case):
        self.dir = Path(tempfile.mkdtemp(prefix="iwb-check-test-"))
        case.addCleanup(shutil.rmtree, str(self.dir), True)
        (self.dir / "scripts").mkdir()
        shutil.copy2(str(SCRIPT), str(self.dir / "scripts" / "check.py"))
        (self.dir / "iwb").write_text("#!/usr/bin/env python3\nVALUE = 1\n", encoding="utf-8")
        (self.dir / "tests" / "nested").mkdir(parents=True)
        (self.dir / "tests" / "test_ok.py").write_text("import unittest\n", encoding="utf-8")
        (self.dir / "tests" / "nested" / "helper.py").write_text("X = 2\n", encoding="utf-8")

    def run(self, *args):
        return subprocess.run([sys.executable, str(self.dir / "scripts" / "check.py"), *args],
                              cwd=str(self.dir), capture_output=True, text=True, timeout=60)


class CompileOnly(unittest.TestCase):
    def test_syntax_error_in_test_file_fails_compile_step(self):
        repo = FakeRepo(self)
        (repo.dir / "tests" / "test_ok.py").write_text("def broken(:\n", encoding="utf-8")
        result = repo.run("--compile-only")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("compile step failed", result.stderr)
        self.assertIn("test_ok.py", result.stderr)

    def test_syntax_error_in_nested_test_source_fails(self):
        repo = FakeRepo(self)
        (repo.dir / "tests" / "nested" / "helper.py").write_text("X = (\n", encoding="utf-8")
        result = repo.run("--compile-only")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("compile step failed", result.stderr)
        self.assertIn("helper.py", result.stderr)

    def test_syntax_error_in_iwb_fails(self):
        repo = FakeRepo(self)
        (repo.dir / "iwb").write_text("if True\n", encoding="utf-8")
        result = repo.run("--compile-only")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("compile step failed for iwb", result.stderr)

    def test_clean_tree_compiles(self):
        repo = FakeRepo(self)
        result = repo.run("--compile-only")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("compile step passed (3 files)", result.stdout)
        self.assertFalse(list(repo.dir.rglob("__pycache__")), "compile step must not write bytecode into the tree")


class Main(unittest.TestCase):
    def setUp(self):
        self.check = load_check()
        self.repo = FakeRepo(self)

    def test_compile_only_never_launches_unittest(self):
        with patch.object(self.check.subprocess, "run") as run:
            self.assertEqual(self.check.main(["--compile-only"], root=self.repo.dir), 0)
        run.assert_not_called()

    def test_compile_failure_never_launches_unittest(self):
        (self.repo.dir / "tests" / "test_ok.py").write_text("def broken(:\n", encoding="utf-8")
        with patch.object(self.check.subprocess, "run") as run, patch("sys.stderr"):
            self.assertEqual(self.check.main([], root=self.repo.dir), 1)
        run.assert_not_called()

    def test_runs_discovery_with_fresh_tmpdir_and_stub(self):
        seen = {}

        def fake_run(command, cwd=None, env=None, **kwargs):
            seen.update(command=command, cwd=cwd, env=env, kwargs=kwargs,
                        tmp_alive=Path(env["TMPDIR"]).is_dir())
            return subprocess.CompletedProcess(command, 0)

        with patch.object(self.check.subprocess, "run", side_effect=fake_run):
            self.assertEqual(self.check.main([], root=self.repo.dir), 0)
        self.assertEqual(seen["command"], [sys.executable, "-m", "unittest", "discover", "-s", "tests"])
        self.assertEqual(seen["cwd"], str(self.repo.dir))
        self.assertNotIn("shell", seen["kwargs"])
        self.assertEqual(seen["env"]["PRBOT_PATH"], str(self.repo.dir / "tests" / "prbot_stub.py"))
        tmpdir = Path(seen["env"]["TMPDIR"])
        self.assertEqual(tmpdir.parent, Path("/tmp"))
        self.assertNotEqual(str(tmpdir), os.environ.get("TMPDIR"))
        self.assertTrue(seen["tmp_alive"], "TMPDIR must exist while the child runs")
        self.assertFalse(tmpdir.exists(), "TMPDIR must be removed after the child exits")

    def test_environment_is_copied_not_mutated(self):
        before = dict(os.environ)
        with patch.object(self.check.subprocess, "run",
                          return_value=subprocess.CompletedProcess([], 0)) as run:
            self.check.main([], root=self.repo.dir)
        self.assertEqual(dict(os.environ), before)
        env = run.call_args.kwargs["env"]
        self.assertEqual(env.get("PATH"), os.environ.get("PATH"))

    def test_unittest_failure_is_reported(self):
        with patch.object(self.check.subprocess, "run",
                          return_value=subprocess.CompletedProcess([], 3)), \
                patch("sys.stderr") as stderr:
            self.assertEqual(self.check.main([], root=self.repo.dir), 3)
        written = "".join(call.args[0] for call in stderr.write.call_args_list)
        self.assertIn("unittest step failed (exit 3)", written)

    def test_signal_exit_still_nonzero(self):
        with patch.object(self.check.subprocess, "run",
                          return_value=subprocess.CompletedProcess([], -9)), patch("sys.stderr"):
            self.assertEqual(self.check.main([], root=self.repo.dir), 1)

    def test_launch_error_is_reported_and_tmpdir_cleaned(self):
        created = []
        real = self.check.tempfile.TemporaryDirectory

        def tracking(*args, **kwargs):
            directory = real(*args, **kwargs)
            created.append(Path(directory.name))
            return directory

        with patch.object(self.check.subprocess, "run", side_effect=FileNotFoundError("no python")), \
                patch.object(self.check.tempfile, "TemporaryDirectory", side_effect=tracking), \
                patch("sys.stderr") as stderr:
            self.assertEqual(self.check.main([], root=self.repo.dir), 1)
        written = "".join(call.args[0] for call in stderr.write.call_args_list)
        self.assertIn("unittest step could not start", written)
        self.assertTrue(created)
        self.assertFalse([path for path in created if path.exists()])


if __name__ == "__main__":
    unittest.main()
