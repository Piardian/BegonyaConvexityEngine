@echo off
chcp 65001 >nul
title Bagimsiz Matematiksel Bot (Convexity Engine) - 7/24 Canli Daemon
cd /d "%~dp0"

:loop
echo ============================================================================
echo [%date% %time%] Bagimsiz Matematiksel Bot (7/24 Canli Daemon) Baslatiliyor...
echo ============================================================================
python execution\lifecycle_daemon.py --live --daemon --run-now
echo [%date% %time%] Surec beklenmedik sekilde durdu! 10 saniye icinde yeniden baslatiliyor...
timeout /t 10 /nobreak >nul
goto loop
