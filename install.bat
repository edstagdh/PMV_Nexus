@echo off
rem First install on Windows (2.25.0): Python packages in .venv,
rem config.yaml with your folders (2.25.1) and port -- or everything from a backup zip.
rem Safe to run again: it only installs what's missing and updates the packages.
setlocal EnableExtensions
cd /d "%~dp0"
title PMV Nexus - install
echo.
echo  PMV Nexus - install
echo  ============================
echo.

rem ---- 1. Python 3.10 or newer ------------------------------------------
set "PY="
py -3 -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>&1 && set "PY=py -3"
if not defined PY python -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>&1 && set "PY=python"
if not defined PY (
    echo  Python 3.10 or newer was not found.
    echo  Install it from https://www.python.org/downloads/ - tick "Add python.exe to PATH" -
    echo  then run install.bat again.
    goto :fail
)
echo  [1/4] Python found:
%PY% --version

rem ---- 2. Virtual environment + packages ---------------------------------
if not exist ".venv\Scripts\python.exe" (
    echo  [2/4] Creating the virtual environment in .venv ...
    %PY% -m venv .venv || goto :fail
) else (
    echo  [2/4] Using the existing .venv
)
set "VPY=.venv\Scripts\python.exe"
"%VPY%" -m pip install --upgrade pip --quiet
echo        Installing the Python packages - this takes a few minutes the first time ...
"%VPY%" -m pip install -r requirements.txt --quiet || goto :fail

rem ---- 3. ffmpeg ---------------------------------------------------------
where ffmpeg >nul 2>&1
if errorlevel 1 (
    echo  [3/4] ffmpeg was NOT found on PATH. Downloads of some videos, re-encoding and previews need it:
    echo        winget install Gyan.FFmpeg   - then open a new window. The app starts without it.
) else (
    echo  [3/4] ffmpeg found
)

rem ---- 4. From a backup, or a new setup ----------------------------------
echo.
echo  [4/4] Set up from a backup?
echo        A backup zip from the app's Backups page brings back the library, users, playlists and
echo        settings - and the port the backup's config.yaml uses. Videos aren't in backups: copy
echo        your video folder over yourself.
set "BACKUP="
set /p "BACKUP=       Backup zip (drag the file here, or just press Enter for a new setup): "
if defined BACKUP set "BACKUP=%BACKUP:"=%"
if defined BACKUP goto :restore

if exist config.yaml (
    echo        config.yaml already exists - keeping it. Check its folders:
    "%VPY%" -m Tools.first_install --folders || goto :fail
    goto :start
)
"%VPY%" -m Tools.first_install --folders || goto :fail
echo.
set "PORT="
set /p "PORT=       Port for the app (Enter = 8676): "
if not defined PORT set "PORT=8676"
"%VPY%" -m Tools.first_install --port %PORT% || goto :fail
goto :start

:restore
"%VPY%" -m Tools.first_install --check-backup "%BACKUP%" || goto :fail
echo.
echo  Installed. The backup is put back when the app starts: it may ask for the backup's password,
echo  then shows the folders from the backup's settings - point them at this computer's folders
echo  (e.g. where you copied the videos). Files it replaces are kept as *.pre-restore.
echo.
pause
"%VPY%" run.py --restore "%BACKUP%"
if errorlevel 1 pause
goto :end

:start
echo.
echo  Installed. Start the app any time with start.bat.
choice /c YN /m " Start it now"
if errorlevel 2 goto :end
call start.bat
goto :end

:fail
echo.
echo  The install stopped because of the error above.
pause
exit /b 1

:end
endlocal
