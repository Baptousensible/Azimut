# Azimut

**Azimut** est une suite d'applications de bureau (Python, PySide6 / Qt) pour traiter des
fichiers photo, vidéo, audio et JSON. Le nom vient de l'angle de visée en topographie :
la précision des outils, la direction donnée aux fichiers.

Tous les traitements lourds s'exécutent en arrière-plan : l'interface ne se fige jamais et
chaque opération peut être annulée proprement.

## Atlas, le lanceur

**Atlas** est le recueil des utilitaires : une carte par module, avec son état (binaires requis
détectés ou non) et un bouton « Ouvrir ». On peut aussi **glisser-déposer** des fichiers ou un
dossier sur une carte pour ouvrir le module avec ces éléments. Sous les cartes, un tableau de bord
propose l'historique des traitements, le journal de l'application et l'état des binaires.

## Les modules

| Module | Rôle |
|---|---|
| **Azimut Convert** (`convert`) | Conversion audio, vidéo et images via FFmpeg, par lots, avec des profils prédéfinis. |
| **Azimut Tags** (`tags`) | Lecture, modification et suppression des métadonnées (EXIF, IPTC, XMP) via ExifTool. |
| **Azimut Takeout** (`takeout`) | Réinjecte dates et positions GPS d'un export Google Photos (JSON compagnons) dans les photos et vidéos, avec annulation. |
| **Azimut Dedup** (`dedup`) | Détection et suppression des doublons : hachage du contenu, similarité visuelle, Live Photos (photo + courte vidéo), fichiers modifiés entre deux dossiers, comparaison par prévisualisation. |
| **Azimut Press** (`press`) | Compression et optimisation d'images et de fichiers JSON. |
| **Azimut Rename** (`rename`) | Inscrit la date de prise de vue (lue dans les métadonnées) dans le nom des photos, vidéos et fichiers audio, avec confirmation détaillée et annulation. |

Chaque module fonctionne seul (`python -m azimut.modules.<id>`) ou depuis Atlas.

## Installation (développement)

Prérequis : Python ≥ 3.10.

```bash
python -m venv .venv
source .venv/bin/activate          # Windows : .venv\Scripts\Activate.ps1
pip install -r requirements.txt    # dépendances de l'application
pip install -r requirements-dev.txt  # outils de test, si présent
```

**FFmpeg, FFprobe et ExifTool** sont localisés automatiquement : dossier `bin/<plateforme>/`,
puis chemin défini dans « Préférences… », puis `PATH` du système (voir `bin/README.md`).

## Lancement

```bash
python main.py                        # ouvre Atlas
python main.py convert photo.heic     # lance directement un module avec des chemins
python main.py --list                 # liste les modules (id, nom, binaires requis)
python main.py --version              # affiche la version
python -m azimut                      # équivalent de python main.py
python -m azimut.modules.tags         # un module seul
```

## Tests

```bash
pytest -q
```

Les tests s'exécutent sans interface visible (`QT_QPA_PLATFORM=offscreen`), sans FFmpeg ni
ExifTool réels et sans réseau.

## Structure du dépôt

```
main.py                    point d'entrée (Atlas)
azimut/
  core/                    socle : chemins, préférences, journaux, binaires, historique, workers
  ui/                      thème, widgets, fenêtre de module, préférences
  launcher/                Atlas : registry, cli, atlas_window, dashboard
  modules/                 convert, tags, takeout, dedup, press, rename
  resources/               ressources embarquées
bin/                       binaires par plateforme (README.md seulement dans le dépôt)
packaging/                 spec PyInstaller, script de build, notes Nuitka
tests/                     tests par composant (core, ui, launcher, modules)
```

Les préférences, l'historique et les journaux sont écrits dans les dossiers utilisateur
(jamais à côté de l'exécutable).

## Construire une distribution

Voir [BUILD.md](BUILD.md) (PyInstaller, tailles, licences).
