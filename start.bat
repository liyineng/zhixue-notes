@echo off
setlocal
cd /d "%~dp0"
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)" >nul 2>&1
if not errorlevel 1 (
  py -3 launch.py %*
  goto finish
)
python -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)" >nul 2>&1
if not errorlevel 1 (
  python launch.py %*
  goto finish
)
echo Python 3.11 or newer was not found.
echo Install Python from https://www.python.org/downloads/windows/
echo Then extract this ZIP and double-click start.bat again.
if "%~1"=="" pause
exit /b 1
:finish
set "LAUNCH_EXIT=%errorlevel%"
if not "%LAUNCH_EXIT%"=="0" (
  echo Startup failed. Read the error above. Do not delete the data folder.
  if "%~1"=="" pause
)
exit /b %LAUNCH_EXIT%
