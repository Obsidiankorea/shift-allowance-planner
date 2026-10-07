@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" goto dependencies
where py >nul 2>nul
if errorlevel 1 goto plainpython
py -3 -m venv .venv
if errorlevel 1 goto failed
goto dependencies
:plainpython
python -m venv .venv
if errorlevel 1 goto failed
:dependencies
if exist ".venv\installed.ok" goto launch
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 goto failed
type nul > ".venv\installed.ok"
:launch
.venv\Scripts\python.exe app.py
if errorlevel 1 goto failed
exit /b 0
:failed
echo.
echo Failed. Install Python 3.11+ and check the message above.
pause
exit /b 1
