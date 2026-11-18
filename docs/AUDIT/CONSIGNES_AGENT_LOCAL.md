# Consignes pour un agent de code local — audit d'Azimut 0.2.0 (suite)

À placer dans `docs/AUDIT/` (ne pas committer s'il contient des notes personnelles). L'agent doit le lire en premier.

## Rôle
- Tu travailles dans le dépôt local, sur la branche `feature/audit-0.2.0-suite` (créée depuis `feature/audit-0.2.0`).
- Tu disposes du réseau, de Qt, de pytest, de ffmpeg et d'ExifTool : **tu dois prouver par exécution**, pas par raisonnement.
- Lis d'abord `docs/AUDIT/CONSTATS.md`, `docs/AUDIT/RAPPORT_FINAL.md`, `docs/PLAN.md`, `docs/DECISIONS.md`.
- Ne touche pas à `docs/AUDIT/outils/` ni à `docs/AUDIT/preuves/` (preuves et outils de remplacement de l'audit précédent).

## Interdits absolus
- `git push`, `git tag`, release, modification de `__version__` (reste `0.2.0`).
- `git reset --hard`, `git clean`, `git commit --amend`, rebase ou réécriture des commits existants, suppression de `.git`.
- Committer `.freebuff/`, `.cloud-attachments/`, `rapport.json`, `rapports_locaux/`, `*.whl`, `build/`, `*.egg-info/`.
- Reformatage global (`ruff format`, black), mise à jour de dépendances, renommage massif, refactoring opportuniste.
- Modifier un test existant pour le faire passer. Si un test te semble faux, explique pourquoi et attends ma réponse.
- Marquer un test `skip` ou `xfail` sans justification écrite dans le test.
- Écrire un chemin personnel dans un fichier du dépôt.

## Méthode, pour chaque constat
1. **Prouver** : commande exécutée et sortie.
2. **Test d'abord** : l'écrire, le lancer, constater qu'il **échoue pour la bonne raison**. Coller la sortie.
3. **Correctif minimal.**
4. Test seul vert, puis `QT_QPA_PLATFORM=offscreen pytest -q`, puis `QT_QPA_PLATFORM=offscreen pytest -q -W error`. Coller la dernière ligne de chaque résumé.
5. **Un commit par constat** : `type(portée): résumé [ID]`, en français.
6. Mettre à jour `docs/AUDIT/CONSTATS.md` (format ci-dessous).
7. **S'arrêter après chaque lot** et résumer : prouvé / lecture de code / non vérifié, commits créés. Attendre ma validation avant le lot suivant.

Format d'entrée du registre :
```
ID: … | Gravité: P0-P3 | Module: … | Axe: …
Constat : …   Preuve : … (commande, sortie)   Impact : …
Correctif: … — commit … | Statut: Corrigé / Non corrigé / Non vérifiable
Test    : …
```
Gravité : P0 perte de données ou sécurité · P1 plantage, logique grave, promesse non tenue · P2 cas limite, compatibilité · P3 documentation, hygiène.

## Honnêteté
- Distinguer « prouvé par exécution », « lecture de code » et « non vérifié ». Si une commande échoue ou si un outil manque, le dire.
- Ne jamais écrire « tous les tests passent » sans avoir collé la ligne de résumé de pytest.
- Un défaut que tu ne peux pas prouver reste « non vérifié », avec un protocole manuel pour l'humain.
- Pas de nouvelle fonctionnalité. Si un lot te paraît mal posé, dis-le plutôt que de l'élargir.

## Lots (dans l'ordre, un seul à la fois)

**Lot 0 — Base réelle.** Exécuter `bash docs/AUDIT/preuves/collecter_rapports_locaux.sh` (installer avant : `pip install ruff bandit vulture pip-audit`).
Ajouter `rapports_locaux/` au `.gitignore` (commit séparé). Mettre à jour `docs/AUDIT/00-baseline.md` avec les vrais chiffres. Ne rien corriger.

**Lot 1 — Relectures P0 (jamais faites).** Un fichier à la fois, dans cet ordre : `modules/takeout/engine.py` ; `modules/dedup/engine.py` et `livephoto.py` ;
`core/workers.py` et `core/binaries.py`. Pour chacun : **avant** de lire les tests, écrire la liste des cas limites attendus (perte ou altération de données d'origine,
écrasement, appariement ambigu, noms spéciaux, annulation, processus orphelins, chemin de recherche des binaires), puis la comparer aux tests existants.
Pour chaque défaut prouvé : test rouge, correctif minimal. Sinon : « non vérifié ».

**Lot 2 — LOG-002 (P2).** `libsvtav1` refuse toute dimension < 64 px, `libaom-av1` l'accepte (`docs/AUDIT/preuves/av1_dimension_minimale.py`).
Faire échouer proprement puis corriger : repli sur `libaom-av1` (si disponible) quand `libsvtav1` échoue sur un petit média. La logique de choix doit vivre dans
`convert/engine.py` (testable sans Qt), pas dans `workers.py`. Test e2e réel avec une image de 48×48 (ignoré sans ffmpeg).

**Lot 3 — SEC-002 (P2).** Les `QLabel`, `QMessageBox` et info-bulles qui affichent des données externes (noms de fichiers, valeurs de tags, chemins) sont en mode texte automatique :
un nom `<b>x</b>.jpg` peut être interprété comme du HTML. D'abord **inventorier** les points d'appel (grep) et me les lister ; ensuite forcer `Qt.TextFormat.PlainText` sur
ceux qui affichent des données externes. Test Qt : un nom HTML reste littéral.

**Lot 4 — Triage ruff / bandit / vulture / pip-audit.** Classer chaque résultat : défaut réel, bruit, ou réglage. Proposer une section `[tool.ruff]` raisonnable dans `pyproject.toml`
(règles fiables seulement). Ne corriger automatiquement que par famille de règle sûre, un commit par famille, aucun reformatage.

**Lot 5 — I18N-001 (P1), un module par commit.** Ordre : atlas (3 chaînes), convert (22, `window.py` et `codec_tree.py`), takeout (13), scope (14), rename (17), tags (19), dedup (27).
Pour chaque module : envelopper les chaînes visibles en `self.tr()` (ou `QCoreApplication.translate` hors `QObject`, contexte = nom de classe) **sans modifier le texte français** ;
laisser les chaînes techniques et les journaux. Étendre `SOURCES` de `tools/update_translations.py`. Régénérer les `.ts`/`.qm` avec cet outil ; les langues non traduites restent
« non terminées » (Qt affiche alors le texte source). Test : aucun littéral visible hors `tr()` dans le fichier (analyse AST). Captures avant/après pour l'humain.

**Lot 6 — PKG-001 (P1).** `packaging/fetch_binaries.py` vérifie les empreintes en mode permissif. **Proposer d'abord un schéma** (fichier de verrouillage `packaging/binaries.lock.json` :
URL exactes + SHA-256, échec par défaut, option explicite pour passer outre, mode `--update-lock` pour le mainteneur) et **attendre ma validation** avant d'implémenter.
Tests unitaires avec réseau simulé.

**Lot 7 — Petits constats.** LOG-003 (profil `video_fix_mono` sans sonde → message trompeur), PKG-002 (extraction tar sans filtre sur Python < 3.11.4), CI-001 (ajouter Python 3.11 aux
workflows de tests). Un commit chacun.

## Ce qui reste à l'humain
Vérifications visuelles (Azimut, RTL arabe/hébreu, nom `<b>x</b>.jpg`, conversion AVIF d'une petite image), choix de licence, validation de `THIRD_PARTY_NOTICES.md`,
décision sur le re-hachage dans Dedup (D4), droits 0600 des exports (D9), fusion finale.
