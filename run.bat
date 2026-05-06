@echo off
setlocal
cd /d "%~dp0"
if not exist .venv (
    py -3 -m venv .venv
    .venv\Scripts\pip install --upgrade pip
    .venv\Scripts\pip install -r requirements.txt
)
set PYTHONPATH=%~dp0src;%PYTHONPATH%
.venv\Scripts\python -m video_editor
endlocal
