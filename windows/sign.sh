#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Sign the LotVulture Windows installer with an Authenticode certificate.
#
# Uses osslsigncode (Linux native) – no Wine required for this step.
#
# Prerequisites:
#   sudo apt install osslsigncode
#
# Usage:
#   ./windows/sign.sh path/to/installer.exe
#
# Environment variables:
#   WINDOWS_SIGN_CERT    – path to .pfx or .p12 certificate file
#                          (default: ~/.lotvulture/windows.p12)
#   WINDOWS_SIGN_PASS    – certificate password (will prompt if not set)
#   WINDOWS_SIGN_URL     – publisher URL for signature (default: https://lotvulture.com)
# ---------------------------------------------------------------------------
set -euo pipefail

if [ $# -lt 1 ]; then
    echo "Usage: $0 <installer.exe>"
    exit 1
fi

INSTALLER="$1"
if [ ! -f "$INSTALLER" ]; then
    echo "File not found: $INSTALLER"
    exit 1
fi

# ---- Config ----------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
CERT="${WINDOWS_SIGN_CERT:-$HOME/.lotvulture/windows.p12}"
URL="${WINDOWS_SIGN_URL:-https://lotvulture.com}"
TIMESTAMP_SERVER="${WINDOWS_SIGN_TSERVER:-http://timestamp.digicert.com}"

# ---- Check prerequisites ---------------------------------------------------
if ! command -v osslsigncode &>/dev/null; then
    echo "[SIGN] osslsigncode not found. Install: sudo apt install osslsigncode"
    echo "       Skipping signing."
    exit 0
fi

# ---- Check certificate -----------------------------------------------------
if [ ! -f "$CERT" ]; then
    echo "[SIGN] No certificate found at: $CERT"
    echo "       Set WINDOWS_SIGN_CERT to your .p12/.pfx file."
    echo "       Skipping signing."
    exit 0
fi

# ---- Get password -----------------------------------------------------------
PASS="${WINDOWS_SIGN_PASS:-}"
if [ -z "$PASS" ]; then
    echo -n "Enter certificate password: "
    read -rs PASS
    echo
fi

# ---- Sign -------------------------------------------------------------------
echo "[SIGN] Signing installer with $CERT..."
OUTPUT="${INSTALLER%.exe}-signed.exe"

osslsigncode sign \
    -pkcs12 "$CERT" \
    -pass "$PASS" \
    -n "Lot Vulture LLC" \
    -i "$URL" \
    -ts "$TIMESTAMP_SERVER" \
    -in "$INSTALLER" \
    -out "$OUTPUT"

# Replace original with signed version
mv "$OUTPUT" "$INSTALLER"

echo "[SIGN] Done: $INSTALLER"
