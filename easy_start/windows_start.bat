@echo off
setlocal
cd /d "%~dp0\.."

if not exist ".venv\Scripts\python.exe" (
  echo Local environment was not found. Running installer first.
  echo.
  call easy_start\windows_install_and_run.bat
  exit /b %ERRORLEVEL%
)

echo Starting DataProcess Web...
echo Leave this window open while using the app.
echo.
.venv\Scripts\python.exe web_app.py
set "STATUS=%ERRORLEVEL%"
echo.
echo DataProcess closed.
pause
exit /b %STATUS%
