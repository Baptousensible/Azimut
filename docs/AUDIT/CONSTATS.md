# Registre des constats — audit d'Azimut 0.2.0 (état intermédiaire)

Format imposé (§ 6 des consignes). « Preuve » = commande exécutée ou test, sinon la mention **lecture de code** est explicite.
Périmètre réellement lu : voir `RAPPORT_FINAL.md` § « Ce qui n'a pas été lu ». L'absence de constat ailleurs ne prouve rien.
Les constats antérieurs au lot 0 ont été mesurés avec l'exécuteur de remplacement (`outils/README.md`) ; depuis le lot 0,
les tests sont rejoués avec pytest + Qt réels (544 réussis, 2 ignorés au 7 novembre 2026).

## A. Corrigés (avec test ou preuve)

```
ID: LOG-001 | Gravité: P0 | Module: dedup | Axe: logique / perte de données
Constat : `check_item` ne revérifiait que la TAILLE de l'exemplaire conservé, pas sa date de modification.
Preuve  : test rouge `test_keeper_rewritten_in_place_with_same_size_blocks_deletion` : exemplaire gardé réécrit sur
          place (même taille, mtime +30 s) → la copie « à supprimer » était supprimée (AssertionError « la copie ne
          doit pas être supprimée »).
Impact  : le seul exemplaire d'origine pouvait être détruit (suppression définitive en mode « delete »).
Correctif: azimut/modules/dedup/actions.py (`check_item`, tolérance MTIME_TOLERANCE) — commit 1a7120f | Statut: Corrigé
Test    : tests/dedup/test_actions.py (2 tests) ; `tests/dedup` : 42 PASS. Depuis DEDUP-002, un échange à taille
          ET date identiques est détecté en suppression définitive par re-hachage du gardien (voir DECISIONS.md D4).
```
```
ID: SEC-001 | Gravité: P2 | Module: tags (utilisé par convert, rename) | Axe: sécurité
Constat : `write_argfile` sautait le contrôle « chemin absolu » pour toute ligne contenant « = » : `a=b.jpg` (relatif) accepté.
Preuve  : `write_argfile(["a=b.jpg"])` → accepté (avant) ; test rouge `…relative_path_with_equals…` (DID NOT RAISE).
Impact  : un chemin relatif pouvait être résolu par ExifTool depuis le dossier courant (mauvais fichier modifié).
Correctif: `if line.startswith("-")` seulement — commit 50d916b | Statut: Corrigé
Test    : tests/tags/test_engine.py::test_argfile_relative_path_with_equals_is_not_an_assignment (les appelants
          passent des lignes « -Option » ou des chemins absolus : vérifié par lecture + tests existants verts).
```
```
ID: I18N-002 | Gravité: P3 | Module: core/i18n | Axe: logique
Constat : `normalize_language("in")` renvoyait « id » et « ji » → « yi » : codes absents du registre (docstring : « "" si inconnu »).
Preuve  : exécution directe ; tests rouges (3 FAIL) puis verts (23 PASS dans test_i18n.py).
Impact  : faible (`resolve_language` filtre ensuite), mais contrat de la fonction faux.
Correctif: alias « in » et « ji » retirés de `_ALIASES` — commit 54234e1 | Statut: Corrigé
Test    : tests/core/test_i18n.py (2 tests dont un invariant « toute cible d'alias ∈ registre »).
```
```
ID: H-03 | Gravité: P3 | Module: dépôt | Axe: hygiène
Constat : 3 roues `.whl` (≈ 9,8 Mo) suivies à la racine ; `pillow_jxl_plugin` n'est importé nulle part (le JXL passe par ffmpeg/libjxl).
Preuve  : `git ls-files | grep whl` ; `grep -rn pillow_jxl azimut tests` → aucun résultat.
Correctif: retirées, `*.whl` ajouté à `.gitignore` — commit d8a6961 | Statut: Corrigé
```
```
ID: H-01/H-02 | Gravité: P3 | Module: dépôt | Axe: hygiène / vie privée
Constat : `.cloud-attachments/`, `.freebuff/`, `rapport.json` présents dans l'archive. `.gitignore` les excluait déjà :
          un `git init` ne les suit donc pas (archive produite hors Git). `rapport.json` contenait un chemin personnel
          (`/home/<utilisateur>/Desktop/Azimut`) et la version 0.1.0.
Preuve  : `git status --ignored` (3 entrées `!!`) ; contenu de `rapport.json` lu (les deux dossiers n'ont PAS été ouverts).
Correctif: supprimés de la copie de travail (aucun commit possible : non suivis) | Statut: Corrigé dans l'arbre livré
Test    : `grep -rn "/home/" .` → seule occurrence : une chaîne de test légitime (`tests/scope/test_scope.py:35`).
```
```
ID: D-01…D-06 + AZ-C02 | Gravité: P3 (AZ-C02 : P2) | Module: docs / build | Axe: documentation
Constat : BUILD.md « Python ≥ 3.10 » ; RELEASING.md « Python 3.10 et 3.12 » (la CI ne teste que 3.12) ; pyproject « …audio et JSON »
          et `PySide6>=6.6` sans borne haute, différent de requirements.txt (Essentials+Addons) ; README « si présent »,
          sans extras / langues / --selftest, et « sans FFmpeg ni ExifTool réels » ; COMPATIBILITE.md : chiffres périmés.
Preuve  : lecture + `tomllib.load(pyproject.toml)` valide après correction ; build.yml : matrice de tests sur Python 3.12 seul.
Correctif: commits 22a8c53, 43d2071, 2bcb312, 457f251, 9b2e13a | Statut: Corrigé (la borne <6.13 est déclarative :
          résolution `pip` non vérifiée, hors ligne)
```
```
ID: E2E-001 | Gravité: — | Module: convert | Axe: couverture de tests
Constat : aucun test n'exécutait réellement FFmpeg pour chaque profil.
Preuve  : `tests/convert/test_e2e_profiles.py` : 31 PASS (30 profils non matériels + réparation mono), vidéo ET audio vérifiés
          par ffprobe ; contrôle par mutation (audio supprimé) → 11 FAIL. Commit 5635e48.
Statut  : Ajouté (ignoré sans ffmpeg/ffprobe).
```

```
ID: LOG-002 | Gravité: P2 | Module: convert | Axe: logique / cas limite
Constat : `AV1_ENCODERS = ("libsvtav1", "libaom-av1")` préférait SVT-AV1 pour tous les médias ; sur FFmpeg 6.1.1,
          SVT-AV1 refuse les dimensions < 64 px et aucun repli n'existait.
Preuve  : docs/AUDIT/preuves/av1_dimension_minimale.py (ffmpeg 6.1.1) : libsvtav1 ÉCHEC pour 16…63 px, ok à 64 ;
          libaom-av1 ok partout. Rejoué le 7 novembre 2026 avec FFmpeg 8.1.3 / SVT-AV1 3.1.2 : 32×32, 48×48 et
          63×63 passent aux presets 4/5/8 ; l'échec 48×48 n'est donc PAS reproduit ici. En revanche, 16×16 se
          bloque (rc=137 après 10 s ; `-svtav1-params lp=1` ou libaom-av1 le contournent).
Impact  : conversion AVIF/AV1 impossible pour les petits médias sur FFmpeg 6.x ; sur SVT-AV1 3.1.2, blocage
          jusqu'au délai de 10 min du chemin image (aucune détection de stall sur ce chemin).
Correctif: `engine.av1_small_media_encoder` (seuil 64 px) appelé par `_convert_image` et `_convert_stream` ;
          dimensions lues par `imaging.image_size` (Pillow) et `MediaProbe.width/height` (ffprobe) — commit ea3fc33
          | Statut: Corrigé (préventif ; échec 48×48 non reproduit ici)
Test    : test rouge e2e avant correctif : `AssertionError: [('libsvtav1', 'libaom-av1')]` (le worker choisissait
          SVT) ; fonction et champs absents (AttributeError). Après : `tests/convert/test_engine.py::
          test_av1_small_media_encoder_prefers_libaom_under_64`, `tests/convert/test_restoration.py::
          test_parse_probe_keeps_first_video_dimensions`, `test_e2e_small_avif_uses_libaom`,
          `test_e2e_small_av1_video_uses_libaom` et `test_e2e_16px_avif_avoids_svt_hang` (worker réel ; image et
          vidéo 48×48, image 16×16 ; encodeur et dimensions vérifiés) ;
          `tests/convert/test_e2e_profiles.py::test_small_av1_media_is_sent_to_libaom[image_avif|video_webm_av1]`
          (48×48 par ffmpeg réel ; rouges sur 0d07aa4 : `imaging.image_size` et `MediaProbe.width` absents).
          Suite : voir § H (contrôle final daté).
Limite  : le blocage de SVT-AV1 à 16×16 est prouvé par commande bornée (SIGKILL après 10 s, rc=137, sortie 0 o),
          pas par un test qui lancerait SVT pour de bon (il occuperait le worker jusqu'à son délai de 600 s) ;
          ne couvre pas un échec SVT sur un média ≥ 64 px ni le cas où Pillow/ffprobe ne donnent pas les
          dimensions.
Note    : arbre partagé avec un lot UI concurrent ; le run `-q` qui a précédé a connu 1 échec hors convert
          (`tests/launcher/test_atlas_smoke.py::test_open_module_failure_disables_card`), vert au run suivant.
```
```
ID: LOG-013 | Gravité: P2 | Module: convert/workers | Axe: robustesse / encodeur figé
Constat : `_convert_image` appelait `run_process(..., timeout=600)` sans `stall_timeout`, contrairement à
          `_convert_stream` : un encodeur figé (SVT-AV1 sur un 16×16, voir LOG-002) immobilisait le fichier
          600 s, sans arrêt ni message.
Preuve  : worker réel dont `engine.build_command` est remplacé par `sleep 600` : sur l'arbre sans correctif, le
          test tourne encore après 25 s (processus tué, rc=137) ; avec correctif, arrêt en 1,84 s.
Impact  : un encodeur bloqué gèle la file d'images 10 min par fichier, sans diagnostic pour l'utilisateur.
Correctif: `IMAGE_STALL_FLOOR = 180` s ; progression ffmpeg et taille du fichier `.part` passées comme `activity`
          à `run_process` (même mécanisme que la vidéo) ; `res.stalled` → un seul réessai avec le preset plus
          rapide suivant (`IMAGE_PRESET_FALLBACKS`), puis état Erreur si le second essai se bloque aussi. Le
          délai global de 600 s reste la borne supérieure.
Test    : `tests/convert/test_restoration.py::test_e2e_frozen_image_encoder_is_stopped` (fenêtre ramenée à 1 s
          par `monkeypatch`) : vert en 1,84 s, aucun fichier produit, 1 erreur ;
          `test_e2e_stalled_image_retries_faster_preset` (preset 4 figé → 5 aboutit) et
          `test_e2e_stalled_image_stops_after_one_retry` (deux blocages → deux presets essayés, puis erreur).
Limite  : un encodeur légitime très lent (une seule image, aucune progression avant la fin) est arrêté au bout
          de 180 s ; le réessai ne va qu'un cran plus loin, pas jusqu'au preset le plus rapide disponible.
```

```
ID: SEC-002 | Gravité: P2 | Module: ui/widgets, modules/*/window | Axe: sécurité / affichage
Constat : seuls 3 QLabel forçaient `Qt.PlainText` (scope/treeview ×2, module_window:383). Les autres textes dynamiques
          (94 appels setText/QMessageBox à texte non littéral, dont `ProgressBar.file_label.setText(<nom de fichier>)`)
          utilisaient le mode automatique de Qt, qui interprète du HTML si le texte « ressemble » à du HTML.
Preuve  : lecture de code + grep `setTextFormat` (3 sites avant correctif) ; comportement Qt non exécuté alors.
Impact  : un fichier nommé `<b>x</b>.jpg` pouvait s'afficher déformé (usurpation d'affichage) ; pas d'exécution de code.
Correctif: QLabel et QMessageBox de données externes en `Qt.TextFormat.PlainText` ; les info-bulles, sans format dans
          Qt, sont échappées dans `<qt>` ; le site Azimut gardant l'API statique `QMessageBox.warning` (exigée par le
          test de fumée) est couvert aussi. 13 lignes `setTextFormat` ajoutées dans launcher/atlas_window, dedup,
          rename, scope, tags, ui/widgets, ui/module_window et ui/preferences_dialog — commit da0a4fd | Statut: Corrigé
Test    : `tests/ui/test_sec002_plaintext.py` (13 tests Qt) : nom de fichier, pastille d'état, zone de dépôt, aperçus
          image/vidéo, source Scope, info Rename, titre Tags, légendes Dedup, `_ask` Dedup, label d'erreur Azimut,
          message et info-bulle littéraux ; suite réelle verte (`CONSTATS.md` § H).
Limite  : les info-bulles n'ont pas de format dans Qt : l'échappement `<qt>` est la parade, pas un `PlainText` ;
          les textes issus de binaires restent affichés tels quels (c'est le but).
```

## B. Non corrigés

```
ID: I18N-001 | Gravité: P1 | Module: modules/*/window.py, convert/codec_tree.py, scope/treeview.py | Axe: i18n
Constat : les fenêtres des 6 modules affichent des chaînes françaises en dur, hors `tr()`. 115 littéraux directs
          passés à des widgets (AST : dedup/window 27, tags/window 19, rename/window 17, scope/window 14, takeout/window 13,
          convert/codec_tree 11, convert/window 11, atlas 3). Seuls 13 fichiers appellent tr()/translate().
          `tools/update_translations.py` n'extrait que ui/*.py, launcher/*.py et modules/*/__init__.py ; les 27 .ts
          n'ont que 141 messages.
Preuve  : script AST (voir RAPPORT_FINAL) ; comptage des messages des 27 .ts (141, 0 unfinished).
Impact  : « 27 langues » annoncées, mais les modules restent en français (promesse non tenue, consigne 7).
Statut  : Non corrigé — exige PySide6 (lupdate/lrelease, tests Qt) et 27 traductions ; le décompte est un minimum
          (f-strings, messages de workers/engines non comptés).
Proposition: lot dédié par module : envelopper en `self.tr()`/`QCoreApplication.translate`, étendre SOURCES, régénérer
          les .ts, laisser `unfinished` les langues non traduites (repli français) ; test « aucun littéral hors tr() ».
```
```
ID: PKG-001 | Gravité: P1 | Module: packaging/fetch_binaries.py | Axe: chaîne d'approvisionnement
Constat : vérification SHA-256 permissive : liste de sommes indisponible ou entrée absente → simple AVERTISSEMENT, le
          build continue. Aucune vérification pour Perl portable, FFmpeg macOS (Martin Riedl) et ExifTool via l'archive
          GitHub. Sources « latest » (BtbN, relocatable-perl) et version d'ExifTool découverte dynamiquement ; les
          sommes viennent de la même origine que les fichiers (protège de la corruption, pas d'une compromission).
Preuve  : lecture de code (fetch_binaries.py : `_verify_btbn`, `_verify_exiftool`, `install_perl`, `install_ffmpeg_macos`).
Statut  : Non corrigé — il faut le réseau pour calculer des empreintes de référence.
Proposition: fichier de verrouillage versionné (URL exactes + SHA-256), échec par défaut, option explicite pour passer outre.
```
```
ID: LOG-003 | Gravité: P3 | Module: convert | Axe: logique
Constat : profil `video_fix_mono` (vidéo copiée) sans sonde ffprobe exploitable → `-c:v ""` → « Encoder not found »,
          classé « encodeur manquant » à tort.
Preuve  : docs/AUDIT/preuves/e2e_convert_echantillons_realistes.py (ligne « sans sonde » ÉCHEC) ; workers.py:505
          (`info = None` si la sonde échoue).
Correctif: `build_command` sans sonde copie la vidéo (`-c:v copy`, sans `-vf`) quand `profile.video_copy`.
Test    : tests/convert/test_restoration.py::test_fix_mono_without_probe_copies_video (rouge avant correctif).
Statut  : Corrigé
```
```
ID: LOG-004 | Gravité: P3 | Module: core/fsutil | Axe: logique / permissions
Constat : `atomic_write_text` crée ses fichiers en 0600 (mkstemp) ; un fichier normal est 0644. Concerne notamment
          `dedup.export_report` (CSV choisi par l'utilisateur).
Preuve  : exécution : mode 0o600 après atomic_write_text, 0o644 pour un fichier ordinaire (umask 022).
Statut  : Non corrigé (choix prudent pour prefs/journaux ; décision du propriétaire, DECISIONS.md D9).
```
```
ID: PKG-002 | Gravité: P3 | Module: packaging/fetch_binaries.py | Axe: sécurité
Constat : `extract_tar` retombe sur `extractall` sans filtre si `tarfile.data_filter` manque (Python 3.11.0–3.11.3).
          `extract_zip` s'appuie sur l'assainissement des noms par `zipfile`.
Preuve  : lecture de code ; test rouge avec `tarfile.data_filter` retiré (membre `../`, chemin absolu, lien sortant).
Correctif: repli `safe_members()` : chemins absolus, `..`, liens sortants et fichiers spéciaux refusés.
Test    : tests/packaging/test_fetch_binaries.py::test_extract_tar_without_data_filter_rejects_unsafe_members.
Statut  : Corrigé
```
```
ID: H-04 | Gravité: P2 | Module: dépôt | Axe: licences
Constat : aucun fichier LICENSE pour Azimut ; FFmpeg est distribué en variante GPL (BtbN) alors que BUILD.md écrit « LGPL ou GPL ».
Preuve  : `ls | grep -i licen` → rien ; fetch_binaries.py (« variante GPL »).
Statut  : Non corrigé — choix de licence = décision du propriétaire ; ébauche `THIRD_PARTY_NOTICES.md` ajoutée (à valider).
```
```
ID: LINT-001 | Gravité: P3 | Module: dépôt | Axe: qualité
Constat : aucune configuration ruff ; 52 `except …: pass` silencieux (AST ; 0 `except:` nu), surtout core/workers (9),
          ui/widgets (6), launcher/atlas_window (4), dedup/actions (4). ruff/bandit/vulture non exécutables ici.
Preuve  : triage `docs/AUDIT/lot4_triage.md`. `ruff check azimut tests tools packaging` : 197 erreurs avant la
          section (règles par défaut), 71 avec la section validée (S110 55, B905 9, PLW1510 3, B007 2, F841 1,
          E402 1), 0 après corrections.
Correctif: section `[tool.ruff]` (76f0ff3), puis un commit par famille : F401/F821 (0bcf094), PLW1510 (0a0d2e9),
          B905 (ae2f270), B007 (d4bbcd0), F841 (cc01be5), E402 (5de0a69), S110 (98cbcfb : 55 `except` remplacés
          par un journal debug, un `contextlib.suppress` équivalent dans un test). Aucun `ruff format`, aucun `# noqa`.
Test    : `QT_QPA_PLATFORM=offscreen pytest -q` → 610 passed, 2 skipped ; `-W error` → 611 passed, 2 skipped.
Statut  : Corrigé
```
```
ID: CI-001 | Gravité: P3 | Module: .github | Axe: couverture
Constat : la CI ne teste que Python 3.12 ; le projet déclare ≥ 3.11. Recommandation : ajouter 3.11 (non fait : non testable ici).
Correctif: matrice de `build.yml` et `tests-branches.yml` : tâche `ubuntu-22.04` / Python 3.11 ajoutée (3.12 conservé).
Test    : non vérifié — la CI ne s'exécute qu'au push ; pousser une branche pour la valider.
Statut  : Corrigé (non vérifié par exécution)
```

## C. Non vérifiables ici (protocole manuel)

| ID | Sujet | Protocole |
|---|---|---|
| I18N-003 | RTL (`ar`, `he`) : aucun code explicite ; l'inversion dépend de `qtbase_<langue>.qm` (Qt) installé par `i18n.install`. Pour `zh-Hans`/`zh-Hant`/`nb`, le nom `qtbase_zh_Hans`… peut ne pas exister dans Qt (hypothèse). | `ls <PySide6>/Qt/translations \| grep qtbase_`; lancer en `ar`/`he`, `app.layoutDirection()` ; capture Azimut + 6 modules ; refaire sur le paquet gelé. |
| AZ-C01, C03–C18 | Compatibilité Windows/macOS/Wayland/HiDPI (fenêtres trop grandes, NFC/NFD, OneDrive, menu macOS…) | Non examinés (Qt et plateformes requis) ; protocole : `COMPATIBILITE.md`. |
| PKG-SHA | Correspondance SHA ↔ tag des actions GitHub épinglées | `git ls-remote` des tags, hors ligne impossible. |
| UI-* | Affichage, ergonomie clavier, captures, contrastes, gel de l'UI, annulation de bout en bout | Qt + xvfb requis ; aucune capture produite. |
| PLAT | Windows / macOS / installeurs / PyInstaller | CI du dépôt + essais manuels (RELEASING.md). |

## D. Lot 1 — relectures P0 (7 novembre 2026, HEAD `139a89f`)

Périmètre relu dans l'ordre imposé : `modules/takeout/engine.py` ; `modules/dedup/engine.py` et `livephoto.py` ;
`core/workers.py` et `core/binaries.py`. Les listes de cas limites attendus ont été écrites **avant** lecture des
tests (`rapports_locaux/lot1/`, non versionné). Tests exécutés avec pytest 9.2 + PySide6 6.12 (`offscreen`).

### Corrigés

```
ID: LOG-005 | Gravité: P1 | Module: takeout/engine | Axe: plantage / entrée externe
Constat : `_timestamp` faisait `int(float(...))` sans intercepter `OverflowError` : un JSON compagnon avec
          `"timestamp": 1e400` ou `Infinity` faisait échouer `load_sidecar`, donc `analyze` et tout le dossier.
Preuve  : test rouge `test_infinite_timestamp_is_ignored_not_fatal` (OverflowError à engine.py:81) ; après
          correctif, la date est ignorée et le GPS du même JSON reste exploitable.
Impact  : analyse Takeout impossible (worker en échec) à cause d'un seul fichier externe malformé.
Correctif: `except (TypeError, ValueError, OverflowError)` — commit 8607584 | Statut: Corrigé
Test    : test seul 1 passed ; suite 543 passed, 2 skipped ; `-W error` 543 passed, 2 skipped.
```
```
ID: LOG-006 | Gravité: P2 | Module: takeout/engine | Axe: appariement ambigu / altération
Constat : `DirIndex._pick` retournait le premier JSON dont le doublon correspondait. Pour un média converti sans
          extension d'origine (`photo.avif`), `photo.jpg.json` et `photo.png.json` partagent le radical : le choix
          dépendait de l'ordre de `os.listdir` (donc de l'ordre de création des fichiers).
Preuve  : avant correctif, `find(photo.avif)` → `photo.png.json` dans un ordre, `photo.jpg.json` dans l'autre ;
          test rouge `test_converted_media_with_two_sidecars_is_not_guessed`.
Impact  : date/GPS d'un autre média écrits dans le fichier (métadonnées fausses), comportement non reproductible.
Correctif: `_pick` refuse tout doublon ambigu (plus d'un chemin) — commit 220a075 | Statut: Corrigé
Test    : test seul 1 passed ; suite 544 passed, 2 skipped ; `-W error` 544 passed, 2 skipped.
```
```
ID: LOG-007 | Gravité: P3 | Module: takeout/engine | Axe: logique / date
Constat : `_timestamp` rejetait `ts <= 0`, donc toutes les dates avant 1970 (1965 inclus), puis `dt.year >= 1971`,
          donc 1970. ExifTool écrit pourtant 1965 et Google Takeout en contient (photos scannées).
Preuve  : exécution : `datetime.fromtimestamp(-157766400)` = 1965-01-01 UTC ; `os.utime` accepte cette date ;
          `exiftool -DateTimeOriginal="1965:01:01 00:00:00"` → RC 0 et date relue. Test rouge
          `test_pre_1971_dates_are_accepted` : 1965 et 1970-06 → `taken=None` (`2 failed`).
Impact  : date de prise de vue silencieusement non écrite ; le JSON est classé « sans date ni GPS ».
Correctif: seul `ts == 0` (marqueur « date inconnue ») est rejeté ; le seuil `dt.year >= 1971` est supprimé ;
          les timestamps négatifs restent soumis à `fromtimestamp` (repli None si la plateforme ne les représente
          pas) — commit 985def6 | Statut: Corrigé
Test    : `test_pre_1971_dates_are_accepted` (1965 et 1970-06, 0 toujours ignoré) ; `tests/takeout` 37 passed ;
          `-W error` 37 passed ; suite complète 581 passed, 1 skipped ; `-W error` 581 passed, 1 skipped
          (arbre partagé, avec les modifications non commitées de l'autre lot).
Limite  : sur une plateforme dont `fromtimestamp` refuse les timestamps négatifs, 1965 reste ignoré (repli None) ;
          1970 est couvert partout.
```

### Non corrigés (prouvés par exécution)

```
ID: LOG-008 | Gravité: P2 | Module: takeout/engine | Axe: appariement ambigu / altération
Constat : l'appariement par titre tronqué ne consomme pas le JSON : deux médias dont le nom commence par le même
          titre JSON reçoivent tous deux le même compagnon, donc la même date/GPS.
Preuve  : deux médias `PXL_…-A.jpg` / `PXL_…-B.jpg` + un JSON tronqué de 43 caractères → `analyze` marque les deux
          « Prêt » avec le même sidecar.
Impact  : métadonnées fausses sur l'un des deux fichiers.
Statut  : Corrigé (lot 3) — règle : un JSON apparié par titre tronqué n'est pas attribué à un média quand son
          titre complet (écrit dans le JSON) nomme exactement un AUTRE média du dossier. Sans titre, ou si le titre ne
          nomme aucun média présent (fichier renommé), l'appariement d'origine est conservé ; « photo-edited.jpg »
          partage toujours le JSON de « photo.jpg ». Test : tests/takeout/test_truncated_titles.py (2 rouges avant).
```
```
ID: LOG-009 | Gravité: P3 | Module: takeout/engine | Axe: comptage
Constat : `orphan_json` ne parcourt que les dossiers contenant au moins un média : un JSON seul dans un dossier
          (ou un dossier ne contenant que des JSON) n'est jamais compté.
Preuve  : dossier `sub/ghost.jpg.json` sans média → `analyze` retourne 0 orphelin.
Impact  : compteur « JSON sans média » sous-estimé (information seulement, aucune écriture).
Statut  : Corrigé (lot 3) — `analyze` recense aussi les dossiers qui ne contiennent que des JSON.
          Test : tests/takeout/test_truncated_titles.py::test_json_alone_in_a_folder_counts_as_orphan.
```
```
ID: LOG-010 | Gravité: P3 | Module: takeout/engine | Axe: affichage / diagnostic
Constat : un JSON présent mais illisible garde l'état `Sans JSON` (message « JSON illisible : … »).
Preuve  : `a.jpg` + `a.jpg.json` = `{oops` → état `Sans JSON`, message « JSON illisible : a.jpg.json ».
Impact  : le tableau compte un JSON illisible comme absent ; le message distingue le cas.
Statut  : Non corrigé (un état dédié toucherait l'UI et les compteurs).
```
```
ID: LOG-011 | Gravité: P3 | Module: dedup/engine | Axe: diagnostic
Constat : `build_inventory` compte `stats.symlinks` après `iter_files(follow_symlinks=False)`, mais `iter_files`
          écarte déjà les liens : le compteur reste toujours à 0, même avec des liens dans le dossier.
Preuve  : dossier avec `link.bin` → inventaire sans le lien, `stats.symlinks == 0`.
Impact  : le journal annonce « 0 lien(s) ignoré(s) » alors que des liens ont été ignorés (aucune donnée touchée).
Statut  : Corrigé (lot 3) — `iter_files(on_link=…)` signale les liens écartés (extension demandée seulement) ;
          Dedup les compte. Test : tests/core/test_skipped_links.py (2 rouges avant).
```
```
ID: LOG-012 | Gravité: P3 | Module: dedup/livephoto | Axe: appariement
Constat : `pair_up` replie la casse du chemin de dossier (`_fold(folder)`). Sur un FS sensible à la casse,
          `Album/IMG_1.HEIC` et `album/IMG_1.MOV` sont jumelés alors qu'ils sont dans deux dossiers distincts.
Preuve  : `pair_up([("Album","IMG_1.HEIC"),("album","IMG_1.MOV")])` → une paire.
Impact  : fausse Live Photo proposée (la vidéo peut être proposée à l'écartement) ; cas rare.
Statut  : Corrigé (lot 3) — la casse du dossier n'est repliée que là où le système l'ignore (`os.path.normcase`,
          Windows). Test : tests/dedup/test_live_modified.py::test_pair_up_does_not_merge_folders_that_differ_only_by_case_on_posix.
```
```
ID: AZ-C04 | Gravité: P1 | Module: core/binaries | Axe: compatibilité
Constat : `platform_tag()` renvoie `linux-x64` / `windows-x64` quelle que soit l'architecture : sur Linux ARM64,
          le dossier `bin/linux-arm64` n'est jamais consulté (et un binaire x64 embarqué serait choisi).
Preuve  : `linux/aarch64 → linux-x64`, `linux/arm64 → linux-x64`, `win32/ARM64 → windows-x64`.
Impact  : binaires embarqués inutilisables sur ARM64 ; repli PATH seulement si `platform_tag` est corrigé.
Statut  : Non corrigé — déjà répertorié dans `COMPATIBILITE.md` (AZ-C04) ; correctif transverse
          (`core/binaries.py` + `packaging/fetch_binaries.py` + `packaging/build.py`), hors lot 1.
```

### Relecture sans défaut P0 prouvé

- **takeout** : conservation date/GPS sans `overwrite`, UTC/local + `OffsetTime`, vidéos QuickTime, WebM (mtime
  seule), JSON illisible, orphelins : couverts par les tests existants ou vérifiés par script ; seuls LOG-008 à
  LOG-010 subsistent.
- **dedup** : même taille/contenu différent, > 128 Kio avec différence non échantillonnée (empreinte complète),
  trois exemplaires, vides, liens, annulation immédiate, images uniformes : vérifiés par script ; aucun faux groupe
  exact prouvé. Non couvert par les tests (lecture seule) : `MAX_BUCKET`, formats Pillow non lisibles, annulation
  pendant les empreintes.
- **core/workers** : annulation avant/pendant, timeout, stall, stdout/stderr borné, ordre des résultats, garde
  mémoire : couverts par `tests/core/test_workers.py`. Lecture de code : `cancel()` ne tue que le processus direct
  (aucun sous-processus dans les usages ffmpeg/ExifTool) ; stdout conservé intégralement en mémoire.
- **core/binaries** : ordre embarqué > personnalisé > PATH, PATH assaini, chmod, version/obsolescence,
  cache/refresh : couverts ; AZ-C04 reste le seul défaut prouvé.

## E. Lot 1a — relecture de Takeout (7 novembre 2026, HEAD `20098ed`)

Périmètre : `modules/takeout/engine.py` et `workers.py`, après les correctifs LOG-005/LOG-006. Liste de cas
limites écrite **avant** lecture des tests : `docs/AUDIT/lot1a_cas_limites.md` (25 lignes, annotée).

### Corrigés

```
ID: TAKEOUT-001 | Gravité: P2 | Module: takeout/engine | Axe: altération / option ignorée
Constat : sans `overwrite`, `build_plan` ne testait que `DateTimeOriginal` (images) ou trois tags de création
          (vidéos). Un fichier sans `DateTimeOriginal` mais avec `CreateDate`/`ModifyDate` recevait les trois
          dates du JSON : les valeurs existantes étaient écrasées alors que l'option « Écraser » était décochée.
Preuve  : test rouge `test_plan_keeps_partial_existing_dates_without_overwrite` (CreateDate/ModifyDate dans
          `plan.changes`) ; e2e réel `test_apply_keeps_existing_create_date_without_overwrite` : CreateDate 2010
          devenait 2020 avant correctif, reste 2010 après.
Impact  : perte silencieuse de dates existantes (récupérables par l'annulation, mais contrairement à l'option).
Correctif: `build_plan` n'ajoute que les tags de date/OffsetTime absents quand `overwrite` est faux — commit a56a62e.
Test    : 2 tests ; contrôle final `tests/takeout -W error` : 31 passed (après TAKEOUT-003).
```
```
ID: TAKEOUT-002 | Gravité: P2 | Module: takeout/workers | Axe: altération / permissions
Constat : `ApplyWorker._one` ne consultait pas les permissions. ExifTool (`-overwrite_original`) réécrit un fichier
          0444 en conservant son mode, puis `os.utime` aligne sa date : un original protégé était modifié.
Preuve  : script : `os.access(W_OK)=False`, apply → « Mis à jour », `DateTimeOriginal` écrite, mtime 1600000000 ;
          test rouge `test_apply_refuses_read_only_file` (état « Mis à jour » au lieu de « Erreur »).
Impact  : altération du contenu et de la mtime d'un original marqué en lecture seule, sans avertissement.
Correctif: refus par fichier si `st_mode & 0o222 == 0` — commit 3ef8271.
Test    : `test_apply_refuses_read_only_file` ; tests/takeout 30 passed au commit ; `-W error` final 31 passed.
```
```
ID: TAKEOUT-003 | Gravité: P1 | Module: takeout/engine | Axe: plantage / entrée externe
Constat : `load_sidecar` n'interceptait pas `RecursionError`. Sous Python 3.11/3.12 (`requires-python >= 3.11`),
          un JSON compagnon très imbriqué fait échouer `analyze`, donc l'analyse de tout le dossier.
Preuve  : Python 3.12 : `json.load` d'un JSON de 20 000 niveaux → `RecursionError` (non-ValueError) ; test rouge
          `test_sidecar_json_recursion_error_is_not_fatal` (RecursionError) ; Python 3.14 ne le reproduit pas.
Impact  : analyse Takeout impossible à cause d'un seul fichier externe pathologique.
Correctif: `except (OSError, ValueError, RecursionError)` — commit 20098ed.
Test    : test ci-dessus ; `tests/takeout` 31 passed et `-W error` 31 passed.
```
```
ID: TAKEOUT-004 | Gravité: P3 | Module: takeout/workers + tags | Axe: annulation / conservation
Constat : l'annulation restaurait la mtime mais forçait l'atime à la même valeur (`os.utime(path, (mt, mt))`) ;
          l'atime d'origine était perdue.
Preuve  : test rouge `test_undo_restores_atime` : atime après undo 1 500 000 000 (mtime) au lieu de 1 400 000 000 ;
          après correctif, atime 1 400 000 000 et mtime 1 500 000 000.
Impact  : métadonnée d'accès d'origine perdue (aucune perte de contenu) ; l'annulation reste logique.
Correctif: `snapshot_record` accepte un `atime` optionnel (clé omise pour les anciens enregistrements) ;
          `ApplyWorker._one` lit `(mtime, atime)` avant la lecture ExifTool (qui peut modifier l'atime) ;
          `UndoWorker` restaure `(atime, mtime)` si présent, sinon `(mtime, mtime)` — commit 4c4283a | Statut: Corrigé
Test    : `test_undo_restores_atime` ; `tests/takeout` 32 passed ; suite 560 passed, 1 skipped ; `-W error` 561
          passed, 1 skipped (au commit 4c4283a).
```

### Ajouté (option de sauvegarde)

```
ID: TAKEOUT-005 | Gravité: — | Module: takeout + tags | Axe: conservation / option
Constat : `-overwrite_original` ne laissait aucune copie binaire ; l'annulation logique ne restitue pas les octets.
Preuve  : expérience ExifTool : sans `-overwrite_original`, copie `p.jpg_original` identique à l'original ; une
          seconde application laisse cette copie intacte. Tests rouges avant ajout : `Options(backup=True)` et
          `write_command(backup=True)` → TypeError ; `w.chk_backup` → AttributeError.
Ajout   : `Options.backup` (décochée par défaut), case « Conserver une copie binaire de l'original », commande
          ExifTool sans `-overwrite_original`. L'annulation ne supprime pas la copie et une application ultérieure
          ne l'écrase pas. | Commit: d8711e3 | Statut: Ajouté
Test    : `test_apply_with_optional_binary_backup` (copie identique octet pour octet, conservée après undo),
          `test_backup_write_command_omits_overwrite_original`, `test_window_backup_option_default_off` ;
          `tests/takeout` 35 passed ; suite 564 passed, 1 skipped ; `-W error` 564 passed, 1 skipped.
Limite  : option décochée par défaut (coût disque doublé) ; la copie n'est pas gérée par l'annulation (elle reste
          sur disque, à supprimer manuellement) ; le second apply avec copie est vérifié par commande, pas par test.
```

Limite restante : sans `backup`, l'annulation reste logique (tags + mtime + atime) et le fichier n'est pas
octet-identique ; l'option `backup` couvre ce besoin, mais elle est décochée par défaut et sa copie n'est pas
supprimée par l'annulation.

Note d'exécution : l'arbre est partagé avec un autre lot ; des exécutions intermédiaires ont été interrompues
par ses modifications hors périmètre (`tests/core/test_workers.py` SyntaxError, `tests/convert/test_e2e_profiles.py`
NameError), et les échecs `test_binaries`/`test_workers` observés alors venaient de ses nouveaux tests.
Contrôle final, arbre stabilisé (HEAD `d8711e3`) : `tests/takeout` 35 passed ; `-W error` 35 passed ; suite complète
564 passed, 1 skipped ; `-W error` 564 passed, 1 skipped. Ce total a été mesuré dans l'arbre partagé, où des tests
non suivis de l'autre lot étaient présents (`tests/ui/test_sec002_plaintext.py` en compte 13 actuellement) ; le
périmètre Takeout reste 35 passed. Aucun échec ne subsiste dans ces exécutions.

## E. Lot 1b — relecture de Dedup (7 novembre 2026, HEAD `11dab8c`)

Cas limites écrits avant lecture des tests : `docs/AUDIT/lot1b_cas_limites.md`. `tests/dedup/` ne contient que
`test_live_modified.py` et `test_actions.py` : l'inventaire et le hachage exact n'avaient aucun test direct.

### Corrigé

```
ID: DEDUP-001 | Gravité: P2 (P0 si le FS alias deux noms sur un inode) | Module: dedup/engine | Axe: logique / fausse copie
Constat : `build_inventory` ne dédoublonnait que par `realpath`. Deux noms du même inode (liens physiques, alias
          NFC/NFD ou casse d'un FS insensible) entraient donc comme deux fichiers. Une sauvegarde `rsync --link-dest`
          est annoncée « doublon » avec un « gain libérable » que la suppression ne libère pas ; si les deux chemins
          désignent la même entrée de dossier (macOS), la suppression détruit l'unique fichier.
Preuve  : deux liens physiques (même inode, `nlink=2`) → groupe `identique` avec « Supprimer » et
          `reclaimable == 8000` ; application : 1 déplacé, `freed == 0`, inode toujours à `nlink=2`. Tests rouges
          `test_hard_link_in_the_same_folder_is_not_a_duplicate` et `test_hard_link_between_a_and_b_is_not_a_duplicate`
          (`2 failed`) → verts après correctif.
Impact  : sur un FS qui confond deux orthographes du même nom, l'unique fichier peut être supprimé ; ailleurs,
          le gain annoncé est faux et un nom de sauvegarde par liens physiques est retiré sans rien libérer.
Correctif: `build_inventory` mémorise aussi `(st_dev, st_ino)` (écarté si `st_ino == 0`), compté dans `stats.repeated` ;
          journal : « fichier(s) déjà vu(s) » | Statut: Corrigé | Commit: « fix(dedup): un lien physique n'est plus
          compté comme un doublon [DEDUP-001] »
Test    : tests/dedup/test_engine.py (2 tests). Suite : voir § H ; `tests/dedup` en
          `-W error` : `48 passed in 1.56s`.
Limite  : l'alias macOS NFC/NFD et les montages bind ne sont pas reproductibles sur ext4 : la perte de données y est
          une déduction, pas une exécution. Un lien physique reste supprimable manuellement (hors Dedup).
```
```
ID: DEDUP-002 | Gravité: P1 (P0 en suppression définitive) | Module: dedup/actions | Axe: logique / perte de données
Constat : `check_item` validait le gardien et l'exemplaire à supprimer par leur taille et leur date (tolérance 2 s).
          Une réécriture sur place qui restaure la même taille et la même date (copie `cp -p`, restauration
          d'archive) passait inaperçue : la copie encore intacte était supprimée définitivement alors que le
          gardien ne contenait plus l'original — et inversement, une modification de l'exemplaire était détruite.
Preuve  : tests rouges `test_keeper_rewritten_in_place_with_same_size_and_mtime_blocks_delete` (gardien de 500
          octets réécrit avec 500 autres octets, mtime restauré à la nanoseconde → `copie_one.bin` supprimé) et
          `test_item_rewritten_in_place_with_same_size_and_mtime_blocks_delete` (même réécriture sur l'exemplaire
          à supprimer → supprimé) et `test_keeper_reverified_before_each_deletion_in_same_group` (gardien réécrit
          entre deux retraits d'un même groupe → seconde copie supprimée) → verts après correctif.
Impact  : en mode « suppression définitive », perte du dernier exemplaire du contenu d'origine ou d'une
          modification récente de l'exemplaire.
Correctif: `check_item(..., verify_content=True)` compare le BLAKE2b complet de l'exemplaire à supprimer, puis
          celui du gardien, à leurs empreintes d'analyse (le gardien avant chaque suppression) ; activé seulement
          pour `MODE_DELETE` (quarantaine et corbeille restent réversibles, sans surcoût) | Statut: Corrigé |
          Commit: « fix(dedup): revérifier les deux exemplaires avant suppression définitive [DEDUP-002] »
Test    : tests/dedup/test_actions.py (3 tests) ; `tests/dedup` : `56 passed in 6.87s`.
Coût    : 8 copies de 96 Mio : application médiane 2,75 s (2,69 s de hachage), +1,03 s par rapport à un
          gardien haché une fois ; ~1,6 s/Gio supprimé en plus. D13 : pas d'option de désactivation.
Limite  : le contenu est vérifié juste avant chaque retrait ; une réécriture entre le hachage et l'`unlink`
          reste possible (TOCTOU).
```

### Vérifications et limites du lot

- Prouvé par exécution : cas 1-5, 9, 14 (bombe Pillow), 15 (annulation en cours) de `lot1b_cas_limites.md` ;
  l'empreinte complète sépare bien deux fichiers > 128 Kio différents hors des échantillons.
- Couvert par les tests existants : cas 6-8, 12 ; cas 13 pour la proposition, la confirmation étant lue dans
  `window._on_apply` (`_ask` avant `ApplyWorker`).
- Non vérifié : alias d'inventaire NFC/NFD (ext4), fichiers > 4 Gio, gardien ≠ ancre d'une grappe similaire.
- `-W error` sur toute la suite : `2 failed, 549 passed, 2 skipped in 66.31s`. Les deux échecs sont deux tests AJOUTÉS
  par un autre lot en cours dans l'arbre de travail (`tests/core/test_binaries.py`, `tests/core/test_workers.py`),
  dont le correctif n'est pas terminé ; sur une copie propre de HEAD (`git archive HEAD`), ces deux fichiers donnent
  `35 passed in 3.45s`. Aucun rapport avec Dedup ; ils n'ont pas été modifiés (règle 3).

## F. Lot 1c — relecture de core/workers.py et core/binaries.py (7 novembre 2026, HEAD `0905c3f`)

Cas limites écrits **avant** lecture de `tests/core/` : `docs/AUDIT/lot1c_cas_limites.md` (20 cas, annotés ensuite).
Périmètre : `azimut/core/workers.py`, `azimut/core/binaries.py`, points d'appel `modules/*/workers.py` et `ui/*`.
Sondes locales non versionnées : `rapports_locaux/lot1c/sondes_1c.py`, `sondes_1c_bis.py`, `sonde_core3.py`
(cette dernière avec `faulthandler` pour situer le blocage).

### Corrigés

```
ID: CORE-1 | Gravité: P1 | Module: core/workers | Axe: annulation / processus orphelins / blocage
Constat : `cancel()` ne visait que le processus direct (`proc.terminate()`). Un binaire fourni par l'utilisateur
          peut être un script qui lance le vrai outil : son petit-fils survivait à l'annulation, gardait
          stdout/stderr ouverts, et `run_process` restait bloqué à la fermeture des flux (`stream.close()` attend
          le verrou du tampon tenu par `readline`), donc le worker ne rendait jamais la main.
Preuve  : sonde `sonde_core3.py` (petit-fils détaché par `setsid`, marqueur de démarrage) : `worker.wait(12000)`
          → False ; faulthandler : thread worker dans `run_process` ligne 333 (`close()`), `_pump_stdout` et
          `_drain_stderr` bloqués dans `readline`/`read1` ; petit-fils encore vivant. Tests rouges :
          `test_cancel_kills_grandchild_of_a_wrapper` (petit-fils vivant après annulation) et
          `test_run_process_returns_when_a_detached_grandchild_holds_the_pipe` (`worker.wait(8000)` faux).
Impact  : annulation et fermeture de fenêtre sans effet sur le sous-arbre ; le processus orphelin continue
          d'écrire la sortie pendant que le worker reste bloqué (QThread détruit en cours d'exécution).
Correctif: `start_new_session=True` (POSIX) + SIGTERM/SIGKILL au groupe (`_terminate_tree`, `_kill_tree`) ;
          sous Windows, `taskkill /T` puis `/T /F`, avec repli `terminate()`/`kill()` — commits 90d0b45 et
          5a00b14 | Statut: Corrigé
Test    : 2 tests dans `tests/core/test_workers.py`, plus `test_terminate_tree_uses_taskkill_without_force_on_windows`,
          `test_terminate_tree_falls_back_to_terminate_when_taskkill_fails` et
          `test_kill_tree_uses_taskkill_force_on_windows` (branche Windows simulée, aucun signal réel) ;
          suite `606 passed, 1 skipped` ; `-W error` idem.
Limite  : Windows : `taskkill /T` n'a pas pu être exécuté ici (tests à `subprocess.run` simulé) ; un descendant
          qui casse la chaîne de paternité ou refuse l'accès reste hors d'atteinte — non mesuré.
```
```
ID: CORE-2 | Gravité: P2 | Module: core/binaries | Axe: sécurité / faux binaire
Constat : un chemin personnalisé relatif (`binaries.ffmpeg = "ffmpeg"`) était accepté et résolu depuis le dossier
          courant, alors que `_system_path()` écarte explicitement les entrées relatives du PATH pour cette raison.
Preuve  : sonde : avec un `ffmpeg` exécutable dans le CWD, `resolve()` → `source=custom`, `path=ffmpeg` ; test
          rouge `test_relative_custom_path_is_not_resolved_against_cwd` : `('custom', PosixPath('ffmpeg'))`
          au lieu de `('system', PosixPath('/usr/bin/ffmpeg'))`.
Impact  : selon le dossier de lancement, un exécutable arbitraire du répertoire courant peut être pris pour le
          binaire voulu.
Correctif: refus + avertissement journalisé — commit 0905c3f | Statut: Corrigé
Test    : 1 test ; le reste de `tests/core/test_binaries.py` vert.
```
```
ID: CORE-5 | Gravité: P2 | Module: ui/module_window | Axe: fermeture / plantage
Constat : `closeEvent` annulait puis attendait 3 s ; si le worker ne s'arrêtait pas, la fenêtre était fermée
          quand même (`log.warning`), donc un `QThread` était détruit pendant son exécution.
Preuve  : lecture de code + message Qt observé en fin de sonde du lot 1c :
          `QThread: Destroyed while thread '' is still running` ; test rouge
          `test_close_is_deferred_until_the_worker_really_stops` (worker qui ignore l'annulation) :
          `AssertionError: la fenêtre a accepté sa fermeture alors que le worker tourne encore`.
Impact  : plantage possible à la fermeture si un worker ignore l'annulation plus de 3 s.
Correctif: si `worker.wait(_CLOSE_WAIT_MS)` échoue, `event.ignore()` + `_close_pending` ; la fenêtre se ferme
          d'elle-même à la fin réelle du worker (`_on_worker_finished`) — commit « fix(ui): reporter la
          fermeture d'une fenêtre dont le worker tourne [CORE-5] » | Statut: Corrigé
Test    : `tests/ui/test_module_window.py` (1 test) ; suite `579 passed, 1 skipped` ; `-W error` idem.
Limite  : l'arrêt de l'application (Azimut) ferme les fenêtres des modules sans attendre ce report : si le
          worker dépasse encore, la destruction reste possible — non vérifié ici.
```
```
ID: CORE-6 | Gravité: P3 | Module: core/binaries (appelé par ui/app) | Axe: délai de démarrage
Constat : `probe_version` s'exécutait sur le fil de l'interface au démarrage (`ui/app.py` → `binaries.refresh()`),
          avec 10 s de délai par binaire.
Preuve  : sonde : 3 binaires embarqués figés (`sleep 60`) → `refresh()` = 30,0 s ; test rouge
          `test_create_app_does_not_probe_binaries_on_the_gui_thread` : `create_app()` sondait
          `['ffmpeg', 'ffprobe', 'exiftool']` sur le fil appelant.
Impact  : démarrage gelé jusqu'à 30 s sur un montage lent, un binaire bloqué ou un faux binaire.
Correctif: `refresh(background=True)` publie la localisation (aucun processus) puis sonde les versions dans un fil
          d'arrière-plan, invalidé par époque si un `refresh()` ultérieur survient ; `create_app()` l'utilise
          — commit f8d3969 | Statut: Corrigé
Test    : `tests/core/test_binaries.py::test_background_refresh_publishes_locations_then_versions` (aucune sonde
          sur le fil appelant, versions publiées ensuite) et le test `create_app` de `tests/ui/test_theme.py` ;
          suite `583 passed, 1 skipped` ; `-W error` idem.
Limite  : une fenêtre ouverte juste après le démarrage peut afficher « version inconnue » quelques instants ;
          l'onglet Binaires du tableau de bord garde sa détection explicite (`refresh()` dans un QThread).
```
```
ID: CORE-3 | Gravité: P2 | Module: core/binaries | Axe: faux binaire / diagnostic
Constat : un binaire embarqué présent mais invalide (0 octet, bit d'exécution ajouté) était retenu :
          `source=bundled`, `version=""`, `outdated=False`, alors qu'un `/usr/bin/ffmpeg` valide existe.
Preuve  : sonde `sondes_1c_bis.py` : `source=bundled version='' disponible=True outdated=False`.
Impact  : toutes les conversions échouent avec un message obscur tant que le fichier invalide reste en place.
Règle   : un embarqué qui ne répond pas à sa sonde de version est un fichier invalide : il est écarté au profit du
          candidat suivant, avec un WARNING ; un chemin fourni par l'utilisateur reste retenu tel quel (un script
          ou wrapper sans version lisible est légitime) — commit eaafd70 | Statut: Corrigé
Test    : `test_bundled_binary_without_readable_version_falls_back` (repli système + WARNING) et
          `test_custom_binary_without_readable_version_is_kept` ; suite `588 passed, 1 skipped` ; `-W error` idem.
Limite  : un embarqué valide mais dont la sortie de version est illisible serait aussi écarté (repli système) ;
          le WARNING nomme le fichier écarté, et la règle ne touche pas les chemins personnalisés.
```
```
ID: CORE-4 | Gravité: P3 | Module: core/workers | Axe: mémoire
Constat : stdout était conservé intégralement en mémoire, sans plafond (seul stderr est borné à 64 Kio).
Preuve  : sonde : 50 Mo écrits par le processus → `len(res.stdout) == 50000001`, RSS max 37 → 132 Mio.
Impact  : pic mémoire proportionnel à la sortie ; un outil bavard peut saturer la RAM.
Règle   : on ne conserve pas ce qu'aucun appelant ne lit — `run_process(..., capture_stdout=False)` pour les
          encodages, où seuls le rappel de progression et le code de retour servent ; les sorties analysées
          (JSON d'ExifTool, `-filters`, `-encoders`, ffprobe) restent entières — commit e891846 | Statut: Corrigé
Test    : `test_run_process_can_skip_stdout_capture` : 2 Mo de stdout, `stdout == ""`, rappel complet ;
          suite `588 passed, 1 skipped` ; `-W error` idem.
Limite  : pas de plafond par défaut (casser les sorties analysées serait pire) ; les trois sites d'encodage de
          `convert/workers.py` sont couverts, pas les lectures ExifTool, bornées par lots de fichiers. Les
          écritures ExifTool gardent la capture : `parse_write_result` lit stdout (« 0 image files updated »
          avec code 0 = échec, « weren't updated due to errors »).
```

### Vérifié sans défaut (exécution)

- `_system_path()` : PATH `.:/usr/bin::dossier-relatif:/bin` → `/usr/bin:/bin` (entrées vide et relatives écartées).
- Processus qui ignore SIGTERM : le worker rend la main en 2,0 s (kill après `_KILL_DELAY`).
- Ordre embarqué > personnalisé > PATH, chmod de l'embarqué, cache/refresh, version/obsolescence : couverts par
  `tests/core/test_binaries.py`.
- Annulation avant lancement / pendant la lecture, timeout, stall, garde mémoire, ordre de `map_parallel` :
  couverts par `tests/core/test_workers.py`.
- Chemins avec espaces, accents ou `#` : arguments passés en liste, jamais de shell (`run_process`, `probe_version`)
  — lecture de code.
- Écritures ExifTool : stdout y est indispensable — `parse_write_result` y lit « 0 image files updated » (code 0
  = échec) et « weren't updated due to errors » ; lecture de code + `tests/tags/test_engine.py::test_parse_write_result`.
  Recensement des 11 sites d'appel de `run_process` : 8 lisent stdout (encodeurs, sonde, filtres, durée, lecture
  JSON ExifTool/Rename, deux écritures), les 3 encodages le coupent — plus aucune capture inutile.

### Non vérifiés

- Annulation entre le contrôle `is_cancelled()` et l'enregistrement du processus (course) : non provoquée.
- Annulation alors que le processus rend déjà 0 : `ProcessResult.cancelled` vaut alors faux (condition
  `returncode != 0`) — lecture de code, non reproduit.
- `stall_timeout` avec une sonde `activity` qui lève : exception neutralisée — lecture de code, non testé.
- `timeout=0` : non testé.
- Arrêt de l'application pendant une sonde de version bloquée : protocole manuel.
- Petit-fils orphelin sous Windows (pas de groupe de processus) : non testable ici.

Note d'exécution : l'arbre est partagé avec les lots 1a/1b. Un `__pycache__` périmé a fait échouer la collecte
`-W error` (`NameError: _run` dans `tests/convert/test_e2e_profiles.py`) ; après purge des caches, `-W error` est
vert. Contrôle final, après CORE-3 et CORE-4 : `588 passed, 1 skipped in 81.89s` et `-W error`
`588 passed, 1 skipped in 81.19s`.
Les deux échecs signalés par le lot 1b (`test_binaries`, `test_workers`) sont les tests rouges de ce lot, verts
après les correctifs CORE-1 et CORE-2.

## G. Vérification ciblée — dedup/livephoto.py (7 novembre 2026, HEAD `62b9ced`)

Question : `video_duration` appelle `subprocess.run` directement, hors de `BaseWorker.run_process` ; l'annulation,
les processus orphelins et le délai y sont-ils maîtrisés ?

### Constats (prouvés par exécution)

```
ID: DEDUP-003 | Gravité: P2 | Module: dedup/livephoto (appelé par dedup/engine) | Axe: annulation
Constat : `video_duration` exécute `subprocess.run(timeout=30)` sans jamais consulter l'annulation ; `Hooks._run_one`
          ne vérifie `cancel()` qu'avant chaque vidéo. Une annulation pendant une sonde attend le timeout complet.
Preuve  : sonde `rapports_locaux/dedup_livephoto/sonde.py` : scan de 4 vidéos (4 workers, `Hooks(workers=4)`)
          annulé à 0,3 s → `ScanCancelled` levée après 30,04 s.
Impact  : l'annulation paraît ignorée jusqu'à 30 s ; pour 100 vidéos / 4 workers ≈ 750 s (calcul, non mesuré).
Correctif: `cancel` est consulté toutes les 0,1 s dans la boucle d'attente (`communicate` borné) ; une annulation
          tue ffprobe et son groupe puis draine les tuyaux — commit 6c89c5e | Statut: Corrigé
Test    : `test_video_duration_cancel_stops_the_probe_without_waiting_for_timeout` et
          `test_scan_cancel_during_duration_probe_is_immediate` (retour < 5 s au lieu de 30 s) ;
          `tests/dedup` : 53 passed ; suite `594 passed, 1 skipped` ; `-W error` idem.
```
```
ID: DEDUP-004 | Gravité: P2 | Module: dedup/livephoto | Axe: processus orphelins
Constat : au timeout, `subprocess.run` tue le seul enfant direct (pas de `start_new_session`, pas de kill de groupe) :
          un ffprobe « wrapper » laisse son petit-fils vivant.
Preuve  : sonde : faux ffprobe lançant un petit-fils qui écrit après 3 s → retour en 1,00 s et fichier témoin
          présent 4 s plus tard.
Impact  : un processus orphelin continue après l'analyse (écriture d'un fichier, accès au média).
Correctif: `start_new_session` POSIX + SIGTERM puis SIGKILL sur le groupe (`_kill_tree`), tuyaux drainés de façon
          bornée ; sous Windows, `taskkill /T /F` sur le PID puis repli `proc.kill()` — commits 6c89c5e et
          54b50b7 | Statut: Corrigé
Test    : `test_video_duration_timeout_kills_the_wrapper_and_its_child` (témoin du petit-fils absent après 3,5 s ;
          test sauté sous Windows) ; `test_kill_tree_uses_taskkill_tree_on_windows` et
          `test_kill_tree_falls_back_to_kill_when_taskkill_fails` (branche Windows simulée, taskkill non exécuté).
```
```
ID: DEDUP-005 | Gravité: P3 | Module: dedup/livephoto | Axe: mémoire
Constat : `subprocess.run(capture_output=True, text=True)` conserve stdout et stderr entiers, sans plafond.
Preuve  : sonde : faux ffprobe écrivant 50 Mo sur stdout → RSS max 38 → 181 Mio (+143) ; résultat `None` (sortie
          illisible) mais tout a été lu en mémoire.
Impact  : un ffprobe anormal ou un faux binaire peut saturer la mémoire pendant l'analyse.
Correctif: drainage continu de stdout et stderr par deux lecteurs ; seule la tête de stdout est conservée (64 Kio au
          plus), stderr est jeté : ni tuyau plein, ni capture entière ; tuyaux refermés en fin de sonde
          — commit f932a0d | Statut: Corrigé
Test    : `test_video_duration_bounds_a_talkative_probe_output` (50 Mo écrits : pic 200,1 Mio avant, < 8 Mio après)
          et `test_video_duration_reads_a_normal_probe` (décodage de la sortie binaire) ; suite `596 passed,
          1 skipped` ; `-W error` idem.
```

### Vérifié sans défaut (exécution)

- `timeout=1` sur un ffprobe bloqué : retour en 1,00 s, `None`, aucun zombie (`os.waitpid(-1, WNOHANG)` →
  `ChildProcessError`).
- `ffprobe` absent (`OSError`) et sortie illisible (`N/A`, vide, négative) : `None`, jamais d'exception — tests
  existants `test_video_duration_with_missing_ffprobe`, `test_parse_duration`.
- Signature : `timeout` vaut 30 s par défaut et n'est pas réglé par l'appelant (`dedup/engine.find_live_groups`).

### Non vérifié

- Windows : la branche `taskkill /T /F` est couverte par des tests unitaires à `subprocess.run` simulé, mais
  l'exécution réelle de `taskkill` et le kill d'un petit-fils Windows n'ont pas pu être mesurés ici ; un descendant
  qui casse la chaîne de paternité ou refuse l'accès reste hors d'atteinte.
- Annulation pendant qu'un ffprobe très bavard écrit : non provoquée (le drainage est continu et borné, mais le test
  de volume ne combine pas annulation et flux).
- Distinction des causes de `None` (absent / timeout / sortie illisible) : aucune trace, seul le compteur
  `live_unknown_duration` la résume — lecture de code.

## H. Contrôle final (7 novembre 2026, HEAD `65c74a6`)

Mesures de référence de fin de lot ; les constats renvoient ici au lieu de répéter les chiffres. L'arbre est
partagé avec d'autres lots : ces chiffres valent pour le HEAD présent au démarrage des suites (`0038cf0` est
apparu pendant les runs), pas au-delà.

| Commande | Résultat | Code |
|---|---|---|
| `QT_QPA_PLATFORM=offscreen pytest -q` | 596 passed, 2 skipped in 89.87s | 0 |
| `QT_QPA_PLATFORM=offscreen pytest -q -W error` | 596 passed, 2 skipped in 94.85s | 0 |
| `QT_QPA_PLATFORM=offscreen pytest tests/convert -q` | 143 passed, 2 skipped in 35.15s | 0 |
| `QT_QPA_PLATFORM=offscreen pytest -q -W error tests/convert` | 143 passed, 2 skipped in 33.33s | 0 |

Photo précédente (`e6f2a2f`) : `-W error` était rouge de 6 tests dans `tests/dedup/test_live_modified.py`, alors
modifiés non commités par le lot dedup ; DEDUP-005 commité (`f932a0d`), ces échecs ont disparu et les deux suites
repassent au vert.

## I. Recensement des subprocess directs — sonde de version (7 novembre 2026, HEAD `b880e0c`)

Périmètre : `core/binaries.py::probe_version` (sondes `-version` / `-ver`), repéré lors du recensement des
processus hors `BaseWorker`. Sonde non versionnée : `rapports_locaux/subprocess_census/sonde.py`.

```
ID: SUBPROC-001 | Gravité: P2 | Module: core/binaries | Axe: processus orphelins
Constat : `subprocess.run(..., timeout=10)` ne tuait que l'enfant direct ; un faux binaire « wrapper » laissait
          son petit-fils vivant après le timeout.
Preuve  : sonde : faux ffmpeg lançant un petit-fils à témoin puis dormant 60 s → retour en 10,01 s,
          `petit-fils vivant=True` ; test rouge `test_probe_version_timeout_kills_the_grandchild`.
Impact  : un sous-processus continue après la détection, par exemple un wrapper qui accède au média.
Correctif: `Popen` en session propre (POSIX) ; au timeout, SIGTERM au groupe puis SIGKILL après 1 s ; sous
          Windows, `taskkill /T /F` puis repli `kill()` — commit f6b456f | Statut: Corrigé
Test    : `test_probe_version_timeout_kills_the_grandchild` (témoin absent, retour < 5 s ; test sauté sous
          Windows).
```
```
ID: SUBPROC-002 | Gravité: P3 | Module: core/binaries | Axe: mémoire
Constat : `subprocess.run(capture_output=True)` conservait toute la sortie sans plafond.
Preuve  : sonde : 50 Mo sur stdout → pic 150,1 Mio ; test rouge `test_probe_version_bounds_a_talkative_output`
          (même pic).
Impact  : un binaire anormal peut saturer la mémoire au démarrage ou à la détection.
Correctif: drainage continu de stdout par un lecteur, tête conservée sur 64 Kio, stderr jeté ; tuyau refermé
          après le lecteur — commit f6b456f | Statut: Corrigé
Test    : `test_probe_version_bounds_a_talkative_output` (pic < 8 Mio pour 50 Mo écrits) ;
          `test_probe_version_parsing` et `test_probe_version_failures` adaptés de `run` à `Popen` sans
          changer leurs assertions (délai 10 s, trois formats, délai/OSError/code retour/sortie illisible).
```
```
ID: SUBPROC-003 | Gravité: P3 | Module: ui/widgets | Axe: annulation / mémoire
Constat : `_load_ffmpeg_still`, `_render_pdf_external` et `_load_video_frame` appellent `subprocess.run(
          capture_output=True, timeout=20)` : aucun point d'annulation pendant l'exécution, capture non bornée.
Preuve  : sonde : aperçu vidéo annulé à 0,3 s → retour en 6,20 s (fin de ffmpeg) ; 50 Mo → 100,2 Mio ;
          PDF : retour en 3,35 s, 100,2 Mio.
Impact  : deux aperçus obsolètes peuvent occuper les deux threads du pool ; pic mémoire proportionnel.
Correctif: `Popen` avec stdin/stdout/stderr sur `DEVNULL` et session propre ; boucle de sondage 0,1 s qui vérifie
          `is_current()`, plafond 20 s, puis SIGTERM au groupe et SIGKILL après 1 s (Windows : `taskkill /T /F`)
          — commit b880e0c | Statut: Corrigé
Test    : `test_video_preview_stops_a_stale_ffmpeg` (ffmpeg dormant 60 s, aperçu annulé à 0,3 s → retour < 5 s) ;
          `test_pdf_preview_stops_a_stale_tool` (pdftoppm dormant, idem) ;
          `test_image_preview_does_not_buffer_ffmpeg_output` (50 Mo sur stdout → pic < 8 Mio) ; sautés sous
          Windows (scripts à shebang), Windows non exécuté.
```

Contrôles après correctif : `tests/core/test_binaries.py` → `18 passed` ; `tests/ui/test_widgets.py` → `35 passed` ;
suite `611 passed, 1 skipped` en 110,58 s ; `-W error` idem en 104,32 s ; `.venv/bin/ruff check` passe sur les
deux fichiers modifiés.

