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

rem ---- 1. Python 3.12 to 3.14 (the newest first) ---------------------------
set "PY="
for %%v in (3.14 3.13 3.12) do if not defined PY py -%%v -c "import sys" >nul 2>&1 && set "PY=py -%%v"
if not defined PY python -c "import sys; sys.exit(not (3, 12) <= sys.version_info[:2] <= (3, 14))" >nul 2>&1 && set "PY=python"
if not defined PY (
    echo  Python 3.12 to 3.14 was not found.
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
rem Face models (3.12.0, the Faces tab of Management): downloaded once, checked by SHA-256
"%VPY%" -m Tools.get_face_models >nul 2>&1 || echo        The face models could not be downloaded now: run  .venv\Scripts\python.exe -m Tools.get_face_models  later.

rem ---- 2b. An update: what it changes in the library (3.12.1, like install.sh) --
rem Exit codes of Tools.upgrade_plan: 0 nothing to change, 3 steps to run, 4 the library was used
rem by a newer version, 1 the check itself failed (then the install goes on, as Setup does).
if not exist config.yaml goto :plan_done
echo.
"%VPY%" -m Tools.upgrade_plan
set "PLAN=%errorlevel%"
if "%PLAN%"=="4" goto :too_new
if not "%PLAN%"=="3" goto :plan_done
choice /c YN /m "       The library is changed when the app starts. Continue"
if errorlevel 2 goto :not_now
:plan_done
rem 3.13.0: a backup of the library first (database, settings, own photos, covers; Server Settings - Backups)
if not exist config.yaml goto :backup_done
set /p APPVER=<VERSION
"%VPY%" -m Tools.pre_install_backup --to %APPVER%
if not errorlevel 1 goto :backup_done
if errorlevel 2 goto :backup_done
choice /c YN /n /m "       No backup could be made (see above). Go on without it? [Y/N] "
if errorlevel 2 goto :not_now
:backup_done

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

:too_new
echo.
echo  Stopped: nothing was changed. Use the newer version of the app with this library.
pause
exit /b 1

:not_now
echo.
echo  Stopped. The library wasn't changed: the app upgrades it when it next starts.
pause
goto :end

:fail
echo.
echo  The install stopped because of the error above.
pause
exit /b 1

:end
endlocal
