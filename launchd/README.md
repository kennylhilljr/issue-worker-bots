# launchd jobs for event-driven intake

These three plists are a proposal and are not installed by anything in this repository. They move `iwb` from a 5-minute full scan to three jobs:

| Label | Runs | When | Log |
| --- | --- | --- | --- |
| `com.kennylhilljr.issue-intake-queue` | `iwb tick --from-queue ~/.local/state/github-events/iwb` | Whenever the issue queue has entries (`QueueDirectories`), at most once a minute (`ThrottleInterval` 60), and once at load | `~/Library/Logs/issue-intake.log` |
| `com.kennylhilljr.issue-code` | `iwb code --from-queue ~/.local/state/github-events/iwb-code` | Whenever the code queue has entries, at most once a minute, and once at load | `~/Library/Logs/issue-code.log` |
| `com.kennylhilljr.issue-intake` | `iwb tick` | Every hour (`StartInterval` 3600), as a backstop for missed events | `~/Library/Logs/issue-intake.log` |

OpenClaw's github-event-router writes one JSON file per GitHub issue event into the issue queue. The issue queue job runs intake, analysis and approval for the issues named there and exits; it never codes. When an issue has a plan that is approved and ready to code (and `coding_live` is set), the job writes a hand-off file (the same event with `"stage": "code"`) into the code queue, and the code job runs the coding. A coding run can take hours, and launchd does not start a job that is still running, so keeping coding in its own job keeps new issue events moving. Before coding, the code job reads the issue again; if it changed since the plan was analyzed, it sends the issue back to the issue queue instead. A `sweep.json` from the router (a post it couldn't tie to one issue) makes the issue queue job run the hourly pass, without coding, for one repository or all of them. The code job and the hourly job's own coding share `work.lock`, so only one coding pass runs at a time.

launchd relaunches each queue job for as long as its folder holds any entry, so each run deletes or moves out everything except issues held back by a per-pass limit, and a run's first issue is never held back:

| Folder | Contents |
| --- | --- |
| `~/.local/state/github-events/iwb/`, `iwb-code/` | Watched by the queue jobs. New events and hand-offs, and the untouched events of issues that only hit a per-pass limit. |
| `iwb-deferred/`, `iwb-code-deferred/` | Events of issues whose step failed or whose GitHub read failed, with an `attempts` count, and events waiting for capacity or a delay (a coding lock held by another pass, the hour after a model failure), which don't count an attempt. The hourly job moves them back into their queue after its scan. |
| `iwb-rejected/`, `iwb-code-rejected/` | Events that can never be handled, events deferred 24 times, and every other entry found in a queue: stray files such as `.DS_Store`, folders (moved whole) and symlinks (the link is moved; its target is never followed or touched). |

All of these sit in `~/.local/state/github-events/`, next to the queues, never inside them. Handled events are deleted. A failing issue is retried once an hour and rejected after about a day. A burst of events larger than the per-pass limits (one analysis or one coding run per run) drains one run at a time: the issues a run couldn't reach stay in the queue, and launchd starts the next run after `ThrottleInterval`, about a minute later. Nothing empties the rejected folders: check them now and then, and delete what you have read. If the issue queue job finds the hourly job holding `tick.lock`, it leaves the queue as it is and exits 0, and launchd starts it again a minute later.

A queue job only touches a folder that is the default path above, or whose parent folder holds a regular file named `.github-events-queue`. The router creates that file in `~/.local/state/github-events/` with every write, and `iwb` creates it when it writes a hand-off. Any other folder, a folder that is a symlink, or a path that is not a folder is refused with exit 2, and nothing in it is moved. So is a queue whose `-deferred` or `-rejected` folder is a symlink or a file.

The paths are absolute because launchd does not expand `~`. `PATH` and `HOME` are copied from the plist that `install-intake.sh` wrote on this machine; if Python, `gh` or the CLIs move, update all three files.

## Install

```bash
mkdir -p ~/.local/state/github-events/iwb ~/.local/state/github-events/iwb-code
touch ~/.local/state/github-events/.github-events-queue
cp launchd/com.kennylhilljr.issue-intake-queue.plist launchd/com.kennylhilljr.issue-code.plist launchd/com.kennylhilljr.issue-intake.plist ~/Library/LaunchAgents/
launchctl bootout gui/$(id -u)/com.kennylhilljr.issue-intake
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.kennylhilljr.issue-intake.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.kennylhilljr.issue-code.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.kennylhilljr.issue-intake-queue.plist
```

Create both queue folders before bootstrapping the queue jobs, and install the code job together with the issue queue job: without it, hand-offs pile up in `iwb-code/` and approved plans are only coded by the hourly job. Run the commands from the repository root.

## Check

```bash
launchctl print gui/$(id -u)/com.kennylhilljr.issue-intake-queue | grep -E 'state|path|runs|last exit'
launchctl print gui/$(id -u)/com.kennylhilljr.issue-code | grep -E 'state|path|runs|last exit'
launchctl print gui/$(id -u)/com.kennylhilljr.issue-intake | grep -E 'state|run interval|runs|last exit'
tail -n 20 ~/Library/Logs/issue-intake.log ~/Library/Logs/issue-code.log
ls -A ~/.local/state/github-events/
```

The backstop should show a run interval of 3600 seconds. After the next issue event, the intake log shows a `queue pass done` line and the issue queue is empty again; after a hand-off, the code log shows a `code queue pass done` line. The `-deferred` and `-rejected` folders are created the first time something is moved there.

## Roll back

```bash
launchctl bootout gui/$(id -u)/com.kennylhilljr.issue-intake-queue
launchctl bootout gui/$(id -u)/com.kennylhilljr.issue-code
rm ~/Library/LaunchAgents/com.kennylhilljr.issue-intake-queue.plist ~/Library/LaunchAgents/com.kennylhilljr.issue-code.plist
./install-intake.sh
```

`install-intake.sh` rewrites `com.kennylhilljr.issue-intake.plist` with the 5-minute interval and bootstraps it again; that job codes as before. Nothing drains the queue folders after that. The router keeps adding files until it is switched off, and each `tick` moves deferred files back into the queues. Once the router is off, empty the folders:

```bash
rm -rf ~/.local/state/github-events/iwb ~/.local/state/github-events/iwb-deferred ~/.local/state/github-events/iwb-rejected ~/.local/state/github-events/iwb-code ~/.local/state/github-events/iwb-code-deferred ~/.local/state/github-events/iwb-code-rejected ~/.local/state/github-events/.tmp
```

## install-intake.sh

The script still installs only the 5-minute full-scan job. It was left alone so that rerunning it stays a known rollback. Teaching it a `--queue` option that writes these plists from the paths it already detects would be a small follow-up.
