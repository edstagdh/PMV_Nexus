@echo off
rem Starts PMV Nexus. Safe to double-click or call from any folder.
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" run.py %*
) else (
    py run.py %*
)
if errorlevel 1 (
    echo.
    echo PMV Nexus exited with an error.
    pause
)
