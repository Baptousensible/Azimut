"""I18N-001 : aucun texte visible en dur dans les fenêtres des modules.

Le test lit le source (pas Qt) et signale tout littéral passé directement à une API qui
affiche du texte. Une chaîne enveloppée dans ``tr()``/``translate()`` est un appel, donc
n'est pas signalée ; un f-string sans champ reste un littéral et doit devenir
``self.tr("...").format(...)``.
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

#: APIs dont un argument littéral est affiché tel quel à l'utilisateur.
VISIBLE_APIS = frozenset({
    "setText", "setToolTip", "setWindowTitle", "setPlaceholderText", "setTabText",
    "addItem", "addTab",
    "QLabel", "QPushButton", "QCheckBox", "QGroupBox", "QAction",
})

#: Arguments réellement affichés, quand les autres portent des données techniques
#: (``addItem(texte, donnée)``, ``addTab(page, texte)``).
VISIBLE_ARGUMENTS = {"addItem": (0,), "addTab": (1,)}


def _call_name(node: ast.expr) -> str:
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


def _literal(node: ast.expr) -> str | None:
    """Texte d'un littéral (constante ou f-string sans champ), sinon ``None``."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr) and all(isinstance(v, ast.Constant) for v in node.values):
        return "".join(str(v.value) for v in node.values)
    return None


def visible_literals(path: Path) -> list[tuple[int, str]]:
    """Littéraux visibles ``(ligne, texte)`` d'un fichier, hors chaînes vides."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or _call_name(node.func) not in VISIBLE_APIS:
            continue
        positions = VISIBLE_ARGUMENTS.get(_call_name(node.func))
        args = node.args if positions is None else [node.args[i] for i in positions if i < len(node.args)]
        for arg in (*args, *(keyword.value for keyword in node.keywords)):
            text = _literal(arg)
            if text is not None and text.strip():
                found.append((node.lineno, text))
    return sorted(found)


def _assert_no_visible_literals(relative_path: str) -> None:
    offenders = visible_literals(ROOT / relative_path)
    assert offenders == [], "\n".join(
        f"{relative_path}:{line} : {text!r} doit passer par tr()" for line, text in offenders)


def test_atlas_window_has_no_visible_literals():
    _assert_no_visible_literals("azimut/launcher/atlas_window.py")
    _assert_no_visible_literals("azimut/launcher/personal_note.py")


def test_module_windows_have_no_visible_literals():
    """I18N-001 : plus aucun texte visible en dur dans les fenêtres des six outils."""
    for relative_path in (
        "azimut/modules/convert/window.py",
        "azimut/modules/convert/codec_tree.py",
        "azimut/modules/takeout/window.py",
        "azimut/modules/rename/window.py",
        "azimut/modules/tags/window.py",
        "azimut/modules/dedup/window.py",
        "azimut/modules/scope/window.py",
    ):
        _assert_no_visible_literals(relative_path)
