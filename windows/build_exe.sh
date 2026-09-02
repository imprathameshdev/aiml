#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Quick rebuild of just the lotvulture.exe (no Inno Setup installer).
# Use this for faster iteration when testing code changes on Windows.
#
# Usage:
#   ./windows/build_exe.sh
#
# Output:
#   build/windows/dist/lotvulture/lotvulture.exe
# ---------------------------------------------------------------------------
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BUILD_DIR="$REPO_ROOT/build/windows"
WINE_PREFIX="${WINE_PREFIX:-$BUILD_DIR/wine}"
GSTREAMER_DIR="${GSTREAMER_DIR:-$REPO_ROOT/gstreamer}"

export WINEPREFIX="$WINE_PREFIX"
export WINEDEBUG=-all
export PATH="$GSTREAMER_DIR/bin:$PATH"

# Kill stale Wine processes
wineserver -k 2>/dev/null || true
sleep 1

# Sanity check
if ! wine python -c "print('ok')" 2>/dev/null; then
    echo "Wine Python not responding — run windows/build.sh first."
    exit 1
fi

# Ensure dependencies are installed (picks up new/changed packages)
echo "[PIP] Installing Python dependencies..."
wine python -m pip install --quiet -r "$REPO_ROOT/requirements.txt" 2>&1 | grep -v "^$" || true

# Rebuild
rm -rf "$BUILD_DIR/dist" "$BUILD_DIR/pycache"
cd "$BUILD_DIR"
wine python -m PyInstaller \
    "$BUILD_DIR/lotvulture.spec" \
    --distpath "$BUILD_DIR/dist" \
    --workpath "$BUILD_DIR/pycache"
wait
cd "$REPO_ROOT"

# Post-process: patch the (null) DLL name in compiled .pyd files
# (MinGW cross-compilation emits "(null)" instead of "python312.dll")
echo "[PATCH] Fixing null DLL names in compiled .pyd files..."
for pyd in \
    "$BUILD_DIR/dist/lotvulture/_internal/vulturevision/_vulturevision.pyd" \
    "$BUILD_DIR/dist/lotvulture/_internal/gi/_gi.pyd"; do
    if [ -f "$pyd" ]; then
        python3 "$SCRIPT_DIR/patch_pyd.py" "$pyd"
    fi
done

EXE="$BUILD_DIR/dist/lotvulture/lotvulture.exe"
if [ -f "$EXE" ]; then
    SIZE=$(du -h "$EXE" | cut -f1)
    echo ""
    echo "============================================================"
    echo "  EXE: $EXE"
    echo "  Size: $SIZE"
    echo "  Copy the entire dist/lotvulture/ folder to Windows."
    echo "============================================================"
else
    echo "Build failed — $EXE not found."
    exit 1
fi