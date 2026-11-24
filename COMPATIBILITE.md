# Compatibilité d'Azimut — Windows, macOS et Linux

Audit de la version **0.2.0** du dépôt, réalisé le 6 novembre 2026.

Objet : vérifier ce qu'Azimut peut réellement promettre sur chaque système, repérer les
incompatibilités concrètes dans le code et dans la chaîne de fabrication, et proposer un
protocole de vérification reproductible. Le périmètre est l'ordinateur de bureau ; le mobile
est traité en fin de document.

| Légende | Signification |
|---|---|
| **Vérifié** | exécuté sur la machine d'audit ou mesuré dans le code |
| **Attendu** | déduit du comportement documenté de Qt et du code, non exécuté ici |
| **À corriger** | défaut identifié, avec référence de fichier |
| **Non supporté** | ne peut pas fonctionner sans travail supplémentaire |

---

## 1. Synthèse

| Plateforme | Base technique | Paquet livré | Verdict |
|---|---|---|---|
| Windows 10 1809 → 22H2 (x64) | Qt 6.12 : **dernière version supportant Windows 10** | `setup.exe` + `portable.zip` x64 | Fonctionne ; dépendance à épingler avant de perdre Windows 10 |
| Windows 11 (x64) | Qt 6.12 | idem | Fonctionne |
| Windows 11 ARM64 | Qt 6.12 ARM64 existe, Azimut ne construit qu'en x64 | x64 | Fonctionne par **émulation x64** ; plus lent, pas de build natif |
| macOS 14.4+ Apple Silicon | PySide6 6.12 : roue `macOS 14.0+` | DMG `arm64` | Fonctionne |
| macOS 14.4+ Intel | idem | DMG `x64` (optionnel) | Fonctionne ; runners Intel retirés en 2027 |
| macOS 12 / 13 | **non supporté par PySide6 6.12** | bundle déclaré `12.0` | **Incohérence** : la version minimale annoncée est fausse |
| Linux x86_64, glibc ≥ 2.35 | PySide6 x86_64 : glibc ≥ 2.34 | AppImage | Fonctionne |
| Linux x86_64, glibc 2.34 (RHEL 9) | roue PySide6 compatible | AppImage | **Échoue** : l'AppImage est construite sur Ubuntu 22.04 (plancher 2.35) |
| Linux ARM64 | roue PySide6 ARM64 : glibc ≥ **2.39** | aucun paquet | **Non supporté** (pas de build, mauvais tag de plateforme) |
| Linux musl (Alpine) | pas de roue PySide6 | aucun paquet | Non supporté |
| Wayland (KDE, GNOME, wlroots…) | plugin `libqwayland.so` présent | — | Fonctionne ; quelques nuances de survol/menus |
| Xorg / XWayland | plugin `libqxcb.so` | — | Fonctionne ; dépend de `libxcb-cursor0` (embarquée par l'AppImage) |

### Les cinq points à traiter en premier

1. **macOS** : `LSMinimumSystemVersion` vaut `12.0` alors que le PySide6 utilisé exige macOS 14.
   C'est une promesse fausse : l'application se lance puis échoue au chargement des bibliothèques Qt.
2. **Windows 10** : la contrainte `PySide6>=6.6` laisse une reconstruction future passer à Qt 6.13,
   qui abandonne Windows 10. Il faut épingler la borne haute tant que Windows 10 est supporté.
3. **Fenêtres** : la fenêtre de module impose `960 × 640` ; sur un 1366 × 768 à 125 % (1093 × 614
   logiques) ou un 1920 × 1080 à 175 % (1097 × 617), elle est plus haute que l'écran.
4. **Aperçu PDF** : `PySide6-Essentials` ne contient pas QtPdf, et `packaging/azimut.spec` exclut QtPdf.
   L'aperçu PDF passe donc par un moteur système (poppler, Ghostscript ou ImageMagick) ; sans aucun
   de ces outils il affiche un repli explicite, et le test reste vert là où ils sont présents.
5. **Linux hors Ubuntu récent** : l'AppImage exclut RHEL 9 et Debian 11, et aucun paquet ARM64
   n'existe. La promesse « Linux » est plus étroite que ce que les utilisateurs entendent.

---

## 2. Méthode et limites

### Ce qui a été vérifié ici

Machine d'audit : **Fedora Linux 44 (KDE Plasma Desktop)**, session **Wayland**, noyau
`7.2.11`, Python **3.14.8**, PySide6 **6.12.0**, Qt **6.12.0**, x86_64, glibc **2.43**.

- `pytest -q` : **472 réussis, 1 échec, 1 ignoré** en 29 s (mesure d'origine, antérieure à l'audit de
  `docs/AUDIT/`). L'échec était `tests/ui/test_polish.py::test_preview_pdf_renders_page` (`QtPdf` absent de
  `PySide6-Essentials`) : il est corrigé depuis (AZ-C06, § 7).
- Les deux plateformes Qt démarrent réellement : `wayland` **et** `xcb` (XWayland) chargent,
  avec un écran 1920 × 1080, `devicePixelRatio = 1.0`, DPI logique 96.
- Le plugin Wayland est bien présent dans la roue PySide6
  (`PySide6/Qt/plugins/platforms/libqwayland.so`), comme le plugin `xcb`.
- Mesures hors écran : le plugin `offscreen` déclare un écran 800 × 800 ; Azimut n'a pas de taille
  minimale (0 × 0) ; la fenêtre de module impose 960 × 640 et demande 1180 × 780.
- Lecture statique de toutes les branches `sys.platform` : chemins, binaires, workers, fichiers,
  renommage, Takeout, Live Photos, packaging PyInstaller, AppImage, DMG, installeur Windows, CI.

### Ce qui n'a pas pu être exécuté

- Aucun lancement réel sous Windows ni macOS : les conclusions Windows/macOS viennent du code,
  des métadonnées de paquet et de la documentation officielle Qt/PySide6.
- La matrice des environnements de bureau est **raisonnée** (comportement Qt documenté + code),
  pas mesurée : seul KDE Plasma 6 Wayland et XWayland ont été exécutés ici.
- Aucun test sur écran HiDPI réel, sur second écran, ni sur lecteur réseau.

---

## 3. Socle technique : ce que Qt 6.12 / PySide6 6.12 supportent

| Élément | Support officiel 6.12 | Conséquence pour Azimut |
|---|---|---|
| Windows | 10 (1809+) x64, 11 x64, ARM64. **6.12 est la dernière version supportant Windows 10** | Ne pas dépasser Qt 6.12 tant que Windows 10 reste cible |
| macOS | Cible 14.4+ (y compris macOS 26) ; roue PyPI `macosx_14_0_universal2` | Relever la version minimale du bundle |
| Linux x86_64 | glibc 2.34+ | Cohérent avec un build sur Ubuntu 22.04 (2.35) |
| Linux ARM64 | roue PyPI `manylinux_2_39_aarch64` | Exige Ubuntu 24.04+, Debian 13+, Fedora 40+ |
| Python | Roues `abi3` CPython 3.10+ | Python 3.11+ déclaré ; 3.12 testé en CI ; 3.14 vérifié ici |
| Modules Qt | `PySide6-Essentials` exclut QtPdf, QtMultimedia, QtWebEngine… | QtMultimedia vient de `PySide6-Addons` (aperçu vidéo) ; l'aperçu PDF dépend d'un moteur système |

> Le paquet `pyproject.toml` déclare `PySide6>=6.6` (roue complète), tandis que
> `requirements.txt` déclare `PySide6-Essentials>=6.6`. Les deux chemins n'embarquent pas les
> mêmes modules Qt : toute fonctionnalité reposant sur un module « Addons » doit être soit
> ajoutée explicitement, soit retirée de l'interface.

---

## 4. Windows

### 4.1 Versions et architectures

| Système | Statut | Remarque |
|---|---|---|
| Windows 11 21H2 → 25H2 x64 | Supporté | Cible principale |
| Windows 10 1809 → 22H2 x64 | Supporté par Qt 6.12 | Fin du support Microsoft le 14 octobre 2025 ; dernière version Qt compatible |
| Windows 10 LTSC 2019 / 2021 | Supporté | Même base 1809 / 21H2 |
| Windows 11 ARM64 | Par émulation x64 | Pas de paquet natif ; encodage vidéo plus lent |
| Windows Server 2022 | Attendu | Non testé ; pas de support Qt officiel « desktop » |
| Windows 8.1 et antérieurs | Non supporté | Qt 6 exige Windows 10 |

### 4.2 Points de compatibilité

**W1 — Fin de Windows 10 avec Qt 6.13 (priorité haute).**
`requirements.txt` et `pyproject.toml` imposent seulement `PySide6>=6.6`. Une reconstruction
après la sortie de Qt 6.13 retirerait Windows 10 sans qu'aucun test ne le signale. Épingler
`PySide6>=6.6,<6.13` (et documenter la décision) tant que Windows 10 est supporté.

**W2 — Chemins longs (priorité haute).**
Les exports Google Takeout produisent des chemins profonds, souvent au-delà de 260 caractères.
Windows ne les accepte que si **deux** conditions sont réunies : la stratégie
`LongPathsEnabled` est active et l'exécutable est déclaré `longPathAware` dans son manifeste.
Les PyInstaller récents activent `longPathAware` dans le manifeste généré ; il faut le vérifier
pour la version utilisée (`pyinstaller>=6.6`) et documenter la clé de registre. Un test avec un
chemin de 300 caractères est indispensable : c'est un cas d'usage central (Takeout), pas un cas
limite théorique.

**W3 — ARM64 (priorité moyenne).**
`platform_tag()` renvoie toujours `windows-x64` (`azimut/core/binaries.py:73`) : même si un
dossier `bin/windows-arm64` existait, il ne serait jamais consulté. Le paquet x64 fonctionne
sur Windows 11 ARM64 par émulation, FFmpeg compris, avec une perte de performance à l'encodage.

**W4 — SmartScreen et antivirus (priorité support).**
Le paquet n'est pas signé et l'UPX est désactivé (bon choix). SmartScreen affiche donc
« Windows a protégé votre ordinateur », et certains antivirus peuvent mettre en quarantaine un
exécutable PyInstaller non signé. C'est documenté dans `RELEASING.md` ; un certificat de
signature reste la seule correction durable.

**W5 — Écrans à échelle différente (corrigé).**
`azimut/ui/icons.py` prenait le facteur HiDPI sur `QGuiApplication.primaryScreen()` uniquement,
résultat mis en cache. Désormais `_active_screen()` lit l'écran de la fenêtre active (la primaire
ne sert que de repli), le cache de rendu est indexé par densité, et un objet Qt déjà détruit est
ignoré. Sur un portable 150 % relié à un écran 100 %, chaque écran est donc rendu à sa propre
échelle. Le zoom suit le même principe : mémorisé par écran (nom + facteur d'échelle, clé
`zoom_by_screen`), il est réévalué quand la fenêtre change d'écran.

**W6 — CLI silencieuse dans le paquet (priorité moyenne).**
`console=False` dans `packaging/azimut.spec` : `Azimut.exe --list`, `--version` et `--help` n'écrivent
rien, même avec une redirection. C'est cohérent avec une application à fenêtres, mais cela
interdit toute automatisation sur Windows. `--selftest` contourne le problème en écrivant un
JSON ; documenter cette voie ou produire une variante console.

**W7 — OneDrive et points d'analyse (priorité moyenne).**
Les fichiers « à la demande » de OneDrive ont une taille apparente nulle et sont hydratés à la
lecture. Dedup peut donc télécharger toute une bibliothèque cloud en croyant calculer des
hashes locaux. Détecter `FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS` (ou au minimum prévenir dans
l'interface) éviterait une surprise coûteuse.

**W8 — Ce qui est déjà correct.**
`CREATE_NO_WINDOW` est appliqué à tous les sous-processus (`binaries.py`, `workers.py`,
`azimut/ui/widgets.py`, `azimut/modules/dedup/livephoto.py`) : aucune fenêtre noire ne clignote. Les collisions de noms sont
comparées en minuscules dans Rename (`taken`, `entries`), ce qui correspond au système de
fichiers insensible à la casse. Les caractères interdits sont neutralisés
(`sanitize_prefix`, `_ILLEGAL`), et les noms finissant par un point ou une espace sont nettoyés.
L'installeur Inno installe par utilisateur (`PrivilegesRequired=lowest`), ce qui évite UAC.

**W9 — Verrous et partages réseau (priorité basse).**
Un fichier ouvert dans l'Explorateur ou analysé par l'antivirus peut provoquer une erreur de
partage lors du renommage ou de la conversion. Les erreurs sont déjà remontées fichier par
fichier ; rien de bloquant.

### 4.3 Protocole de test Windows

1. Machine virtuelle Windows 10 22H2 **et** Windows 11 x64, écran 1366 × 768 à 125 %.
2. Installer en silencieux, lancer `--selftest`, vérifier `rapport.json`.
3. Ouvrir Azimut, chaque module, redimensionner la fenêtre à la taille minimale : aucun contrôle
   ne doit sortir de l'écran.
4. Créer une arborescence Takeout de plus de 300 caractères, lancer Takeout puis Rename.
5. Brancher un second écran à une autre échelle et déplacer la fenêtre.
6. Convertir une vidéo en H.264 ; vérifier qu'aucune console ne s'ouvre.
7. Vérifier le comportement d'un fichier OneDrive « à la demande » dans Dedup.

---

## 5. macOS

### 5.1 Versions et architectures

| Système | Statut | Remarque |
|---|---|---|
| macOS 26 (Tahoe) | Attendu | Qt 6.12 le cite explicitement |
| macOS 15 (Sequoia) | Attendu | Base des runners `macos-15` |
| macOS 14.4+ (Sonoma) | Supporté | Cible minimale de Qt 6.12 |
| macOS 14.0 → 14.3 | Limite | Roue taguée 14.0, cible Qt 14.4 : déclarer 14.4 |
| macOS 13 Ventura | **Non supporté** | Exigerait d'épingler une version antérieure de PySide6 |
| macOS 12 Monterey | **Non supporté** | idem |
| Intel x64 | Supporté | DMG séparé ; runners Intel retirés en 2027 |
| Apple Silicon | Supporté | DMG `arm64` |

### 5.2 Points de compatibilité

**M1 — Version minimale annoncée fausse (priorité haute).**
`packaging/azimut.spec:180` déclare `LSMinimumSystemVersion = 12.0`. Or la roue PySide6 6.12
est taguée `macOS 14.0+` et Qt 6.12 cible 14.4. Sur macOS 12 ou 13, le bundle peut être copié
et lancé, puis échouer au chargement de `QtCore` : l'utilisateur voit une application qui
« ne s'ouvre pas », sans message utile. Relever la valeur à `14.4` (ou `14.0` au minimum) et
l'indiquer dans `RELEASING.md`. Si le support de macOS 12/13 est un objectif, il faut au
contraire épingler PySide6 6.6–6.8 et le tester réellement.

**M2 — Signature ad hoc, pas de notarisation (priorité support).**
`make_dmg.sh` signe chaque binaire Mach-O en ad hoc, ce qui est indispensable sur Apple
Silicon et suffisant pour exécuter l'application, mais Gatekeeper affiche « Azimut ne peut pas
être vérifié ». La parade est documentée (`xattr -dr com.apple.quarantine`). La notarisation
Apple reste payante et hors périmètre.

**M3 — Autorisations TCC (priorité haute pour l'usage réel).**
Une application non sandboxée déclenche quand même les demandes macOS lors du premier accès
aux dossiers **Bureau**, **Documents**, **Téléchargements**, aux volumes amovibles et aux
dossiers réseau. Un refus se traduit par `PermissionError`/`OSError` dans les modules. Il faut
tester ce parcours et afficher un message explicite (« Autorisez Azimut dans Réglages Système →
Confidentialité et sécurité → Accès aux fichiers »), sinon l'utilisateur conclut à un bug.

**M4 — Unicode NFC/NFD (priorité moyenne).**
macOS stocke historiquement les noms en forme décomposée (NFD). `Rename` gère explicitement
NFC **et** NFD (`resolve_path`), et les Live Photos normalisent en NFC (`livephoto._fold`).
En revanche, Takeout indexe les JSON avec `title.lower()` sans normalisation
(`azimut/modules/takeout/engine.py`, `DirIndex`) et la recherche de doublons compare des
chaînes brutes. Un média copié depuis un volume exFAT (NFC) à côté d'un JSON extrait sur APFS
(NFD) peut donc être classé « Sans JSON », avec un accent pour seule différence.

**M5 — Système de fichiers insensible à la casse (priorité moyenne).**
APFS est insensible à la casse par défaut, mais `os.path.normcase` est une identité sous POSIX.
Dans `azimut/modules/convert/engine.py`, `path_key` ne distingue donc pas `IMG.JPG` et `img.jpg`, qui sont
pourtant le même fichier. Dedup peut traiter deux fois le même média ou manquer une paire.
Normaliser en `casefold()` sur `darwin` (et Windows) corrigerait le cas.

**M6 — Barre de menus et raccourcis (priorité basse).**
L'application ne crée pas de `QMenuBar`. Qt fournit donc le menu applicatif minimal (Quitter),
mais pas d'entrée « Préférences… » ni « À propos ». Les raccourcis `Ctrl+,`, `Ctrl+T`,
`Ctrl+Entrée` sont traduits en ⌘ par Qt et fonctionnent dans la fenêtre. À vérifier tout de
même : `⌘Q` pendant un traitement doit passer par la confirmation « traitements en cours ».

**M7 — Chemins dans le bundle (déjà correct).**
`app_root()` utilise `sys._MEIPASS` (dossier `Contents/Frameworks` pour un `.app`), et le spec
embarque `bin/<tag>` au même niveau. `make_dmg.sh` exécute l'auto-test depuis le DMG monté :
c'est la bonne vérification. ExifTool et Perl portables sont inclus ; les binaires téléchargés
par `fetch_binaries.py` n'ont pas l'attribut de quarantaine (téléchargement direct).

**M8 — Fin des runners Intel (planification).**
Apple abandonne Intel et GitHub retirera ses runners en 2027. La version Intel est déjà
marquée optionnelle dans la CI ; il faudra décider explicitement de l'arrêter, pas la laisser
casser silencieusement.

### 5.3 Protocole de test macOS

1. macOS 14.4 et 15 (Apple Silicon), plus un Mac Intel si le DMG est conservé.
2. Monter le DMG, copier dans `/Applications`, lancer par double-clic (Gatekeeper), puis
   `xattr -dr com.apple.quarantine` et relancer.
3. Ouvrir un dossier du Bureau, des Documents et un volume externe : vérifier les demandes TCC.
4. Prendre des photos au nom accentué (`Café.jpg`) et vérifier Takeout, Rename, Dedup.
5. Vérifier `⌘Q` pendant un traitement et `⌘,`.
6. Vérifier l'aperçu des formats HEIC, AVIF, RAW et PDF.

---

## 6. Linux

### 6.1 Distributions et plancher glibc

Les paquets Linux (AppImage, .deb, .rpm, .tar.gz) sont construits sur **Ubuntu 22.04** (glibc 2.35).
C'est le plancher réel des paquets, pas celui de PySide6 (2.34).

| Distribution | glibc | Paquets x86_64 | Remarque |
|---|---|---|---|
| Ubuntu 24.04 / 24.10 / 25.04 | 2.39+ | Oui | |
| Ubuntu 22.04 | 2.35 | Oui | Base de construction |
| Ubuntu 20.04 | 2.31 | **Non** | Fin de vie ; roue PySide6 elle-même trop récente |
| Debian 13 | 2.41 | Oui | |
| Debian 12 | 2.36 | Oui | |
| Debian 11 | 2.31 | **Non** | |
| Linux Mint 22 | 2.39 | Oui | |
| Linux Mint 21 | 2.35 | Oui | |
| Linux Mint 20 | 2.31 | **Non** | |
| RHEL 9 / AlmaLinux 9 / Rocky 9 | 2.34 | **Non** | 0.01 sous le plancher : frustrant, et corrigeable |
| RHEL 8 | 2.28 | Non | |
| RHEL 10 | 2.39 | Oui | |
| Fedora 40 → 44 | 2.39+ | Oui | 2.43 vérifié ici |
| Fedora 36 → 39 | 2.35+ | Oui | |
| openSUSE Leap 15.6 | 2.38 | Oui | |
| openSUSE Tumbleweed | récent | Oui | |
| Arch / Manjaro | récent | Oui | |
| Alpine (musl) | — | **Non** | Aucune roue musl |
| Raspberry Pi OS 64 bits (bookworm) | 2.36 | **Non** | Pas de paquet ARM64, et roue ARM64 exigeant 2.39 |

**Conséquence pratique.** « Linux » signifie aujourd'hui : x86_64, glibc ≥ 2.35. FUSE 2 n'est
nécessaire que pour l'AppImage (ou `--appimage-extract-and-run`) ; les paquets `.deb`, `.rpm` et
`.tar.gz` n'en dépendent pas. C'est une part importante du parc, mais pas RHEL 9, pas Debian 11,
pas ARM64. Deux options :

- construire dans un conteneur `manylinux_2_34` pour descendre à 2.34 et couvrir RHEL 9 ;
- ou assumer le plancher 2.35 et publier en plus un Flatpak (qui embarque son runtime et règle
  à la fois glibc, FUSE et l'intégration bureau).

### 6.2 Wayland et Xorg

Azimut est dans la meilleure catégorie possible pour Wayland : **pas d'icône de zone de
notification, pas de raccourci global, pas de capture d'écran, pas de positionnement de fenêtre
au démarrage**. Ce sont les quatre sources classiques d'incompatibilité. Le reste est géré par
Qt.

| Sujet | Wayland | Xorg / XWayland |
|---|---|---|
| Plugin | `libqwayland.so` (présent) | `libqxcb.so` (présent) |
| Décorations | Client-side (Qt) ; GNOME ne fait pas de SSD | Serveur (KWin, Xfwm, Muffin…) |
| Identité de fenêtre | `setDesktopFileName("azimut")` + `azimut.desktop` de l'AppImage → icône correcte | `WM_CLASS` |
| Positionnement | Impossible par l'application ; Azimut ne le fait pas | Libre |
| Menus contextuels | Position globale ignorée : le menu s'ancre à la fenêtre | Position respectée |
| Pointeur global | `QCursor.pos()` peut être périmé ou (0,0) | Fiable |
| Échelle fractionnaire | Appliquée (Qt 6.12) ; forcer `QT_SCALE_FACTOR_ROUNDING_POLICY=PassThrough` si arrondi | Souvent flou sans `QT_SCALE_FACTOR` |
| Glisser-déposer | Standard entre applications natives ; passe par le portail pour les apps sandboxées | Standard |
| Boîtes de dialogue | Portail XDG (GNOME/KDE) ou dialogue Qt | Dialogue Qt |
| Presse-papiers | Géré par Qt | Géré par Qt |
| `QT_QPA_PLATFORM=xcb` | Fonctionne via XWayland (vérifié) | — |
| `QT_QPA_PLATFORM=wayland` | Vérifié | — |

Deux points de code méritent une retouche :

- `azimut/launcher/atlas_window.py:663` utilise `QCursor.pos()` pour savoir quel onglet est
  survolé. Sous Wayland, la position globale est restreinte : entrer directement sur la croix
  de fermeture peut ne pas la révéler. Un suivi `Enter`/`Leave` sur les boutons d'onglet est
  plus fiable et supprime la dépendance au pointeur global.
- `azimut/ui/widgets.py:465` ouvre le menu de choix avec `menu.exec(position_globale)`. Sous
  Wayland, la position est ignorée et le menu s'affiche ancré à la fenêtre, ce qui reste
  utilisable. Passer le widget parent plutôt qu'une position globale rend le comportement
  identique partout.

### 6.3 Environnements de bureau

Statut « Attendu » = déduit du comportement Qt et du code, non exécuté pendant cet audit.
Seul **KDE Plasma 6 Wayland + XWayland** a été exécuté.

| Environnement | Session | Décorations / dialogues | Statut |
|---|---|---|---|
| KDE Plasma 6 | Wayland (défaut) | SSD KWin, portail KDE | **Vérifié** (Fedora 44) |
| KDE Plasma 5.27 | X11 (défaut) | SSD KWin | Attendu |
| GNOME 45 → 49 | Wayland (défaut) | CSD Qt, portail GNOME, pas de SSD | Attendu ; tester le thème sombre |
| XFCE 4.18 / 4.20 | X11 | Xfwm, dialogues Qt (pas de portail) | Attendu |
| Cinnamon 6 | X11 | Muffin | Attendu |
| MATE 1.28 | X11 | Marco | Attendu |
| Budgie 10.9 | Wayland | Magpie | Attendu |
| LXQt 2.x | X11 / Wayland (labwc) | Openbox/labwc | Attendu |
| Deepin 23 | Wayland / X11 | DDE, thème Qt maison | À tester : risque de conflit de thème de plateforme |
| Pantheon 8 (elementary) | Wayland | GTK, portail | Attendu |
| COSMIC (Epoch 1) | Wayland | smithay | Attendu |
| i3 / Sway / Hyprland / river / niri | X11 / Wayland | Tuilage, pas de décoration | Voir 6.4 |
| WSLg (Windows 11) | Wayland + XWayland | — | Attendu ; accès `/mnt/c` lent |
| ChromeOS Crostini | Wayland | — | Attendu ; FUSE disponible |

Aucun de ces environnements ne nécessite de code spécifique : le style `Fusion`
(`azimut/ui/app.py:29`), la palette Qt complète et les icônes SVG embarquées garantissent un
rendu identique, indépendamment du thème GTK/Qt du système. C'est un choix robuste : les icônes
ne dépendent pas d'une police d'emoji ni d'un thème d'icônes installé.

### 6.4 Fenêtres et gestionnaires de tuilage

Le vrai risque Linux n'est pas le serveur graphique, c'est la **taille minimale** :

- `ModuleWindow.setMinimumSize(960, 640)` (`azimut/ui/module_window.py:126`) ;
- `PreferencesDialog` et `TipsDialog` se dimensionnent sur leur contenu, puis sont plafonnés à la
  zone utile de l'écran (`GlassDialog.fit_to_content`, `azimut/ui/glass.py`) ; plus de largeur
  minimale figée à 760 px.

Sur un 1366 × 768 à 125 %, l'espace logique est 1093 × 614, moins le panneau : la fenêtre de
module ne tient pas en hauteur. Sur un 1920 × 1080 à 175 %, 1097 × 617 : même problème. Sous
i3/Sway/Hyprland, la fenêtre est tuilée et la barre d'actions peut être repoussée hors de
l'écran ; les dialogues flottants de 760 px débordent d'un écran 1024 × 600.

Correctif appliqué : `azimut/core/screenfit.py` plafonne les fenêtres de module et Atlas à la
zone utile de l'écran ; `GlassDialog.fit_to_content` dimensionne les boîtes Préférences et
Conseils sur leur contenu affiché, en recalculant la hauteur à la largeur retenue, puis les
plafonne de la même façon.

### 6.5 Packaging et intégration

- **FUSE 2** : l'AppImage en a besoin. Ubuntu 24.04 fournit `libfuse2t64`, Fedora fournit
  `libfuse.so.2` (vérifié ici), mais certaines distributions minimales ne l'ont pas. Le repli
  `--appimage-extract-and-run` est documenté ; un lanceur qui teste FUSE et affiche la marche
  à suivre serait plus clément.
- **Montages `noexec`** (`/tmp`, clés USB) : l'AppImage doit alors être copiée dans le dossier
  personnel ou extraite.
- **SELinux** (Fedora, RHEL) : exécuter depuis `~/Téléchargements` fonctionne ; les contextes
  restrictive sur certains montages réseau peuvent bloquer l'exécution.
- **Flatpak** : absent, alors qu'il réglerait glibc, FUSE et l'intégration des menus. C'est le
  format attendu sur Fedora Silverblue/Kinoite et de plus en plus sur les distributions
  immuables.
- **Snap** : absent ; moins prioritaire si Flatpak est fait.
- **Paquets `.deb`/`.rpm`** : fournis pour Debian/Ubuntu/Mint et Fedora/RHEL 10+/openSUSE ; ils
  installent dans `/opt/azimut` et restent soumis au plancher glibc 2.35 (donc pas RHEL 9).

### 6.6 Protocole de test Linux

1. Ubuntu 24.04 (GNOME Wayland), Fedora 44 KDE (Wayland), Debian 12 XFCE (X11).
2. Installer le `.deb` (Debian/Ubuntu) ou le `.rpm` (Fedora), ou lancer l'AppImage, avec
   `QT_QPA_PLATFORM=wayland`, puis `xcb`, puis sans variable.
3. Vérifier l'icône dans la barre des tâches et le lanceur (app_id `azimut`).
4. Tester un écran 1366 × 768 à 125 % et un 1920 × 1080 à 175 % : toutes les commandes doivent
   rester accessibles.
5. Glisser-déposer depuis Nautilus, Dolphin et Thunar.
6. Vérifier le menu « Choisir des fichiers / un dossier » et les dialogues natifs.
7. Tester RHEL 9 : l'AppImage doit échouer proprement (message compréhensible), pas planter.
8. Tester un Raspberry Pi 5 : vérifier que l'absence de paquet ARM64 est annoncée, pas subie.

---

## 7. Défauts transverses, par priorité

| ID | Priorité | Défaut | Preuve | Correction |
|---|---|---|---|---|
| AZ-C01 | **P0** | macOS minimal annoncé 12.0, réel 14.0/14.4 | `packaging/azimut.spec:180` | Relever à 14.4 et documenter, ou épingler une ancienne PySide6 — **Corrigé : `LSMinimumSystemVersion` = 14.4** |
| AZ-C02 | **P0** | Windows 10 perdu à la prochaine version de Qt | `requirements.txt`, `pyproject.toml` | Épingler `PySide6>=6.6,<6.13` |
| AZ-C03 | P1 | Fenêtre de module 960 × 640 plus grande que l'écran | `azimut/ui/module_window.py:126` | Taille adaptée à `availableGeometry()` — **Corrigé : `azimut/core/screenfit.py`** |
| AZ-C04 | P1 | ARM64 Linux/Windows non détecté | `azimut/core/binaries.py:73` | Lire `platform.machine()` ; tags `linux-arm64`, `windows-arm64` — **Corrigé : tags `windows-arm64` et `linux-arm64` (repli sur les binaires du système)** |
| AZ-C05 | P1 | AppImage exclut RHEL 9 / Debian 11 | CI `ubuntu-22.04` | Build `manylinux_2_34`, ou Flatpak |
| AZ-C06 | ~~P1~~ | Aperçu PDF : QtPdf absent du paquet, rendu assuré par poppler/Ghostscript/ImageMagick | `azimut/ui/widgets.py` (`_render_pdf_external`) | Corrigé ; déclarer un moteur système dans les dépendances du paquet |
| AZ-C07 | P1 | Chemins > 260 caractères sous Windows | `packaging/azimut.spec` (manifeste) | Vérifier `longPathAware`, documenter la stratégie, tester 300 caractères |
| AZ-C08 | P2 | Icônes HiDPI calculées sur l'écran principal seulement | `azimut/ui/icons.py` (`_device_pixel_ratio`) | Facteur de l'écran du widget, ou maximum des écrans — **Corrigé : `_active_screen()`, écran de la fenêtre active, primaire en repli** |
| AZ-C09 | P2 | Taille de police fixe 10 pt, ignore l'échelle système | `azimut/ui/theme.py:20,350` | Partir de `app.font().pointSizeF()` et appliquer le zoom |
| AZ-C10 | P2 | NFC/NFD non normalisé dans Takeout/Dedup | `azimut/modules/takeout/engine.py` (`DirIndex`) | Normaliser en NFC toutes les clés de noms — **Corrigé : `name_key()` (NFC + minuscules) dans `DirIndex`** |
| AZ-C11 | P2 | Casse ignorée à tort sous macOS | `azimut/modules/convert/engine.py` (`path_key`) | `casefold()` sur `darwin` |
| AZ-C12 | P2 | CLI muette dans le paquet Windows | `packaging/azimut.spec` (`console=False`) | Variante console ou documentation de `--selftest` |
| AZ-C13 | P2 | OneDrive « à la demande » hydraté par Dedup | `fsutil.iter_files` | Détecter les points d'analyse et avertir |
| AZ-C14 | P2 | Dialogues de 680–760 px sur petits écrans | `azimut/ui/preferences_dialog.py`, `azimut/launcher/tips_dialog.py` | Taille dérivée du contenu et plafonnée à l'écran — **Corrigé : `GlassDialog.fit_to_content`** |
| AZ-C15 | P3 | Hover d'onglet via `QCursor.pos()` sous Wayland | `azimut/launcher/atlas_window.py:663` | Suivi `Enter`/`Leave` |
| AZ-C16 | P3 | Menu contextuel positionné globalement | `azimut/ui/widgets.py:465` | Ancrer au widget parent |
| AZ-C17 | P3 | Pas de Flatpak/Snap, pas de `.deb`/`.rpm` | packaging | Flatpak en priorité |
| AZ-C18 | P3 | Menu macOS sans « Préférences… » / « À propos » | absence de `QMenuBar` | Ajouter un `QMenuBar` natif |

### Ce qui est déjà solide

- Répertoires utilisateur corrects : `%APPDATA%\Azimut`, `~/Library/Application Support/Azimut`,
  XDG (`XDG_CONFIG_HOME`, `XDG_DATA_HOME`) — `azimut/core/paths.py`.
- Aucune écriture à côté de l'exécutable ; écritures atomiques (`atomic_write_text`) ;
  publication sans écrasement (`publish_no_replace`), avec repli pour FAT/exFAT et partages
  réseau.
- Tous les sous-processus sont lancés sans console sous Windows et avec une liste blanche de
  protocoles FFmpeg (`-protocol_whitelist file` + préfixe `file:`).
- Icônes SVG embarquées : aucun « tofu » si la police d'emoji manque, cas fréquent sous
  Fedora/KDE.
- Corbeille optionnelle (`send2trash`), avec repli explicite ; le mode est masqué si le module
  est absent.
- Aucune icône de zone de notification, aucun raccourci global : les deux plus gros pièges
  Wayland sont évités par conception.
- Auto-test `--selftest` déjà branché sur les trois systèmes dans la CI.

---

## 8. Angles morts de la CI

La CI actuelle teste **Ubuntu 22.04, Windows et macOS**, toujours avec
`QT_QPA_PLATFORM=offscreen`. Elle ne teste donc jamais un vrai serveur d'affichage, ni un
environnement de bureau, ni une architecture ARM.

| Manque | Proposition |
|---|---|
| Wayland | Job Ubuntu avec un compositeur sans écran (`weston --backend=headless` ou `sway` avec `WLR_BACKENDS=headless`), puis lancement de l'application avec `QT_QPA_PLATFORM=wayland` et sortie automatique après 3 s |
| Xorg | `xvfb-run` + `QT_QPA_PLATFORM=xcb`, même test de démarrage |
| GNOME / KDE / XFCE | Un job par environnement (conteneurs ou `xvfb` + thème), au minimum un démarrage et un rendu de fenêtre |
| Linux ARM64 | Runner `ubuntu-24.04-arm` : build PyInstaller + `--selftest` |
| Windows 10 | Pas de runner GitHub disponible : VM auto-hébergée ou test manuel obligatoire |
| Écrans HiDPI / petits | Le plugin `offscreen` ne simule qu'un 800 × 800 ; tester la fonction de dimensionnement sans écran |
| Chemins longs | Test unitaire Windows créant un chemin de 300 caractères |
| NFD / casse | Test macOS créant un fichier accentué puis un JSON en forme opposée |
| PDF | Le test doit être soit vert (QtPdf présent), soit explicitement ignoré si le module manque |

Ajouter au `--selftest` quatre mesures rendrait les rapports exploitables :
`QGuiApplication.platformName()`, type de session (`XDG_SESSION_TYPE`), environnement de
bureau (`XDG_CURRENT_DESKTOP`), géométrie et `devicePixelRatio` de chaque écran. Sur Windows et
macOS, ajouter la version exacte du système et le résultat d'un test de chemin long.

---

## 9. Application mobile

### 9.1 Ce que Qt permet aujourd'hui

- **Android** : PySide6 déploie depuis Qt 6.5 (`pyside6-android-deploy`).
- **iOS** : support ajouté dans Qt 6.12 (octobre 2026), via `pyside6-ios-deploy` et un projet
  Xcode généré.

### 9.2 Pourquoi Azimut n'est pas portable en l'état

| Obstacle | Android | iOS |
|---|---|---|
| Pillow, pillow-heif, rawpy (extensions C) | Roues mobiles absentes de PyPI | **Interdites** : aucune roue iOS, et Qt 6.12 ne supporte pas les paquets tiers à extension C |
| FFmpeg / FFprobe | Cross-compilation possible mais lourde | Cross-compilation + règles App Store |
| ExifTool | Dépend de **Perl** : irréaliste sur Android/iOS | idem |
| Accès aux dossiers | Scoped storage, SAF | Bac à sable, sélecteur de documents uniquement |
| Traitements longs | Service de premier plan | Temps d'exécution en arrière-plan très limité |

Autrement dit : les trois quarts de la valeur d'Azimut reposent sur des binaires externes
(FFmpeg, ExifTool, Perl) et sur des bibliothèques natives (libraw, libheif). Ce n'est pas un
portage d'interface, c'est un changement de moteur.

### 9.3 Options réalistes

1. **Application compagne (recommandé pour commencer)** : une app mobile qui pilote Azimut sur
   l'ordinateur (file d'attente, progression, transfert) ou qui sert de simple visionneuse.
   Coût faible, aucune duplication des moteurs, pas de contrainte App Store sur FFmpeg.
2. **Android autonome, périmètre restreint** : Takeout, Rename et Tags reposent sur
   métadonnées et JSON, pas sur FFmpeg. Une UI Kotlin/Compose + Exiv2 (ou ExifTool compilé
   avec un Perl embarqué) couvrirait ces trois modules. Convert et Dedup resteraient au bureau.
3. **Réécriture native complète** : Kotlin/Swift + libraw/libheif/FFmpeg/Exiv2. C'est un
   second produit, avec son propre cycle de test et ses propres binaires.
4. **PySide6 mobile tel quel** : à écarter pour iOS (pas de Pillow) et risqué sur Android
   (pas de roues Pillow/rawpy).

### 9.4 Ce qu'il faut préserver dès maintenant

Les moteurs sont déjà majoritairement sans Qt : `takeout`, `rename`, `dedup`, `scope`,
`convert/engine`, `tags`. Deux exceptions à isoler :

- `azimut/core/workers.py` importe `QThread` et `Signal` : séparer l'exécution de processus
  (`run_process`, `map_parallel`) de la classe Qt permettrait de la réutiliser sur mobile ou
  dans un service.
- `azimut/ui/widgets.py` mélange aperçu et UI : `load_preview` dépend de `QImage`, donc de Qt
  GUI, et ne peut pas tourner dans un service sans interface.

Un contrat JSON (entrée : chemins et options, sortie : événements de progression et rapport)
au-dessus de ces moteurs rendrait possible n'importe quelle interface future — mobile, web ou
ligne de commande — sans réécrire la logique. C'est le seul investissement d'architecture qui
ait un sens aujourd'hui.

---

## 10. Plan d'action

### Avant la prochaine version (P0)

1. Relever `LSMinimumSystemVersion` à `14.4` et l'écrire dans `RELEASING.md`.
2. Épingler `PySide6>=6.6,<6.13` tant que Windows 10 est supporté ; ajouter une note dans
   `BUILD.md`.
3. Trancher le PDF : ajouter QtPdf (roue complète ou `PySide6-Addons`) ou retirer l'aperçu PDF
   de l'interface et marquer le test `skipif` quand QtPdf manque.

### Court terme (P1)

4. Dimensionner les fenêtres et dialogues selon `availableGeometry()`.
5. Corriger `platform_tag()` (ARM64 Linux/Windows) et décider du support ARM64.
6. Décider du plancher Linux : build `manylinux_2_34` ou Flatpak.
7. Vérifier et tester les chemins longs Windows.
8. Ajouter les jobs CI Wayland, X11 et ARM64.

### Moyen terme (P2)

9. Facteur HiDPI par écran.
10. Police de base dérivée de la police système.
11. Normalisation NFC et casse macOS dans Takeout/Dedup.
12. CLI Windows et documentation OneDrive.
13. Ajustement des largeurs minimales des dialogues.

### Plus tard (P3)

14. Flatpak, puis éventuellement `.deb`/`.rpm`.
15. Menu macOS natif.
16. Signature Windows / notarisation Apple si le budget le permet.
17. Application compagne mobile.

---

## Annexe A — Commandes de diagnostic

```bash
# Plateforme Qt réellement utilisée
python -c "from PySide6.QtWidgets import QApplication; import sys; a=QApplication(sys.argv); \
from PySide6.QtGui import QGuiApplication; print(QGuiApplication.platformName())"

# Forcer Wayland / Xorg
QT_QPA_PLATFORM=wayland python main.py
QT_QPA_PLATFORM=xcb     python main.py

# Vérifier les plugins disponibles
python -c "import PySide6, pathlib; p=pathlib.Path(PySide6.__file__).parent/'Qt'/'plugins'/'platforms'; \
print([x.name for x in p.glob('*')])"

# glibc et FUSE
ldd --version | head -1
ls /usr/lib*/libfuse.so.2 2>/dev/null || echo "FUSE 2 absent"

# Session et bureau
echo "$XDG_SESSION_TYPE / $XDG_CURRENT_DESKTOP"

# Écrans et échelle
python - <<'PY'
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QGuiApplication
import sys
a = QApplication(sys.argv)
for s in QGuiApplication.screens():
    print(s.name(), s.geometry().size(), s.availableGeometry().size(), s.devicePixelRatio())
PY
```

## Annexe B — Checklist manuelle minimale par plateforme

| # | Vérification | Windows | macOS | Linux |
|---|---|---|---|---|
| 1 | Démarrage d'Azimut et des six modules | ☐ | ☐ | ☐ |
| 2 | Fenêtre à la taille minimale, petit écran | ☐ | ☐ | ☐ |
| 3 | Binaires détectés « embarqué » (FFmpeg, FFprobe, ExifTool) | ☐ | ☐ | ☐ |
| 4 | Takeout sur une arborescence profonde | ☐ | ☐ | ☐ |
| 5 | Rename avec accents et casse | ☐ | ☐ | ☐ |
| 6 | Dedup, mode corbeille | ☐ | ☐ | ☐ |
| 7 | Convert H.264 + HEIC + AVIF | ☐ | ☐ | ☐ |
| 8 | Aperçu image, vidéo, PDF, RAW | ☐ | ☐ | ☐ |
| 9 | Glisser-déposer depuis le gestionnaire de fichiers | ☐ | ☐ | ☐ |
| 10 | Thème clair/sombre et zoom 80–160 % | ☐ | ☐ | ☐ |
| 11 | Deuxième écran à une autre échelle | ☐ | ☐ | ☐ |
| 12 | Fermeture pendant un traitement (confirmation) | ☐ | ☐ | ☐ |
| 13 | Aucune écriture à côté de l'exécutable | ☐ | ☐ | ☐ |
| 14 | `--selftest` vert sur le paquet installé | ☐ | ☐ | ☐ |
