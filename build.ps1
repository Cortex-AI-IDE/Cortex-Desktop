# Cortex AI Agent - Automated Build Script
# Run this script to build Cortex from source to installer

Write-Host "==================================================" -ForegroundColor Cyan
Write-Host "  Cortex AI Agent - Build Automation" -ForegroundColor Cyan
Write-Host "==================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "IMPORTANT: Terminal popup fix included!" -ForegroundColor Yellow
Write-Host "   Runtime hook prevents ALL subprocess console windows" -ForegroundColor Gray
Write-Host ""

# Configuration
$VERSION = "2.7.0"
$PROJECT_ROOT = $PSScriptRoot
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

# Check .env.example
$envExample = Join-Path $PROJECT_ROOT ".env.example"
if (-not (Test-Path $envExample)) {
    Write-Host "ERROR: .env.example not found" -ForegroundColor Red
    exit 1
}
Write-Host "  [OK] .env.example exists" -ForegroundColor Green

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
Write-Host "  Installing Python packages..." -ForegroundColor Gray
pip install -r requirements.txt -q
if ($LASTEXITCODE -eq 0) {
    Write-Host "  [OK] Python dependencies installed" -ForegroundColor Green
} else {
    Write-Host "  ERROR: Failed to install Python dependencies" -ForegroundColor Red
    exit 1
}

# Node.js dependencies
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

$pyinstallerCmd = "python -m PyInstaller cortex.spec --clean --noconfirm"
Write-Host "  Running: $pyinstallerCmd" -ForegroundColor Gray

Invoke-Expression $pyinstallerCmd

if ($LASTEXITCODE -eq 0) {
    Write-Host "  [OK] Main IDE build successful" -ForegroundColor Green
} else {
    Write-Host "  ERROR: Main IDE build failed" -ForegroundColor Red
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
    ".env.example" = Join-Path $checkRoot ".env.example"
    "rg.exe" = Join-Path $checkRoot "bin\rg.exe"
    "node.exe" = Join-Path $checkRoot "bin\node\node.exe"
    "Pyright LSP" = Join-Path $checkRoot "node_modules\pyright"
    "TypeScript LSP" = Join-Path $checkRoot "node_modules\typescript-language-server"
    "AI Chat UI" = Join-Path $checkRoot "src\ui\html\ai_chat"
}

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

# Step 5: Build Installer
Write-Host "[6/6] Building Windows installer..." -ForegroundColor Yellow

$innoSetupPaths = @(
    "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    "C:\Program Files\Inno Setup 6\ISCC.exe"
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
    & $innoSetup $issFile
    
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
}

Write-Host ""

# Summary
Write-Host "==================================================" -ForegroundColor Cyan
Write-Host "  Build Complete!" -ForegroundColor Cyan
Write-Host "==================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Build Artifacts:" -ForegroundColor White
Write-Host "  Executable: dist\Cortex\Cortex.exe" -ForegroundColor Gray
Write-Host "  Installer:  installer_output\Cortex_Setup_v$VERSION.exe" -ForegroundColor Gray
Write-Host ""
Write-Host "Next Steps:" -ForegroundColor White
Write-Host "  1. Test executable: cd dist\Cortex; .\Cortex.exe" -ForegroundColor Gray
Write-Host "  2. Test installer: installer_output\Cortex_Setup_v$VERSION.exe" -ForegroundColor Gray
Write-Host "  3. Review checklist: BUILD_CHECKLIST.md" -ForegroundColor Gray
Write-Host ""

# Check if .env exists in dist (should NOT be there for security)
$distEnv = Join-Path $checkRoot ".env"
if (Test-Path $distEnv) {
    Write-Host "SECURITY WARNING: .env contains API keys!" -ForegroundColor Red
    Write-Host "  This file should NOT be distributed!" -ForegroundColor Yellow
    Write-Host "  Delete it before building installer:" -ForegroundColor Gray
    Write-Host "    Remove-Item $distEnv" -ForegroundColor Gray
} else {
    Write-Host "  [OK] No .env in dist (users will get .env.example)" -ForegroundColor Green
}

Write-Host ""
Write-Host "Build completed at $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" -ForegroundColor Cyan
