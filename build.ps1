$ErrorActionPreference = "Stop"

$pythonRoot = python -c "import sys; print(sys.base_prefix)"
$tclLibrary = Join-Path $pythonRoot "tcl\tcl8.6"
$tkLibrary = Join-Path $pythonRoot "tcl\tk8.6"

$env:TCL_LIBRARY = $tclLibrary
$env:TK_LIBRARY = $tkLibrary

python -m PyInstaller `
  --noconfirm `
  --clean `
  .\OverwatchBiliDrops.spec

Write-Host "打包完成：dist\OverwatchBiliDrops.exe"
