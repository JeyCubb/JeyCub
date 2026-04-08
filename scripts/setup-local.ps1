param(
    [switch]$Reinstall
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VenvPath = Join-Path $ProjectRoot ".venv"
$VenvPython = Join-Path $VenvPath "Scripts\python.exe"

function Get-PythonLauncher {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        return @{
            Command = "py"
            Args = @("-3")
        }
    }
    if (Get-Command python -ErrorAction SilentlyContinue) {
        return @{
            Command = "python"
            Args = @()
        }
    }
    throw "Python was not found. Install Python first, then rerun this script."
}

if ($Reinstall -and (Test-Path $VenvPath)) {
    Remove-Item -LiteralPath $VenvPath -Recurse -Force
}

if (-not (Test-Path $VenvPython)) {
    $PythonLauncher = Get-PythonLauncher
    & $PythonLauncher.Command @($PythonLauncher.Args) -m venv $VenvPath
}

& $VenvPython -m pip install --upgrade pip
& $VenvPython -m pip install -r (Join-Path $ProjectRoot "requirements.txt")

Write-Host ""
Write-Host "Local bot environment is ready."
Write-Host "Use this to run the bot:"
Write-Host "  $ProjectRoot\scripts\run-local.ps1"
