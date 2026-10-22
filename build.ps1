# Cortex AI IDE - Automated Build Script
# Run this script to build Cortex from source to installer.
#
#   .\build.ps1            -> cortex-ide.app installer  (installer_output\*.exe)
#   .\build.ps1 -Store     -> Microsoft Store package   (msix_output\*.msix)
#
# ONE source tree, two outputs. The only difference between the channels is
# the build channel written into src\_build_info.py: the Store build turns
# the self-updater off, because the Store owns updating and rejects apps
# that install their own updates.
#
# Requirements: Python 3.11+ with requirements-dev.txt installed, Node.js,
# `npm install` once for the Monaco editor, Inno Setup 6 or 7 for the
# installer, and the Windows 10/11 SDK (makeappx.exe) for -Store.

param(
    [switch]$Store
)

Write-Host "==================================================" -ForegroundColor Cyan
Write-Host "  Cortex AI IDE - Build Automation" -ForegroundColor Cyan
Write-Host "==================================================" -ForegroundColor Cyan
Write-Host ""

# Configuration
$PROJECT_ROOT = $PSScriptRoot

# Version comes from src\version.py, the single source of truth. It used to
# be hardcoded here as "2.7.0" while the installer said 2.9.5 and the API
# reported 2.8.5. Read from the project root so the script works no matter
# which directory it is invoked from.
Push-Location $PROJECT_ROOT
$VERSION = (python -c "from src.version import VERSION; print(VERSION)")
Pop-Location
if (-not $VERSION) {
    Write-Host "ERROR: could not read the version from src\version.py" -ForegroundColor Red
    exit 1
}
$CHANNEL = if ($Store) { "store" } else { "web" }
Write-Host "  Version: $VERSION   Channel: $CHANNEL" -ForegroundColor Cyan
Write-Host ""

# The generated build marker must not survive the build: left in the source
# tree, a Store build would make a later `python src/main.py` report channel
# "store" and silently disable the updater in development.
$buildInfoPath = Join-Path $PROJECT_ROOT "src\_build_info.py"
function Remove-BuildMarker {
    if (Test-Path $buildInfoPath) {
        Remove-Item -LiteralPath $buildInfoPath -Force
        Write-Host "  [OK] build marker cleared (source tree is 'dev' again)" -ForegroundColor Green
    }
}

$DIST_DIR = Join-Path $PROJECT_ROOT "dist"
$BUILD_DIR = Join-Path $PROJECT_ROOT "build"
$INSTALLER_OUTPUT = Join-Path $PROJECT_ROOT "installer_output"

# Step 0: Pre-Build Checks
Write-Host "[1/6] Pre-Build Verification..." -ForegroundColor Yellow

# Check Python
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Host "ERROR: Python not found in PATH" -ForegroundColor Red
    exit 1
}
Write-Host "  [OK] Python: $(python --version)" -ForegroundColor Green

# Check Node.js
if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    Write-Host "ERROR: Node.js not found in PATH" -ForegroundColor Red
    exit 1
}
Write-Host "  [OK] Node.js: $(node --version)" -ForegroundColor Green

Write-Host ""

# Step 1: Clean Previous Builds
Write-Host "[2/6] Cleaning previous builds..." -ForegroundColor Yellow

if (Test-Path $DIST_DIR) {
    Remove-Item -Recurse -Force $DIST_DIR
    Write-Host "  [OK] Removed dist/" -ForegroundColor Green
}

if (Test-Path $BUILD_DIR) {
    Remove-Item -Recurse -Force $BUILD_DIR
    Write-Host "  [OK] Removed build/" -ForegroundColor Green
}

Write-Host ""

# Step 2: Install Dependencies
Write-Host "[3/6] Installing dependencies..." -ForegroundColor Yellow

# Python dependencies
# Use `python -m pip`, never the pip.exe launcher: pip.exe hardcodes an
# absolute path to python.exe when it is created, so moving the project
# folder breaks it permanently with
#   "Fatal error in launcher: Unable to create process using ..."
# even though python.exe itself still works. (Same reason the PyInstaller
# step below uses `python -m PyInstaller`.)
Write-Host "  Installing Python packages..." -ForegroundColor Gray
# requirements-dev.txt = requirements.txt (runtime) + PyInstaller and dev tools
python -m pip install -r requirements-dev.txt -q
if ($LASTEXITCODE -eq 0) {
    Write-Host "  [OK] Python dependencies installed" -ForegroundColor Green
} else {
    Write-Host "  ERROR: Failed to install Python dependencies" -ForegroundColor Red
    exit 1
}

# Node.js dependencies (Monaco editor; the app loads it from node_modules)
Write-Host "  Installing Node.js packages..." -ForegroundColor Gray
npm install --silent
if ($LASTEXITCODE -eq 0) {
    Write-Host "  [OK] Node.js dependencies installed" -ForegroundColor Green
} else {
    Write-Host "  ERROR: Failed to install Node.js dependencies" -ForegroundColor Red
    exit 1
}

Write-Host ""

# Step 3: PyInstaller Build (Main IDE)
Write-Host "[4/6] Building main IDE executable with PyInstaller..." -ForegroundColor Yellow

# Bake the channel into the build. src\version.py reads this; when the file is
# absent (running from source) the channel is "dev". It is gitignored and
# rewritten on every build, so a Store build can never leak into a web build.
$buildInfo = @"
# GENERATED BY build.ps1 - DO NOT EDIT, DO NOT COMMIT.
# Overwritten on every build; absent when running from source.
CHANNEL = "$CHANNEL"
VERSION = "$VERSION"
"@
[System.IO.File]::WriteAllText($buildInfoPath, $buildInfo, (New-Object System.Text.UTF8Encoding($false)))
Write-Host "  [OK] build channel baked in: $CHANNEL" -ForegroundColor Green

$pyinstallerCmd = "python -m PyInstaller cortex.spec --clean --noconfirm"
Write-Host "  Running: $pyinstallerCmd" -ForegroundColor Gray

Invoke-Expression $pyinstallerCmd

if ($LASTEXITCODE -eq 0) {
    Write-Host "  [OK] Main IDE build successful" -ForegroundColor Green
} else {
    Write-Host "  ERROR: Main IDE build failed" -ForegroundColor Red
    Remove-BuildMarker
    exit 1
}

# Step 4: Verify Build
Write-Host "[5/6] Verifying build contents..." -ForegroundColor Yellow

# PyInstaller one-folder build puts files in _internal subfolder
$buildRoot = Join-Path $DIST_DIR "Cortex"
$internalDir = Join-Path $buildRoot "_internal"
$checkRoot = if (Test-Path $internalDir) { $internalDir } else { $buildRoot }

$buildChecks = @{
    "Cortex.exe" = Join-Path $buildRoot "Cortex.exe"
    "rg.exe" = Join-Path $checkRoot "bin\rg.exe"
    "AI Chat UI" = Join-Path $checkRoot "src\ui\html\ai_chat"
    "Monaco editor" = Join-Path $checkRoot "node_modules\monaco-editor"
    "UI themes" = Join-Path $checkRoot "src\ui\themes"
    "Bundled skills" = Join-Path $checkRoot "src\agent\src\skills\bundled"
}
# Only checked when the folder exists in the source tree, since plugins are
# optional in the open-source checkout.
if (Test-Path (Join-Path $PROJECT_ROOT "plugins")) {
    $buildChecks["Plugins"] = Join-Path $checkRoot "plugins"
}
# node.exe is NOT checked: it is gitignored (69 MB), so a clean clone has no
# bin\node and the app falls back to the Node on PATH.
# Deliberately NOT checked, because cortex.spec excludes them by design:
#   .env.example - installed builds never read .env (keys live in Credential
#                  Manager), and a bundled one overrides users' saved keys.
#   pyright / typescript-language-server - LSP servers are not bundled; the
#                  LSP system has no manager module yet. Checking for them
#                  only produced false "MISSING" warnings on every build.

$allPassed = $true
foreach ($name in $buildChecks.Keys) {
    $path = $buildChecks[$name]
    if (Test-Path -LiteralPath $path) {
        Write-Host "  [OK] $name" -ForegroundColor Green
    } else {
        Write-Host "  [FAIL] $name - MISSING" -ForegroundColor Red
        $allPassed = $false
    }
}

if (-not $allPassed) {
    Write-Host "WARNING: Some build files are missing!" -ForegroundColor Yellow
}

Write-Host ""

# Step 5: Package for the selected channel
if ($Store) {
    Write-Host "[6/6] Building MSIX package for the Microsoft Store..." -ForegroundColor Yellow
    & (Join-Path $PROJECT_ROOT "build_tools\msix\build_msix.ps1") -Channel $CHANNEL
    $msixExit = $LASTEXITCODE
    Remove-BuildMarker
    if ($msixExit -ne 0) {
        Write-Host "  ERROR: MSIX build failed" -ForegroundColor Red
        exit 1
    }
    Write-Host ""
    Write-Host "==================================================" -ForegroundColor Cyan
    Write-Host "  Store build complete" -ForegroundColor Cyan
    Write-Host "==================================================" -ForegroundColor Cyan
    Write-Host "  Package: msix_output\Cortex_*.msix" -ForegroundColor Gray
    Write-Host "  Upload:  Partner Center > Cortex AI IDE > Start submission" -ForegroundColor Gray
    Write-Host ""
    exit 0
}

Write-Host "[6/6] Building Windows installer..." -ForegroundColor Yellow

# Inno Setup 6 and 7, machine-wide and per-user installs. 7 is a drop-in for
# the same .iss file, so accept whichever is present.
$innoSetupPaths = @(
    "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    "C:\Program Files\Inno Setup 6\ISCC.exe",
    (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe"),
    "C:\Program Files (x86)\Inno Setup 7\ISCC.exe",
    "C:\Program Files\Inno Setup 7\ISCC.exe",
    (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 7\ISCC.exe")
)

$innoSetup = $null
foreach ($path in $innoSetupPaths) {
    if (Test-Path $path) {
        $innoSetup = $path
        break
    }
}

if ($innoSetup) {
    Write-Host "  Using Inno Setup: $innoSetup" -ForegroundColor Gray

    $issFile = Join-Path $PROJECT_ROOT "cortex_setup.iss"
    # File types for "Open with" / Default apps, from src\core\file_associations.py
    python (Join-Path $PROJECT_ROOT "build_tools\file_assoc\gen_file_assoc.py") iss
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  [ERROR] could not generate build_tools\file_assoc\cortex_file_assoc.iss" -ForegroundColor Red
        Remove-BuildMarker
        exit 1
    }
    # Pass the version so the installer can never disagree with src\version.py
    & $innoSetup "/DMyAppVersion=$VERSION" $issFile

    if ($LASTEXITCODE -eq 0) {
        Write-Host "  [OK] Installer build successful" -ForegroundColor Green

        # Show installer file
        $installerFile = Join-Path $INSTALLER_OUTPUT "Cortex_Setup_v$VERSION.exe"
        if (Test-Path $installerFile) {
            $fileSize = [math]::Round((Get-Item $installerFile).Length / 1MB, 2)
            Write-Host "  [OK] Installer: $installerFile ($fileSize MB)" -ForegroundColor Green
        }
    } else {
        Write-Host "  ERROR: Installer build failed" -ForegroundColor Red
    }
} else {
    Write-Host "  [WARN] Inno Setup not found - skipping installer build" -ForegroundColor Yellow
    Write-Host "    Download from: https://jrsoftware.org/isdl.php" -ForegroundColor Gray
    Write-Host "    The portable app still works: dist\Cortex\Cortex.exe" -ForegroundColor Gray
}

Remove-BuildMarker
Write-Host ""

# Did an installer actually get produced? The summary below used to print the
# installer path unconditionally, so a skipped or failed Inno Setup step still
# ended with "Installer: ...Cortex_Setup_v2.9.6.exe" and looked like success.
$installerFinal = Join-Path $INSTALLER_OUTPUT "Cortex_Setup_v$VERSION.exe"
$installerBuilt = Test-Path $installerFinal

# Summary
Write-Host "==================================================" -ForegroundColor Cyan
Write-Host "  Build Complete!" -ForegroundColor Cyan
Write-Host "==================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Build Artifacts:" -ForegroundColor White
Write-Host "  Executable: dist\Cortex\Cortex.exe" -ForegroundColor Gray
if ($installerBuilt) {
    Write-Host "  Installer:  installer_output\Cortex_Setup_v$VERSION.exe" -ForegroundColor Gray
} else {
    Write-Host "  Installer:  NOT BUILT - no installer_output\Cortex_Setup_v$VERSION.exe" -ForegroundColor Red
}
Write-Host ""
Write-Host "Next Steps:" -ForegroundColor White
Write-Host "  1. Test executable: cd dist\Cortex; .\Cortex.exe" -ForegroundColor Gray
Write-Host "  2. Test installer: installer_output\Cortex_Setup_v$VERSION.exe" -ForegroundColor Gray
Write-Host ""

# Check if .env exists in dist (should NOT be there for security)
$distEnv = Join-Path $checkRoot ".env"
if (Test-Path $distEnv) {
    Write-Host "SECURITY WARNING: .env contains API keys!" -ForegroundColor Red
    Write-Host "  This file should NOT be distributed!" -ForegroundColor Yellow
    Write-Host "  Delete it before building installer:" -ForegroundColor Gray
    Write-Host "    Remove-Item $distEnv" -ForegroundColor Gray
} else {
    Write-Host "  [OK] No .env in dist" -ForegroundColor Green
}

Write-Host ""
Write-Host "Build completed at $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" -ForegroundColor Cyan
