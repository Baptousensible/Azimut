"""Moteur d'Azimut Dedup : inventaire, empreintes, dHash, regroupement (logique pure, sans Qt)."""
from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from azimut.core.fsutil import format_size, iter_files
from azimut.core.logs import get_logger
from azimut.modules.dedup.model import (
    KIND_EXACT,
    KIND_SIMILAR,
    SIDE_A,
    SIDE_B,
    FileItem,
    Group,
    Strategy,
    resolve_group,
    strategy_from_key,
    totals,
)

_LOG = get_logger(__name__)

IMAGE_EXTS = frozenset({".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tif", ".tiff", ".webp",
                        ".heic", ".heif", ".avif"})
RAW_EXTS = frozenset({".cr2", ".cr3", ".nef", ".arw", ".dng", ".orf", ".rw2", ".raf", ".pef", ".srw"})
VIDEO_EXTS = frozenset({".mp4", ".mov", ".avi", ".mkv", ".m4v", ".mpg", ".mpeg", ".wmv", ".webm",
                        ".3gp", ".mts", ".m2ts"})
MEDIA_EXTS = IMAGE_EXTS | RAW_EXTS | VIDEO_EXTS
SIMILAR_EXTS = IMAGE_EXTS          # les RAW et les vidéos ne sont comparés qu'en exact

QUICK_LIMIT = 128 * 1024           # au plus : fichier entier haché d'emblée
SAMPLE = 64 * 1024                 # taille de chaque échantillon (début, milieu, fin)
BLOCK = 1024 * 1024                # bloc de l'empreinte complète
MAX_BUCKET = 3000                  # paquet de dHash au-delà duquel on abandonne
MAX_TOLERANCE = 7                  # 8 tranches de 8 bits : distance ≤ 7 ⇒ une tranche identique
FULL64 = (1 << 64) - 1


class ScanCancelled(Exception):
    """Analyse annulée (le ScanWorker la convertit en CancelledError du socle)."""


# ── Rappels (progression, annulation, parallélisme) ───────────────────

def _never() -> bool:
    return False


def _default_log(level: str, text: str) -> None:
    _LOG.log({"WARN": 30, "ERR": 40}.get(level, 20), text)


def _no_progress(done: int, total: int, label: str) -> None:
    return None


def _no_status(text: str) -> None:
    return None


@dataclass
class Hooks:
    """Liens avec l'extérieur. Un ScanWorker y branche cancel, emit_log, emit_progress, emit_status
    et `map_parallel` (fn, items, libellé) ; par défaut, un pool de threads local est utilisé."""

    cancel: Callable[[], bool] = _never
    log: Callable[[str, str], None] = _default_log
    progress: Callable[[int, int, str], None] = _no_progress
    status: Callable[[str], None] = _no_status
    map_parallel: Callable[[Callable[[Any], Any], Sequence[Any], str], list[Any]] | None = None
    workers: int = 4

    def check(self) -> None:
        if self.cancel():
            raise ScanCancelled()

    def pmap(self, fn: Callable[[Any], Any], items: Sequence[Any], label: str) -> list[Any]:
        """Résultats dans l'ordre ; None pour un élément en échec. Lève ScanCancelled si annulé."""
        if self.map_parallel is not None:
            return self.map_parallel(fn, items, label)
        return self._local_map(fn, items, label)

    def _run_one(self, fn: Callable[[Any], Any], item: Any) -> Any:
        if self.cancel():
            return None
        try:
            return fn(item)
        except ScanCancelled:
            return None
        except Exception as exc:  # noqa: BLE001 - un fichier en échec ne bloque jamais
            self.log("WARN", f"Échec sur {item} : {exc}")
            return None

    def _local_map(self, fn: Callable[[Any], Any], items: Sequence[Any], label: str) -> list[Any]:
        total = len(items)
        results: list[Any] = [None] * total
        if total == 0:
            return results
        pool = ThreadPoolExecutor(max_workers=max(1, self.workers))
        try:
            futures = {pool.submit(self._run_one, fn, it): i for i, it in enumerate(items)}
            done = 0
            for fut in as_completed(futures):
                self.check()
                results[futures[fut]] = fut.result()
                done += 1
                self.progress(done, total, label)
        finally:
            pool.shutdown(wait=True, cancel_futures=True)
        return results


# ── Options et résultats ──────────────────────────────────────────────

@dataclass
class ScanOptions:
    folder_a: Path
    folder_b: Path | None = None
    protect_a: bool = False
    protect_b: bool = False
    recursive: bool = True
    media_only: bool = False
    skip_hidden: bool = True
    similar: bool = False
    tolerance: int = 3
    strategy: Strategy | str = Strategy.A_FIRST


@dataclass
class ScanStats:
    scanned: int = 0
    empty: int = 0
    symlinks: int = 0
    repeated: int = 0
    unreadable: int = 0
    uniform_skipped: int = 0
    skipped_buckets: int = 0
    exact_groups: int = 0
    similar_groups: int = 0
    grouped_files: int = 0
    reclaimable: int = 0

    def summary(self) -> str:
        return (f"{self.scanned} fichier(s) analysé(s), {self.exact_groups + self.similar_groups} groupe(s) "
                f"({self.exact_groups} identique(s), {self.similar_groups} similaire(s)), "
                f"gain libérable : {format_size(self.reclaimable)}")


@dataclass
class ScanResult:
    groups: list[Group] = field(default_factory=list)
    stats: ScanStats = field(default_factory=ScanStats)
    options: ScanOptions | None = None


# ── 1. Inventaire ─────────────────────────────────────────────────────

def build_inventory(options: ScanOptions, hooks: Hooks, stats: ScanStats) -> list[FileItem]:
    """Liste les fichiers de A puis de B (liens ignorés, vides ignorés, doublons de chemin réel ignorés)."""
    roots = [(SIDE_A, Path(options.folder_a), options.protect_a)]
    if options.folder_b is not None:
        roots.append((SIDE_B, Path(options.folder_b), options.protect_b))
    extensions = MEDIA_EXTS if options.media_only else None
    seen: set[str] = set()
    items: list[FileItem] = []
    for side, root, protected in roots:
        for path in iter_files(root, recursive=options.recursive, extensions=extensions,
                               skip_hidden=options.skip_hidden, follow_symlinks=False,
                               cancel=hooks.cancel):
            try:
                st = os.lstat(path)
            except OSError:
                stats.unreadable += 1
                continue
            if stat.S_ISLNK(st.st_mode):
                stats.symlinks += 1
                continue
            if not stat.S_ISREG(st.st_mode):
                continue
            if st.st_size == 0:
                stats.empty += 1
                continue
            real = os.path.normcase(os.path.realpath(path))
            if real in seen:
                stats.repeated += 1
                continue
            seen.add(real)
            items.append(FileItem(path=Path(path), side=side, root=root, size=st.st_size,
                                  mtime=st.st_mtime, protected=protected))
            stats.scanned += 1
        hooks.check()
    return items


# ── 2. Doublons exacts ────────────────────────────────────────────────

def quick_digest(path: Path, size: int) -> tuple[str, bool]:
    """Empreinte rapide. (hex, True) si elle couvre tout le fichier (≤ 128 Ko), sinon (hex, False)."""
    with open(path, "rb") as f:
        if size <= QUICK_LIMIT:
            h = hashlib.blake2b(f.read(QUICK_LIMIT), digest_size=32)
            return h.hexdigest(), True
        h = hashlib.blake2b(size.to_bytes(8, "little"), digest_size=16)
        h.update(f.read(SAMPLE))
        f.seek(max(0, size // 2 - SAMPLE // 2))
        h.update(f.read(SAMPLE))
        f.seek(max(0, size - SAMPLE))
        h.update(f.read(SAMPLE))
    return h.hexdigest(), False


def full_digest(path: Path, cancel: Callable[[], bool] = _never) -> str | None:
    """Empreinte complète (BLAKE2b 32 octets, blocs de 1 Mio) ; None si annulé en cours de lecture."""
    h = hashlib.blake2b(digest_size=32)
    with open(path, "rb") as f:
        while True:
            if cancel():
                return None
            block = f.read(BLOCK)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def _guarded(hooks: Hooks, fn: Callable[[FileItem], Any]) -> Callable[[FileItem], Any]:
    """Fichier illisible : WARN et None, sans jamais bloquer l'analyse."""
    def run(item: FileItem) -> Any:
        try:
            return fn(item)
        except OSError as exc:
            hooks.log("WARN", f"Fichier illisible, ignoré : {item.path} ({exc})")
            return None
    return run


def find_exact_groups(items: list[FileItem], hooks: Hooks, stats: ScanStats) -> list[list[FileItem]]:
    """Taille, puis empreinte rapide, puis empreinte complète pour les candidats restants."""
    by_size: dict[int, list[FileItem]] = {}
    for it in items:
        by_size.setdefault(it.size, []).append(it)
    candidates = [it for grp in by_size.values() if len(grp) > 1 for it in grp]
    if not candidates:
        return []

    hooks.status("Empreintes rapides…")
    quick = hooks.pmap(_guarded(hooks, lambda it: quick_digest(it.path, it.size)),
                       candidates, "Empreintes rapides")
    hooks.check()
    quick_groups: dict[tuple[int, str], list[FileItem]] = {}
    for it, res in zip(candidates, quick):
        if res is None:
            stats.unreadable += 1
            continue
        it.digest = res[0]
        quick_groups.setdefault((it.size, res[0]), []).append(it)

    exact: list[list[FileItem]] = []
    need_full: list[FileItem] = []
    for (size, _), members in quick_groups.items():
        if len(members) < 2:
            continue
        if size <= QUICK_LIMIT:
            exact.append(members)          # l'empreinte rapide couvre déjà tout le fichier
        else:
            need_full.extend(members)

    if need_full:
        hooks.status("Empreintes complètes…")
        full = hooks.pmap(_guarded(hooks, lambda it: full_digest(it.path, hooks.cancel)),
                          need_full, "Empreintes complètes")
        hooks.check()
        full_groups: dict[tuple[int, str], list[FileItem]] = {}
        for it, digest in zip(need_full, full):
            if digest is None:
                stats.unreadable += 1
                continue
            it.digest = digest
            full_groups.setdefault((it.size, digest), []).append(it)
        exact.extend(m for m in full_groups.values() if len(m) > 1)
    return exact


# ── 3. Images similaires ──────────────────────────────────────────────

def _oriented_size(im: Any) -> tuple[int, int]:
    width, height = im.size
    try:
        orientation = im.getexif().get(0x0112, 1)
    except Exception:  # noqa: BLE001
        orientation = 1
    return (height, width) if orientation in (5, 6, 7, 8) else (width, height)


def read_dimensions(path: Path) -> tuple[int, int]:
    """Dimensions d'une image, orientation EXIF appliquée."""
    from PIL import Image

    with Image.open(path) as im:
        return _oriented_size(im)


def dhash_file(path: Path) -> tuple[int, int, int]:
    """(dHash 64 bits, largeur, hauteur). Niveaux de gris, 9×8 LANCZOS, bit = gauche > droite."""
    from PIL import Image, ImageOps

    resample = getattr(Image, "Resampling", Image).LANCZOS
    with Image.open(path) as im:
        width, height = _oriented_size(im)
        if im.format == "JPEG":
            im.draft("L", (128, 128))
        small = ImageOps.exif_transpose(im).convert("L").resize((9, 8), resample)
    data = small.tobytes()
    bits = 0
    for row in range(8):
        base = row * 9
        for col in range(8):
            bits = (bits << 1) | (1 if data[base + col] > data[base + col + 1] else 0)
    return bits, width, height


def _hash_image(item: FileItem) -> tuple[int, int, int] | None:
    try:
        return dhash_file(item.path)
    except Exception as exc:  # noqa: BLE001 - Pillow lève des types variés
        raise OSError(str(exc)) from exc


def find_similar_groups(exact: list[list[FileItem]], items: list[FileItem], options: ScanOptions,
                        hooks: Hooks, stats: ScanStats) -> tuple[list[list[FileItem]], list[list[FileItem]]]:
    """Regroupe les images proches par dHash. Un groupe de doublons exacts d'images est représenté
    par un seul exemplaire et rejoint, avec tous ses membres, le groupe similaire qui l'accueille.
    Retourne (groupes exacts restants, groupes similaires)."""
    grouped = {id(i) for g in exact for i in g}
    units: list[list[FileItem]] = [g for g in exact if g[0].path.suffix.lower() in SIMILAR_EXTS]
    units += [[i] for i in items if id(i) not in grouped and i.path.suffix.lower() in SIMILAR_EXTS]
    passthrough = [g for g in exact if g[0].path.suffix.lower() not in SIMILAR_EXTS]
    if len(units) < 2:
        return exact, []

    hooks.status("Analyse visuelle…")
    res = hooks.pmap(_guarded(hooks, _hash_image), [u[0] for u in units], "Analyse visuelle")
    hooks.check()

    valid: list[tuple[list[FileItem], int]] = []
    unhashed: list[list[FileItem]] = []
    for unit, out in zip(units, res):
        if out is None:
            stats.unreadable += 1
            unhashed.append(unit)
            continue
        bits, width, height = out
        for member in unit:
            member.width, member.height = width, height
        if bits == 0 or bits == FULL64:
            stats.uniform_skipped += 1
            unhashed.append(unit)
            continue
        valid.append((unit, bits))

    n = len(valid)
    hashes = [h for _, h in valid]
    tol = max(0, min(int(options.tolerance), MAX_TOLERANCE))
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    buckets: dict[tuple[int, int], list[int]] = {}
    for idx, h in enumerate(hashes):
        for chunk in range(8):
            buckets.setdefault((chunk, (h >> (8 * chunk)) & 0xFF), []).append(idx)

    for members in buckets.values():
        if len(members) < 2:
            continue
        if len(members) > MAX_BUCKET:
            stats.skipped_buckets += 1
            hooks.log("WARN", f"Paquet de {len(members)} images quasi identiques ignoré (trop volumineux).")
            continue
        hooks.check()
        for a in range(len(members) - 1):
            ia = members[a]
            ha = hashes[ia]
            if a % 128 == 0:
                hooks.check()
            for b in range(a + 1, len(members)):
                ib = members[b]
                ra, rb = find(ia), find(ib)
                if ra != rb and (ha ^ hashes[ib]).bit_count() <= tol:
                    parent[rb] = ra

    comps: dict[int, list[int]] = {}
    for idx in range(n):
        comps.setdefault(find(idx), []).append(idx)

    similar: list[list[FileItem]] = []
    remaining = list(passthrough)
    remaining += [u for u in unhashed if len(u) > 1]
    for comp in comps.values():
        if len(comp) >= 2:
            similar.append([m for idx in comp for m in valid[idx][0]])
        elif len(valid[comp[0]][0]) > 1:
            remaining.append(valid[comp[0]][0])
    return remaining, similar


# ── 4. Assemblage ─────────────────────────────────────────────────────

def _sorted_items(items: list[FileItem]) -> list[FileItem]:
    return sorted(items, key=lambda i: (i.side, str(i.path).lower()))


def _fill_dimensions(groups: list[Group], hooks: Hooks) -> None:
    todo = [i for g in groups for i in g.items
            if i.width is None and i.path.suffix.lower() in IMAGE_EXTS]
    if not todo:
        return
    hooks.status("Lecture des dimensions…")
    dims = hooks.pmap(_guarded(hooks, lambda it: read_dimensions(it.path)), todo, "Dimensions")
    hooks.check()
    for it, wh in zip(todo, dims):
        if wh is not None:
            it.width, it.height = wh


def scan(options: ScanOptions, hooks: Hooks | None = None) -> ScanResult:
    """Analyse complète. Lève ScanCancelled si hooks.cancel() devient vrai ; ValueError si un dossier manque."""
    hooks = hooks or Hooks()
    if not Path(options.folder_a).is_dir():
        raise ValueError(f"Dossier A introuvable : {options.folder_a}")
    if options.folder_b is not None and not Path(options.folder_b).is_dir():
        raise ValueError(f"Dossier B introuvable : {options.folder_b}")
    stats = ScanStats()

    hooks.status("Inventaire des fichiers…")
    items = build_inventory(options, hooks, stats)
    hooks.log("INFO", f"Inventaire : {stats.scanned} fichier(s) retenu(s), {stats.empty} vide(s), "
                      f"{stats.symlinks} lien(s) et {stats.repeated} chemin(s) répété(s) ignoré(s).")

    exact = find_exact_groups(items, hooks, stats)
    similar: list[list[FileItem]] = []
    if options.similar:
        exact, similar = find_similar_groups(exact, items, options, hooks, stats)
        hooks.log("INFO", f"{stats.uniform_skipped} image(s) quasi unie(s) ignorée(s) pour la similarité.")

    groups = [Group(0, KIND_EXACT, _sorted_items(g)) for g in exact]
    groups += [Group(0, KIND_SIMILAR, _sorted_items(g)) for g in similar]
    _fill_dimensions(groups, hooks)
    strategy = strategy_from_key(options.strategy)
    for g in groups:
        resolve_group(g, strategy)
    groups.sort(key=lambda g: (-g.reclaimable, -g.potential_gain, str(g.items[0].path).lower()))
    for number, g in enumerate(groups, start=1):
        g.id = number

    files, _, gain = totals(groups)
    stats.exact_groups = sum(1 for g in groups if g.kind == KIND_EXACT)
    stats.similar_groups = len(groups) - stats.exact_groups
    stats.grouped_files, stats.reclaimable = files, gain
    if stats.unreadable:
        hooks.log("WARN", f"{stats.unreadable} fichier(s) illisible(s) ignoré(s).")
    hooks.log("OK", stats.summary())
    return ScanResult(groups=groups, stats=stats, options=options)
