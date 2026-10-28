"""Description déclarative des modules (cartes d'Atlas, binaires requis, point d'entrée)."""
from __future__ import annotations

import importlib
from dataclasses import dataclass

MODULE_IDS: tuple[str, ...] = ("convert", "tags", "takeout", "dedup", "press")


@dataclass(frozen=True)
class ModuleSpec:
    id: str                     # « convert »
    name: str                   # « Azimut Convert »
    tagline: str                # une phrase, en français
    icon: str                   # un emoji
    entry: str                  # « azimut.modules.convert.window:ConvertWindow » (importé à la demande)
    requires: tuple[str, ...] = ()    # binaires obligatoires, ex. ("ffmpeg", "ffprobe")
    optional: tuple[str, ...] = ()    # binaires facultatifs
    order: int = 100            # tri des cartes dans Atlas


def load_window_class(spec: ModuleSpec) -> type:
    """Importe et renvoie la classe de fenêtre désignée par ``spec.entry`` (« paquet.module:Classe »)."""
    module_name, sep, class_name = spec.entry.partition(":")
    if not sep or not module_name or not class_name:
        raise ImportError(f"Point d'entrée invalide pour « {spec.name} » : « {spec.entry} » "
                          "(attendu : « paquet.module:Classe »).")
    try:
        module = importlib.import_module(module_name)
    except ImportError as e:
        raise ImportError(f"Impossible de charger le module « {module_name} » pour « {spec.name} » : {e}") from e
    try:
        cls = getattr(module, class_name)
    except AttributeError:
        raise ImportError(f"La classe « {class_name} » est introuvable dans « {module_name} » "
                          f"(module « {spec.name} »).") from None
    return cls
