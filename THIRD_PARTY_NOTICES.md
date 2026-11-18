# Composants tiers (ébauche à valider par le propriétaire)

> Ce fichier est une **ébauche** rédigée pendant l'audit (constat H-04). Les licences ci-dessous sont celles que ces
> projets annoncent publiquement ; elles n'ont pas pu être revérifiées hors ligne et ne constituent pas un avis
> juridique. Le choix de la licence d'Azimut lui-même reste à faire (aucun fichier `LICENSE` n'existe).

| Composant | Rôle | Licence annoncée | Remarque |
|---|---|---|---|
| Qt / PySide6 (Qt for Python) | Interface | LGPL v3 (ou GPL v3 / commerciale) | Respecter les obligations LGPL (relink, texte de licence) pour le paquet distribué. |
| Pillow | Lecture/écriture d'images | MIT-CMU (HPND) | |
| pillow-heif (facultatif) | HEIC/HEIF | BSD-3-Clause | embarque libheif (LGPL v3) |
| rawpy (facultatif) | RAW | MIT | embarque LibRaw (LGPL v2.1 ou CDDL v1.0) |
| send2trash (facultatif) | Corbeille système | BSD-3-Clause | |
| psutil (facultatif) | Garde-fou mémoire | BSD-3-Clause | |
| PyInstaller (construction) | Paquets | GPL v2+ avec exception de distribution | L'exception autorise la redistribution du paquet sous une autre licence. |
| FFmpeg (binaire embarqué) | Conversion audio/vidéo/image | **GPL** (variante GPL des builds BtbN ; builds macOS Martin Riedl à vérifier) | `BUILD.md` écrit « LGPL ou GPL » : le binaire réellement embarqué est en variante GPL. |
| ExifTool | Métadonnées | Licence Perl (Artistic ou GPL) | |
| Perl portable (relocatable-perl) | Exécute ExifTool sous Linux/macOS | Licence Perl (Artistic ou GPL) | |
| Papicons | Icônes de l'interface (thème historique) | MIT (Association Papillon) | Voir `azimut/resources/icons/`. |
| lucide-animated (pqoqubbw/icons) | Icônes de l'interface (thème « Lucide Animées ») | MIT | Voir `azimut/resources/icons_animated/`, notamment `LICENSE-lucide-animated.txt` livré avec l'application. |
| Montserrat | Police du lettrage de l'en-tête | SIL Open Font License 1.1 | Police et licence dans `azimut/resources/fonts/`. |

Les sources et empreintes des binaires embarqués sont listées dans `BINARIES-SOURCES.txt`, généré par
`packaging/fetch_binaries.py` à la construction du paquet.

Le texte intégral de la licence MIT de lucide-animated est livré dans
`azimut/resources/icons_animated/LICENSE-lucide-animated.txt` ; ce fichier est embarqué par
PyInstaller et repris dans les données du paquet Python. Les builds PyInstaller copient également
ce présent fichier à la racine du produit, et les paquets Debian/RPM l'installent dans
`/usr/share/doc/azimut`.
