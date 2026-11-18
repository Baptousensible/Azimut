# Construire Azimut

## Prérequis

- Python ≥ 3.11 et les dépendances de l'application (`PySide6` ≥ 6.6, < 6.13, etc.).
- **PyInstaller** ≥ 6 : `pip install pyinstaller`.
- Les binaires de la plateforme visée dans `bin/<plateforme>/` (voir `bin/README.md`), téléchargés par
  `python packaging/fetch_binaries.py` et vérifiés contre `packaging/binaries.lock.json` (voir plus bas).
  Sans eux, le build réussit mais le paquet dépendra du `PATH` ou des préférences.
- Le build se fait **sur la plateforme visée** : PyInstaller ne compile pas en croisé.
  Sous macOS, les builds `arm64` et `x64` sont distincts.
- Pour les paquets Linux : `dpkg-deb` (paquet `dpkg` sous Debian/Ubuntu, `dnf install dpkg` sous Fedora)
  et `rpmbuild` (paquet `rpm-build` sous Fedora/RHEL, `apt-get install rpm` sous Debian/Ubuntu).

## Commandes

Windows (PowerShell) :

```powershell
py -3 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt pyinstaller
python packaging/build.py
```

Linux / macOS :

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt pyinstaller
python packaging/build.py
```

Fichier unique (déconseillé, voir plus bas) : `AZIMUT_ONEFILE=1 python packaging/build.py`
(PowerShell : `$env:AZIMUT_ONEFILE = "1"`).

Équivalent direct : `python -m PyInstaller packaging/azimut.spec --noconfirm --clean`.

## Paquets Linux (.deb, .rpm, archive portable)

Une fois `dist/Azimut/` construit (mode dossier) :

```bash
bash packaging/make_deb.sh       # Azimut-<version>-linux-<arch>.deb
bash packaging/make_rpm.sh       # Azimut-<version>-linux-<arch>.rpm
bash packaging/make_tarball.sh   # Azimut-<version>-linux-<arch>.tar.gz
```

Les trois paquets partagent la même disposition : application dans `/opt/azimut`, lanceur
`/usr/bin/azimut`, entrée de menu `azimut.desktop` et icône 256×256. L'archive portable se lance
directement depuis son dossier (`./Azimut/Azimut`) et contient les instructions d'intégration.
L'AppImage reste produite par `packaging/make_appimage.sh`. Les scripts adaptent l'architecture
(`x86_64`/`amd64` ou `aarch64`/`arm64`) à partir de `uname -m`.

## Binaires tiers : le verrou `packaging/binaries.lock.json`

`python packaging/fetch_binaries.py` n'installe plus un fichier qu'il n'a pas relu : le dépôt contient
`packaging/binaries.lock.json`, qui fige pour les quatre plateformes l'URL exacte, la taille et l'empreinte
SHA-256 de chaque archive téléchargée (13 archives, une par téléchargement réellement effectué).

Sans option, le script :

1. lit le verrou **avant tout réseau** ;
2. refuse de continuer si le fichier manque, est illisible, porte un `schema` inconnu ou ne couvre pas
   exactement la plateforme courante — code de sortie **2** ;
3. télécharge **une seule** URL par entrée, compare la taille (`Content-Length`) puis le SHA-256, et n'extrait
   qu'en cas de succès — code **3** si l'un des deux diffère, **4** si l'URL épinglée est injoignable,
   **1** si les binaires installés ne s'exécutent pas ;
4. ne réessaie aucune autre source : un repli contournerait l'épinglage.

En cas d'échec, rien n'est installé et `bin/<plateforme>/` est supprimé ; le message donne la commande de
mise à jour. `BINARIES-SOURCES.txt`, joint au paquet, consigne l'URL, l'empreinte et la mention du verrou.

### Mettre à jour le verrou (mainteneur)

À faire sur une machine connectée, puis **relire le diff** : c'est cette relecture qui vaut validation,
le verrou n'est pas une signature.

```bash
# les quatre plateformes (13 archives)
python packaging/fetch_binaries.py --update-lock

# une seule plateforme (diff qu'on peut tester localement)
python packaging/fetch_binaries.py --update-lock --platform macos-x64

# forcer une branche ou une version
python packaging/fetch_binaries.py --update-lock --ffmpeg-branch master
python packaging/fetch_binaries.py --update-lock --exiftool-version 13.59
```

La commande utilise les sources de découverte habituelles (BtbN, Martin Riedl, SourceForge/GitHub,
relocatable-perl), recoupe les listes de sommes amont quand elles existent, affiche un diff par entrée et
réécrit le fichier trié par `platform` puis `asset`. Sans changement, elle n'écrit rien. Une URL sans chemin
versionné (`/download/latest/`, `/redirect/latest/`, `/releases/latest/`) est consignée avec
`url_immutable: false` et une `note` : l'empreinte reste alors la seule garantie d'identité.

### Dérogations et options

| Option | Effet |
|---|---|
| `--force` | retélécharge et revérifie même si `bin/<plateforme>/` est complet |
| `--no-selftest` | n'exécute pas les binaires après installation ; **les empreintes sont vérifiées** (`--no-verify` reste accepté comme alias déprécié) |
| `--allow-unpinned` | ignore le verrou et retrouve les anciens replis de source : **aucune garantie d'identité**, jamais en CI ; `BINARIES-SOURCES.txt` porte alors `verrou : IGNORÉ` |
| `--update-lock` | réservé au mainteneur ; seul écrivain du verrou, incompatible avec `--allow-unpinned` |

`AZIMUT_FFMPEG_BRANCH` et `AZIMUT_EXIFTOOL_VERSION` ne sont plus lues qu'avec `--update-lock` : en mode
verrouillé, une valeur différente du verrou fait échouer le script (code 2) au lieu d'être ignorée en silence.

## Résultat

| Plateforme | Produit |
|---|---|
| Windows | `dist/Azimut/Azimut.exe` (dossier) ou `dist/Azimut.exe` (fichier unique) |
| Linux | `dist/Azimut/Azimut` (dossier) ou `dist/Azimut` (fichier unique) |
| macOS | `dist/Azimut.app` |

`build.py` affiche la taille totale et celle des binaires embarqués, avec un avertissement
au-delà de **300 Mo**. Les paquets Linux sont écrits dans `release/` (voir « Paquets Linux »).

Le paquet est une application à fenêtres (sans console) : `Azimut --list` n'affiche rien
dans un terminal sous Windows. Pour vérifier la liste des modules, utiliser
`python main.py --list` depuis les sources, ou lancer l'application et regarder les cartes d'Azimut.

## Points de vigilance : la taille

Ordres de grandeur **à vérifier sur les builds retenus** (ils varient selon la version et la plateforme) :

- builds FFmpeg statiques complets : typiquement de plusieurs dizaines à plus d'une centaine de Mo par plateforme ;
- PySide6 : environ une centaine de Mo après exclusion des modules inutiles ;
- ExifTool : quelques dizaines de Mo selon la plateforme (Perl embarqué sous Windows) ;
- `rawpy` (libraw) et `pillow-heif` (libheif) : quelques dizaines de Mo à eux deux. Ils sont facultatifs :
  les retirer de `requirements-build.txt` supprime la lecture des RAW et des HEIC, le reste fonctionne.

Pistes de réduction :

- utiliser des builds FFmpeg « essentials » ou allégés (seuls les codecs utiles) ;
- compléter la liste d'exclusions Qt dans `packaging/azimut.spec` si de nouveaux modules Qt ne sont pas utilisés ;
- préférer `onedir` à `onefile` : démarrage plus rapide (pas d'extraction à chaque lancement)
  et permissions d'exécution des binaires embarqués préservées plus sûrement.
  En `onefile`, vérifier que `ffmpeg`, `ffprobe` et `exiftool` restent exécutables après extraction ;
- UPX est volontairement désactivé (faux positifs antivirus, gain incertain).

## Vérifications après un build

1. Lancer l'application, ouvrir « Binaires » dans Azimut puis « Détecter » : la source doit être « Embarqué ».
2. Ouvrir chaque module depuis Azimut : un module absent du paquet apparaît avec une carte désactivée.
3. Sous macOS, vérifier que les binaires sont retrouvés dans le bundle (`app_root()` = `sys._MEIPASS`).
4. Vérifier que `azimut/resources/icons_animated/` contient les 33 SVG et
   `LICENSE-lucide-animated.txt`, et que `THIRD_PARTY_NOTICES.md` est présent à la racine du
   produit ; `packaging/build.py` le contrôle et échoue si un élément manque.
5. Aucune écriture à côté de l'exécutable : préférences, historique et journaux sont dans les dossiers utilisateur.

## Licences à vérifier avant toute redistribution

- **FFmpeg** : LGPL ou GPL selon les options de compilation du build embarqué (certains codecs imposent la GPL) ;
- **PySide6 / Qt** : LGPL (respecter les obligations de relink et d'information) ;
- **ExifTool** : licence Perl / Artistic ;
- **Papicons** : MIT (thème d'icônes historique) et **lucide-animated** : MIT (thème « Lucide Animées ») ;
  le texte intégral de lucide-animated est dans
  `azimut/resources/icons_animated/LICENSE-lucide-animated.txt`, embarqué dans les distributions
  PyInstaller et dans les données du paquet Python.

Joindre les textes de licence et les mentions requises à la distribution.

## Hors périmètre

La signature de code et la notarisation (macOS : certificat développeur Apple + notarisation ;
Windows : certificat de signature, sans quoi SmartScreen avertit l'utilisateur) et les mises à
jour automatiques ne sont pas couvertes par ce dépôt. Les paquets Linux (.deb, .rpm, .tar.gz)
et l'AppImage sont décrits ci-dessus ; les installateurs Windows et macOS sont décrits dans
`RELEASING.md`. Pour un essai Nuitka, voir `packaging/nuitka_notes.md` (non testé).
