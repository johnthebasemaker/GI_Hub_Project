#!/bin/zsh
# run_streamlit.sh — wrapper launched by com.gi.streamlit.plist.
# Solves the exit-126 issue some macOS launchd contexts hit when chaining
# `caffeinate -dis <abs-path-to-streamlit>` directly via ProgramArguments.
#
# The wrapper exec's streamlit with the venv binary, under caffeinate, with
# explicit PATH + cwd so nothing depends on launchd's quirky env defaults.

set -e

# Resolve the project root from this script's location (no "$0" tricks).
SCRIPT_DIR="${0:A:h}"
PROJECT_DIR="${SCRIPT_DIR:h:h}"

cd "$PROJECT_DIR"

export PATH="$PROJECT_DIR/.venv/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export TZ="${TZ:-Asia/Riyadh}"
export GI_SUPPRESS_EMBEDDED_WORKER="${GI_SUPPRESS_EMBEDDED_WORKER:-1}"
export OLLAMA_HOST="${OLLAMA_HOST:-http://localhost:11434}"

STREAMLIT="$PROJECT_DIR/.venv/bin/streamlit"

# Defensive: confirm streamlit binary exists + is executable before launchd
# logs another generic exit-126.
if [[ ! -x "$STREAMLIT" ]]; then
    print -u2 "ERROR: $STREAMLIT not found or not executable."
    print -u2 "Run: $PROJECT_DIR/.venv/bin/pip install -r requirements.txt"
    exit 70   # EX_SOFTWARE — surfaces in launchctl as the failure reason
fi

# caffeinate keeps the Mac awake while streamlit serves. -d = display, -i = idle,
# -s = system. We exec it so caffeinate becomes the supervised PID; when it
# exits, launchd treats it as the service exit.
exec /usr/bin/caffeinate -dis "$STREAMLIT" run main.py \
    --server.headless true \
    --server.address 127.0.0.1 \
    --server.port 8501
