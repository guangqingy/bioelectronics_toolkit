@echo off
setlocal
cd /d "%~dp0\.."

echo DataProcess Web installer
echo This will create a local .venv folder and install dependencies.
echo.

set "PYTHON_CMD="
where py >nul 2>nul
if %ERRORLEVEL%==0 (
  py -3.12 -c "import sys; raise SystemExit(0 if (3, 10) <= sys.version_info[:2] <= (3, 12) else 1)" >nul 2>nul && set "PYTHON_CMD=py -3.12"
  if not defined PYTHON_CMD py -3.11 -c "import sys; raise SystemExit(0 if (3, 10) <= sys.version_info[:2] <= (3, 12) else 1)" >nul 2>nul && set "PYTHON_CMD=py -3.11"
  if not defined PYTHON_CMD py -3.10 -c "import sys; raise SystemExit(0 if (3, 10) <= sys.version_info[:2] <= (3, 12) else 1)" >nul 2>nul && set "PYTHON_CMD=py -3.10"
)

if not defined PYTHON_CMD (
  where python >nul 2>nul
  if %ERRORLEVEL%==0 (
    python -c "import sys; raise SystemExit(0 if (3, 10) <= sys.version_info[:2] <= (3, 12) else 1)" >nul 2>nul && set "PYTHON_CMD=python"
  )
)

if not defined PYTHON_CMD (
  echo Could not find Python 3.10, 3.11, or 3.12.
  echo Please install Python 3.12 from https://www.python.org/downloads/
  echo.
  pause
  exit /b 2
)

%PYTHON_CMD% easy_start\setup_env.py --run
set "STATUS=%ERRORLEVEL%"
echo.
if "%STATUS%"=="0" (
  echo DataProcess closed.
) else (
  echo Installer exited with code %STATUS%.
)
pause
exit /b %STATUS%
