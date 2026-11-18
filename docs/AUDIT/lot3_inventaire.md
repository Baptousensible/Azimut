# Lot 3a — inventaire SEC-002 : données externes dans les widgets à texte automatique

Branche `feature/audit-0.2.0-suite`, HEAD `3d729a4` (l'arbre de travail était partagé avec d'autres lots :
`azimut/modules/tags/engine.py`, `azimut/modules/tags/workers.py`, `azimut/modules/takeout/workers.py`,
`tests/convert/test_restoration.py`, `tests/takeout/test_takeout.py` et `docs/AUDIT/CONSTATS.md` étaient modifiés
hors de ce lot ; ils n'ont pas été touchés).

**Nature de la preuve : lecture de code + grep.** Aucun comportement Qt n'a été exécuté dans ce lot (pas de test,
pas de capture) : la présence du défaut d'affichage reste donc **non vérifiée par exécution**, conformément au
statut déjà inscrit pour SEC-002 dans `CONSTATS.md`. Aucun fichier de code n'a été modifié.

## 1. Méthode

Commandes exécutées depuis `azimut/` :

```bash
grep -rnE '\.setText\(|setHtml\(|QLabel\(|QMessageBox\.|setToolTip\(|setInformativeText\(|setDetailedText\(|setWindowTitle\(|QToolTip\.showText|setStatusTip\(|setWhatsThis\(' --include='*.py' .
# 299 occurrences brutes, tous fichiers confondus

grep -rn 'setTextFormat' --include='*.py' .
# 3x PlainText, 3x RichText (tous avec html.escape)
```

Critères de sélection d'une ligne du tableau :

1. le widget interprète du texte enrichi en mode automatique : `QLabel` (y compris sous-classes et labels internes
   de dialogues), `QMessageBox` (`setText`, `setInformativeText`), infobulle `setToolTip` ;
2. la donnée affichée n'est pas un littéral du code : nom de fichier, chemin, nom ou valeur de tag, sortie d'un
   binaire (ffmpeg / ffprobe / ExifTool), message d'exception pouvant contenir un chemin.

`QMessageBox.setDetailedText` n'est utilisé nulle part dans `azimut/` (0 occurrence).
`setInformativeText` n'a qu'une occurrence (`launcher/atlas_window.py:927`) et son texte est statique.

Rappel Qt (lecture de code / documentation, non exécuté ici) : `QLabel` et `QMessageBox` choisissent
`Qt::TextFormat.AutoText` par défaut ; une chaîne qui « ressemble » à du HTML (`<b>x</b>.jpg`) est rendue comme
telle. Les infobulles suivent la même détection. Les vues d'items (`QTableView`, `QTreeWidget`), `QLineEdit`,
`QPlainTextEdit` et `QPushButton.setText` n'interprètent pas le HTML.

## 2. Résultat

**30 points d'affichage** de données externes passent par un widget à texte automatique. **Aucun** ne force
`Qt.TextFormat.PlainText`. Les **3 seuls** `setTextFormat(PlainText)` du dépôt concernent des textes statiques
(voir § 4). Le décompte de 94 appels à texte non littéral cité dans `CONSTATS.md` reste le décompte large des
appels ; l'inventaire ci-dessous ne retient que ceux qui atteignent réellement un widget interprétant le HTML.

## 3. Tableau

| fichier:ligne | widget | donnée affichée | déjà PlainText ? |
|---|---|---|---|
| `azimut/ui/widgets.py:411` | `QLabel` (`DropZone._label`) | nom du dossier analysé, via `modules/rename/window.py:219` (`folder.name`) | non |
| `azimut/ui/widgets.py:605` | `QLabel` (`ProgressPanel.file_label`) | nom du fichier en cours, via `worker.progress` (`convert/workers.py:223,250`, `dedup/engine.py:119`, `rename/workers.py:114,167`, `tags/workers.py:129,168,293`, `takeout/workers.py:69`) | non |
| `azimut/ui/widgets.py:606` | infobulle (`file_label.setToolTip`) | même nom de fichier | non |
| `azimut/ui/widgets.py:1431` | `QLabel` (`VideoPreview.view`) | message d'erreur de `QMediaPlayer` (peut contenir l'URL / le chemin du média) | non |
| `azimut/ui/widgets.py:1496` | `QMessageBox.warning` | nom du fichier sans application associée (`self._target.name`) | non |
| `azimut/ui/widgets.py:1561` | `QLabel` (`ImagePreview.label`) | texte de repli des aperçus, avec `path.name` (ffmpeg, PDF, audio) | non |
| `azimut/ui/widgets.py:1571` | `QLabel` (`ImagePreview.label`, `_show_snippet`) | extrait du contenu texte du fichier ouvert | non |
| `azimut/ui/module_window.py:422` | infobulle (`StatusChip.setToolTip`) | version renvoyée par le binaire et chemin résolu (`info.version`, `info.path`) | non |
| `azimut/launcher/atlas_window.py:119` | `QLabel` (`error_label`) | message d'exception d'ouverture d'un module (peut contenir un chemin) | non |
| `azimut/launcher/atlas_window.py:120` | infobulle (`error_label.setToolTip`) | même message d'exception | non |
| `azimut/launcher/atlas_window.py:124` | infobulle (`StatusChip.setToolTip`) | même message d'exception | non |
| `azimut/launcher/atlas_window.py:138` | infobulle (`StatusChip.setToolTip`) | version et chemin du binaire détecté | non |
| `azimut/launcher/atlas_window.py:814` | `QMessageBox.warning` | message d'exception d'ouverture (`type(exc).__name__ : exc`) | non |
| `azimut/ui/preferences_dialog.py:221` | `QLabel` (`StatusChip.set_state`) | version renvoyée par le binaire (`BinaryInfo.version`) | non |
| `azimut/ui/preferences_dialog.py:222` | infobulle (`StatusChip.setToolTip`) | chemin du binaire (`str(info.path)`) | non |
| `azimut/ui/preferences_dialog.py:238` | `QLabel` (`StatusChip.set_state`) | version du binaire saisi par l'utilisateur (`probe_version`) | non |
| `azimut/modules/scope/window.py:278` | `QMessageBox.critical` | message d'`OSError` lors de l'export (contient le chemin choisi) | non |
| `azimut/modules/scope/treeview.py:561` | infobulle (`_ElidedLabel.setToolTip`) | chemin complet du dossier comparé | non |
| `azimut/modules/scope/treeview.py:575` | `QLabel` (`_ElidedLabel`, `super().setText`) | nom du dossier comparé (élidé) | non |
| `azimut/modules/dedup/window.py:697` | `QLabel` (`caption_a`) | nom, chemin, empreinte, dimensions du fichier sélectionné (`_describe`) | non |
| `azimut/modules/dedup/window.py:704` | `QLabel` (`caption_a`) | idem, pour le premier fichier de la comparaison | non |
| `azimut/modules/dedup/window.py:705` | `QLabel` (`caption_b`) | idem, pour le second fichier de la comparaison | non |
| `azimut/modules/dedup/window.py:792` | `QMessageBox.setText` (`_ask`) | dossier de quarantaine choisi (chemin) dans la confirmation d'application | non |
| `azimut/modules/rename/window.py:308` | `QLabel` (`lbl_info`) | ancien et nouveau nom de fichier (`r.old_name`, `r.new_name`) | non |
| `azimut/modules/rename/window.py:465` | `QMessageBox.critical` | message d'`OSError` lors de l'export du plan (contient le chemin) | non |
| `azimut/modules/tags/window.py:355` | `QLabel` (`_meta_title`) | nom du fichier dont les tags sont affichés (`ft.path.name`) | non |
| `azimut/modules/tags/window.py:374` | `QMessageBox.warning` | refus d'édition : `write_refusal` préfixe le nom du fichier WebM / Matroska | non |
| `azimut/modules/tags/window.py:394` | `QMessageBox.warning` | refus de suppression : `write_refusal` (nom du fichier) | non |
| `azimut/modules/tags/window.py:480` | `QMessageBox.warning` | noms de tags invalides issus des métadonnées (`"\n".join(problems)`) | non |
| `azimut/modules/tags/window.py:484` | `QMessageBox.warning` | refus d'application : `write_refusal` (nom du fichier) | non |

## 4. Les trois `setTextFormat(PlainText)` existants

| fichier:ligne | widget | donnée affichée | déjà PlainText ? |
|---|---|---|---|
| `azimut/modules/scope/treeview.py:643` | `QLabel` (`_EmptyState.title`) | message d'état vide (littéral) | oui |
| `azimut/modules/scope/treeview.py:647` | `QLabel` (`_EmptyState.detail`) | message d'état vide, dont le terme de recherche (`self._search_text`) | oui |
| `azimut/ui/module_window.py:383` | `QLabel` (en-tête de panneau latéral) | titre du panneau, littéral fourni par le module | oui |

Les trois `setTextFormat(RichText)` (`ui/module_window.py:165,640,651`) échappent les données avec
`html.escape` : ils ne sont pas des points d'injection. Les en-têtes d'aide construits en HTML
(`ui/module_window.py:636,638,649`) échappent eux aussi `spec.name`, `self.tr(...)` et les raccourcis.

## 5. Points examinés et volontairement écartés

- **Vues d'items** : `DataTable` / `RowTableModel` (`ui/widgets.py`), cellules `QTreeWidgetItem` de
  `modules/dedup/window.py:618,619`, tableaux Scope/Rename/Tags/Takeout/Dashboard. Qt y dessine le texte
  via le delegate, sans interprétation HTML (lecture de code). Les chemins et noms de fichiers y sont
  donc hors du défaut SEC-002.
- **Champs de saisie** : `PathField.edit.setText` (`ui/widgets.py:314`), `edit_prefix.setText`
  (`modules/rename/window.py:312`), `search.setText` : `QLineEdit`, texte brut.
- **Journaux** : `LogPanel` et `QPlainTextEdit` (`ui/widgets.py`, `launcher/dashboard.py`, confirmation de
  renommage) : texte brut. Les messages contenant des chemins (`dedup._say`, `rename._confirm`, journal
  d'application) n'y sont pas interprétés.
- **Boutons** : `QPushButton.setText` (`module_window.py:233`, `atlas_window.py:725`,
  `ui/widgets.py:1475-1481`) : texte brut.
- **Libellés à données internes uniquement** : compteurs et totaux (`ProgressPanel.count_label`,
  `scope/window.py:204,244`, `dedup/window.py:633`, `rename/window.py:289`, `takeout/window.py:181`,
  `dashboard.py:149,200`), textes d'aide statiques (`convert/window.py:90,144`, `codec_tree.py`),
  noms de modules statiques (`atlas_window.py:925`). `setInformativeText` (`atlas_window.py:927`) est
  également statique.
- **Descriptions de codecs** : les infobulles de `codec_tree.py:410,413` viennent de `Profile.description`
  (littéraux du dépôt) ; le motif d'indisponibilité `_unavailable` (sortie ffmpeg) apparaît dans le texte
  d'un item de `QComboBox`, non interprété comme HTML.

### Points voisins, hors des API listées (à traiter avec SEC-002 si le lot 3b le décide)

- `azimut/modules/tags/window.py:377` : `QInputDialog.getText(..., f"{entry.full_name} :", ...)` place un nom de
  tag issu d'ExifTool dans le `QLabel` interne du dialogue ; ce label n'est pas accessible par `setTextFormat`
  sans reconstruire le dialogue.
- `azimut/modules/scope/treeview.py:944` : le détail de l'état vide affiche `self._search_text` (terme saisi par
  l'utilisateur) ; le label est déjà en `PlainText`.
- `azimut/ui/module_window.py:384` : infobulle du titre de panneau latéral ; donnée littérale, label déjà
  `PlainText`.

## 6. Limites

- Inventaire **établi par grep et lecture de code** ; le rendu HTML par Qt n'a pas été exécuté (pas de test Qt,
  pas de capture). Les 30 lignes sont donc « à corriger » selon SEC-002, pas « défaut reproduit ».
- Le grep couvre les appels explicites listés dans la consigne ; les labels construits indirectement par des
  dialogues standard (cas `QInputDialog` ci-dessus) ne sont pas tous atteignables par `setTextFormat`.
- L'arbre de travail contient des modifications d'autres lots ; l'inventaire décrit l'état observé, pas un
  commit isolé.
- Aucun test n'a été ajouté ni exécuté : le lot 3a est un inventaire, sans modification de code.
