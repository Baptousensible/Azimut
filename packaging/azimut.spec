# -*- mode: python ; coding: utf-8 -*-
"""Spécification PyInstaller d'Azimut (mode dossier par défaut).

Utilisation : python -m PyInstaller packaging/azimut.spec --noconfirm --clean
(ou simplement `python packaging/build.py`).
Variante fichier unique : variable d'environnement AZIMUT_ONEFILE=1.
Aucun chemin absolu : tout est calculé à partir de l'emplacement de ce fichier.
"""
from __future__ import annotations

import os
import platform
import re
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

# SPECPATH est fourni par PyInstaller (dossier du .spec) ; repli sur __file__.
ROOT = Path(globals().get("SPECPATH") or Path(__file__).resolve().parent).resolve().parent
sys.path.insert(0, str(ROOT))

APP_NAME = "Azimut"
ONEFILE = os.environ.get("AZIMUT_ONEFILE", "").strip() == "1"


def _version() -> str:
    """Lit __version__ sans importer le paquet."""
    text = (ROOT / "azimut" / "__init__.py").read_text(encoding="utf-8")
    found = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', text)
    return found.group(1) if found else "0.0.0"


def _platform_tag() -> str:
    """Même résultat que azimut.core.binaries.platform_tag()."""
    try:
        from azimut.core.binaries import platform_tag
        return platform_tag()
    except Exception:
        machine = platform.machine().lower()
        if sys.platform.startswith("win"):
            return "windows-x64"
        if sys.platform == "darwin":
            return "macos-arm64" if machine in ("arm64", "aarch64") else "macos-x64"
        return "linux-x64"


TAG = _platform_tag()
VERSION = _version()


def _icon() -> str | None:
    name = {"win32": "azimut.ico", "darwin": "azimut.icns"}.get(sys.platform)
    if name and (ROOT / "packaging" / name).is_file():
        return str(ROOT / "packaging" / name)
    return None


def _bundled_binaries() -> list[tuple[str, str]]:
    """bin/<tag>/ → bin/<tag>/ dans le paquet (rien si absent ou vide)."""
    src = ROOT / "bin" / TAG
    if not src.is_dir():
        print(f"[azimut.spec] bin/{TAG}/ absent : aucun binaire embarqué "
              "(Azimut utilisera les préférences ou le PATH).")
        return []
    files = [p for p in src.rglob("*") if p.is_file() and p.name not in (".gitkeep", "README.md")]
    if not files:
        print(f"[azimut.spec] bin/{TAG}/ est vide : aucun binaire embarqué.")
        return []
    print(f"[azimut.spec] {len(files)} fichier(s) de bin/{TAG}/ embarqué(s).")
    return [(str(src), f"bin/{TAG}")]


# Modules Qt inutilisés (PySide6.<nom>) : gain de taille important.
_QT_UNUSED = [
    "QtWebEngineCore", "QtWebEngineWidgets", "QtWebEngineQuick",
    "Qt3DCore", "Qt3DRender", "Qt3DInput", "Qt3DLogic", "Qt3DAnimation", "Qt3DExtras",
    "QtQml", "QtQuick", "QtQuickWidgets", "QtQuickControls2", "QtQuick3D",
    "QtMultimedia", "QtMultimediaWidgets",
    "QtCharts", "QtDataVisualization", "QtBluetooth", "QtNfc", "QtSensors", "QtSerialPort",
    "QtPdf", "QtPdfWidgets", "QtDesigner", "QtHelp", "QtTest", "QtSql",
    "QtLocation", "QtPositioning", "QtRemoteObjects", "QtScxml", "QtSpatialAudio",
    "QtTextToSpeech", "QtWebChannel", "QtWebSockets",
]
EXCLUDES = [f"PySide6.{name}" for name in _QT_UNUSED]
# Évite le conflit si d'autres liaisons Qt sont installées dans l'environnement.
EXCLUDES += ["PyQt5", "PyQt6", "PySide2"]

# Les fenêtres des modules sont importées par chaîne (load_window_class).
HIDDEN_IMPORTS = collect_submodules("azimut")

DATAS = [(str(ROOT / "azimut" / "resources"), "azimut/resources")] + _bundled_binaries()
ICON = _icon()

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=DATAS,
    hiddenimports=HIDDEN_IMPORTS,
    hookspath=[],
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
)
pyz = PYZ(a.pure)

if ONEFILE:
    exe = EXE(
        pyz, a.scripts, a.binaries, a.datas, [],
        name=APP_NAME,
        debug=False,
        strip=False,
        upx=False,
        runtime_tmpdir=None,
        console=False,
        icon=ICON,
    )
    target = exe
else:
    exe = EXE(
        pyz, a.scripts, [],
        exclude_binaries=True,
        name=APP_NAME,
        debug=False,
        strip=False,
        upx=False,
        console=False,
        icon=ICON,
    )
    coll = COLLECT(
        exe, a.binaries, a.datas,
        strip=False,
        upx=False,
        name=APP_NAME,
    )
    target = coll

if sys.platform == "darwin":
    app = BUNDLE(
        target,
        name=f"{APP_NAME}.app",
        icon=ICON,
        bundle_identifier="org.azimut.suite",
        version=VERSION,
        info_plist={
            "CFBundleName": APP_NAME,
            "CFBundleShortVersionString": VERSION,
            "NSHighResolutionCapable": True,
        },
    )
