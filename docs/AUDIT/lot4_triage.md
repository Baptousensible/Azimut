# Lot 4a — triage des analyseurs (ruff, bandit, vulture, pip-audit)

Branche `feature/audit-0.2.0-suite`, HEAD `e6f2a2f`, 7 novembre 2026.
Outils du venv du dépôt : ruff 0.17.4, bandit 1.9.4, vulture 2.16, pip-audit 2.10.1.
**Aucun fichier de code n'a été modifié** : ce document est le seul ajout (`[LINT-001]`).

Réserve de mesure : l'arbre de travail n'était pas propre — `azimut/modules/dedup/actions.py`,
`azimut/modules/dedup/livephoto.py`, `tests/dedup/test_actions.py`, `tests/dedup/test_live_modified.py` et
`docs/DECISIONS.md` étaient modifiés (non commités) par un autre lot. Les chiffres ci-dessous sont donc mesurés sur
cet arbre ; sur un `git archive` de `e6f2a2f`, quelques unités peuvent différer. Les rapports d'origine, eux,
datent du 7 novembre 01:16 et comptaient 200 erreurs ruff.

Nature des preuves utilisées ci-dessous, conformément aux consignes :

- **prouvé par exécution** : chiffres et cas reproduits par une commande listée au § 1 ;
- **lecture de code** : jugement sur un site précis (chemin:ligne cité) ;
- **non vérifié** : ce qui demanderait le réseau, Windows/macOS ou un interpréteur Python ancien.

## 1. Méthode

Les rapports versionnés localement (`rapports_locaux/`, ignoré par Git) ont été lus :
`ruff.txt`, `bandit.txt`, `vulture.txt`, `pip_audit.txt`. Ils ont été collectés le 7 novembre à 01:16, à un HEAD
antérieur. Ils ont donc été **rejoués au HEAD courant** ; les écarts sont signalés.

```bash
.venv/bin/ruff check azimut tests tools packaging --output-format=concise      # 207 erreurs (200 dans le rapport)
.venv/bin/bandit -q -r azimut packaging tools                                  # 26 signalements (identique)
.venv/bin/vulture azimut --min-confidence 80                                   # 0 ligne
.venv/bin/vulture azimut --min-confidence 60                                   # 73 lignes
.venv/bin/pip-audit -r requirements.txt                                        # aucune vulnérabilité, code 0
.venv/bin/pip-audit                                                            # 8 avis sur pip 26.0.1, code 1
```

Écart ruff : 200 → 207 (I001 34 → 40, RUF059 17 → 18, UP017 5 → 6) et une règle nouvelle, `F821`, apparue avec les
commits du 7 novembre. Bandit est inchangé.

`pyproject.toml` ne contient **aucune** section `[tool.ruff]` (lecture de code) : les 207 erreurs sont le jeu de
règles par défaut de ruff 0.17.4, très large. Avec l'ancien jeu `E4,E7,E9,F`, le même arbre ne donne que
**26 erreurs** — c'est ce qui rend le tri nécessaire avant toute correction.

## 2. ruff — 207 erreurs, 25 règles

| règle | nombre | verdict | exemple |
|---|---|---|---|
| `I001` | 40 | réglage (ordre des imports, auto-fixable) | `azimut/modules/convert/engine.py:2` |
| `RUF100` | 32 | réglage (`# noqa: N802` inutiles car N802 n'est pas activée) | `azimut/modules/scope/treeview.py:243` |
| `BLE001` | 22 | réglage (except large : délibéré à la plupart des sites, à statuer un par un) | `azimut/core/workers.py:139` |
| `UP035` | 18 | réglage (import depuis `collections.abc`) | `azimut/core/fsutil.py:7` |
| `RUF059` | 18 | réglage (variables dépaquetées inutilisées, toutes dans `tests/`) | `tests/dedup/test_actions.py:91` |
| `PLW1510` | 12 | réglage (`subprocess.run` : le code de sortie est toujours testé ensuite) | `azimut/core/binaries.py:157` |
| `UP037` | 10 | réglage (guillemets dans les annotations) | `azimut/core/prefs.py:57` |
| `F401` | 10 | **défaut réel** (imports morts), mineur | `azimut/launcher/atlas_window.py:7` (`QFont`, `QIcon`, `QPainter`) |
| `UP017` | 6 | réglage (`datetime.UTC`) | `azimut/modules/takeout/engine.py:87` |
| `PIE808` | 6 | réglage (`range(0, n)`) | `azimut/modules/convert/advanced.py:17` |
| `B008` | 6 | bruit (motif imposé par Qt : `parent=QModelIndex()`) | `azimut/modules/scope/treeview.py:228` |
| `UP031` | 4 | réglage (`%` → f-string) | `packaging/build.py:70` |
| `FURB167` | 4 | réglage (`re.I` → `re.IGNORECASE`) | `azimut/modules/tags/engine.py:36` |
| `SIM102` | 3 | réglage (deux `if` imbriqués) | `azimut/launcher/atlas_window.py:878` |
| `S110` | 3 | **défaut réel** (except silencieux) — mais très sous-détecté, voir ci-dessous | `azimut/modules/rename/workers.py:124` |
| `RUF012` | 3 | bruit (dict/tuple de classe jamais muté ; 2 sites dans `tests/`) | `azimut/ui/widgets.py:648` (`_LEVEL_TOKENS`) |
| `PLR0124` | 2 | bruit (test de NaN délibéré : `z != z`) | `azimut/core/prefs.py:44`, `azimut/modules/takeout/engine.py:108` |
| `SIM115` | 1 | bruit (le descripteur est fermé dans le `finally`) | `azimut/modules/dedup/actions.py:300`, fermé ligne 354 |
| `SIM113` | 1 | réglage (`enumerate`) | `azimut/modules/dedup/engine.py:118` |
| `RUF046` | 1 | bruit (`int(round(x))` : cast redondant, sans effet) | `azimut/core/fsutil.py:31` |
| `PIE807` | 1 | réglage (`lambda: []` dans un test) | `tests/core/test_i18n.py:161` |
| `PERF102` | 1 | réglage (`values()` au lieu de `d[k]` en boucle) | `azimut/modules/convert/codec_tree.py:657` |
| `FURB136` | 1 | réglage (`max(0, x)` au lieu du ternaire) | `azimut/modules/convert/engine.py:482` |
| `F841` | 1 | **défaut réel** mineur (variable affectée et jamais lue) | `tests/ui/test_indicators.py:106` (`c` du walrus) |
| `F821` | 1 | **défaut réel** (nom indéfini → `NameError` si la branche s'exécute) | `tests/core/test_workers.py:288` (`signal` non importé) |

Total : 40+32+22+18+18+12+10+10+6+6+6+4+4+3+3+3+3+2+1+1+1+1+1+1+1 = **207**.

### Le point important : `S110` sous-estime massivement les `except: pass`

Les trois outils ne signalent que **3** `try/except/pass`, alors qu'un décompte AST du dépôt (script jetable)
en trouve **56** dans `azimut/` (61 avec `packaging/`, `tests/` et la sonde non versionnée de `rapports_locaux/`).
`CONSTATS.md` annonçait 52 : l'écart vient d'un décompte antérieur, sur un arbre plus ancien ; le chiffre mesuré
ici au HEAD `e6f2a2f` est 56, celui à retenir.
La cause est un réglage, pas un bug des outils : par défaut, `S110`/`B110` n'examinent que `except:` nu et
`except Exception:` ; `except OSError: pass` est ignoré. Prouvé par exécution sur deux fichiers minimaux :

```text
ruff --select S110 sur un fichier contenant `except OSError: pass` puis `except: pass`
  → réglage par défaut      : 1 erreur (seul le `except` nu)
  → check-typed-exception   : 2 erreurs
```

Conséquence pour LINT-001 : compter sur le rapport par défaut donnerait une fausse impression de propreté. Avec
`lint.flake8-bandit.check-typed-exception = true`, `S110` remonte **59** sites — c'est le vrai gisement, et il
recoupe l'ordre de grandeur du décompte AST de `CONSTATS.md`.

### Autres jugements rendus par lecture de code

- `F401` : sur 10, **5** sont dans `azimut/` (`QFont`/`QIcon`/`QPainter` dans `atlas_window.py:7`, `Path` dans
  `scope/treeview.py:17`, `ACTION_KEEP` dans `dedup/actions.py:19`) ; les 5 autres dans `tests/`. Aucun effet à
  l'exécution. `ACTION_KEEP` mérite un coup d'œil (import laissé par un ancien chemin : rien ne prouve un oubli
  fonctionnel).
- `PLW1510` : les 12 sites testent `result.returncode` juste après (`binaries.py:157`, `fetch_binaries.py:397`,
  `check_selftest.py:72`) ou sont des tests qui ignorent volontairement le code. **Aucun défaut** : le correctif
  serait d'écrire `check=False` explicitement.
- `SIM115` : faux positif — `fh = None` (288), `open` (300), `fh.close()` dans `finally` (354).
- `B008` : les 6 sites sont des overrides Qt (`parent: QModelIndex = QModelIndex()`), motif imposé par PySide6.
- `RUF012` : les 3 attributs de classe sont des constantes de lecture (`_LEVEL_TOKENS`, `Env.system`, un tuple de
  test) ; aucun n'est muté — le défaut est théorique.

## 3. bandit — 26 signalements (22 Low, 2 Medium, 2 High)

### MEDIUM et HIGH, examinés un par un

| # | règle | site | constat | verdict |
|---|---|---|---|---|
| 1 | `B310` (Medium) | `packaging/fetch_binaries.py:89` | `urlopen(_request(url))` dans `download()` : l'URL vient d'une constante du module (BtbN, SourceForge, ExifTool découvert) en `https://`, jamais d'une entrée utilisateur. Le risque visé (schéma `file:`) est hors d'atteinte. | **bruit** (faux positif) |
| 2 | `B310` (Medium) | `packaging/fetch_binaries.py:112` | `fetch_text(url)` : même situation, appelants à URL constante. | **bruit** |
| 3 | `B202` (High) | `packaging/fetch_binaries.py:123` | bandit annonce `tarfile`, mais l'objet est un `zipfile.ZipFile` : `zf.extractall(target)`. `zipfile` retire déjà les chemins absolus et les `..` (doc Python) — pas de zip-slip par ce chemin. Aucun plafond de taille (bombe zip) : non couvert par bandit. | **bruit** pour cette règle |
| 4 | `B202` (High) | `packaging/fetch_binaries.py:131` | vrai `tarfile.extractall(target)`, branche `else` quand `tarfile.data_filter` est absent (Python 3.11.0–3.11.3). `requires-python = ">=3.11"` rend la branche atteignable. | **défaut réel**, déjà répertorié **PKG-002** |

Le cas 3 est à retenir : c'est un faux positif de bandit qui *documente* par accident une vraie question (extraction
d'archives sans limite de taille), à ne pas confondre avec le cas 4. Aucun des deux n'est prouvé par exécution ici
(pas de téléchargement réel ; Python 3.14 possède `data_filter`).

### LOW, par famille

| code | nombre | verdict | justification |
|---|---|---|---|
| `B404` | 8 | bruit | `import subprocess` seul ; l'usage est encadré (voir `B603`). |
| `B603` | 11 | bruit | Toujours une **liste** d'arguments (jamais `shell=True`), binaire résolu par `core/binaries.py`. |
| `B110` | 3 | **défaut réel** | Mêmes 3 sites que `S110` ; voir § 2. |

Aucun `# nosec` dans le dépôt (bandit : 0 exclusion), donc rien n'est masqué en amont.

## 4. vulture — 0 résultat à confiance ≥ 80

`vulture azimut --min-confidence 80` ne rend **rien** (code 0). À seuil plus bas, le bruit est celui attendu :

| invocation | résultat | verdict |
|---|---|---|
| `vulture azimut --min-confidence 80` | 0 | rien à signaler au seuil du projet |
| `vulture azimut --min-confidence 60` | 73 lignes | majoritairement **bruit** : overrides Qt (`dragEnterEvent`, `dropEvent`, `minimumTabSizeHint`), membres lus par le framework |
| `vulture azimut tests --min-confidence 80` | 16 lignes | **bruit** : fixtures pytest (`session`, `config`, `fake_modules`) prises pour des variables inutilisées |

Exemples de faux positifs vérifiés : `language_name`, `catalog_path`, `set_storage`, `summarize` sont appelés par
les tests (vulture ne scanne que `azimut/` et ignore donc ces appelants) ; `set_storage` est de plus documenté dans
`core/history.py:57`.

Seul candidat sérieux : **`optional_support`** (`azimut/modules/convert/imaging.py:61`) n'a **aucune** référence
hors de sa définition (`grep` sur `azimut tests tools packaging`). Code mort probable, sans impact ; non prouvé
(une utilisation dynamique n'est pas exclue). Verdict : **défaut réel** mineur, à confirmer avant suppression.

Conclusion : vulture n'est pas exploitable en l'état comme détecteur de code mort sur ce dépôt (Qt + fixtures). Il
faudrait au minimum lancer `vulture azimut tests` et exclure les décorateurs `@pytest.fixture`/slots Qt.

## 5. pip-audit — aucune vulnérabilité dans les dépendances déclarées

`pip-audit -r requirements.txt` → **« No known vulnerabilities found »**, code de sortie 0 (rejoué au HEAD).
Il n'y a donc **aucune entrée à lister** avec une version corrigée pour les dépendances livrées
(`PySide6-Essentials`/`Addons` < 6.13, `Pillow` ≥ 13.0.0).

En revanche, l'audit de l'environnement (`pip-audit` sans `-r`, qui inclut `pip` lui-même) donne **code 1** :

| paquet | version | avis | version corrigée |
|---|---|---|---|
| pip | 26.0.1 | `PYSEC-2026-2875` | 26.1 |
| pip | 26.0.1 | `PYSEC-2026-2876` | 26.1 |
| pip | 26.0.1 | `PYSEC-2026-196` | 26.1.2 |
| pip | 26.0.1 | `PYSEC-2026-3721` | 26.2.0 |

Verdict : **défaut réel de l'environnement de développement**, pas des dépendances livrées (pip n'est pas
embarqué). Correctif : `pip` ≥ **26.2.0**. Non appliqué : c'est une mise à jour de dépendance, interdite par les
règles du lot ; décision du propriétaire.

Limite de méthode : `requirements.txt` n'épingle pas (`>=`, `<`) ; `pip-audit -r` résout les **dernières** versions
compatibles, donc il n'audite pas forcément les versions réellement installées. L'audit de l'environnement couvre
ce trou et ne remonte que `pip`.

## 6. Proposition de section `[tool.ruff]` — **non appliquée**

Objectif : ne garder que des règles fiables (pas de faux positif sur ce dépôt) et **aucune** qui impose un
reformatage massif (règle 2 du bloc de consignes). `E5` est volontairement absent : le code va jusqu'à 191
caractères, activer `E501` reviendrait à reformater tout le dépôt. `I`, `UP` et `SIM` sont écartés pour la même
raison (style, gros diff) ; ils restent possibles dans un lot dédié.

```toml
[tool.ruff]
target-version = "py311"                     # aligné sur requires-python = ">=3.11"

[tool.ruff.lint]
select = [
    "F",        # pyflakes : F821 nom indéfini, F401 import mort, F841 variable inutilisée
    "E4", "E9", # imports/IO et erreurs réelles (pas E5 ni E7 : pas de reformatage)
    "B",        # bugbear : B905 zip() sans strict, B007 boucle, etc.
    "PLW1510",  # subprocess.run sans check explicite
    "S110",     # except …: pass silencieux — le cœur de LINT-001
]
ignore = ["B008"]                            # Qt : parent=QModelIndex() en argument par défaut

[tool.ruff.lint.flake8-bandit]
check-typed-exception = true                 # sinon S110 ne voit que 3 des 56 except: pass

[tool.ruff.lint.per-file-ignores]
"tests/**" = ["PLW1510"]                     # codes de sortie volontairement ignorés
```

Effet mesuré de ce jeu, sur le même arbre (commande `--isolated --select … --ignore B008`) :
**94 erreurs** — `S110` 59, `PLW1510` 12, `F401` 10, `B905` 8, `B007` 2, `F841` 1, `F821` 1, `E402` 1.
Sur ces 94 : 82 sont des points réels (dont 59 except silencieux, le chantier LINT-001) et 12 `PLW1510` sont des
intentions à rendre explicites. À comparer aux 207 actuelles, dont une grande part est du style.

Si tu préfères un départ silencieux, la variante minimale est `select = ["F", "E9"]` : elle ne remonte que les
vrais défauts (`F821`, `F401`, `F841`) sans rouvrir le chantier des except.

## 7. Suites proposées (aucune correction dans ce lot)

1. **Lot 4b (petit, sûr)** : `F821` (`tests/core/test_workers.py:288`, import `signal` manquant) et `F401` dans
   `azimut/` (5 imports). Un test rouge est possible pour `F821` (le test échoue en `NameError`), les imports morts
   n'en demandent pas.
2. **Lot 4c (LINT-001, par module)** : traiter les `S110` en journalisant l'exception ignorée, pas en supprimant le
   `pass` ; commencer par `core/workers.py` (8) et `modules/dedup/livephoto.py` (6).
3. **Environnement** : `pip` ≥ 26.2.0 (décision propriétaire, hors périmètre audit).
4. **vulture** : ne l'utiliser qu'avec `azimut tests` et des exclusions de fixtures, sinon il ne produit que du bruit.
5. **bandit** : `B202`/`B310` ne demandent rien de plus que PKG-002 (verrouillage des empreintes) ; ne pas ajouter
   de `# nosec` inutile.

## 8. Ce qui reste non vérifié

- **B310/B202** : jugés par lecture de code et documentation, aucun téléchargement réel effectué ; le cas 4
  (`data_filter` absent) n'est pas reproductible sur Python 3.14.
- **pip-audit** : dépend de la base d'avis interrogée le 7 novembre 2026 ; peut changer sans modification du code.
- **vulture** : les 73 lignes du seuil 60 n'ont pas été triées une par une ; seul `optional_support` a été vérifié.
- **`BLE001` (22)** : non trié site par site — c'est un choix de conception (journalisation ou non), pas un défaut
  automatique ; il faut une décision par emplacement, hors de ce lot.
