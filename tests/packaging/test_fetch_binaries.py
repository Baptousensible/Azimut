"""Tests de packaging/fetch_binaries.py : extraction des archives téléchargées."""
from __future__ import annotations

import importlib.util
import io
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load_fetch_binaries():
    """Charge packaging/fetch_binaries.py (le paquet PyPI « packaging » occupe déjà ce nom)."""
    name = "azimut_fetch_binaries"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / "packaging" / "fetch_binaries.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def add_file(tf: tarfile.TarFile, name: str, data: bytes = b"x") -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    tf.addfile(info, io.BytesIO(data))


def test_extract_tar_without_data_filter_rejects_unsafe_members(tmp_path, monkeypatch):
    """PKG-002 : sans tarfile.data_filter, seuls les membres sûrs doivent être extraits."""
    module = load_fetch_binaries()
    archive = tmp_path / "a.tar"
    with tarfile.open(archive, "w") as tf:
        add_file(tf, "ok.txt", b"ok")
        add_file(tf, "../evil.txt")
        add_file(tf, str(tmp_path / "abs.txt"))
        link = tarfile.TarInfo("escape")
        link.type = tarfile.SYMTYPE
        link.linkname = "../evil.txt"
        tf.addfile(link)

    calls: list[tuple[Path, list[str] | None]] = []

    def fake_extractall(self, path=None, members=None, *, filter=None):
        calls.append((Path(path), None if members is None else [m.name for m in members]))

    monkeypatch.delattr(tarfile, "data_filter", raising=False)    # simule Python 3.11.0–3.11.3
    monkeypatch.setattr(tarfile.TarFile, "extractall", fake_extractall)
    target = tmp_path / "out"
    target.mkdir()
    module.extract_tar(archive, target)

    assert calls == [(target, ["ok.txt"])]
