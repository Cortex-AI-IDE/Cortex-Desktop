# Sync-Cortex-OSS.ps1
# ============================================================
# Syncs the private Cortex repo to the public open-source repo
# (cortex_oss -> github.com/Cortex-AI-IDE/Cortex-Desktop).
#
# RULE: include EVERYTHING, except the safety exclusions below.
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

# -- Paths ---------------------------------------------------
$PRIVATE = "C:\Users\Hakeem1\OneDrive\Desktop\Cortex_Ai_Agent\cortex_project\cortex_desktop"
$PUBLIC  = "C:\Users\Hakeem1\OneDrive\Desktop\Cortex_Ai_Agent\cortex_project\cortex_oss"

if (-not (Test-Path $PRIVATE)) { Write-Error "PRIVATE repo not found: $PRIVATE"; exit 1 }
if (-not (Test-Path $PUBLIC))  { Write-Error "PUBLIC repo not found: $PUBLIC";  exit 1 }

Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  Cortex OSS Sync Tool (full sync)" -ForegroundColor Cyan
Write-Host "  $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

# -- EXCLUDED directories (matched by name at any depth) -----
# Build artifacts, caches, agent memory, and anything private.
# NOTE: robocopy /XD also PROTECTS these from /MIR deletion in
# the destination -- .github/ (public-only CI) survives the sync.
$XD = @(
    ".git", ".github", ".cortex", ".claude", ".playwright-mcp",
    "venv", ".venv", "node_modules", "__pycache__",
    "build", "dist", "installer_output",
    ".idea", ".vs", ".pytest_cache"
)

# -- EXCLUDED files (matched by name/wildcard at any depth) --
# Safety exclusions + public-only files that must never be
# overwritten or deleted by the sync (/XF protects them too).
$XF = @(
    # safety: never publish
    "agent_bridge.py",
    ".env",
    "github.txt",
    "behaviour.txt",      # old AI-session transcript debris
    ".cortexrc.json",     # contains local machine paths
    "*.log", "*.log.txt", "error.txt",
    "cortex.log.txt",

    # private repo's .gitignore is NOT pushed to the public repo
    ".gitignore",

    # public-only files: keep the OSS repo's own versions
    "README.md", "LICENSE", "CONTRIBUTING.md", "Sync-Cortex-OSS.ps1"
)

# -- Full-tree mirror with exclusions ------------------------
Write-Host "[1/3] Syncing full tree (with safety exclusions)..." -ForegroundColor Yellow
robocopy $PRIVATE $PUBLIC /MIR /NJH /NJS /NDL /NP /NC /NS /R:1 /W:1 /XD @XD /XF @XF
if ($LASTEXITCODE -ge 8) {
    Write-Warning "  robocopy reported errors (exit $LASTEXITCODE)"
} else {
    Write-Host "  OK   full tree synced" -ForegroundColor Green
}

# -- Secret scan gate: refuse to push if anything leaks ------
Write-Host "[2/3] Scanning for secrets..." -ForegroundColor Yellow
$patterns = @("ghp_[A-Za-z0-9]{20,}", "sk-[A-Za-z0-9]{20,}", "AKIA[0-9A-Z]{16}", "BEGIN [A-Z ]*PRIVATE KEY")
$hits = @()
foreach ($p in $patterns) {
    $found = Get-ChildItem -Path $PUBLIC -Recurse -File -Exclude "*.png","*.jpg","*.jpeg","*.ico","*.exe","*.dll" |
        Where-Object { $_.FullName -notmatch "\\\.git\\" } |
        Select-String -Pattern $p -List -ErrorAction SilentlyContinue
    if ($found) { $hits += $found }
}
if ($hits.Count -gt 0) {
    Write-Host "  SECRETS FOUND -- sync aborted before commit:" -ForegroundColor Red
    $hits | ForEach-Object { Write-Host "    $($_.Path):$($_.LineNumber)" -ForegroundColor Red }
    exit 1
}
Write-Host "  OK   no secrets detected" -ForegroundColor Green

# -- Git commit & push (if -Push flag) -----------------------
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
