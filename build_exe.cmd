@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run start_windows.cmd once first to create .venv.
  pause
  exit /b 1
)
.venv\Scripts\python.exe build_exe.py
if errorlevel 1 (
  echo Build failed. See the messages above.
  pause
  exit /b 1
)
pause
