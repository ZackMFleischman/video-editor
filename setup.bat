@echo off
setlocal
cd /d "%~dp0"
py -3 -m venv .venv
.venv\Scripts\pip install --upgrade pip
.venv\Scripts\pip install -r requirements.txt
echo.
echo Setup complete. Run "run.bat" to start the editor.
echo.
echo Reminder: install FFmpeg and add it to PATH:
echo   winget install Gyan.FFmpeg
endlocal
