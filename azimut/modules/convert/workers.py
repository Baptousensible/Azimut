"""Workers d'Azimut Convert : analyse des entrées, détection des encodeurs, conversion."""
from __future__ import annotations

import os
import shutil
from collections.abc import Collection
import threading
import time
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QObject, Signal

from azimut.core.fsutil import atomic_write_text, format_duration, format_size, iter_files, publish_no_replace
from azimut.core.logs import get_logger
from azimut.core.workers import BaseWorker, CancelledError
from azimut.modules.convert import advanced, engine, imaging, probe as probing
from azimut.modules.convert.engine import (
    STATE_CANCELLED, STATE_COPIED, STATE_DONE, STATE_ERROR, STATE_JSON_OMITTED, STATE_KEPT, STATE_NO_ENCODER,
    STATE_PENDING, STATE_RUNNING, STATE_SKIPPED, STATE_UNSUPPORTED, Job, SourceItem,
)

from azimut.modules.convert.profiles import AUDIO_EXTS, OTHER_KIND, VIDEO_EXTS, Profile
from azimut.modules.tags import engine as tags
from azimut.modules.takeout import engine as tk

_log = get_logger("convert.workers")

NICE_VALUE = 10                                    # priorité abaissée des ffmpeg lancés
IMAGE_PRESET_FALLBACKS = (5, 6, 8)                 # SVT-AV1 refuse les grandes images aux presets lents
RESOLUTION_ERRORS = ("resolution", "8k", "could not open encoder")
STALL_FLOOR_MIN = 5                                # minutes sans signe de vie avant d'arrêter un encodage
SLOW_PRESET_FLOOR = {0: 30, 1: 20, 2: 15}          # presets SVT-AV1 lents : blocage toléré plus longtemps (min)
IMAGE_STALL_FLOOR = 180                            # secondes sans signe de vie avant d'arrêter une image
HARDWARE_PROBE_TIMEOUT = 15.0                       # un pilote qui n'ouvre pas un encodeur en 15 s est considéré muet

# Progression globale : une seule barre de 0 à 100 % pour tout le lot. La file est regroupée par étape
# (copies, images, audios, vidéos) et chaque fichier compte pour la même part : la barre est ainsi
# découpée exactement comme le nombre de fichiers de chaque famille, sans dépendre de leur taille.
STEP_ORDER = ("copy", "image", "audio", "video")
# Poids servant uniquement au temps restant : une grosse vidéo dure bien plus qu'une copie ou une photo.
# Il ne pilote jamais la barre, qui reste proportionnelle aux effectifs.
STEP_WEIGHT = {"copy": 0.2, "image": 1.0, "audio": 1.0, "video": 3.0}
STATUS_INTERVAL = 1.0                              # secondes entre deux mises à jour du libellé d'étape


@dataclass(frozen=True)
class ConvertOptions:
    """Réglages hors profil : nommage, Takeout, ressources."""
    name_ext: bool = False             # « photo_jpg.avif » plutôt que « photo.avif »
    takeout: bool = False              # réinjecter date et GPS depuis les JSON Google Takeout trouvés
    skip_takeout_json: bool = False    # ne pas recopier dans la sortie les JSON Takeout dont les données sont réinjectées
    tz: str = tk.TZ_UTC           # fuseau des dates EXIF des images écrites
    low_priority: bool = True          # ffmpeg en priorité basse
    memory_guard: bool = True          # attendre si la mémoire est saturée avant le fichier suivant


def conversion_notification(*, ok: int, errors: int, skipped: int = 0, unsupported: int = 0,
                            missing: int = 0, gain: int = 0) -> str:
    """Résumé d'une ligne pour la notification de fin de conversion (traduit)."""
    parts = [
        QCoreApplication.translate("ConvertNotification", "{count} fichier(s) traité(s)").format(count=ok),
        QCoreApplication.translate("ConvertNotification", "{count} erreur(s)").format(count=errors),
    ]
    ignored = skipped + unsupported
    if ignored:
        parts.append(QCoreApplication.translate("ConvertNotification", "{count} ignoré(s)")
                     .format(count=ignored))
    if missing:
        parts.append(QCoreApplication.translate("ConvertNotification", "{count} sortie(s) manquante(s)")
                     .format(count=missing))
    if gain > 0 and not errors and not missing:
        parts.append(QCoreApplication.translate("ConvertNotification", "gain {size}")
                     .format(size=format_size(gain)))
    return " · ".join(parts)


def detect_encoders(worker: BaseWorker, ffmpeg: Path) -> frozenset[str] | None:
    """Encodeurs de ce ffmpeg (None si la détection échoue : aucun contrôle préalable)."""
    res = worker.run_process(engine.build_encoders_command(ffmpeg), timeout=30)
    if res.returncode != 0 or not res.stdout:
        worker.emit_log("WARN", "Impossible de lister les encodeurs de FFmpeg ; aucun contrôle préalable.")
        return None
    names = engine.parse_encoders(res.stdout)
    return names or None


def probe_hardware(worker: BaseWorker, ffmpeg: Path, encoders: Collection[str]) -> frozenset[str]:
    """Encodeurs matériels listés par FFmpeg dont le pilote ou le GPU ne répond pas réellement.

    Un encodeur présent dans `ffmpeg -encoders` peut échouer au lancement (pilote absent, GPU occupé ou
    trop ancien). On encode ici une unique image de 64×64 à vide : le code retour prouve que le pilote
    s'est ouvert. Les profils concernés sont ensuite marqués indisponibles avant le démarrage.
    """
    failed: set[str] = set()
    for encoder in engine.HARDWARE_PROBE_ENCODERS:
        if encoder not in encoders:
            continue
        worker.check_cancelled()
        res = worker.run_process(engine.build_hardware_probe_command(ffmpeg, encoder),
                                 timeout=HARDWARE_PROBE_TIMEOUT, capture_stdout=False)
        if res.cancelled or worker.is_cancelled():
            raise CancelledError()
        if res.returncode != 0:
            failed.add(encoder)
            detail = engine.stderr_digest(res.stderr, 200) or "échec sans message"
            worker.emit_log("WARN", f"Test matériel : {encoder} ne répond pas — {detail}")
    return frozenset(failed)


@dataclass(frozen=True)
class EncoderProbe:
    """Détection terminée : encodeurs listés, et encodeurs matériels dont le test a échoué."""
    encoders: frozenset[str] | None
    hardware_failed: frozenset[str] = frozenset()


class ProbeWorker(BaseWorker):
    """Détecte les encodeurs et teste les pilotes matériels (résultat : EncoderProbe)."""

    def __init__(self, ffmpeg: Path, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._ffmpeg = ffmpeg
        self.ok_count = self.error_count = 0
        self.summary = ""

    def do_work(self) -> object:
        encoders = detect_encoders(self, self._ffmpeg)
        return EncoderProbe(encoders, probe_hardware(self, self._ffmpeg, encoders or ()))


class ScanWorker(BaseWorker):
    """Parcourt fichiers et dossiers et renvoie la liste de SourceItem."""

    def __init__(self, paths: list[str], recursive: bool, parent: QObject | None = None, *,
                 include_others: bool = False) -> None:
        super().__init__(parent)
        self._paths = list(paths)
        self._recursive = recursive
        self._include_others = include_others
        self.ok_count = self.error_count = 0
        self.summary = ""

    def do_work(self) -> object:
        self.emit_status(self.tr("Analyse des fichiers…"))
        result = engine.scan_inputs(self._paths, self._recursive, cancel=self.is_cancelled,
                                    include_others=self._include_others)
        self.check_cancelled()
        for msg in result.ignored[:20]:
            self.emit_log("WARN", f"Ignoré : {msg}")
        if len(result.ignored) > 20:
            self.emit_log("WARN", f"… et {len(result.ignored) - 20} autre(s) ignoré(s).")
        self.ok_count = len(result.items)
        self.emit_log("INFO", f"{len(result.items)} fichier(s) trouvé(s).")
        return result.items


# Contrôle d'ouverture après un arrêt forcé : FFprobe ne lit pas les images (HEIC, AVIF…), qui sont
# toujours publiées atomiquement ; les vidéos et les audios sont les sorties qu'il sait vraiment vérifier.
VERIFY_PROBE_EXTS = frozenset(VIDEO_EXTS | AUDIO_EXTS)


@dataclass
class VerifyResult:
    """Résultat d'une vérification de sortie : dossiers contrôlés, fichiers lus et fichiers suspects."""
    bases: list[Path]
    checked: int
    damaged: list[engine.DamagedFile]


class VerifyWorker(BaseWorker):
    """Cherche les fichiers incomplets ou corrompus d'un dossier de sortie, sans rien modifier.

    Trois contrôles : temporaires `.part` laissés par une conversion interrompue (ou par une écriture de
    manifeste interrompue), sorties dont la taille ne correspond plus au manifeste de reprise, et vidéos ou
    audios que FFprobe n'arrive pas à ouvrir. Le manifeste n'est écrit qu'en fin de lot : après un plantage,
    les sorties du lot n'y figurent pas, d'où la vérification de tous les médias de la sortie et pas
    seulement des entrées enregistrées.
    """

    def __init__(self, bases: list[Path], ffprobe: Path | None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._bases = [Path(b) for b in bases]
        self._ffprobe = ffprobe

    def do_work(self) -> object:
        existing: list[Path] = []
        for base in self._bases:
            if base.is_dir():
                existing.append(base)
            else:
                self.emit_log("WARN", self.tr("Dossier de sortie introuvable : {path}").format(path=base))
        damaged: list[engine.DamagedFile] = []
        if not existing:
            self.summary = self.tr("Aucun dossier de sortie à vérifier.")
            return VerifyResult(self._bases, 0, damaged)

        self.emit_status(self.tr("Recherche des conversions interrompues…"))
        for base in existing:
            self.check_cancelled()
            for path in engine.find_incomplete_files(base, cancel=self.is_cancelled):
                damaged.append(engine.DamagedFile(path, "partiel"))
            damaged.extend(engine.manifest_size_problems(base))

        media: list[Path] = []
        if self._ffprobe is not None:
            for base in existing:
                media.extend(iter_files(base, extensions=VERIFY_PROBE_EXTS, cancel=self.is_cancelled))
        else:
            self.emit_log("WARN", self.tr("FFprobe introuvable : le contenu des vidéos et des audios n'est "
                                          "pas contrôlé."))

        checked = 0
        unreadable = 0
        total = len(media)
        for n, path in enumerate(media, 1):
            self.check_cancelled()
            self.emit_progress(n, total, path.name)
            res = self.run_process(engine.build_probe_command(self._ffprobe, path), timeout=60)
            if res.cancelled or self.is_cancelled():
                raise CancelledError()
            checked += 1
            if res.returncode != 0:
                unreadable += 1
                damaged.append(engine.DamagedFile(path, "illisible"))

        unique: dict[str, engine.DamagedFile] = {}
        for item in damaged:                                # un fichier tronqué et illisible ne compte qu'une fois
            unique.setdefault(engine.path_key(item.path), item)
        damaged = list(unique.values())
        self.ok_count = checked
        self.error_count = len(damaged)
        self.summary = self.tr("{checked} fichier(s) contrôlé(s), {bad} suspect(s).").format(
            checked=checked, bad=len(damaged))
        self.emit_progress(total, total, "")
        return VerifyResult(existing, checked, damaged)


class ConvertWorker(BaseWorker):
    """Convertit la file. `job_updated(indice, {"state"?, "progress"?})` alimente le tableau."""

    job_updated = Signal(int, object)
    steps_planned = Signal(object)                 # [(clé, libellé, nombre de fichiers), …]

    def __init__(self, items: list[SourceItem], profiles: dict[str, Profile], out_dir: Path | None,
                 ffmpeg: Path, ffprobe: Path, encoders: frozenset[str] | None = None,
                 parent: QObject | None = None, *, parallel: int = 1,
                 parallel_by_kind: dict[str, int] | None = None,
                 options: ConvertOptions | None = None, exiftool: Path | None = None,
                 hardware_failed: Collection[str] | None = None) -> None:
        super().__init__(parent)
        self._items = list(items)
        self._parallel = advanced.clamp_parallel(parallel)          # valeur de repli pour les familles non réglées
        self._parallel_by_kind = {
            kind: advanced.clamp_parallel((parallel_by_kind or {}).get(kind, self._parallel), kind)
            for kind in advanced.PARALLEL_KINDS
        }
        self._profiles = self._share_cores(dict(profiles), self._parallel_by_kind)
        self._options = options or ConvertOptions()
        self._exiftool = exiftool
        self._lock = threading.Lock()
        self._out_dir = Path(out_dir).expanduser().absolute() if out_dir is not None else None
        self._ffmpeg = ffmpeg
        self._ffprobe = ffprobe
        self._encoders = encoders
        self._hardware_failed = frozenset(hardware_failed or ())
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
        self._metadata_copied = 0
        self._metadata_skipped = 0
        self._takeout_unwritable = 0
        self.ok_count = 0
        self.error_count = 0
        self.summary = ""
        self.notification_summary = ""
        self._track_lock = threading.Lock()
        self._fractions: dict[int, float] = {}
        self._done_units = 0.0                    # avancement de la barre : un fichier = une part
        self._total_jobs = 1
        self._weights: dict[int, float] = {}       # coût estimé, pour le temps restant uniquement
        self._weighted = 0.0
        self._total_weight = 0.0
        self._last_pct = 0
        self._started = 0.0
        self._last_status = float("-inf")
        self._step: tuple[str, int, int, int, dict] = ("", 0, 0, 0, {"done": 0})

    # ── Utilitaires ──
    @staticmethod
    def _share_cores(profiles: dict[str, Profile], parallel_by_kind: dict[str, int]) -> dict[str, Profile]:
        """Threads « Auto » : cœurs partagés entre les fichiers simultanés d'une même famille.

        Le partage est fait par famille (les étapes sont traitées l'une après l'autre) ; une vidéo seule garde
        des threads bornés pour limiter sa mémoire, même quand les images ou les copies sont en parallèle.
        """
        out: dict[str, Profile] = {}
        for k, p in profiles.items():
            if p.kind in ("image", "video") and p.threads == 0:
                count = parallel_by_kind.get(p.kind, 1)
                if count > 1:
                    p = replace(p, threads=advanced.auto_threads(count))
                elif p.kind == "video":
                    p = replace(p, threads=advanced.default_video_threads())
            out[k] = p
        return out

    def _set_state(self, job: Job, state: str, progress: int | None = None) -> None:
        job.state = state
        self.job_updated.emit(job.index, {"state": state, "progress": progress})

    def _missing_encoder_message(self, profile: Profile) -> str:
        """Message quand aucun encodeur ne peut être choisi (encodeur absent ou pilote muet)."""
        return (engine.missing_encoder_message(profile, self._encoders or (), self._hardware_failed)
                or f"Encodeur manquant pour « {profile.label} ».")

    @staticmethod
    def _discard(path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            _log.debug("Fichier temporaire non supprimé (%s) : %s", path, exc)

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

    def _webm_has_mono_audio(self, source: Path) -> bool:
        """Vrai si le WebM source contient au moins une piste audio mono."""
        info = self._media_probe(source)
        return info is not None and any(int(s.get("channels") or 0) == 1 for s in info.audio)

    def _available_filters(self) -> frozenset[str] | None:
        with self._lock:
            loaded = self._filters_loaded
        if not loaded:
            res = self.run_process([str(self._ffmpeg), "-hide_banner", "-filters"], timeout=30)
            names = probing.parse_filters(res.stdout) if res.returncode == 0 else None
            with self._lock:
                self._filters, self._filters_loaded = names or None, True
        return self._filters

    # ── Progression globale et étapes ──
    @staticmethod
    def _step_key(job: Job) -> str:
        """Étape d'un fichier : copie, ou famille de son profil d'encodage."""
        if job.passthrough or job.item.kind == OTHER_KIND:
            return "copy"
        return job.profile.kind

    def _plan_steps(self, jobs: list[Job]) -> list[tuple[str, str, list[Job]]]:
        """Répartit la file en étapes (clé, libellé, fichiers) et initialise les deux progressions.

        La barre compte les fichiers à parts égales : ses segments suivent exactement les effectifs.
        Le temps restant utilise en plus un coût pondéré par la taille et la famille (STEP_WEIGHT),
        pour qu'une longue vidéo ne soit pas estimée comme une copie instantanée.
        """
        labels = {"copy": self.tr("Copies"), "image": self.tr("Images"),
                  "audio": self.tr("Audios"), "video": self.tr("Vidéos")}
        grouped: dict[str, list[Job]] = {key: [] for key in STEP_ORDER}
        for job in jobs:
            grouped[self._step_key(job)].append(job)
        with self._track_lock:
            self._fractions = {job.index: 0.0 for job in jobs}
            self._done_units = 0.0
            self._total_jobs = len(jobs) or 1
            self._weights = {job.index: max(1.0, float(job.item.size)) * STEP_WEIGHT[self._step_key(job)]
                             for job in jobs}
            self._weighted = 0.0
            self._total_weight = sum(self._weights.values()) or 1.0
            self._last_pct = 0
        self._started = time.monotonic()
        self._last_status = float("-inf")
        steps = [(key, labels[key], grouped[key]) for key in STEP_ORDER if grouped[key]]
        self.steps_planned.emit([(key, label, len(group)) for key, label, group in steps])
        return steps

    def _publish_progress(self, job: Job, fraction: float, *, final: bool = False) -> None:
        """Avance la barre globale ; elle ne recule jamais et ne dépasse 99 % qu'à la toute fin."""
        fraction = max(0.0, min(1.0, float(fraction)))
        with self._track_lock:
            old = self._fractions.get(job.index, 0.0)
            if fraction <= old:
                return
            self._fractions[job.index] = fraction
            self._done_units += fraction - old
            self._weighted += (fraction - old) * self._weights.get(job.index, 0.0)
            pct = max(self._last_pct, min(99, int(self._done_units / self._total_jobs * 100)))
            self._last_pct = pct
        self.emit_progress(pct, 100, "")
        self._publish_status(force=final)

    def _finish_job(self, job: Job) -> None:
        """Fichier terminé (réussi ou non) : sa part compte entièrement dans la barre."""
        self._publish_progress(job, 1.0, final=True)

    def _publish_status(self, force: bool = False) -> None:
        """Libellé d'étape sous le tableau : « Étape 2/4 · Images — 12/340 · reste ~1 min 20 s »."""
        now = time.monotonic()
        if not force and now - self._last_status < STATUS_INTERVAL:
            return
        self._last_status = now
        label, number, steps, count, counter = self._step
        if not label:
            return
        with self._track_lock:
            # Le temps restant est pondéré par le coût des fichiers, pas par leur simple nombre.
            fraction = self._weighted / self._total_weight
        elapsed = max(0.0, now - self._started)
        parts = [self.tr("Étape {number}/{steps} · {label}").format(number=number, steps=steps, label=label),
                 f"{counter['done']}/{count}"]
        if fraction > 0.02 and elapsed > 2:
            remaining = elapsed * (1.0 - fraction) / fraction
            if remaining >= 1:
                parts.append(self.tr("reste ~{time}").format(time=format_duration(remaining)))
        self.emit_status(" — ".join(parts))

    # ── Traitement ──
    def do_work(self) -> object:
        if self._encoders is None:
            self._encoders = detect_encoders(self, self._ffmpeg)
            self._hardware_failed = probe_hardware(self, self._ffmpeg, self._encoders or frozenset())
        self.check_cancelled()
        multi = len({str(i.root) for i in self._items if i.root is not None}) > 1
        bases = {engine._output_base(i, self._out_dir, multi) for i in self._items}
        bases.discard(None)
        manifests = {base: engine.load_manifest(base) for base in bases}
        jobs = engine.plan_jobs(self._items, self._profiles, self._out_dir, self._encoders,
                                name_ext=self._options.name_ext, manifests=manifests,
                                hardware_failed=self._hardware_failed)
        todo: list[Job] = []
        warned: set[str] = set()
        stats = {"skipped": 0, "unsupported": 0, "kept": 0, "copied": 0}
        omitted = self._omit_consumed_json(jobs) if (self._options.takeout and self._options.skip_takeout_json) else 0
        for job in jobs:
            if job.state == STATE_PENDING:
                todo.append(job)
                continue
            self._set_state(job, job.state)
            if job.state == STATE_SKIPPED:
                stats["skipped"] += 1
            elif job.state == STATE_JSON_OMITTED:
                continue                                    # volontaire : compté à part, jamais vérifié dans la sortie
            elif job.state == STATE_UNSUPPORTED:
                stats["unsupported"] += 1
                self.emit_log("WARN", f"{job.item.path.name} : {job.message or 'format non pris en charge'}, ignoré.")
            elif job.state == STATE_NO_ENCODER:
                self.error_count += 1
                if job.profile.id not in warned:
                    warned.add(job.profile.id)
                    self.emit_log("ERR", job.message)
        total = len(todo)
        self.emit_log("INFO", f"{total} fichier(s) à convertir, {stats['skipped']} déjà converti(s)"
                              + (f", {omitted} JSON Takeout non recopié(s) (données déjà réinjectées)." if omitted else "."))
        if self._options.takeout and self._exiftool is None:
            self.emit_log("WARN", "ExifTool introuvable : les dates et positions Takeout ne seront pas écrites "
                                  "dans les fichiers (seule la date du fichier est réalignée).")
        steps = self._plan_steps(todo)
        self.emit_progress(0, 100, "")
        self.emit_status("")                           # chaque étape publiera son libellé
        stats_lock = threading.Lock()

        def process(job: Job) -> None:
            self.check_cancelled()
            if self._options.memory_guard:
                self.wait_for_memory()
            name = job.item.path.name
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
                self._step[4]["done"] += 1
            self._finish_job(job)

        if todo:
            self.emit_log("INFO", self.tr(
                "Parallélisme : copies {copy} · images {image} · audios {audio} · vidéos {video} "
                "fichier(s) à la fois.").format(**self._parallel_by_kind))
            cpus = os.cpu_count() or 1
            for kind, name in (("video", self.tr("vidéo")), ("image", self.tr("image"))):
                count = self._parallel_by_kind.get(kind, 1)
                per_file = getattr(self._profiles.get(kind), "threads", 0)
                if count > 1 and per_file and count * per_file > cpus:
                    self.emit_log("WARN", self.tr(
                        "Parallélisme {kind} : {count} × {threads} threads pour {cpus} cœur(s) ; réduisez le "
                        "nombre de fichiers simultanés ou le réglage Threads pour éviter la surcharge.")
                        .format(kind=name, count=count, threads=per_file, cpus=cpus))
        try:
            if todo:
                for number, (key, label, step_jobs) in enumerate(steps, 1):
                    self.check_cancelled()
                    workers = self._parallel_by_kind.get(key, 1)
                    self._step = (label, number, len(steps), len(step_jobs), {"done": 0})
                    self._publish_status(force=True)
                    self.emit_log("INFO", self.tr(
                        "Étape {number}/{steps} : {label} ({count} fichier(s), {parallel} en parallèle).")
                        .format(number=number, steps=len(steps), label=label, count=len(step_jobs),
                                parallel=workers))
                    if workers <= 1:
                        for job in step_jobs:
                            process(job)
                    else:
                        # Le pool ne publie pas son propre compteur : la barre reste celle du lot entier.
                        self.map_parallel(process, step_jobs, workers=workers, report_progress=False)
        finally:
            self._write_problems(jobs)
            self._save_manifests(jobs, manifests)
        if todo:
            self.emit_progress(100, 100, "")           # une seule traversée de 0 à 100 % pour tout le lot
        missing = self._verify(jobs)
        gain = self._bytes_in - self._bytes_out
        self.summary = self.tr(
            "{ok} converti(s), {copied} copié(s), {kept} source(s) conservée(s), {skipped} déjà "
            "converti(s), {unsupported} non pris en charge, {errors} erreur(s) ; gain : {gain}"
        ).format(ok=self.ok_count, copied=stats["copied"], kept=stats["kept"],
                 skipped=stats["skipped"], unsupported=stats["unsupported"],
                 errors=self.error_count, gain=format_size(max(gain, 0)))
        if self._options.takeout:
            self.summary += self.tr(" ; métadonnées Takeout : {ok}, sans JSON : {missing}").format(
                ok=self._meta_ok, missing=self._no_json)
        if omitted:
            self.summary += self.tr(" ; JSON Takeout non recopiés : {count}").format(count=omitted)
        if self._metadata_copied:
            self.summary += self.tr(" ; métadonnées d'origine recopiées : {count}").format(
                count=self._metadata_copied)
        if self._metadata_skipped:
            self.summary += self.tr(" ; métadonnées non recopiées : {count}").format(
                count=self._metadata_skipped)
        if self._takeout_unwritable:
            self.summary += self.tr(" ; champs Takeout non inscriptibles : {count}").format(
                count=self._takeout_unwritable)
        self.notification_summary = conversion_notification(
            ok=self.ok_count, errors=self.error_count, skipped=stats["skipped"],
            unsupported=stats["unsupported"], missing=missing, gain=max(gain, 0))
        self.emit_log("OK" if not self.error_count and not missing else "WARN", self.summary)
        stats["missing"] = missing
        return stats

    def _omit_consumed_json(self, jobs: list[Job]) -> int:
        """Marque « JSON non copié » les JSON Takeout dont la date/position est réinjectée dans un média de la file.

        Ne supprime et ne modifie rien : le JSON reste dans la source, il n'est simplement pas recopié dans la
        sortie. Un JSON n'est écarté que s'il est bien le compagnon d'un média de la file (même dossier, même
        appariement que la réinjection) ET porte une date ou une position exploitable ; tout autre JSON, y compris
        un document sans rapport nommé « x.json », reste copié comme n'importe quel fichier.
        """
        if self._exiftool is None:
            # Sans ExifTool, date et position ne sont PAS écrites dans les fichiers : le JSON est alors la seule
            # trace de ces données dans la sortie, on ne l'écarte donc jamais.
            self.emit_log("WARN", "ExifTool introuvable : les JSON Takeout sont recopiés dans la sortie "
                                  "(leurs données n'étant pas écrites dans les fichiers).")
            return 0
        consumed: set[str] = set()
        for job in jobs:
            if job.item.kind == OTHER_KIND or job.state not in (STATE_PENDING, STATE_SKIPPED):
                continue                                   # média non converti : son JSON garde sa place dans la sortie
            self.check_cancelled()
            folder = job.item.path.parent
            with self._lock:
                index = self._indexes.get(folder)
                if index is None:
                    index = self._indexes[folder] = tk.DirIndex(folder)
            js = index.find(job.item.path)
            if js is None:
                continue
            sc = tk.load_sidecar(js)
            if sc is not None and (sc.taken is not None or sc.has_gps):
                consumed.add(engine.path_key(js))
        count = 0
        for job in jobs:
            if job.item.kind == OTHER_KIND and job.state == STATE_PENDING and engine.path_key(job.item.path) in consumed:
                job.state = STATE_JSON_OMITTED
                job.message = "Données déjà réinjectées dans le média converti ; JSON laissé dans la source."
                count += 1
        return count

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
    def _save_manifests(self, jobs: list[Job], manifests: dict[Path, dict]) -> None:
        """Enregistre l'état des sorties réellement produites (profil, source, taille)."""
        if not manifests:
            return
        for base, current in manifests.items():
            updated = dict(current)
            changed = False
            for job in jobs:
                if job.dest is None or job.final is None or job.state not in (STATE_DONE, STATE_KEPT, STATE_COPIED):
                    continue
                if engine.path_key(job.final) == engine.path_key(job.item.path):
                    continue                                   # source conservée : pas de sortie à décrire
                try:
                    job.final.relative_to(base)
                except ValueError:
                    continue                                   # sortie d'une autre racine de sortie
                try:
                    size = job.final.stat().st_size
                except OSError:
                    continue
                key = engine.manifest_key(job.final, base)
                rec = {"profile": engine.profile_fingerprint(job.profile), "source": str(job.item.path),
                       "source_size": job.item.size, "source_mtime": job.item.mtime, "dest_size": size}
                if updated.get(key) != rec:
                    updated[key] = rec
                    changed = True
            if changed:
                try:
                    engine.save_manifest(base, updated)
                except OSError as exc:
                    self.emit_log("WARN", f"État de reprise non enregistré ({base}) : {exc}")

    def _convert_one(self, job: Job) -> str:
        """Convertit un fichier. Renvoie « ok », « kept », « copied », « unsupported » ou « error »."""
        src, dest, profile = job.item.path, job.dest, job.profile
        if dest is None:
            self.emit_log("ERR", f"{src.name} : dossier de sortie non défini.")
            self._set_state(job, STATE_ERROR)
            return "error"
        dest.parent.mkdir(parents=True, exist_ok=True)
        is_other = job.item.kind == OTHER_KIND
        if not is_other and not job.passthrough and self._is_empty_file(src):
            self.emit_log("ERR", f"{src.name} : fichier vide (0 octet), rien à convertir. Original conservé.")
            self._set_state(job, STATE_ERROR)
            return "error"
        sidecar = self._sidecar_for(job.item) if self._options.takeout and not is_other else None
        if is_other:
            outcome = self._copy(job, dest, "ni image, ni vidéo, ni audio")
        elif job.passthrough:
            if (profile.kind == "video" and profile.out_ext.lower() == ".webm"
                    and self._webm_has_mono_audio(src)):
                self.emit_log("INFO", f"{src.name} : audio mono détecté, réencodage automatique en stéréo "
                                      "pour le WebM.")
                outcome = self._convert_stream(job, sidecar)
            else:
                outcome = self._copy(job, dest, f"déjà au format {profile.out_ext}")
        elif profile.kind == "image":
            outcome = self._convert_image(job, sidecar)
        else:
            outcome = self._convert_stream(job, sidecar)
        if outcome in ("ok", "kept", "copied") and job.final is not None and self._options.takeout and not is_other:
            self._inject(job, sidecar)
        return outcome

    @staticmethod
    def _is_empty_file(path: Path) -> bool:
        try:
            return path.stat().st_size == 0
        except OSError:
            return False                                    # illisible : laisser FFmpeg donner le vrai diagnostic

    def _copy(self, job: Job, target: Path, why: str) -> str:
        """Copie la source telle quelle (déjà au bon format, multi-images…)."""
        src = job.item.path
        part = engine.part_path(target)
        try:
            shutil.copy2(src, part)
            final = publish_no_replace(part, target)        # jamais d'écrasement (AZ-010)
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
        if dest is None:
            raise RuntimeError(f"{src.name} : dossier de sortie non défini.")
        if imaging.is_multiframe(src):                         # GIF animé / TIFF multi-pages : rien ne doit se perdre
            return self._copy(job, job.copy_dest or dest.with_name(src.name), "images multiples")
        if profile.id == "image_heic":
            return self._convert_heic(job)
        tmp = engine.part_path(dest)
        prepared: Path | None = None
        alias_dir: Path | None = None
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
            else:
                try:
                    inp, alias_dir = engine.neutral_input(src)          # nom contenant « % » : motif de séquence FFmpeg
                except OSError as exc:
                    self.emit_log("ERR", f"{src.name} : lecture impossible ({exc}).")
                    self._set_state(job, STATE_ERROR)
                    return "error"
            size = imaging.image_size(inp)
            if size is not None:
                small = engine.av1_small_media_encoder(profile, self._encoders, *size)
                if small:
                    profile = replace(profile, video_encoders=(small,))
                    self.emit_log("INFO", f"{src.name} : petit média AV1 ({size[0]}×{size[1]}), "
                                          f"repli sur {small}.")
            presets = [profile.preset]
            if (profile.preset.isdigit() and "libsvtav1" in profile.video_encoders
                    and "libsvtav1" in (self._encoders or ("libsvtav1",))):
                presets += [str(p) for p in IMAGE_PRESET_FALLBACKS if p > int(profile.preset)]
            res = None
            progress_key = {"v": None}
            stall_retries = 0
            # Dossier de sortie contenant « % » : l'écriteur d'images de FFmpeg y lirait un motif de séquence.
            # On lance alors FFmpeg DANS ce dossier avec un nom relatif (« ./ » évite d'y voir un protocole).
            pattern_safe = "%" in str(tmp.parent) and Path(str(self._ffmpeg)).is_absolute()
            out_arg: Path | str = f".{os.sep}{tmp.name}" if pattern_safe else tmp
            run_cwd = tmp.parent if pattern_safe else None

            def on_line(line: str) -> None:
                if line.startswith(("out_time_us=", "out_time_ms=")):
                    progress_key["v"] = line

            def activity() -> object:
                try:
                    size = tmp.stat().st_size
                except OSError:
                    size = -1
                return (progress_key["v"], size)

            for i, preset in enumerate(presets):
                self.check_cancelled()
                try:
                    cmd = engine.build_command(replace(profile, preset=preset), inp, out_arg, ffmpeg=self._ffmpeg,
                                               encoders=self._encoders, lp_ok=self._lp_ok)
                except engine.EncoderMissingError:
                    self.emit_log("ERR", self._missing_encoder_message(profile))
                    self._set_state(job, STATE_NO_ENCODER)
                    return "error"
                t0 = time.monotonic()
                res = self.run_process(cmd, on_stdout_line=on_line, timeout=600.0, nice=self._nice(),
                                       stall_timeout=IMAGE_STALL_FLOOR, activity=activity,
                                       capture_stdout=False, cwd=run_cwd)
                if res.cancelled or self.is_cancelled():
                    self._discard(tmp)
                    self._set_state(job, STATE_CANCELLED)
                    raise CancelledError()
                if res.stalled:
                    self._discard(tmp)
                    faster = presets[i + 1] if i + 1 < len(presets) else None
                    if faster is not None and not stall_retries:
                        stall_retries += 1
                        self.emit_log("WARN", f"{src.name} : encodage bloqué (aucun signe de vie pendant "
                                              f"{format_duration(IMAGE_STALL_FLOOR)}) ; nouvelle tentative avec "
                                              f"le preset plus rapide « {faster} ».")
                        continue
                    self.emit_log("ERR", f"{src.name} : encodage d'image bloqué (aucun signe de vie pendant "
                                         f"{format_duration(IMAGE_STALL_FLOOR)}) et arrêté.")
                    self._set_state(job, STATE_ERROR)
                    return "error"
                if res.returncode == 0 and not res.timed_out:
                    outcome = self._finish(job, tmp, t0)
                    if outcome == "ok" and prepared is not None and job.final is not None:
                        self._copy_metadata(src, job.final)
                    return outcome
                self._discard(tmp)
                if not any(k in res.stderr.lower() for k in RESOLUTION_ERRORS):
                    break
            return self._fail(job, res)
        finally:
            engine.drop_neutral_input(alias_dir)
            if prepared is not None:
                self._discard(prepared)
            self._discard(tmp)

    def _convert_heic(self, job: Job) -> str:
        """HEIC n'a pas d'encodeur FFmpeg : Pillow développe la source puis écrit le HEIC."""
        src, dest, profile = job.item.path, job.dest, job.profile
        if dest is None:
            raise RuntimeError(f"{src.name} : dossier de sortie non défini.")
        if not imaging.heif_available():
            message = engine.missing_encoder_message(profile, self._encoders or (), self._hardware_failed)
            self.emit_log("ERR", message or f"{src.name} : pillow-heif non installé.")
            self._set_state(job, STATE_NO_ENCODER)
            return "error"
        tmp = engine.part_path(dest)
        prepared: Path | None = None
        try:
            self.check_cancelled()
            try:
                prepared = imaging.prepare_png(src)
            except RuntimeError as exc:
                self.emit_log("WARN", f"{src.name} : {exc}.")
                self._set_state(job, STATE_UNSUPPORTED)
                return "unsupported"
            except Exception as exc:  # noqa: BLE001 - image corrompue ou trop grande pour Pillow
                self.emit_log("ERR", f"{src.name} : développement de l'image impossible ({exc}).")
                self._set_state(job, STATE_ERROR)
                return "error"
            self.check_cancelled()
            t0 = time.monotonic()
            try:
                imaging.save_heic(prepared, tmp, profile.quality if profile.quality is not None else 80)
            except Exception as exc:  # noqa: BLE001 - encodeur HEIC absent ou image refusée
                self.emit_log("ERR", f"{src.name} : écriture HEIC impossible ({exc}).")
                self._set_state(job, STATE_ERROR)
                return "error"
            outcome = self._finish(job, tmp, t0)
            if outcome == "ok" and job.final is not None:
                self._copy_metadata(src, job.final)
            return outcome
        finally:
            if prepared is not None:
                self._discard(prepared)
            self._discard(tmp)

    # ── Vidéo et audio ──
    def _stall_seconds(self, job: Job) -> float:
        size_mb = job.item.size / (1024 * 1024)
        proc_timeout = max(300, int(size_mb * 2))
        slow = (SLOW_PRESET_FLOOR.get(int(job.profile.preset), 0)
                if job.profile.kind == "video" and job.profile.preset.isdigit() else 0)   # pas le niveau FLAC
        return float(max(STALL_FLOOR_MIN, slow, (proc_timeout + 59) // 60) * 60)

    def _convert_stream(self, job: Job, sidecar: tk.Sidecar | None) -> str:
        src, dest, profile = job.item.path, job.dest, job.profile
        if dest is None:
            raise RuntimeError(f"{src.name} : dossier de sortie non défini.")
        tmp = engine.part_path(dest)
        is_video = profile.kind == "video"
        info = self._media_probe(src) if is_video else None
        if info is not None:
            small = engine.av1_small_media_encoder(profile, self._encoders, info.width, info.height)
            if small:
                profile = replace(profile, video_encoders=(small,))
                self.emit_log("INFO", f"{src.name} : petit média AV1 ({info.width}×{info.height}), "
                                      f"repli sur {small}.")
        duration = info.duration if info else self._probe_duration(src)
        hdr_filter = None
        first_audio_only = False
        plain_audio = False
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
        attempts = probing.VIDEO_ATTEMPTS if is_video else probing.audio_attempts(src)
        res = None
        prev_res = None
        empty_output: str | None = None
        last_pct = {"v": -1, "key": None}

        def on_line(line: str) -> None:
            if line.startswith(("out_time_us=", "out_time_ms=")):
                last_pct["key"] = line
            pct = engine.parse_progress_line(line, duration)
            if pct is not None and pct != last_pct["v"]:
                last_pct["v"] = pct
                self.job_updated.emit(job.index, {"progress": pct})
                self._publish_progress(job, pct / 100)

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
                                               first_audio_only=first_audio_only, plain_audio=plain_audio)
                except engine.EncoderMissingError:
                    self.emit_log("ERR", self._missing_encoder_message(profile))
                    self._set_state(job, STATE_NO_ENCODER)
                    return "error"
                if is_video:
                    res = self.run_process(cmd, on_stdout_line=on_line, nice=self._nice(),
                                           stall_timeout=self._stall_seconds(job), activity=activity,
                                           capture_stdout=False)
                else:
                    res = self.run_process(cmd, on_stdout_line=on_line, nice=self._nice(),
                                           timeout=max(1800.0, size_mb * 10), capture_stdout=False)
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
                    verdict = self._check_output(src.name, tmp, duration)
                    if verdict is None:
                        return self._finish(job, tmp, t0, attempt + 1 if attempts > 1 else None)
                    empty_output = verdict                  # code retour 0 mais fichier inutilisable
                    self._discard(tmp)
                    prev_res = res
                    if attempt + 1 < attempts:
                        self.emit_log("WARN", f"{src.name} : tentative {attempt + 1}/{attempts} sans contenu "
                                              f"exploitable ({verdict}) ; nouvelle tentative.")
                    continue
                self._discard(tmp)
                if prev_res is not None and "ignore_editlist not found" in res.stderr.lower():
                    res = prev_res           # extension MP4 mais contenu autre : l'option est refusée, on garde le vrai échec
                    break
                prev_res = res
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
                                          f"{engine.stderr_digest(res.stderr, 300)}")
                if (not plain_audio and engine.needs_plain_audio(res.stderr, audio_only=not is_video)):
                    plain_audio = True                         # disposition de canaux inconnue : reprise sans `aformat`
                    if attempt + 1 < attempts:
                        self.emit_log("INFO", f"{src.name} : disposition des canaux non reconnue ; nouvelle "
                                              "tentative sans filtre de canaux.")
                if self._lp_ok and "svt" in low and "param" in low:
                    self._lp_ok = False
                    self.emit_log("WARN", "Paramètre SVT-AV1 « lp » refusé par ce ffmpeg : désactivé.")
                if info is not None and len(info.audio) > 1 and (
                        "no decoder found" in low or "could not find codec parameters" in low):
                    first_audio_only = True                    # filet de sécurité : première piste audio seule
            if empty_output is not None and res is not None and res.returncode == 0:
                self.emit_log("ERR", f"{src.name} : FFmpeg n'a produit aucun contenu exploitable "
                                     f"({empty_output}) malgré {attempts} tentative(s) ; la source est peut-être "
                                     "vide, tronquée ou dans un codec que ce FFmpeg ne décode pas. "
                                     "L'original est conservé, rien n'a été écrit.")
                self._set_state(job, STATE_ERROR)
                return "error"
            return self._fail(job, res)
        finally:
            self._discard(tmp)

    def _probe_duration(self, source: Path) -> float | None:
        res = self.run_process(engine.build_probe_command(self._ffprobe, source), timeout=30)
        return engine.parse_duration(res.stdout) if res.returncode == 0 else None

    def _check_output(self, name: str, tmp: Path, src_duration: float | None) -> str | None:
        """Contrôle un fichier que ffmpeg dit avoir écrit. None = utilisable ; sinon, le motif du rejet.

        Un média dont la liste d'édition masque tous les échantillons peut ressortir en « succès » (code 0)
        avec un fichier de quelques centaines d'octets, sans aucun paquet : ffprobe ne sait même pas
        l'ouvrir. On ne se fie donc pas au seul code de retour. Si ffprobe est introuvable, on ne bloque rien.
        """
        res = self.run_process(engine.build_probe_command(self._ffprobe, tmp), timeout=60)
        if res.cancelled or self.is_cancelled():
            raise CancelledError()
        if res.returncode == -1 and (res.stderr or "").startswith("Impossible de lancer"):
            return None                                           # pas de ffprobe : contrôle impossible, pas bloquant
        if res.returncode != 0:
            return "fichier de sortie illisible"
        out = engine.parse_duration(res.stdout)
        if out is not None and src_duration and src_duration >= 2.0 and out < 0.5 * src_duration:
            self.emit_log("WARN", f"{name} : la sortie dure {format_duration(out)} pour une source de "
                                  f"{format_duration(src_duration)} ; vérifiez le résultat.")
        return None

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
            self.emit_log("ERR", f"{src.name} : encodeur manquant dans ce FFmpeg. {engine.stderr_digest(stderr)}")
        else:
            self.emit_log("ERR", f"{src.name} : échec de FFmpeg ({profile.label}). {engine.stderr_digest(stderr)}")
            hint = engine.failure_hint(stderr)
            if hint:
                self.emit_log("WARN", f"{src.name} : {hint}")
            if advanced.hardware_kind(profile):
                self.emit_log("WARN", "L'encodeur matériel n'a pas pu démarrer. Vérifiez le pilote et le "
                                      "périphérique, ou choisissez un profil logiciel (H.264/H.265).")
            if "Unable to find a suitable output format" in (stderr or ""):
                self.emit_log("WARN", f"Ce FFmpeg ne sait pas écrire le format « {profile.out_ext} » (version trop "
                                      "ancienne ?). Installez un FFmpeg récent (6 ou plus) ou indiquez-le dans les "
                                      "Préférences.")
        return "error"

    def _finish(self, job: Job, tmp: Path, t0: float, attempt: int | None = None) -> str:
        src, dest, profile = job.item.path, job.dest, job.profile
        if dest is None:
            raise RuntimeError(f"{src.name} : dossier de sortie non défini.")
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
        if tmp.is_symlink():                                      # emplacement préparé par un tiers
            self._discard(tmp)
            self.emit_log("ERR", f"{src.name} : fichier temporaire suspect (lien symbolique), abandon.")
            self._set_state(job, STATE_ERROR)
            return "error"
        if job.replace_existing and job.replace_size is not None and dest.exists() and not dest.is_symlink():
            try:
                if dest.stat().st_size == job.replace_size:
                    os.replace(tmp, dest)                       # sortie que nous avions écrite : remplacement atomique
                    final = dest
                else:
                    final = publish_no_replace(tmp, dest)       # modifiée entre-temps : jamais écrasée
            except OSError:
                final = publish_no_replace(tmp, dest)
        else:
            final = publish_no_replace(tmp, dest)                # jamais d'écrasement, même en cas de course
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
        target = Path(target)
        part = engine.part_path(target)
        try:
            shutil.copy2(src, part)
            target = publish_no_replace(part, target)
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

    def _copy_metadata(self, source: Path, target: Path) -> None:
        """Recopie les métadonnées de la source sur une sortie développée par Pillow."""
        if self._exiftool is None:
            self._metadata_skipped += 1
            if not self._warned_exiftool:
                self._warned_exiftool = True
                self.emit_log("WARN", "ExifTool introuvable : les métadonnées des images développées par Pillow "
                                      "(HEIC, RAW, TIFF, orientation) ne peuvent pas être recopiées.")
            return
        source, target = Path(source).absolute(), Path(target).absolute()
        try:
            argfile = tags.write_argfile(engine.metadata_copy_args(source, target))
        except (OSError, ValueError) as exc:
            self._metadata_skipped += 1
            self.emit_log("WARN", f"{target.name} : copie des métadonnées impossible ({exc}).")
            return
        try:
            res = self.run_process(tags.write_command(self._exiftool, argfile), timeout=120.0)
        finally:
            self._discard(argfile)
        ok, msg = tags.parse_write_result(res.returncode, res.stdout, res.stderr)
        if ok:
            self._metadata_copied += 1
        else:
            self._metadata_skipped += 1
            self.emit_log("WARN", f"{target.name} : métadonnées d'origine non recopiées ({msg}).")

    def _inject(self, job: Job, sidecar: tk.Sidecar | None) -> None:
        """Écrit date et GPS dans le fichier produit (ExifTool), puis aligne sa date de modification."""
        final = job.final
        if sidecar is None or final is None:
            return
        if engine.path_key(final) == engine.path_key(job.item.path):
            self.emit_log("INFO", f"{final.name} : source conservée, métadonnées Takeout non écrites dans l'original.")
            return
        opts = tk.Options(overwrite=True, tz=self._options.tz)
        try:
            plan = tk.build_plan(final, sidecar, [], opts)
            args = tags.change_args(tags.resolve_changes(final, plan.changes))
        except ValueError as exc:
            self.emit_log("WARN", f"{final.name} : métadonnées Takeout ignorées ({exc}).")
            return
        if args:
            if self._exiftool is not None:
                try:
                    argfile = tags.write_argfile([*args, str(Path(final).absolute())])
                except (OSError, ValueError) as exc:
                    self.emit_log("WARN", f"{final.name} : métadonnées Takeout ignorées ({exc}).")
                    argfile = None
                if argfile is not None:
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
        elif tags.write_refusal(final) is not None:
            self._takeout_unwritable += 1
            note = " ".join(plan.notes)
            self.emit_log("WARN", f"{final.name} : métadonnées Takeout non inscriptibles"
                                  + (f" ({note})." if note else " (format non modifiable)."))
        if plan.mtime is not None:
            try:
                ts = plan.mtime.timestamp()
                os.utime(final, (ts, ts))
            except (OSError, OverflowError, ValueError) as exc:
                _log.debug("Date non appliquée à %s : %s", final, exc)
