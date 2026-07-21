# Build the PMU Validation Bench as a standalone Windows app (no Python needed
# on the target machine) and drop a shortcut on the Desktop.
#
#   powershell -ExecutionPolicy Bypass -File build_exe.ps1            # final app (windowed)
#   powershell -ExecutionPolicy Bypass -File build_exe.ps1 -Debug     # console build for diagnosing
#
# Output: dist\PMU Validation Bench\PMU Validation Bench.exe  (a self-contained
# folder). The four instrument apps under apps\ are imported dynamically by
# pmu_validation._vendor, so they are pulled in explicitly with --paths +
# --collect-submodules / --hidden-import below.

param([switch]$Debug)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $repo

$py = "C:\Users\bdzou\AppData\Local\Python\pythoncore-3.14-64\python.exe"
if (-not (Test-Path $py)) { $py = "python" }

# Ensure PyInstaller is available.
& $py -c "import PyInstaller" 2>$null
if ($LASTEXITCODE -ne 0) { & $py -m pip install pyinstaller }

$name = "PMU Validation Bench"
$mode = if ($Debug) { "--console" } else { "--windowed" }

$pyArgs = @(
    "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", $mode,
    "--name", $name,
    # vendored instrument apps (dynamic imports in pmu_validation/_vendor.py)
    "--paths", "apps\scope",
    "--paths", "apps\hp34401",
    "--paths", "apps\hp3325",
    "--paths", "apps\power-brick\host",
    "--hidden-import", "mso8104a",
    "--hidden-import", "measurements",
    "--hidden-import", "hp3325b_driver",
    "--collect-submodules", "hp34401",
    "--collect-submodules", "upmu",
    "--collect-submodules", "pmu_validation",
    # transports
    "--collect-submodules", "serial",
    "--hidden-import", "serial.tools.list_ports",
    "--collect-all", "pyvisa_py",
    "--collect-submodules", "pyvisa",
    "--copy-metadata", "pyvisa",
    "--copy-metadata", "pyvisa_py"
)

# Ship the current calibration as a first-run default (read-only in the bundle).
if (Test-Path "pmu_validation\calibration.json") {
    $pyArgs += @("--add-data", "pmu_validation\calibration.json;.")
}

$pyArgs += "pmu_gui.py"

Write-Host "Building '$name' ($mode)..." -ForegroundColor Cyan
& $py @pyArgs
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed (exit $LASTEXITCODE)" }

$exe = Join-Path $repo "dist\$name\$name.exe"
Write-Host ""
Write-Host "Build complete: $exe" -ForegroundColor Green

# Create / refresh a Desktop shortcut.
$desktop = [Environment]::GetFolderPath("Desktop")
$lnk = Join-Path $desktop "$name.lnk"
$ws = New-Object -ComObject WScript.Shell
$sc = $ws.CreateShortcut($lnk)
$sc.TargetPath = $exe
$sc.WorkingDirectory = (Split-Path -Parent $exe)
$sc.Description = "Elastic Energy micro-PMU validation bench"
$sc.Save()
Write-Host "Desktop shortcut: $lnk" -ForegroundColor Green
