#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Cross-compile PyGObject for Windows using MinGW-w64 on Linux.
#
# Produces a wheel at  dist/PyGObject-*-cp312-cp312-win_amd64.whl
# that provides the ``gi`` package (including ``gi.repository``) for
# Python 3.12 on Windows.  This is needed by ``stream_manager.py`` which
# imports ``gi.repository.Gst``.
#
# The script is idempotent: if the output wheel already exists it skips
# the build.  Delete the wheel to force a rebuild.
#
# Prerequisites (build machine):
#   sudo apt install x86_64-w64-mingw32 build-essential curl ca-certificates
#
# Usage:
#   ./windows/build_pygobject.sh                         # Full build
#   ./windows/build_pygobject.sh --skip-downloads        # Reuse cached SDKs
# ---------------------------------------------------------------------------
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BUILD_DIR="${BUILD_DIR:-$SCRIPT_DIR/build/pygobject}"
OUTPUT_DIR="${OUTPUT_DIR:-$REPO_ROOT/dist}"
SKIP_DOWNLOADS=false

for arg in "$@"; do
    case "$arg" in
        --skip-downloads) SKIP_DOWNLOADS=true ;;
        *) echo "Unknown arg: $arg"; exit 1 ;;
    esac
done

mkdir -p "$BUILD_DIR" "$OUTPUT_DIR"

# ---- Config ----------------------------------------------------------------
PYGOBJECT_VERSION="3.56.3"
PYTHON_VERSION="3.12.9"
PYTHON_VERSION_MAJOR="3.12"
MSYS2_URL="https://repo.msys2.org/mingw/mingw64"

# MSYS2 packages needed for build (headers + import libs)
MSYS2_PKGS=(
    "mingw-w64-x86_64-gobject-introspection-1.86.0-4-any.pkg.tar.zst"
    "mingw-w64-x86_64-glib2-2.88.1-1-any.pkg.tar.zst"
    "mingw-w64-x86_64-libffi-3.4.7-1-any.pkg.tar.zst"
    "mingw-w64-x86_64-pcre2-10.45-1-any.pkg.tar.zst"
)

# Use pip to get the download URL for the correct version
PYGOBJECT_URL=$(python3 -c "
import json, urllib.request
resp = urllib.request.urlopen('https://pypi.org/pypi/PyGObject/${PYGOBJECT_VERSION}/json')
data = json.loads(resp.read())
for u in data['urls']:
    if u['packagetype'] == 'sdist':
        print(u['url'])
        break
" 2>/dev/null)

# ---- Check for existing wheel ----------------------------------------------
WHEEL_FILENAME="PyGObject-${PYGOBJECT_VERSION}-cp312-cp312-win_amd64.whl"
WHEEL_PATH="$OUTPUT_DIR/$WHEEL_FILENAME"

if [ -f "$WHEEL_PATH" ]; then
    echo "[PYGOBJECT] Wheel already exists: $WHEEL_PATH"
    echo "           Delete it or use --rebuild (not yet implemented) to rebuild."
    exit 0
fi

# ---- Dependency check ------------------------------------------------------
echo "[CHECK] Verifying cross-compilation tools..."
command -v x86_64-w64-mingw32-gcc >/dev/null 2>&1 || {
    echo "Missing: MinGW-w64. Install: sudo apt install g++-mingw-w64-x86-64"
    exit 1
}
command -v curl >/dev/null 2>&1 || { echo "Missing: curl"; exit 1; }
command -v tar >/dev/null 2>&1 || { echo "Missing: tar"; exit 1; }
echo "       All tools found."

# ---- Download MSYS2 build dependencies -------------------------------------
MSYS2_DIR="$BUILD_DIR/msys2"
if [ "$SKIP_DOWNLOADS" = true ] && [ -d "$MSYS2_DIR/lib" ]; then
    echo "[MSYS2] Using existing downloads (--skip-downloads)"
elif [ -d "$MSYS2_DIR/lib" ]; then
    echo "[MSYS2] Already downloaded at $MSYS2_DIR"
else
    echo "[MSYS2] Downloading build dependencies from MSYS2..."
    mkdir -p "$MSYS2_DIR/packages"
    for pkg in "${MSYS2_PKGS[@]}"; do
        pkg_path="$MSYS2_DIR/packages/$pkg"
        if [ ! -f "$pkg_path" ]; then
            echo "       Downloading $pkg ..."
            curl -fsSLo "$pkg_path" "$MSYS2_URL/$pkg"
        fi
    done

    echo "       Extracting..."
    for pkg in "$MSYS2_DIR/packages"/*.pkg.tar.zst; do
        [ -f "$pkg" ] || continue
        tar -I zstd -xf "$pkg" -C "$MSYS2_DIR" 2>/dev/null || true
    done
    # The packages extract to mingw64/ subdir; merge into MSYS2_DIR
    if [ -d "$MSYS2_DIR/mingw64" ]; then
        cp -r "$MSYS2_DIR/mingw64/"* "$MSYS2_DIR/"
        rm -rf "$MSYS2_DIR/mingw64"
    fi
    echo "       Extracted to $MSYS2_DIR"
fi

# ---- Windows Python headers + import library -------------------------------
PYTHON_HEADERS_DIR="$BUILD_DIR/python-headers"
PYTHON_LIB_DIR="$BUILD_DIR/python-lib"

if [ "$SKIP_DOWNLOADS" = true ] && [ -f "$PYTHON_HEADERS_DIR/pyconfig.h" ]; then
    echo "[PYTHON] Using existing headers (--skip-downloads)"
elif [ -f "$PYTHON_HEADERS_DIR/pyconfig.h" ]; then
    echo "[PYTHON] Already downloaded at $PYTHON_HEADERS_DIR"
else
    echo "[PYTHON] Downloading Windows Python headers from source tarball..."
    mkdir -p "$PYTHON_HEADERS_DIR"
    curl -fsSLo "$BUILD_DIR/python-src.tar.xz" \
        "https://www.python.org/ftp/python/${PYTHON_VERSION}/Python-${PYTHON_VERSION}.tar.xz"
    tar xf "$BUILD_DIR/python-src.tar.xz" \
        -C "$PYTHON_HEADERS_DIR" \
        --strip-components=2 \
        "Python-${PYTHON_VERSION}/Include" \
        "Python-${PYTHON_VERSION}/PC"
    rm -f "$BUILD_DIR/python-src.tar.xz"

    echo "[PYTHON] Creating import library from embeddable Python..."
    mkdir -p "$PYTHON_LIB_DIR"
    curl -fsSLo "$BUILD_DIR/python-embed.zip" \
        "https://www.python.org/ftp/python/${PYTHON_VERSION}/python-${PYTHON_VERSION}-embed-amd64.zip"
    unzip -qo "$BUILD_DIR/python-embed.zip" python3.dll python312.dll -d "$PYTHON_LIB_DIR"
    rm -f "$BUILD_DIR/python-embed.zip"

    pip3 install pefile --break-system-packages -q 2>/dev/null || true
    python3 -c "
import pefile, os
pe = pefile.PE(os.path.join('$PYTHON_LIB_DIR', 'python312.dll'))
with open(os.path.join('$PYTHON_LIB_DIR', 'python312.def'), 'w') as f:
    f.write('EXPORTS\n')
    for exp in pe.DIRECTORY_ENTRY_EXPORT.symbols:
        if exp.name:
            f.write('    ' + exp.name.decode() + '\n')
"
    x86_64-w64-mingw32-dlltool -d "$PYTHON_LIB_DIR/python312.def" -l "$PYTHON_LIB_DIR/libpython312.dll.a"
    cp "$PYTHON_LIB_DIR/libpython312.dll.a" "$PYTHON_LIB_DIR/libpython3.dll.a"
    echo "       Python headers at $PYTHON_HEADERS_DIR"
    echo "       Python import lib at $PYTHON_LIB_DIR/libpython312.dll.a"
fi

# ---- PyGObject source ------------------------------------------------------
PYGOBJECT_SRC="$BUILD_DIR/pygobject-src"
if [ "$SKIP_DOWNLOADS" = true ] && [ -d "$PYGOBJECT_SRC/gi" ]; then
    echo "[PYGOBJECT] Using existing source (--skip-downloads)"
elif [ -d "$PYGOBJECT_SRC/gi" ]; then
    echo "[PYGOBJECT] Already downloaded at $PYGOBJECT_SRC"
else
    echo "[PYGOBJECT] Downloading PyGObject ${PYGOBJECT_VERSION} source..."
    mkdir -p "$PYGOBJECT_SRC"
    curl -fsSLo "$BUILD_DIR/pygobject.tar.gz" "$PYGOBJECT_URL"
    tar xzf "$BUILD_DIR/pygobject.tar.gz" -C "$PYGOBJECT_SRC" --strip-components=1
    rm -f "$BUILD_DIR/pygobject.tar.gz"
    echo "       Source at $PYGOBJECT_SRC"
fi

# ---- Generate config.h ----------------------------------------------------
echo "[CONFIG] Generating config.h..."
PYGOBJECT_MAJOR=$(echo "$PYGOBJECT_VERSION" | cut -d. -f1)
PYGOBJECT_MINOR=$(echo "$PYGOBJECT_VERSION" | cut -d. -f2)
PYGOBJECT_MICRO=$(echo "$PYGOBJECT_VERSION" | cut -d. -f3)

cat > "$PYGOBJECT_SRC/config.h" << CONFIGH
#define PYGOBJECT_MAJOR_VERSION $PYGOBJECT_MAJOR
#define PYGOBJECT_MINOR_VERSION $PYGOBJECT_MINOR
#define PYGOBJECT_MICRO_VERSION $PYGOBJECT_MICRO
CONFIGH

# ---- Compile _gi.pyd -------------------------------------------------------
echo ""
echo "[COMPILE] Building _gi.pyd with MinGW-w64..."

GI_SRC="$PYGOBJECT_SRC/gi"
cd "$GI_SRC"

# Collect all .c files except Cairo-related
C_FILES=$(find . -maxdepth 1 -name '*.c' '!' -name '*cairo*' | sort)

# Include paths
INC_ROOTS=(
    "$GI_SRC"
    "$PYGOBJECT_SRC"
    "$PYGOBJECT_SRC/subprojects/pythoncapi-compat"
    "$MSYS2_DIR/include/gobject-introspection-1.0"
    "$MSYS2_DIR/include/glib-2.0"
    "$MSYS2_DIR/lib/glib-2.0/include"
    "$MSYS2_DIR/include"
    "$PYTHON_HEADERS_DIR"
)

INC_FLAGS=""
for d in "${INC_ROOTS[@]}"; do
    INC_FLAGS="$INC_FLAGS -I $d"
done

# Library paths
LIB_FLAGS="-L $MSYS2_DIR/lib -L $PYTHON_LIB_DIR"

# Link libraries
LIBS="-lgirepository-2.0 -lgobject-2.0 -lglib-2.0 -lffi -lpython312"

# Compile flags
CFLAGS="-DPY_SSIZE_T_CLEAN -O2 -fno-strict-aliasing -fvisibility=hidden"
LDFLAGS="-shared -static-libgcc"

echo "       Compiling $(echo "$C_FILES" | wc -l) source files..."
x86_64-w64-mingw32-gcc $LDFLAGS $CFLAGS $INC_FLAGS $LIB_FLAGS \
    $C_FILES $LIBS \
    -o _gi.pyd 2>&1 | sed 's/^/       /'

if [ ! -f "_gi.pyd" ]; then
    echo "[ERROR] _gi.pyd was not produced. Check compiler output above."
    exit 1
fi
echo "       Produced: _gi.pyd ($(du -h _gi.pyd | cut -f1))"
cd "$REPO_ROOT"

# ---- Package wheel ---------------------------------------------------------
echo ""
echo "[WHEEL] Packaging..."

PKG_DIR="$BUILD_DIR/pkg"
rm -rf "$PKG_DIR"
mkdir -p "$PKG_DIR/gi/repository" "$PKG_DIR/gi/overrides"

# Compiled extension
cp "$GI_SRC/_gi.pyd" "$PKG_DIR/gi/"

# Pure Python files from source
for f in "$PYGOBJECT_SRC/gi/"*.py; do
    cp "$f" "$PKG_DIR/gi/"
done
cp "$PYGOBJECT_SRC/gi/repository/"*.py "$PKG_DIR/gi/repository/"
cp "$PYGOBJECT_SRC/gi/overrides/"*.py "$PKG_DIR/gi/overrides/"

# Dist-info
DIST_INFO="PyGObject-${PYGOBJECT_VERSION}.dist-info"
mkdir -p "$PKG_DIR/$DIST_INFO"

cat > "$PKG_DIR/$DIST_INFO/METADATA" << META
Metadata-Version: 2.1
Name: PyGObject
Version: ${PYGOBJECT_VERSION}
Summary: Python bindings for GObject Introspection (Windows/MinGW cross-compile)
Requires-Python: >=3.8
META

cat > "$PKG_DIR/$DIST_INFO/WHEEL" << WHEEL
Wheel-Version: 1.0
Generator: windows/build_pygobject.sh
Root-Is-Purelib: false
Tag: cp312-cp312-win_amd64
WHEEL

# RECORD - list all files
cd "$PKG_DIR"
find . -type f | sed 's|^\./||' | while read -r f; do
    echo "$f,,"
done > "$DIST_INFO/RECORD"
cd "$REPO_ROOT"

# Build the wheel
WHEEL_OUTPUT="$OUTPUT_DIR/$WHEEL_FILENAME"
rm -f "$WHEEL_OUTPUT"
(cd "$PKG_DIR" && zip -r "$WHEEL_OUTPUT" . -x ".*" -x "*/.*")

echo ""
echo "============================================================"
echo "  SUCCESS"
echo "  Wheel: $WHEEL_OUTPUT"
echo "  Size:  $(du -h "$WHEEL_OUTPUT" | cut -f1)"
echo ""
echo "  Install in the Wine Python environment before PyInstaller:"
echo "    \$WIN_PIP install $WHEEL_OUTPUT"
echo "============================================================"
