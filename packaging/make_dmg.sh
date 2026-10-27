#!/usr/bin/env bash
# macOS : signe (ad hoc) dist/Azimut.app, crée release/Azimut-<version>-macos-<arch>.dmg,
# puis monte le DMG et lance l'auto-test de l'application qu'il contient (rapport : selftest-dmg.json).
#
# Usage : bash packaging/make_dmg.sh        (après python packaging/build.py)
#
# La signature « ad hoc » (sans certificat Apple) est gratuite : elle évite que macOS Apple Silicon
# refuse d'exécuter des binaires non signés, mais n'est PAS une notarisation (voir RELEASING.md).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PYTHON="${PYTHON:-python3}"
VERSION="$("$PYTHON" packaging/print_version.py)"
case "$(uname -m)" in arm64) TAG="macos-arm64" ;; *) TAG="macos-x64" ;; esac
APP="dist/Azimut.app"
OUT="release/Azimut-$VERSION-$TAG.dmg"

[ -d "$APP" ] || { echo "ERREUR : $APP introuvable. Lancez d'abord : python packaging/build.py" >&2; exit 1; }

echo "==> Signature ad hoc de tous les exécutables Mach-O de $APP"
signed=0
while IFS= read -r -d '' f; do
  if file -b "$f" | grep -q 'Mach-O'; then
    if codesign --force --sign - --timestamp=none "$f" >/dev/null 2>&1; then
      signed=$((signed + 1))
    else
      echo "    AVERTISSEMENT : signature impossible : $f" >&2
    fi
  fi
done < <(find "$APP" -type f \( -perm -u+x -o -name '*.dylib' -o -name '*.so' \) -print0)
echo "    $signed fichier(s) signé(s)"
codesign --force --deep --sign - --timestamp=none "$APP" \
  || echo "    AVERTISSEMENT : signature globale du bundle refusée (les exécutables restent signés individuellement)." >&2
codesign --verify --deep --strict "$APP" 2>&1 \
  || echo "    AVERTISSEMENT : vérification stricte de la signature en échec (normal sans certificat Apple)." >&2

echo "==> Création de $OUT"
mkdir -p release
STAGE="$(mktemp -d)"
ditto "$APP" "$STAGE/Azimut.app"
ln -s /Applications "$STAGE/Applications"
rm -f "$OUT"
for attempt in 1 2 3; do                       # hdiutil échoue parfois (« Resource busy ») sur les runners
  if hdiutil create -volname "Azimut" -srcfolder "$STAGE" -ov -format UDZO -fs HFS+ "$OUT"; then
    break
  fi
  [ "$attempt" -lt 3 ] || { echo "ERREUR : hdiutil a échoué 3 fois." >&2; exit 1; }
  echo "    nouvelle tentative…" >&2
  sleep 5
done
rm -rf "$STAGE"
ls -lh "$OUT"

echo "==> Test de l'application depuis le DMG monté"
MNT="$(mktemp -d)"
hdiutil attach "$OUT" -mountpoint "$MNT" -nobrowse -readonly -quiet
cleanup() { hdiutil detach "$MNT" -quiet -force >/dev/null 2>&1 || true; }
trap cleanup EXIT
if ! QT_QPA_PLATFORM=offscreen "$MNT/Azimut.app/Contents/MacOS/Azimut" --selftest "$ROOT/selftest-dmg.json"; then
  echo "ERREUR : l'auto-test de l'application du DMG a échoué :" >&2
  cat "$ROOT/selftest-dmg.json" >&2 || true
  exit 1
fi
echo "    auto-test réussi (rapport : selftest-dmg.json)"
