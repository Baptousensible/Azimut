# Publier Azimut (GitHub Actions)

Une release se fabrique toute seule : on pousse un tag `vX.Y.Z`, GitHub compile sur Linux, Windows et macOS,
teste chaque paquet, puis joint les fichiers à la page « Releases ». Tout est gratuit (voir « Coût » plus bas).

| Plateforme | Fichier produit | Contenu |
|---|---|---|
| Linux — Debian, Ubuntu, Mint | `Azimut-X.Y.Z-linux-x86_64.deb` | application + Qt + FFmpeg + ExifTool + Perl portable, installée dans `/opt/azimut` avec entrée de menu |
| Linux — Fedora, RHEL 10+, openSUSE | `Azimut-X.Y.Z-linux-x86_64.rpm` | idem, au format RPM |
| Linux — portable (Arch, Gentoo…) | `Azimut-X.Y.Z-linux-x86_64.tar.gz` | idem, à extraire et lancer sans installation |
| Linux — toutes distributions | `Azimut-X.Y.Z-linux-x86_64.AppImage` | idem, exécutable unique sans installation |
| Windows | `Azimut-X.Y.Z-windows-x64-setup.exe` (installeur) et `…-portable.zip` | application + Qt + FFmpeg + ExifTool |
| macOS | `Azimut-X.Y.Z-macos-arm64.dmg` (Apple Silicon) et `…-macos-x64.dmg` (Intel) | application + Qt + FFmpeg + ExifTool + Perl portable |

**Systèmes minimaux** : macOS 14.4 (Sonoma) ou plus récent (limite de Qt 6.12, déclarée dans `LSMinimumSystemVersion`), Windows 10 1809 ou plus récent, Linux x86_64 avec glibc ≥ 2.35. Windows et Linux ARM64 : pas de paquet ; l'application détecte l'architecture et utilise alors FFmpeg et ExifTool du système.

L'utilisateur final n'a **rien** à installer : FFmpeg, FFprobe, ExifTool (et le Perl dont ExifTool a besoin sous
Linux/macOS), HEIC, AVIF et la corbeille système sont dans le paquet.

## Mise en place (une seule fois)

1. **Copiez les fichiers** de ce dossier à la racine de votre dépôt (ils s'ajoutent ou remplacent) :

   ```
   .github/workflows/build.yml      remplace l'ancien squelette
   packaging/azimut.spec            remplace l'actuel
   packaging/build.py               remplace l'actuel (une seule modification)
   packaging/fetch_binaries.py      nouveau : télécharge FFmpeg / ExifTool / Perl (verrouillés)
   packaging/binaries.lock.json     nouveau : URL + taille + SHA-256 des 13 archives épinglées
   packaging/make_icons.py          nouveau : icônes .png / .ico / .icns
   packaging/make_appimage.sh       nouveau
   packaging/make_deb.sh            nouveau : paquet Debian/Ubuntu
   packaging/make_rpm.sh            nouveau : paquet Fedora/RHEL/openSUSE
   packaging/make_tarball.sh        nouveau : archive portable
   packaging/linux_common.sh        nouveau : préparation commune aux paquets Linux
   packaging/make_dmg.sh            nouveau
   packaging/azimut.iss             nouveau : installeur Windows
   packaging/check_selftest.py      nouveau : contrôle « clé en main »
   packaging/print_version.py       nouveau
   requirements-build.txt           nouveau
   ```

2. **Ajoutez à `.gitignore`** :

   ```
   release/
   selftest*.json
   ```

3. **Dépôt public** recommandé : les minutes GitHub Actions y sont illimitées (voir « Coût »).
   Aucun réglage particulier n'est nécessaire : le workflow demande lui-même le droit d'écrire la release.

4. **Essai à blanc** : onglet *Actions* → *build* → *Run workflow*. Vous obtenez les paquets dans les
   *Artifacts* de l'exécution (conservés 14 jours), sans rien publier. Faites-le avant la première vraie release.

## Publier une version

1. Mettez à jour `__version__` dans `azimut/__init__.py` (ex. `"0.2.0"`) et committez.
2. Créez le tag **identique, préfixé de `v`** et poussez-le :

   ```bash
   git tag v0.2.0
   git push origin v0.2.0
   ```

   (Ou, sur GitHub : *Releases* → *Draft a new release* → créer le tag `v0.2.0` → *Publish*. Le workflow se
   déclenche de la même façon et rattache les fichiers à cette release.)

3. Patientez environ 20 à 40 minutes. Les fichiers et `SHA256SUMS.txt` apparaissent sur la page de la release.

Le workflow refuse de publier si le tag ne correspond pas à `__version__` (message explicite dans les logs).
Un tag contenant un tiret (`v1.0.0-rc1`) crée une « pré-release ».

## Ce que voit l'utilisateur à la première ouverture

Les applications ne sont pas signées par un certificat payant (c'est ce qui permet le « 100 % gratuit »).
Elles fonctionnent, mais le système demande une confirmation :

- **Windows** : SmartScreen affiche « Windows a protégé votre ordinateur » → *Informations complémentaires* → *Exécuter quand même*.
- **macOS** : le message dit que l'app ne peut pas être vérifiée. Ouvrez *Réglages Système → Confidentialité et sécurité*,
  descendez jusqu'à « Azimut a été bloqué » → *Ouvrir quand même*. Alternative en terminal :
  `xattr -dr com.apple.quarantine /Applications/Azimut.app`.
- **Linux** :
  - Debian/Ubuntu/Mint : `sudo apt install ./Azimut-*.deb` ;
  - Fedora/RHEL 10+/openSUSE : `sudo dnf install ./Azimut-*.rpm` ;
  - Arch/Gentoo ou autre : extraire `Azimut-*.tar.gz` et lancer `./Azimut/Azimut` (ou suivre `LISEZMOI.txt`) ;
  - AppImage : `chmod +x Azimut-*.AppImage`, puis double-clic. Si rien ne se passe, installer FUSE
    (`sudo apt install libfuse2` ; paquet `libfuse2t64` sur Ubuntu 24.04) ou lancer
    `./Azimut-*.AppImage --appimage-extract-and-run`.

  Prérequis commun : glibc ≥ 2.35 (Ubuntu 22.04+, Debian 12+, Fedora 36+). Les paquets `.deb`,
  `.rpm` et `.tar.gz` n'ont pas besoin de FUSE.

Supprimer ces avertissements suppose un certificat Windows (≈ 100–300 €/an ou service de signature) et un compte
Apple Developer (99 $/an, signature + notarisation) : ce n'est pas gratuit, donc hors de ce dispositif.

## Ce que fait le workflow

1. **test** : `pytest` sous Ubuntu, Windows et macOS (Python 3.12).
2. **package** (en parallèle pour chaque plateforme) :
   télécharge les binaires (`fetch_binaries.py`, qui exige `packaging/binaries.lock.json` et vérifie chaque
   empreinte) → construit avec PyInstaller (`build.py`, qui échoue si les
   binaires manquent) → lance `Azimut --selftest` sur le paquet → fabrique AppImage, `.deb`, `.rpm`,
   `.tar.gz` / installeur / DMG → **retest du livrable final** (AppImage exécutée, `.deb` installé via
   `apt`, `.rpm` installé via `rpm`, archive extraite, installeur installé en silencieux puis lancé,
   DMG monté puis lancé).
   `check_selftest.py` exige que FFmpeg, FFprobe et ExifTool viennent du paquet et s'exécutent, et que HEIC,
   AVIF et la corbeille soient présents. Un paquet incomplet fait échouer le job : il n'est jamais publié.
3. **release** (tags `v*` seulement) : réunit les fichiers, calcule `SHA256SUMS.txt`, crée la release.

La version Intel de macOS est marquée « optionnelle » : si elle échoue, la release part quand même sans elle
(Apple a abandonné Intel et GitHub retirera ses runners Intel en 2027). Pour la supprimer, effacez sa ligne
dans la matrice de `build.yml`.

## Réglages

| Besoin | Où |
|---|---|
| Changer de version de FFmpeg (`master` = plus récent) | `python packaging/fetch_binaries.py --update-lock --ffmpeg-branch master`, relire le diff, committer le verrou |
| Forcer une version d'ExifTool | `python packaging/fetch_binaries.py --update-lock --exiftool-version 13.59`, relire le diff, committer le verrou |
| Votre vrai logo | committez `azimut/resources/azimut.png` (1024×1024, fond transparent) : `packaging/azimut.png`, `.ico` et `.icns` en sont déduits. Sans cela, une boussole de substitution est dessinée. |
| Nom de l'éditeur dans l'installeur | `AppPublisher` dans `packaging/azimut.iss` |

`AZIMUT_FFMPEG_BRANCH` et `AZIMUT_EXIFTOOL_VERSION` restent lues par le script, mais **seulement** avec
`--update-lock` : le mode utilisé par la CI s'appuie sur `packaging/binaries.lock.json`. Une variable qui
contredit le verrou fait échouer le job (code 2). Un changement de version amont se fait donc en trois temps :
régénérer le verrou, en relire le diff, le committer — puis pousser le tag.

Essai local (sur la plateforme visée) :

```bash
pip install -r requirements-build.txt
python packaging/fetch_binaries.py && python packaging/make_icons.py
AZIMUT_REQUIRE_BINARIES=1 python packaging/build.py
dist/Azimut/Azimut --selftest rapport.json && python packaging/check_selftest.py rapport.json
```

## Licences à respecter

- Les builds FFmpeg embarqués sont de variante **GPL** (indispensable : x264 pour H.264, plus AV1, MP3, Opus, WebP).
  Azimut les lance comme programmes séparés, mais redistribuer ces binaires impose de pouvoir fournir leurs
  sources : `bin/<plateforme>/BINARIES-SOURCES.txt`, joint au paquet, indique l'origine exacte (URL + empreinte).
- PySide6 / Qt : LGPL. ExifTool et Perl : licence Perl / Artistic.
- Icônes : Papicons (MIT) et lucide-animated (MIT). Le texte MIT est livré dans
  `azimut/resources/icons_animated/` ; les paquets PyInstaller contiennent aussi
  `THIRD_PARTY_NOTICES.md` à leur racine.
- Joignez un fichier `LICENSE` pour Azimut et mentionnez ces composants dans votre README. (Je ne suis pas juriste :
  en cas de doute pour une diffusion large ou commerciale, faites valider ce point.)

## Coût

- **Dépôt public** : gratuit, minutes illimitées.
- **Dépôt privé** (offre gratuite : 2 000 min/mois) : les minutes macOS comptent ×10 et Windows ×2. Une release
  complète consomme plusieurs centaines de minutes facturables : privilégiez un dépôt public pour les builds, ou
  supprimez la ligne macOS Intel.

## Poids et limites connus

- Les paquets sont lourds (compter 150 à 300 Mo) à cause des FFmpeg statiques complets ; c'est le prix du « tout inclus ».
- Les paquets Linux sont construits sur Ubuntu 22.04 : ils exigent glibc ≥ 2.35 (Ubuntu 22.04+, Debian 12+,
  Fedora 36+). Ils ne couvrent ni RHEL 9 (glibc 2.34) ni Linux ARM64 tant que la CI ne produit pas ces builds.
- Les paquets `.deb` et `.rpm` installent dans `/opt/azimut` et ne remplacent aucune bibliothèque système.
- Pas de mise à jour automatique : l'installeur Windows met à jour sur place (même `AppId`), les autres systèmes
  se remplacent à la main.

## Si un build échoue

| Symptôme dans les logs | Cause probable |
|---|---|
| `Le tag … ne correspond pas à __version__` | mettez `__version__` à jour avant de taguer |
| `fetch_binaries` : « verrou absent » ou « couverture … incomplète » (code 2) | `packaging/binaries.lock.json` n'est pas committé ou ne couvre pas la plateforme : `python packaging/fetch_binaries.py --update-lock`, relisez le diff, committez. `--allow-unpinned` dépanne localement mais ne doit jamais servir en CI |
| `fetch_binaries` : « empreinte SHA-256 différente » (code 3) | les octets téléchargés ne sont plus ceux relus (nouvelle version amont, archive régénérée, miroir) : vérifiez la cause, puis `--update-lock` et relisez le diff. Ne committez pas un verrou dont vous n'avez pas lu le diff |
| `fetch_binaries` : HTTP 404 sur l'URL épinglée (code 4) | la release amont a disparu (rétention BtbN, miroir SourceForge…) : `python packaging/fetch_binaries.py --update-lock`, relisez le diff, committez |
| `check_selftest` : « ne s'exécute pas » | binaire d'une autre architecture ou bibliothèque système manquante : lisez `selftest*.json` dans l'artefact `rapports-<plateforme>` |
| Avertissement « Encodeurs FFmpeg absents » | le build FFmpeg choisi n'a pas un codec utilisé par Convert ; Azimut le signalera à l'utilisateur, pas bloquant |
| macOS : `codesign` avertit | normal sans certificat Apple ; seul l'auto-test du DMG fait foi |
