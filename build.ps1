$ErrorActionPreference = "Stop"

$pythonRoot = python -c "import sys; print(sys.base_prefix)"
$tclLibrary = Join-Path $pythonRoot "tcl\tcl8.6"
$tkLibrary = Join-Path $pythonRoot "tcl\tk8.6"
$tkinterLib = Join-Path $pythonRoot "Lib\tkinter"
$tkinterPyd = Join-Path $pythonRoot "DLLs\_tkinter.pyd"
$tclDll = Join-Path $pythonRoot "DLLs\tcl86t.dll"
$tkDll = Join-Path $pythonRoot "DLLs\tk86t.dll"

$env:TCL_LIBRARY = $tclLibrary
$env:TK_LIBRARY = $tkLibrary

python -m PyInstaller `
  --noconfirm `
  --clean `
  --collect-all selenium `
  --hidden-import _tkinter `
  --add-data "assets/app.ico;assets" `
  --add-data "$tclLibrary;_tcl_data" `
  --add-data "$tkLibrary;_tk_data" `
  --add-data "$tkinterLib;tkinter" `
  --add-binary "$tkinterPyd;." `
  --add-binary "$tclDll;." `
  --add-binary "$tkDll;." `
  --icon "assets/app.ico" `
  --onefile `
  --windowed `
  --name OverwatchBiliDrops `
  app.py

Write-Host "打包完成：dist\OverwatchBiliDrops.exe"
