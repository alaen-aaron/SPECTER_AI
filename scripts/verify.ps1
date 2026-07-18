# SPECTER_AI verification toolkit — Windows PowerShell launcher.
#
# All check logic lives in scripts/doctor.py (stdlib-only Python) so it
# is defined exactly once; this script's only job is finding a Python
# interpreter on Windows and delegating to it.

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

$PythonBin = $null
foreach ($candidate in @("python", "python3", "py")) {
    if (Get-Command $candidate -ErrorAction SilentlyContinue) {
        $PythonBin = $candidate
        break
    }
}

if (-not $PythonBin) {
    Write-Error "No python/python3/py interpreter found on PATH. Install Python 3.12+ and re-run scripts/verify.ps1"
    exit 1
}

$doctorPath = Join-Path $ScriptDir "doctor.py"
& $PythonBin $doctorPath @args
exit $LASTEXITCODE
