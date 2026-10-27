"""Amorçage de l'application : QApplication, journalisation, thème, détection des binaires."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from azimut import APP_NAME, __version__
from azimut.core import binaries
from azimut.core.logs import get_logger, setup_logging
from azimut.core.module_spec import ModuleSpec, load_window_class
from azimut.ui.theme import ThemeManager

log = get_logger(__name__)

_READY_PROPERTY = "azimut.ready"


def create_app(argv: list[str] | None = None) -> QApplication:
    """Crée (ou réutilise) la QApplication et l'initialise une seule fois (idempotent)."""
    app = QApplication.instance()
    if app is None:
        app = QApplication(list(sys.argv if argv is None else argv))
        app.setStyle("Fusion")                       # rendu homogène sous toutes les plateformes
    if bool(app.property(_READY_PROPERTY)):
        return app                                   # type: ignore[return-value]
    app.setOrganizationName(APP_NAME)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    setup_logging()
    ThemeManager.instance().apply(app)               # type: ignore[arg-type]
    binaries.refresh()                               # journalise les versions détectées
    app.setProperty(_READY_PROPERTY, True)
    return app                                       # type: ignore[return-value]


def _existing_paths(args: list[str]) -> list[str]:
    """Arguments de ligne de commande qui désignent un chemin existant (rendus absolus)."""
    found: list[str] = []
    for arg in args:
        try:
            if arg and Path(arg).exists():
                found.append(os.path.abspath(arg))
        except (OSError, ValueError):
            continue
    return found


def run_module(spec: ModuleSpec, argv: list[str] | None = None) -> int:
    """Lance un module seul. `argv` suit la convention de sys.argv (argv[0] = programme)."""
    args = list(sys.argv if argv is None else argv)
    app = create_app(args)
    window_cls = load_window_class(spec)
    window = window_cls(spec)
    window.show()
    paths = _existing_paths(args[1:])
    if paths:
        log.info("Chemins reçus en ligne de commande : %d", len(paths))
        window.open_paths(paths)
    code = int(app.exec())
    window.close()
    window.deleteLater()
    app.processEvents()
    del window
    return code
