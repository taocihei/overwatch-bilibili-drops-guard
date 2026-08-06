$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

$artifact = Join-Path $PSScriptRoot "dist\OverwatchBiliDrops.exe"
if (Test-Path -LiteralPath $artifact) {
  Remove-Item -LiteralPath $artifact -Force
}

python -c "import _tkinter, PIL, PyInstaller, qrcode, requests, selenium, tkinter, wasmtime; print('Build dependencies OK')"
if ($LASTEXITCODE -ne 0) {
  throw "Build dependencies are incomplete. Run: python -m pip install -r requirements.txt"
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

# Fail the release build if PyInstaller stops embedding the Windows high-DPI
# manifest. This catches the exact regression that makes 4K displays blurry.
$manifestVerifier = @'
import sys
from PyInstaller.utils.win32.winresource import get_resources

resources = get_resources(sys.argv[1], types=[24])
manifests = [
    payload
    for names in resources.values()
    for languages in names.values()
    for payload in languages.values()
]
text = b"\n".join(manifests).decode("utf-8", "replace").lower()
required = (
    "<dpiaware xmlns=\"http://schemas.microsoft.com/smi/2005/windowssettings\">false</dpiaware>",
    "<dpiawareness xmlns=\"http://schemas.microsoft.com/smi/2016/windowssettings\">unaware</dpiawareness>",
    "<gdiscaling xmlns=\"http://schemas.microsoft.com/smi/2017/windowssettings\">true</gdiscaling>",
)
missing = [entry for entry in required if entry not in text]
if missing:
    raise SystemExit("Packaged high-DPI manifest verification failed: " + ", ".join(missing))
print("Packaged high-DPI manifest OK")
'@
$manifestVerifier | python - $versionedArtifact
if ($LASTEXITCODE -ne 0) {
  throw "Packaged high-DPI manifest verification failed."
}

$selfTest = Start-Process -FilePath $versionedArtifact -ArgumentList "--self-test-skynet" -Wait -PassThru
if ($selfTest.ExitCode -ne 0) {
  throw "Packaged Skynet/WASM self-test failed with exit code $($selfTest.ExitCode)."
}
$qrSelfTest = Start-Process -FilePath $versionedArtifact -ArgumentList "--self-test-sponsor-qr" -Wait -PassThru
if ($qrSelfTest.ExitCode -ne 0) {
  throw "Packaged sponsor QR self-test failed with exit code $($qrSelfTest.ExitCode)."
}

Write-Host "Build complete: $artifact"
Write-Host "Release artifact: $versionedArtifact"
