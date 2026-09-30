# Builds dist\AutoReview\AutoReview.exe (onedir). config\ and scripts\ are copied NEXT to the exe so they stay editable.
# The person's own data (saved logins, database, results) lives in dist\AutoReview\data and is carried over a rebuild;
# the previous config is kept as config.bak-<time> in case it was edited in the app.
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$out = 'dist\AutoReview'
$keep = Join-Path $env:TEMP 'autoreview_data_keep'
$bak = $null
if (Test-Path $keep) { Remove-Item $keep -Recurse -Force }
if (Test-Path "$out\data") { Copy-Item "$out\data" -Destination $keep -Recurse -Force }
if (Test-Path "$out\config") { $bak = Join-Path $env:TEMP ('autoreview_config_' + (Get-Date -Format 'yyyyMMdd-HHmmss')); Copy-Item "$out\config" -Destination $bak -Recurse -Force }

python -m PyInstaller --noconfirm --clean --windowed --name AutoReview `
  --collect-all playwright --exclude-module PySide6.QtWebEngineCore --exclude-module PySide6.QtWebEngineWidgets --exclude-module PySide6.QtQuick `
  --exclude-module PySide6.Qt3DCore --exclude-module PySide6.QtMultimedia --exclude-module PySide6.QtPdf `
  --exclude-module matplotlib --exclude-module tkinter --exclude-module scipy `
  run_app.py
Copy-Item config -Destination "$out\config" -Recurse -Force
Copy-Item scripts -Destination "$out\scripts" -Recurse -Force
if (Test-Path $keep) { Copy-Item $keep -Destination "$out\data" -Recurse -Force }
if ($bak) { Copy-Item $bak -Destination "$out\config.bak" -Recurse -Force }
Write-Host "done: $out\AutoReview.exe"
