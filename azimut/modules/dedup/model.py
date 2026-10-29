"""Modèle d'Azimut Dedup : fichiers, groupes, stratégies de proposition (sans Qt)."""
from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from azimut.modules.dedup.livephoto import is_video_name

SIDE_A = "A"
SIDE_B = "B"

KIND_EXACT = "identique"
KIND_SIMILAR = "similaire"
KIND_MODIFIED = "modifie"          # même chemin relatif dans A et B, contenu différent
KIND_LIVE = "live"                 # Live Photo : photo + courte vidéo de même nom

KIND_LABELS: dict[str, str] = {
    KIND_EXACT: "doublons exacts",
    KIND_SIMILAR: "images similaires",
    KIND_MODIFIED: "même chemin, contenu différent",
    KIND_LIVE: "Live Photo (photo + vidéo)",
}
# Groupes d'identification : le contenu diffère, donc rien n'est jamais proposé à la suppression d'office
IDENTIFY_KINDS = frozenset({KIND_MODIFIED, KIND_LIVE})

ACTION_KEEP = "Garder"
ACTION_DELETE = "Supprimer"


class Strategy(str, Enum):
    """Stratégies de proposition automatique."""

    A_FIRST = "a_first"
    B_FIRST = "b_first"
    OLDEST = "oldest"
    NEWEST = "newest"
    BEST_QUALITY = "best_quality"
    CLEANEST_NAME = "cleanest_name"
    SHORTEST_PATH = "shortest_path"


STRATEGY_LABELS: dict[Strategy, str] = {
    Strategy.A_FIRST: "A prioritaire",
    Strategy.B_FIRST: "B prioritaire",
    Strategy.OLDEST: "Le plus ancien",
    Strategy.NEWEST: "Le plus récent",
    Strategy.BEST_QUALITY: "Meilleure qualité (résolution, taille)",
    Strategy.CLEANEST_NAME: "Nom le plus propre (sans « copie », « (1) »)",
    Strategy.SHORTEST_PATH: "Chemin le plus court",
}


def strategy_from_key(key: str | Strategy, default: Strategy = Strategy.A_FIRST) -> Strategy:
    """Convertit une clé en stratégie ; `default` si la clé est inconnue."""
    try:
        return Strategy(key)
    except ValueError:
        return default


@dataclass(eq=False)
class FileItem:
    """Un fichier analysé (comparé par identité)."""

    path: Path
    side: str
    root: Path
    size: int
    mtime: float
    protected: bool = False
    width: int | None = None
    height: int | None = None
    digest: str = ""
    action: str = ACTION_KEEP
    duration: float | None = None      # secondes (vidéos des Live Photos)

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def pixels(self) -> int:
        return (self.width or 0) * (self.height or 0)

    @property
    def dimensions_text(self) -> str:
        return f"{self.width} × {self.height}" if self.width and self.height else ""

    @property
    def relative_path(self) -> Path:
        try:
            return self.path.relative_to(self.root)
        except ValueError:
            return Path(self.path.name)


@dataclass(eq=False)
class Group:
    """Un groupe de fichiers identiques ou similaires."""

    id: int
    kind: str
    items: list[FileItem] = field(default_factory=list)

    @property
    def total_size(self) -> int:
        return sum(i.size for i in self.items)

    @property
    def potential_gain(self) -> int:
        """Gain théorique : tout sauf le plus gros exemplaire."""
        return self.total_size - max((i.size for i in self.items), default=0)

    @property
    def reclaimable(self) -> int:
        """Taille des fichiers actuellement proposés à la suppression."""
        return sum(i.size for i in self.items if i.action == ACTION_DELETE)

    def kept(self) -> list[FileItem]:
        return [i for i in self.items if i.action == ACTION_KEEP]

    def to_delete(self) -> list[FileItem]:
        return [i for i in self.items if i.action == ACTION_DELETE]


def totals(groups: Iterable[Group]) -> tuple[int, int, int]:
    """(fichiers dans des groupes, nombre de groupes, gain libérable en octets)."""
    files = count = gain = 0
    for g in groups:
        count += 1
        files += len(g.items)
        gain += g.reclaimable
    return files, count, gain


# ── Classement des exemplaires ────────────────────────────────────────

_COPY_RE = re.compile(r"(?i)(?<![a-z])(?:copie|copy)(?![a-z])|\(\d+\)")


def _dirtiness(item: FileItem) -> int:
    return len(_COPY_RE.findall(item.path.stem))


def _quality_key(item: FileItem) -> tuple[int, int]:
    return (-item.pixels, -item.size)


def _strategy_key(item: FileItem, strategy: Strategy) -> tuple:
    if strategy is Strategy.A_FIRST:
        return (0 if item.side == SIDE_A else 1,)
    if strategy is Strategy.B_FIRST:
        return (0 if item.side == SIDE_B else 1,)
    if strategy is Strategy.OLDEST:
        return (item.mtime,)
    if strategy is Strategy.NEWEST:
        return (-item.mtime,)
    if strategy is Strategy.BEST_QUALITY:
        return _quality_key(item)
    if strategy is Strategy.CLEANEST_NAME:
        return (_dirtiness(item), len(item.path.stem))
    return (len(str(item.path)),)


def rank_key(item: FileItem, strategy: Strategy, kind: str) -> tuple:
    """Clé de tri : le plus petit est gardé. Pour les images similaires, la qualité prime."""
    key: tuple = ()
    if kind == KIND_SIMILAR and strategy is not Strategy.BEST_QUALITY:
        key += _quality_key(item)
    key += _strategy_key(item, strategy)
    path = str(item.path)
    return key + (0 if item.side == SIDE_A else 1, len(path), path.lower())


# ── Proposition automatique ───────────────────────────────────────────

def proposal(group: Group, strategy: Strategy | str, live_drop_video: bool = False) -> dict[FileItem, str]:
    """Action proposée pour chaque fichier, sans rien modifier.

    Doublons exacts / similaires : un fichier protégé est toujours gardé ; s'il y en a, le reste est proposé à la
    suppression ; sinon un seul exemplaire (le mieux classé) est gardé.
    « Même chemin, contenu différent » : tout est gardé (aucune suppression automatique).
    Live Photos : tout est gardé, sauf si ``live_drop_video`` : on propose alors d'écarter la vidéo (jamais la
    photo, jamais un fichier protégé).
    """
    items = group.items
    if not items:
        return {}
    if group.kind == KIND_MODIFIED:
        return {i: ACTION_KEEP for i in items}
    if group.kind == KIND_LIVE:
        return {i: (ACTION_DELETE if live_drop_video and not i.protected and is_video_name(i.name)
                    else ACTION_KEEP) for i in items}
    strat = strategy_from_key(strategy)
    if any(i.protected for i in items):
        return {i: (ACTION_KEEP if i.protected else ACTION_DELETE) for i in items}
    keeper = min(items, key=lambda i: rank_key(i, strat, group.kind))
    return {i: (ACTION_KEEP if i is keeper else ACTION_DELETE) for i in items}


def resolve_group(group: Group, strategy: Strategy | str, live_drop_video: bool = False) -> None:
    """Applique la proposition automatique à tout le groupe (au moins un exemplaire gardé)."""
    for item, action in proposal(group, strategy, live_drop_video).items():
        item.action = action


def resolve_selection(group: Group, selected: Iterable[FileItem], strategy: Strategy | str,
                      live_drop_video: bool = False) -> None:
    """Applique la proposition automatique aux seuls fichiers sélectionnés."""
    wanted = proposal(group, strategy, live_drop_video)
    for item in selected:
        if item in wanted:
            item.action = wanted[item]
    ensure_keeper(group, strategy)


def ensure_keeper(group: Group, strategy: Strategy | str = Strategy.A_FIRST) -> None:
    """Garantit au moins un exemplaire gardé (le mieux classé si besoin)."""
    if group.items and not group.kept():
        strat = strategy_from_key(strategy)
        min(group.items, key=lambda i: rank_key(i, strat, group.kind)).action = ACTION_KEEP


# ── Corrections manuelles ─────────────────────────────────────────────

def set_actions(group: Group, items: Iterable[FileItem], action: str) -> tuple[bool, str]:
    """Change l'action de `items`. Refuse (False, message) si un dossier protégé est touché
    ou si le groupe n'aurait plus aucun exemplaire gardé ; rien n'est modifié dans ce cas."""
    chosen = [i for i in items if i in group.items]
    if not chosen:
        return False, "Aucun fichier à modifier."
    if action == ACTION_DELETE:
        protected = [i for i in chosen if i.protected]
        if protected:
            return False, f"Refusé : « {protected[0].name} » est dans un dossier protégé."
        if not [i for i in group.items if i.action == ACTION_KEEP and i not in chosen]:
            return False, "Refusé : un groupe doit garder au moins un exemplaire."
    elif action != ACTION_KEEP:
        return False, f"Action inconnue : {action}"
    for item in chosen:
        item.action = action
    return True, ""


def keep_only(group: Group, item: FileItem) -> tuple[bool, str]:
    """Garde `item` et propose les autres à la suppression (sauf fichiers protégés, gardés)."""
    if item not in group.items:
        return False, "Fichier absent du groupe."
    item.action = ACTION_KEEP
    count = 0
    for other in group.items:
        if other is not item:
            other.action = ACTION_KEEP if other.protected else ACTION_DELETE
            count += 0 if other.protected else 1
    return True, f"« {item.name} » gardé ; {count} autre(s) proposé(s) à la suppression."
