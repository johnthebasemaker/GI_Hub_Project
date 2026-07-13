#!/usr/bin/env zsh
# uninstall.sh — unloads + removes all GI Hub launchd services.
# Data (gi_database.db, uploads/, backups) is NOT touched.

set -euo pipefail

TARGET_DIR="$HOME/Library/LaunchAgents"
SERVICES=(
    com.gi.streamlit
    com.gi.whatsapp-worker
    com.gi.cloudflared
    com.gi.backup
)

for svc in $SERVICES; do
    if [[ -f "$TARGET_DIR/$svc.plist" ]]; then
        launchctl unload "$TARGET_DIR/$svc.plist" 2>/dev/null || true
        rm -f "$TARGET_DIR/$svc.plist"
        print -P "%F{green}✓%f  removed $svc"
    else
        print -P "%F{yellow}-%f  $svc was not installed"
    fi
done

print -P ""
print -P "Done. Data + logs are intact. To remove logs too:"
print -P "  rm ~/Library/Logs/gi-*.log ~/Library/Logs/gi-*.err"
