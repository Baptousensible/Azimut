# Plan de l'audit d'Azimut 0.2.0 (avancement)

Légende : [x] fait et prouvé · [~] partiel · [ ] à faire. Ordre : P0 sécurité/perte de données → P0/P1 plantages et logique →
P1 affichage/ergonomie → P2 polyvalence/compatibilité → P3 docs/hygiène.

## Phase 0 — Mise en place
- [x] Dépôt Git, commit de base `0d07aa4` ; branche `feature/audit-0.2.0`, poursuivie en `feature/audit-0.2.0-suite`.
- [x] Environnement : exécuteur de remplacement au départ (`docs/AUDIT/outils/`), puis pytest + PySide6 + ffmpeg +
  ExifTool réels ; suite complète `596 passed, 2 skipped` en `-q` comme en `-W error` (`CONSTATS.md` § H).
- [x] `docs/AUDIT/00-baseline.md`, `CONSTATS.md`, `DECISIONS.md`, `PLAN.md`.

## Lot 0 — Hygiène (commit isolé)
- [x] H-03 (`.whl`) ; [x] H-01/H-02 (fichiers non suivis supprimés de l'arbre).

## Lot 1 — P0 perte de données / sécurité
- [x] LOG-001 (dedup : exemplaire gardé réécrit) · [x] SEC-001 (argfile ExifTool).
- [x] Relire : `dedup/engine.py` (hachage, seuils, Live Photos), `rename/engine.py` (collisions, annulation — `execute_renames` relu sans défaut trouvé), `takeout/engine.py` (appariement, aucune altération de l'original selon le mode), `core/workers.py` (annulation, processus orphelins), `core/binaries.py`.
  **Fait** par les lots 1, 1a, 1b et 1c (`CONSTATS.md` § D–F) : LOG-005…007, TAKEOUT-001…005, DEDUP-001…005,
  CORE-1…6, chacun avec test rouge puis vert ; `rename/engine.py` relu sans défaut. Cas limites écrits avant lecture
  des tests (`lot1a_cas_limites.md`, `lot1b_cas_limites.md`, `lot1c_cas_limites.md`).

## Lot 2 — P1
- [ ] PKG-001 (empreintes épinglées, échec par défaut). *Fichiers* : `packaging/fetch_binaries.py`, nouveau fichier de verrouillage. *Test* : unitaires avec réseau simulé. *Nécessite le réseau.*
- [ ] I18N-001 (chaînes des modules). *Fichiers* : `modules/*/window.py`, `convert/codec_tree.py`, `scope/treeview.py`, `tools/update_translations.py`, 27 `.ts/.qm`. *Test* : « aucun littéral visible hors `tr()` » (AST) + régénération reproductible (`git diff` nul). *Risque* : élevé (régressions d'affichage) → un module par commit, captures avant/après. *Parallélisable par module.*

## Lot 3 — P2
- [x] LOG-002 (repli libaom pour AV1 < 64 px, `ea3fc33`) · [x] LOG-013 (encodage d'image figé arrêté à 180 s, un
  seul réessai avec le preset suivant, `1e8a427`) · [ ] SEC-002 (PlainText sur les labels de données externes) · [ ] I18N-003 (RTL, `qtbase_*` zh/nb) · [ ] AZ-C03/C14 (`fit_window_size`) · [ ] AZ-C04/C07/C10/C11/C12/C13 · [ ] H-04 (licence).
- [ ] Configuration ruff raisonnable + tri des constats (LINT-001) ; bandit ; vulture ; `pip-audit`.

## Lot 4 — P3
- [x] D-01…D-06 · [ ] LOG-003 · [ ] LOG-004 (décision) · [ ] PKG-002 · [ ] CI-001 (Python 3.11).

## Phase 4 — Vérification globale (§ 8)
Voir `RAPPORT_FINAL.md` : la matrice est remplie avec le statut réel de chaque ligne. `pytest -q` et `-W error` sont
faits (`596 passed, 2 skipped`, § H de `CONSTATS.md`) ; ordre aléatoire, plateformes, xvfb et installeurs restent à
exécuter.
