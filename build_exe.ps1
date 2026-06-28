$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    python -m venv .venv
}

.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt pyinstaller
.\.venv\Scripts\python.exe -m PyInstaller --noconsole --onefile --clean --name HeadStage --icon "assets\headstage.ico" --add-data "assets;assets" --collect-all cv2 --collect-all mediapipe --hidden-import pystray._win32 headstage.py

Write-Host "Built: $here\dist\HeadStage.exe"
