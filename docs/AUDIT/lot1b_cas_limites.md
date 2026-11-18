# Lot 1b — cas limites de Dedup, écrits AVANT lecture de `tests/dedup/`
Périmètre : `azimut/modules/dedup/{engine,livephoto,model}.py`. Règle de gravité : un faux groupe proposé à la
suppression est une perte de données (P0). « Doublon » = contenu identique ; « similaire » = image proche.

| # | Cas limite | Comportement attendu | Statut (étapes 2-3) |
|---|---|---|---|
| 1 | Même taille, contenu différent, ≤ 128 Kio | empreinte complète : jamais groupés | non couvert par les tests ; vérifié par script (non groupés) |
| 2 | Même taille, différence hors des 3 échantillons (> 128 Kio) | empreinte complète des candidats : jamais groupés | non couvert ; vérifié par script (non groupés) |
| 3 | Fichiers vides (0 octet) | ignorés, jamais proposés | non couvert ; vérifié par script (`empty=2`, aucun groupe) |
| 4 | Lien symbolique vers un fichier du dossier | ignoré ; l'original n'est pas « en double » | non couvert ; vérifié par script (lien absent, original seul) |
| 5 | Deux liens physiques du même inode | un seul fichier : jamais proposés ensemble, gain nul | **DÉFAUT DEDUP-001** ; `test_engine.py` (2 tests) |
| 6 | Fichier modifié pendant l'analyse (taille ou mtime) | écarté à l'application (`check_item`) | couvert par `test_actions.py::test_file_modified_since_scan_is_ignored` et `test_keeper_rewritten_in_place_with_same_size_and_mtime_blocks_delete` ; D4 : re-hachage du gardien avant chaque suppression et de l'exemplaire supprimé, en suppression définitive |
| 7 | Live Photo photo + vidéo courte | jamais séparées par défaut ; vidéo seule jamais supprimée sans option | couvert par `test_live_modified.py` (propositions + application) |
| 8 | Live Photo, durée inconnue (ffprobe absent/illisible) | photo et vidéo gardées | couvert par `test_live_unknown_duration_is_never_deleted_by_proposal` |
| 9 | Deux photos de même radical + une vidéo | aucune des deux photos proposée | jumelage couvert par `test_pair_up_matches_raw_and_converted_names_per_folder` ; proposition vérifiée par script |
| 10 | Égalité parfaite entre exemplaires (stratégie) | gardien déterministe, un seul gardé | non couvert ; lecture de code (`rank_key` total, `min`) |
| 11 | Noms NFC/NFD (même nom, deux normalisations) | un seul fichier si le FS les confond | nom couvert par `test_pair_up_is_unicode_normalization_insensitive` ; alias d'inventaire non vérifiable sur ext4 |
| 12 | Dossier protégé dans un groupe | fichiers protégés jamais supprimés, le reste proposé | couvert par `test_protected_and_keeperless_groups_require_explicit_permission` et `test_plan_includes_keeperless_but_excludes_protected` |
| 13 | Images « similaires » non identiques | suppression seulement après confirmation explicite | proposition couverte par `test_similar_groups_are_star_clusters_around_the_keeper` ; confirmation lue dans `window._on_apply` (`_ask` avant l'application) |
| 14 | Très gros fichier (> 4 Gio) et image géante | hachage en flux ; image illisible ignorée, pas de plantage | image « bombe » vérifiée par script (aucune exception) ; > 4 Gio non testé |
| 15 | Annulation pendant l'analyse | `ScanCancelled`, aucun groupe partiel renvoyé | démarrage couvert par `test_workers_run_logic` ; annulation en cours vérifiée par script (`ScanCancelled`) |
| 16 | Grappe similaire : gardien ≠ ancre (distance > tolérance) | refus à l'application, pas de suppression | non couvert ; refus voisin couvert par `test_similar_check_rejects_keeper_outside_tolerance` |

Conclusion : un seul défaut prouvé (DEDUP-001, cas 5), corrigé et couvert par `tests/dedup/test_engine.py`. Les cas
1-4, 9, 14 et 15 ont été provoqués par script sans défaut ; 11 (alias d'inventaire) reste non vérifiable ici.
