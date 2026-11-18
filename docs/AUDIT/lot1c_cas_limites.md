# Lot 1c — cas limites attendus : core/workers.py et core/binaries.py

Écrit **avant** lecture de `tests/core/`, après lecture de `workers.py`, `binaries.py` et des points d'appel.
Question directrice : ces fichiers lancent ffmpeg/ffprobe/ExifTool avec des chemins fournis par l'utilisateur.

1. Annulation avant le lancement, entre le contrôle et l'enregistrement, et pendant la lecture : aucun processus laissé.
2. Annulation d'un processus qui ignore SIGTERM : terminate → kill après 2 s, puis attente réelle de la fin.
3. Binaire = script ou wrapper lançant le vrai outil : après annulation, le petit-fils survit-il (orphelin, écriture en cours) ?
4. Annulation alors que le processus vient de rendre 0 : résultat « annulé » ou « terminé » ?
5. Fermeture de la fenêtre/application pendant un traitement : workers annulés ET attendus (QThread détruit = plantage).
6. Processus qui refuse de mourir (kill inopérant, état D) : run_process rend-il la main en laissant un vivant ?
7. `timeout` : blocage → `timed_out`, processus tué, pas d'attente infinie ; cas `timeout=0`.
8. `stall_timeout` sans `activity` : jamais d'arrêt ; une sonde `activity` qui lève ne doit pas tuer le traitement.
9. stdout très volumineux : mémoire non bornée (seul stderr est plafonné à 64 Kio) ; pipe plein, lecteur arrêté.
10. Sortie non UTF-8, ligne sans fin, stderr volumineux : décodage `replace`, aucun interblocage.
11. Ordre embarqué > personnalisé > PATH ; un embarqué présent mais invalide est-il préféré au système ?
12. Chemin personnalisé relatif : résolu depuis le dossier courant (donc variable) ou refusé comme les entrées PATH relatives ?
13. Chemin personnalisé : dossier, fichier non exécutable, lien mort, valeur vide → ignoré + message + repli.
14. Faux binaire nommé ffmpeg (texte, mauvaise architecture) : `-version` échoue, version vide ; est-il retenu quand même ?
15. Espaces, accents, `#`, guillemets dans le chemin : liste d'arguments (jamais de shell) ; cas de l'argfile ExifTool.
16. PATH vide ou relatif, binaire absent : `require()` échoue en français, sans blocage ; PATH assaini.
17. Sonde de version bloquée (stdin hérité, 10 s par binaire) : `refresh()` au démarrage gèle-t-il l'UI jusqu'à 30 s ?
18. Cache par nom, préférences ignorées : un résultat `missing` ou personnalisé reste-t-il figé jusqu'à `refresh()` ?
19. Embarqué sans bit d'exécution (chmod), version illisible, binaire hors `MIN_VERSIONS` : `outdated` non inventé.
20. Arrêt de l'application pendant la sonde de version d'un binaire bloqué.

---

## Étape 2 — marquage après lecture de `tests/core/` (7 novembre 2026)

« Couvert » = un test existant exerce le cas. Les défauts prouvés portent un ID CORE-n (voir `CONSTATS.md`).

| # | Cas | Tests existants | Verdict |
|---|---|---|---|
| 1 | Annulation avant / pendant la lecture | `test_run_process_already_cancelled_does_not_launch`, `test_cancel_during_run_process` | couvert |
| 1 | Annulation entre le contrôle et l'enregistrement | aucun | non couvert — non vérifié (course non provoquée) |
| 2 | SIGTERM ignoré → kill après 2 s | aucun | non couvert — vérifié par sonde (2,0 s), conforme, pas de test ajouté |
| 3 | Petit-fils orphelin après annulation | aucun | non couvert — **CORE-1** (corrigé, 2 tests) |
| 4 | Annulation alors que le processus rend 0 | aucun | non couvert — non vérifié (course) |
| 5 | Fermeture de fenêtre pendant un traitement | aucun | non couvert — **CORE-5** (corrigé, 1 test) |
| 6 | Processus qui refuse de mourir | aucun | non couvert — non vérifié (état D non provoquable ici) |
| 7 | `timeout` / `timeout=0` | `test_run_process_timeout_on_silent_process` | couvert (`timeout=0` non testé) |
| 8 | `stall_timeout`, sonde `activity` en échec | `test_run_process_stall_detection_kills_frozen_process`, `test_run_process_progress_prevents_stall` | couvert (sonde en échec : lecture de code) |
| 9 | stdout très volumineux | `test_run_process_can_skip_stdout_capture` | couvert — **CORE-4** (corrigé, 1 test ; capture coupée sur les encodages) |
| 10 | Non-UTF-8, ligne sans fin, stderr volumineux | stderr seulement | partiel — non vérifié (lecture de code : `errors="replace"`, `readline`) |
| 11 | Embarqué invalide préféré au système | `test_bundled_binary_without_readable_version_falls_back` | couvert — **CORE-3** (corrigé, 2 tests) |
| 12 | Chemin personnalisé relatif | aucun | non couvert — **CORE-2** (corrigé, 1 test) |
| 13 | Chemin personnalisé invalide | `test_invalid_custom_path_ignored` (chemin absent) | partiel — dossier / lien mort / valeur vide : lecture de code (`_is_executable` exige `is_file`) |
| 14 | Faux binaire nommé ffmpeg | `test_bundled_binary_without_readable_version_falls_back` | couvert — **CORE-3** (corrigé, 2 tests) |
| 15 | Espaces, accents, `#`, guillemets | aucun | non couvert — lecture de code (liste d'arguments, jamais de shell) |
| 16 | PATH vide/relatif, binaire absent | `test_missing`, `test_require` (`which` simulé) | partiel — `_system_path` vérifié par sonde (`.:/usr/bin::relatif:/bin` → `/usr/bin:/bin`) |
| 17 | Sonde de version bloquée | aucun | non couvert — **CORE-6** (corrigé, 2 tests) |
| 18 | Cache par nom, préférences ignorées | `test_cache_and_refresh` | couvert (comportement documenté, pas de défaut prouvé) |
| 19 | Embarqué sans bit d'exécution, versions | `test_bundled_not_executable_is_fixed_or_skipped`, `test_binary_versions_outdated` | couvert (version illisible : voir **CORE-3**) |
| 20 | Arrêt pendant la sonde de version | aucun | non couvert — non vérifié (protocole manuel) |
