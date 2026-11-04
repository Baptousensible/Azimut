# Icônes

Deux jeux de pictogrammes, tous deux sous licence MIT, au style très proche (glyphes pleins, angles
arrondis, grille 24 px) :

| Dossier | Source | Licence |
|---|---|---|
| `icons/` (ce dossier) | **Papicons** (https://github.com/PapillonApp/Papicons), dessinées par Tom Things pour Papillon | `LICENSE-Papicons.txt` |
| `icons/phosphor/` | **Phosphor Icons**, variante *Fill* (https://github.com/phosphor-icons/core) | `phosphor/LICENSE-Phosphor.txt` |

Papicons est le jeu principal, mais il ne compte qu'une centaine de pictogrammes, pensés pour une
application scolaire : il n'a ni vidéo, ni musique, ni copie, ni étiquette, ni dossier multiple, etc.
Phosphor Fill complète ces manques. La recherche (`azimut/ui/icons.py`) regarde d'abord Papicons, puis
`phosphor/` : **on se réfère à une icône par son nom de fichier sans extension**, quel que soit son dossier
(par ex. `icon="map-pin"` pour Papicons, `icon="file-zip"` pour Phosphor).

Les icônes sont monochromes (`fill="black"`) : `azimut/ui/icons.py` remplace la couleur à l'exécution, ce qui
permet de suivre le thème clair/sombre.

## Ajouter une icône Phosphor

1. Copier `assets/fill/<nom>-fill.svg` de Phosphor dans `phosphor/`, le renommer `<nom>.svg`.
2. Remplacer `fill="currentColor"` par `fill="black"` (sinon la recoloration par thème ne s'applique pas).
3. Choisir un nom absent de Papicons (un test le vérifie ; Papicons l'emporterait de toute façon).

Le test `tests/ui/test_icons.py::test_every_icon_name_used_in_the_code_exists` signale tout nom d'icône
écrit en dur dans le code qui n'existerait pas.

Pourquoi des SVG plutôt que des emojis ? Un emoji dépend d'une police installée sur le système ; elle
manque sur certaines sessions (Fedora/KDE par exemple) et l'icône disparaît. Un SVG embarqué s'affiche
partout de la même façon.
