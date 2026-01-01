$ErrorActionPreference = "Stop"

Set-Location -Path (Split-Path -Parent $PSScriptRoot)

$python = "D:\miniconda3\python.exe"
if (-not (Test-Path $python)) {
  $python = "python"
}

& $python -m uvicorn dashboard_backend.app:app --host 127.0.0.1 --port 8000

