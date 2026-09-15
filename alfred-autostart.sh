#!/usr/bin/env bash
# alfred-autostart.sh — keep Alfred running in the background on this Mac (macOS launchd).
#
#   ./alfred-autostart.sh install     # start run_all.sh at login and restart it if it dies
#   ./alfred-autostart.sh uninstall
#   ./alfred-autostart.sh status
#
# Why: schedules (the morning brief) fire only while the supervisor runs. With this agent
# installed you do not need a terminal open. Logs go to logs/autostart.out.
# Note: launchd cannot wake a sleeping Mac; a job due while asleep runs when it wakes (grace
# window on the Schedules page). To wake on time: System Settings → Energy → schedule, or
# `sudo pmset repeat wakeorpoweron MTWRF 07:55:00`.
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$(pwd)"
LABEL="com.alfred.runall"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

case "${1:-status}" in
  install)
    mkdir -p "$HOME/Library/LaunchAgents" logs
    cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>/bin/bash</string><string>$ROOT/run_all.sh</string></array>
  <key>WorkingDirectory</key><string>$ROOT</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$ROOT/logs/autostart.out</string>
  <key>StandardErrorPath</key><string>$ROOT/logs/autostart.out</string>
  <key>EnvironmentVariables</key><dict>
    <key>PATH</key><string>$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    <key>HOME</key><string>$HOME</string>
  </dict>
</dict></plist>
EOF
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$PLIST"
    echo "installed: Alfred starts at login and is kept alive. Stop any run_all.sh you started by hand (it would fight over port 8787)."
    ;;
  uninstall)
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
    rm -f "$PLIST"
    echo "removed"
    ;;
  status)
    if launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1; then
      echo "installed and loaded ($PLIST)"; launchctl print "gui/$(id -u)/$LABEL" | grep -E "state|pid" | head -3
    else
      echo "not installed — ./alfred-autostart.sh install"
    fi
    ;;
  *) echo "usage: $0 install|uninstall|status"; exit 1 ;;
esac
