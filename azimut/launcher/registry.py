"""Découverte des modules Azimut : import tolérant, tri par ordre."""
from __future__ import annotations

import importlib

from azimut.core.logs import get_logger
from azimut.core.module_spec import MODULE_IDS, ModuleSpec

log = get_logger(__name__)

__all__ = ["discover", "discover_with_errors"]


def _load_spec(module_id: str) -> ModuleSpec:
    """Importe `azimut.modules.<id>` et renvoie son `MODULE`."""
    module = importlib.import_module(f"azimut.modules.{module_id}")
    spec = getattr(module, "MODULE", None)
    if not isinstance(spec, ModuleSpec):
        raise TypeError("le module ne définit pas MODULE (ModuleSpec)")
    if spec.id != module_id:
        raise ValueError(f"identifiant incohérent : « {spec.id} » au lieu de « {module_id} »")
    return spec


def discover_with_errors() -> tuple[list[ModuleSpec], dict[str, str]]:
    """Renvoie (specs triées par `order`, erreurs par identifiant de module)."""
    specs: list[ModuleSpec] = []
    errors: dict[str, str] = {}
    for module_id in MODULE_IDS:
        try:
            specs.append(_load_spec(module_id))
        except Exception as exc:  # un module défaillant ne bloque pas les autres
            errors[module_id] = f"{type(exc).__name__} : {exc}"
            log.warning("Module « %s » indisponible : %s", module_id, errors[module_id])
    specs.sort(key=lambda s: (s.order, s.id))
    return specs, errors


def discover() -> list[ModuleSpec]:
    """Modules chargés correctement, triés par `order`."""
    return discover_with_errors()[0]
