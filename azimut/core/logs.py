"""Journalisation : fichier tournant dans le dossier de journaux utilisateur."""
from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

from azimut.core.paths import user_log_dir

_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
_MARK = "_azimut_file_handler"
_ROOT_NAME = "azimut"


def setup_logging(level: int = logging.INFO) -> Path:
    """Configure le journal ``azimut.log`` (idempotent). Renvoie le chemin du fichier."""
    path = user_log_dir() / "azimut.log"
    logger = logging.getLogger(_ROOT_NAME)
    logger.setLevel(level)
    target = os.path.abspath(str(path))
    for handler in list(logger.handlers):
        if getattr(handler, _MARK, False):
            if os.path.abspath(getattr(handler, "baseFilename", "")) == target:
                return path                      # déjà configuré
            logger.removeHandler(handler)        # autre fichier (ex. dossiers changés) : on remplace
            handler.close()
    try:
        handler = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    except OSError:
        return path                              # journal fichier indisponible : on continue sans
    handler.setFormatter(logging.Formatter(_FORMAT))
    setattr(handler, _MARK, True)
    logger.addHandler(handler)
    return path


def get_logger(name: str) -> logging.Logger:
    """Logger « azimut.<name> » (le préfixe n'est pas doublé si ``name`` le contient déjà)."""
    if name == _ROOT_NAME or name.startswith(_ROOT_NAME + "."):
        return logging.getLogger(name)
    return logging.getLogger(f"{_ROOT_NAME}.{name}")
