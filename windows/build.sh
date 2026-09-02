#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Build the LotVulture Windows installer from Linux using:
#   - Wine           – run Windows PyInstaller + Inno Setup
#   - MinGW-w64      – cross-compile vulturevision .pyd
#   - CMake          – orchestrate the C++ build
#
# This script is designed to run on a Linux build machine (Ubuntu 22.04+).
# No Windows installation is required.
#
# Prerequisites:
#   sudo apt install wine wine-binfmt x86_64-w64-mingw32 cmake build-essential
#   sudo dpkg --add-architecture i386 && sudo apt update  # (for Wine WoW64)
#
# Usage:
#   ./windows/build.sh              # Full build
#   ./windows/build.sh --quick      # Skip vulturevision, use existing .pyd
#   ./windows/build.sh --installer-only  # Rebuild only the .exe installer
#
# Set OUTPUT_DIR to change where the final installer is written (default: ./dist/)
# ---------------------------------------------------------------------------
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

# ---- Config ----------------------------------------------------------------
OUTPUT_DIR="${OUTPUT_DIR:-$REPO_ROOT/dist}"
BUILD_DIR="$REPO_ROOT/build/windows"
WINE_PREFIX="${WINE_PREFIX:-$BUILD_DIR/wine}"
PYTHON_VERSION="3.12.9"
GSTREAMER_DIR="${GSTREAMER_DIR:-$REPO_ROOT/gstreamer}"
ONNXRUNTIME_VERSION="1.19.2"
NSSM_VERSION="2.24"
NSSM_URL="https://nssm.cc/ci/nssm-${NSSM_VERSION}.zip"
SKIP_VULTUREVISION=false
ACTION="full"

# Simple argument parsing
for arg in "$@"; do
    case "$arg" in
        --skip-vulturevision) SKIP_VULTUREVISION=true ;;
        --quick|--installer-only) ACTION="${arg#--}" ;;
        --full) ACTION="full" ;;
    esac
done

mkdir -p "$BUILD_DIR" "$OUTPUT_DIR"

# ---- Dependency checks -----------------------------------------------------
echo "[CHECK] Verifying build tools..."
command -v wine >/dev/null 2>&1 || { echo "Missing: wine. Install: sudo apt install wine wine-binfmt"; exit 1; }
command -v x86_64-w64-mingw32-g++ >/dev/null 2>&1 || { echo "Missing: MinGW-w64. Install: sudo apt install g++-mingw-w64-x86-64"; exit 1; }
command -v cmake >/dev/null 2>&1 || { echo "Missing: cmake. Install: sudo apt install cmake"; exit 1; }
command -v curl >/dev/null 2>&1 || { echo "Missing: curl"; exit 1; }
command -v unzip >/dev/null 2>&1 || { echo "Missing: unzip"; exit 1; }
command -v npm >/dev/null 2>&1 || { echo "Missing: npm (Node.js) - needed to build the frontend"; exit 1; }
echo "       All tools found."

# ---- Demovideo assets check/download ---------------------------------------
echo "[CHECK] Verifying demovideo assets..."
mkdir -p "$REPO_ROOT/demovideo"
VIDEO_FILE="$REPO_ROOT/demovideo/initial_parking_lot_demo.mp4"
SPACES_FILE="$REPO_ROOT/demovideo/initial_parking_lot_demo_spaces.json"

if [ ! -f "$VIDEO_FILE" ]; then
    echo "       Downloading initial_parking_lot_demo.mp4..."
    curl -fsSLo "$VIDEO_FILE" "http://download.lotvulture.com/demovideo/initial_parking_lot_demo.mp4" || {
        echo "Error: Failed to download initial_parking_lot_demo.mp4"
        exit 1
    }
fi

if [ ! -f "$SPACES_FILE" ]; then
    echo "       Downloading initial_parking_lot_demo_spaces.json..."
    curl -fsSLo "$SPACES_FILE" "http://download.lotvulture.com/demovideo/initial_parking_lot_demo_spaces.json" || {
        echo "Error: Failed to download initial_parking_lot_demo_spaces.json"
        exit 1
    }
fi

if [ ! -f "$VIDEO_FILE" ]; then
    echo "Error: demovideo/initial_parking_lot_demo.mp4 is missing!"
    exit 1
fi

if [ ! -f "$SPACES_FILE" ]; then
    echo "Error: demovideo/initial_parking_lot_demo_spaces.json is missing!"
    exit 1
fi
echo "       Demovideo assets verified."

# ---- Wine prefix setup -----------------------------------------------------
export WINEPREFIX="$WINE_PREFIX"
export WINEDEBUG=-all
export WINEARCH=win64

# Guard: skip Python install if it's already there.  We check for
# `python.exe` in the per-user install location (not ``system32``) —
# the official Python 3.12 installer for Windows does NOT copy
# ``python3.dll`` into ``system32``; the previous guard checked
# ``system32/python3.dll`` and re-ran the installer on every build,
# which intermittently fails under Wine with
# ``Fatal Python error: init_sys_streams: ... OSError: [WinError 6]
# Invalid handle`` (the installer tries to spawn a child process
# that can't inherit a broken Wine handle).
if [ ! -f "$WINE_PREFIX/drive_c/Program Files/Python312/python.exe" ]; then
    echo "[WINE] Setting up Wine prefix..."
    wineboot -u 2>/dev/null || true
    wineserver -w 2>/dev/null || true

    echo "[WINE] Installing Python $PYTHON_VERSION for Windows..."
    PYTHON_INSTALLER="$BUILD_DIR/python-${PYTHON_VERSION}-amd64.exe"
    if [ ! -f "$PYTHON_INSTALLER" ]; then
        curl -fsSLo "$PYTHON_INSTALLER" \
            "https://www.python.org/ftp/python/${PYTHON_VERSION}/python-${PYTHON_VERSION}-amd64.exe"
    fi
    chmod +x "$PYTHON_INSTALLER" 2>/dev/null || true
    wine "$PYTHON_INSTALLER" /quiet InstallAllUsers=1 PrependPath=1 Include_test=0 || true
    wineserver -w 2>/dev/null || true
    
    # Fix: ensure pip is available
    wine python -m ensurepip || true
    wineserver -w 2>/dev/null || true
    wine python -m pip install --upgrade pip || true
    wineserver -w 2>/dev/null || true
fi

# Ensure native UCRT is installed (fixes crealf and other missing CRT functions in numpy)
if command -v winetricks >/dev/null 2>&1; then
    echo "[WINE] Installing native UCRT (fixes crealf and other missing CRT functions)..."
    winetricks -q ucrtbase2019 2>/dev/null || winetricks -q ucrtbase 2>/dev/null || true
    wineserver -w 2>/dev/null || true
fi

# Wrap Wine Python in a PTY so it always has a pseudo-tty attached
# to its stdio.  Wine's MSVCRT stdio init has a long-standing bug
# where, on first invocation after a wineserver restart, stdout
# redirected to a non-terminal file (e.g. ``> build.log``) fails
# with "Fatal Python error: init_sys_streams: ... OSError:
# [WinError 6] Invalid handle".  The PTY makes MSVCRT think it
# has a real terminal, which sidesteps the bug.
#
# We use a small Python helper ``wine_pty.py`` for this rather
# than the Linux ``script -qefc`` command, because Wine's
# ``start.exe`` indirection (used for ``wine python -m
# PyInstaller`` and similar) prevents ``script`` from waiting
# cleanly — the pty never gets an EOF, and ``script`` hangs
# forever.  Our helper only waits for the DIRECT child (``wine``
# itself), not the grandchild (start.exe + actual Python), so
# it returns as soon as Wine has finished spawning the real
# Python process.
#
# This whole build script is Linux-only (the rest of the build
# runs ``wine`` commands), so the Python helper is always
# available.
wine_python() {
    if [ -t 1 ]; then
        wine python "$@"
        return
    fi
    # Wine 9.0 has a long-standing MSVCRT stdio init bug:
    # when stdout is redirected to a non-terminal file
    # (e.g. ``> build.log``), the first ``wine python``
    # invocation after a wineserver restart fails with
    #     Fatal Python error: init_sys_streams:
    #         OSError: [WinError 6] Invalid handle
    # The workaround is to attach a pseudo-tty to Wine
    # Python's stdio, so MSVCRT thinks it has a real
    # terminal.  ``script -qfc`` does this.
    #
    # We DON'T use ``-e`` (``--return``) because that
    # makes ``script`` return the exit code of its
    # DIRECT child (``wine``), which is non-zero when
    # Wine's MSVCRT reports ``__std_terminate`` lookup
    # warnings even when the wrapped command actually
    # succeeded.  Without ``-e``, ``script`` returns
    # its own exit code (always 0 unless ``script``
    # itself fails), and the wrapped command's output
    # is captured to stdout for the build log.  We
    # rely on the build's own logging (PyInstaller
    # prints "completed successfully" etc.) to
    # determine success.
    local cmd
    printf -v cmd '%q ' "$@"
    script -qfc "wine python ${cmd}" /dev/null
}
# Pre-define WIN_PYTHON/WIN_PIP for the few places that use them
# as variables (e.g. conditionals), but always invoke via the
# function for stdio safety.
WIN_PYTHON="wine python"
WIN_PIP="wine python -m pip"

# ---- Install Python dependencies under Wine --------------------------------
echo "[PIP] Installing Python packages (Windows target)..."
# Exclude PyGObject (provided by GStreamer runtime) and GPU onnxruntime
wine_python -m pip install pyinstaller
wine_python -m pip install -r requirements.txt

# Remove packages that conflict on Windows
wine_python -m pip uninstall -y PyGObject onnxruntime-gpu 2>/dev/null || true
wine_python -m pip install onnxruntime-directml  # DirectML GPU support

# ---- vulturevision wheel ----------------------------------------------------
# The vulturevision C++ extension must be cross-compiled separately.
echo "[VULTUREVISION] Looking for pre-built wheel..."
mkdir -p "$BUILD_DIR/wheels"

# Try to find or build the wheel
VV_WHEEL=$(ls "$BUILD_DIR"/wheels/vulturevision-*-win_amd64.whl 2>/dev/null | head -1 || true)

# If no wheel found, try auto-copying from vulturevision repo
if [ -z "$VV_WHEEL" ]; then
    VV_DIST="$REPO_ROOT/vulturevision/dist"
    if [ -d "$VV_DIST" ]; then
        auto_wheel=$(ls "$VV_DIST"/vulturevision-*-win_amd64.whl 2>/dev/null | head -1 || true)
        if [ -n "$auto_wheel" ]; then
            echo "       Found in vulturevision/dist/, copying..."
            cp "$auto_wheel" "$BUILD_DIR/wheels/"
            VV_WHEEL="$BUILD_DIR/wheels/$(basename "$auto_wheel")"
        fi
    fi
fi

# If still not found, download from download.lotvulture.com
if [ -z "$VV_WHEEL" ]; then
    if [ "$SKIP_VULTUREVISION" = true ]; then
        echo "       WARNING: --skip-vulturevision: no wheel, PyInstaller bundle will lack _vulturevision.pyd"
    else
        echo "       Downloading vulturevision Windows wheel from download.lotvulture.com..."
        if curl -fsSLo "$BUILD_DIR/wheels/vulturevision-1.0.0-cp312-cp312-win_amd64.whl" \
            "http://download.lotvulture.com/dist/vulturevision/vulturevision-1.0.0-cp312-cp312-win_amd64.whl"; then
            VV_WHEEL="$BUILD_DIR/wheels/vulturevision-1.0.0-cp312-cp312-win_amd64.whl"
        else
            echo "       ERROR: Failed to download vulturevision wheel from server."
            exit 1
        fi
    fi
fi

# Install the wheel if found
if [ -f "$VV_WHEEL" ]; then
    echo "       Installing: $(basename "$VV_WHEEL")"
    wine_python -m pip install "$VV_WHEEL"
fi

# ---- GStreamer runtime ----------------------------------------------------
# Build/download the GStreamer MinGW runtime bundle
echo "[GSTREAMER] Building MinGW runtime bundle..."
"$SCRIPT_DIR/build_gstreamer.sh" --skip-downloads 2>/dev/null || "$SCRIPT_DIR/build_gstreamer.sh"
GST_DIR="$GSTREAMER_DIR"
echo "       GStreamer runtime at: $GST_DIR"

# ---- PyGObject wheel -------------------------------------------------------
# Cross-compile the _gi.pyd extension for Windows and build a wheel
echo "[PYGOBJECT] Cross-compiling PyGObject for Windows..."
"$SCRIPT_DIR/build_pygobject.sh" --skip-downloads 2>/dev/null || "$SCRIPT_DIR/build_pygobject.sh"
echo "       Installing PyGObject wheel under Wine..."
PYGOBJECT_WHEEL=$(ls "$REPO_ROOT/dist"/PyGObject-*-cp312-cp312-win_amd64.whl 2>/dev/null | head -1)
if [ -n "$PYGOBJECT_WHEEL" ]; then
    wine_python -m pip install "$PYGOBJECT_WHEEL"
fi

# Patch MinGW (null) DLL names in site-packages .pyd files
echo "[PATCH] Fixing null DLL names in Wine site-packages .pyd files..."
for pyd in \
    "$WINE_PREFIX/drive_c/Program Files/Python312/Lib/site-packages/gi/_gi.pyd" \
    "$WINE_PREFIX/drive_c/Program Files/Python312/Lib/site-packages/vulturevision/_vulturevision.pyd"; do
    if [ -f "$pyd" ]; then
        python3 "$SCRIPT_DIR/patch_pyd.py" "$pyd"
    fi
done

# ---- NSSM (service wrapper) -----------------------------------------------
NSSM_DIR="$BUILD_DIR/nssm"
if [ ! -f "$NSSM_DIR/nssm.exe" ]; then
    echo "[NSSM] Downloading (with retry)..."
    for i in 1 2 3; do
        curl -fsSLo "$BUILD_DIR/nssm.zip" "$NSSM_URL" && break
        if [ "$i" -lt 3 ]; then echo "       Retry $i/3..."; sleep 3; fi
    done
    mkdir -p "$NSSM_DIR"
    unzip -qo "$BUILD_DIR/nssm.zip" -d "$NSSM_DIR"
    cp "$NSSM_DIR/nssm-${NSSM_VERSION}/win64/nssm.exe" "$NSSM_DIR/"
    rm -rf "$NSSM_DIR/nssm-${NSSM_VERSION}" "$BUILD_DIR/nssm.zip"
fi

# ---- PyInstaller ----------------------------------------------------------
PYINST_DIR="$BUILD_DIR/pyinstaller"
if [ "$ACTION" = "installer-only" ]; then
    echo "[PYINSTALLER] Skipping (--installer-only)"
else
    echo "[PYINSTALLER] Running PyInstaller under Wine..."
    echo "       (This takes 2-5 minutes — not frozen)"
    
    # Add GStreamer DLLs to Wine's PATH so _gi.pyd can load during analysis
    export PATH="$GSTREAMER_DIR/bin:$PATH"

    # Build frontend first
    echo "       Building Vue frontend..."
    if [ -d frontend/node_modules ]; then
        (cd frontend && npm run build)
    else
        (cd frontend && npm ci && npm run build)
    fi
    
    # Clean previous
    rm -rf "$PYINST_DIR" "$BUILD_DIR/dist"
    mkdir -p "$PYINST_DIR"
    
# Create a .spec for PyInstaller
    # (PyInstaller is run under Wine, paths are Windows-style)
    WIN_REPO="Z:$(echo "$REPO_ROOT" | sed 's|/|\\|g')"
    WIN_GST="Z:$(echo "$GST_DIR" | sed 's|/|\\|g')"
    WIN_VC="Z:$(echo "$BUILD_DIR" | sed 's|/|\\|g')\\vc_runtime"

    cat > "$BUILD_DIR/lotvulture.spec" << SPECEOF
# -*- mode: python ; coding: utf-8 -*-
import sys
import os
from pathlib import Path

block_cipher = None

# GStreamer runtime bundle — allowlist approach.
#
# Only include plugins needed for RTSP H264 streaming.  Many GStreamer
# plugins partially load under headless Windows and corrupt the GLib type
# system (classic symptom: "g_once_init_leave_pointer: assertion 'result
# != 0' failed"), so we explicitly allow only the known-safe subset.
#
# NOTE: This is GStreamer 1.24 MinGW from MSYS2.  Some commonly known
# plugins were renamed or merged (e.g. videoconvert+videoscale →
# videoconvertscale; libav → openh264; sdp → sdpelem; decodebin lives
# inside playback.dll).
_ALLOWED_PLUGINS = frozenset({
    # ---- Core (always required) ----
    "libgstcoreelements.dll",      # queue, capsfilter, fakesink, multiqueue, …
    "libgstapp.dll",               # appsink, appsrc
    "libgstcoretracers.dll",       # tracing/logging (harmless)

    # ---- RTSP transport ----
    "libgstrtsp.dll",              # rtspsrc
    "libgstudp.dll",               # udpsrc, udpsink
    "libgstsdpelem.dll",           # sdpdemux, sdpdec (1.24: libgstsdp → sdpelem)

    # ---- RTP depayload / jitter-buffer ----
    "libgstrtp.dll",               # rtph264depay, rtph264pay, …
    "libgstrtpmanager.dll",        # rtpbin, rtpjitterbuffer, rtpsession

    # ---- Auto-decoding ----
    "libgsttypefindfunctions.dll", # typefind
    "libgstvideoparsersbad.dll",   # h264parse
    "libgstopenh264.dll",          # openh264dec (software h264 decoder;
                                   # libav/ffmpeg plugin not in this bundle)
    "libgstplayback.dll",          # decodebin, uridecodebin, playbin
    "libgstautoconvert.dll",       # autovideoconvert

    # ---- Video processing (1.24: videoconvert+videoscale merged) ----
    "libgstvideoconvertscale.dll", # combined videoconvert + videoscale
    "libgstvideorate.dll",
    "libgstvideobox.dll",
    "libgstvideocrop.dll",
    "libgstvideofilter.dll",
    "libgstdeinterlace.dll",
    "libgstimagefreeze.dll",
    "libgstrawparse.dll",
    "libgstlegacyrawparse.dll",
    "libgstvideotestsrc.dll",

    # ---- Common container formats (decodebin may probe these) ----
    "libgstisomp4.dll",            # qtdemux (MP4)
    "libgstmatroska.dll",          # matroskademux
    "libgstflv.dll",               # flvdemux
    "libgstmpegtsdemux.dll",
    "libgstmpegpsdemux.dll",
    "libgstavi.dll",
    "libgstogg.dll",

    # ---- Decoders ----
    "libgstjpeg.dll",              # jpegdec (MJPEG cameras)
    "libgstpng.dll",               # pngdec
    "libgstvpx.dll",               # vp9dec
     "libgsttheora.dll",            # theoradec
     "libgstaom.dll",               # av1dec (AOM AV1; software, slow but always available)
     "libgstsvtav1.dll",            # av1dec (SVT-AV1; hardware-accelerated when supported)
     # libgstclosedcaption.dll intentionally EXCLUDED: it needs libcairo-2.dll
     # and libpango-1.0-0.dll which aren't bundled.  Loading the plugin
     # fails with "module not found" and floods the log on every
     # GStreamer init.

    # ---- Audio (decodebin may auto-plug for RTSP with audio tracks) ----
    "libgstaudioconvert.dll",
    "libgstaudioresample.dll",
    "libgstaudiorate.dll",
    "libgstmulaw.dll",
    "libgstalaw.dll",
    "libgstopus.dll",
    "libgstvorbis.dll",

    # ---- Helpers / utilities ----
    "libgstproxy.dll",
    "libgstpbtypes.dll",
    "libgstsubparse.dll",
    "libgsttimecode.dll",
    "libgstoverlaycomposition.dll",
    "libgsttcp.dll",
    "libgstsoup.dll",
    "libgstgio.dll",
    "libgstmultifile.dll",
    "libgstvolume.dll",
    "libgstinsertbin.dll",
     "libgstaudiotestsrc.dll",
     # libgstclosedcaption.dll intentionally EXCLUDED: it needs
     # libcairo-2.dll + libpango-1.0-0.dll which aren't bundled.
     # Loading the plugin fails with "module not found" and floods
     # the log on every GStreamer init.
 })

gst_root = Path(r"$WIN_GST")
gst_data = []
if gst_root.is_dir():
    for p in gst_root.rglob("*"):
        if p.is_file():
            # Only filter plugin .dlls; include all other files (bin/*.dll,
            # lib/girepository-1.0/*.typelib, etc.) unconditionally.
            if p.parent.name == "gstreamer-1.0" and p.name not in _ALLOWED_PLUGINS:
                continue
            rel = p.relative_to(gst_root.parent)
            gst_data.append((str(p), str(rel.parent)))

# VC++ 2015-2022 runtime DLLs needed by onnxruntime.dll
vc_root = Path(r"$WIN_VC")
vc_binaries = []
if vc_root.is_dir():
    for dll in ["msvcp140.dll", "msvcp140_1.dll", "vcruntime140.dll", "vcruntime140_1.dll"]:
        p = vc_root / dll
        if p.is_file():
            vc_binaries.append((str(p), "vulturevision"))

# vulturevision model files (.enc / .encs)
import glob as _glob
vv_models_src = r"C:\Program Files\Python312\Lib\site-packages\vulturevision\models"
vv_data = [(f, "vulturevision/models") for f in _glob.glob(vv_models_src + "/*.enc*")]

# vulturevision binary — use find_spec instead of import to avoid
# loading _vulturevision.pyd under Wine (which would crash).
import importlib.util
_vv_spec = importlib.util.find_spec("vulturevision")
_vv_pkg_dir = os.path.dirname(_vv_spec.origin) if _vv_spec else None
if _vv_pkg_dir:
    _vv_pyd = os.path.join(_vv_pkg_dir, "_vulturevision.pyd")
    vv_data += [(os.path.join(_vv_pkg_dir, f), "vulturevision") for f in
                ["onnxruntime.dll", "onnxruntime_providers_shared.dll"]
                if os.path.isfile(os.path.join(_vv_pkg_dir, f))]
else:
    _vv_pyd = None

# ``backend._windows_env`` is imported only on win32 in main.py.  Even
# with the unconditional ``from backend import _windows_env`` fallback
# we added, PyInstaller's static analysis can still miss the module's
# bytecode (the import is a no-op on Linux, and PyInstaller may skip
# compiling modules whose code path is never taken on the build host).
# Belt-and-braces: pre-compile to a .pyc and include it as a data file.
# The bundle's runtime Python finds it via ``backend.__pycache__`` on
# the frozen importer's path.
import py_compile as _py_compile
_we_pyc_src = r"$WIN_REPO\backend\_windows_env.py"
_we_pyc_out = os.path.join("/tmp", "_windows_env.cpython-312.pyc")
_py_compile.compile(_we_pyc_src, _we_pyc_out, doraise=True)
_we_data = [(_we_pyc_out, "backend/__pycache__")]

a = Analysis(
    [r"$WIN_REPO\\main.py"],
    pathex=[r"$WIN_REPO"],
    binaries=[(_vv_pyd, "vulturevision"), *vc_binaries],
    datas=[
        (r"$WIN_REPO\\frontend\\dist", "frontend/dist"),
        *gst_data,
        *vv_data,
        *_we_data
    ] + ([(r"$WIN_REPO\\demovideo", "demovideo")] if os.path.isdir(r"$WIN_REPO\\demovideo") else []),
    hiddenimports=[
        "backend._windows_env",
        "backend.app", "backend.database", "backend.scheduler",
        "backend.inference", "backend.stream_manager", "backend.maintenance",
        "backend.auth", "backend.models", "backend.config", "backend.logging_config",
        "backend.cli", "backend.billing", "backend.billing_routes",
        "pydantic", "sqlalchemy", "lmdb", "loguru", "cv2", "PIL",
        "yt_dlp", "requests",
        "opentelemetry", "opentelemetry.sdk",
        "opentelemetry.exporter.otlp.proto.grpc",
        "opentelemetry.instrumentation.fastapi",
        "opentelemetry.instrumentation.sqlalchemy",
        "websockets", "wsproto",
        "numpy",
    ],
    hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=[
        # Note: do NOT add ``distutils`` here.  Python 3.12 removed
        # ``distutils`` from the stdlib, and PyInstaller's built-in
        # ``hook-distutils.py`` (in ``pre_safe_import_module``)
        # aliases ``distutils`` to the setuptools-vendored copy.
        # Excluding ``distutils`` makes it an ExcludedModule, and
        # the alias hook then fails with:
        #   ValueError: Target module "distutils" already imported
        #   as "ExcludedModule('distutils',)".
        # Some packages (e.g. older ``packaging`` releases) still
        # ``import distutils`` at module top-level, so the alias
        # is required at runtime.
        "tkinter", "test", "unittest", "setuptools", "pip",
        "PyQt5", "PyQt6", "PySide2", "PySide6",
    ],
    win_no_prefer_redirects=False, win_private_assemblies=False,
    cipher=block_cipher, noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(pyz, a.scripts, a.binaries, a.zipfiles, a.datas, [],
    name="lotvulture", debug=False, bootloader_ignore_signals=False,
    strip=False, upx=True, console=True, disable_windowed_traceback=False,
    exclude_binaries=True, icon=r"$WIN_REPO\\windows\\lotvulture.ico")
coll = COLLECT(exe, a.binaries, a.zipfiles, a.datas,
    strip=False, upx=True, name="lotvulture")
SPECEOF

    # Kill leftover Wine processes from previous runs (avoids hangs)
    wineserver -k 2>/dev/null || true
    sleep 1

    # Quick sanity check that Wine Python is responsive
    if ! wine_python -c "print('ok')" 2>/dev/null; then
        echo "       Wine Python not responding, re-initializing prefix..."
        wineboot -u 2>/dev/null || true
        sleep 2
    fi

    cd "$BUILD_DIR"
    wine_python -m PyInstaller \
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
    
    echo "       PyInstaller bundle at: $BUILD_DIR/dist/lotvulture.exe (--onefile)"
fi

# ---- Inno Setup installer --------------------------------------------------
echo "[INNO] Building installer with Inno Setup under Wine..."

# Install Inno Setup under Wine if not present
if [ ! -f "$WINE_PREFIX/drive_c/Program Files (x86)/Inno Setup 6/ISCC.exe" ]; then
    echo "       Installing Inno Setup 6 under Wine..."
    IS_INSTALLER="$BUILD_DIR/innosetup-6.7.3.exe"
    if [ ! -f "$IS_INSTALLER" ]; then
        curl -fsSLo "$IS_INSTALLER" \
            "https://github.com/jrsoftware/issrc/releases/download/is-6_7_3/innosetup-6.7.3.exe" || \
        curl -fsSLo "$IS_INSTALLER" \
            "https://files.jrsoftware.org/is/6/innosetup-6.7.3.exe"
    fi
    chmod +x "$IS_INSTALLER" 2>/dev/null || true
    wine "$IS_INSTALLER" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /DIR="C:\\Program Files (x86)\\Inno Setup 6" || true
    wineserver -w 2>/dev/null || true
fi

# Copy NSSM where the .iss expects it
cp "$NSSM_DIR/nssm.exe" "$SCRIPT_DIR/"

# Compile the .iss from the script directory using a Windows-style path.
# Wine converts Linux absolute paths to Z:\... which ISCC interprets
# as a command-line option (treating Z: as a switch).  Using a relative
# path via cd avoids this.
APP_VER="${APP_VERSION:-${VULTURE_VERSION:-$(git describe --tags --always 2>/dev/null || echo v-dev)}}"
ISCC="$WINE_PREFIX/drive_c/Program Files (x86)/Inno Setup 6/ISCC.exe"
(cd "$SCRIPT_DIR" && wine "$ISCC" "/DMyAppVersion=$APP_VER" installer.iss)
wait

# Move the result
INSTALLER=$(ls "$BUILD_DIR"/LotVulture_Setup_*.exe 2>/dev/null | head -1)
if [ -n "$INSTALLER" ]; then
    mv "$INSTALLER" "$OUTPUT_DIR/"
    INSTALLER="$OUTPUT_DIR/$(basename "$INSTALLER")"

    # ---- Code signing (optional) -------------------------------------------
    echo "[SIGN] Attempting code signing..."
    "$SCRIPT_DIR/sign.sh" "$INSTALLER"

    echo ""
    echo "============================================================"
    echo "  SUCCESS: Installer at: $INSTALLER"
    echo "============================================================"
else
    echo "[INNO] Installer output not found — check $BUILD_DIR/"
fi
