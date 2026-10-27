#!/usr/bin/env python3
"""Vérifie qu'un paquet Azimut est réellement « clé en main » à partir du rapport `--selftest`.

Usage : python packaging/check_selftest.py selftest.json

Échec (code 1) si :
  - l'auto-test signale un problème ou n'a pas été lancé depuis un paquet figé ;
  - FFmpeg, FFprobe ou ExifTool ne viennent pas du paquet (source ≠ « bundled ») ou ne s'exécutent pas ;
  - HEIC (pillow-heif), AVIF (écriture) ou la corbeille (send2trash) manquent.

Simple avertissement (annotation GitHub) si un encodeur FFmpeg utilisé par les profils de Convert manque :
Azimut gère ce cas (« encodeur indisponible »), mais le paquet serait moins complet.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

# Encodeurs cités par azimut/modules/convert/profiles.py (AV1 : l'un OU l'autre).
REQUIRED_ENCODERS = ("libx264", "aac", "libmp3lame", "libopus", "flac", "mjpeg", "libwebp")
AV1_ENCODERS = ("libsvtav1", "libaom-av1")


def annotate(level: str, message: str) -> None:
    print(f"::{level}::{message}")          # annotation GitHub Actions (simple texte ailleurs)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    report = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    errors: list[str] = []

    if not report.get("ok"):
        errors += [f"auto-test : {p}" for p in report.get("problems", ["échec sans détail"])]
    if not report.get("frozen"):
        errors.append("l'auto-test n'a pas été exécuté depuis un paquet figé")

    items = report.get("binaries", {}).get("items", {})
    for name in ("ffmpeg", "ffprobe", "exiftool"):
        item = items.get(name)
        if not item or not item.get("available"):
            errors.append(f"{name} : introuvable")
        elif item.get("source") != "bundled":
            errors.append(f"{name} : vient de « {item.get('source')} » et non du paquet")
        elif not item.get("runs"):
            errors.append(f"{name} : présent mais ne s'exécute pas ({item.get('path')})")
        else:
            print(f"OK  {name} {item.get('version')} (embarqué)")

    pillow = report.get("pillow", {})
    if not pillow.get("heif"):
        errors.append("HEIC/HEIF indisponible (pillow-heif absent du paquet)")
    if not pillow.get("avif_save"):
        errors.append("écriture AVIF indisponible (Pillow ≥ 11.3 requis)")
    if not report.get("send2trash", {}).get("ok"):
        errors.append("send2trash absent du paquet (corbeille système)")
    for module_id, entry in report.get("modules", {}).get("modules", {}).items():
        print(f"{'OK ' if entry.get('ok') else 'KO '} module {module_id}")

    ffmpeg = items.get("ffmpeg", {})
    if ffmpeg.get("runs") and ffmpeg.get("path"):
        try:
            out = subprocess.run([ffmpeg["path"], "-hide_banner", "-encoders"],
                                 capture_output=True, text=True, timeout=60).stdout
            have = {line.split()[1] for line in out.splitlines() if len(line.split()) > 1}
            missing = [e for e in REQUIRED_ENCODERS if e not in have]
            if not any(e in have for e in AV1_ENCODERS):
                missing.append(" ou ".join(AV1_ENCODERS))
            if missing:
                annotate("warning", "Encodeurs FFmpeg absents du paquet : " + ", ".join(missing))
            else:
                print("OK  encodeurs FFmpeg attendus par les profils de Convert")
        except Exception as exc:  # noqa: BLE001
            annotate("warning", f"Liste des encodeurs FFmpeg illisible : {exc}")

    if errors:
        for message in errors:
            annotate("error", message)
        print(f"\n{len(errors)} problème(s) : le paquet n'est pas clé en main.")
        return 1
    print("\nPaquet conforme : tout est embarqué et s'exécute.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
