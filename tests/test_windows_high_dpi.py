from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "assets" / "windows_app.manifest"
SPEC = ROOT / "OverwatchBiliDrops.spec"
BUILD_SCRIPT = ROOT / "build.ps1"


def _manifest_text(namespace: str, local_name: str) -> str:
    root = ET.parse(MANIFEST).getroot()
    node = root.find(f".//{{{namespace}}}{local_name}")
    assert node is not None, f"missing manifest element: {local_name}"
    return (node.text or "").strip().lower()


def test_manifest_enables_windows_gdi_high_dpi_scaling() -> None:
    assert _manifest_text(
        "http://schemas.microsoft.com/SMI/2005/WindowsSettings", "dpiAware"
    ) == "false"
    assert _manifest_text(
        "http://schemas.microsoft.com/SMI/2016/WindowsSettings", "dpiAwareness"
    ) == "unaware"
    assert _manifest_text(
        "http://schemas.microsoft.com/SMI/2017/WindowsSettings", "gdiScaling"
    ) == "true"


def test_pyinstaller_embeds_the_high_dpi_manifest() -> None:
    spec = SPEC.read_text(encoding="utf-8")
    assert "manifest='assets\\\\windows_app.manifest'" in spec


def test_pyinstaller_requires_complete_tcl_tk_runtime() -> None:
    spec = SPEC.read_text(encoding="utf-8")
    assert "required_tk_resources" in spec
    assert "missing_tk_resources" in spec
    assert "Missing required Tcl/Tk packaging resources" in spec


def test_release_build_launches_and_cleans_up_packaged_gui() -> None:
    script = BUILD_SCRIPT.read_text(encoding="utf-8")
    assert "$guiSmokeProcess" in script
    assert "MainWindowHandle -ne 0" in script
    assert "$_.Responding" in script
    assert "Packaged GUI smoke test" in script
    assert "Stop-Process" in script
