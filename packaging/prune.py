"""Élagage d'une distribution PyInstaller d'Azimut.

PySide6 livre Qt en entier : QML (une quarantaine de Mo), les métatypes, les
outils de développement (Designer, Linguist, lupdate, uic…) et des greffons que
l'application ne charge jamais (SQL, géolocalisation, Qt3D, WebView, Capteurs…).
Ce script retire ces éléments d'un produit déjà construit et conserve ce que
l'exécution utilise :

- les greffons ``platforms``, ``imageformats``, ``iconengines``, ``multimedia``
  (aperçu vidéo du module Tags), ainsi que les styles, TLS et la saisie IME ;
- les bibliothèques ``libav*`` / ``libsw*`` dont dépend le greffon multimédia ;
- les catalogues ``qtbase_<langue>.qm`` des langues du registre i18n, y compris
  leurs alias (``qtbase_nn.qm`` sert « nb »).

Usage :
    python packaging/prune.py dist/Azimut
    python packaging/prune.py dist/Azimut --dry-run
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from collections.abc import Iterable
from pathlib import Path

# Greffons Qt conservés : plateformes, formats d'image, icônes SVG, aperçu vidéo,
# apparence native, TLS et méthodes de saisie (IME des langues CJK).
KEPT_PLUGIN_DIRS = frozenset({
    "platforms",
    "imageformats",
    "iconengines",
    "multimedia",
    "styles",
    "tls",
    "platforminputcontexts",
})

# Greffons jamais chargés par Azimut : SQL, géolocalisation, capteurs, QML,
# Qt3D/Quick3D, WebView, synthèse vocale, CAN, formats vectoriels Lottie…
DROPPED_PLUGIN_DIRS = frozenset({
    "assetimporters",
    "canbus",
    "designer",
    "generic",
    "geoservices",
    "geometryloaders",
    "networkinformation",
    "position",
    "qmltooling",
    "renderers",
    "renderplugins",
    "sceneparsers",
    "scxmldatamodel",
    "sensors",
    "sqldrivers",
    "texttospeech",
    "vectorimageformats",
    "webview",
})

# Répertoires de Qt supprimés en bloc : QML et descriptions de métatypes.
DROPPED_QT_DIRS = ("qml", "metatypes")

# Outils de développement livrés avec PySide6 (sans suffixe .app / .exe).
DEV_TOOL_STEMS = frozenset({
    "assistant",
    "balsam",
    "balsamui",
    "designer",
    "linguist",
    "lrelease",
    "lupdate",
    "qmlcachegen",
    "qmlformat",
    "qmlimportscanner",
    "qmllint",
    "qmlls",
    "qmlscene",
    "qmltestrunner",
    "qmltyperegistrar",
    "qsb",
    "rcc",
    "svgtoqml",
    "uic",
})

# Bibliothèques dont l'aperçu vidéo dépend : jamais supprimées, même par erreur.
PROTECTED_LIBRARY_PREFIXES = (
    "libav", "libsw", "avcodec-", "avformat-", "avutil-", "swscale-", "swresample-",
    "ffmpegmediaplugin", "darwinmediaplugin",
)


def kept_qtbase_catalogs(names: Iterable[str] = ()) -> frozenset[str]:
    """Catalogues Qt à conserver : ceux des langues du registre et leurs alias.

    ``names`` apporte les fichiers réellement présents dans le produit : un
    catalogue dont le code se normalise vers une langue du registre est gardé
    (``qtbase_nn.qm`` pour « nb »), les autres (``qtbase_bg.qm``…) sont retirés.
    """
    root = Path(__file__).resolve().parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from azimut.core.i18n import LANGUAGES, normalize_language, qt_catalog_code

    codes = {language.code for language in LANGUAGES}
    kept = {f"qtbase_{qt_catalog_code(code)}.qm" for code in codes}
    for name in names:
        stem = name.removeprefix("qtbase_").removesuffix(".qm")
        if normalize_language(stem) in codes:
            kept.add(name)
    return frozenset(kept)


def find_pyside6(dist: Path) -> Path | None:
    """Dossier ``PySide6`` du produit (``dist/Azimut``, ``_internal``, bundle macOS…)."""
    dist = Path(dist)
    if dist.name == "PySide6" and (dist / "Qt").is_dir():
        return dist
    candidates = [path for path in dist.rglob("PySide6") if (path / "Qt").is_dir()]
    return min(candidates, key=lambda path: len(path.parts)) if candidates else None


def _is_dev_tool(path: Path) -> bool:
    name = path.name.lower()
    for suffix in (".app", ".exe"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    return name in DEV_TOOL_STEMS


def _is_protected(path: Path) -> bool:
    name = path.name.lower()
    return any(name.startswith(prefix) for prefix in PROTECTED_LIBRARY_PREFIXES)


def prune(dist: Path, *, dry_run: bool = False) -> list[Path]:
    """Élague le produit ``dist`` et renvoie les chemins retirés (ou à retirer)."""
    pyside = find_pyside6(Path(dist))
    if pyside is None:
        raise FileNotFoundError(f"aucun dossier PySide6/Qt sous {dist}")
    pyside = Path(os.path.abspath(pyside))
    qt = pyside / "Qt"
    removed: list[Path] = []

    def drop(path: Path) -> None:
        target = Path(os.path.abspath(path))
        if not target.is_relative_to(pyside):
            raise RuntimeError(f"élagage hors du produit refusé : {target}")
        if _is_protected(target):
            raise RuntimeError(f"bibliothèque protégée : {target}")
        removed.append(target)
        if dry_run:
            return
        if target.is_symlink() or target.is_file():
            target.unlink()
        else:
            shutil.rmtree(target)

    for name in DROPPED_QT_DIRS:
        if (qt / name).exists():
            drop(qt / name)
    for metatypes in sorted(qt.rglob("*.metatypes.json")):
        drop(metatypes)

    for base in (pyside, qt / "libexec", qt / "bin"):
        if not base.is_dir():
            continue
        for entry in sorted(base.iterdir()):
            if _is_dev_tool(entry):
                drop(entry)

    plugins = qt / "plugins"
    if plugins.is_dir():
        for name in sorted(DROPPED_PLUGIN_DIRS):
            if (plugins / name).exists():
                drop(plugins / name)

    translations = qt / "translations"
    if translations.is_dir():
        entries = sorted(path.name for path in translations.iterdir())
        keep = kept_qtbase_catalogs(entries)
        for name in entries:
            if name not in keep:
                drop(translations / name)

    return removed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Élague une distribution PyInstaller d'Azimut.")
    parser.add_argument("dist", nargs="?", default="dist/Azimut", type=Path,
                        help="produit à élaguer (défaut : dist/Azimut)")
    parser.add_argument("--dry-run", action="store_true",
                        help="liste ce qui serait retiré sans rien supprimer")
    args = parser.parse_args(argv)

    try:
        removed = prune(args.dist, dry_run=args.dry_run)
    except (FileNotFoundError, RuntimeError) as exc:
        print(f"ERREUR : {exc}")
        return 1
    verb = "à retirer" if args.dry_run else "retiré(s)"
    print(f"{len(removed)} élément(s) {verb} de {args.dist}.")
    for path in removed:
        print(f"  - {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
