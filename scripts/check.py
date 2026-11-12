#!/usr/bin/env python3
"""Compile iwb and the tests, then run the unittest suite the way CI does."""
import argparse
import os
import py_compile
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def compile_sources(root):
    """Byte-compile iwb and every .py file under tests/; return True when all compile."""
    sources = [root / "iwb"]
    sources += sorted(path for path in (root / "tests").rglob("*.py")
                      if "__pycache__" not in path.parts)
    with tempfile.TemporaryDirectory(prefix="iwb-check-compile-") as out:
        for index, source in enumerate(sources):
            try:
                py_compile.compile(str(source), cfile=str(Path(out) / f"{index}.pyc"), doraise=True)
            except (py_compile.PyCompileError, OSError) as error:
                print(f"check: compile step failed for {source.relative_to(root)}:\n{error}", file=sys.stderr)
                return False
    print(f"check: compile step passed ({len(sources)} files)", flush=True)
    return True


def run_unittest(root):
    """Run unittest discovery in a fresh TMPDIR with the bundled prbot stub; return its exit status."""
    command = [sys.executable, "-m", "unittest", "discover", "-s", "tests"]
    with tempfile.TemporaryDirectory(prefix="iwb-check-", dir="/tmp") as tmpdir:
        env = os.environ.copy()
        env["TMPDIR"] = tmpdir
        env["PRBOT_PATH"] = str(root / "tests" / "prbot_stub.py")
        try:
            result = subprocess.run(command, cwd=str(root), env=env)
        except OSError as error:
            print(f"check: unittest step could not start: {error}", file=sys.stderr)
            return 1
    if result.returncode != 0:
        print(f"check: unittest step failed (exit {result.returncode})", file=sys.stderr)
        return result.returncode if result.returncode > 0 else 1
    print("check: unittest step passed", flush=True)
    return 0


def main(argv=None, root=ROOT):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compile-only", action="store_true", help="stop after the compile step")
    args = parser.parse_args(argv)
    if not compile_sources(root):
        return 1
    if args.compile_only:
        return 0
    return run_unittest(root)


if __name__ == "__main__":
    sys.exit(main())
