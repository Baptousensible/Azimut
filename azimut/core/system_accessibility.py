"""Réglages d'accessibilité du système, lus sans dépendance obligatoire à Qt.

Les trois fonctions publiques renvoient ``True``/``False`` quand le système expose
le réglage, ``None`` sinon. L'appelant garde la main : une préférence explicite
d'Azimut doit toujours primer sur la valeur du système.
"""
from __future__ import annotations

import configparser
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from azimut.core.logs import get_logger

log = get_logger(__name__)

_GSETTINGS_TIMEOUT = 0.8


def _parse_bool(value: Any) -> bool | None:
    """Convertit les formes renvoyées par gsettings / QSettings / winreg."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    if isinstance(value, bytes):
        value = value.decode("utf-8", "replace")
    if isinstance(value, str):
        text = value.strip().strip("'\"").lower()
        if text in ("true", "yes", "on", "1"):
            return True
        if text in ("false", "no", "off", "0"):
            return False
    return None


def _gsettings_text(schema: str, key: str) -> str | None:
    """Lit la valeur brute d'une clé GSettings ; None si indisponible."""
    executable = shutil.which("gsettings")
    if executable is None:
        return None
    try:
        result = subprocess.run(
            [executable, "get", schema, key],
            capture_output=True, text=True, timeout=_GSETTINGS_TIMEOUT, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.debug("gsettings %s %s indisponible : %s", schema, key, exc)
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip().strip("'\"")


def _gsettings(schema: str, key: str) -> bool | None:
    """Lit une clé GSettings booléenne ; None si gsettings ou la clé n'existe pas."""
    return _parse_bool(_gsettings_text(schema, key))


def _kde_animations_disabled() -> bool | None:
    """KDE : AnimationDurationFactor=0 désactive les animations du bureau."""
    parser = configparser.ConfigParser(interpolation=None)
    try:
        with open(Path.home() / ".config" / "kdeglobals", "r", encoding="utf-8") as handle:
            parser.read_file(handle)
        factor = float(parser.get("KDE", "AnimationDurationFactor"))
    except (OSError, configparser.Error, ValueError):
        return None
    return factor <= 0.0


def _linux_reduce_motion() -> bool | None:
    animations = _gsettings("org.gnome.desktop.interface", "enable-animations")
    if animations is not None:
        return not animations
    return _kde_animations_disabled()


def _kde_color_scheme() -> str | None:
    """KDE : déduit le thème du nom du jeu de couleurs, puis de la luminance du fond."""
    parser = configparser.ConfigParser(interpolation=None)
    try:
        with open(Path.home() / ".config" / "kdeglobals", "r", encoding="utf-8") as handle:
            parser.read_file(handle)
    except (OSError, configparser.Error):
        return None
    scheme = parser.get("General", "ColorScheme", fallback="").replace(" ", "").lower()
    if "dark" in scheme:
        return "dark"
    if "light" in scheme:
        return "light"
    try:
        raw = parser.get("Colors:Window", "BackgroundNormal")
        red, green, blue = (float(part.strip()) for part in raw.split(",")[:3])
    except (configparser.Error, ValueError):
        return None
    return "dark" if 0.2126 * red + 0.7152 * green + 0.0722 * blue < 128.0 else "light"


def _linux_color_scheme() -> str | None:
    """KDE puis GNOME/Freedesktop : None si le bureau n'expose rien d'exploitable."""
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
    if "kde" in desktop or "plasma" in desktop:
        kde = _kde_color_scheme()
        if kde is not None:
            return kde
    scheme = (_gsettings_text("org.gnome.desktop.interface", "color-scheme") or "").lower()
    if "dark" in scheme:
        return "dark"
    if "light" in scheme:
        return "light"
    gtk_theme = (_gsettings_text("org.gnome.desktop.interface", "gtk-theme") or "").lower()
    if "dark" in gtk_theme:
        return "dark"
    if gtk_theme:
        return "light"                              # thème GTK clair sans marqueur « light »
    return _kde_color_scheme()


def _kde_high_contrast() -> bool | None:
    """KDE : le nom du jeu de couleurs signale le variant contrasté."""
    parser = configparser.ConfigParser(interpolation=None)
    try:
        with open(Path.home() / ".config" / "kdeglobals", "r", encoding="utf-8") as handle:
            parser.read_file(handle)
        scheme = parser.get("General", "ColorScheme")
    except (OSError, configparser.Error):
        return None
    normalized = scheme.replace(" ", "").lower()
    return "highcontrast" in normalized if normalized else None


def _linux_high_contrast() -> bool | None:
    value = _gsettings("org.gnome.desktop.a11y.interface", "high-contrast")
    return value if value is not None else _kde_high_contrast()


def _macos_value(key: str) -> bool | None:
    if sys.platform != "darwin":
        return None
    try:
        from PySide6.QtCore import QSettings
        settings = QSettings("com.apple.universalAccess", QSettings.Format.NativeFormat)
        if settings.contains(key):
            return _parse_bool(settings.value(key))
    except (ImportError, OSError, RuntimeError, TypeError) as exc:
        log.debug("QSettings macOS %s indisponible : %s", key, exc)
    return _macos_defaults(key)


def _macos_defaults(key: str) -> bool | None:
    """Repli sur « defaults read » quand QSettings ne voit pas le domaine global."""
    executable = shutil.which("defaults")
    if executable is None:
        return None
    try:
        result = subprocess.run(
            [executable, "read", "com.apple.universalAccess", key],
            capture_output=True, text=True, timeout=_GSETTINGS_TIMEOUT, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.debug("defaults macOS %s indisponible : %s", key, exc)
        return None
    if result.returncode != 0:
        return None
    return _parse_bool(result.stdout)


def _windows_reduce_motion() -> bool | None:
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes
        enabled = wintypes.BOOL()
        ok = ctypes.windll.user32.SystemParametersInfoW(   # type: ignore[attr-defined]
            0x1042, 0, ctypes.byref(enabled), 0)            # SPI_GETCLIENTAREAANIMATION
    except (AttributeError, OSError):
        return None
    return (not bool(enabled.value)) if ok else None


def _windows_high_contrast() -> bool | None:
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class HighContrast(ctypes.Structure):
            _fields_ = (("cbSize", wintypes.UINT),
                        ("dwFlags", wintypes.DWORD),
                        ("lpszDefaultScheme", wintypes.LPWSTR))

        state = HighContrast()
        state.cbSize = ctypes.sizeof(state)
        ok = ctypes.windll.user32.SystemParametersInfoW(   # type: ignore[attr-defined]
            0x0042, state.cbSize, ctypes.byref(state), 0)  # SPI_GETHIGHCONTRAST
    except (AttributeError, OSError):
        return None
    return bool(state.dwFlags & 0x00000001) if ok else None   # HCF_HIGHCONTRASTON


def _windows_reduce_transparency() -> bool | None:
    if sys.platform != "win32":
        return None
    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as key:
            value, _kind = winreg.QueryValueEx(key, "EnableTransparency")
    except (ImportError, OSError):
        return None
    return not bool(value)


def color_scheme() -> str | None:
    """Thème clair/sombre du système, quand le bureau l'expose."""
    if sys.platform == "darwin":
        executable = shutil.which("defaults")
        if executable is None:
            return None
        try:
            result = subprocess.run(
                [executable, "read", "-g", "AppleInterfaceStyle"],
                capture_output=True, text=True, timeout=_GSETTINGS_TIMEOUT, check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if result.returncode == 0:
            return "dark" if "dark" in result.stdout.lower() else "light"
        return "light" if "does not exist" in result.stderr.lower() else None
    if sys.platform == "win32":
        try:
            import winreg
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
            ) as key:
                value, _kind = winreg.QueryValueEx(key, "AppsUseLightTheme")
        except (ImportError, OSError):
            return None
        return "light" if value else "dark"
    return _linux_color_scheme()


def reduce_motion() -> bool | None:
    """Animations réduites dans les réglages du système ?"""
    if sys.platform == "darwin":
        return _macos_value("reduceMotion")
    if sys.platform == "win32":
        return _windows_reduce_motion()
    return _linux_reduce_motion()


def reduce_transparency() -> bool | None:
    """Transparence réduite dans les réglages du système ?"""
    if sys.platform == "darwin":
        return _macos_value("reduceTransparency")
    if sys.platform == "win32":
        return _windows_reduce_transparency()
    return None                                  # pas de réglage GNOME/KDE équivalent


def high_contrast() -> bool | None:
    """Contraste élevé activé dans les réglages du système ?"""
    if sys.platform == "darwin":
        return _macos_value("increaseContrast")
    if sys.platform == "win32":
        return _windows_high_contrast()
    return _linux_high_contrast()
