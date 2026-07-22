<#
  serve.ps1 - Start the RGA backend on the PROJECT venv, always.

  WHY THIS EXISTS
    Typing bare `python -m rga serve` uses whatever `python` is first on PATH.
    On this machine that is the SYSTEM Python (anthropic 0.86.0), NOT the project
    venv (anthropic 0.116.0 - the version the code is built and tested against).
    The venv's launcher also re-spawns the server as a child process, and that
    child resolves its interpreter/site-packages from PATH too - so the ONLY
    reliable fix is to ACTIVATE the venv first (put it on PATH). Activated:
    both the parent and the spawned child use venv Python 0.116.0.

    Symptom when this is wrong: the SRS narrative renders as [TBD] because the
    (untested) anthropic build fails to construct the Foundry client.

  USAGE (from the backend folder):
      .\serve.ps1                              # provider foundry (default)
      .\serve.ps1 --provider mock              # pass through any `rga serve` args
      .\serve.ps1 --provider foundry --port 8001
#>
$ErrorActionPreference = "Stop"

$activate = Join-Path $PSScriptRoot ".venv\Scripts\Activate.ps1"
if (-not (Test-Path $activate)) {
    Write-Error "venv not found at: $activate`nCreate it first:  python -m venv .venv ;  .\.venv\Scripts\python.exe -m pip install -r requirements.txt"
    exit 1
}

# Run from the backend folder so config.yaml and ./data resolve, wherever invoked.
Set-Location $PSScriptRoot

# Activate the venv so `python` (and the child process the server spawns) resolve
# to the venv interpreter + site-packages (anthropic 0.116.0), never system Python.
. $activate

# Default to the Foundry provider when no args are given.
$rgaArgs = if ($args.Count -gt 0) { $args } else { @("--provider", "foundry") }

# Sanity banner: prove which interpreter + anthropic version is actually live.
$exe = python -c "import sys; print(sys.executable)"
$ver = python -c "import anthropic; print(anthropic.__version__)"
Write-Host "RGA backend  (venv activated)"      -ForegroundColor Cyan
Write-Host "  python    -> $exe"                -ForegroundColor Cyan
Write-Host "  anthropic -> $ver"                -ForegroundColor Cyan
Write-Host "  rga serve -> $($rgaArgs -join ' ')" -ForegroundColor Cyan

python -m rga serve @rgaArgs
