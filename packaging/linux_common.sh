#!/usr/bin/env bash
# Fonctions communes aux paquets Linux d'Azimut (.deb, .rpm, archive portable).
# Ce fichier n'est pas exécuté seul : il est chargé par make_deb.sh, make_rpm.sh et make_tarball.sh.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PYTHON="${PYTHON:-python3}"
VERSION="$("$PYTHON" packaging/print_version.py)"
MACHINE="$(uname -m)"
case "$MACHINE" in
  x86_64|amd64)  ARCH="x86_64"  ; DEBARCH="amd64" ; RPMARCH="x86_64"  ;;
  aarch64|arm64) ARCH="aarch64" ; DEBARCH="arm64" ; RPMARCH="aarch64" ;;
  *)
    echo "ERREUR : architecture non prise en charge : $MACHINE (attendu : x86_64 ou aarch64)." >&2
    exit 1
    ;;
esac
DIST="dist/Azimut"


require_dist() {
  # Vérifie que le build PyInstaller (mode dossier) est présent.
  if [ ! -x "$DIST/Azimut" ]; then
    echo "ERREUR : $DIST/Azimut est introuvable. Lancez d'abord : python packaging/build.py" >&2
    exit 1
  fi
}


write_icon() {
  # Écrit l'icône 256×256 d'Azimut à l'emplacement demandé.
  local dest="$1"
  [ -f packaging/azimut.png ] || "$PYTHON" packaging/make_icons.py
  "$PYTHON" - packaging/azimut.png "$dest" <<'PY'
import sys
from PIL import Image
Image.open(sys.argv[1]).convert("RGBA").resize((256, 256), Image.LANCZOS).save(sys.argv[2])
PY
}


write_desktop() {
  # Écrit l'entrée de menu commune (le binaire installé s'appelle « azimut »).
  local dest="$1"
  cat > "$dest" <<'EOF'
[Desktop Entry]
Type=Application
Name=Azimut
GenericName=Utilitaires pour fichiers
Comment=Suite d'utilitaires pour fichiers photo, vidéo, audio et JSON
Exec=azimut
TryExec=azimut
Icon=azimut
Terminal=false
Categories=Utility;Graphics;AudioVideo;
Keywords=photo;video;audio;convert;dedup;rename;tags;
StartupNotify=true
EOF
}
