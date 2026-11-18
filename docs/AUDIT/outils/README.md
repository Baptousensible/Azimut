# Outils de remplacement utilisés pendant l'audit (à ne pas confondre avec pytest / PySide6)

L'environnement d'audit n'avait ni réseau, ni `pytest`, ni `PySide6`, ni `ruff`, ni ExifTool. Pour obtenir malgré
tout des **preuves exécutées** sur tout ce qui ne dépend pas de Qt, deux petits outils ont été écrits :

- `minipytest.py` : exécuteur minimal compatible avec les fonctionnalités de pytest réellement utilisées par les
  tests (fixtures `tmp_path`, `tmp_path_factory`, `monkeypatch`, `capsys`, `caplog`, `parametrize`, `skipif`,
  `importorskip`, `raises`, `approx`). Il **ignore `tests/conftest.py`**.
- `stub/PySide6/` : faux `PySide6.QtCore` qui ne fournit que `QT_TRANSLATE_NOOP` et quelques classes vides, assez
  pour importer les paquets de moteurs (`azimut/modules/<id>/__init__.py`) et `azimut/core/i18n.py`.

Usage (depuis la racine du dépôt) :

```bash
python docs/AUDIT/outils/minipytest.py tests                 # toute la suite, ce qui est exécutable sans Qt
python docs/AUDIT/outils/minipytest.py -k argfile tests/tags # un sous-ensemble
```

Résultats : `PASS` réussi · `FAIL` échec · `SKIP` ignoré par le test lui-même · `NOFIX` fixture Qt (`qapp`…)
indisponible · `QT` fichier de test non importable sans le vrai Qt. **Un `PASS` ici n'équivaut pas à un `PASS`
pytest + Qt** ; ces outils ne remplacent pas la matrice du § 8 des consignes, ils la préparent.
