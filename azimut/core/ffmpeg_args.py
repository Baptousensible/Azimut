"""Options d'entrée sûres pour FFmpeg / FFprobe (défense en profondeur).

FFmpeg choisit son décodeur d'après le **contenu**, pas l'extension : un fichier piégé (export Takeout,
téléchargement, clé USB) peut être une playlist ou un conteneur qui référence le réseau (``http``, ``tcp``,
``concat``…) ou d'autres fichiers locaux. On restreint donc les protocoles d'entrée aux fichiers locaux et on
préfixe chaque chemin par ``file:`` pour qu'un nom ne soit jamais interprété comme un protocole.

Toute commande qui ouvre un média non fiable doit insérer ``SAFE_INPUT_FLAGS`` **avant** ``-i`` et passer le
chemin par ``input_spec()``.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

PROTOCOL_WHITELIST = "file"
SAFE_INPUT_FLAGS: tuple[str, ...] = ("-protocol_whitelist", PROTOCOL_WHITELIST)


def input_spec(source: str | Path) -> str:
    """Chemin d'entrée explicite ``file:<chemin absolu>`` (jamais pris pour un protocole)."""
    return "file:" + os.path.abspath(str(source))


def neutral_input(src: str | Path) -> tuple[Path, Path | None]:
    """(chemin à donner à FFmpeg, dossier temporaire à supprimer ensuite ou None).

    Le lecteur d'images de FFmpeg (`image2`) interprète « %d », « %03d »… dans tout le chemin comme une suite
    d'images numérotées : « photo%03d.jpg », ou un dossier « 100%d » dans le chemin, donnaient « Could find no
    file with path ». Un chemin sans « % » est passé tel quel ; sinon un lien au nom neutre est créé dans un
    dossier temporaire, à supprimer avec `drop_neutral_input` : lien physique, sinon lien symbolique (autre volume),
    et la copie seulement en dernier recours (FAT, partage réseau, Windows sans privilège) car elle coûte la taille
    du fichier : une vidéo de plusieurs Go ne doit pas être recopiée pour un simple aperçu. La source n'est jamais
    modifiée.
    """
    src = Path(src)
    if "%" not in str(src):
        return src, None
    folder = Path(tempfile.mkdtemp(prefix="azimut_in_"))
    alias = folder / f"input{src.suffix.lower()}"
    try:
        try:
            os.link(src, alias)
        except OSError:                                   # autre volume, FAT, partage réseau
            try:
                os.symlink(os.path.abspath(src), alias)   # sans recopier le contenu
            except (OSError, NotImplementedError):
                shutil.copyfile(src, alias)
    except OSError:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    return alias, folder


def drop_neutral_input(folder: Path | None) -> None:
    """Supprime le dossier temporaire de `neutral_input` (sans effet si None)."""
    if folder is not None:
        shutil.rmtree(folder, ignore_errors=True)
