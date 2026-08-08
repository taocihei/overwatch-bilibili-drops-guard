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

# The command-line checks above deliberately return before Tk starts. Launch the
# actual frozen application as well so a release cannot pass while missing the
# Tcl/Tk runtime. PyInstaller one-file mode creates a child process for the GUI,
# therefore the check watches every newly-created process with this EXE name.
$guiProcessName = [System.IO.Path]::GetFileNameWithoutExtension($versionedArtifact)
$existingGuiProcessIds = @(
  Get-Process -Name $guiProcessName -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty Id
)
$guiSmokeProcess = $null
$guiReady = $false
$guiWindowTitle = $null
try {
  $guiSmokeProcess = Start-Process -FilePath $versionedArtifact -PassThru
  $guiDeadline = [DateTime]::UtcNow.AddSeconds(30)
  while ([DateTime]::UtcNow -lt $guiDeadline) {
    Start-Sleep -Milliseconds 100
    $guiWindow = Get-Process -Name $guiProcessName -ErrorAction SilentlyContinue |
      Where-Object {
        $existingGuiProcessIds -notcontains $_.Id -and
        $_.MainWindowHandle -ne 0 -and
        $_.Responding -and
        $_.MainWindowTitle -like "*v$version"
      } |
      Select-Object -First 1
    if ($null -ne $guiWindow) {
      $guiReady = $true
      $guiWindowTitle = $guiWindow.MainWindowTitle
      break
    }
  }
  if (-not $guiReady) {
    throw "Packaged GUI smoke test failed: no responsive main window appeared within 30 seconds."
  }
  Write-Host "Packaged GUI smoke test OK: $guiWindowTitle"
}
finally {
  # Stop only processes created by this smoke test. Repeat briefly because the
  # one-file bootloader and its child can exit a fraction of a second apart.
  for ($attempt = 0; $attempt -lt 5; $attempt++) {
    $guiTestProcesses = @(
      Get-Process -Name $guiProcessName -ErrorAction SilentlyContinue |
        Where-Object { $existingGuiProcessIds -notcontains $_.Id }
    )
    if ($guiTestProcesses.Count -eq 0) {
      break
    }
    $guiTestProcesses |
      Sort-Object @{ Expression = { if ($_.MainWindowHandle -ne 0) { 0 } else { 1 } } } |
      ForEach-Object { Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Milliseconds 250
  }
}

Write-Host "Build complete: $artifact"
Write-Host "Release artifact: $versionedArtifact"
