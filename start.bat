@echo off
title Gnomewatch
cd /d "%~dp0"
set SENTINEL_SUPERVISED=1
set TRIES=0
:run
"%LOCALAPPDATA%\Programs\Python\Python312\python.exe" sentinel.py
if errorlevel 1 goto crashed
goto done
:crashed
set /a TRIES+=1
if %TRIES% GTR 5 goto done
echo Gnomewatch stopped unexpectedly - starting it again...
timeout /t 3 /nobreak >nul
goto run
:done
echo.
echo Gnomewatch stopped.
pause
