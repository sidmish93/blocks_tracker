@echo off
REM Launches the Block & Bulk Deals Tracker and opens it in the default browser.

cd /d "%~dp0"

python -c "import fastapi, uvicorn, openpyxl, curl_cffi" 2>nul
if errorlevel 1 (
    echo Installing dependencies, this only happens once...
    python -m pip install -r requirements.txt || goto :failed
)

start "" http://127.0.0.1:8765/
python -m uvicorn app.main:app --host 127.0.0.1 --port 8765
goto :eof

:failed
echo.
echo Could not install dependencies. Make sure Python 3.10 or newer is installed.
pause
