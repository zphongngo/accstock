@echo off
REM ===== PStock - Windows launcher =====
cd /d "%~dp0"
chcp 65001 >nul

where python >nul 2>nul || (echo [ERROR] Python not found. Install Python 3.10+ and tick "Add to PATH". & pause & exit /b 1)

if not exist ".venv\Scripts\python.exe" (
    echo [1/3] Creating virtual environment...
    python -m venv .venv || (pause & exit /b 1)
)

echo [2/3] Installing / updating packages...
".venv\Scripts\python.exe" -m pip install --upgrade pip -q
".venv\Scripts\python.exe" -m pip install -r requirements.txt -q || (pause & exit /b 1)

if not exist ".env" (
    copy ".env.example" ".env" >nul
    echo [!] Created .env from .env.example - please fill in your keys, then run again.
    notepad .env
    pause
    exit /b 0
)

set "SRV_HOST=127.0.0.1"
set "SRV_PORT=8000"
for /f "usebackq tokens=1,2" %%H in (`.venv\Scripts\python.exe -c "from app.core.config import get_settings as g; s=g(); print(s.APP_HOST, s.APP_PORT)"`) do set "SRV_HOST=%%H" & set "SRV_PORT=%%I"

echo [3/3] Starting server, open:
".venv\Scripts\python.exe" -c "from app.core.netaddr import open_urls; [print('      ' + u) for u in open_urls(r'%SRV_HOST%', %SRV_PORT%)]"
start "" http://127.0.0.1:%SRV_PORT%
".venv\Scripts\python.exe" -m uvicorn app.main:app --host "%SRV_HOST%" --port "%SRV_PORT%"
pause
