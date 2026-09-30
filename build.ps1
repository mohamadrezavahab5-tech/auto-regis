# Builds dist\AutoReview\AutoReview.exe (onedir). config\ and scripts\ are copied NEXT to the exe so they stay editable.
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
python -m PyInstaller --noconfirm --clean --windowed --name AutoReview `
  --exclude-module PySide6.QtWebEngineCore --exclude-module PySide6.QtWebEngineWidgets --exclude-module PySide6.QtQuick `
  --exclude-module PySide6.Qt3DCore --exclude-module PySide6.QtMultimedia --exclude-module PySide6.QtPdf `
  --exclude-module matplotlib --exclude-module tkinter --exclude-module scipy `
  run_app.py
Copy-Item config -Destination dist\AutoReview\config -Recurse -Force
Copy-Item scripts -Destination dist\AutoReview\scripts -Recurse -Force
Write-Host "done: dist\AutoReview\AutoReview.exe"
