@echo off
rem jobHunt - double-click to start. The first run sets everything up.
setlocal
cd /d "%~dp0"
title jobHunt

rem --- 1. Python 3.10 or newer --------------------------------------------------------
set "PY="
where py >nul 2>nul && py -3 -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>nul && set "PY=py -3"
if not defined PY (
  where python >nul 2>nul && python -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo jobHunt needs Python 3.10 or newer, and this computer does not have it yet.
  echo.
  echo  1. Download it from https://www.python.org/downloads/
  echo  2. In the installer, tick "Add python.exe to PATH" before clicking Install.
  echo  3. Double-click start.bat again.
  echo.
  pause
  exit /b 1
)

rem --- 2. A private environment for the app, created once ----------------------------
if not exist ".venv\Scripts\python.exe" (
  echo Setting up jobHunt for the first time. This takes a minute...
  %PY% -m venv .venv || goto failed
)

rem --- 3. Install, again only when pyproject.toml has changed -------------------------
fc /b pyproject.toml ".venv\fyj-installed.toml" >nul 2>nul
if errorlevel 1 (
  echo Installing...
  ".venv\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check -e ".[pdf]" || goto failed
  copy /y pyproject.toml ".venv\fyj-installed.toml" >nul
)

rem --- 4. Start the page -------------------------------------------------------------
".venv\Scripts\python.exe" -m jobs.ui %*
if errorlevel 1 goto failed
exit /b 0

:failed
echo.
echo jobHunt could not start. The message above says why.
echo If you need help, open an issue on the project's GitHub page and paste that message.
pause
exit /b 1
