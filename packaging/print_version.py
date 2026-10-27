"""Affiche la version d'Azimut (lue dans azimut/__init__.py, sans importer le paquet)."""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Accepte `__version__ = "1.2.3"` comme `__version__: str = "1.2.3"`.
PATTERN = re.compile(r"""__version__\s*(?::[^=\n]+)?=\s*["']([^"']+)["']""")


def read_version() -> str:
    text = (ROOT / "azimut" / "__init__.py").read_text(encoding="utf-8")
    found = PATTERN.search(text)
    if not found:
        raise SystemExit("ERREUR : __version__ introuvable dans azimut/__init__.py")
    return found.group(1)


if __name__ == "__main__":
    sys.stdout.write(read_version() + "\n")
