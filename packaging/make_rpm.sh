#!/usr/bin/env bash
# Construit release/Azimut-<version>-linux-<arch>.rpm à partir de dist/Azimut (PyInstaller, mode dossier).
#
# Usage : bash packaging/make_rpm.sh        (après python packaging/build.py)
#
# Prérequis : rpmbuild (paquet « rpm-build » sous Fedora/RHEL ; « rpm » sous Debian/Ubuntu).
# Même disposition que le .deb : /opt/azimut, lanceur /usr/bin/azimut, icône et entrée de menu.
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/linux_common.sh"

require_dist

if ! command -v rpmbuild >/dev/null 2>&1; then
  echo "ERREUR : rpmbuild est requis pour construire le .rpm." >&2
  echo "  Fedora/RHEL : sudo dnf install rpm-build" >&2
  echo "  Debian/Ubuntu : sudo apt-get install rpm" >&2
  exit 1
fi

# RPM n'accepte pas de tiret dans Version : « 1.2.3-rc1 » devient Version 1.2.3, Release rc1.
RPM_VERSION="${VERSION%%-*}"
RPM_RELEASE="1"
case "$VERSION" in
  *-*)
    RPM_RELEASE="${VERSION#*-}"
    RPM_RELEASE="${RPM_RELEASE//-/.}"
    ;;
esac

TOPDIR="build/rpm"
OUT="release/Azimut-${VERSION}-linux-${ARCH}.rpm"
SPEC="$TOPDIR/SPECS/azimut.spec"

echo "==> Préparation de $TOPDIR"
rm -rf "$TOPDIR"
mkdir -p "$TOPDIR/BUILD" "$TOPDIR/BUILDROOT" "$TOPDIR/RPMS" "$TOPDIR/SOURCES" "$TOPDIR/SPECS" "$TOPDIR/SRPMS"
write_icon "$TOPDIR/azimut.png"
write_desktop "$TOPDIR/azimut.desktop"

cat > "$SPEC" <<EOF
# Généré par packaging/make_rpm.sh — ne pas éditer à la main.
%global debug_package %{nil}
%global __os_install_post %{nil}
%global _build_id_links none
%global _binary_payload w9.gzdio

Name:           azimut
Version:        $RPM_VERSION
Release:        $RPM_RELEASE
Summary:        Suite d'utilitaires pour fichiers photo, vidéo et audio
License:        Unspecified
BuildArch:      $RPMARCH
AutoReqProv:    no
Requires:       glibc >= 2.35

%description
Azimut réunit Takeout, Dedup, Convert, Rename, Scope et Tags dans une seule
interface. FFmpeg, FFprobe et ExifTool sont inclus : rien d'autre à installer.

%prep

%build

%install
rm -rf %{buildroot}
mkdir -p %{buildroot}/opt/azimut \\
         %{buildroot}/usr/bin \\
         %{buildroot}/usr/share/applications \\
         %{buildroot}/usr/share/icons/hicolor/256x256/apps \\
         %{buildroot}/usr/share/doc/azimut
cp -a "$ROOT/$DIST/." %{buildroot}/opt/azimut/
ln -s /opt/azimut/Azimut %{buildroot}/usr/bin/azimut
install -m 0644 "$ROOT/$TOPDIR/azimut.desktop" %{buildroot}/usr/share/applications/azimut.desktop
install -m 0644 "$ROOT/$TOPDIR/azimut.png" %{buildroot}/usr/share/icons/hicolor/256x256/apps/azimut.png
install -m 0644 "$ROOT/THIRD_PARTY_NOTICES.md" %{buildroot}/usr/share/doc/azimut/THIRD_PARTY_NOTICES.md
install -m 0644 "$ROOT/README.md" %{buildroot}/usr/share/doc/azimut/README.md

%files
%defattr(-,root,root,-)
/opt/azimut
/usr/bin/azimut
/usr/share/applications/azimut.desktop
/usr/share/icons/hicolor/256x256/apps/azimut.png
/usr/share/doc/azimut/THIRD_PARTY_NOTICES.md
/usr/share/doc/azimut/README.md

%changelog
* $(LC_ALL=C date '+%a %b %d %Y') Azimut - $RPM_VERSION-$RPM_RELEASE
- Paquet généré depuis Azimut $VERSION.
EOF

mkdir -p release
echo "==> Création de $OUT"
rm -f "$OUT"
rpmbuild -bb --define "_topdir $ROOT/$TOPDIR" --target "$RPMARCH" "$SPEC"
BUILT="$(find "$ROOT/$TOPDIR/RPMS" -type f -name '*.rpm' -print -quit)"
if [ -z "$BUILT" ]; then
  echo "ERREUR : rpmbuild n'a produit aucun fichier .rpm." >&2
  exit 1
fi
cp "$BUILT" "$OUT"
rpm -qip "$OUT"
ls -lh "$OUT"
