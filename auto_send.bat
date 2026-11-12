@echo off
rem Bat/tat gui Telegram tu dong khong can mo app.  auto_send.bat [on|off] [board|daily]
cd /d "%~dp0"
".venv\Scripts\python.exe" scripts\auto_send.py %*
if "%~1"=="" pause
