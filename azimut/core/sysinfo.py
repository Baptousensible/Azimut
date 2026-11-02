"""Informations système légères : pression mémoire (psutil si présent, sinon /proc ou l'API Windows)."""
from __future__ import annotations

import sys
from typing import NamedTuple


class MemoryStatus(NamedTuple):
    used_pct: float             # RAM utilisée, en %
    available_pct: float        # RAM disponible, en % du total


def _via_psutil() -> MemoryStatus | None:
    try:
        import psutil
    except ImportError:
        return None
    vm = psutil.virtual_memory()
    if not vm.total:
        return None
    return MemoryStatus(float(vm.percent), vm.available / vm.total * 100.0)


def _via_proc() -> MemoryStatus | None:
    try:
        values: dict[str, int] = {}
        with open("/proc/meminfo", "r", encoding="ascii", errors="ignore") as fh:
            for line in fh:
                key, _, rest = line.partition(":")
                if key in ("MemTotal", "MemAvailable"):
                    values[key] = int(rest.split()[0])
        total, avail = values.get("MemTotal", 0), values.get("MemAvailable")
        if not total or avail is None:
            return None
        avail_pct = avail / total * 100.0
        return MemoryStatus(100.0 - avail_pct, avail_pct)
    except (OSError, ValueError, IndexError):
        return None


def _via_windows() -> MemoryStatus | None:
    try:
        import ctypes

        class _Mem(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

        st = _Mem()
        st.dwLength = ctypes.sizeof(_Mem)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):     # type: ignore[attr-defined]
            return None
        if not st.ullTotalPhys:
            return None
        return MemoryStatus(float(st.dwMemoryLoad), st.ullAvailPhys / st.ullTotalPhys * 100.0)
    except Exception:  # noqa: BLE001 - jamais bloquant
        return None


def memory_status() -> MemoryStatus | None:
    """Pression mémoire actuelle, ou None si elle ne peut pas être mesurée (le garde-fou est alors inactif)."""
    status = _via_psutil()
    if status is not None:
        return status
    if sys.platform == "win32":
        return _via_windows()
    return _via_proc()


def memory_ok(status: MemoryStatus | None, threshold_pct: float = 75.0, reserve_pct: float = 20.0) -> bool:
    """Vrai si l'on peut lancer un traitement de plus."""
    if status is None:
        return True
    return status.used_pct < threshold_pct and status.available_pct >= reserve_pct
