# Builds dist\MusicManager\MusicManager.exe (with ffmpeg bundled).
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
if (-not (Test-Path "vendor\ffmpeg\ffmpeg.exe")) { & "$PSScriptRoot\get-ffmpeg.ps1" }
python -m PyInstaller --noconfirm --clean --windowed --name MusicManager `
    --add-data "vendor\ffmpeg;ffmpeg" `
    --add-data "music_manager\assets;music_manager\assets" `
    --icon "music_manager\assets\icon.ico" `
    music_manager\__main__.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
