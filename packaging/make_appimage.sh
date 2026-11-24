#!/usr/bin/env bash
# Construit release/Azimut-<version>-linux-x86_64.AppImage à partir de dist/Azimut (PyInstaller, mode dossier).
#
# Usage : bash packaging/make_appimage.sh [--appdir-only]
#   --appdir-only : prépare build/Azimut.AppDir sans télécharger appimagetool ni produire l'AppImage.
#
# Prérequis : python packaging/build.py déjà exécuté ; sur la machine de build, les bibliothèques
# listées dans EXTRA_LIBS (paquets libxcb-* et libxkbcommon-x11) pour les embarquer si PyInstaller les a manquées.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PYTHON="${PYTHON:-python3}"
ARCH="${ARCH:-x86_64}"
VERSION="$("$PYTHON" packaging/print_version.py)"
DIST="dist/Azimut"
APPDIR="build/Azimut.AppDir"
OUT_DIR="release"
OUT="$OUT_DIR/Azimut-$VERSION-linux-$ARCH.AppImage"

if [ ! -x "$DIST/Azimut" ]; then
  echo "ERREUR : $DIST/Azimut est introuvable. Lancez d'abord : python packaging/build.py" >&2
  exit 1
fi

echo "==> Préparation de $APPDIR"
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/lib" "$APPDIR/usr/share/applications" \
         "$APPDIR/usr/share/icons/hicolor/256x256/apps" "$OUT_DIR"
cp -a "$DIST" "$APPDIR/usr/lib/azimut"

# --- Bibliothèques de la plateforme Qt « xcb » (souvent absentes des systèmes légers) ---------------
# PyInstaller embarque normalement celles qu'il détecte ; on complète si l'une manque (sans jamais
# remplacer une bibliothèque déjà présente). Les pilotes graphiques (GL/EGL) restent ceux du système.
INTERNAL="$APPDIR/usr/lib/azimut/_internal"
[ -d "$INTERNAL" ] || INTERNAL="$APPDIR/usr/lib/azimut"
EXTRA_LIBS=(
  libxcb-cursor.so.0 libxcb-icccm.so.4 libxcb-image.so.0 libxcb-keysyms.so.1
  libxcb-render-util.so.0 libxcb-xkb.so.1 libxcb-xinerama.so.0 libxcb-shape.so.0
  libxcb-util.so.1 libxkbcommon-x11.so.0
)
case "$ARCH" in aarch64) LDARCH="AArch64" ;; *) LDARCH="x86-64" ;; esac
for lib in "${EXTRA_LIBS[@]}"; do
  if [ -n "$(find "$INTERNAL" -maxdepth 4 -name "$lib*" -print -quit 2>/dev/null)" ]; then
    continue
  fi
  src="$( (/sbin/ldconfig -p 2>/dev/null || ldconfig -p 2>/dev/null || true) \
          | awk -v l="$lib" -v a="$LDARCH" '$1 == l && index($0, a) {print $NF; exit}')"
  if [ -n "$src" ] && [ -f "$src" ]; then
    cp -L "$src" "$INTERNAL/$lib"
    echo "    + $lib (complété depuis $src)"
  else
    echo "    AVERTISSEMENT : $lib introuvable sur cette machine ; non embarqué." >&2
  fi
done

# --- Point d'entrée, entrée de menu et icône ---------------------------------------------------------
cat > "$APPDIR/AppRun" <<'EOF'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/usr/lib/azimut/Azimut" "$@"
EOF
chmod +x "$APPDIR/AppRun"

[ -f packaging/azimut.png ] || "$PYTHON" packaging/make_icons.py
"$PYTHON" - packaging/azimut.png "$APPDIR/azimut.png" <<'PY'
import sys
from PIL import Image
Image.open(sys.argv[1]).convert("RGBA").resize((256, 256), Image.LANCZOS).save(sys.argv[2])
PY
cp "$APPDIR/azimut.png" "$APPDIR/usr/share/icons/hicolor/256x256/apps/azimut.png"
ln -sf azimut.png "$APPDIR/.DirIcon"

cat > "$APPDIR/azimut.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Azimut
GenericName=Utilitaires pour fichiers
Comment=Suite d'utilitaires pour fichiers photo, vidéo, audio et JSON
Exec=Azimut
Icon=azimut
Terminal=false
Categories=Utility;Graphics;AudioVideo;
X-AppImage-Version=$VERSION
EOF
cp "$APPDIR/azimut.desktop" "$APPDIR/usr/share/applications/azimut.desktop"

if [ "${1:-}" = "--appdir-only" ]; then
  echo "==> AppDir prête : $APPDIR (--appdir-only : AppImage non générée)"
  exit 0
fi

# --- Génération de l'AppImage -------------------------------------------------------------------------
TOOL="build/appimagetool-$ARCH.AppImage"
if [ ! -x "$TOOL" ]; then
  echo "==> Téléchargement d'appimagetool"
  curl -fL --retry 4 --retry-delay 3 -o "$TOOL" \
    "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-$ARCH.AppImage"
  chmod +x "$TOOL"
fi

echo "==> Création de $OUT"
rm -f "$OUT"
# APPIMAGE_EXTRACT_AND_RUN=1 : fonctionne même sans FUSE (cas des conteneurs et de certains runners).
ARCH="$ARCH" APPIMAGE_EXTRACT_AND_RUN=1 "$TOOL" --no-appstream "$APPDIR" "$OUT"
chmod +x "$OUT"
ls -lh "$OUT"
