@echo off
rem Run Music Manager from source (needs Python 3.11+ from python.org).
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
    py -3 -m venv .venv || python -m venv .venv
    .venv\Scripts\python -m pip install --upgrade pip
    .venv\Scripts\python -m pip install -r requirements.txt
)
if not exist vendor\ffmpeg\ffmpeg.exe (
    powershell -ExecutionPolicy Bypass -File scripts\get-ffmpeg.ps1
)
start "" .venv\Scripts\pythonw -m music_manager
