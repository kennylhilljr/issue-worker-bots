# launchd jobs for event-driven intake

These two plists are a proposal and are not installed by anything in this repository. They move `iwb` from a 5-minute full scan to two jobs:

| Label | Runs | When |
| --- | --- | --- |
| `com.kennylhilljr.issue-intake-queue` | `iwb tick --from-queue ~/.local/state/github-events/iwb` | Whenever the queue folder has files (`QueueDirectories`), at most once a minute (`ThrottleInterval` 60), and once at load |
| `com.kennylhilljr.issue-intake` | `iwb tick` | Every hour (`StartInterval` 3600), as a backstop for missed events |

OpenClaw's github-event-router writes one JSON file per GitHub issue event into the queue folder. The queue job handles each issue named there and deletes its files. launchd keeps relaunching the job while the folder is not empty, so a file that keeps failing is retried about once a minute; files that can never succeed are deleted with a log line. Both jobs log to `~/Library/Logs/issue-intake.log`.

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
```

The backstop should show a run interval of 3600 seconds. After the next issue event, the log shows a `queue pass done` line and the queue folder is empty again.

## Roll back

```bash
launchctl bootout gui/$(id -u)/com.kennylhilljr.issue-intake-queue
rm ~/Library/LaunchAgents/com.kennylhilljr.issue-intake-queue.plist
./install-intake.sh
```

`install-intake.sh` rewrites `com.kennylhilljr.issue-intake.plist` with the 5-minute interval and bootstraps it again. Nothing reads the queue folder after that, and the router keeps adding files to it until it is switched off, so empty it with `rm ~/.local/state/github-events/iwb/*.json` once the router is off.

## install-intake.sh

The script still installs only the 5-minute full-scan job. It was left alone so that rerunning it stays a known rollback. Teaching it a `--queue` option that writes both plists from the paths it already detects would be a small follow-up.
