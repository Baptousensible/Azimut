"""Fenêtre d'Azimut Convert."""
from __future__ import annotations

import html
import os
from pathlib import Path

from dataclasses import replace

from PySide6.QtCore import QCoreApplication, QPoint, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QCloseEvent
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QInputDialog, QLabel, QMenu, QMessageBox, QPushButton, QScrollArea, QSizePolicy,
    QVBoxLayout, QWidget,
)

from azimut.core import binaries
from azimut.core.binaries import BinaryNotFoundError
from azimut.core.fsutil import format_size
from azimut.core.module_spec import ModuleSpec
from azimut.core.prefs import Preferences
from azimut.modules.convert import MODULE, advanced, engine
from azimut.modules.convert.engine import (
    STATE_CANCELLED, STATE_COPIED, STATE_DONE, STATE_ERROR, STATE_KEPT, STATE_NO_ENCODER, STATE_PENDING,
    STATE_RUNNING, STATE_SKIPPED, STATE_UNSUPPORTED, SourceItem, initial_state, path_key,
    profile_unavailable_reason, state_tag,
)
from azimut.modules.convert.codec_tree import CodecPanel
from azimut.modules.convert.profiles import (
    ALL_EXTS, COPY_PROFILE, KIND_LABELS, KINDS, LEGACY_PROFILES, OTHER_KIND, PROFILES, Profile, audio_codecs_for, audio_fits,
    compose_video, default_profile, get_profile,
)
from azimut.modules.convert.workers import (
    ConvertOptions, ConvertWorker, EncoderProbe, ProbeWorker, ScanWorker, VerifyResult, VerifyWorker,
)
from azimut.ui import icons
from azimut.ui.controls import SwitchRow, set_action_available
from azimut.ui.file_dialog import choose_directory, choose_files, media_filter
from azimut.ui.module_window import ModuleWindow
from azimut.ui.theme import ThemeManager, scaled, token
from azimut.ui.widgets import Card, Column, DataTable, DropZone, plain_tooltip

COLUMNS = [
    Column("file", QCoreApplication.translate("ConvertWindow", "Fichier"), width=320, stretch=True),
    Column("kind", QCoreApplication.translate("ConvertWindow", "Type"), width=80),
    Column("size", QCoreApplication.translate("ConvertWindow", "Taille"), width=90, align="right"),
    Column("profile", QCoreApplication.translate("ConvertWindow", "Format et qualité"), width=260),
    Column("state", QCoreApplication.translate("ConvertWindow", "État"), width=150),
    Column("progress", QCoreApplication.translate("ConvertWindow", "Progression"), width=100, align="center"),
]

FILTERS = (
    QCoreApplication.translate("ConvertWindow", "Tous"),
    QCoreApplication.translate("ConvertWindow", "Images"),
    QCoreApplication.translate("ConvertWindow", "Vidéos"),
    QCoreApplication.translate("ConvertWindow", "Audios"),
    QCoreApplication.translate("ConvertWindow", "Fichiers volumineux"),
)
FILTER_LARGE = 4
FILTER_KINDS = {1: "image", 2: "video", 3: "audio"}
STEP_LEGEND = {
    "copy": ("seg_copy", QCoreApplication.translate("ConvertWindow", "gris")),
    "image": ("seg_image", QCoreApplication.translate("ConvertWindow", "bleu")),
    "audio": ("seg_audio", QCoreApplication.translate("ConvertWindow", "violet")),
    "video": ("seg_video", QCoreApplication.translate("ConvertWindow", "orange")),
}
LARGE_MB_DEFAULT = 50
HARDWARE_NAMES = {"nvenc": "NVIDIA NVENC", "qsv": "Intel Quick Sync", "vaapi": "VA-API"}
LARGE_MB_PRESETS = (10, 25, 50, 100, 250, 500, 1024, 5120)


def _threshold_label(mb: int) -> str:
    """Libellé compact du seuil : « 50 Mo », ou « 1 Go » pour les valeurs rondes en Go."""
    if mb >= 1024 and mb % 1024 == 0:
        return f"{mb // 1024} {QCoreApplication.translate('Units', 'Go')}"
    return f"{mb} {QCoreApplication.translate('Units', 'Mo')}"


KIND_TEXT = {
    "image": QCoreApplication.translate("ConvertWindow", "Image"),
    "video": QCoreApplication.translate("ConvertWindow", "Vidéo"),
    "audio": QCoreApplication.translate("ConvertWindow", "Audio"),
    OTHER_KIND: QCoreApplication.translate("ConvertWindow", "Autre"),
}
STATE_TEXT = {
    STATE_PENDING: QCoreApplication.translate("ConvertWindow", "En attente"),
    STATE_RUNNING: QCoreApplication.translate("ConvertWindow", "En cours"),
    STATE_DONE: QCoreApplication.translate("ConvertWindow", "Terminé"),
    STATE_SKIPPED: QCoreApplication.translate("ConvertWindow", "Déjà converti"),
    STATE_NO_ENCODER: QCoreApplication.translate("ConvertWindow", "Encodeur manquant"),
    STATE_UNSUPPORTED: QCoreApplication.translate("ConvertWindow", "Non pris en charge"),
    STATE_ERROR: QCoreApplication.translate("ConvertWindow", "Erreur"),
    STATE_CANCELLED: QCoreApplication.translate("ConvertWindow", "Annulé"),
    STATE_KEPT: QCoreApplication.translate("ConvertWindow", "Source conservée"),
    STATE_COPIED: QCoreApplication.translate("ConvertWindow", "Copié"),
}


def _format_text(profile: Profile) -> str:
    """Colonne « Format et qualité » de la file : « AVIF — CRF 39 · preset 4 »."""
    detail = advanced.describe(profile)
    return f"{profile.label} — {detail}" if detail else profile.label


class SegmentLegend(QLabel):
    """Légende visible des segments de la barre : une pastille par famille et son effectif.

    Elle reprend les libellés et les nombres déjà traduits par le worker ; seule la
    famille en cours est mise en gras et en couleur pleine. Le texte est du HTML
    reconstruit à chaque changement de thème pour suivre les jetons de couleur.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setTextFormat(Qt.TextFormat.RichText)
        self.setWordWrap(True)
        # Une longue légende ne doit pas élargir la fenêtre : elle passe à la ligne.
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._entries: list[tuple[str, str, int]] = []
        self._active: int | None = None
        self.hide()
        ThemeManager.instance().changed.connect(self._render)

    def set_entries(self, entries: list[tuple[str, str, int]]) -> None:
        """Affiche ``(libellé, jeton de couleur, effectif)`` dans l'ordre de la barre."""
        self._entries = [(str(label), str(color), int(count)) for label, color, count in entries]
        self._active = 0 if self._entries else None
        self._render()
        self.setVisible(bool(self._entries))

    def set_active(self, index: int | None) -> None:
        """Met en avant la famille qui occupe la position courante de la barre."""
        index = index if index is not None and 0 <= index < len(self._entries) else None
        if index != self._active:
            self._active = index
            self._render()

    def clear_entries(self) -> None:
        """Revient à l'état de repos : légende vide et masquée."""
        self._entries = []
        self._active = None
        self.clear()
        self.hide()

    def _render(self, *_args: object) -> None:
        parts = []
        for index, (label, color, count) in enumerate(self._entries):
            active = index == self._active
            text_color = token("text") if active else token("muted")
            weight = "bold" if active else "normal"
            # Espaces insécables dans chaque entrée : la pastille ne se sépare jamais
            # de son libellé. Entre les entrées, un espace sécable autorise le retour
            # à la ligne sans couper une famille en deux.
            parts.append(
                f'<span style="color:{token(color)};">●</span>&nbsp;'
                f'<span style="color:{text_color}; font-weight:{weight};">'
                f'{html.escape(label)}&nbsp;{count}</span>')
        self.setText("&nbsp;&nbsp;&nbsp; ".join(parts))
        self.setAccessibleName(" · ".join(f"{label} : {count}" for label, _color, count in self._entries))


class ConvertWindow(ModuleWindow):
    """File d'attente de conversion : format de sortie et qualité réglés par famille de fichiers."""

    def __init__(self, spec: ModuleSpec = MODULE, parent: QWidget | None = None) -> None:
        super().__init__(spec, parent)
        self._items: list[SourceItem] = []
        self._keys: set[str] = set()
        self._encoders: frozenset[str] | None = None
        self._hardware_failed: frozenset[str] = frozenset()
        self._encoders_for: Path | None = None
        # La détection est terminée (réussie ou non). Distinct de `_encoders_for` : celui-ci
        # reste None quand FFmpeg est introuvable, ce qui laisserait « détection en cours » à vie.
        self._probe_done = False
        self._ffmpeg: Path | None = None
        self._ffprobe: Path | None = None
        self._bins_ok = False
        self._pending: list[list[str]] = []
        self._visible: list[int] = []
        self._segment_bounds: list[float] = []
        self.set_run_label(self.tr("Démarrer la conversion"))
        self._migrate_legacy_prefs()
        self._migrate_parallel_prefs()
        self._build_ui()
        self._locate_binaries(log=True)
        self.show_binary_status(("ffmpeg", "ffprobe"))
        # Timer possédé par la fenêtre (et non un singleShot détaché) : une fenêtre fermée
        # avant le tour de boucle ne lance pas une sonde FFmpeg dans le vide.
        self._probe_timer = QTimer(self)
        self._probe_timer.setSingleShot(True)
        self._probe_timer.timeout.connect(self._probe_encoders)
        self._probe_timer.start(0)

    # ── Interface ───────────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        central = QWidget()
        lay = QVBoxLayout(central)
        self.drop = DropZone(self.tr("Glissez ici des fichiers ou des dossiers à convertir\n(ou cliquez pour parcourir)"))
        self.drop.pathsDropped.connect(self.open_paths)
        lay.addWidget(self.drop)

        # Même barre que Rename : filtre d'affichage, seuil des fichiers volumineux,
        # puis les deux actions qui agissent sur la liste.
        bar = QHBoxLayout()
        bar.addWidget(QLabel(self.tr("Afficher :")))
        self.cmb_filter = QComboBox()
        self.cmb_filter.addItems(FILTERS)
        index = int(self.prefs.get("filter_index", 0) or 0)
        self.cmb_filter.setCurrentIndex(index if 0 <= index < len(FILTERS) else 0)
        self.cmb_filter.currentIndexChanged.connect(self._on_filter_changed)
        bar.addWidget(self.cmb_filter)
        raw_mb = self.prefs.get("large_mb", LARGE_MB_DEFAULT)
        try:
            self._large_mb = max(1, min(1_000_000, int(raw_mb or LARGE_MB_DEFAULT)))
        except (TypeError, ValueError):
            self._large_mb = LARGE_MB_DEFAULT
        self.btn_large = QPushButton()
        self.btn_large.setProperty("kind", "secondary")
        self.btn_large.setProperty("hasMenu", True)
        self.btn_large.setMinimumWidth(scaled(112))
        self.btn_large.setAccessibleName(self.tr("Taille minimale des fichiers affichés"))
        self._large_menu = QMenu(self.btn_large)
        self._large_actions: list[tuple[int, QAction]] = []
        for mb in LARGE_MB_PRESETS:
            action = self._large_menu.addAction(_threshold_label(mb))
            action.setCheckable(True)
            action.triggered.connect(lambda _checked=False, value=mb: self._set_large_threshold(value))
            self._large_actions.append((mb, action))
        self._large_menu.addSeparator()
        custom = self._large_menu.addAction(self.tr("Taille personnalisée…"))
        custom.triggered.connect(lambda _checked=False: self._choose_large_threshold())
        self.btn_large.setMenu(self._large_menu)
        self._refresh_large_button()
        bar.addWidget(self.btn_large)
        self.btn_remove = QPushButton(self.tr("Retirer la sélection"))
        self.btn_clear = QPushButton(self.tr("Vider la liste"))
        self.btn_remove.clicked.connect(self._on_remove)
        self.btn_clear.clicked.connect(self._on_clear)
        bar.addWidget(self.btn_remove)
        bar.addWidget(self.btn_clear)
        bar.addStretch(1)
        lay.addLayout(bar)

        self.table = DataTable(COLUMNS)
        self.table.set_empty_text(self.tr("La file est vide : déposez des fichiers ou un dossier ci-dessus."))
        self.table.selectionRowsChanged.connect(lambda _rows: self._refresh_queue_actions())
        lay.addWidget(self.table, 1)
        # Étape en cours et temps restant : sous le tableau, au-dessus de la barre de progression
        # globale qui reste unique pour tout le lot.
        self.stage_label = QLabel()
        self.stage_label.setProperty("role", "muted")
        self.stage_label.setWordWrap(True)
        self.stage_label.hide()
        lay.addWidget(self.stage_label)
        # Légende visible pendant la conversion : les couleurs de la barre ne sont plus
        # réservées à une infobulle, et la famille en cours y est mise en gras.
        self.progress_legend = SegmentLegend()
        lay.addWidget(self.progress_legend)
        self.progress_bar.valueChanged.connect(self._sync_legend_active)
        self._sync_progress_bar_height()
        ThemeManager.instance().changed.connect(self._sync_progress_bar_height)
        lay.addWidget(self.action_bar)
        self.set_content(central)

        side = QWidget()
        sl = QVBoxLayout(side)
        sl.addWidget(self._build_format_card())

        card_o = Card(self.tr("Sortie"))
        self.chk_recursive = SwitchRow(self.tr("Sous-dossiers"))
        self.chk_recursive.setChecked(bool(self.prefs.get("recursive", True)))
        self.chk_recursive.toggled.connect(lambda v: self.prefs.set("recursive", bool(v)))
        self._out_dir = ""
        self.btn_out = QPushButton(self.tr("Sortie…"))
        self.btn_out.setObjectName("OutputButton")
        self.btn_out.setProperty("kind", "secondary")
        self.btn_out.setIconSize(QSize(scaled(16), scaled(16)))
        self.btn_out.setAccessibleName(self.tr("Dossier de sortie"))
        self.btn_out.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.btn_out.clicked.connect(lambda _checked=False: self._choose_output_dir())
        self.btn_out.customContextMenuRequested.connect(self._show_output_menu)
        self._refresh_out_button()
        self.action_layout.insertWidget(0, self.btn_out)
        self.btn_verify = self._make_button(
            self.tr("Vérifier la sortie…"), self._on_verify, "secondary",
            tooltip=self.tr("Après un arrêt forcé ou un plantage : cherche les fichiers incomplets ou corrompus "
                            "du dossier de sortie et propose de les supprimer pour les reconvertir."))
        self.action_layout.insertWidget(1, self.btn_verify)
        ThemeManager.instance().changed.connect(self._refresh_out_button)
        self.chk_name_ext = SwitchRow(self.tr("Garder l'extension d'origine dans le nom"))
        self.chk_name_ext.setToolTip(self.tr("« photo_jpg.avif » au lieu de « photo.avif » : évite les collisions "
                                     "photo.jpg / photo.png et permet de retrouver le JSON Takeout."))
        self.chk_name_ext.setChecked(bool(self.prefs.get("name_ext", True)))
        self.chk_name_ext.toggled.connect(lambda v: self.prefs.set("name_ext", bool(v)))
        self.chk_takeout = SwitchRow(self.tr("Réinjecter date et GPS des JSON Google Takeout"))
        self.chk_takeout.setToolTip(self.tr("Si un JSON compagnon est trouvé à côté du fichier, sa date de prise de vue et "
                                    "sa position sont écrites dans le fichier converti (ExifTool)."))
        self.chk_takeout.setChecked(bool(self.prefs.get("takeout", True)))
        self.chk_takeout.toggled.connect(lambda v: self.prefs.set("takeout", bool(v)))
        card_o.body_layout.addWidget(self.chk_recursive)
        card_o.body_layout.addWidget(self.chk_name_ext)
        self.chk_copy_others = SwitchRow(self.tr("Copier aussi les autres fichiers (PDF, documents…)"))
        self.chk_copy_others.setToolTip(self.tr("Les fichiers qui ne sont ni des images, ni des audios, ni des vidéos sont "
                                        "copiés tels quels dans le dossier de sortie, à la même place que dans la "
                                        "source. Sans cette case, ils sont ignorés."))
        self.chk_copy_others.setChecked(bool(self.prefs.get("copy_others", False)))
        self.chk_copy_others.toggled.connect(self._on_copy_others_toggled)
        self.chk_skip_json = SwitchRow(self.tr("Ne pas recopier les JSON Takeout déjà réinjectés"))
        self.chk_skip_json.setToolTip(self.tr("Les JSON dont la date et la position sont écrites dans le fichier converti "
                                      "ne sont pas copiés dans le dossier de sortie. Rien n'est supprimé : "
                                      "les JSON restent dans le dossier source. Les autres JSON sont copiés."))
        self.chk_skip_json.setChecked(bool(self.prefs.get("skip_takeout_json", False)))
        self.chk_skip_json.toggled.connect(lambda v: self.prefs.set("skip_takeout_json", bool(v)))
        self.chk_takeout.toggled.connect(self._sync_skip_json_enabled)
        self.chk_copy_others.toggled.connect(self._sync_skip_json_enabled)
        card_o.body_layout.addWidget(self.chk_takeout)
        card_o.body_layout.addWidget(self.chk_copy_others)
        card_o.body_layout.addWidget(self.chk_skip_json)
        sl.addWidget(card_o)

        sl.addStretch(1)
        scroll = QScrollArea()                       # le panneau de réglages peut dépasser la hauteur de la fenêtre
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(side)
        # La largeur vient de ModuleWindow : elle est commune à tous les panneaux de réglages.
        self.set_side_panel(scroll, self.tr("Réglages"))
        self._lockables: list[QWidget] = [self.codec_tree, self.chk_recursive, self.btn_out, self.btn_verify,
                                          self.cmb_filter, self.btn_large, self.btn_remove, self.btn_clear,
                                          self.chk_name_ext, self.chk_takeout, self.chk_copy_others,
                                          self.chk_skip_json]
        self._sync_skip_json_enabled()
        self._update_large_filter()
        self._annotate_profiles()
        self._load_advanced()
        self._refresh_queue_actions()
        self._refresh_hardware_status()

    # ── Format et qualité ───────────────────────────────────────────────────
    def _build_format_card(self) -> QWidget:
        """Panneau unique, sans titre de carte : les sections Images / Vidéos / Audio portent la hiérarchie."""
        card = Card()
        # L'en-tête retiré laisse toute la place au panneau : marges égales pour qu'il respire.
        card.layout().setContentsMargins(16, 16, 16, 16)
        tree = self.codec_tree = CodecPanel()
        card.body_layout.addWidget(tree)
        for kind in KINDS:
            tree.set_selected(kind, str(self.prefs.get(f"profile_{kind}", "")))
        tree.set_video_audio(str(self.prefs.get("profile_video_audio", "")))
        tree.set_hdr_mode(str(self.prefs.get("hdr_mode", "T")))
        tree.slider_threads.set_value(advanced.clamp_threads(self.prefs.get("threads", 0)))
        tree.set_parallel(str(self.prefs.get("parallel_mode", "auto")),
                          {kind: self.prefs.get(f"parallel_{kind}", 1) for kind in advanced.PARALLEL_KINDS})
        tree.chk_low_priority.setChecked(bool(self.prefs.get("low_priority", True)))
        tree.chk_memory.setChecked(bool(self.prefs.get("memory_guard", True)))
        tree.codecChanged.connect(self._on_codec_changed)
        tree.videoAudioChanged.connect(self._on_video_audio_changed)
        tree.qualityEdited.connect(self._on_quality_edited)
        tree.resetRequested.connect(self._reset_quality)
        tree.hdrChanged.connect(self._on_hdr_changed)
        tree.performanceChanged.connect(self._on_perf_changed)
        return card

    def _migrate_legacy_prefs(self) -> None:
        """Reprend le choix et les réglages enregistrés avec les anciens profils (AVIF « HQ », WebM « max »…)."""
        if self.prefs.get("repair_mono"):
            self.prefs.set("repair_mono", False)          # ancienne option supprimée
        selected = {k: str(self.prefs.get(f"profile_{k}", "") or "") for k in KINDS}
        store = self._overrides_store()
        if not any(pid in LEGACY_PROFILES for pid in (*selected.values(), *store)):
            self._migrate_video_audio_overrides(store)
            self._migrate_video_bitrate(store)
            self.prefs.set("advanced", store)
            return
        new_sel, new_store = advanced.migrate_legacy(selected, store)
        for kind, pid in new_sel.items():
            if pid != selected[kind]:
                self.prefs.set(f"profile_{kind}", pid)
        self._migrate_video_audio_overrides(new_store)
        self._migrate_video_bitrate(new_store)
        self.prefs.set("advanced", new_store)

    def _migrate_parallel_prefs(self) -> None:
        """Reprend l'ancien réglage unique « En parallèle » : au-delà de 1, il devient manuel pour les quatre familles."""
        if self.prefs.get("parallel_mode") is not None:
            return
        self.prefs.set("parallel_mode", "auto")
        legacy = self.prefs.get("parallel", None)
        if legacy is None:
            return
        count = advanced.clamp_parallel(legacy)
        if count <= 1:
            return
        self.prefs.set("parallel_mode", "manual")
        for kind in advanced.PARALLEL_KINDS:
            self.prefs.set(f"parallel_{kind}", advanced.clamp_parallel(count, kind))

    def _migrate_video_audio_overrides(self, store: dict) -> None:
        """Sépare les réglages historiquement partagés : la vidéo garde une copie de la famille Audio."""
        if bool(self.prefs.get("video_audio_split", False)):
            return
        for profile in PROFILES:
            key = advanced.video_audio_key(profile.id)
            if profile.kind == "audio" and key not in store and isinstance(store.get(profile.id), dict):
                store[key] = dict(store[profile.id])
        self.prefs.set("video_audio_split", True)

    def _migrate_video_bitrate(self, store: dict) -> None:
        """Le débit audio était réglé sur le profil vidéo : il appartient maintenant au codec audio choisi."""
        try:
            video = get_profile(str(self.prefs.get("profile_video", "") or ""))
        except KeyError:
            return
        saved = store.get(video.id)
        if not isinstance(saved, dict) or "bitrate" not in saved:
            return
        audio = audio_codecs_for(video)[0]
        value = saved.pop("bitrate")
        key = advanced.video_audio_key(audio.id)
        if value is not None:
            store[key] = {**dict(store.get(key) or {}), "bitrate": value}
        if not saved:
            store.pop(video.id, None)
        self.prefs.set("profile_video_audio", audio.id)

    def _profile(self, kind: str) -> Profile:
        """Profil effectif : codec choisi + curseurs de qualité + threads (vidéo : codec image + codec audio)."""
        base = self._base_profile(kind)
        store = self._overrides_store()
        threads = advanced.clamp_threads(self.prefs.get("threads", 0))
        eff = advanced.apply_overrides(base, advanced.Overrides.from_dict(store.get(base.id)), threads,
                                       self._encoders)
        if kind == "video":
            audio = self._base_audio_for_video()
            audio = advanced.apply_overrides(audio, advanced.Overrides.from_dict(
                store.get(advanced.video_audio_key(audio.id))), 0,
                                             self._encoders)
            eff = compose_video(eff, audio)
        if eff.kind == "video":
            eff = replace(eff, hdr_mode=str(self.prefs.get("hdr_mode", "T")))
        return eff

    def _base_audio_for_video(self) -> Profile:
        """Codec audio choisi pour la vidéo, ou le premier compatible avec ce codec."""
        video = self._base_profile("video")
        pid = self.codec_tree.selected("video_audio")
        if pid:
            try:
                audio = get_profile(pid)
            except KeyError:
                audio = None
            if audio is not None and audio.kind == "audio" and audio_fits(video, pid):
                return audio
        return audio_codecs_for(video)[0]

    def _profiles(self) -> dict[str, Profile]:
        profiles = {k: self._profile(k) for k in KINDS}
        profiles[OTHER_KIND] = COPY_PROFILE
        return profiles

    def _annotate_profiles(self) -> None:
        """Marque dans les listes les codecs impossibles avec ce ffmpeg."""
        for p in PROFILES:
            # L'audio a sa propre liste : ne pas le compter pour la disponibilité du codec vidéo.
            check = replace(p, audio_encoders=()) if p.kind == "video" else p
            reason = profile_unavailable_reason(check, self._encoders, self._hardware_failed)
            self.codec_tree.set_codec_available(p.id, reason is None, reason or "encodeur manquant")

    def _overrides_store(self) -> dict:
        data = self.prefs.get("advanced", {})
        return dict(data) if isinstance(data, dict) else {}

    def _base_profile(self, kind: str) -> Profile:
        try:
            return get_profile(self.codec_tree.selected(kind))
        except KeyError:
            return default_profile(kind)

    def _load_advanced(self) -> None:
        """Remplit les curseurs de chaque codec avec ses valeurs d'origine ou personnalisées."""
        store = self._overrides_store()
        for base in PROFILES:
            ov = advanced.Overrides.from_dict(store.get(base.id))
            eff = advanced.apply_overrides(base, ov, 0, self._encoders)
            video_audio_eff = None
            if base.kind == "audio":
                video_audio_eff = advanced.apply_overrides(
                    base, advanced.Overrides.from_dict(store.get(advanced.video_audio_key(base.id))),
                    0, self._encoders)
            self.codec_tree.configure(base, eff, self._encoders, video_audio_eff)

    def _on_quality_edited(self, profile_id: str) -> None:
        if profile_id.startswith(advanced.VIDEO_AUDIO_PREFIX):
            base = get_profile(profile_id[len(advanced.VIDEO_AUDIO_PREFIX):])
            overrides = self.codec_tree.read_video_audio_overrides(base.id)
        else:
            base = get_profile(profile_id)
            overrides = self.codec_tree.read_overrides(profile_id)
        diff = advanced.overrides_diff(base, overrides)
        store = self._overrides_store()
        if diff:
            store[profile_id] = diff
        else:
            store.pop(profile_id, None)
        self.prefs.set("advanced", store)
        self._refresh_states()

    def _reset_quality(self, profile_id: str) -> None:
        store = self._overrides_store()
        store.pop(profile_id, None)
        self.prefs.set("advanced", store)
        self._load_advanced()
        self._refresh_states()

    def _on_codec_changed(self, kind: str, profile_id: str) -> None:
        self.prefs.set(f"profile_{kind}", profile_id)
        if kind == "video":
            self.prefs.set("profile_video_audio", self.codec_tree.selected("video_audio"))
        self._refresh_states()

    def _on_video_audio_changed(self, profile_id: str) -> None:
        if profile_id:
            self.prefs.set("profile_video_audio", profile_id)
            self._refresh_states()

    def _on_hdr_changed(self, mode: str) -> None:
        self.prefs.set("hdr_mode", mode)
        self._refresh_states()

    def _on_perf_changed(self) -> None:
        tree = self.codec_tree
        self.prefs.set("threads", int(tree.slider_threads.value()))
        self.prefs.set("parallel_mode", tree.parallel_mode())
        for kind, value in tree.parallel_values().items():
            self.prefs.set(f"parallel_{kind}", int(value))
        self.prefs.set("low_priority", bool(tree.chk_low_priority.isChecked()))
        self.prefs.set("memory_guard", bool(tree.chk_memory.isChecked()))
        self._refresh_states()

    # ── Binaires et encodeurs ───────────────────────────────────────────────
    def _locate_binaries(self, *, log: bool) -> bool:
        found: dict[str, Path] = {}
        for name in ("ffmpeg", "ffprobe"):
            try:
                found[name] = binaries.require(name)
            except BinaryNotFoundError as exc:
                if log:
                    self.log_panel.append_log("ERR", str(exc))
        self._ffmpeg, self._ffprobe = found.get("ffmpeg"), found.get("ffprobe")
        self._bins_ok = len(found) == 2
        if not self._bins_ok and log:
            self.log_panel.append_log("WARN", "La conversion est désactivée tant que FFmpeg est introuvable "
                                              "(Préférences… pour indiquer son chemin).")
        if hasattr(self, "codec_tree"):
            if self._bins_ok:
                self._refresh_hardware_status()
            else:
                self.codec_tree.set_hardware_status(
                    self.tr("FFmpeg introuvable : matériel GPU indéterminable."))
        self._refresh_run_state()
        return self._bins_ok

    def _available_backends(self) -> list[tuple[str, list[str]]]:
        if not self._encoders:
            return []
        return engine.hardware_backends(self._encoders - self._hardware_failed)

    def _hardware_text(self) -> str:
        """Matériel détecté et codecs disponibles, affiché au-dessus de la liste des codecs vidéo."""
        if self._encoders is None:
            if not self._probe_done:
                return self.tr("Matériel : détection en cours…")
            return self.tr("Matériel : indéterminé (FFmpeg n'a pas listé ses encodeurs).")

        def describe(backends: list[tuple[str, list[str]]]) -> str:
            return " · ".join(f"{HARDWARE_NAMES.get(backend, backend)} ({', '.join(codecs)})"
                             for backend, codecs in backends)

        available = self._available_backends()
        failed = engine.hardware_backends(self._hardware_failed)
        if failed:
            if available:
                return self.tr("GPU : {backends} · pilote ou GPU ne répond pas : {failed}").format(
                    backends=describe(available), failed=describe(failed))
            return self.tr("GPU détecté mais le pilote ou le GPU ne répond pas : {failed} ; les profils "
                           "marqués GPU resteront indisponibles.").format(failed=describe(failed))
        if not available:
            return self.tr("Aucune accélération GPU détectée : les profils marqués GPU resteront "
                           "indisponibles, la conversion se fait par le processeur.")
        return self.tr("GPU : {backends}").format(backends=describe(available))

    def _has_gpu_hardware(self) -> bool:
        return bool(self._available_backends())

    def _refresh_hardware_status(self) -> None:
        tree = getattr(self, "codec_tree", None)
        if tree is not None:
            tree.set_hardware_status(self._hardware_text())
            tree.set_quality_hint_visible(self._has_gpu_hardware())

    def _probe_encoders(self) -> None:
        if not self._bins_ok or self._ffmpeg is None:
            return
        if self.is_busy():
            self._probe_timer.start(100)
            return
        self._probe_done = False
        self.run_worker(ProbeWorker(self._ffmpeg), on_completed=self._on_encoders,
                        on_failed=self._on_probe_failed, on_cancelled=self._after_worker)

    def _on_probe_failed(self, _message: str) -> None:
        """Détection impossible : le matériel reste indéterminé au lieu d'afficher « en cours » à vie."""
        self._encoders_for = self._ffmpeg
        self._probe_done = True
        self._refresh_hardware_status()
        self._after_worker()

    def _on_encoders(self, result: EncoderProbe) -> None:
        self._encoders, self._hardware_failed = result.encoders, result.hardware_failed
        self._encoders_for = self._ffmpeg
        self._probe_done = True
        self.codec_tree.set_visible_encoders(self._encoders)
        self._annotate_profiles()
        self._load_advanced()
        self._refresh_states()
        self._refresh_hardware_status()
        if self._encoders is not None:
            bad = [p.label for p in PROFILES
                   if self.codec_tree.profile_is_visible(p)
                   and profile_unavailable_reason(p, self._encoders, self._hardware_failed)]
            if bad:
                self.log_panel.append_log("WARN", "Formats indisponibles avec ce FFmpeg : " + " ; ".join(bad) + ".")
            else:
                self.log_panel.append_log("INFO", f"{len(self._encoders)} encodeurs détectés : tous les formats sont disponibles.")
        self._after_worker()

    # ── Gestion de la file ──────────────────────────────────────────────────
    def _matches_filter(self, item: SourceItem) -> bool:
        index = self.cmb_filter.currentIndex()
        if index == FILTER_LARGE:
            return item.size >= self._large_mb * 1024 * 1024
        return item.kind == FILTER_KINDS.get(index, item.kind)

    def _render_queue(self) -> None:
        """Applique le filtre d'affichage et reconstruit les lignes visibles."""
        self._visible = [i for i, item in enumerate(self._items) if self._matches_filter(item)]
        profiles = self._profiles()
        self.table.data_model.set_rows([
            self._row_for(self._items[i], profiles,
                          initial_state(self._items[i], profiles[self._items[i].kind], self._encoders,
                                        self._hardware_failed))
            for i in self._visible])
        self._refresh_queue_actions()

    def _refresh_queue_actions(self) -> None:
        """« Retirer » suit la sélection ; « Vider » n'est actif que si la file contient quelque chose."""
        if getattr(self, "btn_remove", None) is None:
            return
        busy = self.is_busy()
        set_action_available(self.btn_remove, bool(self.table.selected_rows()) and not busy)
        set_action_available(self.btn_clear, bool(self._items) and not busy)
        self._refresh_run_state()

    def _refresh_run_state(self) -> None:
        """« Démarrer la conversion » n'est actif que si la file et les prérequis sont prêts."""
        output = self._out_dir.strip()
        isolated = any(item.root is None for item in self._items)
        output_ok = not isolated or (bool(output) and not Path(
            os.path.abspath(os.path.expanduser(output))).is_file())
        self.set_run_enabled(bool(self._items) and self._bins_ok and output_ok)

    def _view_row(self, item_index: int) -> int | None:
        """Ligne affichée pour un fichier de la file, ou None s'il est masqué par le filtre."""
        try:
            return self._visible.index(item_index)
        except ValueError:
            return None

    def _on_filter_changed(self, _index: int) -> None:
        self.prefs.set("filter_index", self.cmb_filter.currentIndex())
        self._update_large_filter()
        self._render_queue()

    def _update_large_filter(self) -> None:
        active = self.cmb_filter.currentIndex() == FILTER_LARGE
        self.btn_large.setVisible(active)
        self.btn_large.setEnabled(active and not self.is_busy())

    def _refresh_large_button(self) -> None:
        size = _threshold_label(self._large_mb)
        self.btn_large.setText(self.tr("≥ {size}").format(size=size))
        self.btn_large.setToolTip(self.tr(
            "Afficher les fichiers d'au moins {size}. Cliquez pour choisir un autre seuil.").format(size=size))
        for mb, action in self._large_actions:
            action.setChecked(mb == self._large_mb)

    def _set_large_threshold(self, value: int) -> None:
        self._large_mb = max(1, min(1_000_000, int(value)))
        self.prefs.set("large_mb", self._large_mb)
        self._refresh_large_button()
        if self.cmb_filter.currentIndex() == FILTER_LARGE:
            self._render_queue()

    def _choose_large_threshold(self) -> None:
        value, ok = QInputDialog.getInt(
            self, self.tr("Taille minimale"), self.tr("Afficher les fichiers d'au moins (en Mo) :"),
            self._large_mb, 1, 1_000_000, 1)
        if ok:
            self._set_large_threshold(value)

    def _row_for(self, item: SourceItem, profiles: dict[str, Profile], state: str) -> dict:
        shown = item.path.name if item.root is None else f"{item.root.name}/{item.rel.as_posix()}"
        return {"file": shown, "kind": KIND_TEXT.get(item.kind, KIND_LABELS[item.kind]), "size": format_size(item.size),
                "_sort_size": item.size,
                "profile": _format_text(profiles[item.kind]), "state": STATE_TEXT.get(state, state),
                "progress": "", "_tag": state_tag(state)}

    def _refresh_states(self) -> None:
        if self.is_busy():
            return
        self._render_queue()

    def open_paths(self, paths: list[str]) -> None:
        paths = [str(p) for p in paths if p]
        if not paths:
            return
        if self.is_busy():
            self._pending.append(paths)
            self.log_panel.append_log("INFO", "Traitement en cours : l'ajout sera effectué ensuite.")
            return
        self._start_scan(paths)

    def _start_scan(self, paths: list[str]) -> None:
        worker = ScanWorker(paths, self.chk_recursive.isChecked(),
                            include_others=self.chk_copy_others.isChecked())
        self.run_worker(worker, on_completed=self._on_scanned,
                        on_failed=lambda _m: self._after_worker(), on_cancelled=self._after_worker,
                        import_progress=self.drop.import_progress)

    def _on_scanned(self, result: object) -> None:
        new: list[SourceItem] = []
        dup = 0
        for it in result if isinstance(result, list) else []:
            key = path_key(it.path)
            if key in self._keys:
                dup += 1
                continue
            self._keys.add(key)
            new.append(it)
        if new:
            if not self._out_dir and any(i.root is None for i in new):
                last = str(self.prefs.get("output_dir", "") or "")
                if last:
                    self._set_output_dir(last)          # fichiers isolés : dernier dossier utilisé
            self._items.extend(new)
            self._render_queue()
        self.log_panel.append_log("INFO", f"{len(new)} fichier(s) ajouté(s) à la file"
                                          + (f" ({dup} déjà présent(s))." if dup else "."))
        self._after_worker()

    def _sync_skip_json_enabled(self, *_args: object) -> None:
        """La case « ne pas recopier les JSON » n'a de sens que si les JSON sont lus ET les autres fichiers copiés."""
        if self.is_busy():
            return
        self.chk_skip_json.setEnabled(self.chk_takeout.isChecked() and self.chk_copy_others.isChecked())

    def _on_copy_others_toggled(self, checked: bool) -> None:
        """Décochée : retire de la file les fichiers « autres » ; cochée : les ajoute depuis les dossiers déjà ajoutés."""
        self.prefs.set("copy_others", bool(checked))
        if self.is_busy():
            return
        if checked:
            roots = sorted({str(i.root) for i in self._items if i.root is not None})
            if roots:
                self._start_scan(roots)
            return
        keep = [n for n, it in enumerate(self._items) if it.kind != OTHER_KIND]
        if len(keep) == len(self._items):
            return
        self._items = [self._items[n] for n in keep]
        self._keys = {path_key(i.path) for i in self._items}
        self._render_queue()

    def _on_remove(self) -> None:
        if self.is_busy():
            return
        sel = set(self.table.selected_rows())
        if not sel:
            return
        drop = {self._visible[i] for i in sel if 0 <= i < len(self._visible)}
        self._items = [it for i, it in enumerate(self._items) if i not in drop]
        self._keys = {path_key(i.path) for i in self._items}
        self._render_queue()

    def _on_clear(self) -> None:
        if self.is_busy() or not self._items:
            return
        if not self.confirm_clear_list():
            return
        self._items.clear()
        self._keys.clear()
        self._visible.clear()
        self.table.data_model.clear()
        self._refresh_queue_actions()

    def _choose_output_dir(self) -> None:
        """Choisit le dossier de sortie ; vide = « <source>_Converted » à côté des sources."""
        if self.is_busy():
            return
        start = self._out_dir or str(self.prefs.get("output_dir", "") or "")
        chosen = choose_directory(self, self.tr("Choisir le dossier de sortie"), start)
        if chosen:
            self._set_output_dir(chosen)

    def _set_output_dir(self, path: str) -> None:
        self._out_dir = str(path or "").strip()
        self.prefs.set("output_dir", self._out_dir)
        self._refresh_out_button()
        self._refresh_run_state()

    def _refresh_out_button(self, _theme: str = "") -> None:
        hint = self.tr("Laisser vide : « <source>_Converted » à côté de chaque dossier ajouté. "
                       "Obligatoire pour les fichiers ajoutés isolément.")
        current = self._out_dir or self.tr("dossier par défaut")
        tip = self.tr("{hint}\n\nDossier de sortie : {path}\n\n"
                      "Cliquez pour choisir un dossier ; clic droit pour revenir au dossier par défaut."
                      ).format(hint=hint, path=current)
        self.btn_out.setToolTip(plain_tooltip(tip))
        self.btn_out.setAccessibleName(self.tr("Dossier de sortie : {path}").format(path=current))
        self.btn_out.setIconSize(QSize(scaled(16), scaled(16)))
        self.btn_out.setIcon(icons.icon("folder", "text", scaled(16)))
        if self._out_dir:
            folder = Path(self._out_dir).name or self._out_dir
            shown = self.btn_out.fontMetrics().elidedText(
                folder, Qt.TextElideMode.ElideMiddle, scaled(180))
            self.btn_out.setText(self.tr("Sortie : {folder}").format(folder=shown))
        else:
            self.btn_out.setText(self.tr("Sortie…"))

    def _show_output_menu(self, pos: QPoint) -> None:
        menu = QMenu(self)
        if self._out_dir:
            shown = self.btn_out.fontMetrics().elidedText(
                self._out_dir, Qt.TextElideMode.ElideMiddle, scaled(320))
            current = menu.addAction(shown)
            current.setEnabled(False)
            current.setToolTip(plain_tooltip(self._out_dir))
            menu.addSeparator()
        choose = menu.addAction(self.tr("Choisir un dossier…"))
        choose.triggered.connect(lambda _checked=False: self._choose_output_dir())
        reset = menu.addAction(self.tr("Utiliser le dossier par défaut"))
        reset.setEnabled(bool(self._out_dir))
        reset.triggered.connect(lambda _checked=False: self._set_output_dir(""))
        menu.exec(self.btn_out.mapToGlobal(pos))

    # ── Import ──────────────────────────────────────────────────────────────
    def on_open(self) -> None:
        prefs = Preferences.instance()
        start = str(prefs.get("last_dir", "") or "")
        patterns = " ".join(f"*{e}" for e in sorted(ALL_EXTS))
        chosen = choose_files(self, self.tr("Importer des fichiers"), start,
                              media_filter(patterns))
        if chosen:
            prefs.set("last_dir", str(Path(chosen[0]).parent))
            self.open_paths(chosen)

    def on_open_folder(self) -> None:
        prefs = Preferences.instance()
        start = str(prefs.get("last_dir", "") or "")
        folder = choose_directory(self, self.tr("Importer un dossier"), start)
        if folder:
            prefs.set("last_dir", folder)
            self.open_paths([folder])

    # ── Conversion ──────────────────────────────────────────────────────────
    def on_run(self) -> None:
        if self.is_busy():
            return
        if not self._items:
            self.log_panel.append_log("WARN", "La file est vide : ajoutez des fichiers ou des dossiers.")
            return
        if not self._locate_binaries(log=True) or self._ffmpeg is None or self._ffprobe is None:
            return
        txt = self._out_dir.strip()
        # Un chemin relatif saisi à la main serait refusé par les fichiers d'arguments ExifTool :
        # on le fixe ici, une fois pour toutes, avant de lancer le worker.
        out_dir = Path(os.path.abspath(os.path.expanduser(txt))) if txt else None
        if out_dir is None and any(i.root is None for i in self._items):
            self.log_panel.append_log("ERR", "Choisissez un dossier de sortie : il est obligatoire pour les "
                                             "fichiers ajoutés isolément.")
            return
        if out_dir is not None and out_dir.exists() and not out_dir.is_dir():
            self.log_panel.append_log("ERR", f"Le dossier de sortie est un fichier : {out_dir}")
            return
        encoders = self._encoders if self._encoders_for == self._ffmpeg else None
        try:
            exiftool = binaries.require("exiftool")
        except BinaryNotFoundError:
            exiftool = None
        tree = self.codec_tree
        options = ConvertOptions(name_ext=self.chk_name_ext.isChecked(), takeout=self.chk_takeout.isChecked(),
                                 skip_takeout_json=self.chk_skip_json.isChecked() and self.chk_skip_json.isEnabled(),
                                 low_priority=tree.chk_low_priority.isChecked(),
                                 memory_guard=tree.chk_memory.isChecked())
        hardware_failed = self._hardware_failed if encoders is not None else frozenset()
        worker = ConvertWorker(self._items, self._profiles(), out_dir, self._ffmpeg, self._ffprobe, encoders,
                               parallel_by_kind=tree.parallel_values(), options=options, exiftool=exiftool,
                               hardware_failed=hardware_failed)
        worker.job_updated.connect(self._on_job_updated)
        worker.status.connect(self._on_stage)
        worker.steps_planned.connect(self._on_segments)
        self.stage_label.setText(self.tr("Préparation de la conversion…"))
        self.stage_label.show()
        self.run_worker(worker, on_completed=lambda _r: self._after_worker(),
                        on_failed=lambda _m: self._after_worker(), on_cancelled=self._on_cancelled,
                        lock_widgets=self._lockables, determinate_start=True, history_action="Conversion")

    def _on_job_updated(self, index: int, values: object) -> None:
        if not isinstance(values, dict) or not 0 <= index < len(self._items):
            return
        row = self._view_row(index)
        if row is None:
            return                                      # ligne masquée par le filtre : rien à peindre
        upd: dict = {}
        if "state" in values:
            upd["state"] = STATE_TEXT.get(str(values["state"]), str(values["state"]))
            upd["_tag"] = state_tag(values["state"])
        if "progress" in values:
            progress = values["progress"]
            # Le tableau garde l'avancement du fichier ; la barre de la barre d'actions, elle, suit
            # la progression globale du lot (0 à 100 % une seule fois) et n'est plus pilotée ici.
            upd["progress"] = "" if progress is None else f"{progress} %"
        if upd:
            self.table.data_model.update_row(row, **upd)

    def _on_stage(self, text: str) -> None:
        """Affiche l'étape en cours et le temps restant estimé (fournis par le worker)."""
        # À fort zoom, « 20 s » ne doit pas se couper entre le nombre et l'unité :
        # l'espace insécable garde la durée lisible quand le libellé passe à la ligne.
        for unit in (" s", " min", " h"):
            text = text.replace(unit, "\u00a0" + unit[1:])
        self.stage_label.setText(text)
        self.stage_label.setVisible(bool(text))

    def _on_segments(self, segments: list[tuple[str, str, int]]) -> None:
        """Découpe la barre selon le nombre de fichiers de chaque famille.

        Les largeurs suivent exactement les effectifs (3 images, 1 audio, 5 vidéos, 1 copie
        donnent 30 %, 10 %, 50 % et 10 %), et non la taille des fichiers.
        """
        if not segments:
            self.progress_bar.clear_segments()
            self.progress_bar.setToolTip("")
            self.progress_legend.clear_entries()
            self._segment_bounds = []
            return
        self.progress_bar.set_segments([(count, STEP_LEGEND[key][0]) for key, _label, count in segments])
        self.progress_legend.set_entries([(label, STEP_LEGEND[key][0], count) for key, label, count in segments])
        total = sum(count for _key, _label, count in segments) or 1
        running = 0.0
        self._segment_bounds = []
        for _key, _label, count in segments:
            running += count
            self._segment_bounds.append(running / total)
        lines = "\n".join(f"{label} ({STEP_LEGEND[key][1]}) : {count}" for key, label, count in segments)
        self.progress_bar.setToolTip(self.tr(
            "Légende des couleurs :\n{lines}\n\n"
            "Chaque segment est proportionnel au nombre de fichiers à traiter.").format(lines=lines))
        self.progress_legend.setToolTip(self.progress_bar.toolTip())
        self._sync_legend_active(self.progress_bar.value())

    def _sync_legend_active(self, value: int) -> None:
        """Suit la famille sous le front de la barre pour la mettre en avant dans la légende."""
        if not self._segment_bounds:
            return
        span = self.progress_bar.maximum() - self.progress_bar.minimum()
        if span <= 0:
            return
        ratio = (float(value) - self.progress_bar.minimum()) / span
        active = next((n for n, end in enumerate(self._segment_bounds) if ratio < end),
                      len(self._segment_bounds) - 1)
        self.progress_legend.set_active(active)

    def _sync_progress_bar_height(self, *_args: object) -> None:
        """Donne à la piste la place du pourcentage, même au zoom maximal.

        La feuille de style globale fige les QProgressBar à 12 px : à 125 % et 150 %,
        le texte déborderait au-dessus et au-dessous de la piste. Un style local, plus
        spécifique, suit le zoom et la hauteur de police sans toucher aux autres modules.
        """
        height = max(scaled(20), self.progress_bar.fontMetrics().height() + scaled(4))
        self.progress_bar.setStyleSheet(
            f"QProgressBar {{ min-height: {height}px; max-height: {height}px; }}")

    def _on_cancelled(self) -> None:
        for i, row in enumerate(self.table.data_model.rows()):
            if row.get("state") == STATE_RUNNING:
                self.table.data_model.update_row(i, state=STATE_TEXT.get(STATE_CANCELLED, STATE_CANCELLED),
                                                 _tag=state_tag(STATE_CANCELLED))
        self._after_worker()

    # ── Vérification de la sortie (arrêt forcé, plantage) ───────────────────
    def _output_bases(self) -> list[Path]:
        """Dossiers de sortie de la file, calculés comme le fait le worker de conversion."""
        txt = self._out_dir.strip()
        out_dir = Path(os.path.abspath(os.path.expanduser(txt))) if txt else None
        multi = len({str(i.root) for i in self._items if i.root is not None}) > 1
        bases = {engine._output_base(i, out_dir, multi) for i in self._items}
        bases.discard(None)
        if not bases and out_dir is not None:
            bases.add(out_dir)
        return sorted(bases)

    def _on_verify(self) -> None:
        """Contrôle la sortie : temporaires interrompus, tailles anormales, médias illisibles."""
        if self.is_busy():
            return
        self._locate_binaries(log=False)
        bases = self._output_bases()
        if not bases:
            folder = choose_directory(self, self.tr("Dossier de sortie à vérifier"),
                                      str(self.prefs.get("output_dir", "") or ""))
            if not folder:
                return
            bases = [Path(folder)]
        if self._ffprobe is None:
            self.log_panel.append_log("WARN", "FFprobe introuvable : seule la recherche des fichiers "
                                              "incomplets et des tailles anormales sera faite.")
        self.log_panel.append_log("INFO", self.tr("Vérification de la sortie : {paths}").format(
            paths=" ; ".join(str(b) for b in bases)))
        worker = VerifyWorker(bases, self._ffprobe)
        self.run_worker(worker, on_completed=self._on_verified,
                        on_failed=lambda _m: self._after_worker(), on_cancelled=self._after_worker,
                        lock_widgets=self._lockables, history_action="Vérification")

    def _on_verified(self, result: object) -> None:
        if isinstance(result, VerifyResult):
            self._report_verification(result)
        self._after_worker()

    def _report_verification(self, result: VerifyResult) -> None:
        """Résume le contrôle, puis propose de supprimer les fichiers suspects (jamais les originaux)."""
        damaged = result.damaged
        paths = "\n".join(str(b) for b in result.bases)
        if not damaged:
            self.log_panel.append_log("OK", self.tr(
                "Vérification : {count} fichier(s) contrôlé(s), aucun fichier incomplet ou illisible."
            ).format(count=result.checked))
            QMessageBox.information(
                self, self.tr("Vérifier la sortie"),
                self.tr("Aucun fichier incomplet ou corrompu n'a été trouvé.\n\n"
                        "{count} fichier(s) contrôlé(s) dans :\n{paths}")
                .format(count=result.checked, paths=paths))
            return
        self.log_panel.append_log("WARN", self.tr(
            "Vérification : {bad} fichier(s) suspect(s) sur {count} contrôlé(s) ; ne supprimez pas vos originaux."
        ).format(bad=len(damaged), count=result.checked))
        reasons = {"partiel": self.tr("conversion interrompue (fichier temporaire)"),
                   "taille": self.tr("taille inattendue : fichier tronqué ?"),
                   "illisible": self.tr("illisible pour FFprobe")}
        for item in damaged[:20]:
            self.log_panel.append_log("WARN", f"{item.path} — {reasons.get(item.reason, item.reason)}"
                                              + (f" ({item.detail})" if item.detail else ""))
        if len(damaged) > 20:
            self.log_panel.append_log("WARN", self.tr("… et {count} autre(s) fichier(s) suspect(s).")
                                      .format(count=len(damaged) - 20))
        answer = QMessageBox.question(
            self, self.tr("Fichiers à vérifier"),
            self.tr("{bad} fichier(s) de la sortie peuvent être incomplets ou corrompus.\n\n"
                    "Les supprimer ? Ils seront recréés à la prochaine conversion ; aucun autre fichier "
                    "n'est touché et les originaux ne sont jamais concernés.").format(bad=len(damaged)),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer == QMessageBox.StandardButton.Yes:
            self._delete_damaged(damaged)

    def _delete_damaged(self, damaged: list[engine.DamagedFile]) -> None:
        """Supprime les fichiers signalés par la vérification, une seule fois par chemin."""
        removed = failed = 0
        seen: set[str] = set()
        for item in damaged:
            key = path_key(item.path)
            if key in seen:
                continue
            seen.add(key)
            try:
                item.path.unlink(missing_ok=True)
                removed += 1
            except OSError as exc:
                failed += 1
                self.log_panel.append_log("WARN", self.tr("{path} : suppression impossible ({error}).")
                                          .format(path=item.path, error=exc))
        self.log_panel.append_log("OK" if not failed else "WARN", self.tr(
            "{count} fichier(s) supprimé(s) ; relancez la conversion pour les recréer.").format(count=removed))

    # ── Fin de traitement ───────────────────────────────────────────────────
    def closeEvent(self, event: QCloseEvent) -> None:
        """Annule la sonde en attente : une fenêtre fermée ne doit plus lancer FFmpeg."""
        self._probe_timer.stop()
        super().closeEvent(event)

    def _after_worker(self) -> None:
        self.stage_label.clear()
        self.stage_label.hide()
        self.progress_bar.clear_segments()
        self.progress_bar.setToolTip("")
        self.progress_legend.clear_entries()
        self._segment_bounds = []
        QTimer.singleShot(0, self._drain_pending)

    def _drain_pending(self) -> None:
        if self.is_busy():
            QTimer.singleShot(100, self._drain_pending)
            return
        self._refresh_run_state()
        self._sync_skip_json_enabled()               # le verrouillage a tout réactivé : on rétablit la dépendance
        if self._pending:
            paths = [p for lst in self._pending for p in lst]
            self._pending.clear()
            self._start_scan(paths)
