"""Palette de commandes d'Atlas (Ctrl+K) : recherche instantanée d'un outil ou d'une action."""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout, QWidget


@dataclass
class PaletteEntry:
    """Une ligne de la palette."""

    id: str
    title: str
    subtitle: str = ""
    icon: str = ""
    keywords: tuple[str, ...] = ()
    enabled: bool = True
    callback: Callable[[], None] | None = field(default=None, repr=False)


def normalize(text: str) -> str:
    """Minuscules sans accents (« Réglages » ~ « reglages »)."""
    decomposed = unicodedata.normalize("NFD", text.casefold())
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn")


def score(entry: PaletteEntry, query: str) -> int:
    """0 = ne correspond pas ; plus grand = meilleur (titre > mots-clés > description)."""
    q = normalize(query).strip()
    if not q:
        return 1
    title = normalize(entry.title)
    words = q.split()
    total = 0
    for word in words:
        if title.startswith(word) or any(part.startswith(word) for part in title.split()):
            total += 100
        elif word in title:
            total += 70
        elif any(word in normalize(k) for k in entry.keywords):
            total += 50
        elif word in normalize(entry.subtitle):
            total += 30
        else:
            return 0                                   # tous les mots doivent correspondre
    return total - len(title) // 20                      # à égalité, le titre le plus court d'abord


def filter_entries(entries: list[PaletteEntry], query: str) -> list[PaletteEntry]:
    """Entrées correspondant à la requête, les meilleures d'abord (ordre d'origine si requête vide)."""
    if not normalize(query).strip():
        return list(entries)
    scored = [(score(e, query), -i, e) for i, e in enumerate(entries)]
    kept = [t for t in scored if t[0] > 0]
    kept.sort(key=lambda t: (t[0], t[1]), reverse=True)
    return [e for _s, _i, e in kept]


class CommandPalette(QDialog):
    """Fenêtre de recherche : flèches pour choisir, Entrée pour lancer, Échap pour fermer."""

    def __init__(self, entries: list[PaletteEntry], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._entries = list(entries)
        self.setWindowTitle("Rechercher un outil ou une action")
        self.setModal(True)
        self.setMinimumWidth(520)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(8)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText("Tapez un outil ou une action : convertir, doublons, thème…")
        self.edit.setClearButtonEnabled(True)
        self.edit.textChanged.connect(self._refill)
        self.edit.installEventFilter(self)
        self.list = QListWidget()
        self.list.setMinimumHeight(260)
        self.list.setUniformItemSizes(True)
        self.list.itemActivated.connect(lambda _i: self.run_current())
        self.list.itemClicked.connect(lambda _i: self.run_current())
        self.hint = QLabel("↑ ↓ pour choisir · Entrée pour lancer · Échap pour fermer")
        self.hint.setProperty("role", "muted")
        lay.addWidget(self.edit)
        lay.addWidget(self.list, 1)
        lay.addWidget(self.hint)
        self.executed: PaletteEntry | None = None
        self._refill("")
        self.edit.setFocus()

    # ── contenu ──
    def visible_entries(self) -> list[PaletteEntry]:
        return [self.list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.list.count())]

    def _refill(self, query: str) -> None:
        self.list.clear()
        for entry in filter_entries(self._entries, query):
            label = f"{entry.icon}  {entry.title}" if entry.icon else entry.title
            if entry.subtitle:
                label += f"\n      {entry.subtitle}"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, entry)
            if not entry.enabled:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled & ~Qt.ItemFlag.ItemIsSelectable)
            self.list.addItem(item)
        for i in range(self.list.count()):                 # sélection initiale : première entrée utilisable
            if self.list.item(i).flags() & Qt.ItemFlag.ItemIsEnabled:
                self.list.setCurrentRow(i)
                break

    # ── actions ──
    def run_current(self) -> None:
        item = self.list.currentItem()
        if item is None:
            return
        entry: PaletteEntry = item.data(Qt.ItemDataRole.UserRole)
        if not entry.enabled:
            return
        self.executed = entry
        self.accept()
        if entry.callback is not None:
            entry.callback()

    def _move(self, step: int) -> None:
        count = self.list.count()
        if not count:
            return
        row = self.list.currentRow()
        for _ in range(count):
            row = (row + step) % count
            if self.list.item(row).flags() & Qt.ItemFlag.ItemIsEnabled:
                self.list.setCurrentRow(row)
                return

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if obj is self.edit and isinstance(event, QKeyEvent) and event.type() == QKeyEvent.Type.KeyPress:
            key = event.key()
            if key in (Qt.Key.Key_Down, Qt.Key.Key_Up):
                self._move(1 if key == Qt.Key.Key_Down else -1)
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.run_current()
                return True
        return super().eventFilter(obj, event)
