"""Inhibition de la veille automatique pendant les traitements longs.

La veille système suspend le processus d'Azimut et ses sous-processus (FFmpeg,
ExifTool…). Ce module maintient une assertion de veille tant qu'au moins un worker
tourne, puis la libère. Une mise en veille demandée explicitement par l'utilisateur
(fermeture du capot, menu système) reste possible : seules les mises en veille
automatiques sont empêchées.

Chaque plateforme a son mécanisme :

- macOS : ``caffeinate -i -w <pid>`` ;
- Linux : ``systemd-inhibit --what=sleep …`` ;
- Windows : ``SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)``.

En cas d'indisponibilité, le garde-fou est simplement inactif : il ne doit jamais
empêcher un traitement de se dérouler.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
from contextlib import contextmanager
from typing import Callable, Protocol

from azimut.core.logs import get_logger

_log = get_logger("power")

_PROCESS_STOP_TIMEOUT = 2.0
_WINDOWS_ES_CONTINUOUS = 0x80000000
_WINDOWS_ES_SYSTEM_REQUIRED = 0x00000001


class _Backend(Protocol):
    def stop(self) -> None:
        """Libère l'assertion de veille."""


class _ProcessBackend:
    """Assertion maintenue par un petit processus utilitaire (macOS, Linux)."""

    def __init__(self, command: list[str]) -> None:
        self._proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

    def stop(self) -> None:
        proc = self._proc
        if proc.poll() is not None:
            return
        if proc.stdin is not None:                 # Linux : fermer stdin fait sortir « cat »
            try:
                proc.stdin.close()
            except OSError as exc:
                _log.debug("Fermeture de stdin du garde-fou ignorée : %s", exc)
        try:
            proc.terminate()
        except OSError as exc:
            _log.debug("Arrêt du garde-fou impossible : %s", exc)
        try:
            proc.wait(timeout=_PROCESS_STOP_TIMEOUT)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except OSError as exc:
                _log.debug("Kill du garde-fou impossible : %s", exc)
            try:
                proc.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                _log.warning("Le garde-fou de veille ne s'est pas arrêté")


class _WindowsExecutionState:
    """Assertion Windows : l'état d'exécution est attaché au fil qui l'a posé."""

    def __init__(self) -> None:
        import ctypes

        self._ctypes = ctypes
        self._kernel32 = ctypes.windll.kernel32
        self._kernel32.SetThreadExecutionState.argtypes = [ctypes.c_ulong]
        self._kernel32.SetThreadExecutionState.restype = ctypes.c_ulong
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._active = False
        self._thread = threading.Thread(target=self._hold, name="azimut-keep-awake", daemon=True)
        self._thread.start()
        if not self._ready.wait(2.0) or not self._active:
            self._stop.set()
            raise OSError("SetThreadExecutionState a échoué")

    def _hold(self) -> None:
        try:
            self._active = bool(self._kernel32.SetThreadExecutionState(
                _WINDOWS_ES_CONTINUOUS | _WINDOWS_ES_SYSTEM_REQUIRED))
        except Exception:  # noqa: BLE001 - l'API Windows ne doit jamais tuer un traitement
            self._active = False
        finally:
            self._ready.set()
        if not self._active:
            return
        try:
            self._stop.wait()
        finally:
            try:
                self._kernel32.SetThreadExecutionState(_WINDOWS_ES_CONTINUOUS)
            except Exception as exc:  # noqa: BLE001
                _log.debug("Libération de l'assertion Windows impossible : %s", exc)

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(_PROCESS_STOP_TIMEOUT)
        if self._thread.is_alive():
            _log.warning("Le garde-fou de veille Windows ne s'est pas arrêté")


def _start_backend() -> _Backend | None:
    """Crée l'assertion adaptée à la plateforme, ou None si elle est indisponible."""
    if sys.platform == "darwin":
        exe = shutil.which("caffeinate")
        if exe is None:
            return None
        # -i : empêche la veille automatique ; -w : s'arrête avec Azimut (même tué).
        return _ProcessBackend([exe, "-i", "-w", str(os.getpid())])
    if sys.platform.startswith("linux"):
        exe = shutil.which("systemd-inhibit")
        cat = shutil.which("cat")
        if exe is None or cat is None:
            return None
        # « cat » attend stdin : fermer le tuyau libère l'inhibiteur proprement.
        return _ProcessBackend([
            exe, "--what=sleep", "--who=Azimut", "--why=Traitement en cours",
            "--mode=block", cat,
        ])
    if sys.platform == "win32":
        return _WindowsExecutionState()
    return None


class _SleepGuard:
    """Compte les workers actifs et ne maintient qu'une assertion à la fois."""

    def __init__(self, factory: Callable[[], _Backend | None] = _start_backend) -> None:
        self._factory = factory
        self._lock = threading.Lock()
        self._depth = 0
        self._backend: _Backend | None = None

    def acquire(self) -> None:
        with self._lock:
            if self._depth == 0:
                try:
                    self._backend = self._factory()
                except Exception as exc:  # noqa: BLE001 - jamais bloquant
                    _log.debug("Veille automatique non inhibée : %s", exc)
                    self._backend = None
            self._depth += 1

    def release(self) -> None:
        backend: _Backend | None = None
        with self._lock:
            if self._depth <= 0:
                return
            self._depth -= 1
            if self._depth == 0:
                backend, self._backend = self._backend, None
        if backend is not None:
            try:
                backend.stop()
            except Exception as exc:  # noqa: BLE001 - l'arrêt du traitement prime
                _log.debug("Arrêt du garde-fou de veille ignoré : %s", exc)


_guard = _SleepGuard()


@contextmanager
def keep_awake():
    """Empêche la veille automatique pendant le bloc ; réentrant et thread-safe."""
    _guard.acquire()
    try:
        yield
    finally:
        _guard.release()
