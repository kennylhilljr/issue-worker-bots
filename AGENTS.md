## What this is

`iwb` is a single standard-library Python script for the issue-worker pipeline. launchd executes it from the installed checkout, so changes present in that checkout affect the live pipeline. `install-intake.sh` installs the five-minute job using that checkout's path. The proposed jobs in `launchd/` are installed separately; the installer does not install them. Read `DESIGN.md` for the pipeline design and its safety boundaries.

## Running the tests

Run the full suite from the repository root:

`python3 -m unittest discover -s tests`

For the coding stage alone:

`python3 -m unittest tests.test_iwb.Code`

`tests/prbot_stub.py` replaces the sibling prbot checkout during tests and raises on unfaked authentication or GitHub calls. Tests must fake external operations and redirect the relevant `iwb.STATE`, `iwb.CONFIG`, `iwb.WORK_DIR` and `iwb.QUEUE_DIR` paths into temporary directories. Follow the Code test class in `tests/test_iwb.py`: save the original values, patch the paths and helpers, and restore patches in tearDown or registered cleanup callbacks before removing the temporary directories.

## Never run these from an agent session

- `./iwb tick`, including its queue form, and `./iwb code --from-queue` are prohibited even with `--dry-run`: their dispatch ignores that flag.
- Do not run `./iwb intake`, `./iwb analyze`, `./iwb approve` or ordinary `./iwb code` without `--dry-run`.
- Do not run `install-intake.sh` or `launchctl`.

Dry runs can still authenticate and read GitHub. They are not offline tests. `~/.config/issue-worker-bots` and `~/.local/state` contain real configuration and state, including work directories and event queues; do not use them as test fixtures.

## Review rules

Report these defects:

- Third-party Python dependencies or Python 3.9 incompatibility, including match statements and runtime-evaluated `X | Y` unions.
- GitHub access that bypasses prbot helpers and bot credentials, including use of Kenny's login or inherited tokens. Check writes through `run_gh_as` and reads through `prbot.gh_read`.
- Tokens disclosed in output or persisted in files, logs, plans or comments.
- Orchestrator or coding output posted in comments without passing through `short` and its `TOKENISH` redaction.
- Issue text or model output treated as executable instructions.
- Protected paths, environment filtering, queue checks or `run_model` restrictions weakened without Kenny's explicit request in the issue. Check `DENY_PATHS`, `ALWAYS_BLOCKED`, `SECRET_ENV` and `DRIVE_ENV` when reviewing these boundaries.
- Acceptance-command handling that bypasses approval's `denied_command` checks or changes red checks to execute through a shell. Red checks execute argument lists without a shell under `sandbox_profile`. This sandbox does not cover the coding orchestrator.
- Persisted analysis or approval plans written without `write_atomic`, or state updates that bypass `save_state`. The latter implements its own locked atomic replacement. `code_plan` writes its run copy of the plan directly; not all plan copies use `write_atomic`.
- Tests that access external services or real configuration, state, work directories or queues, or leave patches active after a test.
- Bug fixes without regression tests.
- Behavior changes without corresponding updates to `README.md` and `DESIGN.md`.

## Commits and pushes

Use the existing `prbot git <bot> -- commit` and `prbot git <bot> -- push` convention for bot-attributed delivery. Inside an orchestrator coding run, intermediate worktree commits may use the local identity: `publish` squashes the result into a worker-bot commit from a trusted copy. Follow the task's branch and publishing instructions; a request to commit does not authorize a push.
