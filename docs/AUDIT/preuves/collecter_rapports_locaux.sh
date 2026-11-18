#!/usr/bin/env bash
# À lancer depuis la racine du dépôt, venv actif, AVEC réseau :
#   bash docs/AUDIT/preuves/collecter_rapports_locaux.sh
# Produit rapports_locaux/*.txt (tronqués à 400 lignes ; version complète en *.full).
# À envoyer à l'instance de Claude du « lot 2 ». Ne pas committer (déjà couvert si vous ajoutez rapports_locaux/ à .gitignore).
set -u
OUT="${1:-rapports_locaux}"
mkdir -p "$OUT"
export QT_QPA_PLATFORM=offscreen

run() {  # run <nom> <commande...>
    local name="$1"; shift
    echo ">>> $name"
    "$@" > "$OUT/$name.full" 2>&1
    local rc=$?
    {
        echo "\$ $*   [code de sortie : $rc]"
        head -n 400 "$OUT/$name.full"
        if [ "$(wc -l < "$OUT/$name.full")" -gt 400 ]; then echo "[... tronqué à 400 lignes, voir $name.full]"; fi
    } > "$OUT/$name.txt"
}
absent() {  # absent <outil> <paquet pip>
    if command -v "$1" >/dev/null 2>&1; then return 1; fi
    echo "(outil absent : $1 — pip install $2)" | tee "$OUT/$1.txt"
    return 0
}

run pytest         python -m pytest -q -rs -p no:cacheprovider
run pytest_werror  python -m pytest -q -W error -p no:cacheprovider
run selftest       python main.py --selftest
absent ruff    ruff    || run ruff    ruff check azimut tests tools packaging --output-format=concise
absent bandit  bandit  || run bandit  bandit -q -r azimut packaging tools
absent vulture vulture || run vulture vulture azimut --min-confidence 80
absent pip-audit pip-audit || run pip_audit pip-audit -r requirements.txt
echo "Terminé : joignez les fichiers $OUT/*.txt à la conversation."
