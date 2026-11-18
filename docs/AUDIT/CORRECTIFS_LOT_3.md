# Correctifs — lot 3

Ce lot réintègre le lot 2 sur le dépôt modifié entre-temps par un autre outil (interface « glass », sélecteur de
fichiers, note personnelle, traductions), puis corrige les défauts trouvés ensuite.

## 1. État du dépôt reçu

Le dépôt contenait les **tests** du lot 2 mais pas tout son **code** : 10 fichiers sources étaient restés à leur état
d'origine, 11 autres avaient été modifiés des deux côtés. Résultat : 17 tests rouges et 2 erreurs d'import.

| Action | Fichiers |
|---|---|
| Repris tels quels du lot 2 | `core/binaries.py`, `core/ffmpeg_args.py`, `core/fsutil.py`, `convert/probe.py`, `dedup/engine.py`, `takeout/engine.py`, `RELEASING.md`, `packaging/azimut.spec`, `packaging/fetch_binaries.py`, `tests/core/test_binaries.py` |
| Fusionnés sans conflit (fusion à trois voies) | `convert/engine.py`, `convert/workers.py`, `convert/profiles.py`, `ui/module_window.py`, `COMPATIBILITE.md` |
| Réintégrés à la main | `convert/window.py` (case « ne pas recopier les JSON »), `launcher/atlas_window.py` (taille selon l'écran), `ui/widgets.py` et `dedup/window.py` (lecteur intégré), `README.md`, `MANUEL.md` |

## 2. Lecteur audio/vidéo de Dedup (DD-001)

L'autre outil avait déjà branché le lecteur de Tags sur Dedup. Ont été ajoutés, sans changer son aspect :

- un seul son à la fois quand deux aperçus sont côte à côte (`playbackStarted` → `pause`) ;
- `MediaPreview.release()` appelé avant l'analyse, l'application, l'annulation et la fermeture : Windows refuse de
  supprimer un fichier encore ouvert par le lecteur ;
- même fichier demandé deux fois : le lecteur n'est pas relancé (la lecture en cours continue après « Garder / Supprimer ») ;
- double-clic sur un audio ou une vidéo : lecture dans l'aperçu au lieu d'ouvrir une application externe ;
- volume et sourdine communs (`_AudioBus`), ligne de volume facultative (`volume_controls=True`, Dedup seulement) ;
- état d'échec (`has_failed`) : un codec absent ne bloque plus le fichier suivant ;
- pochette intégrée des fichiers audio, chargée hors du fil de l'interface ;
- aperçu FFmpeg d'un fichier dont le nom contient « % » (motif de séquence) : alias neutre, lien symbolique avant copie.

## 3. Convert

- **Pistes à disposition de canaux inconnue** (nouveau) : `-af aformat=channel_layouts=…` échoue (« Channel layout
  change is not supported ») sur les AAC/M4A dont le conteneur ne donne que le nombre de canaux (quad(side), 5.1(side),
  6.0, hexagonal…). Reproduit avec FFmpeg 6.1.1, 1 seule tentative audio auparavant. Le worker relance désormais sans ce
  filtre après un échec qui désigne les canaux (`engine.needs_plain_audio`). L'ordre des canaux a été vérifié par
  analyse spectrale sur un 5.1 : identique à la source, surround conservé. Tests : `tests/convert/test_channel_layouts.py`
  (9) et un test de bout en bout du worker dans `test_audio_robustness.py`.
- **« Sonnerie - Nokia.m4a »** (« Could not open encoder before EOF », code -22) : liste d'édition `elst` qui masque tous
  les échantillons ; reprise avec `-ignore_editlist 1` (famille MP4 seulement), contrôle des sorties vides, résumé
  d'erreur lisible. Voir `CORRECTIFS_LOT_2.md` (CV-001 à CV-008).
- **JSON Takeout** : Convert ne supprime jamais un JSON de la source. Avec « copier les autres fichiers », les JSON sont
  copiés ; la case « Ne pas recopier les JSON Takeout déjà réinjectés » écarte seulement ceux dont la date et la
  position ont été écrites dans le fichier converti.

## 4. Constats de l'audit précédent corrigés

| ID | Correctif | Test (rouge avant) |
|---|---|---|
| LOG-008 | Un JSON apparié par titre tronqué n'est plus attribué à un média quand son titre complet nomme un autre média du dossier | `tests/takeout/test_truncated_titles.py` (2) |
| LOG-009 | Les JSON d'un dossier sans média sont comptés comme orphelins | idem |
| LOG-011 | `iter_files(on_link=…)` : le journal Dedup annonce enfin le vrai nombre de liens ignorés | `tests/core/test_skipped_links.py` (2) |
| LOG-012 | Jumelage Live Photo : la casse du dossier n'est repliée que sous Windows | `tests/dedup/test_live_modified.py` |

## 5. Comment les vérifications ont été faites

L'environnement de travail n'avait ni PySide6, ni pytest, ni ruff, ni réseau.

- exécuteur de remplacement (`docs/AUDIT/outils/minipytest.py`) et **faux `QtCore` fonctionnel** (signaux, threads,
  boucle d'événements) : le **vrai `ConvertWorker`** a tourné avec le **vrai FFmpeg 6.1.1** (libopus) ;
- résultat final : **510 tests réussis** sur 602 collectés ; 49 nécessitent `QtWidgets`, 30 `QTranslator`/`.qm`, 11 sont
  ignorés (plateforme, binaire absent) et 2 échouent seulement parce que leur sous-processus ne reçoit pas le faux Qt
  (vérifié séparément : l'import des six modules ne charge pas `QtWidgets`) ;
- détecteur de noms indéfinis (AST) sur tout le code : 0, et il a attrapé une erreur de ma part en cours de route
  (constantes supprimées par erreur, restaurées).

## 6. Ce qui n'a pas pu être vérifié ici — à faire chez vous

1. `pytest -q` : seule exécution qui couvre `QtWidgets` (dont `tests/ui/test_media_preview.py`, écrit pour l'API portée
   ci-dessus mais jamais exécuté) et la traduction.
2. Contrôle visuel de l'effet « verre » (`ui/glass.py`, 47 Ko) sous Windows, macOS et Linux, y compris en bureau à
   distance et sur machine modeste : aucun interrupteur « effets réduits » n'existe.
3. Textes nouveaux ou modifiés (« Photos, vidéos et audios uniquement », case JSON, ligne de volume, aide de Dedup) :
   relancer `python tools/update_translations.py` puis traduire ; en attendant, ils s'affichent en français.
4. Fichier `LICENSE` : toujours absent (choix du propriétaire, constat H-04).
5. Constats encore ouverts : I18N-001 (chaînes en dur restantes), PKG-001 (empreintes de référence, réseau requis),
   LOG-010 (JSON illisible compté « Sans JSON »), LOG-004 (permissions 0600 des écritures atomiques, décision D9).
