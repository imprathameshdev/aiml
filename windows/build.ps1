<#
.SYNOPSIS
    Builds the LotVulture Windows installer.

.DESCRIPTION
    This script orchestrates the full Windows build:

    1. Build the Vue frontend (Node.js)
    2. Create a Python virtualenv and install dependencies
    3. Compile vulturevision as a Windows .pyd (CPU-only)
    4. Run PyInstaller to bundle the app
    5. Build the Inno Setup installer

    Run from the repository root:
        .\windows\build.ps1

.PARAMETER Config
    Build configuration: "Release" (default) or "Debug".

.PARAMETER SkipFrontend
    Skip the Vue frontend build (use existing dist/).

.PARAMETER SkipVultureVision
    Skip vulturevision compilation (use existing .pyd).

.PARAMETER SkipPyInstaller
    Skip the PyInstaller bundle step.

.PARAMETER PyInstallerFlags
    Extra flags to pass to PyInstaller (e.g., "--onefile").
#>

param(
    [ValidateSet("Release", "Debug")]
    [string]$Config = "Release",
    [switch]$SkipFrontend,
    [switch]$SkipVultureVision,
    [switch]$SkipPyInstaller,
    [string]$PyInstallerFlags = "--onedir"
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$BuildDir = "$RepoRoot\build\windows"
$DistDir = "$BuildDir\dist"
$GstDir = "$DistDir\gstreamer"
$Python = "python3"  # adjust if python.exe is python3 on your machine

Write-Host "=== LotVulture Windows Build ===" -ForegroundColor Cyan
Write-Host "Config:  $Config"
Write-Host "Root:    $RepoRoot"
Write-Host "Build:   $BuildDir"
Write-Host ""

# ---------------------------------------------------------------------------
# Check / download demovideo assets
# ---------------------------------------------------------------------------
Write-Host ">>> Verifying demovideo assets..." -ForegroundColor Green
$DemoVideoDir = "$RepoRoot\demovideo"
if (-not (Test-Path $DemoVideoDir)) {
    New-Item -ItemType Directory -Path $DemoVideoDir | Out-Null
}

$VideoFile = "$DemoVideoDir\initial_parking_lot_demo.mp4"
$SpacesFile = "$DemoVideoDir\initial_parking_lot_demo_spaces.json"

if (-not (Test-Path $VideoFile)) {
    Write-Host "Downloading initial_parking_lot_demo.mp4..." -ForegroundColor Yellow
    Invoke-WebRequest -Uri "http://download.lotvulture.com/demovideo/initial_parking_lot_demo.mp4" -OutFile $VideoFile -UseBasicParsing
}

if (-not (Test-Path $SpacesFile)) {
    Write-Host "Downloading initial_parking_lot_demo_spaces.json..." -ForegroundColor Yellow
    Invoke-WebRequest -Uri "http://download.lotvulture.com/demovideo/initial_parking_lot_demo_spaces.json" -OutFile $SpacesFile -UseBasicParsing
}

if (-not (Test-Path $VideoFile)) {
    Write-Error "Error: demovideo\initial_parking_lot_demo.mp4 is missing!"
    exit 1
}

if (-not (Test-Path $SpacesFile)) {
    Write-Error "Error: demovideo\initial_parking_lot_demo_spaces.json is missing!"
    exit 1
}
Write-Host "Demovideo assets verified successfully." -ForegroundColor Gray
Write-Host ""

# ---------------------------------------------------------------------------
# 1. Build the Vue frontend
# ---------------------------------------------------------------------------
if (-not $SkipFrontend) {
    Write-Host ">>> Building Vue frontend..." -ForegroundColor Green
    Push-Location "$RepoRoot\frontend"
    try {
        if (-not (Test-Path "node_modules")) {
            & npm ci
        }
        & npm run build
        if ($LASTEXITCODE -ne 0) { throw "Frontend build failed" }
        Write-Host "    Frontend built to frontend\dist\" -ForegroundColor Green
    } finally {
        Pop-Location
    }
} else {
    Write-Host ">>> Skipping frontend build (--SkipFrontend)" -ForegroundColor Yellow
}

# ---------------------------------------------------------------------------
# 2. Python virtualenv
# ---------------------------------------------------------------------------
Write-Host ">>> Setting up Python virtualenv..." -ForegroundColor Green
$VenvDir = "$BuildDir\venv"
if (-not (Test-Path "$VenvDir\Scripts\python.exe")) {
    & $Python -m venv $VenvDir
}

$Pip = "$VenvDir\Scripts\pip.exe"
$PythonExe = "$VenvDir\Scripts\python.exe"

# Install deps — exclude PyGObject (provided by bundled GStreamer runtime)
# and onnxruntime-gpu (use CPU-only on Windows).
Write-Host ">>> Installing Python dependencies..." -ForegroundColor Green
& $Pip install --upgrade pip
& $Pip install pyinstaller
& $Pip install -r "$RepoRoot\requirements.txt"

# ---------------------------------------------------------------------------
# 3. Compile or download vulturevision for Windows
# ---------------------------------------------------------------------------
if (-not $SkipVultureVision -and (Test-Path "$RepoRoot\vulturevision")) {
    Write-Host ">>> Compiling vulturevision for Windows..." -ForegroundColor Green
    Push-Location "$RepoRoot\vulturevision"
    try {
        # Download ONNX Runtime CPU NuGet package if not present
        $OrtDir = "$BuildDir\onnxruntime"
        if (-not (Test-Path "$OrtDir\lib\onnxruntime.dll")) {
            Write-Host "    Downloading ONNX Runtime CPU for Windows..." -ForegroundColor Yellow
            $null = New-Item -ItemType Directory -Force -Path "$OrtDir"
            # Use the official ONNX Runtime NuGet feed
            # https://www.nuget.org/packages/Microsoft.ML.OnnxRuntime/
            $OrtVersion = "1.19.2"
            $OrtUrl = "https://www.nuget.org/api/v2/package/Microsoft.ML.OnnxRuntime/$OrtVersion"
            $OrtNupkg = "$BuildDir\onnxruntime.nupkg"
            Invoke-WebRequest -Uri $OrtUrl -OutFile $OrtNupkg
            # Extract the native DLLs
            Add-Type -AssemblyName System.IO.Compression.FileSystem
            $Zip = [System.IO.Compression.ZipFile]::OpenRead($OrtNupkg)
            foreach ($entry in $Zip.Entries) {
                if ($entry.FullName -like "runtimes\win-x64\native\*") {
                    $dest = [System.IO.Path]::Combine($OrtDir, [System.IO.Path]::GetFileName($entry.Name))
                    [System.IO.Compression.ZipFileExtensions]::ExtractToFile($entry, $dest, $true)
                }
            }
            $Zip.Dispose()
            Remove-Item $OrtNupkg
        }

        # Build the C++ extension
        python setup.py build_ext --inplace
        if ($LASTEXITCODE -ne 0) { throw "vulturevision compilation failed" }

        # Package as a wheel
        python -m pip wheel --no-deps -w "$BuildDir\wheels" .
        if ($LASTEXITCODE -ne 0) { throw "vulturevision wheel creation failed" }
        Write-Host "    vulturevision wheel created" -ForegroundColor Green
    } finally {
        Pop-Location
    }
} elseif (-not (Test-Path "$RepoRoot\vulturevision")) {
    Write-Host ">>> vulturevision source directory not found; downloading pre-built wheel..." -ForegroundColor Yellow
} else {
    Write-Host ">>> Skipping vulturevision compilation (--SkipVultureVision)" -ForegroundColor Yellow
}

# Install the vulturevision wheel
$VvWheel = Get-ChildItem "$BuildDir\wheels\vulturevision-*-cp312-cp312-win_amd64.whl" 2>$null | Select-Object -First 1
if (-not $VvWheel) {
    Write-Host ">>> Downloading vulturevision Windows wheel from download.lotvulture.com..." -ForegroundColor Green
    $WheelUrl = "http://download.lotvulture.com/dist/vulturevision/vulturevision-1.0.0-cp312-cp312-win_amd64.whl"
    $WheelPath = "$BuildDir\wheels\vulturevision-1.0.0-cp312-cp312-win_amd64.whl"
    New-Item -ItemType Directory -Force -Path (Split-Path $WheelPath) > $null
    Invoke-WebRequest -Uri $WheelUrl -OutFile $WheelPath
    $VvWheel = Get-Item $WheelPath
}

if ($VvWheel) {
    Write-Host ">>> Installing vulturevision wheel..." -ForegroundColor Green
    & $Pip install "$($VvWheel.FullName)"
}

# ---------------------------------------------------------------------------
# 4. Bundle GStreamer MinGW runtime
# ---------------------------------------------------------------------------
Write-Host ">>> Staging GStreamer runtime..." -ForegroundColor Green
if (-not (Test-Path "$GstDir\bin\gstreamer-1.0-0.dll")) {
    Write-Host "    GStreamer runtime not found at $GstDir" -ForegroundColor Yellow
    Write-Host "    Download the MinGW build from https://gstreamer.freedesktop.org/download/" -ForegroundColor Yellow
    Write-Host "    Extract to $GstDir with this layout:" -ForegroundColor Yellow
    Write-Host "      gstreamer/" -ForegroundColor Yellow
    Write-Host "        bin/              # core DLLs" -ForegroundColor Yellow
    Write-Host "        lib/gstreamer-1.0/  # plugin DLLs" -ForegroundColor Yellow
    Write-Host "        lib/girepository-1.0/ # typelib files" -ForegroundColor Yellow
    throw "GStreamer runtime required. See instructions above."
}
Write-Host "    GStreamer runtime found at $GstDir" -ForegroundColor Green

# ---------------------------------------------------------------------------
# 5. PyInstaller bundle
# ---------------------------------------------------------------------------
if (-not $SkipPyInstaller) {
    Write-Host ">>> Running PyInstaller..." -ForegroundColor Green

    $PyInstDir = "$BuildDir\pyinstaller"
    $SpecFile = "$BuildDir\lotvulture.spec"

    # Create a PyInstaller spec for our app
    @"
# -*- mode: python ; coding: utf-8 -*-
import sys
from pathlib import Path

block_cipher = None

# Collect all GStreamer DLLs and typelibs as data files
gst_root = Path(r"$GstDir")
gst_data = []
if gst_root.is_dir():
    # recursively collect everything under gstreamer/
    for p in gst_root.rglob("*"):
        if p.is_file():
            rel = p.relative_to(gst_root.parent)
            gst_data.append((str(p), str(rel.parent)))

a = Analysis(
    [r"$RepoRoot\main.py"],
    pathex=[r"$PyInstDir"],
    binaries=[],
    datas=[
        (r"$RepoRoot\frontend\dist", "frontend/dist"),
        *gst_data,
    ] + ([(r"$RepoRoot\demovideo", "demovideo")] if os.path.isdir(r"$RepoRoot\demovideo") else []),
    hiddenimports=[
        "backend._windows_env",
        "backend.app",
        "backend.database",
        "backend.scheduler",
        "backend.inference",
        "backend.stream_manager",
        "backend.maintenance",
        "backend.auth",
        "backend.models",
        "backend.config",
        "backend.logging_config",
        "backend.cli",
        "backend.billing",
        "backend.billing_routes",
        "pydantic",
        "sqlalchemy",
        "lmdb",
        "loguru",
        "cv2",
        "PIL",
        "yt_dlp",
        "requests",
        "opentelemetry",
        "opentelemetry.sdk",
        "opentelemetry.exporter.otlp.proto.grpc",
        "opentelemetry.instrumentation.fastapi",
        "opentelemetry.instrumentation.sqlalchemy",
        "numpy",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "test",
        "unittest",
        "distutils",
        "setuptools",
        "pip",
        "PyQt5",
        "PyQt6",
        "PySide2",
        "PySide6",
        "notebook",
        "jupyter",
        "ipython",
        "matplotlib",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="lotvulture",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

# Also create a console version for debugging
exe_debug = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="lotvulture-debug",
    debug=True,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="lotvulture"
)
"@ | Out-File -FilePath $SpecFile -Encoding utf8

    # Clean previous build
    if (Test-Path $PyInstDir) { Remove-Item -Recurse -Force $PyInstDir }

    & $PythonExe -m PyInstaller $SpecFile --distpath $DistDir --workpath "$BuildDir\pycache"
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

    Write-Host "    PyInstaller bundle at: $DistDir\lotvulture\" -ForegroundColor Green
} else {
    Write-Host ">>> Skipping PyInstaller (--SkipPyInstaller)" -ForegroundColor Yellow
}

# ---------------------------------------------------------------------------
# 6. Build the Inno Setup installer
# ---------------------------------------------------------------------------
$Iscc = "C:\Program Files (x86)\Inno Setup 6\iscc.exe"
if (Test-Path $Iscc) {
    Write-Host ">>> Building Inno Setup installer..." -ForegroundColor Green
    $AppVer = if ($env:APP_VERSION) { $env:APP_VERSION } elseif ($env:VULTURE_VERSION) { $env:VULTURE_VERSION } else { "v" + (Get-Date -Format "yyyy.MM.dd") + "-" + (git rev-parse --short HEAD) }
    & $Iscc "/DMyAppVersion=$AppVer" "$RepoRoot\windows\installer.iss"
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }
    Write-Host "    Installer at: $BuildDir\LotVulture_Setup.exe" -ForegroundColor Green
} else {
    Write-Host ">>> Inno Setup not found at $Iscc" -ForegroundColor Yellow
    Write-Host "    Install Inno Setup 6 from https://jrsoftware.org/isinfo.php" -ForegroundColor Yellow
    Write-Host "    Then run: iscc windows\installer.iss" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "=== Build complete ===" -ForegroundColor Cyan
