$ErrorActionPreference = "Stop"

python -m PyInstaller `
  --noconfirm `
  --collect-all selenium `
  --onefile `
  --windowed `
  --name OverwatchBiliDrops `
  app.py

Write-Host "打包完成：dist\OverwatchBiliDrops.exe"
