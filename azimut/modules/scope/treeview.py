"""Comparaison d'arborescences d'Azimut Scope : un arbre unique, la mère et la fille côte à côte.

Un seul arbre plutôt que deux panneaux parallèles : chaque dossier occupe une ligne, avec l'effectif de la
mère, celui de la fille, l'écart et un état lisible d'un seul coup d'œil. Les lignes se colorient selon
l'état — vert pâle pour un dossier identique, ambre pour un effectif différent, rouge pour un dossier absent
d'un côté — et une pastille nomme l'état sans dépendre de la couleur seule.

Ergonomie : en-tête des deux sources (nom, totaux), compteurs cliquables d'un coup d'œil, filtre « différences »
(géré par la fenêtre), recherche par nom (Ctrl+F), boutons « Tout déplier / replier », navigation d'une différence
à l'autre (F7 / Maj+F7), tri naturel de l'arborescence, infobulles détaillées et navigation clavier native de QTreeView.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Iterator

from PySide6.QtCore import (
    QAbstractItemModel,
    QCoreApplication,
    QModelIndex,
    QRect,
    QRectF,
    QSize,
    QT_TRANSLATE_NOOP,
    Qt,
)
from PySide6.QtGui import QColor, QFont, QFontMetrics, QKeySequence, QPainter, QPen, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from azimut.modules.scope import engine as eng
from azimut.ui import icons
from azimut.ui.theme import ThemeManager, action_color, scaled, token
from azimut.ui.widgets import StatusChip, plain_tooltip

COL_NAME, COL_A, COL_B, COL_DELTA, COL_STATE = range(5)
STATE_ROLE = int(Qt.ItemDataRole.UserRole) + 1
FOLDER_ROLE = int(Qt.ItemDataRole.UserRole) + 2
DisplayRole = Qt.ItemDataRole.DisplayRole

_STATE_STYLE = {
    eng.STATE_EQUAL: ("ok_bg", "ok_txt", "check"),
    eng.STATE_DIFF: ("warn_bg", "warn_txt", "alert-triangle"),
    eng.STATE_ONLY_A: ("err_bg", "err_txt", "cross"),
    eng.STATE_ONLY_B: ("err_bg", "err_txt", "cross"),
}
# Libellés marqués pour l'extraction ; la traduction se fait à l'affichage, une fois la langue
# installée (un appel translate() au chargement du module figerait la langue de l'import).
_STATE_HINT = {
    eng.STATE_EQUAL: QT_TRANSLATE_NOOP("ScopeTree", "Les deux côtés contiennent le même nombre de fichiers."),
    eng.STATE_DIFF: QT_TRANSLATE_NOOP("ScopeTree", "Le nombre de fichiers diffère entre la mère et la fille."),
    eng.STATE_ONLY_A: QT_TRANSLATE_NOOP("ScopeTree", "Ce dossier existe dans la mère, mais pas dans la fille."),
    eng.STATE_ONLY_B: QT_TRANSLATE_NOOP("ScopeTree", "Ce dossier existe dans la fille, mais pas dans la mère."),
}
_STATE_TEXT = {
    eng.STATE_EQUAL: QT_TRANSLATE_NOOP("ScopeTree", "Identique"),
    eng.STATE_DIFF: QT_TRANSLATE_NOOP("ScopeTree", "Différent"),
    eng.STATE_ONLY_A: QT_TRANSLATE_NOOP("ScopeTree", "Absent de la fille"),
    eng.STATE_ONLY_B: QT_TRANSLATE_NOOP("ScopeTree", "Absent de la mère"),
}


def _num(value: int) -> str:
    """Nombre lisible : espace fine insécable comme séparateur de milliers."""
    return f"{value:,}".replace(",", "\u202f")


def _fold(text: str) -> str:
    """Minuscules sans accents, pour comparer et trier les noms de dossiers."""
    base = unicodedata.normalize("NFKD", text)
    return "".join(char for char in base if not unicodedata.combining(char)).lower()


def _sort_key(name: str) -> tuple:
    """Tri naturel et insensible aux accents : « Été » avant « Hiver », « Jour 2 » avant « Jour 10 »."""
    return tuple((1, int(part)) if part.isdigit() else (0, part)
                 for part in re.split(r"(\d+)", _fold(name)) if part)


def _plural(n: int, singular: str, plural: str) -> str:
    return f"{_num(n)} {singular if n <= 1 else plural}"


def _folders(n: int) -> str:
    return _plural(n, QCoreApplication.translate("ScopeTree", "sous-dossier"), QCoreApplication.translate("ScopeTree", "sous-dossiers"))


def _files(n: int) -> str:
    return _plural(n, QCoreApplication.translate("ScopeTree", "fichier"), QCoreApplication.translate("ScopeTree", "fichiers"))


def _delta(value: int) -> str:
    if value == 0:
        return "0"
    return ("+" if value > 0 else "−") + _num(abs(value))


def _mix(first: QColor, second: QColor, weight: float) -> QColor:
    """Mélange deux couleurs (poids 0 → first, 1 → second)."""
    w = max(0.0, min(1.0, weight))
    return QColor(
        round(first.red() * (1 - w) + second.red() * w),
        round(first.green() * (1 - w) + second.green() * w),
        round(first.blue() * (1 - w) + second.blue() * w),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Données de l'arbre
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class _Node:
    folder: str
    name: str
    row: eng.Row
    depth: int
    parent: "_Node | None" = None
    position: int = 0
    children: list["_Node"] = field(default_factory=list)


def _build_nodes(cmp: eng.Comparison, only_differences: bool) -> list[_Node]:
    """Reconstruit la hiérarchie à partir des lignes d'engine.tree_rows (ordre profondeur d'abord)."""
    roots: list[_Node] = []
    stack: list[_Node] = []
    for tree_row in eng.tree_rows(cmp, only_differences):
        node = _Node(tree_row.folder, tree_row.name, tree_row.row, tree_row.depth)
        while stack and stack[-1].depth >= tree_row.depth:
            stack.pop()
        if stack:
            node.parent = stack[-1]
            node.position = len(stack[-1].children)
            stack[-1].children.append(node)
        else:
            node.position = len(roots)
            roots.append(node)
        stack.append(node)
    _sort_children(roots)
    return roots


def _sort_children(nodes: list[_Node]) -> None:
    for node in nodes:
        node.children.sort(key=lambda child: _sort_key(child.name))
        for position, child in enumerate(node.children):
            child.position = position
        _sort_children(node.children)


def _count_matches(nodes: list[_Node], needle: str) -> int:
    """Compte les noms qui correspondent dans un sous-arbre déjà conservé."""
    total = 0
    for node in nodes:
        if node.folder != "." and needle in _fold(node.name):
            total += 1
        total += _count_matches(node.children, needle)
    return total


def _filter_nodes(nodes: list[_Node], needle: str) -> tuple[list[_Node], int]:
    """Garde les dossiers trouvés, leurs ancêtres (contexte) et tout leur sous-arbre.

    Renvoie aussi le nombre de dossiers dont le nom correspond, sans compter les ancêtres.
    """
    kept: list[_Node] = []
    matches = 0
    for node in nodes:
        if node.folder != "." and needle in _fold(node.name):
            matches += 1 + _count_matches(node.children, needle)
            kept.append(node)                        # sous-arbre conservé tel quel
            continue
        children, child_matches = _filter_nodes(node.children, needle)
        matches += child_matches
        if children:
            node.children = children
            kept.append(node)                        # ancêtre d'un dossier trouvé
    return kept, matches


def _renumber(nodes: list[_Node]) -> None:
    """Réattribue les positions après un filtrage : le modèle s'appuie sur elles."""
    for position, node in enumerate(nodes):
        node.position = position
        _renumber(node.children)


def _walk(nodes: list[_Node]) -> Iterator[_Node]:
    for node in nodes:
        yield node
        yield from _walk(node.children)


class _CompareModel(QAbstractItemModel):
    """Modèle de l'arbre comparatif : une ligne par dossier, cinq colonnes alignées."""

    HEADERS = (
        QT_TRANSLATE_NOOP("ScopeTree", "Dossier"),
        QT_TRANSLATE_NOOP("ScopeTree", "Mère"),
        QT_TRANSLATE_NOOP("ScopeTree", "Fille"),
        QT_TRANSLATE_NOOP("ScopeTree", "Écart"),
        QT_TRANSLATE_NOOP("ScopeTree", "État"),
    )

    def __init__(self, roots: list[_Node], cmp: eng.Comparison | None, parent=None) -> None:
        super().__init__(parent)
        self._roots = roots
        self._cmp = cmp
        self._flat = list(_walk(roots))
        self._by_folder = {node.folder: node for node in self._flat}

    # ── API ──────────────────────────────────────────────────────────────────
    def nodes(self) -> list[_Node]:
        return self._flat

    def node_index(self, node: _Node) -> QModelIndex:
        return self.createIndex(node.position, COL_NAME, node)

    def folder_index(self, folder: str) -> QModelIndex:
        node = self._by_folder.get(folder)
        return self.node_index(node) if node is not None else QModelIndex()

    # ── QAbstractItemModel ───────────────────────────────────────────────────
    def index(self, row: int, column: int, parent: QModelIndex = QModelIndex()) -> QModelIndex:
        if not self.hasIndex(row, column, parent):
            return QModelIndex()
        parent_node = parent.internalPointer() if parent.isValid() else None
        children = parent_node.children if parent_node is not None else self._roots
        return self.createIndex(row, column, children[row])

    def parent(self, index: QModelIndex = QModelIndex()) -> QModelIndex:  # type: ignore[override]
        if not index.isValid():
            return QModelIndex()
        node = index.internalPointer()
        if node is None or node.parent is None:
            return QModelIndex()
        return self.createIndex(node.parent.position, COL_NAME, node.parent)

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        if parent.column() > 0:
            return 0
        node = parent.internalPointer() if parent.isValid() else None
        return len(node.children if node is not None else self._roots)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() and parent.column() > 0 else len(self.HEADERS)

    def headerData(self, section: int, orientation: Qt.Orientation,  # noqa: N802
                   role: int = Qt.ItemDataRole.DisplayRole):
        if orientation != Qt.Orientation.Horizontal or not 0 <= section < len(self.HEADERS):
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            return QCoreApplication.translate("ScopeTree", self.HEADERS[section])
        if role == Qt.ItemDataRole.ToolTipRole:
            return (
                QCoreApplication.translate("ScopeTree", "Dossier et effectif du dossier lui-même (hors sous-dossiers)."),
                QCoreApplication.translate("ScopeTree", "Fichiers valides dans la mère, pour ce dossier."),
                QCoreApplication.translate("ScopeTree", "Fichiers valides dans la fille, pour ce dossier."),
                QCoreApplication.translate("ScopeTree", "Fille − mère : nombre de fichiers manquants (négatif) ou en plus (positif)."),
                QCoreApplication.translate("ScopeTree", "Identique, différent, ou présent d'un seul côté."),
            )[section]
        if role == Qt.ItemDataRole.TextAlignmentRole:
            if section in (COL_A, COL_B, COL_DELTA):
                return Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            return Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        return None

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):  # noqa: C901
        if not index.isValid():
            return None
        node: _Node = index.internalPointer()
        row, col = node.row, index.column()

        if role == DisplayRole:
            if col == COL_NAME:
                return QCoreApplication.translate("ScopeTree", "Racine") if node.folder == "." else node.name
            if col == COL_A:
                return "—" if row.count_a is None else _num(row.count_a)
            if col == COL_B:
                return "—" if row.count_b is None else _num(row.count_b)
            if col == COL_DELTA:
                return _delta(row.delta)
            return row.state

        if role == STATE_ROLE:
            return row.state
        if role == FOLDER_ROLE:
            return node.folder

        if role == Qt.ItemDataRole.ForegroundRole:
            if col == COL_NAME:
                return QColor(token("err_txt" if row.state in (eng.STATE_ONLY_A, eng.STATE_ONLY_B) else "text"))
            if col == COL_STATE:
                return None                                  # peint par la pastille
            if col in (COL_A, COL_B):
                value = row.count_a if col == COL_A else row.count_b
                if value is None:
                    return QColor(token("err_txt"))
                if row.state == eng.STATE_DIFF:
                    return QColor(token("warn_txt"))
                return QColor(token("text"))
            if row.state == eng.STATE_EQUAL:
                return QColor(token("muted"))
            if row.state == eng.STATE_DIFF:
                return QColor(token("warn_txt"))
            return QColor(token("err_txt"))

        if role == Qt.ItemDataRole.FontRole:
            if (col == COL_NAME and node.folder == ".") or (col == COL_DELTA and row.delta != 0):
                font = QFont()
                font.setBold(True)
                return font
            return None

        if role == Qt.ItemDataRole.TextAlignmentRole:
            if col in (COL_A, COL_B, COL_DELTA):
                return Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            return Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter

        if role == Qt.ItemDataRole.ToolTipRole:
            return self._tooltip(node, col)
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    # ── Interne ──────────────────────────────────────────────────────────────
    def _tooltip(self, node: _Node, column: int) -> str:
        if self._cmp is None:
            return ""
        row = node.row
        if node.folder == ".":
            path_a = str(self._cmp.a.root)
            path_b = str(self._cmp.b.root)
        else:
            path_a = str(self._cmp.a.root / node.folder)
            path_b = str(self._cmp.b.root / node.folder)
        side_a = (f"{path_a}\n{_files(row.count_a)}" if row.count_a is not None
                  else QCoreApplication.translate("ScopeTree", "{path}\nDossier absent").format(path=path_a))
        side_b = (f"{path_b}\n{_files(row.count_b)}" if row.count_b is not None
                  else QCoreApplication.translate("ScopeTree", "{path}\nDossier absent").format(path=path_b))
        if column == COL_A:
            return QCoreApplication.translate("ScopeTree", "MÈRE : {details}").format(details=side_a)
        if column == COL_B:
            return QCoreApplication.translate("ScopeTree", "FILLE : {details}").format(details=side_b)
        state = QCoreApplication.translate("ScopeTree", _STATE_TEXT.get(row.state, row.state))
        if column == COL_DELTA:
            return QCoreApplication.translate("ScopeTree", "Écart (fille − mère) : {delta} fichier(s)\n{state}").format(
                delta=f"{row.delta:+d}", state=state)
        hint = QCoreApplication.translate("ScopeTree", _STATE_HINT.get(row.state, ""))
        return (QCoreApplication.translate("ScopeTree", "MÈRE : {a}\n\nFILLE : {b}\n\n{state} — {hint}")
                .format(a=side_a, b=side_b, state=state, hint=hint))


# ─────────────────────────────────────────────────────────────────────────────
# Rendu des lignes et des pastilles
# ─────────────────────────────────────────────────────────────────────────────
class _RowDelegate(QStyledItemDelegate):
    """Peint la pastille d'état et laisse le style dessiner les autres cellules."""

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        if index.column() == COL_STATE:
            self._paint_pill(painter, option, index)
            return
        text = index.data(DisplayRole)
        if text is None or str(text) == "":
            return
        # Texte peint directement : le style grise les lignes sélectionnées, ce qui effacerait
        # les couleurs d'état (rouge, ambre) et nuirait à la lecture de la comparaison.
        font = index.data(Qt.ItemDataRole.FontRole) or option.font
        if self._selected(index):
            color = QColor("#ffffff")                     # sélection : fond plein, texte lisible
        else:
            color = index.data(Qt.ItemDataRole.ForegroundRole) or QColor(token("text"))
        alignment = index.data(Qt.ItemDataRole.TextAlignmentRole) or (
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        rect = option.rect.adjusted(scaled(6), 0, -scaled(8), 0)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        painter.setFont(font)
        painter.setPen(QColor(color))
        metrics = QFontMetrics(font)
        shown = metrics.elidedText(str(text), Qt.TextElideMode.ElideMiddle, max(1, rect.width()))
        painter.drawText(rect, int(alignment), shown)
        painter.restore()

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:  # noqa: N802
        metrics = option.fontMetrics
        height = max(scaled(30), metrics.height() + scaled(12))
        return QSize(metrics.horizontalAdvance(QCoreApplication.translate("ScopeTree", "Absent de la fille")) + scaled(60), height)

    def _selected(self, index: QModelIndex) -> bool:
        """Vrai pour la ligne courante (la vue ne sélectionne pas : elle peint la ligne courante)."""
        view = self.parent()
        current = view.currentIndex() if view is not None else QModelIndex()
        return bool(current.isValid() and index.siblingAtColumn(COL_NAME) == current)

    def _paint_pill(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        state = str(index.data(STATE_ROLE) or eng.STATE_EQUAL)
        text = str(index.data(DisplayRole) or "")
        bg_token, fg_token, icon_name = _STATE_STYLE.get(state, _STATE_STYLE[eng.STATE_EQUAL])
        bg, fg = QColor(token(bg_token)), QColor(token(fg_token))
        metrics = option.fontMetrics
        font = QFont(option.font)
        font.setBold(True)
        bold_metrics = QFontMetrics(font)

        inner = option.rect.adjusted(scaled(8), scaled(4), -scaled(8), -scaled(4))
        icon_size = max(scaled(12), metrics.height() - scaled(3))
        pad = scaled(8)
        gap = scaled(5)
        text_width = bold_metrics.horizontalAdvance(text)
        # 3 px de marge : éviter que l'arrondi de QFontMetrics ne tronque un texte qui tient tout juste.
        width = min(inner.width(), text_width + icon_size + gap + 2 * pad + scaled(3))
        height = min(inner.height(), max(scaled(21), metrics.height() + scaled(4)))
        pill = QRectF(inner.left(), inner.center().y() - height / 2 + 0.5, width, height)

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        border = QColor(fg)
        border.setAlpha(110)
        painter.setPen(QPen(border, 1.0))
        painter.setBrush(bg)
        painter.drawRoundedRect(pill, height / 2, height / 2)

        pixmap = icons.pixmap(icon_name, icon_size, fg_token)
        if not pixmap.isNull():
            painter.drawPixmap(int(pill.left() + pad), int(pill.center().y() - icon_size / 2), pixmap)
        text_left = pill.left() + pad + (icon_size + gap if not pixmap.isNull() else 0)
        available = pill.right() - pad - text_left
        shown = text if text_width <= available else bold_metrics.elidedText(
            text, Qt.TextElideMode.ElideRight, int(max(1.0, available)))
        painter.setFont(font)
        painter.setPen(fg)
        painter.drawText(QRectF(text_left, pill.top(), max(1.0, available), pill.height()),
                         int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), shown)
        painter.restore()


class _CompareView(QTreeView):
    """Arbre comparatif : fonds de ligne peints selon l'état, colonnes alignées, navigation native."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ScopeTree")
        self.setUniformRowHeights(True)
        self.setRootIsDecorated(True)
        self.setIndentation(scaled(18))
        self.setAllColumnsShowFocus(True)
        # La ligne courante est peinte par la vue : pas de sélection Qt, dont le fond et le texte
        # recouvriraient les couleurs d'état. Le clavier et le clic déplacent l'index courant.
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setExpandsOnDoubleClick(True)
        self.setMouseTracking(True)
        self.setAlternatingRowColors(False)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.setItemDelegate(_RowDelegate(self))
        header = self.header()
        header.setSectionsClickable(False)
        header.setHighlightSections(False)
        header.setStretchLastSection(False)
        header.setMinimumSectionSize(scaled(56))
        self._hover = ""
        self.apply_columns()
        ThemeManager.instance().changed.connect(self._on_theme_changed)

    # ── Mise en page ─────────────────────────────────────────────────────────
    def apply_columns(self) -> None:
        if self.model() is None:
            return
        header = self.header()
        metrics = self.fontMetrics()
        header.setSectionResizeMode(COL_NAME, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COL_A, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(COL_B, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(COL_DELTA, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(COL_STATE, QHeaderView.ResizeMode.Fixed)
        count_width = max(scaled(68), metrics.horizontalAdvance("888 888") + scaled(26))
        delta_width = max(scaled(64), metrics.horizontalAdvance("−888") + scaled(26))
        bold = QFont(self.font())
        bold.setBold(True)
        state_width = max(scaled(150), QFontMetrics(bold).horizontalAdvance(QCoreApplication.translate("ScopeTree", "Absent de la fille")) + scaled(64))
        self.setColumnWidth(COL_A, count_width)
        self.setColumnWidth(COL_B, count_width)
        self.setColumnWidth(COL_DELTA, delta_width)
        self.setColumnWidth(COL_STATE, state_width)
        self.setIndentation(scaled(18))

    # ── Peinture des lignes ──────────────────────────────────────────────────
    def drawRow(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:  # noqa: N802
        state = str(index.data(STATE_ROLE) or eng.STATE_EQUAL)
        folder = str(index.data(FOLDER_ROLE) or "")
        current = self.currentIndex()
        selected = bool(current.isValid() and current == index)
        base = QColor(token("surface_alt"))
        if state == eng.STATE_DIFF:
            base = _mix(base, QColor(token("warn_bg")), 0.72)
        elif state in (eng.STATE_ONLY_A, eng.STATE_ONLY_B):
            base = _mix(base, QColor(token("err_bg")), 0.72)
        if folder == self._hover and not selected:
            base = _mix(base, QColor(token("text")), 0.06)

        painter.fillRect(option.rect, QColor(action_color()) if selected else base)
        if state != eng.STATE_EQUAL:
            color = token("warn_txt" if state == eng.STATE_DIFF else "err_txt")
            painter.fillRect(QRect(option.rect.left(), option.rect.top(), max(3, scaled(3)),
                                   option.rect.height()), QColor(color))
        super().drawRow(painter, option, index)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        index = self.indexAt(event.position().toPoint())
        if index.isValid():
            self.setCurrentIndex(index)
        super().mousePressEvent(event)

    # ── Survol ───────────────────────────────────────────────────────────────
    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        index = self.indexAt(event.position().toPoint())
        folder = str(index.data(FOLDER_ROLE) or "") if index.isValid() else ""
        if folder != self._hover:
            self._hover = folder
            self.viewport().update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        if self._hover:
            self._hover = ""
            self.viewport().update()
        super().leaveEvent(event)

    # ── Thème ────────────────────────────────────────────────────────────────
    def _on_theme_changed(self, _theme: str = "") -> None:
        self.apply_columns()
        self.doItemsLayout()
        self.viewport().update()


# ─────────────────────────────────────────────────────────────────────────────
# En-tête des deux sources et état vide
# ─────────────────────────────────────────────────────────────────────────────
class _ElidedLabel(QLabel):
    """Libellé sur une ligne, élidé au milieu (les fins de noms restent lisibles)."""

    def __init__(self, mode: Qt.TextElideMode = Qt.TextElideMode.ElideMiddle,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self._mode = mode
        self._full_text = ""
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

    def set_full_text(self, text: str) -> None:
        self._full_text = text
        self.setToolTip(plain_tooltip(text))
        self._refresh()

    def full_text(self) -> str:
        return self._full_text

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._refresh()

    def _refresh(self) -> None:
        width = max(20, self.contentsRect().width())
        text = self.fontMetrics().elidedText(self._full_text or "—", self._mode, width)
        if text != super().text():
            super().setText(text)


class _SourcePanel(QWidget):
    """Une source : pictogramme, rôle (mère / fille), nom du dossier et totaux."""

    def __init__(self, title: str, icon_color: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._icon_color = icon_color
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        self._icon = QLabel()
        self._icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._icon, 0, Qt.AlignmentFlag.AlignTop)
        texts = QVBoxLayout()
        texts.setContentsMargins(0, 0, 0, 0)
        texts.setSpacing(1)
        heading = QHBoxLayout()
        heading.setContentsMargins(0, 0, 0, 0)
        heading.setSpacing(6)
        self.title = QLabel(title)
        self.title.setObjectName("SourceTitle")
        self.name = _ElidedLabel()
        self.name.setObjectName("SourceName")
        self.name.set_full_text("—")
        heading.addWidget(self.title)
        heading.addWidget(self.name, 1)
        self.totals = QLabel("—")
        self.totals.setProperty("role", "muted")
        texts.addLayout(heading)
        texts.addWidget(self.totals)
        lay.addLayout(texts, 1)
        self.refresh_theme()

    def refresh_theme(self, _theme: str = "") -> None:
        size = scaled(24)
        self._icon.setFixedSize(size, size)
        pixmap = icons.pixmap("folder", scaled(21), self._icon_color)
        self._icon.setPixmap(pixmap)

    def set_structure(self, structure: eng.Structure | None) -> None:
        if structure is None:
            self.name.set_full_text("—")
            self.totals.setText("—")
            self.totals.setToolTip("")
            return
        self.name.set_full_text(structure.root.name or str(structure.root))
        self.name.setToolTip(plain_tooltip(str(structure.root)))
        totals = f"{_folders(structure.folders)} · {_files(structure.files)}"
        self.totals.setText(totals)
        self.totals.setToolTip(totals)


class _EmptyState(QFrame):
    """État vide de l'arbre : comparer pour afficher, ou aucune différence trouvée."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ScopeEmpty")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 18)
        lay.setSpacing(8)
        self.icon = QLabel()
        self.icon.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignLeft)
        self.title = QLabel()
        self.title.setTextFormat(Qt.TextFormat.PlainText)
        self.title.setProperty("role", "title")
        self.title.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.detail = QLabel()
        self.detail.setTextFormat(Qt.TextFormat.PlainText)
        self.detail.setProperty("role", "muted")
        self.detail.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.detail.setWordWrap(True)
        self.detail.setFixedWidth(scaled(560))
        lay.addWidget(self.title, 0, Qt.AlignmentFlag.AlignLeft)
        lay.addWidget(self.detail, 0, Qt.AlignmentFlag.AlignLeft)
        lay.addStretch(1)
        self.refresh_theme()

    def refresh_theme(self, _theme: str = "") -> None:
        self.icon.setPixmap(icons.pixmap("search", scaled(28), "muted"))

    def set_message(self, title: str, detail: str) -> None:
        self.title.setText(title)
        self.detail.setText(detail)
        # QLabel centré avec un texte enroulé : sans hauteur explicite, la dernière ligne est rognée.
        self.detail.setMinimumHeight(self.detail.heightForWidth(self.detail.width() or scaled(560)))


# ─────────────────────────────────────────────────────────────────────────────
# Panneau complet
# ─────────────────────────────────────────────────────────────────────────────
class ComparisonTree(QWidget):
    """Mère et fille dans un seul arbre : effectifs alignés, couleurs d'état, navigation entre écarts."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._cmp: eng.Comparison | None = None
        self._model: _CompareModel | None = None
        self._only_differences = False
        self._search_text = ""
        self._search_folded = ""
        self._search_matches = 0
        self._search_no_match = False
        self._expanded_before_search: set[str] | None = None
        self._current_before_search = ""

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(10)

        # ── En-tête des deux sources ──
        self.sources = QFrame()
        self.sources.setObjectName("ScopeSources")
        sources_lay = QHBoxLayout(self.sources)
        sources_lay.setContentsMargins(14, 10, 14, 10)
        sources_lay.setSpacing(12)
        self.mother = _SourcePanel(QCoreApplication.translate("ScopeTree", "Mère · référence"), "muted")
        self.daughter = _SourcePanel(QCoreApplication.translate("ScopeTree", "Fille · copie"), "muted")
        self._arrow = QLabel()
        self._arrow.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sources_lay.addWidget(self.mother, 1)
        sources_lay.addWidget(self._arrow)
        sources_lay.addWidget(self.daughter, 1)
        outer.addWidget(self.sources)

        # ── Bandeau des états et des outils ──
        self.stats = QWidget()
        stats_lay = QHBoxLayout(self.stats)
        stats_lay.setContentsMargins(2, 0, 2, 0)
        stats_lay.setSpacing(6)
        self.chip_equal = StatusChip("", "neutre")
        self.chip_diff = StatusChip("", "neutre")
        self.chip_absent = StatusChip("", "neutre")
        self.chip_equal.setToolTip(QCoreApplication.translate("ScopeTree", "Dossiers présents des deux côtés avec le même nombre de fichiers."))
        self.chip_diff.setToolTip(QCoreApplication.translate("ScopeTree", "Dossiers présents des deux côtés, mais avec un effectif différent."))
        self.chip_absent.setToolTip(QCoreApplication.translate("ScopeTree", "Dossiers présents dans un seul des deux dossiers."))
        for chip in (self.chip_equal, self.chip_diff, self.chip_absent):
            stats_lay.addWidget(chip)
        stats_lay.addStretch(1)
        self.btn_previous = self._nav_button("arrow-left", QCoreApplication.translate("ScopeTree", "Différence précédente (Maj+F7)"),
                                             self.select_previous_difference)
        self.btn_next = self._nav_button("arrow-right", QCoreApplication.translate("ScopeTree", "Différence suivante (F7)"),
                                         self.select_next_difference)
        stats_lay.addWidget(self.btn_previous)
        stats_lay.addWidget(self.btn_next)
        stats_lay.addSpacing(6)
        self.btn_expand = QPushButton(QCoreApplication.translate("ScopeTree", "Tout déplier"))
        self.btn_expand.setProperty("kind", "secondary")
        self.btn_expand.setToolTip(QCoreApplication.translate("ScopeTree", "Déplier tous les dossiers de l'arbre."))
        self.btn_expand.clicked.connect(self.expand_all)
        self.btn_collapse = QPushButton(QCoreApplication.translate("ScopeTree", "Tout replier"))
        self.btn_collapse.setProperty("kind", "secondary")
        self.btn_collapse.setToolTip(QCoreApplication.translate("ScopeTree", "Replier tous les dossiers : seules les racines restent visibles."))
        self.btn_collapse.clicked.connect(self.collapse_all)
        stats_lay.addWidget(self.btn_expand)
        stats_lay.addWidget(self.btn_collapse)
        outer.addWidget(self.stats)

        # ── Arbre ou état vide ──
        self.view = _CompareView()
        self.empty = _EmptyState()
        self.stack = QStackedWidget()
        self.stack.addWidget(self.view)
        self.stack.addWidget(self.empty)
        outer.addWidget(self.stack, 1)
        self.setFocusProxy(self.view)

        self._shortcuts = []
        for sequence, slot in (("F7", self.select_next_difference), ("Shift+F7", self.select_previous_difference)):
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(slot)
            self._shortcuts.append(shortcut)

        ThemeManager.instance().changed.connect(self._on_theme_changed)
        self._refresh_icons()
        self._set_empty_message()
        self.sources.setVisible(False)
        self.stats.setVisible(False)
        self.stack.setCurrentWidget(self.empty)

    # ── Construction d'outils ────────────────────────────────────────────────
    def _nav_button(self, icon_name: str, tooltip: str, slot) -> QPushButton:
        button = QPushButton()
        button.setProperty("kind", "secondary")
        button.setToolTip(tooltip)
        button.setAccessibleName(tooltip)
        button.setIconSize(QSize(scaled(16), scaled(16)))
        button.clicked.connect(slot)
        button.setProperty("iconName", icon_name)
        return button

    def _refresh_icons(self) -> None:
        for button in (self.btn_previous, self.btn_next):
            name = str(button.property("iconName") or "")
            button.setIcon(icons.icon(name, "text", scaled(16)))
        self._arrow.setPixmap(icons.pixmap("arrow-right", scaled(18), "muted"))

    def _on_theme_changed(self, _theme: str = "") -> None:
        self._refresh_icons()
        for widget in (self.mother, self.daughter, self.empty):
            widget.refresh_theme()
        self.empty.detail.setFixedWidth(scaled(560))
        self.empty.detail.setMinimumHeight(
            self.empty.detail.heightForWidth(self.empty.detail.width() or scaled(560)))

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._sync_compact()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.view.apply_columns()

    def _sync_compact(self) -> None:
        """Fenêtre basse : l'en-tête des sources est un rappel, l'arbre passe en premier."""
        if self._cmp is not None:
            self.sources.setVisible(self.height() >= scaled(270))

    # ── API ──────────────────────────────────────────────────────────────────
    def set_comparison(self, cmp: eng.Comparison | None, only_differences: bool = False) -> None:
        expanded = self._expanded_folders()
        current = self._current_folder()
        had_model = self._model is not None

        self._cmp = cmp
        self._only_differences = bool(only_differences)
        self.sources.setVisible(cmp is not None)
        self.stats.setVisible(cmp is not None)
        self._sync_compact()
        self.mother.set_structure(cmp.a if cmp is not None else None)
        self.daughter.set_structure(cmp.b if cmp is not None else None)
        self._rebuild(expanded, current, had_model)

    def _rebuild(self, expanded: set[str] | None = None, current: str = "",
                 had_model: bool = False, restore_exact: bool = False) -> None:
        """Reconstruit l'arbre depuis la comparaison et la recherche courante."""
        if self._cmp is None:
            self._model = _CompareModel([], None, self)
            self.view.setModel(self._model)
            self.view.apply_columns()
            self._search_matches, self._search_no_match = 0, False
            self._set_empty_message()
            self.stack.setCurrentWidget(self.empty)
            self._update_stats()
            return

        all_roots = _build_nodes(self._cmp, self._only_differences)
        if self._search_folded:
            roots, matches = _filter_nodes(all_roots, self._search_folded)
            _renumber(roots)
        else:
            roots, matches = all_roots, 0
        self._search_matches = matches
        self._search_no_match = bool(self._search_folded and all_roots and not roots)
        self._model = _CompareModel(roots, self._cmp, self)
        self.view.setModel(self._model)
        self.view.apply_columns()
        self._update_stats()

        if not roots:
            if self._search_no_match:
                self._set_empty_message(search_no_match=True)
            else:
                self._set_empty_message(nothing=bool(self._only_differences and not all_roots))
            self.stack.setCurrentWidget(self.empty)
            return

        self.stack.setCurrentWidget(self.view)
        if self._search_folded:
            self.view.expandAll()                    # montrer les résultats et leur contexte
        elif restore_exact and expanded is not None:
            for node in self._model.nodes():
                self.view.setExpanded(self._model.node_index(node), node.folder in expanded)
        elif self._only_differences or not had_model or not expanded:
            self.view.expandAll()
        else:
            for node in self._model.nodes():
                if node.folder in expanded:
                    self.view.setExpanded(self._model.node_index(node), True)
        if current:
            index = self._model.folder_index(current)
            if index.isValid():
                self.view.setCurrentIndex(index)
                self.view.scrollTo(index)

    def set_search(self, text: str) -> None:
        """Filtre les dossiers par nom ; le contexte et le sous-arbre des résultats restent visibles."""
        raw = (text or "").strip()
        folded = _fold(raw)
        if raw == self._search_text and folded == self._search_folded:
            return
        entering = not self._search_folded and bool(folded)
        if entering and self._cmp is not None:
            self._expanded_before_search = self._expanded_folders()
            self._current_before_search = self._current_folder()
        self._search_text, self._search_folded = raw, folded
        if self._cmp is None:
            return
        if not folded and self._expanded_before_search is not None:
            expanded, current = self._expanded_before_search, self._current_before_search
            self._expanded_before_search, self._current_before_search = None, ""
            self._rebuild(expanded, current, had_model=True, restore_exact=True)
        else:
            self._rebuild(had_model=True)

    def search_result_count(self) -> int:
        """Nombre de dossiers dont le nom correspond à la recherche (hors contexte)."""
        return self._search_matches

    def search_no_match(self) -> bool:
        """Vrai si la recherche est active et ne trouve aucun dossier dans le filtre courant."""
        return self._search_no_match
    def row_count(self) -> int:
        return len(self._model.nodes()) if self._model is not None else 0

    def folders(self) -> list[str]:
        """Clés des dossiers affichés, dans l'ordre de l'arbre."""
        return [node.folder for node in self._model.nodes()] if self._model is not None else []

    def counts(self) -> dict[str, int]:
        """Nombre de dossiers par état, sur toute la comparaison (indépendamment de la recherche)."""
        result = {state: 0 for state in _STATE_STYLE}
        if self._cmp is not None:
            for row in self._cmp.rows:
                result[row.state] = result.get(row.state, 0) + 1
        return result

    def expand_all(self) -> None:
        self.view.expandAll()

    def collapse_all(self) -> None:
        self.view.collapseAll()

    def select_next_difference(self) -> None:
        self._select_difference(forward=True)

    def select_previous_difference(self) -> None:
        self._select_difference(forward=False)

    # ── Interne ──────────────────────────────────────────────────────────────
    def _update_stats(self) -> None:
        stats = self.counts()
        equal, different = stats[eng.STATE_EQUAL], stats[eng.STATE_DIFF]
        absent = stats[eng.STATE_ONLY_A] + stats[eng.STATE_ONLY_B]
        self.chip_equal.set_state(_plural(equal, QCoreApplication.translate("ScopeTree", "identique"), QCoreApplication.translate("ScopeTree", "identiques")),
                                  "ok" if equal else "neutre")
        self.chip_diff.set_state(_plural(different, QCoreApplication.translate("ScopeTree", "différent"), QCoreApplication.translate("ScopeTree", "différents")),
                                 "warn" if different else "neutre")
        self.chip_absent.set_state(_plural(absent, QCoreApplication.translate("ScopeTree", "absent"), QCoreApplication.translate("ScopeTree", "absents")),
                                   "err" if absent else "neutre")
        visible = self._model.nodes() if self._model is not None else []
        has_differences = any(node.row.state != eng.STATE_EQUAL for node in visible)
        for button in (self.btn_previous, self.btn_next):
            button.setEnabled(has_differences)
        self.btn_expand.setEnabled(bool(visible))
        self.btn_collapse.setEnabled(bool(visible))

    def _set_empty_message(self, nothing: bool = False, search_no_match: bool = False) -> None:
        if self._cmp is None:
            self.empty.set_message(
                QCoreApplication.translate("ScopeTree", "Aucune comparaison"),
                QCoreApplication.translate("ScopeTree", "Choisissez le dossier mère et le dossier fille, puis lancez la comparaison. "
                    "Les dossiers apparaîtront ici, avec l'effectif de chaque côté et une couleur par état."))
        elif search_no_match:
            self.empty.set_message(
                QCoreApplication.translate("ScopeTree", "Aucun dossier trouvé"),
                QCoreApplication.translate("ScopeTree", "Aucun dossier ne correspond à « {query} » dans les résultats affichés. "
                    "Effacez la recherche (Échap) ou essayez un autre terme.").format(query=self._search_text))
        elif nothing:
            self.empty.set_message(
                QCoreApplication.translate("ScopeTree", "Aucune différence"),
                QCoreApplication.translate("ScopeTree", "Les deux arborescences contiennent les mêmes dossiers, avec le même nombre de fichiers."))
        else:
            self.empty.set_message(QCoreApplication.translate("ScopeTree", "Arborescence vide"), QCoreApplication.translate("ScopeTree", "Les dossiers analysés ne contiennent aucun dossier."))

    def _expanded_folders(self) -> set[str]:
        if self._model is None:
            return set()
        return {node.folder for node in self._model.nodes()
                if self.view.isExpanded(self._model.node_index(node))}

    def _current_folder(self) -> str:
        index = self.view.currentIndex()
        return str(index.data(FOLDER_ROLE) or "") if index.isValid() else ""

    def _select_difference(self, forward: bool) -> None:
        if self._model is None:
            return
        differences = [node for node in self._model.nodes() if node.row.state != eng.STATE_EQUAL]
        if not differences:
            return
        current = self._current_folder()
        folders = [node.folder for node in differences]
        try:
            position = folders.index(current)
        except ValueError:
            position = -1 if forward else len(differences)
        target = position + 1 if forward else position - 1
        if not 0 <= target < len(differences):
            return
        node = differences[target]
        parent = node.parent
        while parent is not None:
            self.view.expand(self._model.node_index(parent))
            parent = parent.parent
        index = self._model.node_index(node)
        self.view.setCurrentIndex(index)
        self.view.scrollTo(index, QAbstractItemView.ScrollHint.PositionAtCenter)
        self.view.setFocus(Qt.FocusReason.OtherFocusReason)
