$ErrorActionPreference = "Stop"

python -m PyInstaller `
  --noconfirm `
  --onefile `
  --windowed `
  --name OverwatchBiliDrops `
  app.py

Write-Host "打包完成：dist\OverwatchBiliDrops.exe"
