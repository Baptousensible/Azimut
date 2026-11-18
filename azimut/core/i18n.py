"""Internationalisation : registre des langues, détection système et catalogues Qt.

Les chaînes du code sont écrites en français : c'est la langue source. Un catalogue
``azimut/resources/translations/azimut_<code>.qm`` traduit ces chaînes vers une autre
langue ; sans catalogue, Qt affiche la chaîne source.

Ordre de repli : langue choisie → langue détectée → anglais → français (source).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from PySide6.QtCore import QCoreApplication, QLibraryInfo, QLocale, Qt, QTranslator

from azimut.core.logs import get_logger
from azimut.core.paths import resource_path

log = get_logger(__name__)

AUTO = "auto"
SOURCE_LANGUAGE = "fr"          # langue dans laquelle le code est écrit
FALLBACK_LANGUAGE = "en"
# Langues qui s'écrivent de droite à gauche : l'interface entière doit être retournée.
RTL_LANGUAGES = frozenset({"ar", "he"})
CATALOG_PREFIX = "azimut"
TRANSLATIONS_DIRNAME = "translations"
# Qt nomme certains de ses catalogues par variante régionale : « zh-Hans » est servi par
# ``qtbase_zh_CN.qm`` et « zh-Hant » par ``qtbase_zh_TW.qm``. Sans cette correspondance,
# ces deux langues garderaient les libellés standard de Qt (boutons, dialogues) en anglais.
QT_CATALOG_CODES: dict[str, str] = {
    "zh-Hans": "zh_CN",
    "zh-Hant": "zh_TW",
}


@dataclass(frozen=True)
class Language:
    """Une langue proposée dans les préférences."""

    code: str                   # code BCP 47 simplifié, ex. « pt-BR »
    native_name: str            # nom dans la langue elle-même, ex. « Português »
    english_name: str           # nom en anglais, pour le tri et les infobulles


# Toutes les langues que l'interface sait accueillir. Une langue n'a d'effet que si son
# catalogue est présent ; les autres sont proposées avec la mention « traduction à venir ».
LANGUAGES: tuple[Language, ...] = (
    Language("ar", "العربية", "Arabic"),
    Language("cs", "Čeština", "Czech"),
    Language("da", "Dansk", "Danish"),
    Language("de", "Deutsch", "German"),
    Language("el", "Ελληνικά", "Greek"),
    Language("en", "English", "English"),
    Language("es", "Español", "Spanish"),
    Language("fi", "Suomi", "Finnish"),
    Language("fr", "Français", "French"),
    Language("he", "עברית", "Hebrew"),
    Language("hi", "हिन्दी", "Hindi"),
    Language("hu", "Magyar", "Hungarian"),
    Language("it", "Italiano", "Italian"),
    Language("ja", "日本語", "Japanese"),
    Language("ko", "한국어", "Korean"),
    Language("nl", "Nederlands", "Dutch"),
    Language("nb", "Norsk bokmål", "Norwegian Bokmål"),
    Language("pl", "Polski", "Polish"),
    Language("pt", "Português", "Portuguese"),
    Language("pt-BR", "Português (Brasil)", "Portuguese (Brazil)"),
    Language("ro", "Română", "Romanian"),
    Language("ru", "Русский", "Russian"),
    Language("sv", "Svenska", "Swedish"),
    Language("tr", "Türkçe", "Turkish"),
    Language("uk", "Українська", "Ukrainian"),
    Language("vi", "Tiếng Việt", "Vietnamese"),
    Language("zh-Hans", "简体中文", "Chinese (Simplified)"),
    Language("zh-Hant", "繁體中文", "Chinese (Traditional)"),
)

_BY_CODE: dict[str, Language] = {language.code: language for language in LANGUAGES}
_CANONICAL: dict[str, str] = {code.lower(): code for code in _BY_CODE}

# Codes hérités ou variantes régionales ramenés vers une langue du registre.
_ALIASES: dict[str, str] = {
    "iw": "he", "mo": "ro", "nb": "nb", "nn": "nb", "no": "nb",
    "zh": "zh-Hans", "zh-cn": "zh-Hans", "zh-sg": "zh-Hans", "zh-my": "zh-Hans",
    "zh-tw": "zh-Hant", "zh-hk": "zh-Hant", "zh-mo": "zh-Hant",
}


def normalize_language(tag: object) -> str:
    """Code de langue canonique du registre (``pt_br`` → ``pt-BR``), ou ``""`` si inconnu."""
    if not isinstance(tag, str):
        return ""
    raw = tag.strip().replace("_", "-").split(".")[0].split("@")[0]
    if not raw:
        return ""
    low = raw.lower()
    if low in _ALIASES:
        return _ALIASES[low]
    if low in _CANONICAL:
        return _CANONICAL[low]
    base = low.split("-")[0]                       # « en-US », « es-419 » : langue de base
    if base in _ALIASES:
        return _ALIASES[base]
    return _CANONICAL.get(base, "")


def language(code: str) -> Language | None:
    """Entrée du registre pour un code (accepte les variantes)."""
    return _BY_CODE.get(normalize_language(code))


def language_name(code: str) -> str:
    """Nom natif d'une langue (le code lui-même s'il est inconnu)."""
    entry = language(code)
    return entry.native_name if entry is not None else code


def system_languages() -> list[str]:
    """Langues de l'interface du système, dans l'ordre de préférence de l'utilisateur."""
    try:
        return [str(tag) for tag in QLocale.system().uiLanguages()]
    except Exception:                              # plateforme exotique : aucune détection
        log.debug("Détection de la langue système impossible", exc_info=True)
        return []


def detect_language(ui_languages: Iterable[str] | None = None) -> str:
    """Première langue du système présente dans le registre (``""`` si aucune)."""
    tags = system_languages() if ui_languages is None else list(ui_languages)
    for tag in tags:
        code = normalize_language(tag)
        if code:
            return code
    return ""


def translations_dir() -> Path:
    """Dossier des catalogues embarqués (``azimut/resources/translations``)."""
    return resource_path(TRANSLATIONS_DIRNAME)


def qt_catalog_code(code: str) -> str:
    """Nom du catalogue Qt (``qtbase_<nom>``) correspondant à une langue du registre."""
    normalized = normalize_language(code)
    return QT_CATALOG_CODES.get(normalized, normalized.replace("-", "_"))


def catalog_path(code: str) -> Path:
    """Chemin du catalogue compilé d'une langue (``azimut_pt_BR.qm``)."""
    normalized = normalize_language(code)
    suffix = f"_{normalized.replace('-', '_')}" if normalized else ""
    return translations_dir() / f"{CATALOG_PREFIX}{suffix}.qm"


def available_languages() -> tuple[str, ...]:
    """Langues dotées d'un catalogue compilé, dans l'ordre du registre."""
    found: set[str] = set()
    try:
        paths = list(translations_dir().glob(f"{CATALOG_PREFIX}_*.qm"))
    except OSError:                                # dossier absent ou illisible
        return ()
    for path in paths:
        stem = path.stem[len(CATALOG_PREFIX) + 1:]
        code = normalize_language(stem)
        if code and code in _BY_CODE:
            found.add(code)
    return tuple(lang.code for lang in LANGUAGES if lang.code in found)


def has_catalog(code: str) -> bool:
    """True si la langue dispose d'un catalogue compilé."""
    normalized = normalize_language(code)
    return bool(normalized) and normalized in available_languages()


def is_available(code: str) -> bool:
    """True si l'interface peut s'afficher dans cette langue (catalogue, ou langue source)."""
    normalized = normalize_language(code)
    return normalized == SOURCE_LANGUAGE or has_catalog(normalized)


def is_rtl(code: str) -> bool:
    """True si la langue s'écrit de droite à gauche (arabe, hébreu)."""
    return normalize_language(code) in RTL_LANGUAGES


def resolve_language(preference: object = AUTO, *, ui_languages: Iterable[str] | None = None,
                     available: Iterable[str] | None = None) -> str:
    """Langue réellement utilisée : choix explicite, sinon détection, sinon anglais, sinon source."""
    catalogued = set(available if available is not None else available_languages())
    catalogued.add(SOURCE_LANGUAGE)                # les chaînes du code sont déjà en français
    chosen = normalize_language(preference) if preference and preference != AUTO else ""
    detected = detect_language(ui_languages)
    for candidate in (chosen, detected):
        if candidate and candidate in catalogued:
            return candidate
    if FALLBACK_LANGUAGE in catalogued:
        return FALLBACK_LANGUAGE
    return SOURCE_LANGUAGE


@dataclass(frozen=True)
class TranslationState:
    """Résultat de l'installation des traducteurs (utile aux journaux et aux tests)."""

    requested: str              # préférence brute : « auto » ou un code
    detected: str               # langue détectée sur le système
    active: str                 # langue effectivement appliquée
    fallback: str               # langue utilisée pour les chaînes sans traduction


_APP_TRANSLATOR: QTranslator | None = None
_QT_TRANSLATOR: QTranslator | None = None
_FALLBACK_TRANSLATOR: QTranslator | None = None
_ACTIVE = SOURCE_LANGUAGE


def _load(app: QCoreApplication, prefix: str, code: str, directory: Path) -> QTranslator | None:
    """Charge ``<prefix>_<code>.qm`` depuis ``directory`` ; None si le fichier manque."""
    translator = QTranslator(app)
    filename = f"{prefix}_{code.replace('-', '_')}"
    if not translator.load(filename, str(directory)):
        return None
    app.installTranslator(translator)
    return translator


def _apply_locale(code: str) -> None:
    """Aligne formats de date et de nombre sur la langue quand Qt sait la représenter."""
    locale = QLocale(code.replace("-", "_"))
    if locale.language() != QLocale.Language.C:
        QLocale.setDefault(locale)


def _apply_direction(app: QCoreApplication) -> None:
    """Retourne l'interface pour l'arabe et l'hébreu (sinon : sens du système)."""
    if is_rtl(_ACTIVE):
        app.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
    else:
        app.setLayoutDirection(Qt.LayoutDirection.LayoutDirectionAuto)


def install(app: QCoreApplication, preference: object = AUTO) -> TranslationState:
    """Installe les catalogues Azimut et Qt pour la langue résolue ; réinstallable.

    La langue source (français) reçoit aussi le catalogue Qt et la locale françaises : les
    libellés standard de Qt (« Oui », « Non », « OK », « Annuler ») et les formats de date
    viennent de Qt, pas des chaînes du code.
    """
    global _APP_TRANSLATOR, _QT_TRANSLATOR, _FALLBACK_TRANSLATOR, _ACTIVE
    uninstall(app)
    detected = detect_language()
    catalogued = available_languages()
    active = resolve_language(preference, available=catalogued)
    fallback = FALLBACK_LANGUAGE if FALLBACK_LANGUAGE in catalogued else SOURCE_LANGUAGE
    # Le traducteur Qt est installé d'abord : le catalogue Azimut garde la priorité. Il sert
    # aussi la langue source, qui n'a pas de catalogue Azimut mais a bien besoin de Qt.
    _QT_TRANSLATOR = _load(app, "qtbase", qt_catalog_code(active),
                           Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)))
    if active != SOURCE_LANGUAGE:
        if active != FALLBACK_LANGUAGE:
            # Qt interroge le dernier traducteur installé puis remonte : l'anglais est posé
            # sous la langue choisie pour combler les messages encore non traduits.
            _FALLBACK_TRANSLATOR = _load(app, CATALOG_PREFIX, FALLBACK_LANGUAGE, translations_dir())
        _APP_TRANSLATOR = _load(app, CATALOG_PREFIX, active, translations_dir())
    _apply_locale(active)
    _ACTIVE = active
    _apply_direction(app)
    if _APP_TRANSLATOR is None and active != SOURCE_LANGUAGE:
        log.warning("Catalogue introuvable pour « %s » : repli sur %s", active, fallback)
    return TranslationState(requested=str(preference or AUTO), detected=detected,
                            active=active, fallback=fallback)


def uninstall(app: QCoreApplication) -> None:
    """Retire les traducteurs précédemment installés par :func:`install`."""
    global _APP_TRANSLATOR, _QT_TRANSLATOR, _FALLBACK_TRANSLATOR, _ACTIVE
    for translator in (_APP_TRANSLATOR, _FALLBACK_TRANSLATOR, _QT_TRANSLATOR):
        if translator is not None:
            app.removeTranslator(translator)
            translator.deleteLater()
    _APP_TRANSLATOR = _QT_TRANSLATOR = _FALLBACK_TRANSLATOR = None
    _ACTIVE = SOURCE_LANGUAGE
    app.setLayoutDirection(Qt.LayoutDirection.LayoutDirectionAuto)
    QLocale.setDefault(QLocale.system())              # formats de date et de nombre d'origine


def active_language() -> str:
    """Langue du traducteur actuellement installé (``SOURCE_LANGUAGE`` si aucun)."""
    return _ACTIVE
