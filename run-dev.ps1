param(
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"
$pythonPath = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
$backendPath = Join-Path $PSScriptRoot "backend"

if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw "Virtual environment missing: $pythonPath"
}

Push-Location $PSScriptRoot
try {
    & $pythonPath -m uvicorn backend.main:app --host 127.0.0.1 --port $Port --reload --reload-dir $backendPath
}
finally {
    Pop-Location
}
