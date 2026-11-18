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

Verrou des binaires tiers (PKG-001, schéma docs/AUDIT/lot6_schema.md) :
    Par défaut, packaging/binaries.lock.json est lu AVANT tout réseau. Verrou absent, illisible,
    schéma inconnu ou entrée manquante : arrêt sans rien télécharger. Chaque archive vient d'une
    seule URL épinglée et sa taille puis son empreinte SHA-256 sont comparées avant extraction.
    Codes de sortie : 0 succès ; 1 binaires non exécutables ou erreur inattendue ; 2 verrou absent,
    illisible ou incomplet ; 3 empreinte ou taille différentes ; 4 URL épinglée injoignable.

Variables d'environnement facultatives (inactives en mode verrouillé : une valeur différente du
verrou fait échouer le script) :
    AZIMUT_FFMPEG_BRANCH       « n9.0 » par défaut ; « master » pour la branche de développement.
    AZIMUT_EXIFTOOL_VERSION    force une version d'ExifTool (ex. « 13.59 »).

Usage : python packaging/fetch_binaries.py [--force] [--no-selftest] [--allow-unpinned]
        python packaging/fetch_binaries.py --update-lock [--platform TAG]
                                            [--ffmpeg-branch B] [--exiftool-version V]
Bibliothèque standard uniquement.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
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
import urllib.parse
import urllib.request
import zipfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

BTBN_BASE = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest"
MARTIN_RIEDL = "https://ffmpeg.martin-riedl.de/redirect/latest/macos/{arch}/{build}/{name}"
EXIFTOOL_HOME = "https://exiftool.org"                 # n'héberge plus les archives : liens vers SourceForge
EXIFTOOL_SF = "https://sourceforge.net/projects/exiftool/files/{name}/download"
EXIFTOOL_GITHUB = "https://github.com/exiftool/exiftool/archive/refs/tags/{version}.tar.gz"
MAGIC = {"zip": b"PK\x03\x04", "gz": b"\x1f\x8b", "xz": b"\xfd7zXZ\x00"}   # signatures des archives
PERL_BASE = "https://github.com/skaji/relocatable-perl/releases/latest/download"

# Verrou des binaires tiers (PKG-001). Une entrée par archive réellement téléchargée.
LOCK_RELPATH = Path("packaging") / "binaries.lock.json"
LOCK_SCHEMA = 1
PLATFORMS = ("linux-x64", "macos-arm64", "macos-x64", "windows-x64")
ASSETS_BY_PLATFORM = {
    "windows-x64": {"ffmpeg", "exiftool"},          # Perl est intégré à exiftool.exe
    "linux-x64": {"ffmpeg", "exiftool", "perl"},
    "macos-arm64": {"ffmpeg", "ffprobe", "exiftool", "perl"},
    "macos-x64": {"ffmpeg", "ffprobe", "exiftool", "perl"},
}
MOVING_URL_MARKERS = ("/download/latest/", "/redirect/latest/")
MOVING_URL_NOTE = "Aucun chemin versionné connu ; l'empreinte seule garantit l'identité."

# Codes de sortie : la CI distingue « amont a bougé » (3) de « amont a disparu » (4).
EXIT_OK = 0
EXIT_ERROR = 1          # échec d'exécution des binaires (verify) ou erreur inattendue
EXIT_LOCK = 2           # verrou absent, illisible, schéma inconnu, entrée manquante
EXIT_INTEGRITY = 3      # empreinte ou taille différentes
EXIT_SOURCE = 4         # source injoignable : HTTP 404 sur l'URL épinglée, réseau


# Ces exceptions ne dérivent volontairement ni de RuntimeError, ni de OSError, ni de ValueError :
# download_first() (ligne « except (URLError, RuntimeError, ValueError, OSError) ») les laisserait
# sinon passer pour « source suivante » et contournerait l'épinglage (risque 6.6 du schéma).
class LockError(Exception):
    """Verrou absent, illisible ou incomplet (code 2)."""


class IntegrityError(Exception):
    """Octets téléchargés différents du verrou (code 3)."""


class SourceError(Exception):
    """URL épinglée injoignable (code 4)."""


FFMPEG_BRANCH = os.environ.get("AZIMUT_FFMPEG_BRANCH", "n9.0").strip() or "n9.0"
USER_AGENT = "azimut-packaging/1.0 (+https://github.com)"

sources: list[tuple[str, str, str]] = []        # (composant, url, sha256) pour BINARIES-SOURCES.txt
lock_note: str | None = None                    # mention du verrou dans BINARIES-SOURCES.txt


# --------------------------------------------------------------------------- utilitaires

_log = logging.getLogger("azimut.packaging.fetch_binaries")


def log(message: str) -> None:
    print(f"[fetch_binaries] {message}", flush=True)


def platform_tag() -> str:
    """Même résultat que azimut.core.binaries.platform_tag()."""
    arm = platform.machine().lower() in ("arm64", "aarch64")
    if sys.platform == "win32":
        return "windows-arm64" if arm else "windows-x64"
    if sys.platform == "darwin":
        return "macos-arm64" if arm else "macos-x64"
    return "linux-arm64" if arm else "linux-x64"


# --------------------------------------------------------------------------- verrou (PKG-001)

def lock_path() -> Path:
    return ROOT / LOCK_RELPATH


def _url_filename(url: str) -> str:
    """Dernier segment du chemin de l'URL ; le « /download » de SourceForge est ignoré (R6)."""
    segments = [segment for segment in urllib.parse.urlsplit(url).path.split("/") if segment]
    if not segments:
        raise LockError(f"URL sans nom de fichier : {url}")
    if len(segments) > 1 and segments[-1] == "download":
        return segments[-2]
    return segments[-1]


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise LockError(message)


def _validate_entry(entry: object, index: int, exiftool_version: str, path: Path) -> dict:
    where = f"{path} : entrée {index}"
    _require(isinstance(entry, dict), f"{where} : objet JSON attendu")
    platform_name = entry.get("platform")
    _require(platform_name in PLATFORMS, f"{where} : plateforme inconnue {platform_name!r}")
    asset = entry.get("asset")
    _require(isinstance(asset, str) and asset in ASSETS_BY_PLATFORM[platform_name],
             f"{where} : asset inconnu ou absent {asset!r}")
    expected_component = "ffmpeg" if asset in ("ffmpeg", "ffprobe") else asset
    _require(entry.get("component") == expected_component,
             f"{where} : component {entry.get('component')!r} incohérent avec asset {asset!r}")
    version = entry.get("version")
    _require(isinstance(version, str) and bool(version.strip()), f"{where} : version absente")
    url = entry.get("url")
    _require(isinstance(url, str) and url.startswith("https://"), f"{where} : url absente ou non https")
    expected_filename = _url_filename(url)
    _require(entry.get("filename") == expected_filename,
             f"{where} : filename {entry.get('filename')!r} différent du dernier segment de l'url "
             f"({expected_filename!r})")
    sha256 = entry.get("sha256")
    _require(isinstance(sha256, str) and re.fullmatch(r"[0-9a-f]{64}", sha256) is not None,
             f"{where} : sha256 invalide (64 hexadécimaux minuscules attendus)")
    magic = entry.get("magic")
    _require(isinstance(magic, str) and magic in MAGIC,
             f"{where} : magic {magic!r} inconnu (zip, gz ou xz)")
    if "size" in entry:
        size = entry["size"]
        _require(isinstance(size, int) and not isinstance(size, bool) and size > 0,
                 f"{where} : size doit être un entier > 0")
    immutable = entry.get("url_immutable", True)
    _require(isinstance(immutable, bool), f"{where} : url_immutable doit être un booléen")
    note = entry.get("note")
    if immutable is False:
        _require(isinstance(note, str) and bool(note.strip()),
                 f"{where} : note obligatoire quand url_immutable vaut false")
    for marker in MOVING_URL_MARKERS:
        if marker in url:
            _require(immutable is False and isinstance(note, str) and bool(note.strip()),
                     f"{where} : URL mouvante ({marker}) sans url_immutable: false et note motivée")
    if asset == "exiftool":
        _require(version == exiftool_version,
                 f"{where} : version {version!r} différente de exiftool_version {exiftool_version!r}")
    return entry


def _validate_lock(lock: object, path: Path) -> dict:
    """Applique R1-R3 et R5-R10 ; R4 (couverture) est vérifiée par platform_entries()."""
    _require(isinstance(lock, dict), f"{path} : objet JSON attendu")
    schema = lock.get("schema")
    _require(isinstance(schema, int) and not isinstance(schema, bool) and schema == LOCK_SCHEMA,
             f"{path} : schéma {schema!r} inconnu (seul {LOCK_SCHEMA} est accepté)")
    generated_at = lock.get("generated_at")
    _require(isinstance(generated_at, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", generated_at) is not None,
             f"{path} : generated_at doit être une date AAAA-MM-JJ")
    _require(isinstance(lock.get("generator"), str) and bool(lock["generator"].strip()),
             f"{path} : generator absent")
    exiftool_version = lock.get("exiftool_version")
    _require(isinstance(exiftool_version, str) and re.fullmatch(r"\d+\.\d+", exiftool_version) is not None,
             f"{path} : exiftool_version doit ressembler à « 13.59 »")
    _require(isinstance(lock.get("ffmpeg_branch", ""), str), f"{path} : ffmpeg_branch doit être une chaîne")
    artifacts = lock.get("artifacts")
    _require(isinstance(artifacts, list) and bool(artifacts), f"{path} : artifacts doit être un tableau non vide")
    seen: set[tuple[str, str]] = set()
    filenames: dict[str, set[str]] = {}
    for index, entry in enumerate(artifacts):
        _validate_entry(entry, index, exiftool_version, path)
        pair = (entry["platform"], entry["asset"])
        _require(pair not in seen, f"{path} : entrée en double {pair[0]}/{pair[1]}")
        seen.add(pair)
        names = filenames.setdefault(entry["platform"], set())
        _require(entry["filename"] not in names,
                 f"{path} : filename {entry['filename']!r} en double pour {entry['platform']}")
        names.add(entry["filename"])
    return lock


def _reject_constant(value: str) -> None:
    raise LockError(f"verrou illisible : constante JSON interdite ({value})")


def load_lock(path: Path | None = None) -> dict:
    """Charge et valide le verrou ; aucune requête réseau n'est faite ici."""
    path = Path(path) if path is not None else lock_path()
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise LockError(
            f"verrou absent : {path}\n"
            "  Rien n'a été téléchargé. Générez-le avec « python packaging/fetch_binaries.py --update-lock »,\n"
            "  relisez le diff, puis committez packaging/binaries.lock.json."
        ) from None
    except OSError as exc:
        raise LockError(f"verrou illisible : {path} ({exc})") from exc
    try:
        data = json.loads(text, parse_constant=_reject_constant)
    except json.JSONDecodeError as exc:
        raise LockError(f"verrou illisible : {path} ({exc})") from exc
    return _validate_lock(data, path)


def platform_entries(lock: dict, tag: str) -> list[dict]:
    """Entrées de `tag`, couverture exacte exigée (R4), triées par asset."""
    expected = ASSETS_BY_PLATFORM[tag]
    found = {item["asset"]: item for item in lock["artifacts"] if item["platform"] == tag}
    missing = sorted(expected - set(found))
    extra = sorted(set(found) - expected)
    if missing or extra:
        details = []
        if missing:
            details.append("manquante(s) : " + ", ".join(missing))
        if extra:
            details.append("en trop : " + ", ".join(extra))
        raise LockError(f"{lock_path()} : couverture de {tag} incomplète ({' ; '.join(details)})")
    return [found[asset] for asset in sorted(expected)]


def check_env_against_lock(lock: dict) -> None:
    """Une variable d'environnement qui contredit le verrou est une erreur, pas un silence (§ 4.5)."""
    forced_version = os.environ.get("AZIMUT_EXIFTOOL_VERSION", "").strip()
    if forced_version and forced_version != lock["exiftool_version"]:
        raise LockError(
            f"AZIMUT_EXIFTOOL_VERSION={forced_version} diffère du verrou ({lock['exiftool_version']}).\n"
            "  En mode verrouillé, retirez la variable ; pour changer de version : --update-lock."
        )
    forced_branch = os.environ.get("AZIMUT_FFMPEG_BRANCH", "").strip()
    if forced_branch and lock.get("ffmpeg_branch") and forced_branch != lock["ffmpeg_branch"]:
        raise LockError(
            f"AZIMUT_FFMPEG_BRANCH={forced_branch} diffère du verrou ({lock['ffmpeg_branch']}).\n"
            "  En mode verrouillé, retirez la variable ; pour changer de branche : --update-lock."
        )


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


def _content_length(response) -> int | None:
    headers = getattr(response, "headers", None)
    value = headers.get("Content-Length") if headers is not None else None
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _nothing_installed() -> str:
    return ("  Rien n'a été installé. Si le changement est légitime (nouvelle version amont),\n"
            "  relancez « python packaging/fetch_binaries.py --update-lock », relisez le diff du\n"
            "  verrou, puis committez-le. Essai local sans garantie d'identité : --allow-unpinned.")


def _hash_error(entry: dict, got: str) -> IntegrityError:
    return IntegrityError(
        f"empreinte SHA-256 différente pour {entry['filename']}\n"
        f"  attendu (packaging/binaries.lock.json) : {entry['sha256']}\n"
        f"  obtenu                                 : {got}\n" + _nothing_installed())


def _size_error(entry: dict, got: int) -> IntegrityError:
    return IntegrityError(
        f"taille différente pour {entry['filename']}\n"
        f"  attendu (packaging/binaries.lock.json) : {entry['size']} octets\n"
        f"  obtenu                                 : {got} octets\n" + _nothing_installed())


def download_entry(entry: dict, tmp: Path) -> Path:
    """Télécharge l'URL épinglée, contrôle taille puis empreinte, et renvoie l'archive vérifiée.

    Une seule URL : aucun repli de source, aucun téléchargement supplémentaire (PKG-001).
    """
    url = entry["url"]
    target = tmp / entry["filename"]
    log(f"Téléchargement (verrou) : {url}")
    try:
        response = urllib.request.urlopen(_request(url), timeout=120)
    except urllib.error.HTTPError as exc:
        raise SourceError(f"HTTP {exc.code} sur l'URL épinglée : {url}") from exc
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
        raise SourceError(f"source injoignable : {url} ({exc})") from exc
    with response:
        declared = _content_length(response)
        if "size" in entry and declared is not None and declared != entry["size"]:
            raise _size_error(entry, declared)
        digest = hashlib.sha256()
        total = 0
        with target.open("wb") as out:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
                digest.update(chunk)
                total += len(chunk)
    if "size" in entry and total != entry["size"]:
        target.unlink(missing_ok=True)
        raise _size_error(entry, total)
    got = digest.hexdigest()
    if got != entry["sha256"]:
        target.unlink(missing_ok=True)
        raise _hash_error(entry, got)
    with target.open("rb") as handle:
        head = handle.read(len(MAGIC[entry["magic"]]))
    if head != MAGIC[entry["magic"]]:
        target.unlink(missing_ok=True)
        raise IntegrityError(f"{entry['filename']} n'est pas une archive {entry['magic']} (contenu inattendu).")
    log(f"  → {target.name} : {total / 1024 / 1024:.1f} Mo, empreinte conforme au verrou")
    return target


def extract_zip(archive: Path, target: Path) -> None:
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(target)


def safe_members(tf: tarfile.TarFile, target: Path) -> list[tarfile.TarInfo]:
    """Membres extractibles dans `target` : ni chemin absolu, ni « .. », ni lien qui en sort."""
    root = target.resolve()
    safe: list[tarfile.TarInfo] = []
    for member in tf.getmembers():
        name = Path(member.name)
        if name.is_absolute() or ".." in name.parts or member.isdev() or member.isfifo():
            continue
        dest = (root / name).resolve()
        if not dest.is_relative_to(root):
            continue
        if member.issym() or member.islnk():
            link = Path(member.linkname)
            base = dest.parent if member.issym() else root
            if link.is_absolute() or not (base / link).resolve().is_relative_to(root):
                continue
        safe.append(member)
    return safe


def extract_tar(archive: Path, target: Path) -> None:
    with tarfile.open(archive) as tf:
        if hasattr(tarfile, "data_filter"):     # Python ≥ 3.10.12 / 3.11.4 / 3.12 : extraction sûre
            tf.extractall(target, filter="data")
        else:                                   # Python 3.11.0–3.11.3 : pas de filtre intégré
            tf.extractall(target, members=safe_members(tf, target))


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


def install_ffmpeg_archive(archive: Path, windows: bool, dest: Path, tmp: Path) -> None:
    """Installe ffmpeg et ffprobe depuis une archive BtbN (une archive pour les deux outils)."""
    extracted = tmp / "ffmpeg-extract"
    extract(archive, extracted)
    suffix = ".exe" if windows else ""
    for tool in ("ffmpeg", "ffprobe"):
        src = find_file(extracted, tool + suffix, "bin")
        shutil.copy2(src, dest / (tool + suffix))
        make_executable(dest / (tool + suffix))


def install_ffmpeg_tool_archive(archive: Path, tool: str, dest: Path, tmp: Path) -> None:
    """Installe un seul outil depuis le zip Martin Riedl (ffmpeg.zip ou ffprobe.zip)."""
    extracted = tmp / f"{tool}-extract"
    extract(archive, extracted)
    shutil.copy2(find_file(extracted, tool), dest / tool)
    make_executable(dest / tool)


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
        install_ffmpeg_archive(archive, target.startswith("win"), dest, tmp)
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
        install_ffmpeg_tool_archive(archive, tool, dest, tmp)
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


def install_exiftool_windows_archive(archive: Path, dest: Path, tmp: Path) -> None:
    extracted = tmp / "exiftool-extract"
    extract(archive, extracted)
    exe = find_file(extracted, "exiftool(-k).exe")
    shutil.copy2(exe, dest / "exiftool.exe")
    shutil.copytree(exe.parent / "exiftool_files", dest / "exiftool_files", dirs_exist_ok=True)


def install_exiftool_windows(dest: Path, tmp: Path, version: str) -> None:
    name = f"exiftool-{version}_64.zip"
    archive, digest, url = download_first([
        (EXIFTOOL_SF.format(name=name), name),                 # source officielle actuelle (SourceForge)
        (f"{EXIFTOOL_HOME}/{name}", "home-" + name),           # ancien emplacement, au cas où il revienne
    ], tmp, "zip")
    _verify_exiftool(name, digest, version)
    install_exiftool_windows_archive(archive, dest, tmp)
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


def install_exiftool_unix_archive(archive: Path, dest: Path, tmp: Path) -> None:
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


def install_exiftool_unix(dest: Path, tmp: Path, version: str) -> None:
    name = f"Image-ExifTool-{version}.tar.gz"
    archive, digest, url = download_first([
        (EXIFTOOL_GITHUB.format(version=version), f"exiftool-{version}-github.tar.gz"),   # dépôt officiel, pérenne
        (EXIFTOOL_SF.format(name=name), name),
        (f"{EXIFTOOL_HOME}/{name}", "home-" + name),
    ], tmp, "gz")
    if "github.com" not in url:
        _verify_exiftool(name, digest, version)
    install_exiftool_unix_archive(archive, dest, tmp)
    sources.append((f"ExifTool {version} (script Perl)", url, digest))


def install_perl_archive(archive: Path, dest: Path, tmp: Path) -> None:
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


def install_perl(tag: str, dest: Path, tmp: Path) -> None:
    system = "linux" if tag.startswith("linux") else "darwin"
    arch = "arm64" if tag.endswith("arm64") else "amd64"
    name = f"perl-{system}-{arch}.tar.xz"
    url = f"{PERL_BASE}/{name}"
    archive = tmp / name
    digest = download(url, archive)
    install_perl_archive(archive, dest, tmp)
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
    if lock_note:
        lines += [f"verrou : {lock_note}", ""]
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
            result = subprocess.run([str(exe), flag], capture_output=True, text=True, timeout=60, check=False)
            first = (result.stdout or result.stderr).strip().splitlines()[:1]
            if result.returncode != 0:
                raise RuntimeError(f"code de sortie {result.returncode} : {result.stderr.strip()[:200]}")
            log(f"  {name} : {first[0] if first else '(sans sortie)'}")
        except Exception as exc:  # noqa: BLE001
            log(f"  ERREUR : {name} ne s'exécute pas ({exc})")
            ok = False
    return ok


# --------------------------------------------------------------------------- installation épinglée

def _locked_label(tag: str, entry: dict) -> str:
    asset = entry["asset"]
    if asset == "ffmpeg":
        return "FFmpeg (verrouillé, macOS)" if tag.startswith("macos") else "FFmpeg + FFprobe (verrouillé)"
    if asset == "ffprobe":
        return "FFprobe (verrouillé, macOS)"
    if asset == "exiftool":
        return f"ExifTool {entry['version']} (verrouillé)"
    return f"Perl portable {entry['version']} (verrouillé)"


def install_from_lock(tag: str, entries: list[dict], dest: Path, tmp: Path) -> None:
    """Télécharge chaque entrée épinglée (une seule URL) puis installe l'archive vérifiée."""
    for entry in entries:
        archive = download_entry(entry, tmp)
        asset = entry["asset"]
        if asset in ("ffmpeg", "ffprobe") and tag.startswith("macos"):
            install_ffmpeg_tool_archive(archive, asset, dest, tmp)
        elif asset == "ffmpeg":
            install_ffmpeg_archive(archive, tag.startswith("windows"), dest, tmp)
        elif asset == "exiftool" and tag.startswith("windows"):
            install_exiftool_windows_archive(archive, dest, tmp)
        elif asset == "exiftool":
            install_exiftool_unix_archive(archive, dest, tmp)
        elif asset == "perl":
            install_perl_archive(archive, dest, tmp)
        else:                                    # pragma: no cover - écarté par la validation du verrou
            raise LockError(f"entrée inattendue dans le verrou : {entry['platform']}/{asset}")
        sources.append((_locked_label(tag, entry), entry["url"], entry["sha256"]))


# --------------------------------------------------------------------------- --update-lock

def _planned_assets(tag: str, branch: str) -> list[dict]:
    """Archives FFmpeg à épingler pour `tag`, avec les sources de découverte actuelles."""
    if tag == "windows-x64":
        name = _btbn_asset(branch, "win64", "zip")
        return [{"asset": "ffmpeg", "version": branch, "magic": "zip", "candidates": [f"{BTBN_BASE}/{name}"]}]
    if tag == "linux-x64":
        name = _btbn_asset(branch, "linux64", "tar.xz")
        return [{"asset": "ffmpeg", "version": branch, "magic": "xz", "candidates": [f"{BTBN_BASE}/{name}"]}]
    arch = "arm64" if tag == "macos-arm64" else "amd64"
    return [
        {"asset": tool, "version": "release", "magic": "zip",
         "candidates": [MARTIN_RIEDL.format(arch=arch, build=build, name=f"{tool}.zip")
                        for build in ("release", "snapshot")]}
        for tool in ("ffmpeg", "ffprobe")
    ]


def _exiftool_plan(tag: str, version: str) -> dict:
    if tag.startswith("windows"):
        name = f"exiftool-{version}_64.zip"
        return {"asset": "exiftool", "version": version, "magic": "zip",
                "candidates": [EXIFTOOL_SF.format(name=name), f"{EXIFTOOL_HOME}/{name}"]}
    name = f"Image-ExifTool-{version}.tar.gz"
    return {"asset": "exiftool", "version": version, "magic": "gz",
            "candidates": [EXIFTOOL_GITHUB.format(version=version), EXIFTOOL_SF.format(name=name),
                           f"{EXIFTOOL_HOME}/{name}"]}


def _perl_plan(tag: str) -> dict:
    system = "linux" if tag.startswith("linux") else "darwin"
    arch = "arm64" if tag.endswith("arm64") else "amd64"
    return {"asset": "perl", "version": "latest", "magic": "xz",
            "candidates": [f"{PERL_BASE}/perl-{system}-{arch}.tar.xz"]}


def _lock_entry(tag: str, plan: dict, url: str, digest: str, size: int) -> dict:
    version = plan["version"]
    if plan["asset"] in ("ffmpeg", "ffprobe") and tag.startswith("macos") and "/snapshot/" in url:
        version = "snapshot"
    entry = {"platform": tag, "asset": plan["asset"],
             "component": "ffmpeg" if plan["asset"] in ("ffmpeg", "ffprobe") else plan["asset"],
             "version": version, "url": url}
    if any(marker in url for marker in MOVING_URL_MARKERS) or "/latest/" in url:
        entry["url_immutable"] = False
        entry["note"] = MOVING_URL_NOTE
    entry.update({"filename": _url_filename(url), "sha256": digest, "size": size, "magic": plan["magic"]})
    return entry


def _cross_check(tag: str, asset: str, url: str, digest: str, version: str) -> None:
    """Recoupe avec les listes de sommes amont quand elles existent (désaccord = arrêt)."""
    name = _url_filename(url)
    if asset == "ffmpeg" and not tag.startswith("macos"):
        _verify_btbn(name, digest)
    elif asset == "exiftool" and "github.com" not in url:
        _verify_exiftool(name, digest, version)


def _serialize_lock(lock: dict) -> str:
    return json.dumps(lock, indent=2, ensure_ascii=False) + "\n"


def _same_lock(previous: dict | None, candidate: dict) -> bool:
    if not previous:
        return False
    fields = ("schema", "exiftool_version", "ffmpeg_branch", "artifacts")
    return all(previous.get(field) == candidate.get(field) for field in fields)


def _log_lock_diff(previous: dict, candidate: dict) -> None:
    old = {(item["platform"], item["asset"]): item for item in previous["artifacts"]}
    new = {(item["platform"], item["asset"]): item for item in candidate["artifacts"]}
    for key in sorted(set(old) | set(new)):
        label = f"{key[0]}/{key[1]}"
        if key not in old:
            log(f"  ajout    {label} : {new[key]['version']} {new[key]['sha256'][:16]}…")
        elif key not in new:
            log(f"  retiré   {label}")
        elif old[key] != new[key]:
            changes = [f"{field} {old[key].get(field)!r} → {new[key].get(field)!r}"
                       for field in ("version", "url", "sha256", "size")
                       if old[key].get(field) != new[key].get(field)]
            log(f"  modifié  {label} : " + " ; ".join(changes))


def update_lock(platforms: list[str], branch: str, version: str | None, command: str) -> int:
    """Écrit packaging/binaries.lock.json (mainteneur) ; jamais atteint par le chemin par défaut."""
    try:
        exiftool = version or exiftool_version()
    except RuntimeError as exc:
        log(f"ERREUR : {exc}")
        return EXIT_SOURCE
    path = lock_path()
    previous = None
    if path.exists():
        try:
            previous = load_lock(path)
        except LockError as exc:
            log(f"AVERTISSEMENT : verrou précédent ignoré ({exc}) ; il sera remplacé.")
    artifacts: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="azimut-lock-") as tmp_name:
        tmp = Path(tmp_name)
        for tag in platforms:
            plans = _planned_assets(tag, branch) + [_exiftool_plan(tag, exiftool)]
            if "perl" in ASSETS_BY_PLATFORM[tag]:   # Windows : Perl est intégré à exiftool.exe
                plans.append(_perl_plan(tag))
            for plan in plans:
                label = f"{tag}/{plan['asset']}"
                candidates = [(url, _url_filename(url)) for url in plan["candidates"]]
                try:
                    archive, digest, url = download_first(candidates, tmp, plan["magic"])
                except (urllib.error.URLError, RuntimeError, ValueError, OSError) as exc:
                    log(f"ERREUR : {label} — {exc}")
                    return EXIT_SOURCE
                try:
                    _cross_check(tag, plan["asset"], url, digest, exiftool)
                except RuntimeError as exc:
                    log(f"ERREUR : {label} — {exc}")
                    return EXIT_INTEGRITY
                item = _lock_entry(tag, plan, url, digest, archive.stat().st_size)
                artifacts.append(item)
                log(f"  {label} : {item['version']} {item['sha256'][:16]}… ({item['size']} octets)")
    if previous:
        artifacts.extend(item for item in previous["artifacts"] if item["platform"] not in platforms)
    artifacts.sort(key=lambda item: (item["platform"], item["asset"]))
    candidate = {
        "schema": LOCK_SCHEMA,
        "generated_at": date.today().isoformat(),
        "generator": command,
        "exiftool_version": exiftool,
        "ffmpeg_branch": branch,
        "artifacts": artifacts,
    }
    if _same_lock(previous, candidate):
        log("Verrou inchangé ; fichier non réécrit.")
        return EXIT_OK
    if previous:
        _log_lock_diff(previous, candidate)
    text = _serialize_lock(candidate)
    _validate_lock(json.loads(text), path)          # garde-fou : on n'écrit pas un verrou invalide
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    log(f"Verrou écrit : {path} ({len(artifacts)} entrées). Relisez le diff avant de committer.")
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    global lock_note

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--force", action="store_true", help="retélécharge même si bin/<plateforme>/ est déjà complet")
    parser.add_argument("--no-selftest", dest="no_selftest", action="store_true",
                        help="n'exécute pas les binaires après installation (les empreintes restent vérifiées)")
    parser.add_argument("--no-verify", dest="no_selftest", action="store_true",
                        help="alias déprécié de --no-selftest ; ne désactive AUCUNE vérification d'empreinte")
    parser.add_argument("--allow-unpinned", action="store_true",
                        help="télécharge sans comparer au verrou (aucune garantie d'identité ; jamais en CI)")
    parser.add_argument("--update-lock", action="store_true",
                        help="réécrit packaging/binaries.lock.json depuis les sources amont (mainteneur)")
    parser.add_argument("--platform", action="append", choices=PLATFORMS, metavar="TAG",
                        help="avec --update-lock : ne réécrit que cette plateforme (répétable)")
    parser.add_argument("--ffmpeg-branch", metavar="BRANCHE",
                        help="avec --update-lock : branche FFmpeg (défaut : AZIMUT_FFMPEG_BRANCH ou n9.0)")
    parser.add_argument("--exiftool-version", metavar="VERSION",
                        help="avec --update-lock : version d'ExifTool à épingler")
    raw = list(sys.argv[1:] if argv is None else argv)
    args = parser.parse_args(raw)

    for stream in (sys.stdout, sys.stderr):      # console Windows en cp1252 : on force l'UTF-8
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError) as exc:
            _log.debug("Reconfiguration de la console ignorée : %s", exc)

    if "--no-verify" in raw:
        log("AVERTISSEMENT : --no-verify est déprécié ; utilisez --no-selftest "
            "(les empreintes sont vérifiées dans les deux cas).")

    if args.update_lock:
        if args.allow_unpinned:
            log("ERREUR : --update-lock et --allow-unpinned sont incompatibles (le verrou ne s'écrit pas "
                "depuis un téléchargement non vérifié).")
            return EXIT_LOCK
        command = "python packaging/fetch_binaries.py --update-lock"
        if args.platform:
            command += "".join(f" --platform {tag}" for tag in args.platform)
        if args.ffmpeg_branch:
            command += f" --ffmpeg-branch {args.ffmpeg_branch}"
        if args.exiftool_version:
            command += f" --exiftool-version {args.exiftool_version}"
        return update_lock(args.platform or list(PLATFORMS), args.ffmpeg_branch or FFMPEG_BRANCH,
                           args.exiftool_version, command)

    if args.platform or args.ffmpeg_branch or args.exiftool_version:
        log("ERREUR : --platform, --ffmpeg-branch et --exiftool-version ne servent qu'avec --update-lock.")
        return EXIT_LOCK

    tag = platform_tag()
    dest = ROOT / "bin" / tag
    log(f"Plateforme : {tag} → {dest}")

    if not args.force and all((dest / item).exists() for item in expected_files(tag)):
        log("bin/%s/ est déjà complet ; rien à faire (--force pour retélécharger)." % tag)
        return EXIT_OK

    entries: list[dict] | None = None
    if args.allow_unpinned:
        lock_note = "IGNORÉ (--allow-unpinned)"
        log("AVERTISSEMENT : --allow-unpinned — AUCUNE garantie d'identité sur les binaires tiers.")
        log("Ne jamais utiliser en CI ni pour un paquet destiné à la publication.")
    else:
        try:
            lock = load_lock()
            entries = platform_entries(lock, tag)
            check_env_against_lock(lock)
        except LockError as exc:
            log(f"ERREUR : {exc}")
            return EXIT_LOCK
        lock_note = f"binaries.lock.json, schéma {lock['schema']}, {lock['generated_at']}"

    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)

    with tempfile.TemporaryDirectory(prefix="azimut-fetch-") as tmp_name:
        tmp = Path(tmp_name)
        try:
            if entries is not None:
                install_from_lock(tag, entries, dest, tmp)
            else:
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
        except LockError as exc:
            shutil.rmtree(dest, ignore_errors=True)
            log(f"ERREUR : {exc}")
            return EXIT_LOCK
        except IntegrityError as exc:
            shutil.rmtree(dest, ignore_errors=True)
            log(f"ERREUR : {exc}")
            return EXIT_INTEGRITY
        except SourceError as exc:
            shutil.rmtree(dest, ignore_errors=True)
            log(f"ERREUR : {exc}")
            return EXIT_SOURCE

    write_sources(dest, tag)
    if not args.no_selftest and not verify(dest, tag):
        return EXIT_ERROR
    log("Terminé.")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
