# LearnMesh Enterprise Platform PowerShell Launcher
Write-Host "===================================================" -ForegroundColor Cyan
Write-Host "    Starting LearnMesh Enterprise Platform" -ForegroundColor Green
Write-Host "    'Correct once. Learn everywhere.'" -ForegroundColor Yellow
Write-Host "===================================================" -ForegroundColor Cyan
Write-Host ""

Set-Location $PSScriptRoot

if (-not (Test-Path ".\.venv\Scripts\python.exe")) {
    Write-Host "[ERROR] Virtual environment not found in .venv." -ForegroundColor Red
    Write-Host "Please create it using: python -m venv .venv; .\.venv\Scripts\pip install -r backend/requirements.txt"
    exit 1
}

Write-Host "[1/2] Opening Enterprise Command Center in your default browser..." -ForegroundColor Green
Start-Process "http://localhost:8000"

Write-Host "[2/2] Launching Uvicorn server on http://127.0.0.1:8000 ..." -ForegroundColor Green
Write-Host ""
Write-Host "===================================================" -ForegroundColor DarkGray
Write-Host "  Access URLs:" -ForegroundColor White
Write-Host "  - Enterprise Command Center: http://localhost:8000" -ForegroundColor Cyan
Write-Host "  - Swagger API Docs:         http://localhost:8000/docs" -ForegroundColor Cyan
Write-Host "  - Prometheus Metrics:       http://localhost:8000/metrics" -ForegroundColor Cyan
Write-Host "  - Health Check:             http://localhost:8000/health" -ForegroundColor Cyan
Write-Host "===================================================" -ForegroundColor DarkGray
Write-Host ""
Write-Host "Press CTRL+C to stop the server at any time." -ForegroundColor Yellow
Write-Host ""

& ".\.venv\Scripts\python.exe" -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
