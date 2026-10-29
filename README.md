<p align="center">
  <img src="logos/claude-dev.png" alt="claude-dev-gh" width="120">
  <img src="logos/codex-dev.png" alt="codex-dev-gh" width="120">
</p>

# issue-worker-bots

Two GitHub Apps, `claude-dev-gh` and `codex-dev-gh`, that will pick up issues in Kenny's repositories, plan the work with a strong model, write the code with the cheapest model that fits, and open a pull request as a bot. Every pull request they open is reviewed by the other agent's review bot from [pr-review-bots](https://github.com/kennylhilljr/pr-review-bots).

## Status

Intake and analysis are live; plan approval and coding are built but not yet run live. [`DESIGN.md`](DESIGN.md) describes the whole pipeline: which issues get picked up, the analysis and clarity gate, which model handles which step, budgets, safety rules, and Kenny's decisions.

| Build step | State |
| --- | --- |
| 1. Worker Apps registered (`claude-dev-gh`, `codex-dev-gh`) | Done. Installed on all of Kenny's repositories with contents, issues and pull requests read and write. |
| 2. Intake watcher | Live: launchd runs it every 5 minutes. Trusts Kenny and `openclaw-ops-assist[bot]`; the 6 issues open at install were skipped. |
| 3. Analysis step | Live: launchd runs `tick` (intake, then one analysis) every 5 minutes. |
| 4. Clarity, policy and red-check gates, agent plan approval, orchestrator runs, draft PRs | Built and tested with fakes; pilot on `kennylhilljr/ecc-customization`. Not yet run live: coding starts once `coding_repos` is set in `config.json`. Agentic clarification is not built yet. |
| 5. Review hand-off and repair round | Not started |
| 6. More repositories and model tuning | Not started |

## Intake (step 2)

`iwb intake` makes one pass over open issues on every repository the worker bots are installed on. An issue is picked up when it was opened by Kenny or by an account in `trusted_openers`, or when Kenny himself added the `bot:work` label. Issues labeled `bot:skip`, issues assigned to a person, and pull requests are left alone. The chosen worker bot adds `bot:queued` and posts one comment; analysis follows within a few minutes.

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

`./iwb tick` runs intake, then one analysis, then one step of plan work; that is what `install-intake.sh` schedules.

## Plan approval and coding (step 4)

`iwb work` moves one planned issue forward per pass, and only in repositories listed in `coding_repos`. Elsewhere the plan stays at `bot:planned` and the worker posts one note saying so.

| Pass | What happens | Label after |
| --- | --- | --- |
| 1 | Plan gates on a fresh clone: clarity, `ecc lint-plan`, and the policy gate (protected paths, risky acceptance commands, network or secrets, budget caps). Then the red check: each non-`mechanical` unit's acceptance commands run on the default branch in a sandbox with no network, and at least one must fail. Then the other agent's review bot judges the plan with its strongest model, read-only: `codex-pr-gh` for `claude-dev-gh` plans, `claude-pr-gh` for `codex-dev-gh` plans. | `bot:approved`, or back to analysis once with the findings, then `bot:blocked` |
| 2 | `ecc review-plan` and `ecc create-run`, then `ecc review-plan` and `ecc drive`, in `iwb`'s own clone with `bot/<n>-<slug>` as the target branch. The changed files are checked against the plan's scope and the protected paths, committed once as the worker bot, and pushed to the `bot/` branch. A PR with `Closes #<n>` and `agent:claude` or `agent:codex` is opened as a draft and marked ready, so `prbot watch` reviews it. | `bot:in-progress`, then `bot:pr-open`; `bot:blocked` if the run stops |

The approver answers approve, revise or reject. It may raise a unit's tier or risk and add acceptance commands, never lower or remove them. Its verdict is posted on the issue as the review bot. Nothing is ever merged and the default branch is never pushed; Kenny merges the PR. At most 2 issues are approved or being coded at once.

```bash
./iwb work --dry-run    # gates and red check only; no approver run, nothing posted or saved
```

Settings in `~/.config/issue-worker-bots/config.json`, all optional:

```json
{
  "coding_repos": ["kennylhilljr/ecc-customization"],
  "policy": {"kennylhilljr/ecc-customization": ["bootstrap.sh", "stack.conf", "machine/**"]},
  "budget_caps": {"attempts": 3, "minutes": 60, "maximum_cost_usd": 5},
  "approval_models": {"codex": {"model": "gpt-6-astra"}},
  "ecc_config": {"certifications": "~/.local/share/openclaw-ecc-orchestrator/runtime/.orchestration/runtime/cert-latest.json"}
}
```

| Key | Default | Meaning |
| --- | --- | --- |
| `coding_repos` | none | Repositories where step 4 runs. Nothing is coded until this is set. |
| `policy` | the seed list above for `ecc-customization`, nothing for other repositories | Extra protected paths per repository. An entry replaces that repository's seed list. `.github/workflows/**`, `**/.env*`, `**/*.pem`, `**/*.key` and `**/secrets/**` are always protected. |
| `budget_caps` | 3 attempts, 60 minutes, 5 USD | The most a single unit may budget. |
| `approval_models` | the analysis models; medium effort, high when a unit is Tier 2 | The approver's model per agent. |
| `ecc_config` | certifications and catalog from the orchestrator runtime | Orchestrator config keys for the run (for example `repo_checks`). Coding waits, without posting, while the certifications file is missing. |

The orchestrator is `../openclaw-ecc-orchestrator/bin/ecc` (override with `IWB_ECC`). Its state, worktrees and `iwb`'s clones live in `~/.local/state/issue-worker-bots/` (override with `IWB_WORK`), apart from Kenny's own orchestrator runs.

## Talking to the bots

Comment on an issue the bots picked up. Comments from Kenny and trusted openers are read on the next pass; everyone else's are ignored.

| Comment | What happens |
| --- | --- |
| `resume`, `continue`, `go`, `proceed`, `approve`, `lgtm` | The bot answers with where things stand. No model run. A plan moves on by itself; nobody needs to approve it. |
| `replan` | A fresh analysis. |
| `status` | The bot reports its last analysis. |
| `stop` | The bot adds `bot:stop` and does nothing more until it's removed. |
| Anything else | Treated as feedback: questions get answered, a plan gets revised. |

Commands may be prefixed with `@claude-dev-gh` or `@codex-dev-gh`. Editing the issue's title or body, switching `agent:claude` and `agent:codex`, and reopening the issue also bring the bot back. Once coding has started, comments on the issue no longer start a new analysis. Removing `bot:blocked` starts the issue again from analysis. `"paused": true` in `config.json` stops all workers. The full list of triggers, including the PR ones for later steps, is under "Coming back to an issue" in [`DESIGN.md`](DESIGN.md).

## OpenClaw

Plans ready, questions posted and blocked issues (including plans refused by the gates or the approver, and stopped coding runs) appear in OpenClaw as `attention.required` events under one `github` run (Phase A of the bridge in [`DESIGN.md`](DESIGN.md)). They go through the orchestrator runtime's own event writer via `prbot`, and are skipped quietly if the runtime is not installed. No-code answers and dry runs send nothing.

## Layout

| Path | Contents |
| --- | --- |
| [`DESIGN.md`](DESIGN.md) | The design. |
| `bots/` | App specs for `prbot register --spec`, including each App's permissions. |
| `logos/` | Bot logos: SVG sources and 512 px PNGs uploaded to GitHub. |
| `iwb` | The CLI. Standard library only; uses `prbot` for sign-in and tokens. |
| `install-intake.sh` | Installs the launchd job that runs `iwb tick` every 5 minutes. |
| `tests/` | Unit tests with fake GitHub, model and orchestrator: pick-up rules, analysis, the plan gates, approval routing, coding and label changes. |

## Depends on

- [openclaw-ecc-orchestrator](https://github.com/kennylhilljr/openclaw-ecc-orchestrator): plans, routing by tier, worktrees, escalation and budgets.
- [pr-review-bots](https://github.com/kennylhilljr/pr-review-bots): `prbot` for App identities, tokens, commits, pushes and cross-review.
