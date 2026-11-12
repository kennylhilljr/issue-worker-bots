# Continuous integration

`scripts/check.py` is the single check for this repository. It byte-compiles `iwb` and every Python file under `tests/`, then runs `python3 -m unittest discover -s tests` with a fresh temporary `TMPDIR` under `/tmp` and `PRBOT_PATH` pointing at the bundled `tests/prbot_stub.py`. Run it locally with `python3 scripts/check.py`; `python3 scripts/check.py --compile-only` stops after the compile step.

## Installing the workflow

Workflow files are on the always-blocked path list, so no bot writes them. CI starts only after Kenny saves the block below as `.github/workflows/tests.yml` and pushes it to main.

```yaml
name: tests

on:
  pull_request:
  push:
    branches:
      - main

permissions:
  contents: read

jobs:
  tests:
    runs-on: macos-latest
    strategy:
      fail-fast: false
      matrix:
        python-version:
          - '3.9'
          - '3.13'
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}
      - run: python3 scripts/check.py
```

## Why it looks like this

- **macOS runner.** The red-check tests (`RedCheckSandbox` in `tests/test_iwb.py`) run real commands under `sandbox-exec`, which exists only on macOS.
- **No pr-review-bots checkout.** The suite loads `tests/prbot_stub.py` instead of the sibling `prbot`, and the stub raises on any unfaked authentication or GitHub call, so the job needs no secrets.
- **Python 3.9 and 3.13.** `iwb` must stay 3.9-compatible; 3.13 covers a recent release.
- **Read-only permissions and no dependency installation.** `iwb` and its tests use the standard library only.
