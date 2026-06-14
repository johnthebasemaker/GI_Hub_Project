#!/usr/bin/env zsh
# restart_app.sh — restart Streamlit (and worker if --worker passed)
# after pulling new code. Tunnel + backup keep running.
#
# Usage:
#   ./host_setup/scripts/restart_app.sh             # just Streamlit
#   ./host_setup/scripts/restart_app.sh --worker    # Streamlit + WhatsApp worker
#   ./host_setup/scripts/restart_app.sh --all       # all four services

set -euo pipefail

restart() {
    local svc="$1"
    launchctl kickstart -k "gui/$UID/$svc" 2>/dev/null \
        || { echo "  ⚠ $svc not loaded — run install.sh first"; return 0; }
    print -P "%F{green}✓%f restarted $svc"
}

case "${1:-}" in
    --worker) restart com.gi.streamlit; restart com.gi.whatsapp-worker ;;
    --all)
        for svc in com.gi.streamlit com.gi.whatsapp-worker com.gi.cloudflared com.gi.backup; do
            restart $svc
        done
        ;;
    *) restart com.gi.streamlit ;;
esac

sleep 1
print -P ""
"${0:A:h}/install.sh" --status
