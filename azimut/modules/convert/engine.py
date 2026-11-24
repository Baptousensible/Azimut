"""Logique pure d'Azimut Convert (sans Qt) : travaux, commandes ffmpeg, progression."""
from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Collection, NamedTuple

from azimut.core.ffmpeg_args import SAFE_INPUT_FLAGS, drop_neutral_input, neutral_input  # noqa: F401 - ré-exportés
from azimut.core.fsutil import atomic_write_text, iter_files, safe_relative
from azimut.core.logs import get_logger
from azimut.modules.convert import imaging
from azimut.modules.convert.advanced import family, hardware_kind
from azimut.modules.convert.probe import (
    MediaProbe, build_media_probe_command as probe_cmd, input_flags, input_spec,
)
from azimut.modules.convert.profiles import (
    ALL_EXTS, AUDIO_EXTS, COPY_PROFILE, IMAGE_EXTS, OTHER_KIND, RAW_EXTS, VIDEO_EXTS, Profile,
)

_log = get_logger("convert.engine")

# ── États affichés dans le tableau ───────────────────────────────────────────
STATE_PENDING = "En attente"
STATE_RUNNING = "En cours"
STATE_DONE = "Terminé"
STATE_SKIPPED = "Déjà converti"
STATE_NO_ENCODER = "Encodeur manquant"
STATE_UNSUPPORTED = "Non pris en charge"
STATE_ERROR = "Erreur"
STATE_CANCELLED = "Annulé"
STATE_KEPT = "Source conservée"
STATE_COPIED = "Copié"
STATE_JSON_OMITTED = "JSON non copié"      # JSON Takeout déjà réinjecté : volontairement absent de la sortie

STATE_TAGS = {
    STATE_PENDING: "neutre", STATE_RUNNING: "info", STATE_DONE: "ok", STATE_SKIPPED: "neutre",
    STATE_NO_ENCODER: "err", STATE_UNSUPPORTED: "warn", STATE_ERROR: "err",
    STATE_CANCELLED: "warn", STATE_KEPT: "warn", STATE_COPIED: "ok", STATE_JSON_OMITTED: "neutre",
}

MANIFEST_NAME = ".azimut_convert_state.json"   # état de reprise, à côté des sorties
MANIFEST_MTIME_TOLERANCE = 2.0
# La géométrie et l'orientation du PNG préparé ne doivent pas écraser celles du fichier produit.
METADATA_EXCLUDED = (
    "Orientation", "ImageWidth", "ImageHeight", "ExifImageWidth", "ExifImageHeight",
    "ThumbnailImage", "PreviewImage", "ExifThumbnail",
    "FileName", "Directory", "FileSize", "FileType", "MIMEType",
)


def profile_fingerprint(profile: Profile) -> str:
    """Empreinte stable des réglages qui influencent le fichier produit."""
    data = asdict(profile)
    data.pop("only_if_encoder", None)          # purement d'affichage : ne doit pas invalider « Déjà converti »
    payload = json.dumps(data, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def manifest_key(dest: Path, base: Path | None) -> str:
    """Clé du manifeste : chemin relatif au dossier de sortie si possible."""
    if base is None:
        return str(dest)
    try:
        return Path(dest).relative_to(base).as_posix()
    except ValueError:
        return str(dest)


def load_manifest(base: Path) -> dict:
    """Contenu du manifeste de reprise d'un dossier de sortie (vide si absent/illisible)."""
    try:
        data = json.loads((Path(base) / MANIFEST_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_manifest(base: Path, data: dict) -> None:
    """Écrit le manifeste atomiquement ; une erreur d'écriture ne doit pas invalider le lot."""
    atomic_write_text(Path(base) / MANIFEST_NAME, json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False))


# ── Vérification de la sortie (arrêt forcé, plantage) ────────────────────────
#: Nom d'un temporaire de conversion : « .photo.a1b2c3d4e5f6.part.avif ». Il n'est jamais une
#: sortie : FFmpeg écrit dedans et il est supprimé dès la publication. Il ne reste donc sur le
#: disque que si le processus a été tué avant la fin — c'est le fichier corrompu typique.
_PART_FILE_RE = re.compile(r"^\..+\.[0-9a-f]{12}\.part(?:\..+)?$", re.IGNORECASE)


@dataclass(frozen=True)
class DamagedFile:
    """Fichier de sortie qu'un arrêt forcé a pu laisser incomplet ou corrompu."""
    path: Path
    reason: str                 # « partiel », « taille » ou « illisible »
    detail: str = ""


def is_partial_file(path: Path | str) -> bool:
    """Vrai pour un temporaire de conversion (`part_path`) resté sur le disque."""
    return bool(_PART_FILE_RE.match(Path(path).name))


def _is_stale_manifest_tmp(path: Path) -> bool:
    """Temporaire d'écriture du manifeste laissé par un arrêt pendant `save_manifest`."""
    return path.name.startswith(MANIFEST_NAME + ".") and path.name.endswith(".tmp")


def find_incomplete_files(base: Path, cancel: Callable[[], bool] | None = None) -> list[Path]:
    """Temporaires laissés par une conversion interrompue, sous `base` (récursif).

    Leur nom commence par un point : `iter_files` les ignore et le manifeste n'en parle pas. On les
    parcourt donc directement. Les liens symboliques ne sont pas suivis (pas de boucle).
    """
    found: list[Path] = []
    stack: list[Path] = [Path(base)]
    while stack:
        if cancel is not None and cancel():
            break
        folder = stack.pop()
        try:
            with os.scandir(folder) as it:
                entries = list(it)
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_dir(follow_symlinks=False):
                    stack.append(Path(entry.path))
                elif entry.is_file(follow_symlinks=False) and (
                        is_partial_file(entry.name) or _is_stale_manifest_tmp(Path(entry.name))):
                    found.append(Path(entry.path))
            except OSError:
                continue
    return sorted(found)


def manifest_size_problems(base: Path, manifest: dict | None = None) -> list[DamagedFile]:
    """Sorties dont la taille ne correspond plus à celle qu'avait enregistrée le manifeste.

    Un fichier tronqué (arrêt brutal, disque plein, copie interrompue) garde son nom mais plus son contenu.
    Une sortie absente n'est pas signalée ici : le contrôle final de la conversion s'en charge déjà.
    """
    data = load_manifest(base) if manifest is None else manifest
    problems: list[DamagedFile] = []
    for key, rec in data.items():
        if not isinstance(rec, dict):
            continue
        expected = rec.get("dest_size")
        if not isinstance(expected, int) or isinstance(expected, bool) or expected < 0:
            continue
        path = Path(key)
        if not path.is_absolute():
            path = Path(base) / key
        try:
            actual = path.stat().st_size
        except OSError:
            continue
        if actual != expected:
            problems.append(DamagedFile(path, "taille", f"{actual} ≠ {expected} octets"))
    return sorted(problems, key=lambda p: str(p.path).lower())


def metadata_copy_args(source: Path, target: Path) -> list[str]:
    """Arguments ExifTool pour recopier les métadonnées de la source vers une sortie Pillow.

    L'orientation et les dimensions sont exclues : elles ont déjà été appliquées aux pixels par Pillow.
    """
    return ["-overwrite_original", "-m", "-TagsFromFile", str(source), "-all:all",
            *[f"--{tag}" for tag in METADATA_EXCLUDED], str(target)]

EVEN_VF = "scale=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p"


class EncoderMissingError(RuntimeError):
    """Aucun encodeur candidat n'est disponible dans ce ffmpeg."""


def state_tag(state: str) -> str:
    """Couleur de ligne (« _tag » du tableau) associée à un état."""
    return STATE_TAGS.get(state, "neutre")


# ── Entrées ──────────────────────────────────────────────────────────────────
def classify(path: Path | str) -> str | None:
    """Famille d'un fichier d'après son extension : image | video | audio | None."""
    ext = Path(path).suffix.lower()
    if ext in IMAGE_EXTS or ext in RAW_EXTS:
        return "image"
    if ext in VIDEO_EXTS:
        return "video"
    if ext in AUDIO_EXTS:
        return "audio"
    return None


def path_key(path: Path | str) -> str:
    """Clé de déduplication d'un chemin."""
    return os.path.normcase(os.path.abspath(str(path)))


@dataclass(frozen=True)
class SourceItem:
    """Un fichier à convertir. `root` : dossier ajouté (None pour un fichier isolé)."""
    path: Path
    root: Path | None
    kind: str
    size: int

    @property
    def rel(self) -> Path:
        return Path(self.path.name) if self.root is None else safe_relative(self.path, self.root)

    @property
    def mtime(self) -> float:
        """Date de modification de la source (0 si elle a disparu)."""
        try:
            return self.path.stat().st_mtime
        except OSError:
            return 0.0


class ScanResult(NamedTuple):
    items: list[SourceItem]
    ignored: list[str]


def scan_inputs(paths: Collection[str | Path], recursive: bool = True,
                cancel: Callable[[], bool] | None = None, *, include_others: bool = False) -> ScanResult:
    """Transforme fichiers et dossiers en liste de SourceItem (dédoublonnée, triée).

    `include_others` : garde aussi les fichiers qui ne sont ni images, ni vidéos, ni audios (kind « other »),
    destinés à être copiés tels quels.
    """
    items: list[SourceItem] = []
    ignored: list[str] = []
    seen: set[str] = set()

    def add(path: Path, root: Path | None) -> None:
        kind = classify(path)
        if kind is None and include_others:
            kind = OTHER_KIND
        if kind is None:
            ignored.append(f"{path} (extension non gérée)")
            return
        key = path_key(path)
        if key in seen:
            return
        try:
            size = path.stat().st_size
        except OSError:
            ignored.append(f"{path} (illisible)")
            return
        seen.add(key)
        items.append(SourceItem(path, root, kind, size))

    for raw in paths:
        if cancel is not None and cancel():
            break
        p = Path(raw)
        if p.is_dir():
            for f in iter_files(p, recursive=recursive, extensions=None if include_others else ALL_EXTS,
                                cancel=cancel):
                add(f, p)
        elif p.is_file():
            add(p, None)
        else:
            ignored.append(f"{p} (introuvable)")
    items.sort(key=lambda i: (str(i.root or "").lower(), str(i.path).lower()))
    return ScanResult(items, ignored)


# ── Encodeurs ────────────────────────────────────────────────────────────────
_ENC_RE = re.compile(r"^\s*[VAS][F.][S.][X.][B.][D.]\s+(\S+)")


def parse_encoders(text: str) -> frozenset[str]:
    """Noms d'encodeurs d'après la sortie de `ffmpeg -encoders`."""
    names: set[str] = set()
    has_sep = any(line.strip().startswith("---") for line in text.splitlines())
    started = not has_sep
    for line in text.splitlines():
        if not started:
            started = line.strip().startswith("---")
            continue
        m = _ENC_RE.match(line)
        if m and m.group(1) != "=":
            names.add(m.group(1))
    return frozenset(names)


def pick_encoder(candidates: tuple[str, ...], encoders: Collection[str] | None) -> str:
    """Premier encodeur candidat disponible (le premier si la liste est inconnue)."""
    if not candidates:
        return ""
    if encoders is None:
        return candidates[0]
    for c in candidates:
        if c in encoders:
            return c
    raise EncoderMissingError(" ou ".join(candidates))


AV1_SMALL_MEDIA_MAX_PX = 64


def av1_small_media_encoder(profile: Profile, encoders: Collection[str] | None,
                            width: int | None, height: int | None) -> str | None:
    """Encodeur AV1 de repli pour un média plus petit que 64 px, ou None.

    `libsvtav1` refuse ces dimensions sur FFmpeg 6.x et peut se bloquer à 16×16 avec SVT-AV1 3.1.2 ;
    `libaom-av1` les encode. Le choix est fait avant le premier essai pour ne pas attendre le délai
    d'expiration d'un blocage. None si le profil n'a pas libaom, si l'encodeur n'est pas disponible
    ou si les dimensions sont inconnues.
    """
    if profile.kind not in ("image", "video"):
        return None
    if family(profile) != "av1" or "libaom-av1" not in profile.video_encoders:
        return None
    if encoders is not None and "libaom-av1" not in encoders:
        return None
    if width is None or height is None or width <= 0 or height <= 0:
        return None
    if min(width, height) >= AV1_SMALL_MEDIA_MAX_PX:
        return None
    return "libaom-av1"


def find_vaapi_device() -> str | None:
    """Périphérique VA-API à utiliser, ou None si aucun n'est disponible."""
    override = os.environ.get("AZIMUT_VAAPI_DEVICE")
    if override:
        return override
    try:
        for path in sorted(Path("/dev/dri").glob("renderD*")):
            if path.exists():
                return str(path)
    except OSError as exc:
        _log.debug("Périphériques de rendu illisibles : %s", exc)
    return None


#: Encodeurs matériels exposés par les profils, par backend, avec le codec affiché à l'utilisateur.
HARDWARE_ENCODERS: dict[str, tuple[tuple[str, str], ...]] = {
    "nvenc": (("h264_nvenc", "H.264"), ("hevc_nvenc", "H.265"), ("av1_nvenc", "AV1")),
    "qsv": (("h264_qsv", "H.264"), ("hevc_qsv", "H.265"), ("av1_qsv", "AV1")),
    "vaapi": (("h264_vaapi", "H.264"), ("hevc_vaapi", "H.265"), ("av1_vaapi", "AV1")),
}


#: Encodeurs dont le pilote est réellement testé au démarrage (petit encodage à vide).
HARDWARE_PROBE_ENCODERS = tuple(
    encoder for entries in HARDWARE_ENCODERS.values() for encoder, _ in entries
    if encoder.endswith(("_nvenc", "_qsv"))
)


def build_hardware_probe_command(ffmpeg: str | Path, encoder: str) -> list[str]:
    """Commande qui encode réellement une image de 64×64 avec un encodeur matériel.

    Aucun fichier n'est lu ni écrit (`lavfi` → `-f null -`) : seuls le pilote et le GPU sont sollicités.
    Un code retour 0 prouve que l'encodeur s'est ouvert, pas seulement que FFmpeg le liste. QSV reçoit
    la même initialisation que `build_command` (`qsv=hw` + upload GPU), sinon le test ne représenterait
    pas la conversion réelle.
    """
    setup: list[str] = []
    filters: list[str] = []
    if encoder.endswith("_qsv"):
        setup = ["-init_hw_device", "qsv=hw", "-filter_hw_device", "hw"]
        filters = ["-vf", "format=nv12,hwupload=extra_hw_frames=64"]
    return [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-nostdin", *setup,
            "-f", "lavfi", "-i", "color=c=black:s=64x64:d=0.1:r=10", *filters,
            "-frames:v", "1", "-c:v", encoder, "-f", "null", "-"]


def hardware_backends(encoders: Collection[str] | None) -> list[tuple[str, list[str]]]:
    """Backends matériels utilisables et leurs codecs, d'après les encodeurs réellement testés.

    VA-API n'est retenu que si un périphérique de rendu existe : FFmpeg expose ses encodeurs même quand
    aucun GPU ne peut les exécuter. Pour NVENC et QSV, la liste reçue doit déjà exclure les encodeurs
    dont le pilote a échoué au test de démarrage (voir `workers.probe_hardware`).
    """
    if not encoders:
        return []
    found: list[tuple[str, list[str]]] = []
    for backend, entries in HARDWARE_ENCODERS.items():
        if backend == "vaapi" and find_vaapi_device() is None:
            continue
        codecs = [label for encoder, label in entries if encoder in encoders]
        if codecs:
            found.append((backend, codecs))
    return found


def _failed_hardware(profile: Profile, hardware_failed: Collection[str] | None) -> list[str]:
    """Encodeurs du profil dont le test de pilote a échoué."""
    return [e for e in profile.video_encoders if e in (hardware_failed or ())]


def profile_unavailable_reason(profile: Profile, encoders: Collection[str] | None,
                               hardware_failed: Collection[str] | None = None) -> str | None:
    """Raison courte pour laquelle un profil ne peut pas tourner ici, ou None.

    `hardware_failed` contient les encodeurs matériels listés par FFmpeg mais dont le pilote ou le GPU
    n'a pas répondu au test de démarrage : le profil est marqué indisponible avant même le lancement.
    """
    if profile.id == "image_heic" and not imaging.heif_available():
        return "pillow-heif manquant"
    if hardware_kind(profile) == "vaapi" and find_vaapi_device() is None:
        return "périphérique VA-API absent"
    if _failed_hardware(profile, hardware_failed):
        return "pilote ou GPU ne répond pas"
    if encoders is not None:
        for candidates in (profile.video_encoders, profile.audio_encoders):
            if candidates and not any(e in encoders for e in candidates):
                return "encodeur manquant"
    return None


def missing_encoder_message(profile: Profile, encoders: Collection[str] | None,
                            hardware_failed: Collection[str] | None = None) -> str | None:
    """Message explicite si le profil est impossible avec ce ffmpeg, sinon None."""
    reason = profile_unavailable_reason(profile, encoders, hardware_failed)
    if reason is None:
        return None
    if reason == "pillow-heif manquant":
        return (f"Profil « {profile.label} » impossible : pillow-heif non installé "
                "(pip install pillow-heif).")
    if reason == "périphérique VA-API absent":
        return (f"Profil « {profile.label} » impossible : aucun périphérique VA-API "
                "(/dev/dri/renderD*) détecté.")
    if reason == "pilote ou GPU ne répond pas":
        failed = _failed_hardware(profile, hardware_failed)
        return (f"Profil « {profile.label} » impossible : le pilote graphique ou le GPU ne répond pas "
                f"({', '.join(failed) or 'test matériel'}). Vérifiez le pilote du GPU, "
                "ou choisissez un profil processeur.")
    manque = [" ou ".join(c) for c in (profile.video_encoders, profile.audio_encoders)
              if c and not any(e in encoders for e in c)]
    return f"Profil « {profile.label} » impossible avec ce FFmpeg : encodeur manquant ({' ; '.join(manque)})."


# ── Travaux et chemins de sortie ─────────────────────────────────────────────
@dataclass
class Job:
    index: int                      # indice dans la file (= ligne du tableau)
    item: SourceItem
    profile: Profile
    dest: Path | None = None        # None : dossier de sortie à choisir
    copy_dest: Path | None = None   # emplacement de la copie de la source (anti-grossissement)
    state: str = STATE_PENDING
    message: str = ""
    passthrough: bool = False       # déjà au format final (AVIF / WebM) : copié sans recompression
    final: Path | None = None       # fichier réellement produit (dest, ou copie de la source)
    replace_existing: bool = False  # sortie créée par un passage précédent : remplacement atomique permis
    replace_size: int | None = None # taille attendue de cette sortie avant remplacement


def is_passthrough(item: SourceItem, profile: Profile) -> bool:
    """Un AVIF vers un profil AVIF (ou un WebM vers un profil WebM AV1) est copié, pas ré-encodé.

    Pour la vidéo, la copie n'est proposée qu'avec l'audio Opus : c'est le codec audio habituel des WebM AV1
    et le seul que ce raccourci peut garantir sans analyser la source.
    """
    if item.kind == OTHER_KIND:
        return True                                         # ni image, ni vidéo, ni audio : copie pure
    if profile.id == "image_heic" and item.path.suffix.lower() in imaging.HEIF_EXTS:
        return True                                         # HEIC/HEIF déjà au format demandé
    if not (profile.kind in ("image", "video") and family(profile) == "av1"
            and item.path.suffix.lower() == profile.out_ext):
        return False
    return profile.kind == "image" or profile.audio_encoders == ("libopus",)


def default_output_dir(root: Path) -> Path:
    """<source>_Converted, voisin de la source."""
    return root.parent / f"{root.name}_Converted"


def part_path(dest: Path) -> Path:
    """Fichier temporaire imprévisible `.<nom>.<aléa>.part.<ext>`.

    L'extension réelle permet à ffmpeg de choisir le format ; le suffixe aléatoire empêche un autre processus
    de préparer un lien symbolique à cet emplacement (voir AZ-010).
    """
    dest = Path(dest)
    # « % » : l'écriteur d'images de FFmpeg y lit un motif de séquence (« photo%03d.webp » → photo001.webp) et
    # le fichier temporaire serait introuvable. Le nom publié, lui, garde son « % » (il ne passe pas par FFmpeg).
    stem = dest.stem.replace("%", "_")
    # Les systèmes de fichiers limitent un nom à 255 octets : le préfixe « . » et le suffixe « .<aléa>.part » en
    # ajoutent une vingtaine à un nom déjà proche de la limite (noms Takeout, accents, caractères asiatiques).
    budget = 235 - len(dest.suffix.encode("utf-8")) - 19
    raw = stem.encode("utf-8")
    if len(raw) > budget:
        stem = raw[:max(budget, 8)].decode("utf-8", errors="ignore")
    return dest.with_name(f".{stem}.{uuid.uuid4().hex[:12]}.part{dest.suffix}")


def initial_state(item: SourceItem, profile: Profile, encoders: Collection[str] | None,
                  hardware_failed: Collection[str] | None = None) -> str:
    """État prévisible sans toucher au disque."""
    if is_passthrough(item, profile):
        return STATE_PENDING                              # copie pure : aucun encodeur nécessaire
    if item.path.suffix.lower() in RAW_EXTS and not imaging.raw_available():
        return STATE_UNSUPPORTED
    if missing_encoder_message(profile, encoders, hardware_failed):
        return STATE_NO_ENCODER
    return STATE_PENDING


def _output_base(item: SourceItem, out_dir: Path | None, multi_roots: bool) -> Path | None:
    if item.root is None:
        return out_dir
    if out_dir is None:
        return default_output_dir(item.root)
    return out_dir / item.root.name if multi_roots else out_dir


def _claim(dest: Path, taken: set[str]) -> Path:
    """Premier nom libre dans le lot (même schéma que unique_path : nom__1.ext)."""
    cand, i = dest, 0
    while path_key(cand) in taken:
        i += 1
        cand = dest.with_name(f"{dest.stem}__{i}{dest.suffix}")
    taken.add(path_key(cand))
    return cand


def _already_done(job: Job, base: Path | None = None, manifest: dict | None = None) -> tuple[bool, bool]:
    """(déjà fait, remplacer la sortie existante).

    Sans manifeste (sortie créée avant cette version, ou par un autre outil), le comportement historique
    est conservé. Avec manifeste, un profil ou une source différente demande une reconversion ; le
    remplacement n'est autorisé que si la sortie est toujours exactement celle que nous avions écrite.
    """
    try:
        if job.dest is not None and job.dest.exists() and job.dest.stat().st_size > 0:
            size = job.dest.stat().st_size
            rec = manifest.get(manifest_key(job.dest, base)) if manifest and base is not None else None
            if not isinstance(rec, dict):
                return True, False                      # compatibilité avec les sorties sans manifeste
            same_dest = rec.get("dest_size") == size
            try:
                same_source = (rec.get("source_size") == job.item.size
                               and abs(float(rec.get("source_mtime", 0)) - job.item.mtime)
                               <= MANIFEST_MTIME_TOLERANCE)
            except (TypeError, ValueError):
                same_source = False
            if rec.get("profile") == profile_fingerprint(job.profile) and same_source and same_dest:
                return True, False
            if same_dest:
                job.replace_size = size
                return False, True
            return False, False
        cp = job.copy_dest
        if (job.profile.guard_growth and cp is not None and path_key(cp) != path_key(job.item.path)
                and cp.exists() and cp.stat().st_size == job.item.size):
            return True, False     # source déjà conservée lors d'un passage précédent
    except OSError as exc:
        _log.debug("Vérification de la copie impossible (%s) : %s", job.item.path, exc)
    return False, False


def output_name(item: SourceItem, profile: Profile, *, name_ext: bool, passthrough: bool) -> Path:
    """Chemin relatif de sortie. `name_ext` : « photo_jpg.avif » (l'extension d'origine reste lisible, ce qui
    évite les collisions photo.jpg / photo.png et permet de retrouver le JSON Takeout) ; sinon « photo.avif ».
    Un fichier copié (passthrough) garde son nom."""
    rel = item.rel
    if passthrough:
        return rel
    if name_ext and item.path.suffix:
        return rel.with_name(f"{rel.stem}_{item.path.suffix.lstrip('.').lower()}{profile.out_ext}")
    return rel.with_suffix(profile.out_ext)


def plan_jobs(items: list[SourceItem], profiles: dict[str, Profile], out_dir: Path | None,
              encoders: Collection[str] | None = None, *, check_disk: bool = True,
              name_ext: bool = False, manifests: dict[Path, dict] | None = None,
              hardware_failed: Collection[str] | None = None) -> list[Job]:
    """Une liste de Job alignée sur `items` : chemins de sortie, collisions, états initiaux.

    Les collisions de noms dans un lot sont résolues de façon déterministe (ordre alphabétique des sources),
    donc un second passage retrouve les mêmes noms et reconnaît les fichiers « Déjà converti ».
    """
    multi = len({str(i.root) for i in items if i.root is not None}) > 1
    taken: set[str] = {path_key(i.path) for i in items}
    jobs: list[Job] = [Job(n, it, profiles.get(it.kind, COPY_PROFILE)) for n, it in enumerate(items)]
    for n in sorted(range(len(items)), key=lambda k: str(items[k].path).lower()):
        job = jobs[n]
        state = initial_state(job.item, job.profile, encoders, hardware_failed)
        if state != STATE_PENDING:
            job.state = state
            if state == STATE_NO_ENCODER:
                job.message = missing_encoder_message(job.profile, encoders, hardware_failed) or ""
            else:
                job.message = "RAW : installez rawpy (pip install rawpy) pour le prendre en charge."
            continue
        base = _output_base(job.item, out_dir, multi)
        if base is None:
            continue
        job.passthrough = is_passthrough(job.item, job.profile)
        dest = _claim(base / output_name(job.item, job.profile, name_ext=name_ext, passthrough=job.passthrough),
                      taken)
        job.dest = dest
        job.copy_dest = dest.with_name(job.item.path.name)
        if check_disk:
            manifest = (manifests or {}).get(base)
            done, replace = _already_done(job, base, manifest)
            if done:
                job.state = STATE_SKIPPED
            elif replace:
                job.replace_existing = True
    return jobs


# ── Commandes ffmpeg / ffprobe ───────────────────────────────────────────────
def build_encoders_command(ffmpeg: str | Path) -> list[str]:
    return [str(ffmpeg), "-hide_banner", "-encoders"]


def build_probe_command(ffprobe: str | Path, source: str | Path) -> list[str]:
    return [str(ffprobe), *SAFE_INPUT_FLAGS, "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", input_spec(source)]


def _hardware_setup_args(profile: Profile) -> list[str]:
    """Options globales d'initialisation du backend matériel."""
    hw = hardware_kind(profile)
    if hw == "qsv":
        return ["-init_hw_device", "qsv=hw", "-filter_hw_device", "hw"]
    if hw == "vaapi":
        device = find_vaapi_device()
        return ["-vaapi_device", device] if device else []
    return []


def _hardware_filter(profile: Profile, chain: str) -> str:
    """Ajoute l'upload GPU requis par QSV et VA-API à la chaîne de filtres logicielle."""
    hw = hardware_kind(profile)
    if hw == "qsv":
        return f"{chain},format=nv12,hwupload=extra_hw_frames=64"
    if hw == "vaapi":
        return f"{chain},format=nv12,hwupload"
    return chain


def _video_quality_args(profile: Profile, venc: str, *, still: bool, lp_ok: bool = True) -> list[str]:
    n = profile.threads if profile.threads > 0 else 0
    threads = ["-threads", str(n)] if n else []
    if venc.endswith("_nvenc"):
        args = ["-rc", "vbr", "-cq", str(profile.crf), "-b:v", "0"]
        return args + (["-preset", profile.preset] if profile.preset else [])
    if venc.endswith("_qsv"):
        args = ["-global_quality", str(profile.crf)]
        return args + (["-preset", profile.preset] if profile.preset else [])
    if venc.endswith("_vaapi"):
        return ["-qp", str(profile.crf)]
    if venc == "libsvtav1":
        args = ["-crf", str(profile.crf), "-preset", profile.preset]
        if still:
            args += ["-b:v", "0"]
        return args + (["-svtav1-params", f"lp={n}"] if n and lp_ok else [])    # lp = niveau de parallélisme
    if venc == "libaom-av1":
        cpu = str(min(int(profile.preset or 4), 8))
        return (["-crf", str(profile.crf), "-b:v", "0", "-cpu-used", cpu] + ([] if still else ["-row-mt", "1"])
                + (["-threads", str(n)] if n else []))
    if venc in ("libx264", "libx265"):
        return ["-crf", str(profile.crf), "-preset", profile.preset] + threads
    if venc == "libvpx-vp9":
        return ["-crf", str(profile.crf), "-b:v", "0", "-row-mt", "1",
                "-cpu-used", profile.preset or "2"] + threads
    if venc in ("mpeg4", "mpeg2video"):
        return ["-q:v", str(profile.quality)] + threads
    if venc == "libtheora":
        return ["-q:v", str(profile.quality)]
    if venc == "prores_ks":
        return ["-profile:v", profile.preset or "hq"] + threads
    if venc == "mjpeg":
        return ["-q:v", str(profile.quality)]
    if venc == "libwebp":
        return ["-quality", str(profile.quality)]
    if venc == "png":
        return ["-compression_level", profile.preset or "6"]
    if venc == "libjxl":
        return ["-distance", str(profile.quality), "-effort", profile.preset or "7"]
    if venc == "tiff":
        return ["-compression_algo", profile.preset or "deflate"]
    return []


def _audio_args(profile: Profile, aenc: str, *, plain: bool = False) -> list[str]:
    args = ["-c:a", aenc]
    if profile.audio_bitrate:
        args += ["-b:a", profile.audio_bitrate]
    if profile.kind == "audio" and aenc == "flac" and profile.preset.isdigit():
        args += ["-compression_level", profile.preset]           # FLAC : « preset » = niveau de compression
    if profile.audio_filter and not plain:
        args += ["-af", profile.audio_filter]
    return args


# Messages de FFmpeg qui désignent la liste de dispositions de canaux imposée par `aformat` : une piste AAC dont
# le conteneur ne dit que le nombre de canaux (disposition « unknown ») la fait échouer, alors que le même fichier
# passe très bien sans cette liste (FFmpeg devine alors la disposition ; ordre des canaux vérifié).
CHANNEL_LAYOUT_ERRORS = ("channel layout", "error while filtering", "failed to inject frame",
                         "impossible to convert between the formats")
ENCODER_OPEN_ERRORS = ("could not open encoder", "error while opening encoder")


def needs_plain_audio(stderr: str, *, audio_only: bool = False) -> bool:
    """Vrai si l'échec ressemble à un refus de la liste de dispositions de canaux (reprise sans filtre).

    « Could not open encoder » ne compte que pour un fichier audio ou une ligne qui vise un flux audio : sur une
    vidéo, c'est le plus souvent l'encodeur vidéo (résolution refusée) et retirer le filtre audio n'y changerait rien.
    """
    low = (stderr or "").lower()
    if any(marker in low for marker in CHANNEL_LAYOUT_ERRORS):
        return True
    for line in low.splitlines():
        if any(marker in line for marker in ENCODER_OPEN_ERRORS) and (audio_only or "aost" in line
                                                                       or "audio" in line):
            return True
    return False


EVEN_SCALE = "scale=trunc(iw/2)*2:trunc(ih/2)*2"
MONO_TO_STEREO = "pan=stereo|c0=c0|c1=c0"                    # duplique la voie mono sur gauche et droite
MULTICHANNEL_FILTER = "aformat=channel_layouts=7.1|5.1|quad|stereo"


def _probed_audio_args(profile: Profile, aenc: str, audio: list[dict], *, plain: bool = False) -> list[str]:
    """Pistes décodables mappées explicitement ; Opus copié ; mono des sorties WebM → stéréo.

    Les sorties WebM ne doivent jamais conserver une piste mono : chaque piste mono est
    dupliquée sur les deux voies, même lorsqu'une copie Opus serait normalement possible.
    """
    if not audio:
        return []
    args: list[str] = []
    for s in audio:
        args += ["-map", f"0:{s['index']}"]
    webm = profile.out_ext.lower() == ".webm"
    mono = [i for i, s in enumerate(audio) if int(s.get("channels") or 0) == 1]
    if webm and mono and len(mono) < len(audio):
        # Pistes mixtes : seules les mono sont ré-encodées, les autres gardent leur copie.
        for i, s in enumerate(audio):
            channels = int(s.get("channels") or 0)
            if channels == 1:
                args += [f"-c:a:{i}", aenc]
                if profile.audio_bitrate:
                    args += [f"-b:a:{i}", profile.audio_bitrate]
                args += [f"-filter:a:{i}", MONO_TO_STEREO]
            elif aenc == "libopus" and str(s.get("codec_name")) == "opus":
                args += [f"-c:a:{i}", "copy"]
            else:
                args += [f"-c:a:{i}", aenc]
                if profile.audio_bitrate:
                    args += [f"-b:a:{i}", profile.audio_bitrate]
                chain = MULTICHANNEL_FILTER if aenc == "libopus" and channels >= 2 \
                    else profile.audio_filter
                if chain and not plain:
                    args += [f"-filter:a:{i}", chain]
        return args
    if (aenc == "libopus" and all(str(s.get("codec_name")) == "opus" for s in audio)
            and not (webm and mono)):
        return args + ["-c:a", "copy"]
    args += ["-c:a", aenc]
    if profile.audio_bitrate:
        args += ["-b:a", profile.audio_bitrate]
    channels = max((int(s.get("channels") or 0) for s in audio), default=0)
    if webm and mono:
        args += ["-af", MONO_TO_STEREO]
    elif aenc == "libopus" and channels == 1:
        args += ["-af", MONO_TO_STEREO]
    elif aenc == "libopus" and channels >= 2:
        if not plain:
            args += ["-af", MULTICHANNEL_FILTER]
    elif profile.audio_filter and not plain:
        args += ["-af", profile.audio_filter]
    return args


def _probed_video_filter_args(profile: Profile, probe: MediaProbe, hdr_filter: str | None) -> list[str]:
    """Filtre et étiquettes de couleur ; HDR : tone-mapping vers SDR (« T ») ou 10 bits natif (« H », AV1 logiciel)."""
    even = EVEN_SCALE if profile.even_dims else None
    if probe.hdr_trc and profile.kind == "video":
        if profile.hdr_mode == "H" and family(profile) == "av1" and not hardware_kind(profile):
            vf = even or "null"
            return ["-vf", vf, "-pix_fmt", "yuv420p10le", "-color_primaries", "bt2020",
                    "-color_trc", probe.hdr_trc, "-colorspace", "bt2020nc"]
        from azimut.modules.convert.probe import hdr_to_sdr_filter
        chain = hdr_filter or hdr_to_sdr_filter(probe.hdr_trc, None)
        chain = f"{even},{chain}" if even else chain
        return ["-vf", _hardware_filter(profile, chain),
                "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709"]
    return ["-vf", _hardware_filter(profile, EVEN_VF if profile.even_dims else "format=yuv420p")]


def build_command(profile: Profile, source: Path, output: Path | str, *, ffmpeg: str | Path = "ffmpeg",
                  encoders: Collection[str] | None = None, probe: MediaProbe | None = None,
                  attempt: int = 0, hdr_filter: str | None = None, creation_time: str | None = None,
                  lp_ok: bool = True, first_audio_only: bool = False, plain_audio: bool = False) -> list[str]:
    """Commande ffmpeg complète (progression sur stdout). `output` est normalement part_path(dest).

    Sans `probe` : comportement historique (`-map 0:a?`, `-sn`). Avec `probe` (sonde ffprobe) : pistes audio
    décodables mappées une à une, sous-titres texte conservés, HDR géré. `attempt` choisit les options de
    lecture pour les sources abîmées (0 = normale) ; `first_audio_only` : repli sur la première piste audio ;
    `plain_audio` : sans liste de dispositions de canaux (`aformat`), pour les pistes dont la disposition est inconnue.
    """
    if profile.id == "image_heic":
        raise ValueError("HEIC est encodé par Pillow, pas par FFmpeg")
    venc = pick_encoder(profile.video_encoders, encoders)
    aenc = pick_encoder(profile.audio_encoders, encoders)
    cmd = [str(ffmpeg), "-y", "-hide_banner", "-loglevel", "error", "-nostdin",
           *_hardware_setup_args(profile), *input_flags(attempt, source), "-i", input_spec(source)]
    if profile.kind == "image":
        cmd += ["-map", "0:v:0", "-frames:v", "1"]
        if profile.even_dims:
            cmd += ["-vf", EVEN_VF]
        cmd += ["-c:v", venc, *_video_quality_args(profile, venc, still=True, lp_ok=lp_ok)]
    elif profile.kind == "video" and probe is None:
        cmd += ["-map", "0:V:0", "-map", "0:a?"]
        vf = _hardware_filter(profile, EVEN_VF if profile.even_dims else "format=yuv420p")
        cmd += ["-vf", vf, "-c:v", venc, *_video_quality_args(profile, venc, still=False, lp_ok=lp_ok)]
        cmd += [*_audio_args(profile, aenc, plain=plain_audio), "-sn", "-dn"]
    elif profile.kind == "video":
        audio = probe.audio[:1] if first_audio_only else probe.audio
        cmd += ["-map", "0:V:0"]
        cmd += [*_probed_video_filter_args(profile, probe, hdr_filter), "-c:v", venc,
                *_video_quality_args(profile, venc, still=False, lp_ok=lp_ok)]
        cmd += _probed_audio_args(profile, aenc, audio, plain=plain_audio)
        if probe.subs and profile.sub_codec:
            for s in probe.subs:
                cmd += ["-map", f"0:{s['index']}"]
            cmd += ["-c:s", profile.sub_codec]
        else:
            cmd += ["-sn"]
        cmd += ["-dn"]
    else:
        cmd += ["-vn", "-sn", "-dn", "-map", "0:a:0", *_audio_args(profile, aenc, plain=plain_audio)]
    cmd += ["-map_metadata", "0"]
    if creation_time:
        cmd += ["-metadata", f"creation_time={creation_time}"]
    cmd += [*profile.extra_out, "-progress", "pipe:1", "-nostats", str(output)]
    return cmd


def build_media_probe_command(ffprobe: str | Path, source: str | Path) -> list[str]:
    return probe_cmd(ffprobe, source)


# ── Lecture des sorties ──────────────────────────────────────────────────────
def parse_duration(text: str) -> float | None:
    """Durée en secondes depuis la sortie de ffprobe, ou None."""
    for line in (text or "").splitlines():
        try:
            d = float(line.strip())
        except ValueError:
            continue
        return d if d > 0 else None
    return None


def parse_progress_line(line: str, duration: float | None) -> int | None:
    """Pourcentage (0–100) depuis une ligne `-progress pipe:1`. out_time_ms est en réalité en microsecondes."""
    line = line.strip()
    if line == "progress=end":
        return 100
    for prefix in ("out_time_us=", "out_time_ms="):
        if line.startswith(prefix):
            try:
                us = int(line.split("=", 1)[1])
            except ValueError:
                return None
            if us < 0 or not duration or duration <= 0:
                return None
            return max(0, min(99, int(us / 1_000_000 / duration * 100)))
    return None


def grew(size_in: int, size_out: int) -> bool:
    """Vrai si le résultat est plus gros que la source (anti-grossissement)."""
    return size_in > 0 and size_out > size_in


def classify_failure(stderr: str, ext: str) -> str:
    """État à donner à un échec ffmpeg : encodeur manquant, format non pris en charge ou erreur."""
    low = (stderr or "").lower()
    if "unknown encoder" in low or "encoder not found" in low:
        return STATE_NO_ENCODER
    if ext.lower() in (".heic", ".heif"):
        return STATE_UNSUPPORTED
    if any(k in low for k in ("invalid data found when processing input", "no decoder", "decoder not found",
                              "unknown decoder", "unsupported codec", "could not find codec parameters")):
        return STATE_UNSUPPORTED
    return STATE_ERROR


def stderr_tail(text: str, n: int = 400) -> str:
    s = (text or "").strip()
    return s[-n:]


_ADDR_RE = re.compile(r"0x[0-9a-fA-F]+")


def stderr_digest(text: str, limit: int = 700) -> str:
    """Résumé lisible d'une sortie d'erreur FFmpeg : début ET fin, lignes répétées regroupées.

    FFmpeg écrit la cause réelle d'un échec dans les premières lignes (décodeur, démuxeur) et sa conclusion
    dans les dernières. Ne garder que la queue, comme `stderr_tail`, cachait la cause : on garde donc les deux
    extrémités. Les adresses mémoire (« 0x55a47b2aa700 ») varient d'un appel à l'autre et rendaient les
    répétitions invisibles : elles sont neutralisées avant le regroupement.
    """
    lines: list[str] = []
    counts: list[int] = []
    for raw in (text or "").splitlines():
        line = _ADDR_RE.sub("0x…", raw.strip())
        if not line:
            continue
        if lines and lines[-1] == line:
            counts[-1] += 1
        else:
            lines.append(line)
            counts.append(1)
    merged = [f"{ln} (×{n})" if n > 1 else ln for ln, n in zip(lines, counts, strict=True)]
    out = " | ".join(merged)
    if len(out) <= limit:
        return out
    head_budget = limit // 2
    tail_budget = limit - head_budget
    head: list[str] = []
    used = 0
    for ln in merged:
        if used + len(ln) > head_budget and head:
            break
        head.append(ln)
        used += len(ln) + 3
    tail: list[str] = []
    used = 0
    for ln in reversed(merged[len(head):]):
        if used + len(ln) > tail_budget and tail:
            break
        tail.append(ln)
        used += len(ln) + 3
    tail.reverse()
    omitted = len(merged) - len(head) - len(tail)
    middle = [f"[… {omitted} ligne(s) omise(s) …]"] if omitted > 0 else []
    return " | ".join([*head, *middle, *tail])


NO_FRAME_MARKERS = ("could not open encoder before eof", "nothing was written into output file",
                    "received no packets", "output file is empty")


def failure_hint(stderr: str) -> str | None:
    """Explication en clair d'un échec FFmpeg courant, ou None si la cause n'est pas reconnue."""
    low = (stderr or "").lower()
    if any(k in low for k in NO_FRAME_MARKERS):
        return ("FFmpeg n'a reçu aucun échantillon décodé de ce fichier : il est vide, tronqué, ou son codec "
                "n'est pas décodé par ce FFmpeg. Vérifiez qu'il se lit ailleurs ; sinon conservez l'original.")
    if "decode error rate" in low or "error while decoding" in low:
        return "La source est endommagée (erreurs de décodage) : la conversion n'est pas fiable."
    if "no space left" in low:
        return "Disque plein : libérez de la place dans le dossier de sortie."
    if "permission denied" in low:
        return "Accès refusé : vérifiez les droits du dossier de sortie ou de la source."
    return None
