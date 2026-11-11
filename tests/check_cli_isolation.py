"""Check test isolation in fresh interpreters, independent of PATH and TMPDIR."""
import argparse
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import time


ROOT = Path(__file__).resolve().parents[1]


def run_tests(arguments, env, *, timeout=None):
    command = [sys.executable, "-m", "unittest", *arguments]
    print(f"Running {shlex.join(command)} (TMPDIR={env.get('TMPDIR', '<inherited default>')})", flush=True)
    started = time.monotonic()
    subprocess.run(command, cwd=ROOT, env=env, check=True, timeout=timeout)
    return time.monotonic() - started


def check_code():
    env = os.environ.copy()
    elapsed = run_tests(["tests.test_iwb.Code"], env, timeout=3)
    if elapsed >= 3:
        raise AssertionError(f"Code took {elapsed:.3f}s; must finish in less than three seconds")
    print(f"Code with inherited PATH finished in {elapsed:.3f}s", flush=True)

    with tempfile.TemporaryDirectory(prefix="iwb-cli-first-") as first, \
            tempfile.TemporaryDirectory(prefix="iwb-cli-second-") as second:
        markers = []
        for directory in (Path(first), Path(second)):
            for name in ("claude", "codex"):
                marker = directory / f"{name}.called"
                markers.append(marker)
                script = directory / name
                script.write_text(f"#!/bin/sh\nprintf called > {shlex.quote(str(marker))}\n"
                                  "printf '99.0.0\\n'\n")
                script.chmod(0o755)
        env["PATH"] = os.pathsep.join([first, second, env.get("PATH", "")])
        run_tests(["tests.test_iwb.Code"], env)
        found = [str(marker) for marker in markers if marker.exists()]
        if found:
            raise AssertionError(f"Code launched runners from inherited PATH: {found}")
    print("Code left all inherited runner markers absent", flush=True)


def check_tmpdirs():
    env = os.environ.copy()
    env["TMPDIR"] = "/tmp"
    run_tests(["discover", "-s", "tests"], env)
    with tempfile.TemporaryDirectory(prefix="iwb-isolation-", dir="/tmp") as directory:
        nested = Path(directory) / ".openclaw" / "tmp"
        nested.mkdir(parents=True)
        env["TMPDIR"] = str(nested)
        run_tests(["discover", "-s", "tests"], env)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--code", action="store_true", help="check Code runtime and inherited runner isolation")
    parser.add_argument("--tmpdirs", action="store_true", help="run discovery with ordinary and .openclaw TMPDIRs")
    args = parser.parse_args()
    if not (args.code or args.tmpdirs):
        parser.error("select --code, --tmpdirs, or both")
    try:
        if args.code:
            check_code()
        if args.tmpdirs:
            check_tmpdirs()
    except subprocess.CalledProcessError as error:
        return error.returncode if error.returncode > 0 else 1
    except (AssertionError, subprocess.TimeoutExpired) as error:
        print(f"Isolation check failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
