# Notes Nuitka (indicatif — NON TESTÉ)

> ⚠️ **Non testé.** Cette commande est une base de travail, pas une procédure validée.
> Le chemin d'empaquetage officiel d'Azimut est PyInstaller (`packaging/azimut.spec`).

## Commande indicative (Linux)

```bash
python -m pip install nuitka
python -m nuitka main.py \
  --standalone \
  --enable-plugin=pyside6 \
  --include-package=azimut \
  --include-data-dir=azimut/resources=azimut/resources \
  --include-data-dir=bin/linux-x64=bin/linux-x64 \
  --output-dir=dist-nuitka \
  --output-filename=Azimut
```

Adapter `bin/linux-x64` à la plateforme (`windows-x64`, `macos-arm64`, `macos-x64`).

## Points à vérifier

- `--include-package=azimut` est indispensable : les fenêtres des modules sont
  importées par chaîne (`load_window_class`), Nuitka ne les voit pas statiquement.
- Windows : ajouter `--windows-console-mode=disable` (selon la version de Nuitka,
  `--windows-disable-console`) et `--windows-icon-from-ico=packaging/azimut.ico`.
- macOS : `--macos-create-app-bundle` (et `--macos-app-icon=packaging/azimut.icns`).
- En mode figé, `azimut.core.paths.app_root()` retombe sur le dossier de l'exécutable
  si `sys._MEIPASS` n'existe pas : vérifier que `bin/<plateforme>/` se retrouve bien
  à côté de l'exécutable produit.
- Vérifier l'absence des modules Qt inutiles (`--noinclude-qt-plugins`, exclusions
  de modules) pour limiter la taille.
- Contrôler les permissions d'exécution des binaires embarqués (ffmpeg, ffprobe, exiftool).
