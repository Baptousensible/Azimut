"""Construit Azimut avec PyInstaller (multiplateforme) et affiche les tailles.

Usage : python packaging/build.py
        AZIMUT_ONEFILE=1 python packaging/build.py   (fichier unique)
"""
from __future__ import annotations

import importlib.util
import logging
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = Path("packaging") / "azimut.spec"

_log = logging.getLogger("azimut.packaging.build")
OUTPUT_NAME = "Azimut"
WARN_LIMIT = 300 * 1024 * 1024  # 300 Mo
REQUIRED_BINARIES = ("ffmpeg", "ffprobe", "exiftool")
ANIMATED_SOURCE = ROOT / "azimut" / "resources" / "icons_animated"
ANIMATED_LICENSE = "LICENSE-lucide-animated.txt"
THIRD_PARTY_NOTICES = "THIRD_PARTY_NOTICES.md"


def platform_tag() -> str:
    """Même résultat que azimut.core.binaries.platform_tag() (repli local)."""
    sys.path.insert(0, str(ROOT))
    try:
        from azimut.core.binaries import platform_tag as core_tag
        return core_tag()
    except Exception:
        machine = platform.machine().lower()
        if sys.platform.startswith("win"):
            return "windows-x64"
        if sys.platform == "darwin":
            return "macos-arm64" if machine in ("arm64", "aarch64") else "macos-x64"
        return "linux-x64"


def format_size(n: float) -> str:
    """« 1,5 Mo » (base 1024, virgule décimale)."""
    for unit in ("o", "Ko", "Mo", "Go", "To"):
        if n < 1024 or unit == "To":
            return f"{int(n)} o" if unit == "o" else f"{n:.1f} {unit}".replace(".", ",")
        n /= 1024
    return ""


def path_size(path: Path) -> int:
    """Taille totale d'un fichier ou d'un dossier (liens symboliques ignorés)."""
    if path.is_file():
        return path.stat().st_size
    total = 0
    for dirpath, _dirs, files in os.walk(path):
        for name in files:
            full = os.path.join(dirpath, name)
            if os.path.islink(full):
                continue
            try:
                total += os.path.getsize(full)
            except OSError as exc:
                _log.debug("Taille illisible (%s) : %s", full, exc)
    return total


def check_binaries(tag: str) -> bool:
    """Avertit (sans bloquer) si des binaires à embarquer manquent."""
    folder = ROOT / "bin" / tag
    suffix = ".exe" if tag.startswith("windows") else ""
    missing = [n for n in REQUIRED_BINARIES if not (folder / f"{n}{suffix}").is_file()]
    if tag.startswith("windows") and (folder / f"exiftool{suffix}").is_file() \
            and not (folder / "exiftool_files").is_dir():
        print("ATTENTION : bin/%s/exiftool_files/ est absent (requis par exiftool.exe)." % tag)
    if not missing:
        print(f"Binaires trouvés dans bin/{tag}/ : {', '.join(REQUIRED_BINARIES)}.")
        return True
    print(f"ATTENTION : binaire(s) absent(s) de bin/{tag}/ : {', '.join(n + suffix for n in missing)}.")
    print("  Le paquet sera construit sans eux : Azimut cherchera alors dans les préférences, puis le PATH.")
    print("  Voir bin/README.md pour les déposer.")
    return False


def find_output(dist: Path) -> Path | None:
    for name in (f"{OUTPUT_NAME}.app", OUTPUT_NAME, f"{OUTPUT_NAME}.exe"):
        candidate = dist / name
        if candidate.exists():
            return candidate
    return None


def publish_notice(output: Path) -> bool:
    """Copie les mentions tierces à la racine du produit, pas seulement dans `_internal`.

    PyInstaller range les données dans `_internal` en mode dossier ; les paquets Linux
    installent aussi le fichier dans `/usr/share/doc`, mais Windows et macOS n'ont pas
    cette étape. On rend donc la notice visible dans toutes les distributions.
    """
    source = ROOT / THIRD_PARTY_NOTICES
    if not source.is_file():
        print(f"ERREUR : {THIRD_PARTY_NOTICES} est absent du dépôt.")
        return False
    if output.is_file():
        target = output.parent / THIRD_PARTY_NOTICES
    elif output.suffix == ".app":
        target = output / "Contents" / "Resources" / THIRD_PARTY_NOTICES
    else:
        target = output / THIRD_PARTY_NOTICES
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    except OSError as exc:
        print(f"ERREUR : copie de {THIRD_PARTY_NOTICES} impossible : {exc}")
        return False
    return True


def check_packaged_assets(output: Path) -> bool:
    """Vérifie que les icônes animées, leur licence et les mentions tierces sont dans le produit.

    PyInstaller embarque tout `azimut/resources`, mais ce contrôle empêche qu'un build
    silencieusement incomplet (dossier déplacé, fichier oublié) parte en distribution.
    En mode fichier unique, le contenu est dans l'archive : on vérifie la source.
    """
    expected = {path.name for path in ANIMATED_SOURCE.glob("*.svg")}
    if not expected or not (ANIMATED_SOURCE / ANIMATED_LICENSE).is_file():
        print(f"ERREUR : icônes animées ou licence MIT absentes de {ANIMATED_SOURCE}.")
        return False
    if output.is_file():
        print(f"Icônes animées : {len(expected)} SVG et licence dans l'archive fichier unique.")
        return True

    candidates = [folder for folder in output.rglob("icons_animated") if folder.is_dir()]
    complete = next(
        (folder for folder in candidates
         if expected <= {path.name for path in folder.glob("*.svg")}
         and (folder / ANIMATED_LICENSE).is_file()),
        None,
    )
    if complete is None:
        found = ", ".join(str(folder.relative_to(output)) for folder in candidates) or "aucun dossier"
        print("ERREUR : les SVG animés ou leur licence MIT ne sont pas dans le produit "
              f"({found}).")
        return False
    if not any(path.name == THIRD_PARTY_NOTICES for path in output.rglob(THIRD_PARTY_NOTICES)):
        print(f"ERREUR : {THIRD_PARTY_NOTICES} n'est pas livré dans le produit.")
        return False
    print(f"Icônes animées : {len(expected)} SVG + licence MIT dans "
          f"{complete.relative_to(output).as_posix()}/.")
    return True


def embedded_binaries_size(output: Path, tag: str) -> tuple[int, str]:
    """Taille des binaires embarqués : dans le paquet si possible, sinon dans bin/<tag>/."""
    if output.is_dir():
        for candidate in output.rglob(tag):
            if candidate.is_dir() and candidate.parent.name == "bin":
                return path_size(candidate), "dans le paquet"
    source = ROOT / "bin" / tag
    if source.is_dir():
        return path_size(source), "dossier source, paquet en fichier unique"
    return 0, "aucun"


def main() -> int:
    tag = platform_tag()
    onefile = os.environ.get("AZIMUT_ONEFILE", "").strip() == "1"
    print(f"Plateforme : {tag} — mode {'fichier unique' if onefile else 'dossier'}")
    found = check_binaries(tag)
    if not found and os.environ.get("AZIMUT_REQUIRE_BINARIES", "").strip() == "1":
        print("ERREUR : binaires manquants alors que AZIMUT_REQUIRE_BINARIES=1 (lancez python packaging/fetch_binaries.py).")
        return 1

    if importlib.util.find_spec("PyInstaller") is None:
        print("ERREUR : PyInstaller n'est pas installé (pip install pyinstaller).")
        return 1

    cmd = [sys.executable, "-m", "PyInstaller", SPEC.as_posix(), "--noconfirm", "--clean"]
    print("Commande :", " ".join(cmd))
    result = subprocess.run(cmd, cwd=ROOT, check=False)
    if result.returncode != 0:
        print(f"ERREUR : PyInstaller a échoué (code {result.returncode}).")
        return result.returncode

    output = find_output(ROOT / "dist")
    if output is None:
        print("ERREUR : aucun produit trouvé dans dist/.")
        return 1
    if not publish_notice(output) or not check_packaged_assets(output):
        return 1
    total = path_size(output)
    bins, where = embedded_binaries_size(output, tag)
    print("-" * 60)
    print(f"Produit : {output.relative_to(ROOT).as_posix()}")
    print(f"Taille totale : {format_size(total)}")
    print(f"Binaires embarqués : {format_size(bins)} ({where})")
    if total > WARN_LIMIT:
        print(f"ATTENTION : le produit dépasse {format_size(WARN_LIMIT)}. "
              "Voir BUILD.md (pistes de réduction).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
