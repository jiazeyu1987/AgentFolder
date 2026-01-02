$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -Path $root

function Stop-Port($port) {
  $conns = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
  if (-not $conns) { return }
  $pids = $conns | Select-Object -ExpandProperty OwningProcess -Unique
  foreach ($pid in $pids) {
    try {
      Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue
    } catch {}
  }
}

Write-Host "Stopping old dashboard processes (ports 8000/5173)..." -ForegroundColor Yellow
Stop-Port 8000
Stop-Port 5173

Write-Host "Starting backend (uvicorn on :8000)..." -ForegroundColor Green
Start-Process -FilePath "powershell" -ArgumentList @(
  "-ExecutionPolicy", "Bypass",
  "-NoProfile",
  "-File", (Join-Path $root "run_backend.ps1")
) -WorkingDirectory $root -WindowStyle Normal | Out-Null

Start-Sleep -Seconds 1

Write-Host "Starting frontend (vite on :5173)..." -ForegroundColor Green
Start-Process -FilePath "powershell" -ArgumentList @(
  "-ExecutionPolicy", "Bypass",
  "-NoProfile",
  "-File", (Join-Path $root "dashboard_ui\\run_frontend.ps1")
) -WorkingDirectory $root -WindowStyle Normal | Out-Null

Write-Host "Done. UI: http://127.0.0.1:5173  API: http://127.0.0.1:8000" -ForegroundColor Cyan
