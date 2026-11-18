# Base de référence de l'audit (Azimut 0.2.0)

## Mesure réelle du 7 novembre 2026

Rejouée sur `feature/audit-0.2.0-suite`, HEAD `5b1db1b`, avec
`bash docs/AUDIT/preuves/collecter_rapports_locaux.sh` (sorties brutes dans `rapports_locaux/`, ignoré par Git).
Elle remplace la base « hors ligne » de l'audit précédent, qui n'avait pu exécuter ni pytest avec Qt ni les analyseurs.

### Environnement
| Élément | Valeur |
|---|---|
| Système / Python | Linux Fedora 44 (noyau 7.2.14), Python 3.14.8, venv local |
| PySide6 / Qt | 6.12.0 (Essentials + Addons), `QT_QPA_PLATFORM=offscreen` |
| Pillow | 13.0.0 (écriture AVIF : oui ; HEIF : non) |
| pytest | 9.2.0 |
| FFmpeg / FFprobe | 8.1.3, réels |
| ExifTool | 13.50, réel |
| Analyseurs | ruff 0.17.4, bandit 1.9.4, vulture 2.16, pip-audit 2.10.1 |
| Réseau | présent (installation des analyseurs, exécution de pip-audit) |
| Absents (tous optionnels) | `pillow_heif`, `send2trash`, `rawpy`, `psutil` |
| xvfb / sous-agents / worktrees | xvfb présent et non utilisé ; sous-agents et worktrees non utilisés |

### Résultats
| Commande | Résultat |
|---|---|
| `python -m pytest -q -rs -p no:cacheprovider` | **542 passed, 2 skipped** — 64,62 s, code 0 |
| `python -m pytest -q -W error -p no:cacheprovider` | **542 passed, 2 skipped** — 63,60 s, code 0 |
| `python main.py --selftest` | `"ok": true`, `"problems": []` |
| `ruff check azimut tests tools packaging` | **200 erreurs**, 121 corrigeables par `--fix`, code 1 |
| `bandit -q -r azimut packaging tools` | **26 signalements** (22 Low, 2 Medium, 2 High), code 1 |
| `vulture azimut --min-confidence 80` | **0 résultat**, code 0 |
| `pip-audit -r requirements.txt` | **aucune vulnérabilité connue**, code 0 |

544 tests collectés (`--collect-only`) = 542 réussis + 2 ignorés. Les 2 ignorés sont
`tests/convert/test_restoration.py:225` et `:396` (`pillow_heif` absent) : ce ne sont pas des défauts du dépôt.
Le chiffre « 499 réussis / 4 ignorés » des consignes **n'est pas reproduit ici**, et l'écart n'est pas expliqué à ce stade.
Toute comparaison ultérieure doit partir de **542/2**. Les 338/375 tests de l'audit précédent provenaient de l'exécuteur
de remplacement (sans Qt), pas de pytest : ils ne sont plus comparables et ne servent que d'historique.

### Détail des analyseurs
- **ruff** : `pyproject.toml` ne contient aucun `[tool.ruff]`. Ruff 0.17.4 active 407 règles par défaut et
  `--isolated` donne le même total (200 erreurs) : `I001` 34, `RUF100` 32, `BLE001` 22, `UP035` 18, `RUF059` 17,
  `PLW1510` 13, `UP037` 10, `F401` 10, puis 13 règles à 1–6 occurrences. Avec l'ancien jeu par défaut `E4,E7,E9,F`,
  il n'y a que 26 erreurs : le lot 4 devra fixer explicitement la sélection avant de trier.
- **bandit** : 2 High = `B202` (`tarfile.extractall`), `packaging/fetch_binaries.py:123` et `:131` (cf. PKG-002) ;
  2 Medium = `B310` (`urlopen`), même fichier `:89` et `:112` ; les 22 Low sont `B603`/`B404`/`B110`.
- **vulture** : 0 à confiance ≥ 80 ; 74 à confiance ≥ 60, dont beaucoup de méthodes Qt appelées par le framework
  (`dragEnterEvent`, `headerData`, `paint`…) : bruit attendu, à trier au lot 4.
- **pip-audit** : `requirements.txt` résolu, aucune vulnérabilité connue à cette date.

### Inventaire (rejouable)
- `find azimut -name '*.py' | xargs wc -l | tail -1` → 17 562 lignes (70 fichiers).
- `find tests -name '*.py' | xargs wc -l | tail -1` → 6 787 lignes (26 fichiers).
- 27 catalogues `.ts` et 27 `.qm` correspondants.

Non remesuré au lot 0 (mesures hors ligne de l'audit précédent, non rejouées) : validité YAML des workflows,
liens Markdown, comportement de la CLI sans Qt.
