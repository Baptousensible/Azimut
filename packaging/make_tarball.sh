#!/usr/bin/env bash
# Construit release/Azimut-<version>-linux-<arch>.tar.gz à partir de dist/Azimut.
#
# Usage : bash packaging/make_tarball.sh        (après python packaging/build.py)
#
# Archive portable pour les distributions sans gestionnaire de paquets pris en charge
# (Arch, Gentoo, Slackware…) : l'application se lance directement depuis le dossier extrait.
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/linux_common.sh"

require_dist

NAME="Azimut-${VERSION}-linux-${ARCH}"
STAGE="build/tarball/$NAME"
OUT="release/$NAME.tar.gz"

echo "==> Préparation de $STAGE"
rm -rf "$STAGE"
mkdir -p "$STAGE"
cp -a "$DIST" "$STAGE/Azimut"
write_icon "$STAGE/azimut.png"
write_desktop "$STAGE/azimut.desktop"
install -m 0644 THIRD_PARTY_NOTICES.md "$STAGE/THIRD_PARTY_NOTICES.md"

cat > "$STAGE/LISEZMOI.txt" <<EOF
Azimut $VERSION — paquet portable Linux ($ARCH)

Lancer sans installer :
  ./Azimut/Azimut

Intégration au menu (facultative, pour tous les utilisateurs) :
  sudo mkdir -p /opt/azimut
  sudo cp -a Azimut/. /opt/azimut/
  sudo ln -sf /opt/azimut/Azimut /usr/local/bin/azimut
  sudo install -Dm644 azimut.desktop /usr/share/applications/azimut.desktop
  sudo install -Dm644 azimut.png /usr/share/icons/hicolor/256x256/apps/azimut.png

FFmpeg, FFprobe et ExifTool sont inclus dans Azimut/. Les préférences, l'historique
et les journaux sont écrits dans les dossiers utilisateur, jamais dans le paquet.
EOF

mkdir -p release
echo "==> Création de $OUT"
rm -f "$OUT"
tar -czf "$OUT" -C "build/tarball" "$NAME"
ls -lh "$OUT"
