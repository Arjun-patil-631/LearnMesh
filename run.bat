@echo off
title LearnMesh Enterprise Platform
echo ===================================================
echo     Starting LearnMesh Enterprise Platform
echo     "Correct once. Learn everywhere."
echo ===================================================
echo.

cd /d "%~dp0"

echo [1/3] Checking Python virtual environment...
if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Virtual environment not found in .venv.
    echo Please run: python -m venv .venv ^&^& .venv\Scripts\pip install -r backend\requirements.txt
    pause
    exit /b 1
)

echo [2/3] Opening Enterprise Command Center in your browser...
start http://localhost:8000

echo [3/3] Launching Uvicorn server on http://127.0.0.1:8000 ...
echo.
echo ===================================================
echo   Access URLs:
echo   - Enterprise Command Center: http://localhost:8000
echo   - Swagger API Docs:         http://localhost:8000/docs
echo   - Prometheus Metrics:       http://localhost:8000/metrics
echo   - Health Check:             http://localhost:8000/health
echo ===================================================
echo.
echo Press CTRL+C to stop the server at any time.
echo.

.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
pause
