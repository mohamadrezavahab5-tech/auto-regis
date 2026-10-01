# Builds the installer:  dist\AutoReview-Setup-<version>.exe
#   1. icon + version info          (installer\make_assets.py -> build\)
#   2. the app, one folder           (build\app\AutoReview\AutoReview.exe, config\ and scripts\ inside its _internal folder)
#   3. the payload                   (build\payload.zip = that folder)
#   4. the setup, one file           (dist\AutoReview-Setup-<version>.exe, carries the payload)
# The person's data is never part of a build: it lives in %LOCALAPPDATA%\AutoReview on each PC.
# Paths are absolute: PyInstaller resolves relative data paths from the spec folder (build\), not from here.
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$root = $PSScriptRoot
$version = (python -c "from autoreview.version import __version__; print(__version__)").Trim()
New-Item -ItemType Directory -Force "$root\build", "$root\dist" | Out-Null

python "$root\installer\make_assets.py"
if ($LASTEXITCODE -ne 0) { throw "assets failed" }

$notNeeded = @('tkinter', 'matplotlib', 'scipy', 'numpy', 'pandas', 'playwright', 'IPython', 'PIL',
  'PySide6.Qt3DCore', 'PySide6.Qt3DRender', 'PySide6.QtCharts', 'PySide6.QtDataVisualization', 'PySide6.QtGraphs',
  'PySide6.QtMultimedia', 'PySide6.QtMultimediaWidgets', 'PySide6.QtBluetooth', 'PySide6.QtNfc', 'PySide6.QtSensors',
  'PySide6.QtSerialPort', 'PySide6.QtTest', 'PySide6.QtDesigner', 'PySide6.QtHelp', 'PySide6.QtSql', 'PySide6.QtScxml',
  'PySide6.QtRemoteObjects', 'PySide6.QtTextToSpeech', 'PySide6.QtHttpServer', 'PySide6.QtSpatialAudio', 'PySide6.QtQuick3D')
$excludeApp = $notNeeded | ForEach-Object { '--exclude-module'; $_ }

python -m PyInstaller --noconfirm --clean --windowed --name AutoReview `
  --icon "$root\build\autoreview.ico" --version-file "$root\build\version_app.txt" `
  --add-data "$root\config;config" --add-data "$root\scripts;scripts" `
  --distpath "$root\build\app" --workpath "$root\build\work-app" --specpath "$root\build" `
  @excludeApp "$root\run_app.py"
if ($LASTEXITCODE -ne 0) { throw "app build failed" }

python "$root\installer\pack_payload.py"
if ($LASTEXITCODE -ne 0) { throw "payload failed" }

$excludeSetup = ($notNeeded + @('PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets', 'PySide6.QtQuick', 'PySide6.QtQml',
  'PySide6.QtNetwork', 'PySide6.QtWebChannel', 'PySide6.QtPositioning', 'PySide6.QtPdf', 'httpx', 'openpyxl', 'xlsxwriter')) |
  ForEach-Object { '--exclude-module'; $_ }
python -m PyInstaller --noconfirm --clean --onefile --windowed --name "AutoReview-Setup-$version" `
  --icon "$root\build\autoreview.ico" --version-file "$root\build\version_setup.txt" `
  --add-data "$root\build\payload.zip;." `
  --distpath "$root\dist" --workpath "$root\build\work-setup" --specpath "$root\build" `
  --paths "$root" @excludeSetup "$root\installer\setup_app.py"
if ($LASTEXITCODE -ne 0) { throw "setup build failed" }
Write-Host "done: dist\AutoReview-Setup-$version.exe"
