"""Localisation de FFmpeg, FFprobe et ExifTool : embarqué, chemin personnalisé, puis PATH système."""
from __future__ import annotations

import os
import platform
import re
import shutil
import signal
import stat
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

from azimut.core.logs import get_logger
from azimut.core.paths import app_root
from azimut.core.prefs import Preferences

_log = get_logger("binaries")

KNOWN_BINARIES: tuple[str, ...] = ("ffmpeg", "ffprobe", "exiftool")

_SOURCE_LABELS = {"bundled": "embarqué", "custom": "personnalisé", "system": "système"}
MIN_VERSIONS: dict[str, tuple[int, ...]] = {
    "exiftool": (12, 24),      # CVE-2021-22204 (DjVu) corrigée en 12.24
    "ffmpeg": (6, 0),
    "ffprobe": (6, 0),
}
_VERSION_TIMEOUT = 10.0
_IS_WINDOWS = sys.platform == "win32"
_NO_WINDOW = {"creationflags": subprocess.CREATE_NO_WINDOW} if _IS_WINDOWS else {}
_CAPTURE_MAX = 64 * 1024        # tête de stdout conservée : un binaire bavard ne gonfle pas la mémoire
_KILL_GRACE = 1.0               # délai laissé après SIGTERM avant SIGKILL
_TASKKILL_TIMEOUT = 5.0         # délai laissé à taskkill sous Windows


class BinaryNotFoundError(RuntimeError):
    """Un binaire obligatoire est introuvable."""


@dataclass(frozen=True)
class BinaryInfo:
    name: str
    path: Path | None
    source: str                 # "bundled" | "custom" | "system" | "missing"
    version: str                # « 7.1 », « 12.76 »… ; "" si inconnue
    outdated: bool = False      # version inférieure au minimum de sécurité connu

    @property
    def available(self) -> bool:
        return self.path is not None


def _version_tuple(text: str) -> tuple[int, ...]:
    """« 12.76 » → (12, 76) ; « 7.1.1-static » → (7, 1, 1) ; () si illisible."""
    found = re.findall(r"\d+", text or "")
    return tuple(int(x) for x in found[:3]) if found else ()


def is_outdated(name: str, version: str) -> bool:
    """Vrai si la version connue est antérieure au minimum recommandé (AZ-009)."""
    minimum = MIN_VERSIONS.get(name)
    current = _version_tuple(version)
    return bool(minimum and current and current < minimum)


def _system_path() -> str:
    """PATH sans entrée vide ni relative (Windows chercherait dans le dossier courant)."""
    entries = [entry for entry in os.environ.get("PATH", "").split(os.pathsep)
               if entry and os.path.isabs(entry)]
    return os.pathsep.join(entries)


_cache: dict[str, BinaryInfo] = {}
_cache_lock = threading.RLock()
_cache_epoch = 0                                 # incrémenté à chaque refresh() : invalide les sondes en cours
_refresh_thread: threading.Thread | None = None   # dernier sondage de versions lancé en arrière-plan


def platform_tag() -> str:
    """« windows-x64 », « linux-x64 », « macos-arm64 », « macos-x64 », « windows-arm64 » ou « linux-arm64 ».

    Les deux derniers n'ont pas de binaires embarqués : l'application se rabat alors sur FFmpeg et ExifTool du
    système (PATH). Annoncer « x64 » sur une machine ARM ferait lancer des exécutables du mauvais format
    (« Exec format error », « n'est pas une application Win32 valide »). Un Python x64 émulé sous Windows ARM
    rapporte « AMD64 » : il garde le tag x64 et ses binaires x64, qui fonctionnent par émulation (AZ-C04).
    """
    arm = platform.machine().lower() in ("arm64", "aarch64")
    if sys.platform == "win32":
        return "windows-arm64" if arm else "windows-x64"
    if sys.platform == "darwin":
        return "macos-arm64" if arm else "macos-x64"
    return "linux-arm64" if arm else "linux-x64"


def bundled_dir() -> Path:
    """Dossier des binaires embarqués : ``<app_root>/bin/<platform_tag>``."""
    return app_root() / "bin" / platform_tag()


def _exe_name(name: str) -> str:
    return f"{name}.exe" if sys.platform == "win32" else name


def _is_executable(path: Path) -> bool:
    if not path.is_file():
        return False
    return sys.platform == "win32" or os.access(path, os.X_OK)


def _bundled_candidate(name: str) -> Path | None:
    path = bundled_dir() / _exe_name(name)
    if not path.is_file():
        return None
    if _is_executable(path):
        return path
    try:                                           # fichier extrait sans bit d'exécution
        os.chmod(path, path.stat().st_mode | stat.S_IXUSR)
    except OSError as exc:
        _log.debug("Bit d'exécution non posé sur %s : %s", path, exc)
    if _is_executable(path):
        return path
    _log.warning("Le binaire embarqué « %s » n'est pas exécutable : ignoré (%s).", name, path)
    return None


def custom_path(value: str) -> Path | None:
    """Chemin saisi par l'utilisateur s'il est utilisable, sinon ``None``.

    Mêmes règles que le candidat personnalisé de ``_locate`` : « ~ » est développé, le chemin
    doit être absolu (un relatif dépendrait du dossier courant) et le fichier exécutable.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    path = Path(value.strip()).expanduser()
    if not path.is_absolute() or not _is_executable(path):
        return None
    return path


def _custom_candidate(name: str, prefs: Preferences) -> Path | None:
    value = prefs.get(f"binaries.{name}", "")
    if not isinstance(value, str) or not value.strip():
        return None
    path = custom_path(value)
    if path is not None:
        return path
    raw = Path(value.strip()).expanduser()
    if not raw.is_absolute():                      # comme les entrées relatives du PATH : dossier courant variable
        _log.warning("Chemin personnalisé relatif ignoré pour « %s » (dépend du dossier courant) : %s", name, raw)
    else:
        _log.warning("Chemin personnalisé invalide pour « %s » (absent ou non exécutable) : %s", name, raw)
    return None


def _locate(name: str, prefs: Preferences, *, skip_bundled: bool = False) -> tuple[Path | None, str]:
    """Candidat retenu et son origine ; ``skip_bundled`` écarte l'embarqué (version illisible)."""
    if not skip_bundled:
        path = _bundled_candidate(name)
        if path is not None:
            return path, "bundled"
    path = _custom_candidate(name, prefs)
    if path is not None:
        return path, "custom"
    found = shutil.which(name, path=_system_path())
    if found:
        return Path(found), "system"
    return None, "missing"


def _read_capped(stream, kept: bytearray) -> None:
    """Vide stdout en gardant au plus ``_CAPTURE_MAX`` octets : la mémoire reste bornée (SUBPROC-002)."""
    try:
        while chunk := stream.read1(8192):
            if len(kept) < _CAPTURE_MAX:
                kept += chunk[: _CAPTURE_MAX - len(kept)]
    except (OSError, ValueError) as exc:
        _log.debug("Lecture de la sortie du binaire interrompue : %s", exc)


def _kill_probe(proc: subprocess.Popen) -> None:
    """Tue le sous-arbre de la sonde : groupe POSIX, ``taskkill /T`` sous Windows (SUBPROC-001)."""
    if proc.poll() is not None:
        return
    if _IS_WINDOWS:
        try:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], timeout=_TASKKILL_TIMEOUT,
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, check=False, **_NO_WINDOW)
        except (OSError, subprocess.SubprocessError) as exc:
            _log.debug("taskkill a échoué pour la sonde %s : %s", proc.pid, exc)
        try:
            proc.wait(timeout=_KILL_GRACE)
            return
        except subprocess.TimeoutExpired as exc:
            _log.debug("La sonde %s ignore taskkill : %s", proc.pid, exc)
        try:
            proc.kill()
        except OSError as exc:
            _log.debug("kill() impossible sur la sonde %s : %s", proc.pid, exc)
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except OSError as exc:
        _log.debug("SIGTERM au groupe de la sonde %s impossible : %s", proc.pid, exc)
    else:
        try:
            proc.wait(timeout=_KILL_GRACE)
            return
        except subprocess.TimeoutExpired as exc:
            _log.debug("La sonde %s ignore le SIGTERM du groupe : %s", proc.pid, exc)
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except OSError as exc:
        _log.debug("SIGKILL au groupe de la sonde %s impossible : %s", proc.pid, exc)
        try:
            proc.kill()
        except OSError as kill_exc:
            _log.debug("kill() impossible sur la sonde %s : %s", proc.pid, kill_exc)


def probe_version(name: str, path: Path) -> str:
    """Version du binaire (``-version`` pour FFmpeg/FFprobe, ``-ver`` pour ExifTool) ; "" en cas d'échec.

    La sortie est drainée en continu et seule sa tête est conservée ; au timeout, la sonde tue son groupe
    (ou son arbre Windows) pour ne pas laisser vivre le petit-fils d'un wrapper.
    """
    if name in ("ffmpeg", "ffprobe"):
        args = [str(path), "-version"]
    elif name == "exiftool":
        args = [str(path), "-ver"]
    else:
        return ""
    try:
        proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, start_new_session=not _IS_WINDOWS,
                                **_NO_WINDOW)
    except OSError:
        return ""
    out = bytearray()
    reader = threading.Thread(target=_read_capped, args=(proc.stdout, out), daemon=True)
    reader.start()
    try:
        try:
            proc.wait(timeout=_VERSION_TIMEOUT)
        except subprocess.TimeoutExpired:
            _kill_probe(proc)
        reader.join(timeout=2.0)                     # lisser ce qui reste dans le tuyau
        if proc.returncode != 0:
            return ""
        lines = bytes(out).decode("utf-8", errors="replace").strip().splitlines()
        first = lines[0].strip() if lines else ""
        if name == "exiftool":
            return first
        match = re.search(r"version\s+(\S+)", first, re.IGNORECASE)
        return match.group(1) if match else ""
    except Exception:                              # délai, binaire illisible, etc.
        return ""
    finally:
        if not reader.is_alive():
            try:
                proc.stdout.close()
            except (OSError, ValueError) as exc:
                _log.debug("Fermeture de stdout ignorée : %s", exc)


def _detected(name: str, path: Path | None, source: str, version: str) -> BinaryInfo:
    """Construit l'information d'un binaire et signale une version trop ancienne (AZ-009)."""
    info = BinaryInfo(name=name, path=path, source=source, version=version,
                      outdated=is_outdated(name, version))
    if info.outdated:
        _log.warning("%s %s est antérieur au minimum recommandé %s ; mettez-le à jour.",
                     name, version, ".".join(str(x) for x in MIN_VERSIONS.get(name, ())))
    return info


def _log_binary(info: BinaryInfo) -> None:
    """Journalise une détection : introuvable, ou version + origine + chemin."""
    if info.path is None:
        _log.warning("%s introuvable (ni embarqué, ni dans les préférences, ni dans le PATH)", info.name)
        return
    label = f"{info.name} {info.version}".strip() if info.version else f"{info.name} (version inconnue)"
    if info.outdated:
        label += " — VERSION ANTÉRIEURE AU MINIMUM RECOMMANDÉ"
    _log.info("%s — %s : %s", label, _SOURCE_LABELS.get(info.source, info.source), info.path)


def _resolve_with_fallback(name: str, prefs: Preferences) -> BinaryInfo:
    """Localise et sonde ; un binaire embarqué muet est un fichier invalide, le candidat suivant prend sa place."""
    path, source = _locate(name, prefs)
    version = probe_version(name, path) if path is not None else ""
    if source == "bundled" and path is not None and not version:
        _log.warning("Binaire embarqué « %s » inutilisable (aucune version lisible) : ignoré (%s).", name, path)
        path, source = _locate(name, prefs, skip_bundled=True)
        version = probe_version(name, path) if path is not None else ""
    return _detected(name, path, source, version)


def resolve(name: str, prefs: Preferences | None = None) -> BinaryInfo:
    """Localise un binaire (résultat mis en cache jusqu'au prochain ``refresh()``).

    Ordre : 1) embarqué ; 2) chemin personnalisé (préférence « binaries.<name> ») ; 3) PATH système.
    """
    with _cache_lock:
        cached = _cache.get(name)
    if cached is not None:
        return cached
    if prefs is None:
        prefs = Preferences.instance()
    info = _resolve_with_fallback(name, prefs)
    with _cache_lock:
        return _cache.setdefault(name, info)


def require(name: str, prefs: Preferences | None = None) -> Path:
    """Chemin du binaire, ou ``BinaryNotFoundError`` (message en français)."""
    info = resolve(name, prefs)
    if info.path is None:
        raise BinaryNotFoundError(
            f"« {name} » est introuvable. Installez-le, placez-le dans « bin/{platform_tag()} » "
            "ou indiquez son chemin dans les préférences.")
    return info.path


def refresh_in_progress() -> bool:
    """Vrai tant qu'une sonde de versions lancée par ``refresh(background=True)`` tourne encore."""
    thread = _refresh_thread
    return thread is not None and thread.is_alive()


def refresh(prefs: Preferences | None = None, *, background: bool = False) -> dict[str, BinaryInfo]:
    """Vide le cache, re-détecte les binaires connus et journalise le résultat.

    ``background=True`` rend la main sans lancer de processus : la localisation (rapide) est publiée
    tout de suite, les versions sont sondées dans un fil d'arrière-plan puis remplacées dans le cache.
    Un ``refresh()`` ultérieur invalide la publication du fil précédent.
    """
    global _cache_epoch, _refresh_thread
    if prefs is None:
        prefs = Preferences.instance()
    with _cache_lock:
        _cache.clear()
        _cache_epoch += 1
        epoch = _cache_epoch
        located: dict[str, BinaryInfo] = {}
        for name in KNOWN_BINARIES:
            path, source = _locate(name, prefs)
            located[name] = _detected(name, path, source, "")
            _cache[name] = located[name]
    if not background:
        found: dict[str, BinaryInfo] = {}
        for name in KNOWN_BINARIES:
            info = _resolve_with_fallback(name, prefs)
            with _cache_lock:
                if _cache_epoch == epoch:
                    _cache[name] = info
            found[name] = info
            _log_binary(info)
        return found

    _log.info("Détection des binaires en arrière-plan (versions sondées hors de l'interface)…")

    def probe_all() -> None:
        for name in KNOWN_BINARIES:
            try:
                info = _resolve_with_fallback(name, prefs)
            except Exception:                            # une sonde fautive ne prive pas les suivantes de résultat
                _log.exception("Sonde de version impossible pour « %s »", name)
                continue
            with _cache_lock:
                if _cache_epoch != epoch:                # un refresh() est passé entre-temps : ne rien écraser
                    return
                _cache[name] = info
            _log_binary(info)

    thread = threading.Thread(target=probe_all, name="azimut-binaries", daemon=True)
    with _cache_lock:
        _refresh_thread = thread
    thread.start()
    return located
