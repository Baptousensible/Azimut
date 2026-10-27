"""Workers d'Azimut Convert : analyse des entrées, détection des encodeurs, conversion."""
from __future__ import annotations

import os
import shutil
import time
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from azimut.core.fsutil import format_duration, format_size, unique_path
from azimut.core.workers import BaseWorker, CancelledError
from azimut.modules.convert import engine
from azimut.modules.convert.engine import (
    STATE_CANCELLED, STATE_DONE, STATE_ERROR, STATE_KEPT, STATE_NO_ENCODER, STATE_PENDING,
    STATE_RUNNING, STATE_SKIPPED, STATE_UNSUPPORTED, Job, SourceItem,
)
from azimut.modules.convert.profiles import Profile


def detect_encoders(worker: BaseWorker, ffmpeg: Path) -> frozenset[str] | None:
    """Encodeurs de ce ffmpeg (None si la détection échoue : aucun contrôle préalable)."""
    res = worker.run_process(engine.build_encoders_command(ffmpeg), timeout=30)
    if res.returncode != 0 or not res.stdout:
        worker.emit_log("WARN", "Impossible de lister les encodeurs de FFmpeg ; aucun contrôle préalable.")
        return None
    names = engine.parse_encoders(res.stdout)
    return names or None


class ProbeWorker(BaseWorker):
    """Détecte les encodeurs disponibles (résultat : frozenset[str] ou None)."""

    def __init__(self, ffmpeg: Path, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._ffmpeg = ffmpeg
        self.ok_count = self.error_count = 0
        self.summary = ""

    def do_work(self) -> object:
        return detect_encoders(self, self._ffmpeg)


class ScanWorker(BaseWorker):
    """Parcourt fichiers et dossiers et renvoie la liste de SourceItem."""

    def __init__(self, paths: list[str], recursive: bool, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._paths = list(paths)
        self._recursive = recursive
        self.ok_count = self.error_count = 0
        self.summary = ""

    def do_work(self) -> object:
        self.emit_status("Analyse des fichiers…")
        result = engine.scan_inputs(self._paths, self._recursive, cancel=self.is_cancelled)
        self.check_cancelled()
        for msg in result.ignored[:20]:
            self.emit_log("WARN", f"Ignoré : {msg}")
        if len(result.ignored) > 20:
            self.emit_log("WARN", f"… et {len(result.ignored) - 20} autre(s) ignoré(s).")
        self.ok_count = len(result.items)
        self.emit_log("INFO", f"{len(result.items)} fichier(s) trouvé(s).")
        return result.items


class ConvertWorker(BaseWorker):
    """Convertit la file. `job_updated(indice, {"state"?, "progress"?})` alimente le tableau."""

    job_updated = Signal(int, object)

    def __init__(self, items: list[SourceItem], profiles: dict[str, Profile], out_dir: Path | None,
                 ffmpeg: Path, ffprobe: Path, encoders: frozenset[str] | None = None,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._items = list(items)
        self._profiles = dict(profiles)
        self._out_dir = out_dir
        self._ffmpeg = ffmpeg
        self._ffprobe = ffprobe
        self._encoders = encoders
        self._bytes_in = 0
        self._bytes_out = 0
        self.ok_count = 0
        self.error_count = 0
        self.summary = ""

    # ── Utilitaires ──
    def _set_state(self, job: Job, state: str, progress: int | None = None) -> None:
        job.state = state
        self.job_updated.emit(job.index, {"state": state, "progress": progress})

    @staticmethod
    def _discard(path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    def _probe_duration(self, source: Path) -> float | None:
        res = self.run_process(engine.build_probe_command(self._ffprobe, source), timeout=30)
        return engine.parse_duration(res.stdout) if res.returncode == 0 else None

    # ── Traitement ──
    def do_work(self) -> object:
        if self._encoders is None:
            self._encoders = detect_encoders(self, self._ffmpeg)
        self.check_cancelled()
        jobs = engine.plan_jobs(self._items, self._profiles, self._out_dir, self._encoders)
        todo: list[Job] = []
        warned: set[str] = set()
        stats = {"skipped": 0, "unsupported": 0, "kept": 0}
        for job in jobs:
            if job.state == STATE_PENDING:
                todo.append(job)
                continue
            self._set_state(job, job.state)
            if job.state == STATE_SKIPPED:
                stats["skipped"] += 1
            elif job.state == STATE_UNSUPPORTED:
                stats["unsupported"] += 1
                self.emit_log("WARN", f"{job.item.path.name} : format non pris en charge, ignoré.")
            elif job.state == STATE_NO_ENCODER:
                self.error_count += 1
                if job.profile.id not in warned:
                    warned.add(job.profile.id)
                    self.emit_log("ERR", job.message)
        total = len(todo)
        self.emit_log("INFO", f"{total} fichier(s) à convertir, {stats['skipped']} déjà converti(s).")
        self.emit_progress(0, total, "")
        for n, job in enumerate(todo, 1):
            self.check_cancelled()
            name = job.item.path.name
            self.emit_progress(n - 1, total, name)
            self.emit_status(f"{n} / {total} — {name}")
            self._set_state(job, STATE_RUNNING, 0)
            try:
                outcome = self._convert_one(job)
            except CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — un fichier en échec ne doit pas arrêter le lot
                self.emit_log("ERR", f"{name} : {exc}")
                self._set_state(job, STATE_ERROR)
                outcome = "error"
            if outcome == "ok":
                self.ok_count += 1
            elif outcome == "kept":
                self.ok_count += 1
                stats["kept"] += 1
            elif outcome == "unsupported":
                stats["unsupported"] += 1
            else:
                self.error_count += 1
            self.emit_progress(n, total, name)
        gain = self._bytes_in - self._bytes_out
        self.summary = (f"{self.ok_count} converti(s), {stats['kept']} source(s) conservée(s), "
                        f"{stats['skipped']} déjà converti(s), {stats['unsupported']} non pris en charge, "
                        f"{self.error_count} erreur(s) ; gain : {format_size(max(gain, 0))}")
        self.emit_log("OK" if not self.error_count else "WARN", self.summary)
        return stats

    def _convert_one(self, job: Job) -> str:
        """Convertit un fichier. Renvoie « ok », « kept », « unsupported » ou « error »."""
        src, dest, profile = job.item.path, job.dest, job.profile
        if dest is None:
            self.emit_log("ERR", f"{src.name} : dossier de sortie non défini.")
            self._set_state(job, STATE_ERROR)
            return "error"
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = engine.part_path(dest)
        try:
            duration = self._probe_duration(src) if profile.kind != "image" else None
            try:
                cmd = engine.build_command(profile, src, tmp, ffmpeg=self._ffmpeg, encoders=self._encoders)
            except engine.EncoderMissingError:
                self.emit_log("ERR", engine.missing_encoder_message(profile, self._encoders or ()) or
                              f"Encodeur manquant pour « {profile.label} ».")
                self._set_state(job, STATE_NO_ENCODER)
                return "error"
            last = {"pct": -1}

            def on_line(line: str) -> None:
                pct = engine.parse_progress_line(line, duration)
                if pct is not None and pct != last["pct"]:
                    last["pct"] = pct
                    self.job_updated.emit(job.index, {"progress": pct})

            size_mb = job.item.size / (1024 * 1024)
            timeout = {"image": 600.0, "audio": max(1800.0, size_mb * 10),
                       "video": max(900.0, size_mb * 20)}[profile.kind]
            t0 = time.monotonic()
            res = self.run_process(cmd, on_stdout_line=on_line, timeout=timeout)
            if res.cancelled or self.is_cancelled():
                self._discard(tmp)
                self._set_state(job, STATE_CANCELLED)
                raise CancelledError()
            if res.timed_out:
                self.emit_log("ERR", f"{src.name} : délai dépassé ({format_duration(timeout)}).")
                self._set_state(job, STATE_ERROR)
                return "error"
            if res.returncode != 0:
                state = engine.classify_failure(res.stderr, src.suffix)
                self._set_state(job, state)
                if state == STATE_UNSUPPORTED:
                    self.emit_log("WARN", f"{src.name} : format non pris en charge par ce FFmpeg.")
                    return "unsupported"
                if state == STATE_NO_ENCODER:
                    self.emit_log("ERR", f"{src.name} : encodeur manquant dans ce FFmpeg. "
                                         f"{engine.stderr_tail(res.stderr)}")
                else:
                    self.emit_log("ERR", f"{src.name} : échec de FFmpeg. {engine.stderr_tail(res.stderr)}")
                return "error"
            size_in = job.item.size
            size_out = tmp.stat().st_size if tmp.exists() else 0
            if size_out <= 0:
                self.emit_log("ERR", f"{src.name} : fichier de sortie vide.")
                self._set_state(job, STATE_ERROR)
                return "error"
            if profile.guard_growth and engine.grew(size_in, size_out):
                self._discard(tmp)
                self._bytes_in += size_in
                self._bytes_out += size_in
                self.emit_log("WARN", f"{src.name} : le résultat ({format_size(size_out)}) est plus gros que la "
                                      f"source ({format_size(size_in)}) → source conservée.")
                return self._keep_source(job)
            final = unique_path(dest) if dest.exists() else dest      # apparu entre-temps : jamais d'écrasement
            os.replace(tmp, final)
            self._bytes_in += size_in
            self._bytes_out += size_out
            self._set_state(job, STATE_DONE, 100)
            self.emit_log("OK", f"{src.name} → {final.name}  ({format_size(size_in)} → {format_size(size_out)}, "
                                f"{format_duration(time.monotonic() - t0)})")
            return "ok"
        finally:
            self._discard(tmp)

    def _keep_source(self, job: Job) -> str:
        """Anti-grossissement : copie la source à la place du résultat."""
        src = job.item.path
        target = job.copy_dest or job.dest.with_name(src.name)       # type: ignore[union-attr]
        if engine.path_key(target) == engine.path_key(src):
            self._set_state(job, STATE_KEPT, 100)
            return "kept"
        target = unique_path(target)
        part = engine.part_path(target)
        try:
            shutil.copy2(src, part)
            os.replace(part, target)
        except OSError as exc:
            self._discard(part)
            self.emit_log("ERR", f"{src.name} : copie de la source impossible ({exc}).")
            self._set_state(job, STATE_ERROR)
            return "error"
        self._set_state(job, STATE_KEPT, 100)
        return "kept"
