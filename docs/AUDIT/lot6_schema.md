# Lot 6a — PKG-001 : schéma du verrouillage des binaires tiers

Branche `feature/audit-0.2.0-suite` ; code lu au HEAD `98cbcfb`, commit créé au HEAD `0430c00` (l'autre lot a
committé `e9a72de` et `0430c00` pendant ce lot), 8 novembre 2026.
**Aucun fichier de code n'a été modifié** : ce document est le seul ajout (`[PKG-001]`).
L'arbre de travail est partagé avec un autre lot (`azimut/launcher/atlas_window.py`, `tools/update_translations.py`,
les 27 `.ts`/`.qm` et `tests/ui/test_i18n_literals.py` non suivi étaient modifiés hors de ce lot ; ils n'ont pas été
touchés). Les modifications de ce lot-ci se limitent à `docs/AUDIT/lot6_schema.md`.

Nature des preuves, conformément aux consignes :

- **lecture de code** : chaque affirmation sur `packaging/fetch_binaries.py` cite un numéro de ligne du HEAD `98cbcfb` ;
- **prouvé par exécution** : l'inventaire de l'arbre (§ 1, dernière puce) et la validation du JSON d'exemple (§ 3.4) ;
- **non vérifié** : tout ce qui dépend des serveurs amont (URL réelles, empreintes, rétention des releases) — § 7.
  **Aucune requête réseau n'a été effectuée dans ce lot.**

## 1. État mesuré du code actuel

| composant | téléchargement | vérification actuelle | stabilité de la source | lignes |
|---|---|---|---|---|
| FFmpeg + FFprobe Windows / Linux | 1 archive BtbN (zip ou tar.xz) | `checksums.sha256` de la **même** release ; liste indisponible → AVERTISSEMENT ; entrée absente → AVERTISSEMENT | `.../releases/download/latest/` (**mouvant**) | 201-216, 218-240 |
| FFmpeg + FFprobe macOS | 2 zip Martin Riedl (`ffmpeg.zip`, `ffprobe.zip`) | **aucune** | `redirect/latest/...`, repli `release` → `snapshot` | 242-262 |
| ExifTool Windows | `exiftool-<v>_64.zip` (SourceForge) | `checksums-<v>.txt` d'exiftool.org ; indisponible ou illisible → AVERTISSEMENT | version découverte dynamiquement | 176-193, 284-297 |
| ExifTool Linux / macOS | tar.gz du tag GitHub, replis SourceForge puis exiftool.org | **aucune** pour GitHub (`if "github.com" not in url`) | tag = version (pérenne) | 318-344 |
| Perl portable | tar.xz relocatable-perl | **aucune** | `releases/latest/download/` (**mouvant**) | 346-368 |

Faits de lecture qui commandent le schéma :

- `download()` (86-113) calcule **déjà** le SHA-256 en flux pendant l'écriture : le point d'insertion du contrôle
  existe, sans seconde passe ni coût réseau supplémentaire. Il renvoie l'empreinte, aujourd'hui seulement recopiée.
- `sources` (59) et `write_sources` (371-387) écrivent URL + empreinte dans `BINARIES-SOURCES.txt`. C'est un
  **compte rendu a posteriori**, pas une politique : l'empreinte consignée est celle du fichier reçu, quelle qu'elle soit.
- Trois replis de source existent : branche demandée → `master` (219), `release` → `snapshot` (247),
  GitHub → SourceForge → exiftool.org (319-323). **Toute source de secours contourne l'épinglage** : un verrou ne
  peut pas cohabiter avec un repli.
- `download_first` (154-173) n'accepte un fichier que sur sa **signature d'archive** (`MAGIC`, 53) : cela écarte une
  page HTML d'erreur, pas un contenu substitué.
- **Piège d'implémentation** — ligne 170 :
  `except (urllib.error.URLError, RuntimeError, ValueError, OSError)`. Le module lève `RuntimeError` pour une
  empreinte fausse (`_verify_btbn` 213, `_verify_exiftool` 186). Ces appels sont aujourd'hui **hors** de la boucle
  de `download_first` ; une implémentation naïve qui vérifierait l'empreinte *dans* la boucle verrait l'erreur
  avalée comme « source suivante » et passerait au candidat non épinglé — exactement le mode permissif à fermer.
- `main` (412-431) ne connaît que `--force` (414) et `--no-verify` (415). Or `--no-verify` signifie « ne pas
  **exécuter** les binaires après installation » (453) : le nom devient trompeur dès qu'un contrôle d'intégrité
  existe.
- Ligne 428 : si `bin/<plateforme>/` est complet, `main` sort en 0 **sans aucun contrôle**. Le verrou ne
  s'appliquerait donc pas aux binaires déjà en place.
- Rien n'est épinglé aujourd'hui : `BTBN_BASE` (48) et `PERL_BASE` (54) contiennent `latest`, `MARTIN_RIEDL` (49)
  contient `redirect/latest`, et la version d'ExifTool est lue sur la page d'accueil ou `ver.txt` (265-281).
- **Arbre local** (prouvé par exécution) : `find bin -maxdepth 2` → `bin` et `bin/README.md` seulement ;
  `ls packaging/*.json` → aucun fichier ; `.gitignore` ignore `bin/*/` mais n'exclut que `*.local.json`, donc
  `packaging/binaries.lock.json` sera versionné à côté du script.

## 2. Principe proposé

1. **Le contrôle dur est l'empreinte, pas l'URL.** Une URL immuable est un confort opérationnel (elle évite de
   casser le build à chaque dérive), mais la garantie est : « les octets installés sont ceux relus et committés ».
   Une source qui n'offre aucun chemin versionné peut donc rester épinglée par le seul SHA-256.
2. **Un seul fichier pour les 4 plateformes** : une montée de version doit être visible dans un seul diff, et il
   devient impossible d'oublier Windows ou macOS Intel en mettant à jour Linux.
3. **Défaut = échec.** Verrou absent, illisible, schéma inconnu, entrée manquante ou empreinte différente :
   le téléchargement s'arrête **avant** d'installer quoi que ce soit. Le contournement est un drapeau explicite,
   bruyant et consigné dans le manifeste livré.
4. **Une entrée par archive réellement téléchargée**, pas par binaire produit : c'est le seul découpage qui
   corresponde à un objet hachable. Cela donne 13 archives (§ 3.3).

## 3. Format de `packaging/binaries.lock.json`

### 3.1 Exemple (partiel, valeurs factices)

Les empreintes, tailles et numéros de build ci-dessous sont **inventés** ; ils montrent la forme, pas des valeurs à
reprendre. Le fichier réel comportera les 13 entrées du § 3.3.

```json
{
  "schema": 1,
  "generated_at": "2026-11-08",
  "generator": "python packaging/fetch_binaries.py --update-lock",
  "exiftool_version": "13.59",
  "ffmpeg_branch": "n9.0",
  "artifacts": [
    {
      "platform": "linux-x64",
      "asset": "ffmpeg",
      "component": "ffmpeg",
      "version": "n9.0",
      "url": "https://github.com/BtbN/FFmpeg-Builds/releases/download/autobuild-2026-11-06-13-02/ffmpeg-n9.0-latest-linux64-gpl-9.0.tar.xz",
      "filename": "ffmpeg-n9.0-latest-linux64-gpl-9.0.tar.xz",
      "sha256": "0000000000000000000000000000000000000000000000000000000000000001",
      "size": 123456789,
      "magic": "xz"
    },
    {
      "platform": "macos-arm64",
      "asset": "ffprobe",
      "component": "ffmpeg",
      "version": "release",
      "url": "https://ffmpeg.martin-riedl.de/redirect/latest/macos/arm64/release/ffprobe.zip",
      "url_immutable": false,
      "note": "Aucun chemin versionné connu au 8 novembre 2026 ; l'empreinte seule garantit l'identité.",
      "filename": "ffprobe.zip",
      "sha256": "0000000000000000000000000000000000000000000000000000000000000002",
      "size": 23456789,
      "magic": "zip"
    },
    {
      "platform": "windows-x64",
      "asset": "exiftool",
      "component": "exiftool",
      "version": "13.59",
      "url": "https://sourceforge.net/projects/exiftool/files/exiftool-13.59_64.zip/download",
      "filename": "exiftool-13.59_64.zip",
      "sha256": "0000000000000000000000000000000000000000000000000000000000000003",
      "size": 9876543,
      "magic": "zip"
    },
    {
      "platform": "linux-x64",
      "asset": "perl",
      "component": "perl",
      "version": "2.5.0",
      "url": "https://github.com/skaji/relocatable-perl/releases/download/v2.5.0/perl-linux-amd64.tar.xz",
      "filename": "perl-linux-amd64.tar.xz",
      "sha256": "0000000000000000000000000000000000000000000000000000000000000004",
      "size": 34567890,
      "magic": "xz"
    }
  ]
}
```

### 3.2 Champs

| champ | type | requis | règle |
|---|---|---|---|
| `schema` | entier | oui | `1` ; toute autre valeur → refus explicite (pas d'interprétation devinée) |
| `generated_at` | chaîne `AAAA-MM-JJ` | oui | date de génération, informative |
| `generator` | chaîne | oui | commande exacte ayant produit le fichier |
| `exiftool_version` | chaîne `\d+\.\d+` | oui | doit être identique au champ `version` de **toutes** les entrées `exiftool` (R8) |
| `ffmpeg_branch` | chaîne | non | informative (`n9.0`, `master`) |
| `artifacts` | tableau non vide | oui | une entrée par archive téléchargée |
| `artifacts[].platform` | énum | oui | `linux-x64`, `windows-x64`, `macos-arm64`, `macos-x64` — mêmes valeurs que `platform_tag()` (71-77) |
| `artifacts[].asset` | énum | oui | `ffmpeg`, `ffprobe`, `exiftool`, `perl` ; unique par plateforme |
| `artifacts[].component` | énum | oui | `ffmpeg` (couvre `ffmpeg`+`ffprobe`), `exiftool`, `perl` |
| `artifacts[].version` | chaîne | oui | lisible par un humain ; ne sert **pas** à résoudre l'URL |
| `artifacts[].url` | `https://` | oui | URL exacte utilisée pour le téléchargement |
| `artifacts[].filename` | chaîne | oui | dernier segment de l'URL ; unique par plateforme (fichiers temporaires) |
| `artifacts[].sha256` | 64 hexadécimaux minuscules | oui | empreinte de l'archive telle que téléchargée |
| `artifacts[].size` | entier > 0 | non | octets ; permet un contrôle `Content-Length` avant lecture |
| `artifacts[].magic` | énum | oui | `zip`, `gz`, `xz` — clés exactes de `MAGIC` (53), pas de re-dérivation depuis l'extension |
| `artifacts[].url_immutable` | booléen | non (défaut `true`) | `false` autorise `latest`/`redirect` dans l'URL |
| `artifacts[].note` | chaîne | si `url_immutable: false` | justification (source sans chemin versionné) |

### 3.3 Couverture obligatoire : 13 archives

| plateforme | `ffmpeg` | `ffprobe` | `exiftool` | `perl` | total |
|---|---|---|---|---|---|
| `windows-x64` | BtbN zip (1 archive pour les 2 binaires) | — (inclus) | SourceForge `_64.zip` | — (Perl intégré à `exiftool.exe`) | 2 |
| `linux-x64` | BtbN tar.xz | — (inclus) | tar.gz du tag GitHub | relocatable-perl | 3 |
| `macos-arm64` | Martin Riedl zip | Martin Riedl zip | tar.gz du tag GitHub | relocatable-perl | 4 |
| `macos-x64` | Martin Riedl zip amd64 | Martin Riedl zip amd64 | tar.gz du tag GitHub | relocatable-perl | 4 |

Cette table reproduit exactement les appels de `main` (431-448) : un seul téléchargement BtbN fournit `ffmpeg` **et**
`ffprobe` (233-236), tandis que macOS les télécharge séparément (244-261) ; `install_perl` (346) n'est appelé que
pour Linux et macOS.

### 3.4 Règles de validation

- **R1** `schema == 1`, sinon refus.
- **R2** `(platform, asset)` unique ; le couple manquant **ou en trop** pour la plateforme visée est une erreur
  (une faute de frappe dans `asset` ne doit pas passer pour une absence).
- **R3** `sha256` : exactement 64 caractères `[0-9a-f]`. Une empreinte vide, `null` ou en majuscules est refusée.
- **R4** Couverture exacte du § 3.3 pour `platform_tag()`.
- **R5** `url` en `https://` ; interdiction de `/download/latest/` et `/redirect/latest/` **sauf** si
  `url_immutable: false` **et** `note` non vide. (Le nom d'asset BtbN contient légitimement `-latest-` : la règle
  porte sur le segment de release, pas sur le nom de fichier.)
- **R6** `filename` unique par plateforme et égal au dernier segment de `url` hors redirection.
- **R7** `magic` ∈ {`zip`, `gz`, `xz`} (table `MAGIC`, 53).
- **R8** Toutes les entrées `exiftool` portent la même `version` que `exiftool_version` (Empêche Windows 13.60 et
  Linux 13.59 dans le même paquet).
- **R9** `size`, s'il est présent, est un entier > 0 ; s'il diffère d'un `Content-Length` fourni, échec **avant**
  lecture complète.
- **R10** JSON strict en UTF-8 (pas de commentaire, pas de virgule finale), entrées triées par `platform` puis
  `asset` : le diff de `--update-lock` reste lisible.

Contrôle exécuté sur le bloc JSON du § 3.1 (extrait du présent fichier, donc celui qui est réellement committé) :

```bash
python - <<'PY'
import json, re, pathlib
text = pathlib.Path("docs/AUDIT/lot6_schema.md").read_text(encoding="utf-8")
block = text.split("```json\n", 1)[1].split("```", 1)[0]
lock = json.loads(block)                                    # R10 : JSON strict
assert lock["schema"] == 1                                  # R1
pairs = [(a["platform"], a["asset"]) for a in lock["artifacts"]]
assert len(pairs) == len(set(pairs))                        # R2
for a in lock["artifacts"]:
    assert re.fullmatch(r"[0-9a-f]{64}", a["sha256"])       # R3
    assert a["magic"] in {"zip", "gz", "xz"}                # R7
    assert a["url"].startswith("https://")
    if "/download/latest/" in a["url"] or "/redirect/latest/" in a["url"]:
        assert a["url_immutable"] is False and a["note"]    # R5
    if a["asset"] == "exiftool":
        assert a["version"] == lock["exiftool_version"]     # R8
print("OK : bloc JSON du § 3.1 conforme à R1-R3, R5, R7, R8, R10")
PY
```

Résultat (exécuté sur ce fichier après écriture) :

```text
OK : bloc JSON du 3.1 conforme a R1-R3, R5, R7, R8, R10 (4 entrees)
RC_VALIDATION=0
```

Cela valide la **forme** du fichier, pas les valeurs : les URL et les empreintes réelles restent à relever avec le
réseau (§ 7.1).

## 4. Comportement attendu de `packaging/fetch_binaries.py`

### 4.1 Mode verrouillé (défaut, sans option)

1. Lire `packaging/binaries.lock.json` **avant tout réseau** : un verrou absent ou invalide ne doit pas coûter
   un seul octet téléchargé.
2. Sélectionner les entrées de `platform_tag()` (71-77) et vérifier R1-R10.
3. Télécharger **une seule** URL par entrée : plus de repli de branche (219), de repli `release` → `snapshot` (247)
   ni de cascade GitHub → SourceForge → exiftool.org (319-323).
4. Comparer l'empreinte renvoyée par `download()` (86-113) à `sha256`. Si `size` est connu et `Content-Length`
   présent, comparer la taille d'abord (R9) — le message doit dire « taille différente », pas « empreinte
   différente », pour ne pas masquer une troncature.
5. En cas de succès seulement : extraire et installer. `sources` enregistre alors l'URL, l'empreinte **et** la
   mention du verrou (`vérifié : binaries.lock.json, schéma 1, 2026-11-08`) dans `BINARIES-SOURCES.txt`.
6. En cas d'échec : supprimer l'archive partielle, **ne rien installer**, message actionnable.

Message attendu en cas d'écart (l'action corrective doit être dans le message, pas seulement dans ce document) :

```text
ERREUR : empreinte SHA-256 différente pour ffmpeg-n9.0-latest-linux64-gpl-9.0.tar.xz
  attendu (packaging/binaries.lock.json) : 0123…cdef
  obtenu                                 : fedc…3210
  Rien n'a été installé. Si le changement est légitime (nouvelle version amont),
  relancez « python packaging/fetch_binaries.py --update-lock », relisez le diff du
  verrou, puis committez-le. Essai local sans garantie d'identité : --allow-unpinned.
```

### 4.2 Codes de sortie (proposition)

| code | sens | situation actuelle |
|---|---|---|
| 0 | succès | 0 |
| 1 | échec d'exécution des binaires (`verify`, 395-410) ou erreur inattendue | 1 |
| 2 | verrou absent, illisible, `schema` inconnu, entrée manquante (R1-R4) | n'existe pas |
| 3 | intégrité : empreinte ou taille différente (R3, R9) | `RuntimeError` non interceptée → traceback, code 1 |
| 4 | source injoignable : HTTP 404 sur l'URL épinglée, réseau | `RuntimeError` non interceptée → code 1 |

Des codes distincts permettent à la CI et au mainteneur de distinguer « amont a bougé » (3), « amont a disparu » (4)
et « verrou à mettre à jour » (2) sans lire les logs.

### 4.3 Échappatoire explicite

`--allow-unpinned` : télécharge comme aujourd'hui (découverte + replis), **sans** comparer au verrou, et l'annonce :

```text
AVERTISSEMENT : --allow-unpinned — AUCUNE garantie d'identité sur les binaires tiers.
Ne jamais utiliser en CI ni pour un paquet destiné à la publication.
```

- `BINARIES-SOURCES.txt` porte alors `verrou : IGNORÉ (--allow-unpinned)` : le manifeste livré dit la vérité.
- **Pas de variable d'environnement équivalente** : un drapeau ne s'hérite pas silencieusement d'un poste à un
  workflow.
- Incompatible avec `--update-lock` (refus), pour que le verrou ne puisse pas être écrit depuis un état non vérifié.

### 4.4 `--update-lock` (mainteneur)

- **Seul écrivain** du fichier ; jamais atteint par le chemin par défaut.
- Réutilise la découverte actuelle (branche, `release` → `snapshot`, page d'accueil d'ExifTool), télécharge, calcule
  les empreintes, réécrit le verrou trié (R10).
- **Affiche un diff par entrée** (ancien → nouveau : version, URL, SHA-256) : le contrôle réel est la relecture du
  commit, pas la commande.
- Options : `--platform <tag>` pour ne réécrire qu'une plateforme (limiter un diff qu'on ne peut pas tester
  localement), `--ffmpeg-branch` et `--exiftool-version` en remplacement explicite des variables d'environnement.
- Sort en 0 sans réécrire si rien n'a changé (rejouable en CI de veille).
- Ne signe rien et ne prouve rien sur la légitimité du changement : voir § 6.9.

### 4.5 Options et variables existantes

- `--force` (414) : inchangé — re-télécharge **et** re-vérifie.
- `--no-verify` (415) : à renommer `--no-selftest` (alias `--no-verify` conservé, déprécié), sinon « ne pas vérifier »
  sera lu comme « ne pas contrôler les empreintes » au pire moment. Décision § 8.
- `AZIMUT_FFMPEG_BRANCH` (56) et `AZIMUT_EXIFTOOL_VERSION` (266) : **inactives** en mode verrouillé. Si elles sont
  définies à une valeur différente du verrou, **échec explicite** plutôt qu'ignoration silencieuse (l'opérateur
  croirait avoir changé de version). Elles restent honorées avec `--update-lock`.
- `exiftool_version()` (265-281) : la découverte réseau n'a de sens qu'en `--update-lock` ; en mode verrouillé la
  version vient du fichier.

## 5. Impact sur la CI

- **Aucune modification de workflow n'est nécessaire pour que le contrôle s'applique** : `.github/workflows/build.yml:138`
  appelle `python packaging/fetch_binaries.py` sans argument, donc le mode verrouillé est le comportement par défaut.
  C'est l'intérêt du choix « échec par défaut ».
- 4 jobs `package` (matrice `linux-x64`, `windows-x64`, `macos-arm64`, `macos-x64`) : 2 à 4 archives chacun,
  **13 au total**. Coût nul : le SHA-256 est déjà calculé pendant le flux (86-113).
- **Conséquence assumée** : une dérive amont rend le job `package` rouge ; une release ne peut plus partir avec des
  binaires non relus. `macos-x64` est `optional: true` + `continue-on-error` (build.yml 126-129) : une dérive qui ne
  touche que lui n'empêche pas la release, mais le job échoue et le DMG Intel manque.
- `AZIMUT_REQUIRE_BINARIES: "1"` (env du job `package`) garantit la **présence** des binaires ; le verrou garantit
  leur **identité**. Les deux restent nécessaires : `build.py` (`check_binaries`, 78-95) ne teste que l'existence.
- `test` et `audit` n'appellent pas `fetch_binaries.py` : les tests unitaires du lot 6b tournent **hors ligne**
  (réseau simulé), sans nouvelle dépendance à GitHub dans la matrice de tests.
- **Ne pas ajouter de cache de `bin/`** : un cache réutiliserait des binaires non revérifiés et rouvrirait le
  contournement de la ligne 428.
- **Mise à jour du verrou** : aucun job n'a aujourd'hui le droit d'écrire le dépôt (permissions globales
  `contents: read`, seul `release` demande `contents: write`). Une automatisation demanderait un job `schedule` avec
  `contents: write` + `pull-requests: write` et une PR relue. **Non implémenté** ; à décider (§ 8).
- **Provenance** : le job `release` atteste les *produits* (`release/*` + `SHA256SUMS.txt`), pas la recette. Copier le
  verrou et `BINARIES-SOURCES.txt` dans `release/` ferait couvrir les entrées par `SHA256SUMS.txt` et l'attestation
  SLSA. Amélioration possible, hors périmètre de ce lot.
- **Documentation à ajuster en 6b** (non modifiée ici) : `BUILD.md` (mention de `--update-lock`), `RELEASING.md`
  (deux lignes de dépannage : « empreinte différente », « URL épinglée 404 » ; préciser que
  `AZIMUT_EXIFTOOL_VERSION` n'agit plus qu'avec `--update-lock`).

## 6. Risques

| # | risque | effet | parade proposée | vérifié ? |
|---|---|---|---|---|
| 6.1 | Rétention des releases BtbN : un autobuild épinglé peut être supprimé | build cassé (code 4) sans changement de code | monter de version via `--update-lock` ; consigner la date dans `generated_at` | **non vérifié** |
| 6.2 | Tarballs auto-générés GitHub non garantis stables (compression/format régénérés) | faux positif d'empreinte sur ExifTool unix | message nommant cette cause possible + `--update-lock` après relecture ; à terme, préférer une source au fichier immuable | **non vérifié** |
| 6.3 | `ffmpeg.martin-riedl.de` peut n'offrir aucun chemin versionné | URL `latest` inévitable | `url_immutable: false` + empreinte seule comme garantie ; toute reconstruction amont casse le build | **non vérifié** |
| 6.4 | SourceForge redirige vers un miroir tournant, avec parfois une page HTML d'attente | contenu inattendu | `MAGIC` (53) écarte le HTML, l'empreinte écarte le reste ; URL épinglée propre à la version | **non vérifié** |
| 6.5 | Variables d'environnement désynchronisées du verrou | build non reproductible en apparence conforme | échec explicite si valeur ≠ verrou (§ 4.5) | lecture de code |
| 6.6 | `download_first` (170) avale `RuntimeError` | une vérification placée dans la boucle serait contournée par le repli | une seule candidate + vérification après retour, **ou** exception dédiée ne dérivant ni de `RuntimeError`, ni de `OSError`, ni de `ValueError` | lecture de code (ligne 170) |
| 6.7 | Sortie anticipée ligne 428 quand `bin/<tag>/` est complet | binaires en place jamais contrôlés | comportement inchangé assumé (CI part d'un arbre vide ; `--force` pour revérifier) et message explicite ; option `--check` hors périmètre | lecture de code |
| 6.8 | Téléchargement tronqué sans exception (connexion coupée proprement) | empreinte fausse prise pour une substitution | comparer `Content-Length` (R9) et décider d'un re-téléchargement unique avant échec (§ 8) | non vérifié |
| 6.9 | Le verrou n'est **pas une signature** | qui peut committer peut le modifier ; `--update-lock` est le chemin documenté | protection contre la dérive/compromission **amont** et la corruption, pas contre un dépôt compromis ; le contrôle réel est la relecture du diff | par construction |
| 6.10 | Deux archives ExifTool (Windows `_64.zip`, tarball unix) peuvent diverger | paquet incohérent entre plateformes | `exiftool_version` en tête + R8 | lecture de code |
| 6.11 | `--no-verify` ambigu | opérateur croyant désactiver les empreintes | renommage `--no-selftest` (§ 4.5) | lecture de code |
| 6.12 | Vieillissement : un verrou stable fige un FFmpeg/ExifTool vulnérable | binaire obsolète embarqué | `pip-audit` ne couvre pas les binaires : cadence de rafraîchissement = décision de sécurité (§ 8) | non vérifié |
| 6.13 | `--update-lock` sans filtre réécrit les 4 plateformes | diff qu'on ne peut pas tester localement (Windows/macOS) | `--platform` + relecture « aucune entrée non visée n'a bougé » | par construction |

## 7. Ce qu'il faudra tester

### 7.1 Avec le réseau (une fois pour constituer le verrou, puis périodiquement)

1. **Résoudre les 13 URL** : tag d'autobuild BtbN exact et nom d'asset ; chemin Martin Riedl (versionné s'il existe) ;
   URL du zip Windows ExifTool sur SourceForge ; URL du tag GitHub ExifTool ; tag de release relocatable-perl par
   architecture.
2. **Télécharger chaque archive** et consigner SHA-256 + taille ; recouper avec `checksums.sha256` (BtbN) et
   `checksums-<version>.txt` (ExifTool) quand ces listes existent. Un désaccord arrête la constitution du verrou.
3. **Stabilité** : télécharger deux fois chaque archive à quelques minutes d'intervalle et comparer les empreintes
   (le test de stabilité le moins coûteux).
4. **Aucune URL** du verrou ne contient `/download/latest/` ni `/redirect/latest/` (sauf `url_immutable: false` motivé).
5. **Bout en bout** : `--update-lock` puis `fetch_binaries.py` sans option → succès ; `BINARIES-SOURCES.txt` porte la
   mention du verrou.
6. **Tenue dans le temps** : rejouer l'étape 2 à J+7 et J+30 (rétention BtbN, GitHub, SourceForge, Martin Riedl).
7. **CI** : `workflow_dispatch` avec le verrou sur les 4 runners, y compris `macos-x64` (optionnel).
8. **Échecs provoqués sur réseau réel** : un chiffre modifié dans le verrou → code 3, aucun repli, rien d'installé ;
   une URL épinglée pointée vers un fichier absent → code 4 distinct.

### 7.2 Hors réseau (lot 6b, réseau simulé — `unittest.mock` sur `urllib.request.urlopen`)

1. Verrou absent → code 2 et **aucun** appel réseau (assertion sur le mock).
2. Entrée manquante pour la plateforme → code 2.
3. `sha256` différent → code 3, **pas** de repli vers un second candidat (c'est le test qui protège du piège 6.6).
4. `sha256` vide, `null`, en majuscules ou de mauvaise longueur → verrou refusé (R3).
5. `schema` inconnu (0, 2, `"1"`) → refus (R1).
6. `asset` inconnu ou en double sur une plateforme → refus (R2), et une entrée en trop n'est pas ignorée.
7. Windows sans entrée `perl` accepté ; Windows **avec** entrée `perl` refusé (couverture exacte, R4).
8. macOS sans `ffprobe` refusé (les deux archives sont nécessaires).
9. `--allow-unpinned` → succès, avertissement présent, `BINARIES-SOURCES.txt` marque « verrou IGNORÉ ».
10. `--update-lock` sur téléchargements simulés → fichier trié, réécriture idempotente, aucun `latest`/`redirect` non motivé.
11. `AZIMUT_EXIFTOOL_VERSION` différente du verrou → échec en mode verrouillé ; appliquée avec `--update-lock`.
12. `--force` re-télécharge et re-vérifie ; `--no-selftest` n'exécute pas les binaires mais vérifie quand même les empreintes.
13. `Content-Length` différent de `size` → échec avant lecture (R9).
14. Test statique sur le fichier committé : R1-R10, plus « aucune URL `latest`/`redirect` non motivée ».

Aucun de ces essais n'a été exécuté dans ce lot.

## 8. Décisions à valider avant implémentation

1. **Format** : fichier unique `packaging/binaries.lock.json`, tableau `artifacts`, clés en anglais (comme les
   identifiants du code) ? *Recommandé : oui.*
2. **Nom de l'échappatoire** : `--allow-unpinned` (recommandé) ou `--no-lock` ?
3. **Codes de sortie** 2/3/4 (§ 4.2) ou tout en 1 avec des messages distincts ?
4. **Écart d'empreinte** : échec immédiat (recommandé) ou un re-téléchargement unique puis échec (couvre 6.8) ?
5. **`--no-verify`** : renommer `--no-selftest` avec alias déprécié, ou seulement documenter l'ambiguïté ?
6. **`bin/` déjà complet** (ligne 428) : conserver la sortie anticipée (recommandé pour ce lot) ou ajouter `--check`
   qui re-hache l'existant ?
7. **Mise à jour automatisée** : job `schedule` + PR (recommandé à terme), ou mise à jour manuelle assumée ?
8. **Cadence** : à quelle fréquence revoir le verrou (sécurité, 6.12) — mensuelle, trimestrielle, à chaque release ?
9. **`--update-lock` en CI** : l'interdire hors du job de veille dédié (variable `CI`/`GITHUB_ACTIONS`), ou s'en
   remettre à la relecture du diff ?

Une fois ces points tranchés, ils iront dans `docs/DECISIONS.md` (D14 et suivantes) **en même temps que
l'implémentation** ; ce fichier n'a pas été modifié dans ce lot.

## 9. Ce qui n'est pas vérifié

- **Aucune requête réseau** : les URL réelles, les tags d'autobuild BtbN, l'existence d'un chemin versionné chez
  Martin Riedl, la rétention des releases et les empreintes de référence restent **inconnues**. Le § 7.1 décrit le
  travail à faire, il ne l'a pas fait.
- **Aucune exécution du code de packaging** : ce lot ne modifie rien, donc aucun test rouge/vert n'est attendu. Le
  JSON d'exemple est validé contre R1-R3/R5/R7/R8/R10 (§ 3.4), ce qui ne prouve rien sur le comportement futur.
- **Windows et macOS non testés** ici : les chemins `install_exiftool_windows` (284-297) et `install_ffmpeg_macos`
  (242-262) sont lus, pas exécutés.
- **BtbN / GitHub / SourceForge** : aucune vérification de disponibilité, de débit ni de quota.
- L'état de la suite `pytest` au moment du commit est reporté dans la réponse du lot, pas dans ce document.
