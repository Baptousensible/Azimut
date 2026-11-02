# -*- mode: python ; coding: utf-8 -*-
"""Spécification PyInstaller d'Azimut (mode dossier par défaut).

Utilisation : python -m PyInstaller packaging/azimut.spec --noconfirm --clean
(ou simplement `python packaging/build.py`).
Variante fichier unique : variable d'environnement AZIMUT_ONEFILE=1.
Garde-fou de distribution : AZIMUT_REQUIRE_BINARIES=1 fait échouer le build si bin/<plateforme>/ est
absent ou vide (utilisé par la CI : on ne publie jamais un paquet sans FFmpeg / ExifTool embarqués).
Aucun chemin absolu : tout est calculé à partir de l'emplacement de ce fichier.
"""
from __future__ import annotations

import importlib.util
import os
import platform
import re
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

# SPECPATH est fourni par PyInstaller (dossier du .spec) ; repli sur __file__.
ROOT = Path(globals().get("SPECPATH") or Path(__file__).resolve().parent).resolve().parent
sys.path.insert(0, str(ROOT))

APP_NAME = "Azimut"
ONEFILE = os.environ.get("AZIMUT_ONEFILE", "").strip() == "1"
REQUIRE_BINARIES = os.environ.get("AZIMUT_REQUIRE_BINARIES", "").strip() == "1"


def _version() -> str:
    """Lit __version__ sans importer le paquet (accepte `__version__ = "x"` et `__version__: str = "x"`)."""
    text = (ROOT / "azimut" / "__init__.py").read_text(encoding="utf-8")
    found = re.search(r"""__version__\s*(?::[^=\n]+)?=\s*["']([^"']+)["']""", text)
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
    """bin/<tag>/ → bin/<tag>/ dans le paquet (rien si absent ou vide, sauf AZIMUT_REQUIRE_BINARIES=1)."""
    src = ROOT / "bin" / TAG
    files = [p for p in src.rglob("*") if p.is_file() and p.name not in (".gitkeep", "README.md")] if src.is_dir() else []
    if not files:
        reason = "absent" if not src.is_dir() else "vide"
        if REQUIRE_BINARIES:
            raise SystemExit(f"[azimut.spec] ERREUR : bin/{TAG}/ est {reason} alors que AZIMUT_REQUIRE_BINARIES=1. "
                             "Lancez d'abord : python packaging/fetch_binaries.py")
        print(f"[azimut.spec] bin/{TAG}/ {reason} : aucun binaire embarqué "
              "(Azimut utilisera les préférences ou le PATH).")
        return []
    print(f"[azimut.spec] {len(files)} fichier(s) de bin/{TAG}/ embarqué(s).")
    return [(str(src), f"bin/{TAG}")]


def _optional(package: str) -> tuple[list, list, list]:
    """(datas, binaries, hiddenimports) d'un paquet facultatif (HEIC, corbeille…) ; vide s'il est absent.

    Garantit que ses bibliothèques natives (libheif, etc.) sont bien embarquées.
    """
    if importlib.util.find_spec(package) is None:
        print(f"[azimut.spec] paquet facultatif « {package} » non installé : ignoré.")
        return [], [], []
    try:
        return collect_all(package)
    except Exception as exc:  # noqa: BLE001
        print(f"[azimut.spec] collecte de « {package} » impossible ({exc}) : ignoré.")
        return [], [], []


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

EXTRA_DATAS: list = []
EXTRA_BINARIES: list = []
for _pkg in ("pillow_heif", "send2trash", "rawpy", "psutil"):
    _d, _b, _h = _optional(_pkg)
    EXTRA_DATAS += _d
    EXTRA_BINARIES += _b
    HIDDEN_IMPORTS += _h

DATAS = [(str(ROOT / "azimut" / "resources"), "azimut/resources")] + _bundled_binaries() + EXTRA_DATAS
ICON = _icon()

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=EXTRA_BINARIES,
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
            "CFBundleDisplayName": APP_NAME,
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "LSApplicationCategoryType": "public.app-category.utilities",
            "LSMinimumSystemVersion": "12.0",
            "NSHighResolutionCapable": True,
        },
    )
