$ErrorActionPreference = "Stop"

Set-Location -Path $PSScriptRoot

$python = "D:\miniconda3\python.exe"
if (-not (Test-Path $python)) {
  $python = "python"
}

& $python -m uvicorn dashboard_backend.app:app --app-dir $PSScriptRoot --host 127.0.0.1 --port 8000
 
