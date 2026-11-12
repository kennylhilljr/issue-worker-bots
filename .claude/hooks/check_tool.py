#!/usr/bin/env python3
"""Inert Claude Code hook skeleton: reads the hook payload and allows it."""
import argparse
import json
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(description="Claude Code tool hook")
    parser.add_argument("--mode", required=True, choices=["pre", "post"])
    parser.parse_args(argv)
    json.load(sys.stdin)
    return 0


if __name__ == "__main__":
    sys.exit(main())
