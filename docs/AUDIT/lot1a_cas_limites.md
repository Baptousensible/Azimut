# Lot 1a — cas limites Takeout (liste écrite avant lecture des tests, puis annotée)

Périmètre : engine.py, workers.py. Question : un mode peut-il altérer ou perdre l'original ? (écriture en place.)
## Appariement média ↔ JSON
1. Doublons Google `(1)` : couvert par `test_duplicates_do_not_cross` et `test_matching`.
2. Variante `-edited` : couvert par `test_matching` ; si le JSON de base et celui de l'édition coexistent, le base gagne (script).
3. JSON tronqué : couvert par `test_truncated_long_title` ; troncature dans l'extension ou le suffixe non couverte.
4. Un JSON tronqué pour deux médias : LOG-008, non corrigé ; titre complet préfixe d'un autre média : non couvert.
5. Même nom dans deux dossiers : vérifié par script (OK) ; média/JSON séparés et racine incluse dans une racine : non vérifié.
## Entrées dégradées
6. Deux JSON pour un média : non couvert ; script : refus sûr (« Sans JSON », 2 orphelins), aucune écriture.
7. JSON absent : `test_analyze_states` ; illisible : LOG-010 ; imbrication extrême : RecursionError sous Python 3.12 → TAKEOUT-003 (corrigé).
8. Date 0/Infinity : `test_parse_sidecar...` et `test_infinite_timestamp...` ; < 1971 : LOG-007 (corrigé) ; future aberrante : non vérifié.
9. GPS absent/0,0/NaN/hors bornes : `test_parse_sidecar...` ; GPS partiel (latitude seule) : conservé sans note (script).
10. Caractères spéciaux (`#`, `%`, emoji) : vérifiés par script (OK) ; NFC/NFD, chemins > 4096 : non vérifiés.
## Écriture et conservation
11. Fichier 0444 : non couvert, script : réécrit → TAKEOUT-002 (corrigé). Dossier 0555 : erreur ExifTool propre, fichier intact (script).
12. WebM/Matroska : `test_plan_matroska_only_mtime` et e2e WebM (mtime seule).
13. Dates existantes sans overwrite : `test_plan_keeps_existing_unless_overwrite` (DateTimeOriginal seul) ; CreateDate/ModifyDate seuls écrasés → TAKEOUT-001 (corrigé).
14. `overwrite` : e2e partiel (nouveaux tags) ; valeurs écrasées puis rétablies par l'annulation : vérifié par script (OK).
15. TOCTOU (fichier remplacé/renommé entre analyse et application) : non vérifié.
16. Échec d'ExifTool : dossier 0555 vérifié ; disque plein, coupure : non vérifiés.
17. Annulation : non couverte par les tests ; script : undo partiel enregistré, une fois rejoué restaure le fichier (OK).
18. Volume : 5 000 fichiers analysés en 0,3 s (script) ; application = un ExifTool par fichier (lecture de code), non mesurée.
19. JSON/média confondus : extensions disjointes ; après apply+undo le fichier n'est pas octet-identique, mais l'atime est restauré (TAKEOUT-004) et une copie binaire optionnelle est proposée (TAKEOUT-005).
