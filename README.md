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
| 2. Intake watcher | Not started |
| 3. Analysis step | Not started |
| 4. Clarity gate, approval, orchestrator runs, draft PRs | Not started |
| 5. Review hand-off and repair round | Not started |
| 6. More repositories and model tuning | Not started |

## Layout

| Path | Contents |
| --- | --- |
| [`DESIGN.md`](DESIGN.md) | The design. |
| `bots/` | App specs for `prbot register --spec`, including each App's permissions. |
| `logos/` | Bot logos: SVG sources and 512 px PNGs uploaded to GitHub. |

## Depends on

- [openclaw-ecc-orchestrator](https://github.com/kennylhilljr/openclaw-ecc-orchestrator): plans, routing by tier, worktrees, escalation and budgets.
- [pr-review-bots](https://github.com/kennylhilljr/pr-review-bots): `prbot` for App identities, tokens, commits, pushes and cross-review.
