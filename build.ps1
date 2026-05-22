$ErrorActionPreference = "Stop"

python -m PyInstaller `
  --noconfirm `
  --collect-all selenium `
  --add-data "assets/app.ico;assets" `
  --icon "assets/app.ico" `
  --onefile `
  --windowed `
  --name OverwatchBiliDrops `
  app.py

Write-Host "打包完成：dist\OverwatchBiliDrops.exe"
