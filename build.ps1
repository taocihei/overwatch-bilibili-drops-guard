$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

$artifact = Join-Path $PSScriptRoot "dist\OverwatchBiliDrops.exe"
if (Test-Path -LiteralPath $artifact) {
  Remove-Item -LiteralPath $artifact -Force
}

python -c "import _tkinter, PIL, PyInstaller, requests, selenium, tkinter; print('Build dependencies OK')"
if ($LASTEXITCODE -ne 0) {
  throw "Build dependencies are incomplete. Run: python -m pip install -r requirements.txt"
}

python -m pip check
if ($LASTEXITCODE -ne 0) {
  throw "Python dependency conflicts were detected."
}

$pythonRoot = python -c "import sys; print(sys.base_prefix)"
if ($LASTEXITCODE -ne 0) {
  throw "Unable to read the Python installation directory."
}
$version = python -c "from bili_drop_guard import __version__; print(__version__)"
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($version)) {
  throw "Unable to read the application version."
}
$version = $version.Trim()
$versionedArtifact = Join-Path $PSScriptRoot "dist\OverwatchBiliDrops-v$version.exe"
if (Test-Path -LiteralPath $versionedArtifact) {
  Remove-Item -LiteralPath $versionedArtifact -Force
}
$tclLibrary = Join-Path $pythonRoot "tcl\tcl8.6"
$tkLibrary = Join-Path $pythonRoot "tcl\tk8.6"

$env:TCL_LIBRARY = $tclLibrary
$env:TK_LIBRARY = $tkLibrary

python -m PyInstaller `
  --noconfirm `
  --clean `
  .\OverwatchBiliDrops.spec

if ($LASTEXITCODE -ne 0) {
  throw "PyInstaller failed with exit code $LASTEXITCODE."
}
if (-not (Test-Path -LiteralPath $artifact -PathType Leaf)) {
  throw "PyInstaller did not create the expected artifact: $artifact"
}
Copy-Item -LiteralPath $artifact -Destination $versionedArtifact -Force
if (-not (Test-Path -LiteralPath $versionedArtifact -PathType Leaf)) {
  throw "The versioned release artifact was not created: $versionedArtifact"
}

Write-Host "Build complete: $artifact"
Write-Host "Release artifact: $versionedArtifact"
