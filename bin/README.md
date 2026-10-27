# Binaires embarqués

Azimut utilise **FFmpeg**, **FFprobe** et **ExifTool**. Ils sont cherchés dans cet ordre :

1. ce dossier (`bin/<plateforme>/`, ou `bin/<plateforme>/` à l'intérieur du paquet figé) ;
2. le chemin personnalisé défini dans « Préférences… » ;
3. le `PATH` du système.

Les binaires ne sont **pas** fournis avec le dépôt : déposez-les ici avant un build.

## Arborescence attendue

```
bin/
├── README.md
├── windows-x64/
│   ├── ffmpeg.exe
│   ├── ffprobe.exe
│   ├── exiftool.exe
│   └── exiftool_files/        ← dossier voisin d'exiftool.exe (obligatoire)
├── linux-x64/
│   ├── ffmpeg
│   ├── ffprobe
│   ├── exiftool               ← script Perl
│   └── lib/                   ← bibliothèques Perl d'ExifTool (voisines du script)
├── macos-arm64/
│   └── ffmpeg  ffprobe  exiftool  lib/
└── macos-x64/
    └── ffmpeg  ffprobe  exiftool  lib/
```

## Où les obtenir (sites officiels)

- **FFmpeg / FFprobe** : <https://ffmpeg.org/download.html> (la page renvoie vers les
  builds pour Windows, Linux et macOS). Prendre un build contenant `ffmpeg` **et** `ffprobe`.
- **ExifTool** : <https://exiftool.org/>
  - Windows : version « Windows Executable » ; renommer `exiftool(-k).exe` en `exiftool.exe`
    et copier avec lui le dossier `exiftool_files/`.
  - Linux / macOS : archive « Image-ExifTool » (script Perl `exiftool` + dossier `lib/`).
    **Perl doit être installé** sur la machine de l'utilisateur ; le script et son dossier
    `lib/` doivent rester côte à côte.

## Permissions (Linux / macOS)

```bash
chmod +x bin/linux-x64/ffmpeg bin/linux-x64/ffprobe bin/linux-x64/exiftool
```

Sous macOS, un binaire téléchargé peut être mis en quarantaine par le système :

```bash
xattr -dr com.apple.quarantine bin/macos-arm64
```

## Git

Ne committez pas de gros binaires sans y réfléchir : ils alourdissent durablement
l'historique. Le `.gitignore` ignore le contenu de `bin/*/` et ne conserve que ce fichier.
Si vous devez versionner des binaires, utilisez Git LFS ou un stockage séparé, ou forcez
l'ajout (`git add -f`) en connaissance de cause.
