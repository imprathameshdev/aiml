# Windows Packaging Guide

## Prerequisites (Build Machine — Linux)

```bash
sudo apt install wine wine-binfmt x86_64-w64-mingw32 g++-mingw-w64-x86-64 \
  cmake build-essential curl unzip zstd perl npm
sudo dpkg --add-architecture i386 && sudo apt update  # for Wine WoW64
pip3 install pefile  # for .pyd import table patching
```

## Building

```bash
# Full build (everything from scratch)
./windows/build.sh

# Quick rebuild (reuse existing .pyd, just bundle)
./windows/build.sh --quick

# Just rebuild the installer (if bundle already exists)
./windows/build.sh --installer-only
```

The final installer is written to `./dist/LotVulture_Setup_<version>.exe`.

## How it works

Everything runs on the Linux build machine under Wine + MinGW cross-compilation:

| Step | Tool | What it does |
|---|---|---|
| 1. GStreamer runtime | `windows/build_gstreamer.sh` | Downloads 30 MSYS2 MinGW packages → `gstreamer/` (55 MB of DLLs, plugins, typelibs) |
| 2. PyGObject wheel | `windows/build_pygobject.sh` | Cross-compiles `_gi.pyd` from source → `dist/PyGObject-*.whl` |
| 3. vulturevision wheel | `vulturevision/build_windows.sh` | Cross-compiles `_vulturevision.pyd` with DirectML ONNX Runtime → `dist/vulturevision-*.whl` |
| 4. Python env | Wine + Python 3.12 | Installs Windows Python under Wine |
| 5. Python deps | pip (under Wine) | Installs requirements + wheels |
| 6. Patch .pyd files | `windows/patch_pyd.py` | Fixes MinGW `(null)` DLL name → `python312.dll` |
| 7. Frontend | npm build | Builds Vue SPA to `frontend/dist/` |
| 8. PyInstaller | Wine + PyInstaller | Bundles everything using allowlist .spec (54 of 228 GStreamer plugins) |
| 9. Inno Setup | Wine + ISCC | Creates `LotVulture_Setup.exe` with NSSM service |

## File layout of the PyInstaller bundle

The onedir bundle is at `build/windows/dist/lotvulture/`:

```
lotvulture.exe                   # Console entry point
_internal/
  backend/                       # Python backend
  frontend/dist/                 # Built Vue SPA
  gstreamer/
    bin/                         #   93 shared DLLs (GStreamer + codecs)
    lib/gstreamer-1.0/           #   54 allowed plugin DLLs
    lib/girepository-1.0/        #   typelib files
  vulturevision/
    _vulturevision.pyd           #   C++ inference engine
    onnxruntime.dll              #   DirectML-enabled ONNX Runtime
    onnxruntime_providers_shared.dll
  gi/
    _gi.pyd                     #   PyGObject introspection

C:\ProgramData\LotVulture\        # Persistent data
  storage_blobs.mdb/              #   LMDB blob store
  parkinglot.db                   #   SQLite database
  logs/                           #   Service logs
```

The service runs as `NT AUTHORITY\SYSTEM`, auto-starts on boot, and restarts on crash.

## GPU Inference

- **Windows:** DirectML (DirectX 12) — requires Windows 10 1903+ with a DX12-capable GPU
- **Linux:** CUDA (NVIDIA GPU)
- **Fallback:** CPU-only on all platforms when no GPU is available or `inference_device: cpu` is set

Set via Settings → Inference Device in the web UI, or the `inference_device` DB setting (`"cuda"` / `"cpu"`).

## Testing with Wine

Before deploying, verify the GStreamer runtime works under Wine:

```bash
export WINEPREFIX=build/windows/wine
wine python windows/test_gst_wine.py
```

This runs 18 tests: init, element discovery, frame delivery, RTSP pipeline parse, GLib MainLoop.

## Windows EC2 deployment

The installer works on any Windows Server 2019/2022 EC2 instance:
- No Hyper-V, WSL2, or Docker required
- GStreamer runtime is bundled inside the installer
- DirectML GPU inference on instances with DX12-capable GPUs (e.g., g4dn, g5)
- CPU-only fallback on instances without GPU

## Uninstall

Via `Apps & Features` or the Start Menu shortcut. User data (`C:\ProgramData\LotVulture\`) is kept unless the uninstaller explicitly confirms deletion.
