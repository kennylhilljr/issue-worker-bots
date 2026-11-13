#!/usr/bin/env python3
"""Claude Code tool hook for this checkout.

--mode pre blocks Write/Edit/MultiEdit calls on secret-shaped files.
--mode post currently allows everything.
"""
import argparse
import json
import sys
from pathlib import PurePosixPath

EDIT_TOOLS = ("Write", "Edit", "MultiEdit")


def is_secret_path(file_path):
    """Return True when file_path looks like a secret file, mirroring iwb's ALWAYS_BLOCKED secrets."""
    parts = PurePosixPath(file_path).parts
    if not parts:
        return False
    name = parts[-1]
    if name.startswith(".env") or name.endswith(".pem") or name.endswith(".key"):
        return True
    return "secrets" in parts[:-1]


def check_pre(payload):
    """Return (exit_code, message) for a PreToolUse payload."""
    if not isinstance(payload, dict) or payload.get("tool_name") not in EDIT_TOOLS:
        return 0, ""
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return 0, ""
    file_path = tool_input.get("file_path")
    if not isinstance(file_path, str) or not file_path:
        return 0, ""
    if is_secret_path(file_path):
        return 2, (f"Blocked: {file_path} looks like a secret file. "
                   "Secrets stay in Keychain and reach the bots through prbot.")
    return 0, ""


def main(argv=None):
    parser = argparse.ArgumentParser(description="Claude Code tool hook")
    parser.add_argument("--mode", required=True, choices=["pre", "post"])
    args = parser.parse_args(argv)
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return 0
    if args.mode == "pre":
        code, message = check_pre(payload)
        if message:
            print(message, file=sys.stderr)
        return code
    return 0


if __name__ == "__main__":
    sys.exit(main())
