#!/usr/bin/env bash
# Install (or remove, with --uninstall) a launchd agent that runs `iwb tick` (intake, then analysis) every 5 minutes.
# launchd starts jobs with a bare PATH, so this records where python3, gh and openssl live right now.
set -euo pipefail
repo="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
label="com.kennylhilljr.issue-intake"
plist="$HOME/Library/LaunchAgents/$label.plist"
log="$HOME/Library/Logs/issue-intake.log"
domain="gui/$(id -u)"

launchctl bootout "$domain/$label" 2>/dev/null || true
if [ "${1:-}" = "--uninstall" ]; then
  rm -f "$plist"
  echo "Removed $label."
  exit 0
fi

path_dirs=""
for tool in python3 gh openssl git claude codex; do
  if p="$(command -v "$tool")"; then
    d="$(dirname "$p")"
    case ":$path_dirs:" in *":$d:"*) ;; *) path_dirs="${path_dirs:+$path_dirs:}$d" ;; esac
  else
    echo "warning: $tool not found on PATH" >&2
  fi
done
path_dirs="$path_dirs:/usr/bin:/bin:/usr/sbin:/sbin"
python="$(command -v python3)"

mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
cat > "$plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$label</string>
  <key>ProgramArguments</key>
  <array><string>$python</string><string>$repo/iwb</string><string>tick</string></array>
  <key>WorkingDirectory</key><string>$repo</string>
  <key>StartInterval</key><integer>300</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$log</string>
  <key>StandardErrorPath</key><string>$log</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>$path_dirs</string>
    <key>HOME</key><string>$HOME</string>
  </dict>
</dict>
</plist>
PLIST
plutil -lint "$plist" >/dev/null
launchctl bootstrap "$domain" "$plist"
echo "Installed $label: runs every 5 minutes. Log: $log"
