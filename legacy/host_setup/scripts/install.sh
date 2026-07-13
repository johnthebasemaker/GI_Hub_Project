#!/usr/bin/env zsh
# -----------------------------------------------------------------------------
# install.sh — one-shot installer for the GI Hub launchd services on macOS.
#
# Installs four services that auto-start on login and restart on crash:
#   1. com.gi.streamlit         — the Streamlit web app on 127.0.0.1:8501
#   2. com.gi.whatsapp-worker   — pywhatkit/Twilio outbound worker
#   3. com.gi.cloudflared       — Cloudflare Tunnel to gi.giinventory.com
#   4. com.gi.backup            — nightly SQLite + uploads backup to iCloud
#
# Prerequisites:
#   - cloudflared installed at /usr/local/bin/cloudflared
#   - cloudflared tunnel "gi-hub" created and ~/.cloudflared/config.yml set up
#   - .venv exists at PROJECT/.venv with all requirements installed
#   - gi_database.db exists (run a one-off `streamlit run main.py` first)
#
# Usage:
#   ./host_setup/scripts/install.sh                           # install + load (no AI sidecar)
#   ./host_setup/scripts/install.sh --with-locate-anything    # also install Smart Scan AI sidecar
#   ./host_setup/scripts/install.sh --status                  # show launchctl status
#   ./host_setup/scripts/install.sh --logs                    # tail all log files
#
# Phase 8B note on --with-locate-anything:
#   - The LocateAnything sidecar (com.gi.locate-anything) is OPT-IN per
#     site. Run the install with this flag ONLY on pilot sites that have
#     the LocateAnything-3B weights bundle deployed under
#     ~/Library/Caches/gi_locate/LocateAnything-3B/.
#   - Sites without the flag never load the plist — no sidecar process,
#     no resource cost. Smart Scan keeps working on its two-tier YOLO path.
# -----------------------------------------------------------------------------

set -euo pipefail

SCRIPT_DIR="${0:A:h}"
PROJECT_DIR="${SCRIPT_DIR:h:h}"
LAUNCHD_DIR="$PROJECT_DIR/host_setup/launchd"
TARGET_DIR="$HOME/Library/LaunchAgents"

# Default service set — unchanged from pre-Phase 8B installs.
SERVICES=(
    com.gi.streamlit
    com.gi.whatsapp-worker
    com.gi.cloudflared
    com.gi.backup
)

# Phase 8B — optional 5th service added when --with-locate-anything is passed.
WITH_LOCATE_ANYTHING=0
NEW_ARGS=()
for arg in "$@"; do
    case "$arg" in
        --with-locate-anything)
            WITH_LOCATE_ANYTHING=1
            ;;
        *)
            NEW_ARGS+=("$arg")
            ;;
    esac
done
set -- "${NEW_ARGS[@]}"

if [[ $WITH_LOCATE_ANYTHING -eq 1 ]]; then
    SERVICES+=(com.gi.locate-anything)
fi

print_header() {
    print -P "%F{cyan}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━%f"
    print -P "%F{cyan}  $1%f"
    print -P "%F{cyan}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━%f"
}

show_status() {
    print_header "GI Hub launchd status"
    local svc line pid rc
    for svc in $SERVICES; do
        line=$(launchctl list 2>/dev/null | awk -v s="$svc" '$3==s {print $0}')
        if [[ -n "$line" ]]; then
            pid=$(echo "$line" | awk '{print $1}')
            rc=$(echo "$line" | awk '{print $2}')
            if [[ "$pid" == "-" ]]; then
                print -P "%F{yellow}⏸  $svc%f  (loaded, not running, last exit=$rc)"
            else
                print -P "%F{green}✓  $svc%f  PID=$pid"
            fi
        else
            print -P "%F{red}✗  $svc%f  (not loaded)"
        fi
    done
}

show_logs() {
    print_header "Streaming logs — Ctrl-C to stop"
    tail -f "$HOME/Library/Logs/gi-"*.log "$HOME/Library/Logs/gi-"*.err 2>/dev/null
}

# Dispatch the read-only flags
if [[ "${1:-}" == "--status" ]]; then show_status; exit 0; fi
if [[ "${1:-}" == "--logs"   ]]; then show_logs;   exit 0; fi

# -----------------------------------------------------------------------------
# Sanity checks
# -----------------------------------------------------------------------------
print_header "Pre-flight"

if [[ ! -d "$PROJECT_DIR/.venv" ]]; then
    print -P "%F{red}ERROR%f .venv not found at $PROJECT_DIR/.venv"
    exit 1
fi
if [[ ! -f "$PROJECT_DIR/.venv/bin/streamlit" ]]; then
    print -P "%F{red}ERROR%f streamlit not installed in .venv. Run:"
    echo "  cd '$PROJECT_DIR' && .venv/bin/pip install -r requirements.txt"
    exit 1
fi
if ! command -v cloudflared >/dev/null 2>&1; then
    print -P "%F{red}ERROR%f cloudflared not on PATH. Install it first:"
    echo "  curl -L -o cloudflared.tgz \\"
    echo "    https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-darwin-arm64.tgz"
    echo "  tar -xzf cloudflared.tgz && sudo mv cloudflared /usr/local/bin/"
    exit 1
fi
if [[ ! -f "$HOME/.cloudflared/config.yml" ]]; then
    print -P "%F{yellow}WARN%f  ~/.cloudflared/config.yml missing. Tunnel service will"
    echo "         crash-loop until you create the tunnel. See host_setup/README.md."
fi

print -P "%F{green}OK%f all checks passed"
print -P "    PROJECT_DIR = $PROJECT_DIR"
print -P "    USER_HOME   = $HOME"

# -----------------------------------------------------------------------------
# Render plist templates → ~/Library/LaunchAgents
# -----------------------------------------------------------------------------
print_header "Installing plists → $TARGET_DIR"
mkdir -p "$TARGET_DIR"
mkdir -p "$HOME/Library/Logs"

for svc in $SERVICES; do
    local tmpl="$LAUNCHD_DIR/$svc.plist.tmpl"
    local out="$TARGET_DIR/$svc.plist"
    if [[ ! -f "$tmpl" ]]; then
        print -P "%F{red}ERROR%f template missing: $tmpl"
        exit 1
    fi
    sed \
        -e "s|__PROJECT_DIR__|$PROJECT_DIR|g" \
        -e "s|__USER_HOME__|$HOME|g" \
        -e "s|__USER__|$USER|g" \
        "$tmpl" > "$out"
    print -P "%F{green}✓%f  rendered $svc.plist"
done

# Make every shipped script executable
chmod +x "$PROJECT_DIR/host_setup/scripts/"*.sh

# -----------------------------------------------------------------------------
# Load (or reload) each service
# -----------------------------------------------------------------------------
print_header "Loading services"
for svc in $SERVICES; do
    # Unload silently if already loaded — idempotent reinstall.
    launchctl unload "$TARGET_DIR/$svc.plist" 2>/dev/null || true
    launchctl load -w "$TARGET_DIR/$svc.plist"
    print -P "%F{green}✓%f  loaded $svc"
done

sleep 2
show_status

print_header "Next steps"
cat <<EOF
1. Test the app locally:           open http://localhost:8501
2. Test the public URL:            open https://gi.giinventory.com
3. Tail logs while you work:       ./host_setup/scripts/install.sh --logs
4. Fire a one-shot backup now:     ./host_setup/scripts/backup_db.sh
5. After editing code:             git pull && ./host_setup/scripts/restart_app.sh
EOF
