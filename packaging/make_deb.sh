#!/usr/bin/env bash
# Construit release/Azimut-<version>-linux-<arch>.deb à partir de dist/Azimut (PyInstaller, mode dossier).
#
# Usage : bash packaging/make_deb.sh        (après python packaging/build.py)
#
# Prérequis : dpkg-deb (paquet « dpkg » sous Debian/Ubuntu ; « sudo dnf install dpkg » sous Fedora).
# Le paquet installe l'application dans /opt/azimut, un lanceur /usr/bin/azimut, l'icône et
# l'entrée de menu ; il n'écrit rien dans les dossiers utilisateur.
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/linux_common.sh"

require_dist

if ! command -v dpkg-deb >/dev/null 2>&1; then
  echo "ERREUR : dpkg-deb est requis pour construire le .deb." >&2
  echo "  Debian/Ubuntu : sudo apt-get install dpkg" >&2
  echo "  Fedora        : sudo dnf install dpkg" >&2
  exit 1
fi

PKGROOT="build/deb/azimut_${VERSION}_${DEBARCH}"
OUT="release/Azimut-${VERSION}-linux-${ARCH}.deb"

echo "==> Préparation de $PKGROOT"
rm -rf "$PKGROOT"
mkdir -p "$PKGROOT/DEBIAN" \
         "$PKGROOT/opt/azimut" \
         "$PKGROOT/usr/bin" \
         "$PKGROOT/usr/share/applications" \
         "$PKGROOT/usr/share/icons/hicolor/256x256/apps" \
         "$PKGROOT/usr/share/doc/azimut"

cp -a "$DIST/." "$PKGROOT/opt/azimut/"
ln -s /opt/azimut/Azimut "$PKGROOT/usr/bin/azimut"
write_icon "$PKGROOT/usr/share/icons/hicolor/256x256/apps/azimut.png"
write_desktop "$PKGROOT/usr/share/applications/azimut.desktop"
install -m 0644 THIRD_PARTY_NOTICES.md README.md "$PKGROOT/usr/share/doc/azimut/"

cat > "$PKGROOT/usr/share/doc/azimut/copyright" <<'EOF'
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: Azimut
Comment: La licence d'Azimut n'est pas encore fixée. Les composants tiers et leurs
 licences sont détaillés dans THIRD_PARTY_NOTICES.md, joint à ce paquet.
Files: *
Copyright: Azimut contributors
License: Unspecified
EOF

INSTALLED_SIZE="$(du -sk "$PKGROOT" | cut -f1)"
cat > "$PKGROOT/DEBIAN/control" <<EOF
Package: azimut
Version: $VERSION
Section: utils
Priority: optional
Architecture: $DEBARCH
Maintainer: Azimut
Installed-Size: $INSTALLED_SIZE
Depends: libc6 (>= 2.35), libgl1, libegl1, libfontconfig1, libdbus-1-3
Recommends: libxcb-cursor0, libxkbcommon-x11-0
Description: Suite d'utilitaires pour fichiers photo, vidéo et audio
 Azimut réunit Takeout, Dedup, Convert, Rename, Scope et Tags dans une seule
 interface. FFmpeg, FFprobe et ExifTool sont inclus : rien d'autre à installer.
EOF
chmod 0755 "$PKGROOT/DEBIAN"
chmod 0644 "$PKGROOT/DEBIAN/control"

mkdir -p release
echo "==> Création de $OUT"
rm -f "$OUT"
dpkg-deb --build --root-owner-group "$PKGROOT" "$OUT"
dpkg-deb --info "$OUT"
ls -lh "$OUT"
