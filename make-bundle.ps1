$ErrorActionPreference = "Stop"

$repoRoot = git rev-parse --show-toplevel
if (-not $repoRoot) {
    throw "This script must be run inside a Git repository."
}

Set-Location $repoRoot

$branch = git branch --show-current
$commit = git rev-parse --short HEAD
$status = git status --porcelain

if ($status) {
    Write-Host ""
    Write-Host "WARNING: Working tree contains uncommitted changes."
    Write-Host "git archive exports only the committed version."
    Write-Host ""
    git status --short
    Write-Host ""

    $answer = Read-Host "Continue anyway? (y/N)"
    if ($answer -ne "y") {
        exit 1
    }
}

$dist = Join-Path $repoRoot "dist"
New-Item -ItemType Directory -Force -Path $dist | Out-Null

$output = Join-Path $dist "VaultBridge-$branch-$commit.zip"

if (Test-Path $output) {
    Remove-Item $output -Force
}

git archive `
    --format=zip `
    --output="$output" `
    HEAD

if ($LASTEXITCODE -ne 0) {
    throw "git archive failed."
}

Write-Host ""
Write-Host "Bundle created:"
Write-Host $output
Write-Host ""
Write-Host "Branch: $branch"
Write-Host "Commit: $commit"