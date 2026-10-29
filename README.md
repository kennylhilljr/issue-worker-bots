<p align="center">
  <img src="logos/claude-dev.png" alt="claude-dev-gh" width="120">
  <img src="logos/codex-dev.png" alt="codex-dev-gh" width="120">
</p>

# issue-worker-bots

Two GitHub Apps, `claude-dev-gh` and `codex-dev-gh`, that will pick up issues in Kenny's repositories, plan the work with a strong model, write the code with the cheapest model that fits, and open a pull request as a bot. Every pull request they open is reviewed by the other agent's review bot from [pr-review-bots](https://github.com/kennylhilljr/pr-review-bots).

## Status

Design stage. [`DESIGN.md`](DESIGN.md) describes the whole pipeline: which issues get picked up, the analysis and clarity gate, which model handles which step, budgets, and safety rules.

| Build step | State |
| --- | --- |
| 1. Worker Apps registered (`claude-dev-gh`, `codex-dev-gh`) | Done. Installed on all of Kenny's repositories with contents, issues and pull requests read and write. |
| 2. Intake watcher | Live: launchd runs it every 5 minutes. Trusts Kenny and `openclaw-ops-assist[bot]`; the 6 issues open at install were skipped. |
| 3. Analysis step | Built; dry-run on a real issue with Opus. The running job does intake only until switched to `tick`. |
| 4. Clarity gate, approval, orchestrator runs, draft PRs | Not started |
| 5. Review hand-off and repair round | Not started |
| 6. More repositories and model tuning | Not started |

## Intake (step 2)

`iwb intake` makes one pass over open issues on every repository the worker bots are installed on. An issue is picked up when it was opened by Kenny or by an account in `trusted_openers`, or when Kenny himself added the `bot:work` label. Issues labeled `bot:skip`, issues assigned to a person, and pull requests are left alone. The chosen worker bot adds `bot:queued` and posts one comment; nothing else happens until step 3.

The worker is chosen by an `agent:claude` or `agent:codex` label, otherwise the two alternate. Each issue is picked up once: a hidden marker in the bot's comment prevents a second pick-up even if the state file is lost. A pass picks up at most 5 issues.

```bash
./iwb intake --dry-run
```

Trust more openers, for example Kenny's own ops bot, in `~/.config/issue-worker-bots/config.json`:

```json
{"trusted_openers": ["openclaw-ops-assist[bot]"]}
```

Run it every 5 minutes with launchd. To skip the issues already open, run `./iwb intake --baseline` once first.

```bash
./install-intake.sh
```

Log: `~/Library/Logs/issue-intake.log`. State: `~/.config/issue-worker-bots/state.json`. Remove the job with `./install-intake.sh --uninstall`. Tests: `python3 -m unittest discover -s tests`.

## Analysis (step 3)

`iwb analyze` takes one picked-up issue per pass, clones its repository read-only with the worker bot's token, and runs the worker's strongest model on it: `opus` at high effort for Claude, `gpt-6-astra` at high effort for Codex (override in `config.json` under `analysis_models`). Only the issue and comments from Kenny, trusted openers and the worker bots go into the prompt; other people's comments are left out.

The model answers with exactly one of:

| Answer | Posted as | Label |
| --- | --- | --- |
| No code change needed | The reason | `bot:no-code` |
| Questions | Up to 5 questions | `bot:needs-info` |
| A plan | A unit table plus the full plan in orchestrator format, saved to `~/.config/issue-worker-bots/plans/` | `bot:planned` |

A reply from Kenny after questions or a no-code answer runs the analysis again. Plans are checked before posting: 1 to 8 units, unique ids, every work-unit field present, files and acceptance commands on every unit, dependencies that exist. Two unusable answers mark the issue `bot:blocked`; removing that label tries again. A model failure such as a usage limit retries after an hour.

```bash
./iwb analyze --dry-run
```

`./iwb tick` runs intake, then one analysis; that is what `install-intake.sh` schedules.

## Layout

| Path | Contents |
| --- | --- |
| [`DESIGN.md`](DESIGN.md) | The design. |
| `bots/` | App specs for `prbot register --spec`, including each App's permissions. |
| `logos/` | Bot logos: SVG sources and 512 px PNGs uploaded to GitHub. |
| `iwb` | The CLI. Standard library only; uses `prbot` for sign-in and tokens. |
| `install-intake.sh` | Installs the launchd job that runs `iwb intake` every 5 minutes. |
| `tests/` | Unit tests for the pick-up rules, with fake GitHub data. |

## Depends on

- [openclaw-ecc-orchestrator](https://github.com/kennylhilljr/openclaw-ecc-orchestrator): plans, routing by tier, worktrees, escalation and budgets.
- [pr-review-bots](https://github.com/kennylhilljr/pr-review-bots): `prbot` for App identities, tokens, commits, pushes and cross-review.
