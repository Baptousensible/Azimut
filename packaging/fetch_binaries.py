#!/usr/bin/env python3
"""Télécharge FFmpeg, FFprobe et ExifTool (avec son Perl) dans bin/<plateforme>/.

Le résultat respecte ce qu'attend azimut.core.binaries (voir bin/README.md) :

    Windows : ffmpeg.exe, ffprobe.exe, exiftool.exe + exiftool_files/
    Linux / macOS : ffmpeg, ffprobe, exiftool (lanceur), exiftool-perl/ (script + lib/), perl/ (Perl portable)

Sous Linux et macOS, ExifTool est un script Perl : on embarque donc un Perl « relocatable » et un petit
lanceur `exiftool`, pour que le paquet fonctionne sans aucun Perl installé sur la machine de l'utilisateur.

Sources (toutes gratuites, téléchargées depuis des URL publiques) :
    FFmpeg Windows / Linux : https://github.com/BtbN/FFmpeg-Builds   (variante GPL statique, avec libx264,
                              libsvtav1, libaom, libopus, libmp3lame, libwebp… ; somme de contrôle vérifiée)
    FFmpeg macOS           : https://ffmpeg.martin-riedl.de            (builds statiques arm64 / amd64)
    ExifTool               : SourceForge / GitHub (exiftool.org n'héberge plus les archives)
    Perl portable          : https://github.com/skaji/relocatable-perl

Variables d'environnement facultatives :
    AZIMUT_FFMPEG_BRANCH       « n9.0 » par défaut ; « master » pour la branche de développement.
    AZIMUT_EXIFTOOL_VERSION    force une version d'ExifTool (ex. « 13.59 »).

Usage : python packaging/fetch_binaries.py [--force] [--no-verify]
Bibliothèque standard uniquement.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

BTBN_BASE = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest"
MARTIN_RIEDL = "https://ffmpeg.martin-riedl.de/redirect/latest/macos/{arch}/{build}/{name}"
EXIFTOOL_HOME = "https://exiftool.org"                 # n'héberge plus les archives : liens vers SourceForge
EXIFTOOL_SF = "https://sourceforge.net/projects/exiftool/files/{name}/download"
EXIFTOOL_GITHUB = "https://github.com/exiftool/exiftool/archive/refs/tags/{version}.tar.gz"
MAGIC = {"zip": b"PK\x03\x04", "gz": b"\x1f\x8b", "xz": b"\xfd7zXZ\x00"}   # signatures des archives
PERL_BASE = "https://github.com/skaji/relocatable-perl/releases/latest/download"

FFMPEG_BRANCH = os.environ.get("AZIMUT_FFMPEG_BRANCH", "n9.0").strip() or "n9.0"
USER_AGENT = "azimut-packaging/1.0 (+https://github.com)"

sources: list[tuple[str, str, str]] = []        # (composant, url, sha256) pour BINARIES-SOURCES.txt


# --------------------------------------------------------------------------- utilitaires

def log(message: str) -> None:
    print(f"[fetch_binaries] {message}", flush=True)


def platform_tag() -> str:
    """Même résultat que azimut.core.binaries.platform_tag()."""
    if sys.platform == "win32":
        return "windows-x64"
    if sys.platform == "darwin":
        return "macos-arm64" if platform.machine().lower() in ("arm64", "aarch64") else "macos-x64"
    return "linux-x64"


def _request(url: str) -> urllib.request.Request:
    # SourceForge redirige directement vers un miroir pour les clients de type curl (pas de page HTML).
    agent = "curl/8.5.0" if "sourceforge" in url else USER_AGENT
    return urllib.request.Request(url, headers={"User-Agent": agent})


def download(url: str, target: Path, attempts: int = 4) -> str:
    """Télécharge `url` vers `target` (nouvelles tentatives) et renvoie son SHA-256."""
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            log(f"Téléchargement : {url}")
            digest = hashlib.sha256()
            with urllib.request.urlopen(_request(url), timeout=120) as response, target.open("wb") as out:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
                    digest.update(chunk)
            log(f"  → {target.name} : {target.stat().st_size / 1024 / 1024:.1f} Mo, sha256 {digest.hexdigest()[:16]}…")
            return digest.hexdigest()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:                 # inutile de réessayer
                raise
            last = exc
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            last = exc
        if attempt < attempts:
            wait = 3 * attempt
            log(f"  échec ({last}) ; nouvelle tentative dans {wait} s")
            time.sleep(wait)
    raise RuntimeError(f"Téléchargement impossible : {url} ({last})")


def fetch_text(url: str) -> str:
    with urllib.request.urlopen(_request(url), timeout=60) as response:
        return response.read().decode("utf-8", errors="replace")


def make_executable(path: Path) -> None:
    if sys.platform != "win32":
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def extract_zip(archive: Path, target: Path) -> None:
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(target)


def extract_tar(archive: Path, target: Path) -> None:
    with tarfile.open(archive) as tf:
        if hasattr(tarfile, "data_filter"):     # Python ≥ 3.10.12 / 3.11.4 / 3.12 : extraction sûre
            tf.extractall(target, filter="data")
        else:                                   # pragma: no cover - très anciens Python
            tf.extractall(target)


def extract(archive: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    if archive.suffix == ".zip":
        extract_zip(archive, target)
    else:
        extract_tar(archive, target)


def find_file(base: Path, name: str, parent_name: str | None = None) -> Path:
    """Premier fichier `name` sous `base` (éventuellement dans un dossier nommé `parent_name`)."""
    for candidate in sorted(base.rglob(name)):
        if candidate.is_file() and (parent_name is None or candidate.parent.name == parent_name):
            return candidate
    raise FileNotFoundError(f"« {name} » introuvable dans l'archive extraite ({base}).")


def download_first(candidates: list[tuple[str, str]], tmp: Path, kind: str) -> tuple[Path, str, str]:
    """Essaie chaque (url, nom de fichier) et renvoie (archive, sha256, url) du premier fichier valide.

    « Valide » = la signature de l'archive correspond à `kind` (zip, gz, xz) : une page HTML d'erreur
    ou d'attente renvoyée à la place du fichier est donc écartée.
    """
    problems: list[str] = []
    for url, name in candidates:
        target = tmp / name
        try:
            digest = download(url, target)
            with target.open("rb") as handle:
                head = handle.read(len(MAGIC[kind]))
            if head != MAGIC[kind]:
                raise ValueError("le fichier reçu n'est pas une archive %s (page HTML ?)" % kind)
            return target, digest, url
        except (urllib.error.URLError, RuntimeError, ValueError, OSError) as exc:
            log(f"  échec : {url} ({exc}) ; source suivante")
            problems.append(f"{url} -> {exc}")
    raise RuntimeError("Aucune source n'a fourni l'archive attendue :\n  " + "\n  ".join(problems))


def _verify_exiftool(filename: str, digest: str, version: str) -> None:
    """Compare avec checksums-<version>.txt publié par ExifTool (avertit si indisponible ou illisible)."""
    try:
        listing = fetch_text(f"{EXIFTOOL_HOME}/checksums-{version}.txt")
    except Exception as exc:  # noqa: BLE001
        log(f"  AVERTISSEMENT : checksums-{version}.txt indisponible ({exc}) ; empreinte non vérifiée.")
        return
    for line in listing.splitlines():
        found = re.search(r"\b([0-9a-fA-F]{64})\b", line)
        if filename in line and found:
            if found.group(1).lower() != digest.lower():
                raise RuntimeError(f"Somme de contrôle incorrecte pour {filename} (attendu {found.group(1)}, obtenu {digest}).")
            log("  somme de contrôle conforme à checksums-%s.txt" % version)
            return
    log(f"  AVERTISSEMENT : {filename} absent de checksums-{version}.txt ; empreinte non vérifiée.")


# --------------------------------------------------------------------------- FFmpeg / FFprobe

def _btbn_asset(branch: str, target: str, ext: str) -> str:
    if branch == "master":
        return f"ffmpeg-master-latest-{target}-gpl.{ext}"
    return f"ffmpeg-{branch}-latest-{target}-gpl-{branch.lstrip('n')}.{ext}"


def _verify_btbn(name: str, digest: str) -> None:
    """Compare avec checksums.sha256 publié dans la même release (avertit si indisponible)."""
    try:
        listing = fetch_text(f"{BTBN_BASE}/checksums.sha256")
    except Exception as exc:  # noqa: BLE001
        log(f"  AVERTISSEMENT : checksums.sha256 indisponible ({exc}) ; empreinte non vérifiée.")
        return
    for line in listing.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[-1].lstrip("*") == name:
            if parts[0].lower() != digest.lower():
                raise RuntimeError(f"Somme de contrôle incorrecte pour {name} (attendu {parts[0]}, obtenu {digest}).")
            log("  somme de contrôle conforme à checksums.sha256")
            return
    log(f"  AVERTISSEMENT : {name} absent de checksums.sha256 ; empreinte non vérifiée.")


def install_ffmpeg_btbn(target: str, ext: str, dest: Path, tmp: Path) -> None:
    branches = [FFMPEG_BRANCH] + (["master"] if FFMPEG_BRANCH != "master" else [])
    for branch in branches:
        name = _btbn_asset(branch, target, ext)
        url = f"{BTBN_BASE}/{name}"
        try:
            archive = tmp / name
            digest = download(url, archive)
        except urllib.error.HTTPError as exc:
            log(f"  {name} : HTTP {exc.code} ; essai suivant")
            continue
        _verify_btbn(name, digest)
        extracted = tmp / "ffmpeg-extract"
        extract(archive, extracted)
        suffix = ".exe" if target.startswith("win") else ""
        for tool in ("ffmpeg", "ffprobe"):
            src = find_file(extracted, tool + suffix, "bin")
            shutil.copy2(src, dest / (tool + suffix))
            make_executable(dest / (tool + suffix))
        sources.append((f"FFmpeg + FFprobe (BtbN, variante GPL, {branch})", url, digest))
        return
    raise RuntimeError("Aucun build FFmpeg BtbN téléchargeable (branche demandée et master essayées).")


def install_ffmpeg_macos(arch: str, dest: Path, tmp: Path) -> None:
    """Martin Riedl : un zip par outil (ffmpeg.zip, ffprobe.zip), builds statiques."""
    for tool in ("ffmpeg", "ffprobe"):
        archive = tmp / f"{tool}.zip"
        digest = url = None
        for build in ("release", "snapshot"):
            url = MARTIN_RIEDL.format(arch=arch, build=build, name=f"{tool}.zip")
            try:
                digest = download(url, archive)
                break
            except urllib.error.HTTPError as exc:
                log(f"  {url} : HTTP {exc.code} ; essai suivant")
        if digest is None:
            raise RuntimeError(f"{tool} introuvable chez ffmpeg.martin-riedl.de pour macOS/{arch}.")
        extracted = tmp / f"{tool}-extract"
        extract(archive, extracted)
        shutil.copy2(find_file(extracted, tool), dest / tool)
        make_executable(dest / tool)
        sources.append((f"{tool} (Martin Riedl, macOS {arch})", str(url), digest))


# --------------------------------------------------------------------------- ExifTool

def exiftool_version() -> str:
    forced = os.environ.get("AZIMUT_EXIFTOOL_VERSION", "").strip()
    if forced:
        return forced
    try:                                               # la page d'accueil cite toujours la dernière version
        found = re.search(r"exiftool-(\d+\.\d+)_64\.zip", fetch_text(EXIFTOOL_HOME + "/"))
        if found:
            return found.group(1)
    except Exception as exc:  # noqa: BLE001
        log(f"  page d'accueil d'ExifTool illisible ({exc})")
    try:
        found = re.match(r"\s*(\d+\.\d+)", fetch_text(EXIFTOOL_HOME + "/ver.txt"))
        if found:
            return found.group(1)
    except Exception as exc:  # noqa: BLE001
        log(f"  ver.txt illisible ({exc})")
    raise RuntimeError("Version d'ExifTool introuvable : définissez AZIMUT_EXIFTOOL_VERSION (ex. 13.59).")


def install_exiftool_windows(dest: Path, tmp: Path, version: str) -> None:
    name = f"exiftool-{version}_64.zip"
    archive, digest, url = download_first([
        (EXIFTOOL_SF.format(name=name), name),                 # source officielle actuelle (SourceForge)
        (f"{EXIFTOOL_HOME}/{name}", "home-" + name),           # ancien emplacement, au cas où il revienne
    ], tmp, "zip")
    _verify_exiftool(name, digest, version)
    extracted = tmp / "exiftool-extract"
    extract(archive, extracted)
    exe = find_file(extracted, "exiftool(-k).exe")
    shutil.copy2(exe, dest / "exiftool.exe")
    shutil.copytree(exe.parent / "exiftool_files", dest / "exiftool_files", dirs_exist_ok=True)
    sources.append((f"ExifTool {version} (Windows, avec Perl intégré)", url, digest))


_WRAPPER = """#!/bin/sh
# Lanceur ExifTool d'Azimut : utilise le Perl embarqué (aucun Perl système requis).
SELF="$0"
while [ -h "$SELF" ]; do
  LINK=$(readlink "$SELF")
  case "$LINK" in
    /*) SELF="$LINK" ;;
    *)  SELF="$(dirname "$SELF")/$LINK" ;;
  esac
done
DIR=$(cd "$(dirname "$SELF")" && pwd)
# Une application figée (PyInstaller) modifie le chemin des bibliothèques : on rétablit l'original.
if [ -n "${LD_LIBRARY_PATH_ORIG+x}" ]; then export LD_LIBRARY_PATH="$LD_LIBRARY_PATH_ORIG"; else unset LD_LIBRARY_PATH; fi
if [ -n "${DYLD_LIBRARY_PATH_ORIG+x}" ]; then export DYLD_LIBRARY_PATH="$DYLD_LIBRARY_PATH_ORIG"; else unset DYLD_LIBRARY_PATH; fi
unset PERL5LIB PERLLIB PERL5OPT
exec "$DIR/perl/bin/perl" "$DIR/exiftool-perl/exiftool" "$@"
"""


def install_exiftool_unix(dest: Path, tmp: Path, version: str) -> None:
    name = f"Image-ExifTool-{version}.tar.gz"
    archive, digest, url = download_first([
        (EXIFTOOL_GITHUB.format(version=version), f"exiftool-{version}-github.tar.gz"),   # dépôt officiel, pérenne
        (EXIFTOOL_SF.format(name=name), name),
        (f"{EXIFTOOL_HOME}/{name}", "home-" + name),
    ], tmp, "gz")
    if "github.com" not in url:
        _verify_exiftool(name, digest, version)
    extracted = tmp / "exiftool-extract"
    extract(archive, extracted)
    script = find_file(extracted, "exiftool")
    if not (script.parent / "lib").is_dir():
        raise RuntimeError("Le dossier lib/ d'ExifTool est absent de l'archive.")
    target = dest / "exiftool-perl"
    target.mkdir(parents=True, exist_ok=True)
    shutil.copy2(script, target / "exiftool")
    shutil.copytree(script.parent / "lib", target / "lib", dirs_exist_ok=True)
    for extra in ("README", "README.md", "LICENSE"):
        if (script.parent / extra).is_file():
            shutil.copy2(script.parent / extra, target / extra)
    make_executable(target / "exiftool")
    wrapper = dest / "exiftool"
    wrapper.write_text(_WRAPPER, encoding="utf-8", newline="\n")
    make_executable(wrapper)
    sources.append((f"ExifTool {version} (script Perl)", url, digest))


def install_perl(tag: str, dest: Path, tmp: Path) -> None:
    system = "linux" if tag.startswith("linux") else "darwin"
    arch = "arm64" if tag.endswith("arm64") else "amd64"
    name = f"perl-{system}-{arch}.tar.xz"
    url = f"{PERL_BASE}/{name}"
    archive = tmp / name
    digest = download(url, archive)
    extracted = tmp / "perl-extract"
    extract(archive, extracted)
    roots = [p for p in extracted.iterdir() if p.is_dir()]
    if len(roots) != 1 or not (roots[0] / "bin" / "perl").exists():
        raise RuntimeError("Structure inattendue de l'archive Perl portable (bin/perl absent).")
    target = dest / "perl"
    if target.exists():
        shutil.rmtree(target)
    shutil.move(str(roots[0]), str(target))
    shutil.rmtree(target / "man", ignore_errors=True)         # allègement : ni pages de manuel ni POD
    for pod in target.rglob("*.pod"):
        pod.unlink()
    make_executable(target / "bin" / "perl")
    sources.append(("Perl portable (relocatable-perl)", url, digest))


# --------------------------------------------------------------------------- orchestration

def write_sources(dest: Path, tag: str) -> None:
    lines = [
        f"Binaires tiers embarqués dans Azimut ({tag}).",
        "Ils sont téléchargés à la construction du paquet depuis les adresses ci-dessous ;",
        "les sources correspondantes sont disponibles auprès de leurs projets respectifs.",
        "",
        "FFmpeg (variante GPL) : https://ffmpeg.org/ — code source : https://github.com/FFmpeg/FFmpeg",
        "Scripts de compilation BtbN : https://github.com/BtbN/FFmpeg-Builds",
        "Scripts de compilation Martin Riedl : https://git.martin-riedl.de/ffmpeg/build-script",
        "ExifTool (licence Perl / Artistic) : https://exiftool.org/",
        "Perl (licence Perl / Artistic) : https://github.com/skaji/relocatable-perl",
        "",
    ]
    for component, url, digest in sources:
        lines += [f"* {component}", f"    {url}", f"    sha256 : {digest}"]
    (dest / "BINARIES-SOURCES.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def expected_files(tag: str) -> list[str]:
    if tag.startswith("windows"):
        return ["ffmpeg.exe", "ffprobe.exe", "exiftool.exe", "exiftool_files"]
    return ["ffmpeg", "ffprobe", "exiftool", "exiftool-perl/lib", "perl/bin/perl"]


def verify(dest: Path, tag: str) -> bool:
    ok = True
    suffix = ".exe" if tag.startswith("windows") else ""
    for name, flag in (("ffmpeg", "-version"), ("ffprobe", "-version"), ("exiftool", "-ver")):
        exe = dest / (name + suffix)
        try:
            result = subprocess.run([str(exe), flag], capture_output=True, text=True, timeout=60)
            first = (result.stdout or result.stderr).strip().splitlines()[:1]
            if result.returncode != 0:
                raise RuntimeError(f"code de sortie {result.returncode} : {result.stderr.strip()[:200]}")
            log(f"  {name} : {first[0] if first else '(sans sortie)'}")
        except Exception as exc:  # noqa: BLE001
            log(f"  ERREUR : {name} ne s'exécute pas ({exc})")
            ok = False
    return ok


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--force", action="store_true", help="retélécharge même si bin/<plateforme>/ est déjà complet")
    parser.add_argument("--no-verify", action="store_true", help="ne lance pas les binaires après installation")
    args = parser.parse_args(argv)

    for stream in (sys.stdout, sys.stderr):      # console Windows en cp1252 : on force l'UTF-8
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    tag = platform_tag()
    dest = ROOT / "bin" / tag
    log(f"Plateforme : {tag} → {dest}")

    if not args.force and all((dest / item).exists() for item in expected_files(tag)):
        log("bin/%s/ est déjà complet ; rien à faire (--force pour retélécharger)." % tag)
        return 0

    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)

    with tempfile.TemporaryDirectory(prefix="azimut-fetch-") as tmp_name:
        tmp = Path(tmp_name)
        version = exiftool_version()
        log(f"ExifTool {version} ; FFmpeg branche « {FFMPEG_BRANCH} »")
        if tag == "windows-x64":
            install_ffmpeg_btbn("win64", "zip", dest, tmp)
            install_exiftool_windows(dest, tmp, version)
        elif tag == "linux-x64":
            install_ffmpeg_btbn("linux64", "tar.xz", dest, tmp)
            install_exiftool_unix(dest, tmp, version)
            install_perl(tag, dest, tmp)
        else:
            install_ffmpeg_macos("arm64" if tag == "macos-arm64" else "amd64", dest, tmp)
            install_exiftool_unix(dest, tmp, version)
            install_perl(tag, dest, tmp)

    write_sources(dest, tag)
    if not args.no_verify and not verify(dest, tag):
        return 1
    log("Terminé.")
    return 0


def _run() -> int:
    """Exécute main() ; sous GitHub Actions, expose l'erreur en annotation (visible sans le journal complet)."""
    try:
        return main()
    except Exception as exc:  # noqa: BLE001
        if os.environ.get("GITHUB_ACTIONS"):
            message = f"{type(exc).__name__}: {exc}".replace("\r", " ").replace("\n", " ")
            print(f"::error title=fetch_binaries::{message[:900]}", flush=True)
        raise


if __name__ == "__main__":
    sys.exit(_run())
