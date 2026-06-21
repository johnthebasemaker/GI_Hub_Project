#!/usr/bin/env bash
# ----------------------------------------------------------------------
# scripts/bundle_locate_anything_weights.sh — Phase 8B
#
# Package the LocateAnything-3B weights at HQ for transport to sites.
# Run this ONCE at HQ after scripts/download_model.sh has populated
# ~/Library/Caches/gi_locate/LocateAnything-3B/.
#
# Produces TWO files under ~/Downloads/:
#   gi_locate_bundle_<YYYY-MM-DD>.tar.gz   (~6 GB)
#   gi_locate_bundle_<YYYY-MM-DD>.sha256   (integrity check)
#
# Transport both files together to each site (USB / SFTP / signed S3 url
# / whatever). At the site, run:
#   ./scripts/install_locate_anything_weights.sh gi_locate_bundle_*.tar.gz
# ----------------------------------------------------------------------

set -euo pipefail

# ── Config ────────────────────────────────────────────────────────────
WEIGHTS_DIR="${HOME}/Library/Caches/gi_locate"
WEIGHTS_SUBDIR="LocateAnything-3B"
TODAY="$(date +%Y-%m-%d)"
OUT_DIR="${HOME}/Downloads"
OUT_BASE="gi_locate_bundle_${TODAY}"

ok()   { printf '\033[32m[ok]\033[0m   %s\n' "$1"; }
fail() { printf '\033[31m[fail]\033[0m %s\n' "$1" >&2; exit 1; }
info() { printf '\033[36m[info]\033[0m %s\n' "$1"; }

# ── Pre-flight ────────────────────────────────────────────────────────
echo "════════════════════════════════════════════════════════════════"
echo "  GI Hub — LocateAnything bundle (HQ → site transport)"
echo "════════════════════════════════════════════════════════════════"

if [ ! -d "${WEIGHTS_DIR}/${WEIGHTS_SUBDIR}" ]; then
  fail "Weights dir missing: ${WEIGHTS_DIR}/${WEIGHTS_SUBDIR}
        Run scripts/download_model.sh first."
fi

if [ ! -f "${WEIGHTS_DIR}/${WEIGHTS_SUBDIR}/config.json" ]; then
  fail "config.json missing inside ${WEIGHTS_DIR}/${WEIGHTS_SUBDIR}/
        The download appears incomplete — re-run scripts/download_model.sh."
fi

mkdir -p "${OUT_DIR}"

# ── Bundle ────────────────────────────────────────────────────────────
TARBALL="${OUT_DIR}/${OUT_BASE}.tar.gz"
SHASUM="${OUT_DIR}/${OUT_BASE}.sha256"

info "Packing ${WEIGHTS_DIR}/${WEIGHTS_SUBDIR}/ → ${TARBALL}"
info "This typically takes 2–5 minutes for a 6 GB model."
tar -czf "${TARBALL}" -C "${WEIGHTS_DIR}" "${WEIGHTS_SUBDIR}"
ok "tarball: $(du -h "${TARBALL}" | cut -f1) → ${TARBALL}"

info "Computing SHA-256 (integrity check) …"
# Output format is `<hash>  <filename>` — matches `shasum -c` expectation.
(cd "${OUT_DIR}" && shasum -a 256 "${OUT_BASE}.tar.gz" > "${OUT_BASE}.sha256")
ok "checksum file: ${SHASUM}"

# ── Done ──────────────────────────────────────────────────────────────
echo ""
echo "════════════════════════════════════════════════════════════════"
echo "  Bundle ready for transport. Transfer BOTH files to each site:"
echo ""
echo "    ${TARBALL}"
echo "    ${SHASUM}"
echo ""
echo "  At the site:"
echo "    ./scripts/install_locate_anything_weights.sh ${OUT_BASE}.tar.gz"
echo "════════════════════════════════════════════════════════════════"
