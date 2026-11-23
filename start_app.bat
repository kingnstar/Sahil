@echo off
setlocal
where python >nul 2>nul
if errorlevel 1 (
  echo Python 3.10 or later is required. Install Python and try again.
  pause
  exit /b 1
)
python -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)"
if errorlevel 1 (
  echo Python 3.10 or later is required.
  pause
  exit /b 1
)
python -m pip install -r requirements.txt
if errorlevel 1 (
  echo Dependency installation failed.
  pause
  exit /b 1
)
start "" "http://127.0.0.1:5000"
python flask_app.py
endlocal
