# Install Sink CLI plus Claude Code and Codex skills on this machine.
#   - pip install -e . (so the `sink` CLI is on PATH)
#   - link skills\sink into each agent's skill directory
$ErrorActionPreference = "Stop"

$RepoDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$SkillSrc = Join-Path $RepoDir "skills\sink"
$CodexRoot = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $env:USERPROFILE ".codex" }
$SkillTargets = @(
    (Join-Path $env:USERPROFILE ".claude\skills\sink"),
    (Join-Path $CodexRoot "skills\sink")
)

Write-Host "[sink] installing CLI (force-reinstall to refresh stale copies)..."
python -m pip install --force-reinstall --no-deps -e $RepoDir
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

foreach ($SkillDst in $SkillTargets) {
    $SkillsDir = Split-Path -Parent $SkillDst
    Write-Host "[sink] linking skill into $SkillDst"
    New-Item -ItemType Directory -Force -Path $SkillsDir | Out-Null
    if (Test-Path $SkillDst) {
        Write-Host "[sink] $SkillDst already exists - removing it"
        Remove-Item -Recurse -Force -LiteralPath $SkillDst
    }
    try {
        New-Item -ItemType SymbolicLink -Path $SkillDst -Target $SkillSrc -ErrorAction Stop | Out-Null
        Write-Host "[sink] linked"
    } catch {
        Write-Host "[sink] symlink failed (need Developer Mode or admin) - copying instead"
        Copy-Item -Recurse $SkillSrc $SkillDst
    }
}

Write-Host "[sink] verifying CLI..."
$v = & sink --version 2>$null
if ($LASTEXITCODE -eq 0) {
    Write-Host "[sink] OK: $v"
} else {
    Write-Host "[sink] WARNING: 'sink' is not on PATH. Make sure your pip Scripts dir is in PATH."
}

Write-Host ""
Write-Host "[sink] Done. Restart Claude Code or Codex and the 'sink' skill will be available."
Write-Host "[sink] Ask the agent to set up Sink coordination, or run: sink --help"
