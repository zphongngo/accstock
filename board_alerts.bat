@echo off
REM ===== PStock - bản tin Cổ phiếu mạnh / yếu / Quan Tâm từ bảng điện, gửi Telegram =====
REM Lịch gửi tự động hiện do app xử lý; task Task Scheduler cũ không có --once nên sẽ không gửi.
REM Log: data\logs\board_alerts.log
cd /d "%~dp0"
chcp 65001 >nul
".venv\Scripts\python.exe" scripts\board_alerts.py %*
