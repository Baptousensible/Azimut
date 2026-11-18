"""Tests du verrouillage des binaires tiers de packaging/fetch_binaries.py (PKG-001, lot 6b).

Le réseau est simulé : urllib.request.urlopen est remplacé et aucun test ne sort de la machine.
Les archives factices gardent la structure attendue par le script (bin/, lib/, perl/bin/perl).
"""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import tarfile
import urllib.error
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
LOCK_RELPATH = Path("packaging") / "binaries.lock.json"

BTBN_LATEST = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest"
FFMPEG_URL = ("https://github.com/BtbN/FFmpeg-Builds/releases/download/autobuild-2026-11-06-13-02/"
              "ffmpeg-n9.0-latest-linux64-gpl-9.0.tar.xz")
EXIFTOOL_URL = "https://github.com/exiftool/exiftool/archive/refs/tags/13.59.tar.gz"
PERL_URL = "https://github.com/skaji/relocatable-perl/releases/latest/download/perl-linux-amd64.tar.xz"
WIN_FFMPEG_URL = ("https://github.com/BtbN/FFmpeg-Builds/releases/download/autobuild-2026-11-06-13-02/"
                  "ffmpeg-n9.0-latest-win64-gpl-9.0.zip")
WIN_EXIFTOOL_URL = "https://sourceforge.net/projects/exiftool/files/exiftool-13.59_64.zip/download"


def _zip(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def _tar(members: dict[str, bytes], mode: str) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode=mode) as archive:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o755
            info.mtime = 0
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


FFMPEG_PAYLOAD = _tar({
    "ffmpeg-n9.0-latest-linux64-gpl-9.0/bin/ffmpeg": b"#!/bin/sh\n",
    "ffmpeg-n9.0-latest-linux64-gpl-9.0/bin/ffprobe": b"#!/bin/sh\n",
}, "w:xz")
EXIFTOOL_PAYLOAD = _tar({
    "Image-ExifTool-13.59/exiftool": b"#!/bin/sh\n",
    "Image-ExifTool-13.59/lib/Image/ExifTool.pm": b"package Image::ExifTool;\n",
}, "w:gz")
PERL_PAYLOAD = _tar({
    "perl-linux-amd64/bin/perl": b"#!/bin/sh\n",
    "perl-linux-amd64/lib/strict.pm": b"",
}, "w:xz")
WIN_FFMPEG_PAYLOAD = _zip({
    "ffmpeg-9.0-full_build/bin/ffmpeg.exe": b"MZ",
    "ffmpeg-9.0-full_build/bin/ffprobe.exe": b"MZ",
})
WIN_EXIFTOOL_PAYLOAD = _zip({
    "exiftool-13.59_64/exiftool(-k).exe": b"MZ",
    "exiftool-13.59_64/exiftool_files/exiftool.pl": b"# perl\n",
})


class FakeHTTPError(urllib.error.HTTPError):
    """HTTPError sans fichier temporaire implicite (sinon ResourceWarning sous -W error)."""

    def __init__(self, url: str, code: int = 404) -> None:
        Exception.__init__(self, f"HTTP {code}: Not Found")
        self.code = code
        self.msg = "Not Found"
        self.hdrs: dict = {}
        self.fp = None
        self.filename = url


class FakeResponse(io.BytesIO):
    """Réponse HTTP minimale : corps, en-têtes et URL finale."""

    def __init__(self, payload: bytes, url: str = "", content_length: int | None = None) -> None:
        super().__init__(payload)
        self.headers = {"Content-Length": str(len(payload) if content_length is None else content_length)}
        self._url = url

    def geturl(self) -> str:
        return self._url


@pytest.fixture()
def fb(monkeypatch, tmp_path):
    """Module rechargé pour chaque test : `sources` et `lock_note` repartent de zéro."""
    spec = importlib.util.spec_from_file_location("azimut_fetch_binaries_lock",
                                                  ROOT / "packaging" / "fetch_binaries.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "platform_tag", lambda: "linux-x64")
    monkeypatch.setattr(module, "FFMPEG_BRANCH", "n9.0")
    monkeypatch.delenv("AZIMUT_FFMPEG_BRANCH", raising=False)
    monkeypatch.delenv("AZIMUT_EXIFTOOL_VERSION", raising=False)
    return module


def serve(monkeypatch, module, payloads: dict[str, object]) -> list[str]:
    """Remplace urlopen : `payloads` associe une URL à son corps (ou à (corps, Content-Length))."""
    calls: list[str] = []

    def fake_urlopen(request, timeout=None):        # noqa: ARG001 - signature de urlopen
        url = getattr(request, "full_url", str(request))
        calls.append(url)
        value = payloads.get(url)
        if value is None:
            raise FakeHTTPError(url)
        if isinstance(value, tuple):
            data, declared = value
            return FakeResponse(data, url, declared)
        return FakeResponse(value, url)

    monkeypatch.setattr(module.urllib.request, "urlopen", fake_urlopen)
    return calls


def filename_of(url: str) -> str:
    """Dernier segment du chemin ; « /download » de SourceForge est ignoré (comme R6)."""
    segments = [segment for segment in url.split("/") if segment]
    return segments[-2] if segments[-1] == "download" else segments[-1]


def entry(platform: str, asset: str, url: str, payload: bytes, magic: str, version: str = "13.59") -> dict:
    return {
        "platform": platform,
        "asset": asset,
        "component": "ffmpeg" if asset in ("ffmpeg", "ffprobe") else asset,
        "version": version,
        "url": url,
        "filename": filename_of(url),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size": len(payload),
        "magic": magic,
    }


def build_lock(artifacts: list[dict]) -> dict:
    return {
        "schema": 1,
        "generated_at": "2026-11-08",
        "generator": "python packaging/fetch_binaries.py --update-lock",
        "exiftool_version": "13.59",
        "ffmpeg_branch": "n9.0",
        "artifacts": artifacts,
    }


def linux_lock() -> dict:
    return build_lock([
        entry("linux-x64", "ffmpeg", FFMPEG_URL, FFMPEG_PAYLOAD, "xz", "n9.0"),
        entry("linux-x64", "exiftool", EXIFTOOL_URL, EXIFTOOL_PAYLOAD, "gz"),
        entry("linux-x64", "perl", PERL_URL, PERL_PAYLOAD, "xz", "latest"),
    ])


def linux_payloads() -> dict[str, bytes]:
    return {FFMPEG_URL: FFMPEG_PAYLOAD, EXIFTOOL_URL: EXIFTOOL_PAYLOAD, PERL_URL: PERL_PAYLOAD}


def windows_lock(with_perl: bool = False) -> dict:
    artifacts = [
        entry("windows-x64", "ffmpeg", WIN_FFMPEG_URL, WIN_FFMPEG_PAYLOAD, "zip", "n9.0"),
        entry("windows-x64", "exiftool", WIN_EXIFTOOL_URL, WIN_EXIFTOOL_PAYLOAD, "zip"),
    ]
    if with_perl:
        artifacts.append(entry("windows-x64", "perl", PERL_URL, PERL_PAYLOAD, "xz", "latest"))
    return build_lock(artifacts)


def write_lock(tmp_path: Path, lock: dict) -> Path:
    path = tmp_path / LOCK_RELPATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")
    return path


# ── comportement du mode verrouillé ─────────────────────────────────────────────


def test_missing_lock_fails_before_any_network(fb, monkeypatch, capsys):
    calls = serve(monkeypatch, fb, {})
    assert fb.main(["--no-selftest"]) == 2
    assert calls == []
    assert "verrou absent" in capsys.readouterr().out


def test_correct_digest_installs_and_records_the_lock(fb, monkeypatch, tmp_path):
    write_lock(tmp_path, linux_lock())
    calls = serve(monkeypatch, fb, linux_payloads())
    assert fb.main(["--no-selftest"]) == 0
    dest = tmp_path / "bin" / "linux-x64"
    for name in ("ffmpeg", "ffprobe", "exiftool", "exiftool-perl/lib", "perl/bin/perl"):
        assert (dest / name).exists(), name
    sources = (dest / "BINARIES-SOURCES.txt").read_text(encoding="utf-8")
    assert "verrou : binaries.lock.json, schéma 1, 2026-11-08" in sources
    assert sorted(calls) == sorted(linux_payloads())


def test_wrong_digest_stops_and_installs_nothing(fb, monkeypatch, tmp_path, capsys):
    lock = linux_lock()
    lock["artifacts"][2]["sha256"] = "0" * 64                   # perl : dernière entrée installée
    write_lock(tmp_path, lock)
    calls = serve(monkeypatch, fb, linux_payloads())
    assert fb.main(["--no-selftest"]) == 3
    out = capsys.readouterr().out
    assert "empreinte SHA-256 différente" in out
    assert "Rien n'a été installé" in out
    assert not (tmp_path / "bin" / "linux-x64").exists()
    assert calls == [EXIFTOOL_URL, FFMPEG_URL, PERL_URL]        # une URL par entrée, aucun repli


def test_missing_entry_for_the_platform_is_refused(fb, monkeypatch, tmp_path, capsys):
    lock = linux_lock()
    lock["artifacts"] = [item for item in lock["artifacts"] if item["asset"] != "perl"]
    write_lock(tmp_path, lock)
    calls = serve(monkeypatch, fb, linux_payloads())
    assert fb.main(["--no-selftest"]) == 2
    assert calls == []
    assert "couverture de linux-x64" in capsys.readouterr().out


def test_unavailable_upstream_checksum_list_cannot_weaken_the_lock(fb, monkeypatch, tmp_path):
    """PKG-001 : une liste de sommes amont absente (404 ici) ne change plus rien, le verrou tranche."""
    write_lock(tmp_path, linux_lock())
    calls = serve(monkeypatch, fb, linux_payloads())
    assert fb.main(["--no-selftest"]) == 0
    assert not [url for url in calls if "checksums" in url]


def test_size_mismatch_is_reported_before_reading(fb, monkeypatch, tmp_path, capsys):
    write_lock(tmp_path, linux_lock())
    payloads = linux_payloads()
    payloads[EXIFTOOL_URL] = (EXIFTOOL_PAYLOAD, len(EXIFTOOL_PAYLOAD) + 7)
    serve(monkeypatch, fb, payloads)
    assert fb.main(["--no-selftest"]) == 3
    out = capsys.readouterr().out
    assert "taille différente" in out
    assert "empreinte SHA-256 différente" not in out
    assert not (tmp_path / "bin" / "linux-x64").exists()


def test_conflicting_exiftool_version_is_refused(fb, monkeypatch, tmp_path, capsys):
    write_lock(tmp_path, linux_lock())
    monkeypatch.setenv("AZIMUT_EXIFTOOL_VERSION", "13.60")
    calls = serve(monkeypatch, fb, linux_payloads())
    assert fb.main(["--no-selftest"]) == 2
    assert calls == []
    assert "AZIMUT_EXIFTOOL_VERSION" in capsys.readouterr().out


def test_deprecated_no_verify_still_checks_digests(fb, monkeypatch, tmp_path, capsys):
    lock = linux_lock()
    lock["artifacts"][2]["sha256"] = "0" * 64
    write_lock(tmp_path, lock)
    serve(monkeypatch, fb, linux_payloads())
    assert fb.main(["--no-verify"]) == 3
    assert "--no-verify est déprécié" in capsys.readouterr().out


def test_allow_unpinned_succeeds_and_says_the_lock_was_ignored(fb, monkeypatch, tmp_path):
    monkeypatch.setenv("AZIMUT_EXIFTOOL_VERSION", "13.59")
    payloads = linux_payloads()
    payloads[f"{fb.BTBN_BASE}/ffmpeg-n9.0-latest-linux64-gpl-9.0.tar.xz"] = FFMPEG_PAYLOAD
    serve(monkeypatch, fb, payloads)                           # aucun verrou écrit : il n'est pas lu
    assert fb.main(["--no-selftest", "--allow-unpinned"]) == 0
    sources = (tmp_path / "bin" / "linux-x64" / "BINARIES-SOURCES.txt").read_text(encoding="utf-8")
    assert "verrou : IGNORÉ (--allow-unpinned)" in sources


# ── validation du fichier ───────────────────────────────────────────────────────


@pytest.mark.parametrize("schema", [0, 2, "1", True, None])
def test_schema_must_be_exactly_1(fb, tmp_path, schema):
    lock = linux_lock()
    lock["schema"] = schema
    with pytest.raises(fb.LockError, match="schéma"):
        fb.load_lock(write_lock(tmp_path, lock))


@pytest.mark.parametrize("digest", ["", None, "A" * 64, "0" * 63, "0" * 65, "g" + "0" * 63])
def test_sha256_must_be_64_lowercase_hex(fb, tmp_path, digest):
    lock = linux_lock()
    lock["artifacts"][1]["sha256"] = digest
    with pytest.raises(fb.LockError, match="sha256"):
        fb.load_lock(write_lock(tmp_path, lock))


@pytest.mark.parametrize("field,value,message", [
    ("asset", "ffmpg", "asset inconnu"),
    ("filename", "autre.tar.xz", "filename"),
    ("magic", "rar", "magic"),
    ("url", "http://example.invalid/x.tar.xz", "https"),
])
def test_invalid_entry_is_refused(fb, tmp_path, field, value, message):
    lock = linux_lock()
    lock["artifacts"][1][field] = value
    with pytest.raises(fb.LockError, match=message):
        fb.load_lock(write_lock(tmp_path, lock))


def test_duplicate_platform_asset_pair_is_refused(fb, tmp_path):
    lock = linux_lock()
    lock["artifacts"].append(json.loads(json.dumps(lock["artifacts"][0])))
    with pytest.raises(fb.LockError, match="double"):
        fb.load_lock(write_lock(tmp_path, lock))


def test_exiftool_versions_must_agree(fb, tmp_path):
    lock = linux_lock()
    lock["artifacts"][1]["version"] = "13.60"
    with pytest.raises(fb.LockError, match="exiftool_version"):
        fb.load_lock(write_lock(tmp_path, lock))


def test_macos_requires_both_archives(fb):
    with pytest.raises(fb.LockError, match="couverture"):
        fb.platform_entries({"artifacts": [{"platform": "macos-arm64", "asset": "ffmpeg"}]}, "macos-arm64")


def test_windows_without_perl_is_accepted(fb, tmp_path):
    lock = fb.load_lock(write_lock(tmp_path, windows_lock()))
    assert [item["asset"] for item in fb.platform_entries(lock, "windows-x64")] == ["exiftool", "ffmpeg"]


def test_windows_perl_entry_is_refused(fb, tmp_path):
    with pytest.raises(fb.LockError, match="asset"):
        fb.load_lock(write_lock(tmp_path, windows_lock(with_perl=True)))


def test_moving_url_requires_flag_and_note(fb, tmp_path):
    lock = linux_lock()
    moving = f"{BTBN_LATEST}/ffmpeg-n9.0-latest-linux64-gpl-9.0.tar.xz"
    lock["artifacts"][0].update({"url": moving, "filename": filename_of(moving)})
    path = write_lock(tmp_path, lock)
    with pytest.raises(fb.LockError, match="latest"):
        fb.load_lock(path)
    lock["artifacts"][0].update({"url_immutable": False, "note": "Aucun chemin versionné connu."})
    fb.load_lock(write_lock(tmp_path, lock))


# ── --update-lock ──────────────────────────────────────────────────────────────


def test_update_lock_writes_a_sorted_lock_then_leaves_it_untouched(fb, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AZIMUT_EXIFTOOL_VERSION", "13.59")
    payloads = linux_payloads()
    payloads[f"{fb.BTBN_BASE}/ffmpeg-n9.0-latest-linux64-gpl-9.0.tar.xz"] = FFMPEG_PAYLOAD
    serve(monkeypatch, fb, payloads)
    assert fb.main(["--update-lock", "--platform", "linux-x64"]) == 0
    path = tmp_path / LOCK_RELPATH
    lock = json.loads(path.read_text(encoding="utf-8"))
    assert lock["schema"] == 1
    assert lock["exiftool_version"] == "13.59"
    assert [(item["platform"], item["asset"]) for item in lock["artifacts"]] == [
        ("linux-x64", "exiftool"), ("linux-x64", "ffmpeg"), ("linux-x64", "perl")]
    ffmpeg_entry, perl_entry = lock["artifacts"][1], lock["artifacts"][2]
    assert ffmpeg_entry["sha256"] == hashlib.sha256(FFMPEG_PAYLOAD).hexdigest()
    assert ffmpeg_entry["size"] == len(FFMPEG_PAYLOAD)
    assert ffmpeg_entry["url_immutable"] is False and ffmpeg_entry["note"]
    assert perl_entry["url_immutable"] is False                # relocatable-perl : URL « latest »

    lock["generated_at"] = "2000-01-01"                        # preuve de non-réécriture
    path.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")
    assert fb.main(["--update-lock", "--platform", "linux-x64"]) == 0
    assert "Verrou inchangé" in capsys.readouterr().out
    assert json.loads(path.read_text(encoding="utf-8"))["generated_at"] == "2000-01-01"


def test_update_lock_refuses_allow_unpinned(fb, monkeypatch):
    serve(monkeypatch, fb, {})
    assert fb.main(["--update-lock", "--allow-unpinned"]) == 2
