# Icônes animées

Ce dossier contient le second thème d'icônes d'Azimut, proposé dans **Préférences →
Apparence → Icônes**. La plupart des pictogrammes et leurs mouvements viennent de
**[lucide-animated](https://github.com/pqoqubbw/icons)** (MIT, voir
`LICENSE-lucide-animated.txt`), portés de React/Motion vers Qt ; les douze pictogrammes
absents du projet sont dessinés dans le même style pour compléter la couverture.

Le dossier entier (SVG, ce README et la licence) est déclaré dans `pyproject.toml` et
embarqué par PyInstaller : il fait partie du wheel, de la sdist et des paquets de bureau.

Les fichiers gardent les noms du thème Papicons pour que le reste du code n'ait pas à
connaître la bibliothèque : `azimut/ui/icons.py` cherche d'abord ici quand le thème
« Lucide Animées » est actif, puis retombe sur `../icons/` si un nom n'existe pas. Chaque tracé
porte un identifiant (`p1`, `p2`…) utilisé par `azimut/ui/icon_animation.py` pour animer
les éléments séparément. Les icônes dessinent des traits (`currentColor`) au lieu de
formes pleines : `icons.py` remplace simplement cette couleur avant le rendu.

| Nom Azimut | Pictogramme lucide-animated | Mouvement au survol |
|---|---|---|
| `archive` | `archive` | le couvercle se soulève |
| `arrow-left` | `arrow-left` | la pointe recule, le trait se raccourcit |
| `arrow-right` | `arrow-right` | la pointe avance, le trait se raccourcit |
| `arrow-up` | `arrow-up` | la pointe monte |
| `bookmark` | `bookmark` | le ruban s'étire |
| `check` | `check` | la coche apparaît d'un coup |
| `chevron-down` | `chevron-down` | le chevron descend |
| `chevron-right` | `chevron-right` | le chevron avance |
| `chevron-up` | `chevron-up` | le chevron monte |
| `cross` | `x` | la croix se dessine |
| `gears` | `settings` | l'engrenage fait un demi-tour |
| `globe` | `earth` | le méridien central apparaît |
| `grid` | `layout-grid` | les quatre cases échangent leur place |
| `lock` | `lock` | le cadenas oscille |
| `map-pin` | `map-pin` | l'épingle rebondit |
| `pause` | `pause` | les deux barres ondulent |
| `pie` | `chart-pie` | la part se détache |
| `play` | `play` | le triangle pivote |
| `search` | `search` | la loupe balaie |
| `sparkles` | `sparkles` | l'étincelle gonfle, les éclats clignotent |
| `trash` | `delete` | le couvercle se soulève |

Douze pictogrammes du thème ne figurent pas dans lucide-animated : `alert-triangle`,
`folder`, `gallery`, `ghost`, `info`, `list`, `pen-alt`, `swap-horizontal`, `tasks`,
`volume`, `volume-cross` et `volume-half`. Ils sont dessinés dans le même style (trait
de 2 px, viewBox 24×24) pour garder un jeu cohérent ; les trois volumes reprennent le
haut-parleur et les ondes du composant `volume`. Ils reçoivent un mouvement discret ou
leur propre piste dans `icon_animation.py`.

L'option **Réduire les animations** désactive tous ces mouvements, sans changer le dessin.
