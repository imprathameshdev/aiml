#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Download and bundle the GStreamer MinGW runtime for Windows.
#
# Produces a ``gstreamer/`` directory tree in the repo root with the layout
# expected by ``backend/_windows_env.py``::
#
#   gstreamer/
#     bin/                    # DLLs
#     lib/
#       gstreamer-1.0/        # plugin .dlls
#       girepository-1.0/     # .typelib files
#
# Prerequisites:
#   sudo apt install curl zstd
#
# Usage:
#   ./windows/build_gstreamer.sh                         # Full download
#   ./windows/build_gstreamer.sh --skip-downloads        # Reuse cached pkgs
# ---------------------------------------------------------------------------
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BUILD_DIR="${BUILD_DIR:-$SCRIPT_DIR/build/gstreamer}"
OUTPUT_DIR="${OUTPUT_DIR:-$REPO_ROOT/gstreamer}"
SKIP_DOWNLOADS=false

for arg in "$@"; do
    case "$arg" in
        --skip-downloads) SKIP_DOWNLOADS=true ;;
        *) echo "Unknown arg: $arg"; exit 1 ;;
    esac
done

mkdir -p "$BUILD_DIR/packages"
MSYS2_URL="https://repo.msys2.org/mingw/mingw64"

# ---- Package list ----------------------------------------------------------
# These packages provide the GStreamer runtime, plugin DLLs, typelibs,
# and all transitive DLL dependencies (GLib, libffi, zlib, libsoup, ...).
declare -A PKG_MAP
PKG_MAP=(
    # GStreamer core + plugins
    ["gstreamer"]="mingw-w64-x86_64-gstreamer-1.28.4-1-any.pkg.tar.zst"
    ["gst-plugins-base"]="mingw-w64-x86_64-gst-plugins-base-1.28.4-1-any.pkg.tar.zst"
    ["gst-plugins-good"]="mingw-w64-x86_64-gst-plugins-good-1.28.4-1-any.pkg.tar.zst"
    ["gst-plugins-bad-libs"]="mingw-w64-x86_64-gst-plugins-bad-libs-1.28.4-1-any.pkg.tar.zst"
    ["gst-plugins-bad"]="mingw-w64-x86_64-gst-plugins-bad-1.24.12-3-any.pkg.tar.zst"

    # GLib/GObject/GIO/GIRepository runtime (provides libgirepository-2.0-0.dll
    # needed by _gi.pyd, plus typelibs for GObject/GLib/Gio).
    ["glib2"]="mingw-w64-x86_64-glib2-2.88.1-1-any.pkg.tar.zst"

    # GLib transitive dependencies
    ["libffi"]="mingw-w64-x86_64-libffi-3.6.0-1-any.pkg.tar.zst"
    ["pcre2"]="mingw-w64-x86_64-pcre2-10.47-1-any.pkg.tar.zst"
    ["gettext"]="mingw-w64-x86_64-gettext-0.22.4-3-any.pkg.tar.zst"

    # MinGW runtime (libwinpthread DLL is in -libwinpthread-, not -winpthreads-)
    ["gcc-libs"]="mingw-w64-x86_64-gcc-libs-16.1.0-5-any.pkg.tar.zst"
    ["libwinpthread"]="mingw-w64-x86_64-libwinpthread-14.0.0.r98.g19f5121a2-1-any.pkg.tar.zst"

    # Compression (needed by zlib1.dll)
    ["zlib"]="mingw-w64-x86_64-zlib-ng-compat-2.3.3-2-any.pkg.tar.zst"

    # libiconv (needed by gettext, libintl, libidn2, libpsl, libunistring)
    ["libiconv"]="mingw-w64-x86_64-libiconv-1.19-1-any.pkg.tar.zst"

    # ORC runtime (needed by gstreamer audio/video plugins)
    ["orc"]="mingw-w64-x86_64-orc-0.4.42-1-any.pkg.tar.zst"

    # External codec libraries (needed by gst-plugins-bad plugins)
    ["openh264"]="mingw-w64-x86_64-openh264-2.6.0-1-any.pkg.tar.zst"
    ["libjpeg-turbo"]="mingw-w64-x86_64-libjpeg-turbo-3.1.4.1-3-any.pkg.tar.zst"
    ["libpng"]="mingw-w64-x86_64-libpng-1.6.58-1-any.pkg.tar.zst"
    ["libogg"]="mingw-w64-x86_64-libogg-1.3.6-1-any.pkg.tar.zst"
    ["opus"]="mingw-w64-x86_64-opus-1.5.2-1-any.pkg.tar.zst"
    ["libvorbis"]="mingw-w64-x86_64-libvorbis-1.3.7-2-any.pkg.tar.zst"
    ["libvpx"]="mingw-w64-x86_64-libvpx-1.16.0-1-any.pkg.tar.zst"
    ["libtheora"]="mingw-w64-x86_64-libtheora-1.2.0-1-any.pkg.tar.zst"

    # AV1 codec libraries (needed by libgstaom and libgstsvtav1).
    # ``libaom.dll`` and ``libSvtAv1Enc-3.dll`` are runtime DLLs that the
    # GStreamer plugins dynamically link against.  Without them the AV1
    # plugins fail to load with "missing a plug-in" even though the
    # plugin .dlls themselves are present.  The package versions are
    # pinned to match what the GStreamer plugins were built against:
    # the SVT-AV1 plugin's import table references libSvtAv1Enc-3.dll
    # exactly (not -2), so the older 2.x package won't satisfy the import.
    ["aom"]="mingw-w64-x86_64-aom-3.12.1-3-any.pkg.tar.zst"
    ["svt-av1"]="mingw-w64-x86_64-svt-av1-3.0.2-2-any.pkg.tar.zst"

    # bzip2 (needed by some plugins)
    ["bzip2"]="mingw-w64-x86_64-bzip2-1.0.8-3-any.pkg.tar.zst"

    # HTTP library (needed by souphttpsrc plugin)
    ["libsoup3"]="mingw-w64-x86_64-libsoup3-3.6.4-1-any.pkg.tar.zst"
    ["nghttp2"]="mingw-w64-x86_64-nghttp2-1.69.0-1-any.pkg.tar.zst"
    ["sqlite3"]="mingw-w64-x86_64-sqlite3-3.53.2-1-any.pkg.tar.zst"
    ["brotli"]="mingw-w64-x86_64-brotli-1.2.0-1-any.pkg.tar.zst"
    ["libpsl"]="mingw-w64-x86_64-libpsl-0.21.5-3-any.pkg.tar.zst"
    ["libidn2"]="mingw-w64-x86_64-libidn2-2.3.8-4-any.pkg.tar.zst"
    ["libunistring"]="mingw-w64-x86_64-libunistring-1.4.2-1-any.pkg.tar.zst"
)

PKG_NAMES=(
    gstreamer gst-plugins-base gst-plugins-good gst-plugins-bad-libs gst-plugins-bad
    glib2 libffi pcre2 gettext gcc-libs libwinpthread zlib
    libiconv orc bzip2
    libsoup3 nghttp2 sqlite3 brotli libpsl libidn2 libunistring
    openh264 libjpeg-turbo libpng libogg opus libvorbis libvpx libtheora
    aom svt-av1
)

# ---- Check prerequisites ---------------------------------------------------
echo "[CHECK] Verifying tools..."
command -v curl >/dev/null 2>&1 || { echo "Missing: curl"; exit 1; }
command -v tar >/dev/null 2>&1 || { echo "Missing: tar"; exit 1; }
command -v zstd >/dev/null 2>&1 || { echo "Missing: zstd. Install: sudo apt install zstd"; exit 1; }
echo "       All tools found."

# ---- Download packages -----------------------------------------------------
STAGING_DIR="$BUILD_DIR/staging"
if [ "$SKIP_DOWNLOADS" = true ] && [ -d "$STAGING_DIR/bin" ]; then
    echo "[GSTREAMER] Using existing downloads (--skip-downloads)"
elif [ -d "$STAGING_DIR/bin" ]; then
    echo "[GSTREAMER] Already downloaded; remove $STAGING_DIR to re-download"
else
    echo "[GSTREAMER] Downloading MSYS2 packages..."
    for name in "${PKG_NAMES[@]}"; do
        pkg="${PKG_MAP[$name]}"
        pkg_path="$BUILD_DIR/packages/$pkg"
        if [ ! -f "$pkg_path" ]; then
            printf "  %-20s %s\n" "$name" "downloading..."
            curl -fsSLo "$pkg_path" "$MSYS2_URL/$pkg"
        else
            printf "  %-20s %s\n" "$name" "cached"
        fi
    done

    echo "  Extracting..."
    mkdir -p "$STAGING_DIR"
    for pkg in "$BUILD_DIR/packages"/*.pkg.tar.zst; do
        [ -f "$pkg" ] || continue
        tar -I zstd -xf "$pkg" -C "$STAGING_DIR" 2>/dev/null || true
    done

    # MSYS2 packages extract under mingw64/; merge into staging root.
    if [ -d "$STAGING_DIR/mingw64" ]; then
        cp -r "$STAGING_DIR/mingw64/"* "$STAGING_DIR/"
        rm -rf "$STAGING_DIR/mingw64"
    fi

    # Strip unneeded artifacts (headers, static libs, pkgconfig, etc.)
    rm -rf "$STAGING_DIR/include" "$STAGING_DIR/share" "$STAGING_DIR/lib/pkgconfig"
    # Keep only .dll.a import libs? No, those aren't needed at runtime.
    find "$STAGING_DIR/lib" -name '*.dll.a' -delete
    find "$STAGING_DIR/lib" -name '*.a' -delete

    echo "  Extracted to $STAGING_DIR"
fi

# ---- Remove previous output and copy to final location ---------------------
echo "[GSTREAMER] Assembling output in $OUTPUT_DIR ..."
rm -rf "$OUTPUT_DIR"
mkdir -p "$OUTPUT_DIR/bin" "$OUTPUT_DIR/lib/gstreamer-1.0" "$OUTPUT_DIR/lib/girepository-1.0"

# DLLs from staging/bin/
echo "  Copying DLLs..."
if [ -d "$STAGING_DIR/bin" ]; then
    cp "$STAGING_DIR/bin/"*.dll "$OUTPUT_DIR/bin/" 2>/dev/null || true
fi

# GStreamer plugin DLLs
echo "  Copying plugins..."
if [ -d "$STAGING_DIR/lib/gstreamer-1.0" ]; then
    cp "$STAGING_DIR/lib/gstreamer-1.0/"*.dll "$OUTPUT_DIR/lib/gstreamer-1.0/" 2>/dev/null || true
fi

# Typelib files
echo "  Copying typelibs..."
if [ -d "$STAGING_DIR/lib/girepository-1.0" ]; then
    cp "$STAGING_DIR/lib/girepository-1.0/"*.typelib "$OUTPUT_DIR/lib/girepository-1.0/" 2>/dev/null || true
fi

# ---- Verify DLL dependencies -----------------------------------------------
echo ""
echo "[VERIFY] Checking DLL dependencies..."
MISSING=0
TOTAL=0

verify_dll() {
    local dll="$1"
    local name
    name=$(basename "$dll")
    TOTAL=$((TOTAL + 1))
    local deps
    deps=$(x86_64-w64-mingw32-objdump -p "$dll" 2>/dev/null | grep "DLL Name" | awk '{print $3}')
    local missing=""
    while IFS= read -r dep; do
        [ -z "$dep" ] && continue
        # Skip Windows system DLLs
        case "$dep" in
            KERNEL32.dll|msvcrt.dll|ADVAPI32.dll|ole32.dll|SHELL32.dll|USER32.dll|WS2_32.dll|OLEAUT32.dll|GDI32.dll|COMDLG32.dll|IMM32.dll|SETUPAPI.dll|WINMM.dll|WTSAPI32.dll|UIAutomationCore.dll|d3d9.dll|dwrite.dll|SHLWAPI.dll|CRYPT32.dll|WLDAP32.dll|WININET.dll|IPHLPAPI.dll|RASAPI32.dll|WINTRUST.dll|Secur32.dll|NETAPI32.dll)
                continue ;;
        esac
        if [ ! -f "$OUTPUT_DIR/bin/$dep" ]; then
            missing="$missing $dep"
        fi
    done <<< "$deps"
    if [ -n "$missing" ]; then
        echo "  MISSING: $name needs:$missing"
        MISSING=$((MISSING + 1))
    fi
}

# Check DLLs in bin/
for dll in "$OUTPUT_DIR/bin/"*.dll; do
    [ -f "$dll" ] || continue
    verify_dll "$dll"
done

# Check plugin DLLs in lib/gstreamer-1.0/
for dll in "$OUTPUT_DIR/lib/gstreamer-1.0/"*.dll; do
    [ -f "$dll" ] || continue
    verify_dll "$dll"
done

echo ""
echo "  DLLs checked: $TOTAL"
echo "  DLLs with missing deps: $MISSING"

if [ "$MISSING" -gt 0 ]; then
    echo ""
    echo "[WARNING] Some DLLs have missing dependencies."
    echo "  The app may still start if those plugins are not loaded at runtime."
    echo "  To fix, add the missing packages to PKG_MAP and re-run."
fi

# ---- Summary ---------------------------------------------------------------
DLL_COUNT=$(find "$OUTPUT_DIR/bin" -name '*.dll' | wc -l)
PLUGIN_COUNT=$(find "$OUTPUT_DIR/lib/gstreamer-1.0" -name '*.dll' | wc -l)
TYPELIB_COUNT=$(find "$OUTPUT_DIR/lib/girepository-1.0" -name '*.typelib' | wc -l)
TOTAL_SIZE=$(du -sh "$OUTPUT_DIR" | cut -f1)

echo ""
echo "============================================================"
echo "  GStreamer Runtime Bundle Complete"
echo "  Location: $OUTPUT_DIR"
echo "  DLLs:      $DLL_COUNT files ($TOTAL_SIZE)"
echo "  Plugins:   $PLUGIN_COUNT"
echo "  Typelibs:  $TYPELIB_COUNT"
echo "============================================================"
