#!/usr/bin/env bash
# ----------------------------------------------------------------------
# scripts/install_locate_anything_weights.sh — Phase 8B
#
# Install a transported LocateAnything-3B bundle at a site. Run AT EACH
# SITE after the bundle .tar.gz + .sha256 from HQ are placed in CWD.
#
# Per Phase 8B Q6 — OVERWRITE always. Existing weights are replaced
# atomically; no version comparison, no downgrade refusal. Keeps the
# script simple and idempotent.
#
# Usage:
#   ./scripts/install_locate_anything_weights.sh gi_locate_bundle_2026-06-21.tar.gz
#
# Expects the matching .sha256 file in the same directory.
# ----------------------------------------------------------------------

set -euo pipefail

ok()   { printf '\033[32m[ok]\033[0m   %s\n' "$1"; }
fail() { printf '\033[31m[fail]\033[0m %s\n' "$1" >&2; exit 1; }
info() { printf '\033[36m[info]\033[0m %s\n' "$1"; }

# ── Args ──────────────────────────────────────────────────────────────
if [ $# -lt 1 ]; then
  echo "Usage: $0 <gi_locate_bundle_YYYY-MM-DD.tar.gz>"
  echo "       (expects matching .sha256 file in the same directory)"
  exit 2
fi

BUNDLE="$1"
SHASUM_FILE="${BUNDLE%.tar.gz}.sha256"
WEIGHTS_DIR="${HOME}/Library/Caches/gi_locate"
WEIGHTS_SUBDIR="LocateAnything-3B"

echo "════════════════════════════════════════════════════════════════"
echo "  GI Hub — install LocateAnything-3B weights at site"
echo "════════════════════════════════════════════════════════════════"

[ -f "${BUNDLE}" ]      || fail "Bundle not found: ${BUNDLE}"
[ -f "${SHASUM_FILE}" ] || fail "Checksum file not found: ${SHASUM_FILE}"

# ── Verify integrity ─────────────────────────────────────────────────
info "Verifying SHA-256 (refuses on mismatch — protects against partial transfer) …"
BUNDLE_DIR="$(cd "$(dirname "${BUNDLE}")" && pwd)"
BUNDLE_FN="$(basename "${BUNDLE}")"
SHASUM_FN="$(basename "${SHASUM_FILE}")"
(cd "${BUNDLE_DIR}" && shasum -a 256 -c "${SHASUM_FN}") \
  || fail "Checksum mismatch. Re-transfer the bundle from HQ."
ok "checksum verified"

# ── Install (overwrite always per spec Q6) ────────────────────────────
mkdir -p "${WEIGHTS_DIR}"
if [ -d "${WEIGHTS_DIR}/${WEIGHTS_SUBDIR}" ]; then
  info "Existing weights detected — removing for clean overwrite."
  rm -rf "${WEIGHTS_DIR}/${WEIGHTS_SUBDIR}"
  ok "old weights removed"
fi

info "Extracting ${BUNDLE_FN} → ${WEIGHTS_DIR}/"
tar -xzf "${BUNDLE}" -C "${WEIGHTS_DIR}"
ok "extracted"

# ── Verify destination ────────────────────────────────────────────────
if [ ! -f "${WEIGHTS_DIR}/${WEIGHTS_SUBDIR}/config.json" ]; then
  fail "Extraction succeeded but config.json missing under
        ${WEIGHTS_DIR}/${WEIGHTS_SUBDIR}/ — bundle may have been built wrong."
fi
ok "config.json present at ${WEIGHTS_DIR}/${WEIGHTS_SUBDIR}/"

# ── Done ──────────────────────────────────────────────────────────────
echo ""
echo "════════════════════════════════════════════════════════════════"
echo "  Smart Scan AI weights installed."
echo ""
echo "  Next steps at this site:"
echo "    1. (One-time) install the sidecar service with:"
echo "         ./host_setup/scripts/install.sh --with-locate-anything"
echo "    2. Flip the toggle ON via:"
echo "         Admin Portal → Settings → AI Sidecar"
echo "       (or directly:  sqlite3 gi_database.db"
echo "          \"UPDATE app_settings SET value='1' WHERE key='locate_anything_enabled';\")"
echo "════════════════════════════════════════════════════════════════"
