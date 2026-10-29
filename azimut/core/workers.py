"""Classe de base des tâches longues (QThread) : annulation, progression, processus externes."""
from __future__ import annotations

import logging
import os
import queue
import subprocess
import sys
import threading
import time
import traceback
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence, TypeVar

from PySide6.QtCore import QObject, QThread, Signal

from azimut.core.logs import get_logger

T = TypeVar("T")
R = TypeVar("R")

LEVELS: tuple[str, ...] = ("INFO", "OK", "WARN", "ERR")

_PY_LEVELS = {"INFO": logging.INFO, "OK": logging.INFO, "WARN": logging.WARNING, "ERR": logging.ERROR}
_PROGRESS_INTERVAL = 0.1        # ~10 émissions par seconde
_STDERR_MAX = 64 * 1024         # octets conservés de stderr
_KILL_DELAY = 2.0               # terminate → kill
_POLL = 0.1
_NOTHING = object()


class CancelledError(Exception):
    """Levée quand l'utilisateur a demandé l'annulation."""


@dataclass
class ProcessResult:
    returncode: int
    stdout: str
    stderr: str                 # 64 Ko finaux au plus
    timed_out: bool = False
    cancelled: bool = False
    stalled: bool = False       # arrêté faute de signe de vie (voir ``stall_timeout``) ; ``timed_out`` est aussi vrai


def _pump_stdout(stream, lines: "queue.Queue[str | None]") -> None:
    """Lit stdout ligne à ligne ; ``None`` marque la fin du flux."""
    try:
        for raw in iter(stream.readline, b""):
            lines.put(raw.decode("utf-8", errors="replace").rstrip("\r\n"))
    except (OSError, ValueError):
        pass
    finally:
        lines.put(None)


def _drain_stderr(stream, buf: bytearray) -> None:
    """Vide stderr en continu (évite l'interblocage) en ne gardant que la fin."""
    try:
        while True:
            chunk = stream.read1(8192)
            if not chunk:
                break
            buf += chunk
            if len(buf) > 2 * _STDERR_MAX:
                del buf[:-_STDERR_MAX]
    except (OSError, ValueError):
        pass


def _kill_alive(procs: list[subprocess.Popen]) -> None:
    for proc in procs:
        if proc.poll() is None:
            try:
                proc.kill()
            except OSError:
                pass


def _stop_and_reap(proc: subprocess.Popen) -> None:
    """terminate, puis kill après ``_KILL_DELAY`` s, puis attend la fin du processus."""
    try:
        proc.terminate()
    except OSError:
        pass
    try:
        proc.wait(timeout=_KILL_DELAY)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except OSError:
            pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


class BaseWorker(QThread):
    """Tâche exécutée dans un thread. Une instance = une exécution (ne pas relancer après annulation)."""

    progress = Signal(int, int, str)     # (fait, total, libellé courant)
    status = Signal(str)                 # texte d'état court
    log = Signal(str, str)               # (niveau parmi LEVELS, texte)
    completed = Signal(object)           # résultat renvoyé par do_work() (succès)
    cancelled = Signal()                 # annulation prise en compte
    failed = Signal(str)                 # exception inattendue (message court + trace dans le journal)
    # QThread.finished est toujours émis en dernier.

    ok_count: int
    error_count: int
    summary: str

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._cancel_flag = threading.Event()
        self._procs: set[subprocess.Popen] = set()
        self._procs_lock = threading.Lock()
        self._progress_lock = threading.Lock()
        self._last_progress = float("-inf")
        self._logger = get_logger(f"worker.{type(self).__name__}")
        self.ok_count = 0
        self.error_count = 0
        self.summary = ""

    # ── À surcharger ─────────────────────────────────────────────────────
    def do_work(self) -> object:
        """Exécuté dans le thread ; renvoie le résultat. À surcharger."""
        raise NotImplementedError("do_work() doit être surchargée.")

    # ── Cycle de vie ─────────────────────────────────────────────────────
    def run(self) -> None:
        try:
            result = self.do_work()
        except CancelledError:
            self.cancelled.emit()
        except Exception as e:
            self.emit_log("ERR", traceback.format_exc())
            message = (str(e) or type(e).__name__)[:300]
            self.failed.emit(message)
        else:
            self.completed.emit(result)

    # ── Annulation ───────────────────────────────────────────────────────
    def cancel(self) -> None:
        """Thread-safe : lève le drapeau et termine les processus enregistrés (kill après 2 s)."""
        self._cancel_flag.set()
        with self._procs_lock:
            procs = list(self._procs)
        self._terminate(procs)

    def is_cancelled(self) -> bool:
        return self._cancel_flag.is_set()

    def check_cancelled(self) -> None:
        if self._cancel_flag.is_set():
            raise CancelledError()

    @staticmethod
    def _terminate(procs: list[subprocess.Popen]) -> None:
        alive: list[subprocess.Popen] = []
        for proc in procs:
            if proc.poll() is None:
                try:
                    proc.terminate()
                    alive.append(proc)
                except OSError:
                    pass
        if alive:
            timer = threading.Timer(_KILL_DELAY, _kill_alive, args=(alive,))
            timer.daemon = True
            timer.start()

    # ── Émission de signaux ──────────────────────────────────────────────
    def emit_progress(self, done: int, total: int, label: str = "") -> None:
        """Limité à ~10 émissions par seconde, sauf quand ``done >= total``."""
        now = time.monotonic()
        with self._progress_lock:
            if done < total and now - self._last_progress < _PROGRESS_INTERVAL:
                return
            self._last_progress = now
        self.progress.emit(int(done), int(total), label)

    def emit_status(self, text: str) -> None:
        self.status.emit(text)

    def emit_log(self, level: str, text: str) -> None:
        """Émet ``log`` ET écrit dans le logger Python."""
        if level not in LEVELS:
            level = "INFO"
        self._logger.log(_PY_LEVELS[level], "%s", text)
        self.log.emit(level, text)

    # ── Processus externes ───────────────────────────────────────────────
    def run_process(self, cmd: list[str], *, on_stdout_line: Callable[[str], None] | None = None,
                    timeout: float | None = None, cwd: Path | None = None,
                    env: dict[str, str] | None = None, nice: int | None = None,
                    stall_timeout: float | None = None,
                    activity: Callable[[], object] | None = None) -> ProcessResult:
        """Lance un processus (jamais via un shell) et attend sa fin.

        ``on_stdout_line`` est appelée dans le thread qui exécute ``run_process`` (donc celui du worker).
        Une exception levée par ce rappel (ex. ``CancelledError``) arrête le processus puis se propage.

        ``nice`` : priorité abaissée du processus (valeur Unix, ex. 10 ; « sous la normale » sous Windows).
        ``stall_timeout`` + ``activity`` : si la valeur renvoyée par ``activity()`` (sondée toutes les 0,1 s)
        ne change plus pendant ``stall_timeout`` secondes, le processus est arrêté (``stalled``).
        """
        if self.is_cancelled():
            return ProcessResult(-1, "", "", cancelled=True)
        kwargs: dict = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            if nice:
                kwargs["creationflags"] |= subprocess.BELOW_NORMAL_PRIORITY_CLASS
        args = [str(c) for c in cmd]
        try:
            proc = subprocess.Popen(
                args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                cwd=str(cwd) if cwd is not None else None, env=env, **kwargs)
        except OSError as e:
            message = f"Impossible de lancer « {args[0] if args else '?'} » : {e}"
            self.emit_log("ERR", message)
            return ProcessResult(-1, "", message)

        with self._procs_lock:
            self._procs.add(proc)
        if nice and sys.platform != "win32":
            try:
                os.setpriority(os.PRIO_PROCESS, proc.pid, int(nice))
            except (OSError, AttributeError):
                pass
        threads: list[threading.Thread] = []
        try:
            if self.is_cancelled():                      # cancel() passé entre le contrôle et l'enregistrement
                self._terminate([proc])
            lines: queue.Queue = queue.Queue()
            err_buf = bytearray()
            t_out = threading.Thread(target=_pump_stdout, args=(proc.stdout, lines), daemon=True)
            t_err = threading.Thread(target=_drain_stderr, args=(proc.stderr, err_buf), daemon=True)
            threads = [t_out, t_err]
            t_out.start()
            t_err.start()

            out_lines: list[str] = []

            def handle(line: str) -> None:
                out_lines.append(line)
                if on_stdout_line is not None:
                    on_stdout_line(line)

            deadline = None if timeout is None else time.monotonic() + timeout
            timed_out = False
            stalled = False
            stdout_done = False
            last_key: object = _NOTHING
            last_change = time.monotonic()
            while True:
                try:
                    item = lines.get(timeout=_POLL)
                except queue.Empty:
                    item = _NOTHING
                if item is None:
                    stdout_done = True
                elif item is not _NOTHING:
                    handle(item)
                if item is _NOTHING and proc.poll() is not None:
                    t_out.join(1.0)                       # processus fini : dernières lignes
                    while True:
                        try:
                            rest = lines.get_nowait()
                        except queue.Empty:
                            break
                        if rest is not None:
                            handle(rest)
                    break
                if stdout_done and proc.poll() is not None:
                    break
                if deadline is not None and time.monotonic() >= deadline:
                    timed_out = True
                    break
                if stall_timeout is not None and activity is not None:
                    now = time.monotonic()
                    try:
                        key = activity()
                    except Exception:  # noqa: BLE001 - une sonde défaillante ne doit pas tuer le traitement
                        key = last_key
                    if key != last_key:
                        last_key, last_change = key, now
                    elif now - last_change >= stall_timeout:
                        timed_out = stalled = True
                        break
            if timed_out:
                _stop_and_reap(proc)
            t_err.join(2.0)
            t_out.join(1.0)
            returncode = proc.poll()
            if returncode is None:
                returncode = -9
            stderr = bytes(err_buf[-_STDERR_MAX:]).decode("utf-8", errors="replace")
            return ProcessResult(
                returncode=returncode,
                stdout="".join(line + "\n" for line in out_lines),
                stderr=stderr,
                timed_out=timed_out,
                cancelled=self.is_cancelled() and not timed_out and returncode != 0,
                stalled=stalled)
        finally:
            if proc.poll() is None:                      # sortie anormale (exception du rappel…)
                _stop_and_reap(proc)
            for stream in (proc.stdout, proc.stderr):
                try:
                    if stream is not None:
                        stream.close()
                except (OSError, ValueError):
                    pass
            with self._procs_lock:
                self._procs.discard(proc)

    # ── Mémoire ──────────────────────────────────────────────────────────
    def wait_for_memory(self, threshold_pct: float = 75.0, reserve_pct: float = 20.0) -> None:
        """Attend que la pression mémoire redevienne raisonnable (annulable). Sans mesure possible : ne bloque pas."""
        from azimut.core.sysinfo import memory_ok, memory_status
        warned = False
        while True:
            self.check_cancelled()
            status = memory_status()
            if memory_ok(status, threshold_pct, reserve_pct):
                return
            if not warned and status is not None:
                warned = True
                self.emit_log("WARN", f"Pression mémoire : RAM utilisée {status.used_pct:.0f} %, disponible "
                                      f"{status.available_pct:.0f} % — attente avant le prochain fichier…")
            self.emit_status("En attente de mémoire disponible…")
            for _ in range(20):                           # ~2 s, interrompu dès l'annulation
                self.check_cancelled()
                time.sleep(_POLL)

    # ── Parallélisme ─────────────────────────────────────────────────────
    def map_parallel(self, fn: Callable[[T], R], items: Sequence[T], *, workers: int = 4,
                     label: str = "") -> list[R | None]:
        """Applique ``fn`` à chaque élément sur ``workers`` threads ; résultats dans l'ordre de ``items``.

        Une exception dans ``fn`` donne ``None`` + un WARN. L'annulation lève ``CancelledError`` :
        les tâches non démarrées sont abandonnées, celles en cours se terminent.
        """
        total = len(items)
        results: list = [None] * total
        if total == 0:
            return results
        self.check_cancelled()
        self.emit_progress(0, total, label)
        executor = ThreadPoolExecutor(max_workers=max(1, min(int(workers), total)))
        done_count = 0
        try:
            futures = {executor.submit(fn, item): i for i, item in enumerate(items)}
            pending = set(futures)
            while pending:
                self.check_cancelled()
                finished, pending = wait(pending, timeout=_POLL, return_when=FIRST_COMPLETED)
                for fut in finished:
                    i = futures[fut]
                    try:
                        results[i] = fut.result()
                    except CancelledError:
                        raise
                    except Exception as e:
                        prefix = f"{label} : " if label else ""
                        self.emit_log("WARN", f"{prefix}élément {i + 1}/{total} en échec "
                                              f"({type(e).__name__} : {e})")
                    done_count += 1
                    self.emit_progress(done_count, total, label)
        finally:
            executor.shutdown(wait=True, cancel_futures=True)
        return results
