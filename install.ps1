# Install Sink as a Claude Code skill on this machine.
#   - pip install -e . (so the `sink` CLI is on PATH)
#   - link skill\ into %USERPROFILE%\.claude\skills\sink so Claude Code sees it on next start
$ErrorActionPreference = "Stop"

$RepoDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$SkillSrc = Join-Path $RepoDir "skills\sink"
$SkillsDir = Join-Path $env:USERPROFILE ".claude\skills"
$SkillDst = Join-Path $SkillsDir "sink"

Write-Host "[sink] installing CLI (force-reinstall to refresh stale copies)..."
python -m pip install --force-reinstall --no-deps -e $RepoDir
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

Write-Host "[sink] linking skill into $SkillDst"
New-Item -ItemType Directory -Force -Path $SkillsDir | Out-Null
if (Test-Path $SkillDst) {
    Write-Host "[sink] $SkillDst already exists - removing it"
    Remove-Item -Recurse -Force $SkillDst
}

$linked = $false
try {
    New-Item -ItemType SymbolicLink -Path $SkillDst -Target $SkillSrc -ErrorAction Stop | Out-Null
    Write-Host "[sink] linked"
    $linked = $true
} catch {
    Write-Host "[sink] symlink failed (need Developer Mode or admin) - copying instead"
    Copy-Item -Recurse $SkillSrc $SkillDst
}

Write-Host "[sink] verifying CLI..."
$v = & sink --version 2>$null
if ($LASTEXITCODE -eq 0) {
    Write-Host "[sink] OK: $v"
} else {
    Write-Host "[sink] WARNING: 'sink' is not on PATH. Make sure your pip Scripts dir is in PATH."
}

Write-Host ""
Write-Host "[sink] Done. Restart Claude Code and the 'sink' skill will be available."
Write-Host "[sink] Try: claude  -> /sink   (or just ask 'set up sink coordination')"
