# Pack the PyInstaller output into an MSIX for the Microsoft Store.
#
# Run build.ps1 -Store instead of calling this directly. It needs dist\Cortex
# to already exist AND to have been built with the store build flag (which
# turns the self-updater off; the Store rejects apps that update themselves).
#
# The manifest and Assets are written INTO dist\Cortex rather than copying the
# whole ~1GB tree to a staging folder. dist is rebuilt by every build, so
# nothing stale survives.
#
# Signing: NOT needed for Store submission, Microsoft signs the package after
# upload. Signing is only required to sideload the package for local testing
# (see -SignForTesting below).

param(
    [switch]$SignForTesting,
    [string]$Channel = ""
)

$ErrorActionPreference = "Stop"

$MSIX_DIR     = $PSScriptRoot
$PROJECT_ROOT = (Resolve-Path (Join-Path $MSIX_DIR "..\..")).Path
$DIST_APP     = Join-Path $PROJECT_ROOT "dist\Cortex"
$OUTPUT_DIR   = Join-Path $PROJECT_ROOT "msix_output"
$SOURCE_LOGO  = Join-Path $PROJECT_ROOT "src\assets\logo\app.png"

Write-Host "==================================================" -ForegroundColor Cyan
Write-Host "  Cortex - MSIX package (Microsoft Store)" -ForegroundColor Cyan
Write-Host "==================================================" -ForegroundColor Cyan
Write-Host ""

# ---------------------------------------------------------------- checks ---
if (-not (Test-Path $DIST_APP)) {
    Write-Host "ERROR: dist\Cortex not found - run the PyInstaller build first" -ForegroundColor Red
    exit 1
}
if (-not (Test-Path (Join-Path $DIST_APP "Cortex.exe"))) {
    Write-Host "ERROR: dist\Cortex\Cortex.exe not found" -ForegroundColor Red
    exit 1
}

# The Store rejects self-updating apps. Refuse to package a build that still
# has the updater switched on rather than failing certification days later.
# The channel is handed over by build.ps1 with -Channel. The marker file it
# writes is only a fallback for a manual run: reading it back is what reported
# channel '' in a real Store build (the packer refused a package whose exe was
# correctly channel-locked), so never let the decision depend on that file.
$channel = $Channel
if (-not $channel) {
    $buildInfo = Join-Path $PROJECT_ROOT "src\_build_info.py"
    if (Test-Path $buildInfo) {
        $match = Select-String -Path $buildInfo -Pattern 'CHANNEL\s*=\s*"([^"]+)"'
        if ($match) { $channel = $match.Matches[0].Groups[1].Value }
    }
}
if ($channel -ne "store") {
    Write-Host "ERROR: this build is channel '$channel', not 'store'." -ForegroundColor Red
    Write-Host "       The self-updater would still be active - the Store rejects that." -ForegroundColor Yellow
    Write-Host "       Build with:  .\build.ps1 -Store" -ForegroundColor Gray
    exit 1
}
Write-Host "  [OK] build channel: store (self-updater disabled)" -ForegroundColor Green

# --------------------------------------------------------------- version ---
Push-Location $PROJECT_ROOT
$MSIX_VERSION = (python -c "from src.version import MSIX_VERSION; print(MSIX_VERSION)") 2>$null
$APP_VERSION  = (python -c "from src.version import VERSION; print(VERSION)") 2>$null
Pop-Location

if (-not $MSIX_VERSION) {
    Write-Host "ERROR: could not read the version from src\version.py" -ForegroundColor Red
    exit 1
}
Write-Host "  [OK] version: $APP_VERSION  (manifest: $MSIX_VERSION)" -ForegroundColor Green

# ---------------------------------------------------------------- assets ---
Write-Host ""
Write-Host "[1/4] Generating tile images..." -ForegroundColor Yellow
$assetsDir = Join-Path $DIST_APP "Assets"
python (Join-Path $MSIX_DIR "make_assets.py") $SOURCE_LOGO $assetsDir
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: tile generation failed (is Pillow installed?)" -ForegroundColor Red
    exit 1
}

# ------------------------------------------------------------ sanitise ---
# An MSIX *is* an OPC package, so it owns the OPC control files at its root:
# "[Content_Types].xml" and "_rels\.rels". A payload that contains a nested,
# UNZIPPED OPC package collides with them and makeappx aborts with
#   0x8007007b - The filename, directory name, or volume label syntax is incorrect
# without naming the file (bisected 2026-07-31 to
# docx\templates\default-docx-template\_rels\.rels).
#
# python-docx ships exactly such a folder: default-docx-template\ is the
# expanded source of default.docx and holds 6 OPC artifacts. Nothing reads it
# at runtime, because docx.api._default_docx_path() returns
# templates\default.docx, which is a normal file and stays in the package.
#
# Any directory holding a "[Content_Types].xml" is by definition an unzipped
# OPC package, so the whole directory goes. dist\ is rebuilt by every build,
# so this is not destructive.
Write-Host ""
Write-Host "[2/4] Removing nested OPC packages (MSIX reserves those names)..." -ForegroundColor Yellow
$removed = 0
$opcRoots = Get-ChildItem -LiteralPath $DIST_APP -Recurse -File -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -eq '[Content_Types].xml' } |
            ForEach-Object { $_.DirectoryName } | Sort-Object -Unique
foreach ($dir in $opcRoots) {
    Remove-Item -LiteralPath $dir -Recurse -Force
    Write-Host "  [OK] removed $($dir.Substring($DIST_APP.Length + 1))\" -ForegroundColor Green
    $removed++
}
# Safety net: a stray _rels folder or content-types file outside such a root.
Get-ChildItem -LiteralPath $DIST_APP -Recurse -Directory -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -eq '_rels' } | ForEach-Object {
        Remove-Item -LiteralPath $_.FullName -Recurse -Force
        Write-Host "  [OK] removed $($_.FullName.Substring($DIST_APP.Length + 1))\" -ForegroundColor Green
        $removed++
    }
Get-ChildItem -LiteralPath $DIST_APP -Recurse -File -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -eq '[Content_Types].xml' } | ForEach-Object {
        Remove-Item -LiteralPath $_.FullName -Force
        Write-Host "  [OK] removed $($_.FullName.Substring($DIST_APP.Length + 1))" -ForegroundColor Green
        $removed++
    }
if ($removed -eq 0) { Write-Host "  [OK] none found" -ForegroundColor Green }

# -------------------------------------------------------------- manifest ---
Write-Host ""
Write-Host "[3/4] Writing AppxManifest.xml..." -ForegroundColor Yellow
$template = Get-Content (Join-Path $MSIX_DIR "AppxManifest.template.xml") -Raw
$manifest = $template.Replace("{VERSION}", $MSIX_VERSION)
# File types ("Open with", Default apps) come from src\core\file_associations.py.
$fileTypes = (python (Join-Path $MSIX_DIR "..\file_assoc\gen_file_assoc.py") msix) -join "`n"
if ($LASTEXITCODE -ne 0 -or -not $fileTypes) {
    Write-Host "ERROR: could not generate the file-type associations" -ForegroundColor Red
    exit 1
}
$manifest = $manifest.Replace("      {FILE_TYPE_ASSOCIATIONS}", $fileTypes)
if ($manifest.Contains("{FILE_TYPE_ASSOCIATIONS}")) {
    Write-Host "ERROR: {FILE_TYPE_ASSOCIATIONS} left in the manifest (indentation changed?)" -ForegroundColor Red
    exit 1
}
Write-Host "  [OK] file types from src\core\file_associations.py" -ForegroundColor Green
$manifestPath = Join-Path $DIST_APP "AppxManifest.xml"
# UTF-8 WITHOUT BOM - makeappx rejects a BOM in the manifest.
[System.IO.File]::WriteAllText($manifestPath, $manifest, (New-Object System.Text.UTF8Encoding($false)))
Write-Host "  [OK] $manifestPath" -ForegroundColor Green

# ------------------------------------------------------------- makeappx ---
Write-Host ""
Write-Host "[4/4] Packing MSIX..." -ForegroundColor Yellow

$makeappx = $null
$sdkRoots = @(
    (Join-Path ${env:ProgramFiles(x86)} "Windows Kits\10\bin"),
    (Join-Path $env:ProgramFiles "Windows Kits\10\bin")
)
foreach ($root in $sdkRoots) {
    if (-not (Test-Path $root)) { continue }
    # newest SDK first
    $candidates = Get-ChildItem $root -Directory -ErrorAction SilentlyContinue |
                  Sort-Object Name -Descending
    foreach ($dir in $candidates) {
        $candidate = Join-Path $dir.FullName "x64\makeappx.exe"
        if (Test-Path $candidate) { $makeappx = $candidate; break }
    }
    if ($makeappx) { break }
}

if (-not $makeappx) {
    Write-Host "ERROR: makeappx.exe not found." -ForegroundColor Red
    Write-Host "       Install the Windows 10/11 SDK (Windows App Certification Kit)." -ForegroundColor Yellow
    Write-Host "       https://developer.microsoft.com/windows/downloads/windows-sdk/" -ForegroundColor Gray
    exit 1
}
Write-Host "  Using: $makeappx" -ForegroundColor Gray

if (-not (Test-Path $OUTPUT_DIR)) { New-Item -ItemType Directory -Path $OUTPUT_DIR | Out-Null }
$msixPath = Join-Path $OUTPUT_DIR "Cortex_$MSIX_VERSION.msix"
if (Test-Path $msixPath) { Remove-Item $msixPath -Force }

& $makeappx pack /d $DIST_APP /p $msixPath /o
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: makeappx failed" -ForegroundColor Red
    exit 1
}

$sizeMB = [math]::Round((Get-Item $msixPath).Length / 1MB, 2)
Write-Host "  [OK] $msixPath ($sizeMB MB)" -ForegroundColor Green

# ------------------------------------------------- optional test signing ---
if ($SignForTesting) {
    Write-Host ""
    Write-Host "Signing for LOCAL TESTING only (never for Store upload)..." -ForegroundColor Yellow
    Write-Host "  A self-signed certificate must exist and be trusted on this" -ForegroundColor Gray
    Write-Host "  machine. Store submissions are signed by Microsoft." -ForegroundColor Gray
}

Write-Host ""
Write-Host "==================================================" -ForegroundColor Cyan
Write-Host "  MSIX ready" -ForegroundColor Cyan
Write-Host "==================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Upload to Partner Center:" -ForegroundColor White
Write-Host "  Apps and games > Cortex AI IDE > Start submission > Packages" -ForegroundColor Gray
Write-Host "  Do NOT sign it yourself - Microsoft signs it after upload." -ForegroundColor Gray
Write-Host ""
