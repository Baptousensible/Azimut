# Correctifs du lot 2 (Convert, Dedup)

Validation : exécutable sans Qt = 374 tests réussis (les 2 échecs de la base de référence sont des artefacts de
l'environnement d'audit : sous-processus sans PySide6). Les tests Qt (`tests/ui/test_media_preview.py`) et les tests de
bout en bout avec le vrai worker (`test_audio_robustness.py`, `test_json_omission.py`) sont à lancer avec `pytest`
sur une machine disposant de PySide6, FFmpeg et ffprobe ; ils ont été validés ici avec un faux Qt fonctionnel.

## Convert

| Réf. | Défaut | Correctif |
|---|---|---|
| CV-001 | Un M4A dont la liste d'édition masque tous les échantillons ressortait « converti » avec un Opus vide de 283 octets (FFmpeg 6) ou échouait par « Could not open encoder before EOF » (FFmpeg 7). | Contrôle ffprobe de chaque sortie avant de la déclarer réussie ; 4ᵉ tentative audio avec `-ignore_editlist 1` (MP4/M4A uniquement : FFmpeg refuse cette option ailleurs). |
| CV-002 | L'audio n'avait qu'une tentative alors que la vidéo en avait trois. | Les modes de lecture tolérants s'appliquent aussi à l'audio. |
| CV-003 | Message d'erreur FFmpeg tronqué à ses 400 derniers caractères : la cause réelle (début) était perdue. | `stderr_digest` (début + fin, lignes répétées regroupées) et `failure_hint` (explication en clair). |
| CV-004 | Pas de progression pour l'audio ; fichier de 0 octet soumis à 3 tentatives. | Durée sondée pour tous les fichiers ; fichier vide signalé directement. |
| CV-005 | Les JSON Takeout copiés dans la sortie ne pouvaient pas être écartés. | Option « Ne pas recopier les JSON Takeout déjà réinjectés » (désactivée par défaut) : seuls les JSON compagnons exploitables d'un média de la file sont écartés, uniquement si ExifTool est disponible ; rien n'est jamais supprimé dans la source. |

## Dedup

| Réf. | Évolution | Détail |
|---|---|---|
| DD-001 | Lecteur audio et vidéo intégré | `MediaPreview` (image ou lecteur), curseur cliquable, volume et sourdine communs, un seul son à la fois, lecture conservée quand on bascule Garder/Supprimer, fichiers lâchés avant analyse, suppression, restauration et fermeture. |
| DD-002 | L'audio n'était pas un « média » | Les extensions audio entrent dans « Photos, vidéos et audios uniquement » (comparaison exacte uniquement). |
| DD-003 | Un lecteur en erreur restait bloqué pour les fichiers suivants (bouton Lecture désactivé) | Réactivé à chaque nouveau fichier (aussi dans Tags). |

## Compatibilité multi-systèmes (défauts de `COMPATIBILITE.md`)

| Réf. | Correctif | Vérification |
|---|---|---|
| AZ-C01 | `LSMinimumSystemVersion` relevé de 12.0 à 14.4 (limite réelle de PySide6/Qt 6.12) ; systèmes minimaux documentés dans `RELEASING.md`. | Lecture du paquet ; à confirmer par un build macOS. |
| AZ-C03 | Fenêtres de module et Atlas dimensionnées d'après la zone utile de l'écran (`azimut/core/screenfit.py`). | `tests/core/test_screenfit.py` (logique) ; rendu à vérifier sur un petit écran. |
| AZ-C04 | `platform_tag()` distingue `windows-arm64` et `linux-arm64` : plus d'exécutables x64 lancés sur ARM, repli sur FFmpeg/ExifTool du système. | `tests/core/test_binaries.py`. |
| AZ-C10 | Appariement média ↔ JSON Takeout insensible à la forme Unicode (NFC/NFD) : une photo « été.jpg » retrouvait son JSON seulement si les deux noms avaient la même forme. | `tests/takeout/test_unicode_names.py` (rouge sur l'original, vert maintenant). |

Volontairement non traités : AZ-C07 (chemins > 260 caractères sous Windows : exige un manifeste PyInstaller à valider
par un build Windows), AZ-C05 (AppImage glibc 2.34 : changement d'image CI), AZ-C11 (la casse n'est pas ignorée sous
macOS : un volume APFS sensible à la casse pourrait fusionner à tort deux fichiers distincts), AZ-C09, AZ-C13 à C18.

## Robustesse supplémentaire (noms de fichiers, formats, Windows)

| Réf. | Défaut constaté | Correctif | Vérification |
|---|---|---|---|
| CV-006 | Une image dont le nom (ou dont un dossier du chemin) contient un motif « %d », « %03d » échouait : FFmpeg le lit comme une suite d'images numérotées (« Could find no file with path… », « Could not open file… »). Touchait aussi l'aperçu FFmpeg des images. | Alias neutre de l'entrée (`core/ffmpeg_args.neutral_input`) ; nom temporaire sans « % » ; FFmpeg lancé dans le dossier de sortie avec un nom relatif quand ce dossier contient « % ». | `tests/convert/test_hostile_names.py` : rouge sur l'original, vert maintenant (WebP, PNG, JPEG ; les 9 profils image vérifiés à la main). |
| CV-007 | Nom temporaire de conversion (`.nom.<aléa>.part.ext`) dépassant 255 octets pour un fichier au nom déjà long (accents, caractères asiatiques). | Nom tronqué à un budget d'octets (UTF-8). | `tests/convert/test_hostile_names.py`. |
| CV-008 | Les sonneries `.m4r`, livres audio `.m4b` et autres conteneurs audio/vidéo courants étaient traités comme « autres fichiers » (copiés sans conversion). | Extensions ajoutées (`.m4r`, `.m4b`, `.oga`, `.caf`, `.ape`, `.rm`, `.dv`…). | `tests/convert/test_extensions.py` ; conversion réelle d'un `.m4r`. |
| FS-001 | Le parcours récursif suivait les jonctions NTFS (boucles possibles sous Windows). | `iter_files` ignore les jonctions (Python ≥ 3.12). | `tests/core/test_junctions.py`. |

Identifiés mais non corrigés (Scope) : clés de dossiers non normalisées en NFC (deux dossiers « Été » écrits
différemment apparaissent comme deux dossiers) ; dossier illisible signalé comme « absent » au lieu d'« inaccessible ».
