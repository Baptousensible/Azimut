"""Workers d'Azimut Convert : analyse des entrées, détection des encodeurs, conversion."""
from __future__ import annotations

import os
import shutil
import threading
import time
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from azimut.core.fsutil import atomic_write_text, format_duration, format_size, unique_path
from azimut.core.workers import BaseWorker, CancelledError
from azimut.modules.convert import advanced, engine, imaging, probe as probing
from azimut.modules.convert.engine import (
    STATE_CANCELLED, STATE_COPIED, STATE_DONE, STATE_ERROR, STATE_KEPT, STATE_NO_ENCODER, STATE_PENDING,
    STATE_RUNNING, STATE_SKIPPED, STATE_UNSUPPORTED, Job, SourceItem,
)
from azimut.modules.convert.profiles import Profile
from azimut.modules.tags import engine as tags
from azimut.modules.takeout import engine as tk

NICE_VALUE = 10                                    # priorité abaissée des ffmpeg lancés
IMAGE_PRESET_FALLBACKS = (5, 6, 8)                 # SVT-AV1 refuse les grandes images aux presets lents
RESOLUTION_ERRORS = ("resolution", "8k", "could not open encoder")
STALL_FLOOR_MIN = 5                                # minutes sans signe de vie avant d'arrêter un encodage
SLOW_PRESET_FLOOR = {0: 30, 1: 20, 2: 15}          # presets SVT-AV1 lents : blocage toléré plus longtemps (min)


@dataclass(frozen=True)
class ConvertOptions:
    """Réglages hors profil : nommage, Takeout, ressources."""
    name_ext: bool = False             # « photo_jpg.avif » plutôt que « photo.avif »
    takeout: bool = False              # réinjecter date et GPS depuis les JSON Google Takeout trouvés
    tz: str = tk.TZ_UTC           # fuseau des dates EXIF des images écrites
    low_priority: bool = True          # ffmpeg en priorité basse
    memory_guard: bool = True          # attendre si la mémoire est saturée avant le fichier suivant


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
                 parent: QObject | None = None, *, parallel: int = 1,
                 options: ConvertOptions | None = None, exiftool: Path | None = None) -> None:
        super().__init__(parent)
        self._items = list(items)
        self._parallel = advanced.clamp_parallel(parallel)
        self._profiles = self._share_cores(dict(profiles), self._parallel)
        self._options = options or ConvertOptions()
        self._exiftool = exiftool
        self._lock = threading.Lock()
        self._out_dir = out_dir
        self._ffmpeg = ffmpeg
        self._ffprobe = ffprobe
        self._encoders = encoders
        self._filters: frozenset[str] | None = None
        self._filters_loaded = False
        self._lp_ok = True
        self._indexes: dict[Path, tk.DirIndex] = {}
        self._problems: list[tuple[str, str]] = []
        self._warned_exiftool = False
        self._bytes_in = 0
        self._bytes_out = 0
        self._meta_ok = 0
        self._no_json = 0
        self.ok_count = 0
        self.error_count = 0
        self.summary = ""

    # ── Utilitaires ──
    @staticmethod
    def _share_cores(profiles: dict[str, Profile], parallel: int) -> dict[str, Profile]:
        """Threads « Auto » : vidéo bornée (RAM) ; en parallèle, les cœurs sont partagés entre les encodages."""
        out: dict[str, Profile] = {}
        for k, p in profiles.items():
            if p.kind in ("image", "video") and p.threads == 0 and not p.video_copy:
                if parallel > 1:
                    p = replace(p, threads=advanced.auto_threads(parallel))
                elif p.kind == "video":
                    p = replace(p, threads=advanced.default_video_threads())
            out[k] = p
        return out

    def _set_state(self, job: Job, state: str, progress: int | None = None) -> None:
        job.state = state
        self.job_updated.emit(job.index, {"state": state, "progress": progress})

    @staticmethod
    def _discard(path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    def _nice(self) -> int | None:
        return NICE_VALUE if self._options.low_priority else None

    def _add_bytes(self, size_in: int, size_out: int) -> None:
        with self._lock:
            self._bytes_in += size_in
            self._bytes_out += size_out

    def _problem(self, kind: str, item: SourceItem) -> None:
        with self._lock:
            self._problems.append((kind, item.rel.as_posix()))

    def _media_probe(self, source: Path) -> probing.MediaProbe | None:
        res = self.run_process(probing.build_media_probe_command(self._ffprobe, source), timeout=60)
        return probing.parse_media_probe(res.stdout) if res.returncode == 0 else None

    def _available_filters(self) -> frozenset[str] | None:
        with self._lock:
            loaded = self._filters_loaded
        if not loaded:
            res = self.run_process([str(self._ffmpeg), "-hide_banner", "-filters"], timeout=30)
            names = probing.parse_filters(res.stdout) if res.returncode == 0 else None
            with self._lock:
                self._filters, self._filters_loaded = names or None, True
        return self._filters

    # ── Traitement ──
    def do_work(self) -> object:
        if self._encoders is None:
            self._encoders = detect_encoders(self, self._ffmpeg)
        self.check_cancelled()
        jobs = engine.plan_jobs(self._items, self._profiles, self._out_dir, self._encoders,
                                name_ext=self._options.name_ext)
        todo: list[Job] = []
        warned: set[str] = set()
        stats = {"skipped": 0, "unsupported": 0, "kept": 0, "copied": 0}
        for job in jobs:
            if job.state == STATE_PENDING:
                todo.append(job)
                continue
            self._set_state(job, job.state)
            if job.state == STATE_SKIPPED:
                stats["skipped"] += 1
            elif job.state == STATE_UNSUPPORTED:
                stats["unsupported"] += 1
                self.emit_log("WARN", f"{job.item.path.name} : {job.message or 'format non pris en charge'}, ignoré.")
            elif job.state == STATE_NO_ENCODER:
                self.error_count += 1
                if job.profile.id not in warned:
                    warned.add(job.profile.id)
                    self.emit_log("ERR", job.message)
        total = len(todo)
        self.emit_log("INFO", f"{total} fichier(s) à convertir, {stats['skipped']} déjà converti(s).")
        if self._options.takeout and self._exiftool is None:
            self.emit_log("WARN", "ExifTool introuvable : les dates et positions Takeout ne seront pas écrites "
                                  "dans les fichiers (seule la date du fichier est réalignée).")
        self.emit_progress(0, total, "")
        stats_lock = threading.Lock()
        counter = {"done": 0}

        def process(job: Job) -> None:
            self.check_cancelled()
            if self._options.memory_guard:
                self.wait_for_memory()
            name = job.item.path.name
            if self._parallel <= 1:
                self.emit_progress(counter["done"], total, name)
                self.emit_status(f"{counter['done'] + 1} / {total} — {name}")
            self._set_state(job, STATE_RUNNING, 0)
            try:
                outcome = self._convert_one(job)
            except CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — un fichier en échec ne doit pas arrêter le lot
                self.emit_log("ERR", f"{name} : {exc}")
                self._set_state(job, STATE_ERROR)
                outcome = "error"
            with stats_lock:
                if outcome == "ok":
                    self.ok_count += 1
                elif outcome == "kept":
                    self.ok_count += 1
                    stats["kept"] += 1
                elif outcome == "copied":
                    self.ok_count += 1
                    stats["copied"] += 1
                elif outcome == "unsupported":
                    stats["unsupported"] += 1
                else:
                    self.error_count += 1
                    self._problem("ERREUR", job.item)
                counter["done"] += 1
                done = counter["done"]
            self.emit_progress(done, total, name)
            if self._parallel > 1:
                self.emit_status(f"{done} / {total} terminé(s)")

        try:
            if self._parallel <= 1:
                for job in todo:
                    process(job)
            else:
                self.emit_log("INFO", f"Conversion en parallèle : {self._parallel} fichier(s) à la fois.")
                self.map_parallel(process, todo, workers=self._parallel)
        finally:
            self._write_problems(jobs)
        missing = self._verify(jobs)
        gain = self._bytes_in - self._bytes_out
        self.summary = (f"{self.ok_count} converti(s), {stats['copied']} copié(s), {stats['kept']} source(s) "
                        f"conservée(s), {stats['skipped']} déjà converti(s), {stats['unsupported']} non pris en "
                        f"charge, {self.error_count} erreur(s) ; gain : {format_size(max(gain, 0))}"
                        + (f" ; métadonnées Takeout : {self._meta_ok}, sans JSON : {self._no_json}"
                           if self._options.takeout else ""))
        self.emit_log("OK" if not self.error_count and not missing else "WARN", self.summary)
        stats["missing"] = missing
        return stats

    # ── Contrôle final et liste des problèmes ──
    def _verify(self, jobs: list[Job]) -> int:
        """Chaque fichier traité doit avoir son équivalent dans la sortie."""
        checked = [j for j in jobs if j.state in (STATE_DONE, STATE_KEPT, STATE_COPIED, STATE_SKIPPED)]
        if self.is_cancelled() or not checked:
            return 0
        absent = [j for j in checked
                  if not any(p is not None and p.exists() for p in (j.final, j.dest, j.copy_dest))]
        if not absent:
            self.emit_log("OK", f"Contrôle final : {len(checked)} / {len(checked)} fichiers présents dans la sortie.")
            return 0
        self.emit_log("ERR", f"Contrôle final : {len(absent)} fichier(s) sur {len(checked)} absents de la sortie — "
                             "ne supprimez pas vos originaux.")
        for j in absent[:20]:
            self.emit_log("WARN", f"   absent : {j.item.rel.as_posix()}")
        for j in absent:
            self._problem("ABSENT", j.item)
        return len(absent)

    def _write_problems(self, jobs: list[Job]) -> None:
        if not self._problems:
            return
        first = next((j for j in jobs if j.dest is not None), None)
        if first is None:
            return
        base = self._out_dir or (engine.default_output_dir(first.item.root) if first.item.root else None)
        if base is None:
            return
        path = base / f"azimut_convert_{datetime.now():%Y-%m-%d_%H-%M-%S}_problemes.txt"
        text = "TYPE\tFICHIER (relatif au dossier source)\n" + "".join(f"{k}\t{rel}\n" for k, rel in self._problems)
        try:
            base.mkdir(parents=True, exist_ok=True)
            atomic_write_text(path, text)
            self.emit_log("WARN", f"Fichiers à vérifier : {path}")
        except OSError as exc:
            self.emit_log("WARN", f"Liste des problèmes non enregistrée ({exc}).")

    # ── Un fichier ──
    def _convert_one(self, job: Job) -> str:
        """Convertit un fichier. Renvoie « ok », « kept », « copied », « unsupported » ou « error »."""
        src, dest, profile = job.item.path, job.dest, job.profile
        if dest is None:
            self.emit_log("ERR", f"{src.name} : dossier de sortie non défini.")
            self._set_state(job, STATE_ERROR)
            return "error"
        dest.parent.mkdir(parents=True, exist_ok=True)
        sidecar = self._sidecar_for(job.item) if self._options.takeout else None
        if job.passthrough:
            outcome = self._copy(job, dest, f"déjà au format {profile.out_ext}")
        elif profile.kind == "image":
            outcome = self._convert_image(job, sidecar)
        else:
            outcome = self._convert_stream(job, sidecar)
        if outcome in ("ok", "kept", "copied") and job.final is not None and self._options.takeout:
            self._inject(job, sidecar)
        return outcome

    def _copy(self, job: Job, target: Path, why: str) -> str:
        """Copie la source telle quelle (déjà au bon format, multi-images…)."""
        src = job.item.path
        part = engine.part_path(target)
        try:
            shutil.copy2(src, part)
            final = unique_path(target) if target.exists() else target
            os.replace(part, final)
        except OSError as exc:
            self._discard(part)
            self.emit_log("ERR", f"{src.name} : copie impossible ({exc}).")
            self._set_state(job, STATE_ERROR)
            return "error"
        job.final = final
        self._add_bytes(job.item.size, job.item.size)
        self._set_state(job, STATE_COPIED, 100)
        self.emit_log("INFO", f"{src.name} copié ({why}).")
        return "copied"

    # ── Images ──
    def _convert_image(self, job: Job, sidecar: tk.Sidecar | None) -> str:
        src, dest, profile = job.item.path, job.dest, job.profile
        assert dest is not None
        if imaging.is_multiframe(src):                         # GIF animé / TIFF multi-pages : rien ne doit se perdre
            return self._copy(job, job.copy_dest or dest.with_name(src.name), "images multiples")
        tmp = engine.part_path(dest)
        prepared: Path | None = None
        try:
            inp = src
            if imaging.needs_pillow(src):
                try:
                    prepared = imaging.prepare_png(src)
                    inp = prepared
                except RuntimeError as exc:
                    self.emit_log("WARN", f"{src.name} : {exc}.")
                    self._set_state(job, STATE_UNSUPPORTED)
                    return "unsupported"
                except Exception as exc:  # noqa: BLE001 - image corrompue ou trop grande pour Pillow
                    self.emit_log("ERR", f"{src.name} : développement de l'image impossible ({exc}).")
                    self._set_state(job, STATE_ERROR)
                    return "error"
            presets = [profile.preset]
            if profile.preset.isdigit() and "libsvtav1" in (self._encoders or ("libsvtav1",)):
                presets += [str(p) for p in IMAGE_PRESET_FALLBACKS if p > int(profile.preset)]
            res = None
            for preset in presets:
                self.check_cancelled()
                try:
                    cmd = engine.build_command(replace(profile, preset=preset), inp, tmp, ffmpeg=self._ffmpeg,
                                               encoders=self._encoders, lp_ok=self._lp_ok)
                except engine.EncoderMissingError:
                    self.emit_log("ERR", engine.missing_encoder_message(profile, self._encoders or ()) or
                                  f"Encodeur manquant pour « {profile.label} ».")
                    self._set_state(job, STATE_NO_ENCODER)
                    return "error"
                t0 = time.monotonic()
                res = self.run_process(cmd, timeout=600.0, nice=self._nice())
                if res.cancelled or self.is_cancelled():
                    self._discard(tmp)
                    self._set_state(job, STATE_CANCELLED)
                    raise CancelledError()
                if res.returncode == 0 and not res.timed_out:
                    return self._finish(job, tmp, t0)
                self._discard(tmp)
                if not any(k in res.stderr.lower() for k in RESOLUTION_ERRORS):
                    break
            return self._fail(job, res)
        finally:
            if prepared is not None:
                self._discard(prepared)
            self._discard(tmp)

    # ── Vidéo et audio ──
    def _stall_seconds(self, job: Job) -> float:
        size_mb = job.item.size / (1024 * 1024)
        proc_timeout = max(300, int(size_mb * 2))
        slow = SLOW_PRESET_FLOOR.get(int(job.profile.preset), 0) if job.profile.preset.isdigit() else 0
        return float(max(STALL_FLOOR_MIN, slow, (proc_timeout + 59) // 60) * 60)

    def _convert_stream(self, job: Job, sidecar: tk.Sidecar | None) -> str:
        src, dest, profile = job.item.path, job.dest, job.profile
        assert dest is not None
        tmp = engine.part_path(dest)
        is_video = profile.kind == "video"
        info = self._media_probe(src) if is_video else None
        duration = info.duration if info else (self._probe_duration(src) if is_video else None)
        hdr_filter = None
        first_audio_only = False
        if info is not None:
            if info.hdr_trc:
                mode = "HDR natif" if profile.hdr_mode == "H" and advanced.family(profile) == "av1" \
                    else "tone-mapping SDR"
                self.emit_log("INFO", f"{src.name} : HDR détecté ({info.hdr_trc}) → {mode}.")
                hdr_filter = probing.hdr_to_sdr_filter(info.hdr_trc, self._available_filters())
            if info.unusable_audio:
                self.emit_log("WARN", f"{src.name} : {info.unusable_audio} piste(s) audio non décodable(s) ignorée(s).")
        size_mb = job.item.size / (1024 * 1024)
        creation = sidecar.taken.strftime("%Y-%m-%dT%H:%M:%SZ") if sidecar and sidecar.taken else None
        attempts = 3 if is_video else 1
        res = None
        last_pct = {"v": -1, "key": None}

        def on_line(line: str) -> None:
            if line.startswith(("out_time_us=", "out_time_ms=")):
                last_pct["key"] = line
            pct = engine.parse_progress_line(line, duration)
            if pct is not None and pct != last_pct["v"]:
                last_pct["v"] = pct
                self.job_updated.emit(job.index, {"progress": pct})

        def activity() -> object:
            try:
                size = tmp.stat().st_size
            except OSError:
                size = -1
            return (last_pct["key"], size)

        try:
            t0 = time.monotonic()
            for attempt in range(attempts):
                self.check_cancelled()
                try:
                    cmd = engine.build_command(profile, src, tmp, ffmpeg=self._ffmpeg, encoders=self._encoders,
                                               probe=info, attempt=attempt, hdr_filter=hdr_filter,
                                               creation_time=creation, lp_ok=self._lp_ok,
                                               first_audio_only=first_audio_only)
                except engine.EncoderMissingError:
                    self.emit_log("ERR", engine.missing_encoder_message(profile, self._encoders or ()) or
                                  f"Encodeur manquant pour « {profile.label} ».")
                    self._set_state(job, STATE_NO_ENCODER)
                    return "error"
                if is_video:
                    res = self.run_process(cmd, on_stdout_line=on_line, nice=self._nice(),
                                           stall_timeout=self._stall_seconds(job), activity=activity)
                else:
                    res = self.run_process(cmd, on_stdout_line=on_line, nice=self._nice(),
                                           timeout=max(1800.0, size_mb * 10))
                if res.cancelled or self.is_cancelled():
                    self._discard(tmp)
                    self._set_state(job, STATE_CANCELLED)
                    raise CancelledError()
                if res.stalled:
                    self._discard(tmp)
                    self.emit_log("ERR", f"{src.name} : encodage bloqué (aucun signe de vie) et arrêté. "
                                         "Essayez un preset plus rapide (5-6) ou fermez d'autres applications.")
                    self._set_state(job, STATE_ERROR)
                    return "error"
                if res.timed_out:
                    self._discard(tmp)
                    self.emit_log("ERR", f"{src.name} : délai dépassé.")
                    self._set_state(job, STATE_ERROR)
                    return "error"
                if res.returncode == 0:
                    return self._finish(job, tmp, t0, attempt + 1 if attempts > 1 else None)
                self._discard(tmp)
                if res.returncode == -9:
                    self.emit_log("ERR", f"{src.name} : ffmpeg tué (manque de mémoire ?). Essayez un preset plus "
                                         "rapide, moins de fichiers en parallèle, ou fermez d'autres applications.")
                    self._set_state(job, STATE_ERROR)
                    return "error"
                low = res.stderr.lower()
                if engine.classify_failure(res.stderr, src.suffix) in (STATE_NO_ENCODER, STATE_UNSUPPORTED):
                    break
                if attempt + 1 < attempts:
                    self.emit_log("WARN", f"{src.name} : tentative {attempt + 1}/{attempts} échouée — "
                                          f"{engine.stderr_tail(res.stderr, 200)}")
                if self._lp_ok and "svt" in low and "param" in low:
                    self._lp_ok = False
                    self.emit_log("WARN", "Paramètre SVT-AV1 « lp » refusé par ce ffmpeg : désactivé.")
                if info is not None and len(info.audio) > 1 and (
                        "no decoder found" in low or "could not find codec parameters" in low):
                    first_audio_only = True                    # filet de sécurité : première piste audio seule
            return self._fail(job, res)
        finally:
            self._discard(tmp)

    def _probe_duration(self, source: Path) -> float | None:
        res = self.run_process(engine.build_probe_command(self._ffprobe, source), timeout=30)
        return engine.parse_duration(res.stdout) if res.returncode == 0 else None

    # ── Fin d'un encodage ──
    def _fail(self, job: Job, res) -> str:
        src, profile = job.item.path, job.profile
        stderr = res.stderr if res is not None else ""
        state = engine.classify_failure(stderr, src.suffix)
        self._set_state(job, state)
        if state == STATE_UNSUPPORTED:
            self.emit_log("WARN", f"{src.name} : format non pris en charge par ce FFmpeg.")
            return "unsupported"
        if state == STATE_NO_ENCODER:
            self.emit_log("ERR", f"{src.name} : encodeur manquant dans ce FFmpeg. {engine.stderr_tail(stderr)}")
        else:
            self.emit_log("ERR", f"{src.name} : échec de FFmpeg ({profile.label}). {engine.stderr_tail(stderr)}")
        return "error"

    def _finish(self, job: Job, tmp: Path, t0: float, attempt: int | None = None) -> str:
        src, dest, profile = job.item.path, job.dest, job.profile
        assert dest is not None
        size_in = job.item.size
        size_out = tmp.stat().st_size if tmp.exists() else 0
        if size_out <= 0:
            self.emit_log("ERR", f"{src.name} : fichier de sortie vide.")
            self._set_state(job, STATE_ERROR)
            return "error"
        if profile.guard_growth and engine.grew(size_in, size_out):
            self._discard(tmp)
            self._add_bytes(size_in, size_in)
            self.emit_log("WARN", f"{src.name} : le résultat ({format_size(size_out)}) est plus gros que la "
                                  f"source ({format_size(size_in)}) → source conservée.")
            return self._keep_source(job)
        final = unique_path(dest) if dest.exists() else dest      # apparu entre-temps : jamais d'écrasement
        os.replace(tmp, final)
        job.final = final
        self._add_bytes(size_in, size_out)
        self._set_state(job, STATE_DONE, 100)
        extra = f", tentative {attempt}" if attempt and attempt > 1 else ""
        self.emit_log("OK", f"{src.name} → {final.name}  ({format_size(size_in)} → {format_size(size_out)}, "
                            f"{format_duration(time.monotonic() - t0)}{extra})")
        return "ok"

    def _keep_source(self, job: Job) -> str:
        """Anti-grossissement : copie la source à la place du résultat."""
        src = job.item.path
        target = job.copy_dest or job.dest.with_name(src.name)       # type: ignore[union-attr]
        if engine.path_key(target) == engine.path_key(src):
            job.final = src
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
        job.final = target
        self._set_state(job, STATE_KEPT, 100)
        return "kept"

    # ── Takeout : dates et positions ──
    def _sidecar_for(self, item: SourceItem) -> tk.Sidecar | None:
        folder = item.path.parent
        with self._lock:
            index = self._indexes.get(folder)
            if index is None:
                index = self._indexes[folder] = tk.DirIndex(folder)
        js = index.find(item.path)
        if js is None:
            with self._lock:
                self._no_json += 1
            self._problem("SANS_JSON", item)
            return None
        sc = tk.load_sidecar(js)
        if sc is None:
            self.emit_log("WARN", f"{item.path.name} : JSON illisible ({js.name}).")
            self._problem("JSON_ILLISIBLE", item)
        elif sc.taken is None:
            self._problem("SANS_DATE", item)
        return sc

    def _inject(self, job: Job, sidecar: tk.Sidecar | None) -> None:
        """Écrit date et GPS dans le fichier produit (ExifTool), puis aligne sa date de modification."""
        final = job.final
        if sidecar is None or final is None:
            return
        opts = tk.Options(overwrite=True, tz=self._options.tz)
        try:
            plan = tk.build_plan(final, sidecar, [], opts)
            args = tags.change_args(tags.resolve_changes(final, plan.changes))
        except ValueError as exc:
            self.emit_log("WARN", f"{final.name} : métadonnées Takeout ignorées ({exc}).")
            return
        if args and self._exiftool is not None:
            argfile = tags.write_argfile([*args, str(final)])
            try:
                res = self.run_process(tags.write_command(self._exiftool, argfile), timeout=120.0)
            finally:
                self._discard(argfile)
            ok, msg = tags.parse_write_result(res.returncode, res.stdout, res.stderr)
            if ok:
                with self._lock:
                    self._meta_ok += 1
            else:
                self.emit_log("WARN", f"{final.name} : ExifTool a échoué ({msg}).")
        if plan.mtime is not None:
            try:
                ts = plan.mtime.timestamp()
                os.utime(final, (ts, ts))
            except (OSError, OverflowError, ValueError):
                pass
