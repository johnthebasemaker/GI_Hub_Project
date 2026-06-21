#!/usr/bin/env bash
# ----------------------------------------------------------------------
# scripts/download_model.sh — Phase 8A
#
# Downloads the two AI models the GI Hub depends on:
#   1. LocateAnything-3B  (~6 GB)  → Smart Scan Tier-3 fallback
#   2. qwen2.5vl:7b       (~5 GB)  → handwriting OCR (Ollama)
#
# This script is NOT executed by any automated tooling. Run it MANUALLY
# once per site (or once at HQ + bundle to sites). Both models are large
# downloads — verify the destination has free disk + good network first.
#
# Idempotent: re-running with the weights already present is a fast
# no-op for the LocateAnything bundle. `ollama pull` likewise no-ops
# if the model digest matches.
# ----------------------------------------------------------------------

set -euo pipefail

# ── Configuration ─────────────────────────────────────────────────────
LOCATE_REPO="nvidia/LocateAnything-3B"
LOCATE_DEST="${HOME}/Library/Caches/gi_locate/LocateAnything-3B"
QWEN_MODEL="qwen2.5vl:7b"

echo "════════════════════════════════════════════════════════════════"
echo "  GI Hub — AI model bootstrap"
echo "════════════════════════════════════════════════════════════════"

# ── Step 1: LocateAnything-3B ─────────────────────────────────────────
echo ""
echo "[1/2] LocateAnything-3B → ${LOCATE_DEST}"
mkdir -p "${LOCATE_DEST}"

if [ -f "${LOCATE_DEST}/config.json" ]; then
  echo "      ✓ Already on disk — skipping download."
else
  if ! command -v huggingface-cli >/dev/null 2>&1; then
    echo "      Installing huggingface_hub CLI ..."
    pip install --quiet 'huggingface_hub[cli]>=0.24'
  fi
  echo "      Downloading ${LOCATE_REPO} (~6 GB, may take a while) ..."
  huggingface-cli download "${LOCATE_REPO}" \
    --local-dir "${LOCATE_DEST}" \
    --local-dir-use-symlinks False
  echo "      ✓ Downloaded to ${LOCATE_DEST}"
fi

# ── Step 2: qwen2.5vl:7b via Ollama ───────────────────────────────────
echo ""
echo "[2/2] ${QWEN_MODEL} via Ollama (for handwriting OCR)"

if ! command -v ollama >/dev/null 2>&1; then
  echo "      ✗ ERROR: 'ollama' CLI not found on PATH."
  echo "        Install from https://ollama.com/download then re-run this script."
  exit 1
fi

if ! ollama list 2>/dev/null | grep -q "${QWEN_MODEL%:*}"; then
  echo "      Pulling ${QWEN_MODEL} (~5 GB) ..."
  ollama pull "${QWEN_MODEL}"
  echo "      ✓ Installed."
else
  echo "      ✓ Already installed."
fi

echo ""
echo "════════════════════════════════════════════════════════════════"
echo "  All models ready. Smart Scan AI + handwriting OCR are good to go."
echo "  Toggle Smart Scan AI on at:  Admin Portal → Settings → AI Sidecar"
echo "════════════════════════════════════════════════════════════════"
