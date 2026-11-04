# Azimut

**Azimut** est une suite d'applications de bureau (Python, PySide6 / Qt) pour traiter des
fichiers photo, vidéo, audio et JSON. Le nom vient de l'angle de visée en topographie :
la précision des outils, la direction donnée aux fichiers.

Tous les traitements lourds s'exécutent en arrière-plan : l'interface ne se fige jamais et
chaque opération peut être annulée proprement.

## Atlas, le lanceur

**Atlas** (version 0.2.0) est le recueil des utilitaires. Sa fenêtre s'organise ainsi :

- une **barre latérale** : recherche, Accueil, Activité, un bouton par outil (les modules à venir,
  Azimut Sync et Azimut Watch, sont marqués « bientôt ») ;
- un **accueil** : une zone de **dépôt intelligent** (déposez des fichiers ou des dossiers, Atlas
  propose les outils adaptés : photos et vidéos → Convert, export Google Photos → Takeout, deux
  dossiers → Scope…), la liste « Reprendre » des derniers éléments ouverts, et toutes les cartes
  d'outils avec leur état (binaires requis détectés ou non) ; les trois outils les plus utilisés
  portent la pastille « Recommandé » ;
- une page **Activité** : historique des traitements, journal de l'application, état des binaires ;
- une **palette de commandes** (`Ctrl+K`) : tapez « doublons », « thème », « convert »… pour lancer
  un outil ou une action.

On peut toujours glisser-déposer des fichiers directement sur une carte pour ouvrir le module avec
ces éléments.

## Interface des outils

Tous les outils partagent la même organisation : en-tête compact, barre d'outils, panneau de
réglages à droite (qui défile quand la fenêtre est basse), journal discret en bas (il s'agrandit
pendant un traitement). Les réglages numériques (CRF, qualité, threads, fichiers en parallèle,
tolérance et durée de Dedup…) se règlent avec des **curseurs** accompagnés d'une valeur modifiable
au clavier ; les sections longues (options de Dedup) se replient.

## Les modules

| Module | Rôle |
|---|---|
| **Azimut Convert** (`convert`) | Conversion audio, vidéo et images via FFmpeg, par lots, avec des profils prédéfinis. |
| **Azimut Tags** (`tags`) | Lecture, modification et suppression des métadonnées (EXIF, IPTC, XMP) via ExifTool. |
| **Azimut Takeout** (`takeout`) | Réinjecte dates et positions GPS d'un export Google Photos (JSON compagnons) dans les photos et vidéos, avec annulation. |
| **Azimut Dedup** (`dedup`) | Détection et suppression des doublons : hachage du contenu, similarité visuelle, Live Photos (photo + courte vidéo), fichiers modifiés entre deux dossiers, comparaison par prévisualisation. |
| **Azimut Press** (`press`) | Compression et optimisation d'images et de fichiers JSON. |
| **Azimut Scope** (`scope`) | Compare deux dossiers (arborescence et nombre de fichiers par dossier), en lecture seule : vérifie qu'une copie ou une conversion est complète. |
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
  launcher/                Atlas : registry, cli, atlas_window, dashboard, routing (dépôt intelligent),
                           palette (Ctrl+K), recents
  modules/                 convert, tags, takeout, dedup, press, rename, scope
  resources/               ressources embarquées
bin/                       binaires par plateforme (README.md seulement dans le dépôt)
packaging/                 spec PyInstaller, script de build, notes Nuitka
tests/                     tests par composant (core, ui, launcher, modules)
```

Les préférences, l'historique et les journaux sont écrits dans les dossiers utilisateur
(jamais à côté de l'exécutable).

## Construire une distribution

Voir [BUILD.md](BUILD.md) (PyInstaller, tailles, licences).
