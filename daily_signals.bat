@echo off
REM ===== PStock - quét BO/BD Ichimoku cuối phiên và gửi Telegram =====
REM Được Task Scheduler gọi lúc 15:05 thứ 2 - thứ 6 (task "PStock Daily Signals").
REM Log: data\logs\daily_signals.log
cd /d "%~dp0"
chcp 65001 >nul
".venv\Scripts\python.exe" scripts\daily_signals.py %*
