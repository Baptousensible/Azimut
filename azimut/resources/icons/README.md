# Icônes

Ces icônes proviennent de **Papicons** (https://github.com/PapillonApp/Papicons), dessinées par Tom Things
pour Papillon, sous licence MIT (voir `LICENSE-Papicons.txt`).

Elles sont monochromes (`fill="black"`) : `azimut/ui/icons.py` remplace la couleur à l'exécution, ce qui
permet de suivre le thème clair/sombre. Pour en ajouter une : copier le `.svg` ici, puis s'y référer par
son nom de fichier sans extension (par ex. `icon="map-pin"` dans le `ModuleSpec` d'un module).

Pourquoi des SVG plutôt que des emojis ? Un emoji dépend d'une police installée sur le système ; elle
manque sur certaines sessions (Fedora/KDE par exemple) et l'icône disparaît. Un SVG embarqué s'affiche
partout de la même façon.
