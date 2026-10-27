# Construire Azimut

## Prérequis

- Python ≥ 3.10 et les dépendances de l'application (`PySide6` ≥ 6.6, etc.).
- **PyInstaller** ≥ 6 : `pip install pyinstaller`.
- Les binaires de la plateforme visée dans `bin/<plateforme>/` (voir `bin/README.md`).
  Sans eux, le build réussit mais le paquet dépendra du `PATH` ou des préférences.
- Le build se fait **sur la plateforme visée** : PyInstaller ne compile pas en croisé.
  Sous macOS, les builds `arm64` et `x64` sont distincts.

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

## Résultat

| Plateforme | Produit |
|---|---|
| Windows | `dist/Azimut/Azimut.exe` (dossier) ou `dist/Azimut.exe` (fichier unique) |
| Linux | `dist/Azimut/Azimut` (dossier) ou `dist/Azimut` (fichier unique) |
| macOS | `dist/Azimut.app` |

`build.py` affiche la taille totale et celle des binaires embarqués, avec un avertissement
au-delà de **300 Mo**.

Le paquet est une application à fenêtres (sans console) : `Azimut --list` n'affiche rien
dans un terminal sous Windows. Pour vérifier la liste des modules, utiliser
`python main.py --list` depuis les sources, ou lancer l'application et regarder les cartes d'Atlas.

## Points de vigilance : la taille

Ordres de grandeur **à vérifier sur les builds retenus** (ils varient selon la version et la plateforme) :

- builds FFmpeg statiques complets : typiquement de plusieurs dizaines à plus d'une centaine de Mo par plateforme ;
- PySide6 : environ une centaine de Mo après exclusion des modules inutiles ;
- ExifTool : quelques dizaines de Mo selon la plateforme (Perl embarqué sous Windows).

Pistes de réduction :

- utiliser des builds FFmpeg « essentials » ou allégés (seuls les codecs utiles) ;
- compléter la liste d'exclusions Qt dans `packaging/azimut.spec` si de nouveaux modules Qt ne sont pas utilisés ;
- préférer `onedir` à `onefile` : démarrage plus rapide (pas d'extraction à chaque lancement)
  et permissions d'exécution des binaires embarqués préservées plus sûrement.
  En `onefile`, vérifier que `ffmpeg`, `ffprobe` et `exiftool` restent exécutables après extraction ;
- UPX est volontairement désactivé (faux positifs antivirus, gain incertain).

## Vérifications après un build

1. Lancer l'application, ouvrir « Binaires » dans Atlas puis « Détecter » : la source doit être « Embarqué ».
2. Ouvrir chaque module depuis Atlas : un module absent du paquet apparaît avec une carte désactivée.
3. Sous macOS, vérifier que les binaires sont retrouvés dans le bundle (`app_root()` = `sys._MEIPASS`).
4. Aucune écriture à côté de l'exécutable : préférences, historique et journaux sont dans les dossiers utilisateur.

## Licences à vérifier avant toute redistribution

- **FFmpeg** : LGPL ou GPL selon les options de compilation du build embarqué (certains codecs imposent la GPL) ;
- **PySide6 / Qt** : LGPL (respecter les obligations de relink et d'information) ;
- **ExifTool** : licence Perl / Artistic.

Joindre les textes de licence et les mentions requises à la distribution.

## Hors périmètre

Signature de code et notarisation (macOS : certificat développeur Apple + notarisation ;
Windows : certificat de signature, sans quoi SmartScreen avertit l'utilisateur), installateurs
(MSI, DMG, AppImage) et mises à jour automatiques ne sont pas couverts par ce dépôt.
Pour un essai Nuitka, voir `packaging/nuitka_notes.md` (non testé).
