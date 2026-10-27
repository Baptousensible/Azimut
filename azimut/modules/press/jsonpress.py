"""Azimut Press — logique pure JSON (sans Qt) : validation, minification, indentation, vérification."""
from __future__ import annotations

import codecs
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

MODE_MINIFY = "minify"
MODE_INDENT = "indent"
MODES: tuple[str, ...] = (MODE_MINIFY, MODE_INDENT)

STATUS_DONE = "done"
STATUS_ALREADY = "already"

INDENT_WARNING = (
    "Mode « Indenter » : Python peut normaliser les nombres (par exemple 1E5 devient 100000.0)."
)


class JsonInvalidError(Exception):
    """Le fichier n'est pas un JSON valide (message avec ligne et colonne si connues)."""


class JsonVerificationError(Exception):
    """La sortie n'est pas équivalente à l'entrée : le fichier ne doit pas être écrit."""


@dataclass
class JsonSettings:
    mode: str = MODE_MINIFY
    indent: int = 2
    sort_keys: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Any) -> "JsonSettings":
        base = cls()
        if not isinstance(data, dict):
            return base
        mode = data.get("mode", base.mode)
        indent = data.get("indent", base.indent)
        return cls(
            mode=mode if mode in MODES else base.mode,
            indent=indent if indent in (2, 4) else base.indent,
            sort_keys=bool(data.get("sort_keys", base.sort_keys)),
        )


@dataclass
class JsonResult:
    status: str                 # STATUS_DONE | STATUS_ALREADY
    data: bytes | None          # contenu à écrire (UTF-8 sans BOM) ; None = copier l'original
    size_in: int
    size_out: int
    notes: list[str] = field(default_factory=list)


# --- Messages d'erreur en français -------------------------------------------------------------

_MESSAGES_FR: tuple[tuple[str, str], ...] = (
    ("Illegal trailing comma before end of object", "virgule en trop avant la fin de l'objet"),
    ("Illegal trailing comma before end of array", "virgule en trop avant la fin du tableau"),
    ("Expecting property name enclosed in double quotes", "nom de propriété entre guillemets doubles attendu"),
    ("Expecting value", "valeur attendue"),
    ("Expecting ',' delimiter", "séparateur « , » attendu"),
    ("Expecting ':' delimiter", "séparateur « : » attendu"),
    ("Unterminated string", "chaîne de caractères non terminée"),
    ("Extra data", "données superflues après la fin du document"),
    ("Invalid control character", "caractère de contrôle interdit dans une chaîne"),
    ("Invalid \\escape", "séquence d'échappement invalide"),
    ("Invalid \\u", "séquence \\u invalide"),
)


def _translate(msg: str) -> str:
    for en, fr in _MESSAGES_FR:
        if msg.startswith(en):
            return fr
    return msg


# --- Lecture et validation ---------------------------------------------------------------------

def decode_json_bytes(raw: bytes) -> str:
    """Décode du JSON en UTF-8 (BOM toléré). Lève JsonInvalidError si l'encodage est invalide."""
    if raw.startswith(codecs.BOM_UTF8):
        raw = raw[len(codecs.BOM_UTF8):]
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise JsonInvalidError(f"encodage UTF-8 invalide (octet {exc.start})") from exc


def parse_json(text: str) -> Any:
    """Valide et charge le texte avec json.loads ; lève JsonInvalidError avec ligne et colonne."""
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise JsonInvalidError(
            f"{_translate(exc.msg)} (ligne {exc.lineno}, colonne {exc.colno})"
        ) from exc
    except (ValueError, RecursionError) as exc:  # entier démesuré, imbrication trop profonde…
        raise JsonInvalidError(f"document non exploitable : {exc}") from exc


# --- Minification à jetons ---------------------------------------------------------------------

# Jetons : une chaîne complète (guillemets, échappements \" et \\ inclus) OU un run de blancs JSON.
# Les chaînes étant consommées d'un bloc, les espaces qu'elles contiennent ne sont jamais touchés ;
# tout le reste (nombres, true/false/null, ponctuation) est recopié tel quel : 1E5 reste 1E5.
_TOKEN_RE = re.compile(r'("[^"\\]*(?:\\.[^"\\]*)*")|[ \t\n\r]+', re.DOTALL)


def minify_text(text: str) -> str:
    """Retire les espaces et retours à la ligne situés hors des chaînes, sans réécrire les nombres."""
    return _TOKEN_RE.sub(r"\1", text)


def indent_text(value: Any, indent: int = 2, sort_keys: bool = False) -> str:
    """Sérialise avec json.dumps (les nombres peuvent être normalisés par Python)."""
    return json.dumps(value, indent=indent, ensure_ascii=False, sort_keys=sort_keys)


# --- Équivalence -------------------------------------------------------------------------------

def json_equal(a: Any, b: Any) -> bool:
    """Égalité de deux documents chargés (équivaut à a == b, mais NaN == NaN et True != 1)."""
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if isinstance(a, float) and isinstance(b, float) and a != a and b != b:
        return True
    if isinstance(a, dict):
        return (isinstance(b, dict) and a.keys() == b.keys()
                and all(json_equal(v, b[k]) for k, v in a.items()))
    if isinstance(a, list):
        return (isinstance(b, list) and len(a) == len(b)
                and all(json_equal(x, y) for x, y in zip(a, b)))
    return bool(a == b)


# --- Traitement complet ------------------------------------------------------------------------

def process_text(text: str, settings: JsonSettings) -> str:
    """Valide `text`, produit la sortie demandée et vérifie son équivalence avec l'entrée."""
    value = parse_json(text)
    if settings.mode == MODE_INDENT:
        out = indent_text(value, settings.indent, settings.sort_keys)
    else:
        out = minify_text(text)
    try:
        same = json_equal(json.loads(out), value)
    except (ValueError, RecursionError) as exc:
        raise JsonVerificationError(f"la sortie n'est plus un JSON valide ({exc})") from exc
    if not same:
        raise JsonVerificationError("la sortie n'est pas équivalente à l'entrée")
    return out


def compress_json(src: Path, settings: JsonSettings) -> JsonResult:
    """Traite un fichier JSON sans jamais le modifier. Lève JsonInvalidError / JsonVerificationError."""
    raw = src.read_bytes()
    out = process_text(decode_json_bytes(raw), settings).encode("utf-8")
    if settings.mode == MODE_MINIFY and len(out) >= len(raw):
        return JsonResult(STATUS_ALREADY, None, len(raw), len(raw))
    return JsonResult(STATUS_DONE, out, len(raw), len(out))
