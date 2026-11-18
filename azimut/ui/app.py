"""Amorçage de l'application : QApplication, journalisation, thème, détection des binaires."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEvent, QProcess
from PySide6.QtWidgets import QApplication, QWidget

from azimut import APP_NAME, __version__
from azimut.core import binaries, i18n
from azimut.core.logs import get_logger, setup_logging
from azimut.core.module_spec import ModuleSpec, load_window_class
from azimut.core.prefs import Preferences
from azimut.ui import icons
from azimut.ui.theme import ThemeManager

log = get_logger(__name__)

_READY_PROPERTY = "azimut.ready"


def create_app(argv: list[str] | None = None) -> QApplication:
    """Crée (ou réutilise) la QApplication et l'initialise une seule fois (idempotent)."""
    app = QApplication.instance()
    if app is None:
        app = QApplication(list(sys.argv if argv is None else argv))
        if sys.platform != "darwin":
            app.setStyle("Fusion")                   # rendu homogène hors macOS ; macOS garde son style natif
    if bool(app.property(_READY_PROPERTY)):
        return app                                   # type: ignore[return-value]
    app.setOrganizationName(APP_NAME)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setDesktopFileName("azimut")                 # Wayland/KDE : rattache la fenêtre à azimut.desktop si présent
    app.setWindowIcon(icons.app_icon())              # icône de fenêtre / barre des tâches (logo embarqué)
    setup_logging()
    state = i18n.install(app, Preferences.instance().get("language", i18n.AUTO))
    log.info("Langue de l'interface : %s (demandée : %s ; détectée : %s ; repli : %s)",
             state.active, state.requested, state.detected or "—", state.fallback)
    app.setProperty("azimut.language", state.active)
    ThemeManager.instance().apply(app)               # type: ignore[arg-type]
    binaries.refresh(background=True)                # détecte sans geler l'interface (versions sondées à part)
    app.setProperty(_READY_PROPERTY, True)
    return app                                       # type: ignore[return-value]


def shutdown_ui(app: QApplication, window: QWidget | None = None) -> None:
    """Détruit la fenêtre et ses enfants avant la fin de l'interpréteur.

    Les connexions à ``ThemeManager.changed`` gardent une référence forte vers les
    fenêtres : un simple ``del`` ne libère donc pas l'objet Qt. Sans destruction
    explicite, PySide le détruit pendant la finalisation de Python, après la
    QApplication, ce qui provoque un SIGBUS à la sortie (PySide 6.12 / Python 3.14).
    On force la destruction pendant que l'application est encore vivante.
    """
    if window is not None:
        window.close()
        window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def restart_command() -> tuple[str, list[str]]:
    """Programme et arguments pour relancer Azimut avec les mêmes options."""
    args = list(sys.argv[1:])
    if getattr(sys, "frozen", False):                 # application empaquetée (PyInstaller…)
        return sys.executable, args
    # Comparaison par composants de chemin : « azimut/__main__.py » et « azimut\\__main__.py »
    # désignent le même point d'entrée selon la plateforme.
    entry = Path(sys.argv[0]) if sys.argv else Path("")
    if entry.name == "__main__.py" and entry.parent.name == "azimut":
        return sys.executable, ["-m", "azimut", *args]
    if sys.argv and sys.argv[0]:
        return sys.executable, [os.path.abspath(sys.argv[0]), *args]
    return sys.executable, ["-m", "azimut", *args]


def restart_application() -> bool:
    """Démarre une nouvelle instance d'Azimut ; False si le lancement échoue."""
    program, args = restart_command()
    result = QProcess.startDetached(program, args, os.getcwd())
    if isinstance(result, tuple):                      # PySide renvoie (succès, pid)
        result = result[0]
    return bool(result)


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
    shutdown_ui(app, window)
    del window
    return code
