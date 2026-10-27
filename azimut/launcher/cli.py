"""Analyse de la ligne de commande d'Azimut (aucun import Qt au niveau du module)."""
from __future__ import annotations

import sys

from azimut import APP_NAME, __version__
from azimut.core.logs import get_logger
from azimut.launcher import registry

log = get_logger(__name__)

_PROG = "azimut-launcher"  # argv[0] factice : jamais un chemin existant

AIDE = """Usage :
  python main.py                       ouvre Atlas, le lanceur Azimut
  python main.py <module> [chemins…]   lance directement un module
  python main.py --list                liste les modules disponibles
  python main.py --version             affiche la version
  python main.py --help                affiche cette aide
"""


def _write(stream, text: str) -> None:
    """Écrit une ligne ; tolère l'absence de console (exécutable sans fenêtre)."""
    if stream is None:
        return
    try:
        stream.write(text + "\n")
        stream.flush()
    except UnicodeEncodeError:
        try:
            stream.write(text.encode("ascii", "replace").decode("ascii") + "\n")
        except (OSError, ValueError):
            pass
    except (OSError, ValueError):
        pass


def _cmd_list() -> int:
    specs, errors = registry.discover_with_errors()
    if not specs and not errors:
        _write(sys.stdout, "Aucun module disponible.")
        return 0
    _write(sys.stdout, "Modules Azimut :")
    for spec in specs:
        requis = ", ".join(spec.requires) or "aucun"
        _write(sys.stdout, f"  {spec.id:<10}{spec.name:<18}binaires requis : {requis}")
    for module_id, message in errors.items():
        _write(sys.stdout, f"  {module_id:<10}(indisponible : {message})")
    return 0


def _run_atlas() -> int:
    try:
        from azimut.launcher.atlas_window import AtlasWindow
        from azimut.ui.app import create_app

        app = create_app([_PROG])
        window = AtlasWindow()
        window.show()
        code = int(app.exec())
        window.close()
        window.deleteLater()
        app.processEvents()          # laisse Qt détruire la fenêtre avant l'application
        del window
        return code
    except Exception:
        log.exception("Échec du démarrage d'Atlas")
        _write(sys.stderr, "Erreur : Atlas n'a pas pu démarrer (voir le journal).")
        return 1


def _run_module(module_id: str, paths: list[str]) -> int:
    specs, errors = registry.discover_with_errors()
    spec = next((s for s in specs if s.id == module_id), None)
    if spec is None:
        if module_id in errors:
            _write(sys.stderr, f"Erreur : le module « {module_id} » est indisponible : {errors[module_id]}")
            return 1
        connus = ", ".join(s.id for s in specs) or "aucun"
        _write(sys.stderr, f"Erreur : module inconnu « {module_id} ». "
                           f"Modules disponibles : {connus}. Utilisez --list pour le détail.")
        return 2
    from azimut.ui.app import run_module

    return int(run_module(spec, [_PROG, *paths]))


def main(argv: list[str] | None = None) -> int:
    """Point d'entrée commun à `main.py` et `python -m azimut`."""
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        return _run_atlas()
    first = args[0]
    if first in ("-h", "--help"):
        _write(sys.stdout, AIDE)
        return 0
    if first == "--version":
        _write(sys.stdout, f"{APP_NAME} {__version__}")
        return 0
    if first == "--list":
        return _cmd_list()
    if first.startswith("-"):
        _write(sys.stderr, f"Erreur : option inconnue « {first} ».\n{AIDE}")
        return 2
    return _run_module(first, args[1:])
