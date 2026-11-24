# Manuel utilisateur d'Azimut

Azimut est une suite d'applications de bureau pour traiter des fichiers photo, vidéo et audio.
Les traitements lourds s'exécutent en arrière-plan : l'interface reste réactive et les opérations
longues peuvent être annulées proprement.

Ce manuel décrit le lancement, les modules et les trois modes de suppression de Dedup.
L'installation, la construction et la compatibilité sont traitées dans le
[README](README.md), [BUILD.md](BUILD.md) et [COMPATIBILITE.md](COMPATIBILITE.md).

## Sommaire

- [Lancer Azimut](#lancer-azimut)
- [Les modules](#les-modules)
- [Dedup : lecteur intégré, quarantaine, corbeille et suppression définitive](#dedup--lecteur-intégré-quarantaine-corbeille-et-suppression-définitive)
- [Où aller plus loin](#où-aller-plus-loin)

## Lancer Azimut

`python main.py` ouvre **Azimut**, le lanceur. Sa première page présente une carte par module avec
l'état des binaires requis et un bouton « Ouvrir » ; on peut aussi glisser-déposer des fichiers ou
un dossier sur une carte. La page « Journaux et historique » rassemble l'historique des
traitements, le journal de l'application et l'état des binaires.

Un module peut être lancé directement :

```bash
python main.py convert photo.heic          # Azimut transmet les chemins au module
python -m azimut.modules.tags              # un module seul
python main.py --list                      # modules disponibles
python main.py --version
python main.py --selftest [rapport]        # vérifie l'installation
```

## Les modules

| Module | Rôle |
|---|---|
| **Takeout** (`takeout`) | Réinjecte dates et positions GPS d'un export Google Photos (JSON compagnons) dans les photos et vidéos, avec annulation. |
| **Dedup** (`dedup`) | Détection et suppression des doublons : hachage du contenu, similarité visuelle, Live Photos (photo + courte vidéo), fichiers modifiés entre deux dossiers, comparaison par prévisualisation avec **lecteur audio et vidéo intégré** (lecture, déplacement, volume, sans ouvrir d'application externe). |
| **Convert** (`convert`) | Conversion audio, vidéo et images via FFmpeg, par lots. Format de sortie au choix par famille (AVIF, WebP, H.264, Opus, FLAC…), qualité réglée à la main par curseurs (CRF, preset, débit). Chaque fichier produit est contrôlé (une sortie vide n'est jamais comptée comme réussie) ; option pour ne pas recopier les JSON Takeout déjà réinjectés. Le parallélisme est réglable par famille (images, audios, vidéos, copies) ou automatique à partir des cœurs et du réglage Threads ; en automatique, deux vidéos avancent ensemble si le codec choisi est accéléré par le GPU (NVENC, QSV, VA-API), une seule avec un encodeur processeur. À qualité visuelle égale, les profils GPU sont plus rapides et laissent le processeur libre, mais produisent souvent des fichiers plus volumineux que les profils CPU, plus lents et plus compacts. Les profils AV1 matériels (NVENC, QSV, VA-API) n'apparaissent que si FFmpeg expose l'encodeur correspondant. Au démarrage, chaque encodeur GPU listé par FFmpeg est testé par un petit encodage à vide : si le pilote ou le GPU ne répond pas, le profil est marqué indisponible avant le lancement au lieu d'échouer en cours de conversion. La progression est globale (0 à 100 % une seule fois pour tout le lot, les copies puis les images, les audios et les vidéos) : chaque fichier compte pour la même part, et la barre est découpée en segments colorés proportionnels au nombre d'images, d'audios, de vidéos et de copies à traiter. Le libellé indique l'étape en cours avec un temps restant estimé tenant compte de la taille et du type des fichiers, pour que les longues vidéos ne soient pas comptées comme des copies instantanées. Après un arrêt forcé ou un plantage, « Vérifier la sortie… » recherche les fichiers incomplets ou corrompus du dossier de sortie (temporaires d'encodage, tailles anormales, médias illisibles) et propose de les supprimer pour qu'une nouvelle conversion les recrée ; les originaux ne sont jamais touchés. |
| **Rename** (`rename`) | Inscrit la date de prise de vue (lue dans les métadonnées) dans le nom des photos, vidéos et fichiers audio, avec confirmation détaillée et annulation. |
| **Scope** (`scope`) | Compare deux dossiers (arborescence et nombre de fichiers par dossier), en lecture seule : vérifie qu'une copie ou une conversion est complète. |
| **Tags** (`tags`) | Lecture, modification et suppression des métadonnées (EXIF, IPTC, XMP) via ExifTool. |

## Dedup : lecteur intégré, quarantaine, corbeille et suppression définitive

**Lecteur intégré.** Sélectionner un fichier audio ou vidéo (ou deux, pour les comparer) ouvre un lecteur dans le
panneau de droite : lecture/pause, curseur de position (un clic saute à l'endroit voulu), volume et sourdine.
Le double-clic lance ou met en pause la lecture au lieu d'ouvrir une application. Un seul des deux aperçus joue à la
fois. Les fichiers sont lâchés avant toute suppression, déplacement ou fermeture de la fenêtre. Si un codec n'est
pas lisible, le bouton « Ouvrir dans le lecteur du système » reste disponible. Le lecteur demande le module
PySide6-Addons (installé avec Azimut) ; sans lui, l'aperçu image habituel est utilisé.

Le mode se choisit dans « Mode de suppression ». Les trois modes retirent de leur emplacement les
fichiers marqués « Supprimer », mais ne laissent pas les mêmes possibilités de retour :

| Mode | Sort du fichier | Retour possible | Contrôle avant retrait |
|---|---|---|---|
| **Quarantaine** | Dossier `Quarantaine_<date>` | Oui, via le `journal.csv` | Taille, date et gardien |
| **Corbeille** | Corbeille du système | Oui, tant qu'elle n'est pas vidée | Taille, date et gardien |
| **Définitive** | Retiré du disque | Non | Taille, date et BLAKE2b complet |

La corbeille système n'est proposée que si le module `send2trash` est installé.

**Quarantaine.** Les fichiers sont déplacés dans un dossier `Quarantaine_<horodatage>` créé à
l'emplacement indiqué (par défaut, le dossier parent de A), en conservant leur côté (A ou B) et
leur chemin relatif. Le `journal.csv` est écrit et synchronisé avant chaque déplacement ;
`journal.meta.json` enregistre les dossiers analysés. « Annuler une application… » demande ce
journal puis remet les fichiers à leur place, sans jamais écraser un fichier existant. La
restauration refuse une source hors du dossier de quarantaine, une destination hors des dossiers
analysés, un emplacement d'exécution automatique ou une taille différente de celle du journal. Un
journal importé ou antérieur, sans liste de dossiers, déclenche un avertissement et une
confirmation explicite. Un fichier dont l'emplacement d'origine est déjà occupé reste en
quarantaine et est signalé comme erreur.

**Corbeille système.** Les fichiers partent dans la corbeille du système, sans journal dans
l'application. On peut les récupérer tant que la corbeille n'a pas été vidée, avec le
gestionnaire de fichiers du système ; Azimut ne propose pas cette restauration. Si `send2trash`
est absent, ce mode n'apparaît pas.

**Suppression définitive.** Aucune copie ni journal n'est conservé : le fichier est retiré du
disque et l'opération ne peut pas être annulée. Deux confirmations sont demandées, avec « Non »
par défaut. Avant chaque retrait, pour les doublons exacts, Azimut relit l'empreinte BLAKE2b
complète du fichier à supprimer et celle de l'exemplaire conservé, et les compare aux empreintes
de l'analyse. L'exemplaire est revérifié avant chaque suppression d'un même groupe. Si un contenu
a été réécrit avec la même taille et la même date, si l'exemplaire a disparu ou ne correspond
plus, le fichier est ignoré et le lot continue ; le compte rendu affiche par exemple
« Ignoré (contenu modifié depuis l'analyse) » ou « Ignoré (aucun exemplaire conservé valide) ».

La revérification relit entièrement les fichiers : sur un gros groupe, elle peut prendre plusieurs
minutes. Il n'existe volontairement pas d'option pour la désactiver, car elle est la seule
protection contre la perte d'un contenu modifié depuis l'analyse. Elle a lieu juste avant le
retrait ; une modification faite par un autre programme pendant l'opération peut toutefois
échapper au contrôle. Pour les images « similaires », le contenu n'est pas identique : le
contrôle reste la taille, la date et la distance perceptuelle (dHash), et la suppression n'est
appliquée qu'après validation.

Dans tous les modes, un fichier d'un dossier protégé n'est jamais proposé ; un groupe sans
exemplaire conservé est ignoré ; une erreur sur un fichier est signalée sans interrompre les
autres. L'application peut être annulée entre deux fichiers : les fichiers déjà traités le
restent, les suivants ne sont pas touchés. L'option « Dossiers vides » supprime les dossiers
devenus vides après l'opération. En cas de doute, préférez la quarantaine ou la corbeille.

## Où aller plus loin

- [README](README.md) : présentation, installation de développement, lancement et tests.
- [COMPATIBILITE.md](COMPATIBILITE.md) : systèmes supportés et limites connues.
- [BUILD.md](BUILD.md) : construction des paquets.
- [RELEASING.md](RELEASING.md) : publication d'une version.
