# Sync-Cortex-OSS.ps1
# ============================================================
# Syncs public-safe files from the private Cortex repo
# to the public open-source repo (cortex_oss).
#
# Usage:
#   .\Sync-Cortex-OSS.ps1
#   .\Sync-Cortex-OSS.ps1 -Push  (also commits & pushes)
# ============================================================

param(
    [switch]$Push,
    [string]$CommitMessage = "sync: update from private repo"
)

$ErrorActionPreference = "Stop"

# ── Paths ───────────────────────────────────────────────────
$PRIVATE = "C:\Users\Hakeem1\OneDrive\Desktop\Cortex_Ai_Agent\cortex_project\cortex_desktop"
$PUBLIC  = "C:\Users\Hakeem1\OneDrive\Desktop\Cortex_Ai_Agent\cortex_project\cortex_oss"

# ── Validate paths ──────────────────────────────────────────
if (-not (Test-Path $PRIVATE)) {
    Write-Error "PRIVATE repo not found: $PRIVATE"
    exit 1
}
if (-not (Test-Path $PUBLIC)) {
    Write-Error "PUBLIC repo not found: $PUBLIC"
    exit 1
}

Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  Cortex OSS Sync Tool" -ForegroundColor Cyan
Write-Host "  $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

# ── Directories to copy in full ─────────────────────────────
$DIRS = @(
    "src\ui",
    "src\ai\providers",
    "src\utils",
    "src\services",
    "src\plugin",
    "src\coordinator",
    "plugins",
    "Docs",
    "tests"
)

# ── Individual safe files ───────────────────────────────────
$FILES = @(
    # AI layer
    "src\ai\__init__.py",
    "src\ai\model_limits.py",
    "src\ai\model_registry.py",
    "src\ai\tool_executor.py",
    "src\ai\tool_result_storage.py",
    "src\ai\file_skeleton.py",
    "src\ai\streaming.py",
    "src\ai\circuit_breaker.py",
    "src\ai\session_task.py",

    # Core layer
    "src\core\__init__.py",
    "src\core\code_chunker.py",
    "src\core\embeddings.py",
    "src\core\siliconflow_embeddings.py",
    "src\core\semantic_search.py",
    "src\core\semantic_memory.py",
    "src\core\database.py",
    "src\core\task_graph.py",
    "src\core\event_bus.py",
    "src\core\file_manager.py",
    "src\core\git_manager.py",
    "src\core\codebase_index.py",
    "src\core\edit_impact.py",
    "src\core\change_orchestrator.py",
    "src\core\background_worker.py",
    "src\core\debug_loop.py",
    "src\core\memory_types.py",
    "src\core\memory_storage.py",
    "src\core\live_server.py",

    # Config
    "src\config\__init__.py",
    "src\config\settings.py",
    "src\config\theme_manager.py",

    # Root files
    "requirements.txt",
    "package.json",
    "package-lock.json",
    "pytest.ini",
    ".env.example"
)

# ── Sync directories ────────────────────────────────────────
Write-Host "[1/3] Syncing directories..." -ForegroundColor Yellow
foreach ($dir in $DIRS) {
    $src = Join-Path $PRIVATE $dir
    $dst = Join-Path $PUBLIC $dir

    if (-not (Test-Path $src)) {
        Write-Host "  SKIP $dir (source missing)" -ForegroundColor DarkGray
        continue
    }

    # Ensure destination exists
    New-Item -ItemType Directory -Force -Path $dst | Out-Null

    # Robocopy with mirror-like behavior (only copy changed files)
    $result = robocopy $src $dst /MIR /NJH /NJS /NDL /NP /NC /NS /R:1 /W:1
    if ($LASTEXITCODE -ge 8) {
        Write-Warning "  WARN $dir — robocopy had errors"
    } else {
        Write-Host "  OK   $dir" -ForegroundColor Green
    }
}

# ── Sync individual files ───────────────────────────────────
Write-Host "[2/3] Syncing individual files..." -ForegroundColor Yellow
foreach ($file in $FILES) {
    $src = Join-Path $PRIVATE $file
    $dst = Join-Path $PUBLIC $file

    if (-not (Test-Path $src)) {
        Write-Host "  SKIP $file (source missing)" -ForegroundColor DarkGray
        continue
    }

    $dstDir = Split-Path $dst -Parent
    New-Item -ItemType Directory -Force -Path $dstDir | Out-Null

    Copy-Item $src $dst -Force
    Write-Host "  OK   $file" -ForegroundColor Green
}

# ── Git commit & push (if -Push flag) ───────────────────────
if ($Push) {
    Write-Host "[3/3] Committing & pushing..." -ForegroundColor Yellow

    Push-Location $PUBLIC

    try {
        $status = git status --porcelain
        if ([string]::IsNullOrWhiteSpace($status)) {
            Write-Host "  No changes to commit." -ForegroundColor Green
        } else {
            git add -A
            git commit -m $CommitMessage

            Write-Host "  Pushing to remote..." -ForegroundColor Yellow
            git push origin main
            Write-Host "  PUSHED successfully!" -ForegroundColor Green
        }
    } finally {
        Pop-Location
    }
} else {
    Write-Host "[3/3] Skipping push (use -Push to commit & push)" -ForegroundColor DarkGray
}

Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  Sync complete!" -ForegroundColor Green
Write-Host "============================================" -ForegroundColor Cyan
