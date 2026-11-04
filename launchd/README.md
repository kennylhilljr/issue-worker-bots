# launchd jobs for event-driven intake

These two plists are a proposal and are not installed by anything in this repository. They move `iwb` from a 5-minute full scan to two jobs:

| Label | Runs | When |
| --- | --- | --- |
| `com.kennylhilljr.issue-intake-queue` | `iwb tick --from-queue ~/.local/state/github-events/iwb` | Whenever the queue folder has files (`QueueDirectories`), at most once a minute (`ThrottleInterval` 60), and once at load |
| `com.kennylhilljr.issue-intake` | `iwb tick` | Every hour (`StartInterval` 3600), as a backstop for missed events |

OpenClaw's github-event-router writes one JSON file per GitHub issue event into the queue folder. launchd relaunches the queue job for as long as that folder holds any entry, so each run empties it into three places:

| Folder | Contents |
| --- | --- |
| `~/.local/state/github-events/iwb/` | Watched by the queue job. New events only. |
| `~/.local/state/github-events/iwb-deferred/` | Events of issues that failed or wait for a later pass, with an `attempts` count. The hourly job moves them back into the queue after its scan. |
| `~/.local/state/github-events/iwb-rejected/` | Events that can never be handled, entries that aren't regular `.json` files (such as `.DS_Store`), and events deferred 24 times. |

Handled events are deleted. A failing issue is therefore retried once an hour and rejected after about a day. A burst of events larger than the per-pass limits (one analysis per run) also waits for the hourly job, so it drains about one analysis an hour. Nothing empties `iwb-rejected/`: check it now and then, and delete what you have read. If the queue job finds the hourly job holding `tick.lock`, it leaves the queue as it is and exits 0, and launchd starts it again a minute later. Both jobs log to `~/Library/Logs/issue-intake.log`.

The paths are absolute because launchd does not expand `~`. `PATH` and `HOME` are copied from the plist that `install-intake.sh` wrote on this machine; if Python, `gh` or the CLIs move, update both files.

## Install

```bash
mkdir -p ~/.local/state/github-events/iwb
cp launchd/com.kennylhilljr.issue-intake-queue.plist launchd/com.kennylhilljr.issue-intake.plist ~/Library/LaunchAgents/
launchctl bootout gui/$(id -u)/com.kennylhilljr.issue-intake
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.kennylhilljr.issue-intake.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.kennylhilljr.issue-intake-queue.plist
```

Create the queue folder before bootstrapping the queue job. Run the commands from the repository root.

## Check

```bash
launchctl print gui/$(id -u)/com.kennylhilljr.issue-intake-queue | grep -E 'state|path|runs|last exit'
launchctl print gui/$(id -u)/com.kennylhilljr.issue-intake | grep -E 'state|run interval|runs|last exit'
tail -n 20 ~/Library/Logs/issue-intake.log
ls -A ~/.local/state/github-events/iwb ~/.local/state/github-events/iwb-deferred ~/.local/state/github-events/iwb-rejected 2>/dev/null
```

The backstop should show a run interval of 3600 seconds. After the next issue event, the log shows a `queue pass done` line and the queue folder is empty again. The `-deferred` and `-rejected` folders are created the first time something is moved there.

## Roll back

```bash
launchctl bootout gui/$(id -u)/com.kennylhilljr.issue-intake-queue
rm ~/Library/LaunchAgents/com.kennylhilljr.issue-intake-queue.plist
./install-intake.sh
```

`install-intake.sh` rewrites `com.kennylhilljr.issue-intake.plist` with the 5-minute interval and bootstraps it again. Nothing drains the queue folder after that. The router keeps adding files to it until it is switched off, and each `tick` moves deferred files back into it. Once the router is off, empty all three folders:

```bash
rm -rf ~/.local/state/github-events/iwb ~/.local/state/github-events/iwb-deferred ~/.local/state/github-events/iwb-rejected
```

## install-intake.sh

The script still installs only the 5-minute full-scan job. It was left alone so that rerunning it stays a known rollback. Teaching it a `--queue` option that writes both plists from the paths it already detects would be a small follow-up.
