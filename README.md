# Azimut

**Azimut** est une suite d'applications de bureau (Python, PySide6 / Qt) pour traiter des
fichiers photo, vidéo et audio. Le nom vient de l'angle de visée en topographie :
la précision des outils, la direction donnée aux fichiers.

Tous les traitements lourds s'exécutent en arrière-plan : l'interface ne se fige jamais et
chaque opération peut être annulée proprement.

## Azimut, le lanceur

**Azimut** est le couteau suisse pour convertir, dédoublonner et ranger vos médias. Sa première page regroupe une carte par module, avec son état
(binaires requis détectés ou non) et un bouton « Ouvrir » ; on peut aussi **glisser-déposer** des fichiers
ou un dossier sur une carte pour ouvrir le module avec ces éléments. Le bouton « Journaux et historique »
ouvre la seconde page, qui rassemble l'historique des traitements, le journal de l'application et l'état
des binaires.

## Les modules

| Module | Rôle |
|---|---|
| **Takeout** (`takeout`) | Réinjecte dates et positions GPS d'un export Google Photos (JSON compagnons) dans les photos et vidéos, avec annulation. |
| **Dedup** (`dedup`) | Détection et suppression des doublons : hachage du contenu, similarité visuelle, Live Photos (photo + courte vidéo), fichiers modifiés entre deux dossiers, comparaison par prévisualisation avec **lecteur audio et vidéo intégré** (lecture, déplacement, volume, sans ouvrir d'application externe). |
| **Convert** (`convert`) | Conversion audio, vidéo et images via FFmpeg, par lots. Format de sortie au choix par famille (AVIF, WebP, H.264, Opus, FLAC…), qualité réglée à la main par curseurs (CRF, preset, débit). Chaque fichier produit est contrôlé (une sortie vide n'est jamais comptée comme réussie) ; option pour ne pas recopier les JSON Takeout déjà réinjectés. |
| **Rename** (`rename`) | Inscrit la date de prise de vue (lue dans les métadonnées) dans le nom des photos, vidéos et fichiers audio, avec confirmation détaillée et annulation. |
| **Scope** (`scope`) | Compare deux dossiers (arborescence et nombre de fichiers par dossier), en lecture seule : vérifie qu'une copie ou une conversion est complète. |
| **Tags** (`tags`) | Lecture, modification et suppression des métadonnées (EXIF, IPTC, XMP) via ExifTool. |

L'interface est livrée en 27 langues (catalogues Qt, français = langue source ; voir `tools/update_translations.py`).

Chaque module fonctionne seul (`python -m azimut.modules.<id>`) ou depuis Azimut.

## Manuel utilisateur

Le [manuel utilisateur](MANUEL.md) décrit le lancement, les modules et les trois modes de
suppression de Dedup (quarantaine, corbeille, suppression définitive).

## Installation (développement)

Prérequis : Python ≥ 3.11.

```bash
python -m venv .venv
source .venv/bin/activate          # Windows : .venv\Scripts\Activate.ps1
pip install -r requirements.txt    # dépendances de l'application
pip install -r requirements-dev.txt  # outils de test (pytest)
```

Facultatifs (fonctions supplémentaires si installés) : `pillow-heif` (HEIC/HEIF), `rawpy` (RAW),
`send2trash` (corbeille système pour Dedup), `psutil` (garde-fou mémoire de Convert).

**FFmpeg, FFprobe et ExifTool** sont localisés automatiquement : dossier `bin/<plateforme>/`,
puis chemin défini dans « Préférences… », puis `PATH` du système (voir `bin/README.md`).

## Distributions toutes prêtes

Les releases GitHub proposent, en plus des paquets Windows et macOS :

- **Linux — AppImage** : `Azimut-…-linux-x86_64.AppImage`, sans installation ;
- **Linux — Debian/Ubuntu/Mint** : `Azimut-…-linux-x86_64.deb`
  (`sudo apt install ./Azimut-*.deb`) ;
- **Linux — Fedora/RHEL 10+/openSUSE** : `Azimut-…-linux-x86_64.rpm`
  (`sudo dnf install ./Azimut-*.rpm`) ;
- **Linux — portable** : `Azimut-…-linux-x86_64.tar.gz`, pour les autres distributions.

FFmpeg, FFprobe et ExifTool sont inclus dans tous ces paquets. La construction est décrite dans
[BUILD.md](BUILD.md) et la publication dans [RELEASING.md](RELEASING.md).

## Lancement

```bash
python main.py                        # ouvre Azimut
python main.py convert photo.heic     # lance directement un module avec des chemins
python main.py --list                 # liste les modules (id, nom, binaires requis)
python main.py --version              # affiche la version
python main.py --selftest [rapport]   # vérifie l'installation (code 0 si tout est en ordre)
python -m azimut                      # équivalent de python main.py
python -m azimut.modules.tags         # un module seul
```

## Tests

```bash
pytest -q
```

Les tests s'exécutent sans interface visible (`QT_QPA_PLATFORM=offscreen`) et sans réseau. Sans FFmpeg
ni ExifTool, les tests de bout en bout qui en ont besoin sont ignorés ; installez-les pour tout exécuter.

## Structure du dépôt

```
main.py                    point d'entrée (Azimut)
MANUEL.md                  manuel utilisateur
azimut/
  core/                    socle : chemins, préférences, journaux, binaires, historique, workers
  ui/                      thème, widgets, fenêtre de module, préférences
  launcher/                Azimut : registry, cli, atlas_window, dashboard
  modules/                 takeout, dedup, convert, rename, scope, tags
  resources/               ressources embarquées
bin/                       binaires par plateforme (README.md seulement dans le dépôt)
packaging/                 spec PyInstaller, script de build, notes Nuitka
tests/                     tests par composant (core, ui, launcher, modules)
```

Les préférences, l'historique et les journaux sont écrits dans les dossiers utilisateur
(jamais à côté de l'exécutable).

## Compatibilité

Windows, macOS et Linux (Wayland et Xorg, environnements de bureau) : la matrice de support,
les limites connues et le protocole de vérification sont dans [COMPATIBILITE.md](COMPATIBILITE.md).

## Construire une distribution

Voir [BUILD.md](BUILD.md) (PyInstaller, tailles, licences).

## Crédits

Les icônes de l'interface viennent de [Papicons](https://github.com/PapillonApp/Papicons)
(Association Papillon, licence MIT) et de [lucide-animated](https://github.com/pqoqubbw/icons)
(licence MIT) ; voir `azimut/resources/icons/` et `azimut/resources/icons_animated/`.
