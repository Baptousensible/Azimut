"""Azimut Press — workers : scan des entrées et compression par lots."""
from __future__ import annotations

import os
import shutil
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from azimut.core.fsutil import format_size, iter_files, safe_relative, unique_path
from azimut.core.workers import BaseWorker
from azimut.modules.press import imagepress, jsonpress
from azimut.modules.press.imagepress import ImageSettings
from azimut.modules.press.jsonpress import JsonSettings

KIND_IMAGE = "image"
KIND_JSON = "json"

# États d'une ligne du tableau
ST_DONE = "done"
ST_ALREADY = "already"
ST_ANIMATED = "animated"
ST_INVALID = "invalid"
ST_ERROR = "error"
SUCCESS_STATES = frozenset({ST_DONE, ST_ALREADY, ST_ANIMATED})

STATE_PENDING = "En attente"
STATE_LABELS: dict[str, str] = {
    ST_DONE: "Terminé",
    ST_ALREADY: "Déjà optimisé",
    ST_ANIMATED: "Ignoré (animée)",
    ST_INVALID: "JSON invalide",
    ST_ERROR: "Erreur",
}
STATE_TAGS: dict[str, str] = {   # couleur de ligne du RowTableModel
    ST_DONE: "ok",
    ST_ALREADY: "info",
    ST_ANIMATED: "neutre",
    ST_INVALID: "warn",
    ST_ERROR: "err",
}


@dataclass
class ItemResult:
    """Résultat du traitement d'un fichier."""
    status: str
    size_before: int = 0
    size_after: int | None = None
    output: Path | None = None
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.status in SUCCESS_STATES


@dataclass
class PressItem:
    """Un fichier de la file d'attente."""
    path: Path
    root: Path                  # dossier de référence de l'arborescence relative
    kind: str                   # KIND_IMAGE | KIND_JSON
    size: int = 0
    display: str = ""           # libellé de la colonne « Fichier »
    result: ItemResult | None = None

    @property
    def done(self) -> bool:
        return self.result is not None and self.result.ok


@dataclass
class PressOptions:
    image_settings: ImageSettings = field(default_factory=ImageSettings)
    json_settings: JsonSettings = field(default_factory=JsonSettings)
    out_dir: Path | None = None         # None : dossier « <source>_Pressed » voisin de la source
    in_place: bool = False
    backup_dir: Path | None = None      # obligatoire si in_place
    workers: int | None = None          # None : min(4, nombre de cœurs)


@dataclass
class PressSummary:
    ok: int = 0
    errors: int = 0
    total_before: int = 0
    total_after: int = 0

    @property
    def saved(self) -> int:
        return self.total_before - self.total_after


def pressed_dir(root: Path) -> Path:
    """Dossier de sortie par défaut : « <source>_Pressed », voisin de la source."""
    if not root.name:   # racine de disque
        return root / "Azimut_Pressed"
    return root.with_name(f"{root.name}_Pressed")


def gain_percent(before: int, after: int) -> float:
    return (before - after) * 100.0 / before if before else 0.0


def format_gain(before: int, after: int) -> str:
    return f"{gain_percent(before, after):.1f}".replace(".", ",") + " %"


def kind_of(path: Path) -> str | None:
    """Type d'un fichier selon son extension, ou None s'il n'est pas pris en charge."""
    ext = path.suffix.lower()
    if ext == ".json":
        return KIND_JSON
    if ext in imagepress.supported_extensions():
        return KIND_IMAGE
    return None


def all_extensions() -> tuple[str, ...]:
    return (*imagepress.supported_extensions(), ".json")


class ScanWorker(BaseWorker):
    """Parcourt fichiers et dossiers et renvoie la liste des PressItem à ajouter."""

    def __init__(self, paths: list[Path], recursive: bool, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.paths = paths
        self.recursive = recursive

    def do_work(self) -> list[PressItem]:
        items: list[PressItem] = []
        seen: set[Path] = set()
        extensions = all_extensions()

        def add(path: Path, root: Path, display: str) -> None:
            kind = kind_of(path)
            try:
                key = path.resolve()
                if kind is None or key in seen:
                    return
                seen.add(key)
                items.append(PressItem(path, root, kind, path.stat().st_size, display))
            except OSError as exc:
                self.emit_log("WARN", f"{path.name} : lecture impossible ({exc})")

        total = len(self.paths)
        for number, path in enumerate(self.paths, start=1):
            self.check_cancelled()
            if path.is_dir():
                for found in iter_files(path, recursive=self.recursive, extensions=extensions,
                                        cancel=self.is_cancelled):
                    rel = safe_relative(found, path)
                    add(found, path, f"{path.name}/{rel.as_posix()}" if path.name else rel.as_posix())
            elif path.is_file():
                if kind_of(path) is None:
                    self.emit_log("WARN", f"{path.name} : format non pris en charge, ignoré.")
                else:
                    add(path, path.parent, path.name)
            else:
                self.emit_log("WARN", f"{path} : introuvable.")
            self.emit_progress(number, total, path.name)
        self.check_cancelled()
        self.ok_count = len(items)
        self.summary = f"{len(items)} fichier(s) ajouté(s)"
        self.emit_log("INFO", self.summary + ".")
        return items


class _Skipped(Exception):
    """Fichier abandonné car l'annulation a été demandée avant son écriture."""


class PressWorker(BaseWorker):
    """Compresse les fichiers de la file ; un signal item_done par fichier traité."""

    item_done = Signal(int, object)     # (indice de ligne, ItemResult)

    def __init__(self, jobs: list[tuple[int, PressItem]], options: PressOptions,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.jobs = jobs
        self.options = options
        self._commit_lock = threading.Lock()   # choix du nom de sortie + renommage, en série

    # --- Orchestration -------------------------------------------------------------------------

    def do_work(self) -> PressSummary:
        opts = self.options
        total = len(self.jobs)
        self.emit_status(f"Compression de {total} fichier(s)…")
        if any(item.kind == KIND_JSON for _, item in self.jobs) and opts.json_settings.mode == jsonpress.MODE_INDENT:
            self.emit_log("WARN", jsonpress.INDENT_WARNING)
        if opts.in_place:
            self.emit_log("WARN", f"Remplacement sur place : les originaux sont déplacés dans {opts.backup_dir}.")
        workers = opts.workers or min(4, os.cpu_count() or 2)
        results = self.map_parallel(self._process, self.jobs, workers=workers, label="Compression")
        self.check_cancelled()

        summary = PressSummary()
        for (row, item), result in zip(self.jobs, results):
            if result is None:   # exception inattendue interceptée par map_parallel
                result = ItemResult(ST_ERROR, item.size, message="échec inattendu")
                self.item_done.emit(row, result)
            if result.ok:
                summary.ok += 1
                summary.total_before += result.size_before
                summary.total_after += result.size_after or 0
            else:
                summary.errors += 1
        self.ok_count, self.error_count = summary.ok, summary.errors
        self.summary = (
            f"{summary.ok} fichier(s) traité(s), {summary.errors} erreur(s) — "
            f"gain {format_size(summary.saved)} ({format_gain(summary.total_before, summary.total_after)})"
        )
        self.emit_log("OK" if not summary.errors else "WARN", self.summary)
        return summary

    def _process(self, job: tuple[int, PressItem]) -> ItemResult | None:
        row, item = job
        if self.is_cancelled():
            return None
        try:
            result = self._run_item(item)
        except _Skipped:
            return None
        except jsonpress.JsonInvalidError as exc:
            result = ItemResult(ST_INVALID, item.size, message=str(exc))
        except Exception as exc:  # noqa: BLE001 — un fichier en échec ne doit pas arrêter le lot
            result = ItemResult(ST_ERROR, item.size, message=str(exc) or exc.__class__.__name__)
        self._log_result(item, result)
        self.item_done.emit(row, result)
        return result

    def _log_result(self, item: PressItem, result: ItemResult) -> None:
        name = item.display or item.path.name
        if result.status == ST_INVALID:
            self.emit_log("ERR", f"{name} : JSON invalide — {result.message}. Fichier non modifié.")
        elif result.status == ST_ERROR:
            self.emit_log("ERR", f"{name} : {result.message}")
        elif result.status == ST_ANIMATED:
            self.emit_log("INFO", f"{name} : image animée, copiée telle quelle.")
        elif result.status == ST_ALREADY:
            self.emit_log("INFO", f"{name} : déjà optimisé.")
        else:
            after = result.size_after or 0
            self.emit_log("OK", f"{name} : {format_size(result.size_before)} → {format_size(after)} "
                                f"({format_gain(result.size_before, after)})")

    # --- Traitement d'un fichier ---------------------------------------------------------------

    def _run_item(self, item: PressItem) -> ItemResult:
        opts = self.options
        if item.kind == KIND_JSON:
            res = jsonpress.compress_json(item.path, opts.json_settings)
            status = ST_DONE if res.status == jsonpress.STATUS_DONE else ST_ALREADY
            data, suffix, notes = res.data, item.path.suffix, []
            size_in = res.size_in
        else:
            img = imagepress.compress_image(item.path, opts.image_settings)
            status = {imagepress.STATUS_DONE: ST_DONE, imagepress.STATUS_ALREADY: ST_ALREADY}.get(
                img.status, ST_ANIMATED)
            data, suffix, notes, size_in = img.data, img.suffix, img.notes, img.size_in
        for note in notes:
            self.emit_log("WARN", f"{item.display or item.path.name} : {note}")

        rel = safe_relative(item.path, item.root)
        if opts.in_place:
            output = self._commit_in_place(item, rel, data, suffix)
        else:
            base = opts.out_dir if opts.out_dir else pressed_dir(item.root)
            output = self._commit_copy(item, (base / rel).with_suffix(suffix), data)
        size_after = size_in if data is None else output.stat().st_size
        return ItemResult(status, size_in, size_after, output)

    def _write_temp(self, folder: Path, data: bytes | None, src: Path) -> Path:
        """Écrit `data` (ou une copie de `src`) dans un fichier temporaire du dossier cible."""
        folder.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=".press_", suffix=".tmp", dir=folder)
        tmp = Path(name)
        try:
            with os.fdopen(fd, "wb") as out:
                if data is None:
                    with src.open("rb") as source:
                        shutil.copyfileobj(source, out)
                else:
                    out.write(data)
            st = src.stat()
            os.utime(tmp, ns=(st.st_atime_ns, st.st_mtime_ns))   # conserve les dates de l'original
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        return tmp

    def _commit_copy(self, item: PressItem, dest: Path, data: bytes | None) -> Path:
        """Mode dossier de sortie : fichier temporaire puis renommage atomique, nom unique."""
        tmp = self._write_temp(dest.parent, data, item.path)
        try:
            if self.is_cancelled():
                raise _Skipped()
            with self._commit_lock:
                final = unique_path(dest)
                os.replace(tmp, final)
        finally:
            tmp.unlink(missing_ok=True)   # n'existe plus si le renommage a réussi
        return final

    def _commit_in_place(self, item: PressItem, rel: Path, data: bytes | None, suffix: str) -> Path:
        """Mode « Remplacer sur place » : sauvegarde de l'original, puis résultat écrit à sa place."""
        src = item.path
        if data is None:
            return src   # rien à remplacer : l'original reste en place, intact
        backup_root = self.options.backup_dir
        if backup_root is None:
            raise ValueError("dossier de sauvegarde non défini")
        tmp = self._write_temp(src.parent, data, src)
        try:
            if self.is_cancelled():
                raise _Skipped()
            with self._commit_lock:
                backup = unique_path(backup_root / rel)
                if backup.resolve() == src.resolve():
                    raise ValueError("le dossier de sauvegarde contient l'original lui-même")
                backup.parent.mkdir(parents=True, exist_ok=True)
                final = src if suffix == src.suffix else unique_path(src.with_suffix(suffix))
                shutil.move(str(src), str(backup))
                try:
                    os.replace(tmp, final)
                except BaseException:
                    shutil.move(str(backup), str(src))   # remet l'original en place
                    raise
        finally:
            tmp.unlink(missing_ok=True)
        return final
