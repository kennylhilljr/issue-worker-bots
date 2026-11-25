# Continuous integration

`scripts/check.py` is the single check for this repository. It byte-compiles `iwb` and every Python file under `tests/`, then runs `python3 -m unittest discover -s tests` with a fresh temporary `TMPDIR` under `/tmp` and `PRBOT_PATH` pointing at the bundled `tests/prbot_stub.py`. Run it locally with `python3 scripts/check.py`; `python3 scripts/check.py --compile-only` stops after the compile step.

## The workflow

The workflow is `.github/workflows/tests.yml`, and it must stay identical to the block below (`tests/test_ci_docs.py` checks this). Workflow files are on the always-blocked path list, so no worker bot writes them; a change to either goes through a PR that Kenny merges.

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
      - uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4
      - uses: actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065 # v5
        with:
          python-version: ${{ matrix.python-version }}
      - run: python3 scripts/check.py
```

## Why it looks like this

- **macOS runner.** The red-check tests (`RedCheckSandbox` in `tests/test_iwb.py`) run real commands under `sandbox-exec`, which exists only on macOS.
- **No pr-review-bots checkout.** The suite loads `tests/prbot_stub.py` instead of the sibling `prbot`, and the stub raises on any unfaked authentication or GitHub call, so the job needs no secrets.
- **Python 3.9 and 3.13.** `iwb` must stay 3.9-compatible; 3.13 covers a recent release.
- **Actions pinned to commit SHAs.** As in `backup-review.yml`, a moved tag can't change what runs; the comment names the tag.
- **Read-only permissions and no dependency installation.** `iwb` and its tests use the standard library only.
