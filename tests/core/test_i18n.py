"""Tests : registre des langues, détection système, repli et catalogues Qt."""
from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QLibraryInfo, QLocale, Qt, QTranslator

from azimut.core import i18n
from azimut.core.prefs import DEFAULTS, Preferences


ATLAS_HINT = "Ouvrez un module, ou déposez-y des fichiers ou des dossiers."
CATALOG_CODES = tuple(entry.code for entry in i18n.LANGUAGES if entry.code != i18n.SOURCE_LANGUAGE)


def _atlas_entries(path: Path) -> dict[tuple[str, str], str]:
    """Traductions d'un catalogue, indexées par (contexte, source)."""
    entries: dict[tuple[str, str], str] = {}
    for context in ET.parse(path).getroot().findall("context"):
        for message in context.findall("message"):
            translation = message.find("translation")
            if translation is not None and translation.get("type") == "vanished":
                continue                                   # texte absent du code : ignoré
            entries[(context.findtext("name") or "", message.findtext("source") or "")] = \
                (message.findtext("translation") or "").strip()
    return entries


def _messages(path: Path) -> set[tuple[str, str]]:
    """(contexte, source) des entrées actives d'un catalogue.

    Les entrées « vanished » (texte retiré du code) sont ignorées par précaution :
    ``tools/update_translations.py`` les purge (``lupdate -no-obsolete``), donc elles ne
    devraient plus exister, mais un catalogue édité à la main pourrait en réintroduire.
    """
    root = ET.parse(path).getroot()
    entries = set()
    for context in root.findall("context"):
        for message in context.findall("message"):
            translation = message.find("translation")
            if translation is not None and translation.get("type") == "vanished":
                continue
            entries.add((context.findtext("name") or "", message.findtext("source") or ""))
    return entries


def _atlas_hint_translation(path: Path) -> str:
    """Traduction de la consigne Azimut dans un catalogue .ts."""
    root = ET.parse(path).getroot()
    translations = [
        msg.findtext("translation") or ""
        for context in root.findall("context") if context.findtext("name") == "AtlasWindow"
        for msg in context.findall("message") if msg.findtext("source") == ATLAS_HINT
    ]
    assert translations and translations[0].strip(), path.name
    return translations[0].strip()


# ── Registre ─────────────────────────────────────────────────────────────
def test_registry_offers_more_than_twenty_languages():
    codes = [entry.code for entry in i18n.LANGUAGES]
    assert len(codes) >= 20
    assert len(set(codes)) == len(codes)
    assert {"fr", "en", "de", "es", "it", "pt", "pt-BR", "zh-Hans"} <= set(codes)
    assert all(entry.native_name and entry.english_name for entry in i18n.LANGUAGES)


def test_language_lookup_accepts_variants():
    assert i18n.language("pt_br").code == "pt-BR"
    assert i18n.language_name("de-AT") == "Deutsch"
    assert i18n.language("xx") is None
    assert i18n.language_name("xx") == "xx"


@pytest.mark.parametrize("tag,expected", [
    ("pt_BR", "pt-BR"),
    ("pt-br", "pt-BR"),
    ("pt-PT", "pt"),
    ("en-US", "en"),
    ("fr_FR.UTF-8", "fr"),
    ("de", "de"),
    ("zh_TW", "zh-Hant"),
    ("zh-Hans-CN", "zh-Hans"),
    ("iw", "he"),
    ("no", "nb"),
    ("  es-ES  ", "es"),
])
def test_normalize_language(tag, expected):
    assert i18n.normalize_language(tag) == expected


def test_normalize_rejects_unknown_and_non_strings():
    assert i18n.normalize_language("xx-YY") == ""
    assert i18n.normalize_language("") == ""
    assert i18n.normalize_language(None) == ""
    assert i18n.normalize_language(42) == ""


@pytest.mark.parametrize("tag", ["in", "ji"])
def test_normalize_never_returns_a_code_outside_the_registry(tag):
    """I18N-002 : « in » (indonésien) et « ji » (yiddish) n'ont pas de catalogue : résultat vide, pas « id » / « yi »."""
    assert i18n.normalize_language(tag) == ""


def test_every_alias_targets_a_language_of_the_registry():
    assert all(target in i18n._BY_CODE for target in i18n._ALIASES.values())


# ── Détection et repli ───────────────────────────────────────────────────
def test_detect_language_takes_first_known_tag():
    assert i18n.detect_language(["xx-YY", "pt_BR", "en-US"]) == "pt-BR"
    assert i18n.detect_language([]) == ""


def test_resolve_language_fallback_chain():
    available = ("en", "de")
    assert i18n.resolve_language("de", ui_languages=["es-ES"], available=available) == "de"
    assert i18n.resolve_language(i18n.AUTO, ui_languages=["de-DE"], available=available) == "de"
    # choix sans catalogue → langue détectée, puis anglais, puis français source
    assert i18n.resolve_language("ja", ui_languages=["es-ES"], available=available) == "en"
    assert i18n.resolve_language(i18n.AUTO, ui_languages=["es-ES"], available=()) == "fr"
    assert i18n.resolve_language("en", ui_languages=[], available=()) == "fr"


def test_catalog_path_follows_qt_naming():
    assert i18n.catalog_path("pt-BR").name == "azimut_pt_BR.qm"
    assert i18n.catalog_path("auto").name == "azimut.qm"


def test_qt_catalog_code_follows_qt_naming():
    """Qt nomme ses catalogues chinois par région : zh_CN / zh_TW, pas zh_Hans / zh_Hant."""
    assert i18n.qt_catalog_code("zh-Hans") == "zh_CN"
    assert i18n.qt_catalog_code("zh-Hant") == "zh_TW"
    assert i18n.qt_catalog_code("pt-BR") == "pt_BR"
    assert i18n.qt_catalog_code("de") == "de"


def test_available_languages_reads_compiled_catalogs(monkeypatch, tmp_path):
    for name in ("azimut_en.qm", "azimut_pt_BR.qm", "azimut_zz.qm", "autre_en.qm"):
        (tmp_path / name).write_bytes(b"")
    monkeypatch.setattr(i18n, "translations_dir", lambda: tmp_path)
    assert i18n.available_languages() == ("en", "pt-BR")
    assert i18n.has_catalog("pt_br")
    assert not i18n.has_catalog("zz")
    assert i18n.is_available("fr")                 # langue source : toujours affichable
    assert not i18n.is_available("zz")


def test_available_languages_without_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(i18n, "translations_dir", lambda: tmp_path / "absente")
    assert i18n.available_languages() == ()


def test_shipped_catalogs_are_present():
    """Chaque langue offerte doit avoir son catalogue compilé, sinon elle resterait sans effet."""
    available = set(i18n.available_languages())
    expected = {entry.code for entry in i18n.LANGUAGES} - {i18n.SOURCE_LANGUAGE}
    assert expected <= available


def test_import_completion_message_is_translated_in_every_catalog():
    """Le message affiché après un import doit être traduit dans les 27 catalogues."""
    for path in sorted(i18n.translations_dir().glob("azimut_*.ts")):
        root = ET.parse(path).getroot()
        translations = [
            msg.findtext("translation") or ""
            for context in root.findall("context") if context.findtext("name") == "ImportProgress"
            for msg in context.findall("message") if msg.findtext("source") == "Importation terminée !"
        ]
        assert translations and translations[0].strip(), path.name


def test_import_completion_translation_is_loaded(qapp):
    i18n.install(qapp, "en")
    try:
        assert QCoreApplication.translate("ImportProgress", "Importation terminée !") == "Import complete!"
    finally:
        i18n.uninstall(qapp)


def test_every_catalog_lists_the_same_messages():
    """Un catalogue périmé (ou régénéré seul) ferait réapparaître le français : tous doivent être en phase."""
    reference = _messages(i18n.catalog_path("en").with_suffix(".ts"))
    for path in sorted(i18n.translations_dir().glob("azimut_*.ts")):
        assert _messages(path) == reference, f"{path.name} n'est pas synchronisé avec le code"


def test_english_catalog_translates_every_message():
    """L'anglais est le repli de toutes les langues : la moindre entrée vide laisserait fuir le français."""
    root = ET.parse(i18n.catalog_path("en").with_suffix(".ts")).getroot()
    missing = []
    for context in root.findall("context"):
        for message in context.findall("message"):
            translation = message.find("translation")
            if translation is None or translation.get("type") == "unfinished" \
                    or not (translation.text or "").strip():
                missing.append(f"{context.findtext('name')} · {message.findtext('source')}")
    assert not missing, "Chaînes sans traduction anglaise :\n" + "\n".join(missing[:20])


def test_every_catalog_is_fully_translated():
    """Une langue livrée doit être traduite à 100 % : la moindre entrée vide laisserait fuir le français."""
    missing = []
    for path in sorted(i18n.translations_dir().glob("azimut_*.ts")):
        root = ET.parse(path).getroot()
        for context in root.findall("context"):
            for message in context.findall("message"):
                translation = message.find("translation")
                if translation is None or translation.get("type") == "unfinished" \
                        or not (translation.text or "").strip():
                    missing.append(f"{path.name} · {context.findtext('name')} · "
                                   f"{message.findtext('source')}")
    assert not missing, "Entrées non traduites :\n" + "\n".join(missing[:20])


# ── Sens de lecture ──────────────────────────────────────────────────────
def test_is_rtl_follows_the_registry():
    assert i18n.is_rtl("ar") and i18n.is_rtl("he") and i18n.is_rtl("ar-SA")
    assert not i18n.is_rtl("fr") and not i18n.is_rtl("ja") and not i18n.is_rtl("")


@pytest.mark.parametrize("code", sorted(i18n.RTL_LANGUAGES))
def test_rtl_languages_switch_the_layout_direction(qapp, code):
    """Arabe et hébreu retournent toute l'interface ; une langue LTR la remet dans le sens courant."""
    i18n.install(qapp, code)
    try:
        assert i18n.active_language() == code
        assert qapp.layoutDirection() == Qt.LayoutDirection.RightToLeft
    finally:
        i18n.uninstall(qapp)
    i18n.install(qapp, "de")
    try:
        assert qapp.layoutDirection() != Qt.LayoutDirection.RightToLeft
    finally:
        i18n.uninstall(qapp)


def test_atlas_hint_is_translated_in_every_catalog():
    """La consigne d'accueil d'Azimut doit être traduite dans les 27 catalogues."""
    for path in sorted(i18n.translations_dir().glob("azimut_*.ts")):
        _atlas_hint_translation(path)


def test_atlas_hint_translation_is_loaded(qapp):
    i18n.install(qapp, "en")
    try:
        assert QCoreApplication.translate("AtlasWindow", ATLAS_HINT) == (
            "Open a module, or drop files or folders onto it.")
    finally:
        i18n.uninstall(qapp)


@pytest.mark.parametrize("code", CATALOG_CODES)
def test_atlas_hint_translation_is_loaded_from_every_compiled_catalog(qapp, code):
    """Chaque .qm compilé doit restituer la traduction écrite dans son .ts."""
    ts_path = i18n.catalog_path(code).with_suffix(".ts")
    expected = _atlas_hint_translation(ts_path)
    assert expected != ATLAS_HINT

    catalog = i18n.catalog_path(code)
    translator = QTranslator()
    assert translator.load(str(catalog)), catalog.name
    qapp.installTranslator(translator)
    try:
        assert QCoreApplication.translate("AtlasWindow", ATLAS_HINT) == expected
    finally:
        qapp.removeTranslator(translator)


# ── Préférence ───────────────────────────────────────────────────────────
def test_language_default_is_auto(tmp_prefs):
    assert DEFAULTS["language"] == i18n.AUTO
    assert tmp_prefs.get("language") == i18n.AUTO


def test_language_preference_is_normalized_and_persisted(tmp_prefs):
    tmp_prefs.set("language", "pt_br")
    assert tmp_prefs.get("language") == "pt-BR"
    assert Preferences(tmp_prefs.path).get("language") == "pt-BR"


def test_unknown_language_preference_falls_back_to_auto(tmp_prefs):
    tmp_prefs.set("language", "klingon")
    assert tmp_prefs.get("language") == i18n.AUTO


# ── Installation ─────────────────────────────────────────────────────────
@pytest.mark.parametrize("code,catalog", [("zh-Hans", "qtbase_zh_CN.qm"),
                                           ("zh-Hant", "qtbase_zh_TW.qm")])
def test_chinese_qt_catalog_is_loaded(qapp, code, catalog):
    """Les libellés standard de Qt (boutons, dialogues) doivent suivre la langue choisie."""
    qt_dir = Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath))
    if not (qt_dir / catalog).is_file():
        pytest.skip(f"{catalog} absent de cette installation de Qt")
    i18n.install(qapp, code)
    try:
        assert i18n._QT_TRANSLATOR is not None
        assert QCoreApplication.translate("QPlatformTheme", "Cancel") != "Cancel"
    finally:
        i18n.uninstall(qapp)


def test_install_applies_catalog_and_uninstall_restores_source(qapp):
    state = i18n.install(qapp, "en")
    try:
        assert state.active == "en"
        assert state.fallback == "en"
        assert i18n.active_language() == "en"
        assert QCoreApplication.translate("ModuleWindow", "Démarrer") == "Start"
        assert QCoreApplication.translate("ModuleWindow", "Journal") == "Log"
    finally:
        i18n.uninstall(qapp)
    assert i18n.active_language() == i18n.SOURCE_LANGUAGE
    assert QCoreApplication.translate("ModuleWindow", "Démarrer") == "Démarrer"


def test_install_follows_system_language(qapp, monkeypatch):
    monkeypatch.setattr(i18n, "system_languages", lambda: ["de-DE", "en-US"])
    state = i18n.install(qapp, i18n.AUTO)
    try:
        assert state.detected == "de"
        assert state.active == "de"
    finally:
        i18n.uninstall(qapp)


def test_install_without_catalog_uses_detected_then_english(qapp, monkeypatch):
    catalogued = tuple(code for code in i18n.available_languages() if code != "ja")
    monkeypatch.setattr(i18n, "available_languages", lambda: catalogued)
    monkeypatch.setattr(i18n, "system_languages", lambda: ["es-ES"])
    state = i18n.install(qapp, "ja")                       # catalogue japonais simulé absent
    try:
        assert state.active == "es"                        # langue détectée
    finally:
        i18n.uninstall(qapp)

    monkeypatch.setattr(i18n, "system_languages", lambda: [])
    state = i18n.install(qapp, "ja")                       # aucune détection
    try:
        assert state.active == i18n.FALLBACK_LANGUAGE      # anglais par défaut
    finally:
        i18n.uninstall(qapp)


def test_install_loads_english_below_the_selected_language(qapp, monkeypatch):
    """Repli documenté : la langue choisie d'abord, l'anglais ensuite, la source en dernier."""
    calls = []
    real_load = i18n._load

    def spy(app, prefix, code, directory):
        calls.append((prefix, code))
        return real_load(app, prefix, code, directory)

    monkeypatch.setattr(i18n, "_load", spy)
    state = i18n.install(qapp, "de")
    try:
        assert state.active == "de"
        assert (i18n.CATALOG_PREFIX, "en") in calls
        assert calls.index((i18n.CATALOG_PREFIX, "en")) < calls.index((i18n.CATALOG_PREFIX, "de"))
    finally:
        i18n.uninstall(qapp)


def test_missing_translation_falls_back_to_english(qapp, monkeypatch):
    """Une entrée absente du catalogue actif doit s'afficher en anglais, jamais en français.

    On simule un catalogue japonais vide : l'anglais reste installé en dessous, comme dans
    :func:`i18n.install`, et Qt doit y remonter pour la chaîne manquante.
    """
    english = _atlas_entries(i18n.catalog_path("en").with_suffix(".ts"))
    source = "Embarqué"
    expected = english[("Dashboard", source)]
    real_load = i18n._load

    def load(app, prefix, code, directory):
        if (prefix, code) == (i18n.CATALOG_PREFIX, "ja"):
            translator = QTranslator(app)              # catalogue vide : aucune entrée
            app.installTranslator(translator)
            return translator
        return real_load(app, prefix, code, directory)

    monkeypatch.setattr(i18n, "_load", load)
    i18n.install(qapp, "ja")
    try:
        shown = QCoreApplication.translate("Dashboard", source)
    finally:
        i18n.uninstall(qapp)
    assert shown != source
    assert shown == expected


def test_install_source_language_keeps_source_strings(qapp):
    """Le français n'a pas de catalogue Azimut : les chaînes du code restent la référence."""
    state = i18n.install(qapp, "fr")
    try:
        assert state.active == "fr"
        assert i18n.active_language() == "fr"
        assert i18n._APP_TRANSLATOR is None
        assert QCoreApplication.translate("ModuleWindow", "Démarrer") == "Démarrer"
    finally:
        i18n.uninstall(qapp)


def test_source_language_loads_the_french_qt_catalog(qapp):
    """En français, les libellés standard de Qt (Oui/Non/OK/Annuler) doivent être traduits."""
    qt_dir = Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath))
    if not (qt_dir / "qtbase_fr.qm").is_file():
        pytest.skip("qtbase_fr.qm absent de cette installation de Qt")
    i18n.install(qapp, "fr")
    try:
        assert i18n._QT_TRANSLATOR is not None
        assert QCoreApplication.translate("QPlatformTheme", "Cancel") == "Annuler"
        assert QCoreApplication.translate("QPlatformTheme", "Save") == "Enregistrer"
    finally:
        i18n.uninstall(qapp)


def test_source_language_applies_the_french_locale(qapp, monkeypatch):
    """Le français doit imposer les formats de date français, pas ceux de la locale système."""
    calls: list[str] = []
    real = i18n._apply_locale
    monkeypatch.setattr(i18n, "_apply_locale", lambda code: (calls.append(code), real(code)))
    QLocale.setDefault(QLocale("en_US"))
    i18n.install(qapp, "fr")
    try:
        assert calls == ["fr"]
        assert QLocale().language() == QLocale.Language.French
        assert QLocale().dateFormat(QLocale.FormatType.ShortFormat) == "dd/MM/yyyy"
    finally:
        i18n.uninstall(qapp)
