"""Tests : toute chaîne visible par l'utilisateur est extractible par lupdate (I18N-001).

`pyside6-lupdate` ne lit que les littéraux passés à `tr()`, `QCoreApplication.translate()` et
`QT_TRANSLATE_NOOP()`. Un texte en dur dans un `QLabel`, un `setText(f"...")` ou un helper
maison n'entre jamais dans les catalogues : ces tests empêchent la réapparition du problème.
"""
from __future__ import annotations

import ast
import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "azimut"

# APIs dont un argument est affiché tel quel à l'utilisateur.
VISIBLE_APIS = {
    "QLabel": (0,), "QPushButton": (0,), "QCheckBox": (0,), "QRadioButton": (0,),
    "QGroupBox": (0,), "QMenu": (0,), "QAction": (0,), "QListWidgetItem": (0,),
    "QTableWidgetItem": (0,), "QTreeWidgetItem": (0,), "Card": (0,), "StatusChip": (0,),
    "setText": (0,), "setToolTip": (0,), "setWindowTitle": (0,), "setPlaceholderText": (0,),
    "setStatusTip": (0,), "setAccessibleName": (0,), "setAccessibleDescription": (0,),
    "addTab": (1,), "addItem": (0,), "addAction": (0,),
    "_make_button": (0,), "add_button": (0,), "add_action_button": (0,),
    "Column": (1,),
}
# Boîtes de dialogue : (titre, texte), réservé aux méthodes statiques de QMessageBox.
QMessageBox_APIS = {"information": (1, 2), "warning": (1, 2), "critical": (1, 2),
                    "question": (1, 2), "about": (1,)}
# Valeurs techniques passées à ces APIs (styles, niveaux, raccourcis, glyphes, compteurs).
TECHNICAL = {
    "kind", "level", "ghost", "primary", "secondary", "accent", "danger", "success", "auto",
    "ok", "err", "neutre", "warn", "info", "T", "H", "—", "…", "×", "▶", "❚❚", "0 / 0 (0 %)",
    "0:00 / 0:00", "Ctrl+L", "F1", "Esc", "Ctrl+Return", "Ctrl+,", "Ctrl+E", "Ctrl+T",
    "system", "dark", "light", "left", "center", "right",
}
TRANSLATORS = {"tr", "translate", "_tr", "QT_TRANSLATE_NOOP", "QT_TR_NOOP"}


def _call_name(node: ast.Call) -> str:
    """Nom court de l'appel : `QLabel(...)` -> QLabel, `box.setText(...)` -> setText."""
    func = node.func
    return func.attr if isinstance(func, ast.Attribute) else (func.id if isinstance(func, ast.Name) else "")


def _owner(node: ast.Call) -> str:
    """Classe d'un appel statique : `QMessageBox.warning(...)` -> QMessageBox."""
    func = node.func
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        return func.value.id
    return ""


def _parents(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    parents: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    return parents


def _is_wrapped(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> bool:
    """Vrai si le nœud est l'argument (direct ou parenthésé) d'un appel de traduction."""
    current = parents.get(node)
    while current is not None:
        if isinstance(current, ast.Call):
            return _call_name(current) in TRANSLATORS
        current = parents.get(current)
    return False


def _offenders(path: Path) -> list[str]:
    """Littéraux visibles (ou f-strings) qui n'entrent pas dans un catalogue."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:                                  # pragma: no cover - fichier invalide
        return [f"{path}: syntaxe invalide"]
    parents = _parents(tree)
    issues = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node)
        indexes = QMessageBox_APIS.get(name, ()) if _owner(node) == "QMessageBox" else VISIBLE_APIS.get(name, ())
        if not indexes:
            continue
        for index in indexes:
            if index >= len(node.args):
                continue
            argument = node.args[index]
            if _is_wrapped(argument, parents):
                continue
            line = f"{path.relative_to(ROOT)}:{node.lineno} {name}()"
            if isinstance(argument, ast.JoinedStr):
                # Seul le texte littéral compte : `f"{nom}"` ou une enveloppe HTML n'a rien à traduire.
                literal = "".join(value.value for value in argument.values
                                  if isinstance(value, ast.Constant) and isinstance(value.value, str))
                literal = re.sub(r"<[^>]*>", " ", literal)
                literal = re.sub(r"&[a-zA-Z]+;|&#\d+;", " ", literal)
                if re.search(r"[A-Za-zÀ-ÿ]{3}", literal):
                    issues.append(f"{line} : f-string non traduite -> self.tr(...).format(...)")
            elif isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                text = argument.value.strip()
                if text and text not in TECHNICAL and any(c.isalpha() for c in text):
                    issues.append(f"{line} : {text!r} hors tr()")
    return issues


def test_no_visible_string_escapes_translation():
    issues = [issue for path in sorted(PACKAGE.rglob("*.py")) for issue in _offenders(path)]
    assert not issues, "Chaînes visibles non extractibles :\n" + "\n".join(issues)


def test_no_private_translation_helper():
    """Un helper `_tr()` rendrait toutes ses chaînes invisibles pour lupdate."""
    helpers = []
    for path in sorted(PACKAGE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_tr":
                helpers.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert not helpers, "Utiliser QCoreApplication.translate(<contexte>, <littéral>) : " + ", ".join(helpers)


def test_no_translation_context_override():
    """Redéfinir tr() changerait le contexte à l'exécution sans que lupdate le sache :
    les chaînes seraient extraites sous le nom de la classe et jamais retrouvées à l'affichage."""
    overrides = []
    for path in sorted(PACKAGE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "tr" \
                    and node.args.args and node.args.args[0].arg == "self":
                overrides.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert not overrides, "Laisser Qt fournir tr() (contexte = classe) : " + ", ".join(overrides)


def test_update_tool_scans_the_whole_package():
    """tools/update_translations.py doit couvrir azimut/**, pas seulement azimut/ui."""
    spec = importlib.util.spec_from_file_location("update_translations",
                                                  ROOT / "tools" / "update_translations.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    scanned = {Path(name) for name in module._source_files()}
    expected = {path.relative_to(ROOT) for path in PACKAGE.rglob("*.py")}
    assert expected <= scanned, f"Sources non scannées : {sorted(expected - scanned)}"
