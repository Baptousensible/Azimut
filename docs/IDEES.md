# Idées hors périmètre (non implémentées)

- Fichier de verrouillage des binaires tiers (URL exactes + SHA-256) partagé par `fetch_binaries.py` et la CI.
- Repli automatique d'encodeur AV1 (`libsvtav1` → `libaom-av1`) selon les dimensions et en cas d'échec.
- Test « aucun littéral visible hors `tr()` » exécuté en CI (AST) pour empêcher toute régression d'i18n.
- Ajouter Python 3.11 à la matrice de tests ; `pytest-randomly` dans `requirements-dev.txt`.
- Option « re-hacher l'exemplaire conservé avant suppression définitive » dans Dedup.
- Distribution Flatpak/deb/rpm (AZ-C05/C17) : à planifier seulement si la demande existe.
