<#
.SYNOPSIS
  One-command setup for the PMU Validation Bench on a clean Windows machine.

.DESCRIPTION
  A fresh Windows install has NO real Python and NO git on PATH -- it only ships
  0-byte Microsoft Store "app execution alias" stubs for python.exe/python3.exe,
  which make it look like Python is present when it is not. This script:

    1. Detects a REAL Python (ignoring the Store stubs); installs 3.12 via winget
       if missing.
    2. Detects git; installs it via winget if missing.
    3. Initializes the four instrument submodules under apps\.
    4. Creates a .venv and installs the package WITH the serial/visa/plot extras.
       (These are required even for --simulate: the vendored instrument drivers
       do a top-level `import serial` / pyvisa at import time, so core-only
       numpy is not enough to run simulate mode.)
    5. Runs a simulate smoke test to prove the whole chain works.

  Safe to re-run: everything is idempotent.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\bootstrap.ps1
#>
[CmdletBinding()]
param(
  [switch]$SkipSmokeTest
)

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

function Write-Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "    OK: $msg" -ForegroundColor Green }
function Write-Warn2($msg){ Write-Host "    !! $msg" -ForegroundColor Yellow }

function Refresh-Path {
  $env:Path = [Environment]::GetEnvironmentVariable('Path','Machine') + ';' +
              [Environment]::GetEnvironmentVariable('Path','User')
}

# Returns the path to a REAL python.exe (never a 0-byte WindowsApps store stub), or $null.
function Find-RealPython {
  $candidates = @()
  $candidates += (Get-Command python.exe -All -ErrorAction SilentlyContinue | ForEach-Object Source)
  $candidates += "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
  $candidates += "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe"
  foreach ($c in $candidates) {
    if ([string]::IsNullOrWhiteSpace($c)) { continue }
    if (-not (Test-Path $c)) { continue }
    if ($c -like '*WindowsApps*') { continue }          # Microsoft Store alias stub
    if ((Get-Item $c).Length -eq 0) { continue }         # 0-byte stub
    return $c
  }
  return $null
}

# --- 1. Python -------------------------------------------------------------
Write-Step "Checking for a real Python interpreter"
$py = Find-RealPython
if (-not $py) {
  Write-Warn2 "No real Python found (only Store stubs, if any). Installing Python 3.12 via winget..."
  winget install --id Python.Python.3.12 -e --source winget `
    --accept-package-agreements --accept-source-agreements --scope user
  Refresh-Path
  $py = Find-RealPython
  if (-not $py) { throw "Python install did not produce a usable interpreter. Install it manually from python.org, then re-run." }
}
Write-Ok "$py ($(& $py --version))"
Write-Warn2 "If 'python' still opens the Microsoft Store in new terminals, disable the aliases: Settings > Apps > Advanced app settings > App execution aliases > turn off python.exe / python3.exe"

# --- 2. git ----------------------------------------------------------------
Write-Step "Checking for git"
if (-not (Get-Command git.exe -ErrorAction SilentlyContinue)) {
  Write-Warn2 "git not on PATH. Installing via winget..."
  winget install --id Git.Git -e --source winget `
    --accept-package-agreements --accept-source-agreements
  Refresh-Path
}
if (-not (Get-Command git.exe -ErrorAction SilentlyContinue)) {
  throw "git is still not available. Install it manually, open a NEW terminal, and re-run."
}
Write-Ok "$((Get-Command git.exe).Source) ($(git --version))"

# --- 3. submodules ---------------------------------------------------------
Write-Step "Initializing instrument submodules (apps\)"
git submodule update --init --recursive
Write-Ok "submodules in sync"
git submodule status

# --- 4. venv + install -----------------------------------------------------
Write-Step "Creating .venv and installing the package (with hardware+plot extras)"
if (-not (Test-Path ".\.venv\Scripts\python.exe")) {
  & $py -m venv .venv
  Write-Ok ".venv created"
} else {
  Write-Ok ".venv already exists"
}
$venvPy = Resolve-Path ".\.venv\Scripts\python.exe"
& $venvPy -m pip install --upgrade pip
# NOTE: extras are required even for --simulate (see .DESCRIPTION).
& $venvPy -m pip install -e ".[serial,visa,plot]"
Write-Ok "package installed"

# --- 5. smoke test ---------------------------------------------------------
if (-not $SkipSmokeTest) {
  Write-Step "Smoke test: pmu-validate --simulate amplitude"
  & ".\.venv\Scripts\pmu-validate.exe" --simulate amplitude
  Write-Ok "simulate run completed -- see results\ for CSV + PNG"
}

Write-Host "`nDone. Activate the environment in new terminals with:" -ForegroundColor Cyan
Write-Host "    .\.venv\Scripts\Activate.ps1" -ForegroundColor White
Write-Host "then run:  pmu-validate --simulate amplitude   |   pmu-validate-gui" -ForegroundColor White
