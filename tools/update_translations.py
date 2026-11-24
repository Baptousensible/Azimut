"""Met à jour les catalogues de traduction Qt (``.ts``) et les compile (``.qm``).

Usage :
    python tools/update_translations.py            # met à jour puis compile tous les catalogues
    python tools/update_translations.py --release  # compile seulement les .ts existants
    python tools/update_translations.py --check    # vérifie que les .ts sont à jour, sans les modifier
    python tools/update_translations.py --stats    # avancement des traductions, langue par langue

Les chaînes source sont en français ; ``pyside6-lupdate`` extrait les appels ``tr()``,
``QCoreApplication.translate()`` et les marqueurs ``QT_TRANSLATE_NOOP()``. Après l'ajout de
texte, relancer ce script puis traduire les nouvelles entrées soit dans Qt Linguist, soit à la
main dans le ``.ts``. Les langues incomplètes restent lisibles : Qt retombe sur l'anglais
(``azimut_en.qm``), lui-même complet, donc aucune chaîne française ne fuit.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TRANSLATIONS = ROOT / "azimut" / "resources" / "translations"
# Tout le paquet est scanné : plus aucune fenêtre, aucun noyau ni utilitaire n'échappe à
# l'extraction quand une chaîne visible y est ajoutée.
SOURCES = ("azimut/**/*.py",)


def _tool(name: str) -> str:
    """Chemin d'un outil PySide6 (PATH, puis ~/.local/bin, puis Scripts de l'interpréteur)."""
    found = shutil.which(name)
    if found:
        return found
    candidates = [Path.home() / ".local" / "bin" / name,
                  Path(sys.executable).parent / name]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    raise SystemExit(f"{name} introuvable : installez PySide6 (pip install PySide6).")


def _catalogs() -> list[Path]:
    """Catalogues ``.ts`` présents, triés par langue."""
    catalogs = sorted(TRANSLATIONS.glob("*.ts"))
    if not catalogs:
        raise SystemExit(f"Aucun catalogue dans {TRANSLATIONS}")
    return catalogs


def _source_files() -> list[str]:
    """Fichiers réels de ``SOURCES`` : lupdate n'étend pas les jokers lui-même."""
    files = sorted({str(path.relative_to(ROOT)) for pattern in SOURCES for path in ROOT.glob(pattern)})
    if not files:
        raise SystemExit(f"Aucune source dans {ROOT} pour {SOURCES}")
    return files


def update() -> None:
    """Extrait les chaînes du code vers chaque ``.ts`` existant.

    ``-no-obsolete`` supprime les entrées dont la chaîne a disparu du code : sans cela les
    catalogues gardent indéfiniment des traductions mortes et divergent les uns des autres.
    """
    catalogs = _catalogs()
    cmd = [_tool("pyside6-lupdate"), *_source_files(), "-no-obsolete", "-ts", *map(str, catalogs)]
    subprocess.run(cmd, cwd=ROOT, check=True)


def release() -> None:
    """Compile chaque ``.ts`` en ``.qm`` lisible par QTranslator."""
    catalogs = _catalogs()
    subprocess.run([_tool("pyside6-lrelease"), *map(str, catalogs)], cwd=ROOT, check=True)
    for qm in sorted(TRANSLATIONS.glob("*.qm")):
        print(f"  {qm.relative_to(ROOT)} ({qm.stat().st_size} octets)")


def _counts(catalog: Path) -> tuple[int, int]:
    """(chaînes actives, chaînes sans traduction) d'un catalogue.

    Les entrées « vanished » (texte disparu du code) ne sont pas comptées : elles ne partent
    pas dans le ``.qm`` et gonfleraient artificiellement la couverture.
    """
    root = ET.parse(catalog).getroot()
    total = unfinished = 0
    for message in root.iter("message"):
        translation = message.find("translation")
        if translation is not None and translation.get("type") == "vanished":
            continue
        total += 1
        if translation is None or translation.get("type") == "unfinished" \
                or not (translation.text or "").strip():
            unfinished += 1
    return total, unfinished


def stats() -> int:
    """Avancement des traductions par langue (0 = tout est traduit)."""
    total_ref = 0
    rows: list[tuple[str, int, int]] = []
    for catalog in _catalogs():
        total, unfinished = _counts(catalog)
        total_ref = max(total_ref, total)
        rows.append((catalog.stem.removeprefix("azimut_"), total, unfinished))
    print(f"{'langue':<8}{'messages':>10}{'à traduire':>12}{'couverture':>12}")
    for language, total, unfinished in rows:
        done = total - unfinished
        share = (100 * done / total) if total else 100.0
        print(f"{language:<8}{total:>10}{unfinished:>12}{share:>11.1f} %")
    return 0 if all(total == total_ref for _, total, _ in rows) else 1


def check() -> int:
    """Vérifie que les ``.ts`` correspondent au code, sans les laisser modifiés."""
    catalogs = _catalogs()
    backups = {catalog: catalog.read_bytes() for catalog in catalogs}
    try:
        update()
        stale = [str(catalog.relative_to(ROOT)) for catalog in catalogs
                 if catalog.read_bytes() != backups[catalog]]
    finally:
        for catalog, content in backups.items():
            catalog.write_bytes(content)
    if stale:
        print("Catalogues périmés (relancer tools/update_translations.py) :")
        for name in stale:
            print(f"  {name}")
        return 1
    print(f"{len(catalogs)} catalogues à jour.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--release", action="store_true", help="ne pas lancer lupdate")
    parser.add_argument("--check", action="store_true",
                        help="vérifier que les .ts sont à jour (code retour 1 sinon)")
    parser.add_argument("--stats", action="store_true", help="afficher l'avancement des traductions")
    args = parser.parse_args()
    if args.stats:
        stats()
    if args.check:
        return check()
    if not args.release and not args.stats:
        update()
    if not args.stats:
        release()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
