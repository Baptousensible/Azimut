"""Auto-test d'Azimut : vérifie qu'un paquet (source ou figé) est complet, sans ouvrir de fenêtre.

Usage : ``Azimut --selftest [rapport.json]`` (ou ``python main.py --selftest``).
Écrit un rapport JSON (fichier donné, sinon stdout s'il existe) et renvoie 0 si tout est en ordre.
Un binaire obligatoire absent ou non exécutable, un module qui ne se charge pas ou un
Qt inutilisable donnent un code de sortie 1 ; les composants facultatifs ne sont que signalés.
"""
from __future__ import annotations

import json
import os
import platform
import sys
from pathlib import Path
from typing import Any


def _check_qt() -> dict[str, Any]:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        import PySide6
        from PySide6.QtCore import QCoreApplication, qVersion
        from PySide6.QtWidgets import QApplication  # noqa: F401 - vérifie la présence des plugins
        return {"ok": True, "pyside6": PySide6.__version__, "qt": qVersion(),
                "app": QCoreApplication.instance() is not None or True}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def _check_pillow() -> dict[str, Any]:
    info: dict[str, Any] = {"ok": True}
    try:
        import PIL
        from PIL import Image
        info["version"] = PIL.__version__
        Image.init()
        info["webp_save"] = "WEBP" in Image.SAVE
        info["avif_save"] = "AVIF" in Image.SAVE
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    try:  # C1 : azimut.core.imaging.register_heif ; repli direct si le module n'existe pas encore
        try:
            from azimut.core.imaging import register_heif
            info["heif"] = bool(register_heif())
        except ImportError:
            import pillow_heif
            pillow_heif.register_heif_opener()
            info["heif"] = True
    except Exception:  # noqa: BLE001 - facultatif
        info["heif"] = False
    return info


def _check_send2trash() -> dict[str, Any]:
    try:
        import send2trash  # noqa: F401
        return {"ok": True}
    except ImportError:
        return {"ok": False, "optional": True}


def _check_binaries() -> dict[str, Any]:
    """Résout chaque binaire connu et le **lance** (la version doit être lisible)."""
    from azimut.core import binaries
    out: dict[str, Any] = {"tag": binaries.platform_tag(), "items": {}}
    try:
        found = binaries.refresh()
    except Exception as exc:  # noqa: BLE001
        return {**out, "error": f"{type(exc).__name__}: {exc}"}
    for name, info in found.items():
        out["items"][name] = {
            "available": info.available,
            "source": info.source,
            "path": str(info.path) if info.path else None,
            "version": info.version,
            "runs": bool(info.version),          # version lue = le binaire s'exécute vraiment
        }
    return out


def _check_modules() -> dict[str, Any]:
    """Importe chaque module ET sa classe de fenêtre (détecte les imports dynamiques manquants du paquet figé)."""
    from azimut.core.module_spec import load_window_class
    from azimut.launcher import registry
    specs, errors = registry.discover_with_errors()
    result: dict[str, Any] = {"errors": dict(errors), "modules": {}}
    for spec in specs:
        try:
            load_window_class(spec)
            result["modules"][spec.id] = {"ok": True, "requires": list(spec.requires)}
        except Exception as exc:  # noqa: BLE001
            result["modules"][spec.id] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return result


def collect() -> tuple[dict[str, Any], list[str]]:
    """Rapport complet et liste des problèmes bloquants."""
    from azimut import APP_NAME, __version__
    from azimut.core.paths import app_root, is_frozen

    report: dict[str, Any] = {
        "app": APP_NAME, "version": __version__, "python": sys.version.split()[0],
        "platform": platform.platform(), "machine": platform.machine(),
        "frozen": is_frozen(), "app_root": str(app_root()),
        "qt": _check_qt(), "pillow": _check_pillow(), "send2trash": _check_send2trash(),
    }
    problems: list[str] = []
    if not report["qt"].get("ok"):
        problems.append(f"Qt inutilisable : {report['qt'].get('error')}")
    if not report["pillow"].get("ok"):
        problems.append(f"Pillow inutilisable : {report['pillow'].get('error')}")

    report["binaries"] = _check_binaries()
    if report["binaries"].get("error"):
        problems.append(f"Détection des binaires : {report['binaries']['error']}")

    report["modules"] = mods = _check_modules()
    for module_id, message in mods["errors"].items():
        problems.append(f"Module {module_id} indisponible : {message}")
    required_by_module: set[str] = set()
    for module_id, entry in mods["modules"].items():
        if not entry["ok"]:
            problems.append(f"Fenêtre du module {module_id} non chargeable : {entry['error']}")
        required_by_module.update(entry.get("requires", []))

    for name in sorted(required_by_module):
        item = report["binaries"].get("items", {}).get(name)
        if item is None or not item["available"]:
            problems.append(f"Binaire requis introuvable : {name}")
        elif not item["runs"]:
            problems.append(f"Binaire requis non exécutable ou d'une autre architecture : {name} ({item['path']})")
    if report["frozen"] and report["binaries"].get("items"):
        for name, item in report["binaries"]["items"].items():
            if item["available"] and item["source"] != "bundled":
                report.setdefault("warnings", []).append(f"{name} vient de « {item['source']} », pas du paquet.")
    return report, problems


def run(args: list[str] | None = None) -> int:
    """Point d'entrée de ``--selftest [rapport.json]``."""
    target = Path(args[0]) if args else None
    try:
        report, problems = collect()
    except Exception as exc:  # noqa: BLE001 - l'auto-test ne doit jamais planter sans rapport
        report, problems = {"fatal": f"{type(exc).__name__}: {exc}"}, [f"Échec de l'auto-test : {exc}"]
    report["problems"] = problems
    report["ok"] = not problems
    text = json.dumps(report, indent=2, ensure_ascii=False)
    if target is not None:
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        except OSError:
            target = None
    if target is None and sys.stdout is not None:
        try:
            sys.stdout.write(text + "\n")
            sys.stdout.flush()
        except (OSError, ValueError, UnicodeEncodeError):
            pass
    return 0 if report["ok"] else 1
