$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$BotEntry = Join-Path $ProjectRoot "bot.py"

if (-not (Test-Path $VenvPython)) {
    throw "Local virtual environment not found. Run scripts\setup-local.ps1 first."
}

& $VenvPython $BotEntry
