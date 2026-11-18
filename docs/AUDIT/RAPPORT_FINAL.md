# Rapport d'audit — Azimut 0.2.0 (état intermédiaire, honnête)

Branche `feature/audit-0.2.0-suite` (aucun push, aucun tag, `__version__` inchangé). Commit de base : `0d07aa4` ;
état de ce rapport : HEAD `8399147`.
**Cet audit est partiel** : l'environnement de départ n'avait ni réseau, ni PySide6, ni pytest, ni ruff, ni ExifTool, ni
sous-agents (`00-baseline.md`). Depuis, pytest, PySide6, ffmpeg et ExifTool réels sont disponibles (voir § H de
`CONSTATS.md`) ; `ruff` reste absent de cet environnement. Tout ce qui suit est classé « prouvé par exécution »,
« lecture de code » ou « non vérifié ».

## Chiffres (exécuteur de remplacement, même outil avant et après — voir `outils/README.md`)
| | Avant (`0d07aa4`) | Après (HEAD) |
|---|---|---|
| Tests exécutés / PASS | 338 / 264 | 375 / 301 |
| FAIL (artefacts d'environnement) | 2 | 2 |
| Non exécutables sans Qt (NOFIX + QT) | 67 | 67 |
Base officielle (499 réussis / 4 ignorés) : **non reproductible au départ** ; depuis, pytest + Qt réels donnent
596 réussis / 2 ignorés (voir le contrôle réel ci-dessous et `CONSTATS.md` § H).

Contrôle réel (pytest + Qt + ffmpeg, HEAD `65c74a6`, 7 novembre 2026) : **`596 passed, 2 skipped`** en `pytest -q`
comme en `-W error` ; `tests/convert` : `143 passed, 2 skipped` dans les deux modes. Lignes complètes et codes de
sortie : `CONSTATS.md` § H.

## Corrigés (preuve exécutée)
- **P0 LOG-001** (dedup) : une copie pouvait être supprimée alors que l'exemplaire conservé avait été réécrit (même taille).
- **P2 SEC-001** (tags) : l'argfile ExifTool acceptait un chemin relatif contenant « = ».
- **P3 I18N-002**, **H-01…H-03**, **D-01…D-06**, **AZ-C02** (borne `<6.13`, déclarative).
- **E2E-001** : test réel de chaque profil Convert avec ffmpeg (31 PASS, mutation → 11 FAIL).
- **P2 LOG-002** (convert) : un média AV1 < 64 px partait à SVT-AV1, qui se bloque sur un 16×16 ; il est désormais
  confié à `libaom-av1` (`ea3fc33`, tests 48×48 et 16×16 réels).
- **P2 LOG-013** (convert) : le chemin image pouvait attendre 600 s un encodeur figé ; arrêt après 180 s sans signe
  de vie, puis un seul réessai avec le preset plus rapide suivant, sinon erreur (`1e8a427`).
- **Lots 1a–1c** (`CONSTATS.md` § D–G) : LOG-005…LOG-007, TAKEOUT-001…005, DEDUP-001…005, CORE-1…CORE-6 —
  plantages sur entrée externe, dates écrasées ou perdues, gardien revérifié avant suppression définitive, annulation
  et processus orphelins, captures bornées, faux binaires, fermeture de fenêtre, sonde de durée. Chacun avec test
  rouge puis vert et sa preuve.

## Non corrigés notables
**P1** I18N-001 (chaînes des modules hors `tr()`, 115 littéraux relevés) · PKG-001 (vérification des binaires tiers
permissive) · AZ-C04 (`platform_tag` ignore l'architecture, ARM64 non servi).
**P2** SEC-002 (labels en mode texte automatique, par lecture) · H-04 (pas de licence) · LOG-008 (JSON tronqué
partagé par deux médias).
**P3** LOG-003, LOG-004, LOG-009…LOG-012, PKG-002, LINT-001, CI-001. Détail et preuves : `CONSTATS.md`.

## Ce qui est **vérifié sans Qt** (par exécution ou analyse)
- Aucun `shell=True`, `eval`, `exec`, `pickle`, `yaml.load` ; aucun code réseau dans `azimut/` (grep + AST).
- `-protocol_whitelist file` et le préfixe `file:` présents sur tous les appels FFmpeg/FFprobe repérés (grep).
- **Scope est en lecture seule** : AST sans aucune API d'écriture/suppression ; seule exception, l'export du rapport texte vers un fichier choisi par l'utilisateur.
- Rename : `execute_renames` (permutations, casse, annulation) relu, aucun défaut trouvé ; ne jamais écraser (`move_no_replace`).
- Roue construite hors ligne (`pip wheel --no-deps --no-build-isolation`) : 235 entrées, 104 SVG, 27 `.ts` + 27 `.qm`, `LICENSE-Papicons.txt` inclus ; ni `tests/` ni `docs/`.
- 27 `.ts` valides, 141 messages, 0 inachevé ; workflows YAML valides ; liens locaux Markdown : 0 cassé.

## Matrice de vérification (§ 8)
| Contrôle | Statut |
|---|---|
| `pytest -q` complet (Qt + ffmpeg + ExifTool réels) | Fait : `596 passed, 2 skipped` (HEAD `65c74a6`, § H de `CONSTATS.md`) |
| Suite via exécuteur de remplacement | Fait : 301 PASS, 2 FAIL d'environnement, 67 non exécutables |
| `-W error` | Fait : `596 passed, 2 skipped` avec pytest réel (HEAD `65c74a6`) ; 3 passages identiques avec l'exécuteur |
| Ordre aléatoire (3 passages) | **Non exécuté** |
| CLI (`--version`, `--help`, `--list`, erreurs → codes 0/2) | Fait (branches sans Qt) ; `--selftest` et lancement de module non exécutés |
| Démarrage Azimut + 6 modules sous xvfb, captures | **Non exécuté** |
| Installation propre | Roue construite sans dépendances ; `pip install` complet non exécuté (pas de réseau) |
| PyInstaller / AppImage / installeur / DMG | **Non exécuté** |
| Liens et références croisées | Fait (local) ; URL externe non vérifiable |
| Traductions : XML valide | Fait ; régénération reproductible (`git diff` nul) **non exécutée** (lrelease absent) |
| `git status` propre ; `grep /home/` | Fait (seule occurrence : chaîne de test légitime) |
| Registre des constats à jour | Fait (`CONSTATS.md`) |
| Revue indépendante (`requesting-code-review`) | **Non faite** |

## Ce qui n'a pas été lu (l'absence de constat n'y prouve rien)
Relus depuis par les lots 1 et 1a–1c (`CONSTATS.md` § D–F) : `takeout/engine.py` et `workers.py`, `dedup/engine.py` et
`livephoto.py`, `core/workers.py`, `core/binaries.py`.
Restent non lus : `history`, `storage`, `prefs`, `logs`, l'essentiel de `ui/` et de la plupart des `window.py`,
`launcher/atlas_window.py`, `dashboard.py`, `selftest.py`, `tools/`, `packaging/build.py` et installeurs, et le contenu
de la plupart des tests. Lus en profondeur : `core/fsutil.py`, `ffmpeg_args.py`, `i18n.py`, `launcher/cli.py`,
`dedup/actions.py`, `rename/engine.py` (planification et exécution), `tags/engine.py` (argfile, validation),
`packaging/fetch_binaries.py`, `convert/engine.py` (commandes) et `profiles.py`.

## Pour tout rejouer (environnement complet, réseau)
```bash
git checkout feature/audit-0.2.0-suite
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt pytest-randomly
export QT_QPA_PLATFORM=offscreen
pytest -q ; pytest -q -W error ; for i in 1 2 3; do pytest -q -p randomly; done
ruff check . ; bandit -r azimut ; vulture azimut ; pip-audit -r requirements.txt
python main.py --selftest rapport.json      # le fichier produit n'est pas à commiter
python tools/update_translations.py && git diff --stat azimut/resources/translations   # doit être vide
python docs/AUDIT/preuves/av1_dimension_minimale.py && python docs/AUDIT/preuves/e2e_convert_tous_profils.py
```
Puis traiter, dans l'ordre : I18N-001 par module, PKG-001, SEC-002, I18N-003, AZ-C04. Les relectures du Lot 1 et des
lots 1a–1c sont faites (`CONSTATS.md` § D–G) ; `PLAN.md` reste la référence pour la suite.
