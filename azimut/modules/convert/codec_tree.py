"""Panneau « Format et qualité » d'Azimut Convert.

Une liste déroulante par famille (Images, Audio) et deux listes pour la vidéo : codec image et codec audio.
Seul le codec choisi affiche ses curseurs de qualité, ce qui remplace l'ancienne arborescence à boutons radio
(une ligne par codec, très longue à la verticale) : le panneau reste compact même avec beaucoup de codecs.

    Images      [ AVIF ▾ ]
                Qualité : CRF ──●── 39 · preset ──●── 4
    Vidéos      Vidéo [ AV1 (WebM) ▾ ]   Audio [ Opus ▾ ]
                HDR : [ Tone-mapping vers SDR ▾ ]
                Qualité : CRF … · preset … · débit audio …
    Audio       [ MP3 ▾ ]
                Débit : ──●── 192 kbit/s
    Performances : threads, fichiers en parallèle, priorité, mémoire

Le widget ne connaît ni les préférences ni FFmpeg : la fenêtre le configure (``configure``) et lit les
valeurs (``read_overrides``).
"""
from __future__ import annotations

from typing import Any, Callable, Collection, Mapping, Sequence

from PySide6.QtCore import QCoreApplication, QEvent, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QSlider, QVBoxLayout, QWidget,
)

from azimut.modules.convert import advanced
from azimut.ui.controls import SwitchRow, attach_value_bubble, fit_combo_to_panel
from azimut.ui.theme import scaled
from azimut.modules.convert.profiles import (
    AUDIO_CODECS, PROFILES, Profile, audio_codecs_for, get_profile, profiles_for,
)

KIND_TITLES = {"image": "Images", "video": "Vidéos", "audio": "Audio"}
DEFAULT_MISSING_REASON = "encodeur manquant"
GPU_TAG = "GPU"                                  # repère des profils accélérés par la carte graphique

_TIP_RIGHT = "Vers la droite : meilleure qualité, fichier plus gros."
_INDENT = 10                                     # un cran de la hiérarchie section → codec → réglage


class SliderRow(QWidget):
    """Une ligne « libellé — curseur — valeur » dont le curseur parcourt une liste de valeurs ordonnée."""

    valueChanged = Signal(object)                    # valeur réelle (int ou str), pas l'index du curseur

    def __init__(self, label: str, tooltip: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._values: list[Any] = []
        self._fmt: Callable[[Any], str] = str
        self._read_only = False                  # valeur calculée : curseur visible mais non modifiable
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 1, 0, 1)
        lay.setSpacing(8)
        self.label = QLabel(label)
        # Largeurs mises à l'échelle du zoom : à 150 %, « Vidéo » ne tenait plus dans
        # 74 px figés et se faisait rogner de 2 px.
        self.label.setMinimumWidth(scaled(74))
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setPageStep(1)
        self.slider.setMinimumWidth(scaled(80))
        self.slider.setMinimumHeight(scaled(18))
        self.slider.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.value_label = QLabel()
        self.value_label.setMinimumWidth(scaled(70))
        self.value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(self.label)
        lay.addWidget(self.slider, 1)
        lay.addWidget(self.value_label)
        for w in (self, self.label, self.slider, self.value_label):
            w.setToolTip(tooltip)
        self.slider.valueChanged.connect(self._on_slider)
        attach_value_bubble(self.slider,
                            lambda: self._fmt(self.value()) if self._values else "")

    # ── Valeurs ─────────────────────────────────────────────────────────────
    def set_choices(self, values: Sequence[Any], current: Any = None,
                    fmt: Callable[[Any], str] | None = None) -> None:
        """Remplace la liste de valeurs (dans l'ordre du curseur) et positionne le curseur, sans signal."""
        self._values = list(values)
        self._fmt = fmt or str
        self.slider.blockSignals(True)
        try:
            self.slider.setRange(0, max(0, len(self._values) - 1))
            self.slider.setEnabled(not self._read_only and len(self._values) > 1)
            self.slider.setValue(self._index_of(current))
        finally:
            self.slider.blockSignals(False)
        self._show()

    def value(self) -> Any:
        return self._values[self.slider.value()] if self._values else None

    def set_value(self, value: Any) -> None:
        """Positionne le curseur (valeur la plus proche si elle n'est pas dans la liste), sans signal."""
        self.slider.blockSignals(True)
        try:
            self.slider.setValue(self._index_of(value))
        finally:
            self.slider.blockSignals(False)
        self._show()

    def set_read_only(self, read_only: bool) -> None:
        """Curseur affiché mais non déplaçable (mode automatique)."""
        self._read_only = bool(read_only)
        self.slider.setEnabled(not self._read_only and len(self._values) > 1)

    def _index_of(self, value: Any) -> int:
        if not self._values:
            return 0
        if value in self._values:
            return self._values.index(value)
        try:
            target = float(value)
            nums = [float(v) for v in self._values]
        except (TypeError, ValueError):
            return self.slider.value() if 0 <= self.slider.value() < len(self._values) else 0
        return min(range(len(nums)), key=lambda i: abs(nums[i] - target))

    def _show(self) -> None:
        self.value_label.setText(self._fmt(self.value()) if self._values else "")

    def _on_slider(self, _index: int) -> None:
        self._show()
        self.valueChanged.emit(self.value())


class _HdrRow(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(2 * _INDENT, 1, 0, 1)
        lay.setSpacing(8)
        self.label = QLabel(self.tr("HDR"))
        self.label.setMinimumWidth(scaled(74))
        self.combo = QComboBox()
        fit_combo_to_panel(self.combo)
        self.combo.addItem(self.tr("Tone-mapping vers SDR"), "T")
        self.combo.addItem(self.tr("HDR natif (AV1 10 bits)"), "H")
        tip = self.tr("Vidéos HDR : conversion vers le SDR (compatible partout) ou conservation du HDR "
                      "(codec AV1 uniquement ; les autres codecs passent toujours en SDR).")
        self._tip = tip
        self._tip_locked = self.tr("Le HDR natif n'est possible qu'avec l'AV1 logiciel (SVT-AV1 ou libaom) : "
                                   "avec le codec choisi, les vidéos HDR sont converties en SDR (tone-mapping).")
        for w in (self, self.label, self.combo):
            w.setToolTip(tip)
        lay.addWidget(self.label)
        lay.addWidget(self.combo, 1)
        # La police du zoom arrive sur la liste elle-même : c'est là qu'il faut remesurer.
        self.combo.installEventFilter(self)
        self._fit_combo()

    def _fit_combo(self) -> None:
        """La liste affiche entièrement son libellé le plus long, sans le rogner.

        Les deux choix HDR sont longs (21 et 25 caractères) : la largeur de contenu
        type les couperait. Elle ne s'étend que de ce qu'il faut, et suit la police.
        """
        metrics = self.combo.fontMetrics()
        needed = max((metrics.horizontalAdvance(self.combo.itemText(index))
                      for index in range(self.combo.count())), default=0)
        self.combo.setMinimumWidth(needed + scaled(64))     # marges intérieures et flèche

    def eventFilter(self, watched: object, event: QEvent) -> bool:  # noqa: N802 - API Qt
        """Refait la largeur après un changement de police (zoom, thème)."""
        if watched is self.combo and event.type() == QEvent.Type.FontChange:
            self._fit_combo()
        return super().eventFilter(watched, event)

    def set_native_allowed(self, allowed: bool) -> None:
        """Grise « HDR natif » quand le codec vidéo n'est pas AV1 (le choix de l'utilisateur est conservé ailleurs)."""
        item = self.combo.model().item(self.combo.findData("H"))
        if item is not None:
            item.setEnabled(allowed)
            item.setToolTip("" if allowed else self._tip_locked)
        tip = self._tip if allowed else self._tip_locked
        for w in (self, self.label, self.combo):
            w.setToolTip(tip)


class CodecPanel(QWidget):
    """Listes de codecs par famille et curseurs du codec choisi, plus les performances."""

    codecChanged = Signal(str, str)                  # famille, identifiant du codec choisi
    videoAudioChanged = Signal(str)                  # codec audio choisi pour la vidéo
    qualityEdited = Signal(str)                      # identifiant du codec dont un curseur a bougé
    resetRequested = Signal(str)                     # identifiant du codec à rétablir
    performanceChanged = Signal()
    hdrChanged = Signal(str)                         # « T » ou « H »

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("CodecPanel")
        self._combos: dict[str, QComboBox] = {}
        self._audio_combo = QComboBox()
        self._rows: dict[str, dict[str, SliderRow]] = {}
        self._video_audio_rows: dict[str, dict[str, SliderRow]] = {}
        self._containers: dict[str, QWidget] = {}
        self._video_audio_containers: dict[str, QWidget] = {}
        self._reset_buttons: dict[str, QPushButton] = {}
        self._labels: dict[str, str] = {p.id: p.label for p in PROFILES}
        self._descriptions: dict[str, str] = {p.id: p.description for p in PROFILES}
        self._unavailable: dict[str, str] = {}
        self._encoders: Collection[str] | None = None
        self._build()

    # ── Construction ────────────────────────────────────────────────────────
    @staticmethod
    def _section_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setProperty("role", "title")
        font = QFont(label.font())
        font.setBold(True)
        label.setFont(font)
        return label

    @staticmethod
    def _combo_row(text: str, combo: QComboBox) -> QWidget:
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(_INDENT, 0, 0, 0)
        lay.setSpacing(8)
        label = QLabel(text)
        label.setMinimumWidth(scaled(44))
        lay.addWidget(label)
        fit_combo_to_panel(combo)
        combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        lay.addWidget(combo, 1)
        return row

    @staticmethod
    def _quality_box() -> tuple[QWidget, QVBoxLayout]:
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(2 * _INDENT, 0, 4, 2)
        lay.setSpacing(2)
        return box, lay

    def _build(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        # ── Images ──
        lay.addWidget(self._section_label(KIND_TITLES["image"]))
        combo = self._combos["image"] = QComboBox()
        combo.currentIndexChanged.connect(lambda _i: self._on_kind_combo("image"))
        lay.addWidget(self._combo_row("Codec", combo))
        self._fill_combo(combo, profiles_for("image"))
        for profile in profiles_for("image"):
            self._add_codec(lay, profile, self._containers)

        # ── Vidéos : deux listes (image et audio) ──
        lay.addWidget(self._section_label(KIND_TITLES["video"]))
        # Matériel détecté : on sait tout de suite si NVENC / QSV / VA-API est utilisable, et pour quels codecs.
        self.hardware_label = QLabel()
        self.hardware_label.setProperty("role", "muted")
        self.hardware_label.setWordWrap(True)
        lay.addWidget(self.hardware_label)
        # Qualité : le GPU va vite, le CPU compresse mieux ; l'utilisateur doit le savoir avant de choisir.
        self.quality_hint = QLabel(self.tr(
            "GPU : plus rapide et libère le processeur, mais à qualité égale le fichier est souvent plus gros. "
            "CPU : plus lent, fichiers plus compacts."))
        self.quality_hint.setProperty("role", "muted")
        self.quality_hint.setWordWrap(True)
        self.quality_hint.hide()
        lay.addWidget(self.quality_hint)
        vcombo = self._combos["video"] = QComboBox()
        vcombo.currentIndexChanged.connect(lambda _i: self._on_kind_combo("video"))
        lay.addWidget(self._combo_row("Vidéo", vcombo))
        self._fill_combo(vcombo, self._visible_video_profiles())
        self._audio_combo.currentIndexChanged.connect(lambda _i: self._on_audio_combo())
        lay.addWidget(self._combo_row("Audio", self._audio_combo))
        self._fill_audio_combo(get_profile(str(vcombo.currentData())), None)
        self.hdr = _HdrRow()
        self._hdr_wanted = "T"                     # choix de l'utilisateur, même quand « HDR natif » est grisé
        self._hdr_syncing = False
        self.hdr.combo.currentIndexChanged.connect(self._on_hdr_combo)
        lay.addWidget(self.hdr)
        for profile in profiles_for("video"):
            self._add_codec(lay, profile, self._containers)
        for profile in AUDIO_CODECS:
            self._add_codec(lay, profile, self._video_audio_containers, video_audio=True)

        # ── Audio ──
        lay.addWidget(self._section_label(KIND_TITLES["audio"]))
        acombo = self._combos["audio"] = QComboBox()
        acombo.currentIndexChanged.connect(lambda _i: self._on_kind_combo("audio"))
        lay.addWidget(self._combo_row("Codec", acombo))
        self._fill_combo(acombo, profiles_for("audio"))
        for profile in profiles_for("audio"):
            self._add_codec(lay, profile, self._containers)

        self._build_performance(lay)
        self._reveal("image", str(combo.currentData()))
        self._reveal("video", str(vcombo.currentData()))
        self._reveal("audio", str(acombo.currentData()))
        self._sync_audio_choices(str(vcombo.currentData()), None)
        self._sync_hdr()

    def _add_codec(self, lay: QVBoxLayout, profile: Profile, store: dict[str, QWidget], *,
                   video_audio: bool = False) -> None:
        rows = self._make_rows(profile, video_audio=video_audio)
        if not rows:
            return
        box, box_lay = self._quality_box()
        for row in rows.values():
            box_lay.addWidget(row)
        reset = QPushButton(self.tr("Rétablir"))
        reset.setProperty("kind", "ghost")
        reset.setToolTip(self.tr("Remet les curseurs de ce codec à leurs valeurs de départ."))
        reset_key = advanced.video_audio_key(profile.id) if video_audio else profile.id
        reset.clicked.connect(lambda _c=False, key=reset_key: self.resetRequested.emit(key))
        # Le padding du bouton fantôme le place un cran sous les libellés des curseurs.
        box_lay.addWidget(reset, 0, Qt.AlignmentFlag.AlignLeft)
        lay.addWidget(box)
        box.hide()
        store[profile.id] = box
        if video_audio:
            self._video_audio_rows[profile.id] = rows
        else:
            self._rows[profile.id] = rows
            self._reset_buttons[profile.id] = reset

    def _make_rows(self, profile: Profile, *, video_audio: bool = False) -> dict[str, SliderRow]:
        """Curseurs applicables à ce codec (plages et valeurs exactes posées par ``configure``)."""
        rows: dict[str, SliderRow] = {}
        pid = profile.id
        if advanced.crf_range(profile):
            rows["crf"] = SliderRow("CRF", "Constant Rate Factor : l'échelle est inversée pour que la droite "
                                    "donne la meilleure qualité (CRF bas) et le fichier le plus gros.")
        if advanced.preset_choices(profile):
            rows["preset"] = SliderRow(advanced.preset_word(profile).capitalize(),
                                       self._preset_tip(profile))
        if advanced.quality_range(profile):
            rows["quality"] = SliderRow(advanced.quality_word(profile).capitalize(),
                                        self._quality_tip(profile))
        if profile.kind != "video" and advanced.bitrate_range(profile):
            rows["bitrate"] = SliderRow("Débit", "Débit de l'audio en kbit/s. " + _TIP_RIGHT)
        edited_key = advanced.video_audio_key(pid) if video_audio else pid
        for name, row in rows.items():
            row.valueChanged.connect(lambda value, key=edited_key, k=name: self._on_row_value(key, k, value))
        return rows

    @staticmethod
    def _preset_tip(profile: Profile) -> str:
        fam = advanced.family(profile)
        if profile.kind == "audio":
            return ("Niveau de compression FLAC. Sans perte : la qualité ne change pas. Vers la droite : "
                    "fichier plus petit, encodage plus lent.")
        if fam == "png":
            return ("Niveau de compression PNG (0–9). Vers la droite : fichier plus petit, écriture plus lente.")
        if fam == "jxl":
            return ("Effort JPEG XL (1–9). Vers la droite : encodage plus lent, fichier plus petit.")
        if fam == "tiff":
            return ("Compression TIFF : PackBits (rapide), LZW (équilibré), Deflate (fichier plus petit).")
        if fam == "prores":
            return ("Profil ProRes : proxy (léger) … 4444 (qualité maximale).")
        hw = advanced.hardware_kind(profile)
        if hw == "nvenc":
            return ("Preset NVENC : p1 (plus rapide) … p7 (plus lent et plus efficace).")
        if hw == "qsv":
            return ("Preset Quick Sync : veryfast (rapide) … veryslow (plus lent et plus efficace).")
        if fam == "vp9":
            return ("Vitesse d'encodage VP9 (cpu-used). Vers la droite : plus lent, compression plus efficace.")
        return ("Vitesse d'encodage. Vers la droite : plus lent, mais compression plus efficace "
                "(fichier plus petit à qualité égale).")

    @staticmethod
    def _quality_tip(profile: Profile) -> str:
        fam = advanced.family(profile)
        if fam == "jxl":
            return ("Distance JPEG XL : 0 = sans perte ; plus la valeur est basse, meilleure est la qualité.")
        if fam == "mjpeg":
            return "Qualité JPEG (-q:v) : l'échelle est inversée pour que la droite donne la meilleure qualité."
        if fam in ("mpeg4", "mpeg2"):
            return ("Qualité MPEG (-q:v) : l'échelle est inversée pour que la droite donne la meilleure qualité "
                    "et le fichier le plus gros.")
        if fam == "theora":
            return "Qualité Theora (-q:v, 0–10). " + _TIP_RIGHT
        if fam == "webp":
            return "Qualité WebP, de 0 à 100. " + _TIP_RIGHT
        if fam == "heic":
            return "Qualité HEIC, de 0 à 100. " + _TIP_RIGHT
        return "Qualité de l'encodeur. " + _TIP_RIGHT

    def _build_performance(self, lay: QVBoxLayout) -> None:
        lay.addWidget(self._section_label("Performances"))
        self.slider_threads = SliderRow("Threads", "Threads par encodage (images et vidéos). « Auto » : bornés pour "
                                        "limiter la mémoire (vidéo). En parallélisme automatique, cette valeur "
                                        "sert aussi à calculer combien de fichiers peuvent avancer ensemble.")
        self.slider_threads.set_choices(range(0, advanced.MAX_THREADS + 1), 0,
                                        lambda v: "Auto" if int(v) == 0 else str(v))
        self.chk_parallel_auto = SwitchRow(self.tr("Parallélisme automatique (recommandé)"))
        self.chk_parallel_auto.setToolTip(self.tr(
            "Répartit les fichiers selon les cœurs disponibles et le réglage Threads : les copies et les "
            "images avancent ensemble ; deux vidéos peuvent avancer si le codec choisi est accéléré par le "
            "GPU (NVENC, QSV, VA-API), une seule avec un encodeur processeur, qui utilise déjà tous les "
            "cœurs et beaucoup de mémoire. Décochez pour régler chaque famille à la main."))
        self.parallel_rows: dict[str, SliderRow] = {
            "image": SliderRow("Images", self.tr("Fichiers image convertis en même temps. Chaque encodage "
                                                 "utilise le nombre de threads choisi ci-dessus.")),
            "audio": SliderRow("Audios", self.tr("Fichiers audio convertis en même temps : les encodeurs audio "
                                                 "sont presque mono-thread, un peu de parallélisme aide.")),
            "video": SliderRow("Vidéos", self.tr("Vidéos encodées en même temps. Au-delà de 1, chaque encodeur "
                                                 "doit partager les threads et la mémoire : à réserver aux "
                                                 "machines puissantes.")),
            "copy": SliderRow("Copies", self.tr("Fichiers copiés en même temps (déjà au bon format, documents, "
                                                "JSON…). Cette étape est limitée par le disque, pas par le processeur.")),
        }
        self._manual_parallel: dict[str, int] = {kind: 1 for kind in advanced.PARALLEL_KINDS}
        self.chk_low_priority = SwitchRow(self.tr("Priorité basse (garde l'ordinateur réactif)"))
        self.chk_low_priority.setToolTip(
            self.tr("Les processus FFmpeg sont alors lancés avec une priorité réduite : la conversion peut être "
            "un peu plus lente, mais les autres applications restent réactives."))
        self.chk_memory = SwitchRow(self.tr("Attendre si la mémoire est saturée"))
        self.chk_memory.setToolTip(self.tr(
            "Avant chaque fichier, la conversion attend si la RAM utilisée atteint 75 % ou si la mémoire "
            "disponible tombe sous 20 %. Elle réessaie toutes les 2 secondes et s'interrompt si vous annulez. "
            "Sans cette case, les fichiers s'enchaînent même sous pression : ralentissement général, swap et "
            "risque d'échec par manque de mémoire."))
        controls = QWidget()
        controls_lay = QVBoxLayout(controls)
        controls_lay.setContentsMargins(_INDENT, 0, 0, 0)
        controls_lay.setSpacing(6)
        self.slider_threads.valueChanged.connect(self._on_threads_changed)
        controls_lay.addWidget(self.slider_threads)
        self.chk_parallel_auto.toggled.connect(self._on_parallel_auto_toggled)
        controls_lay.addWidget(self.chk_parallel_auto)
        for kind, row in self.parallel_rows.items():
            row.set_choices(range(1, advanced.max_parallel(kind) + 1), 1)
            row.valueChanged.connect(lambda value, k=kind: self._on_parallel_value(k, value))
            controls_lay.addWidget(row)
        hint = QLabel(self.tr("Chaque fichier utilise le réglage Threads ci-dessus : en manuel, gardez "
                              "« fichiers simultanés × threads » dans la limite des cœurs de la machine."))
        hint.setProperty("role", "muted")
        hint.setWordWrap(True)
        controls_lay.addWidget(hint)
        for chk in (self.chk_low_priority, self.chk_memory):
            chk.toggled.connect(lambda _c: self.performanceChanged.emit())
            controls_lay.addWidget(chk)
        lay.addWidget(controls)

    # ── Parallélisme ────────────────────────────────────────────────────────
    def _on_threads_changed(self, _value: object) -> None:
        self._refresh_parallel_rows()
        self.performanceChanged.emit()

    def _on_parallel_auto_toggled(self, _checked: bool) -> None:
        self._refresh_parallel_rows()
        self.performanceChanged.emit()

    def _on_parallel_value(self, kind: str, value: object) -> None:
        if not self.chk_parallel_auto.isChecked():
            self._manual_parallel[kind] = advanced.clamp_parallel(value, kind)
        self.performanceChanged.emit()

    def _current_video_profile(self) -> Profile | None:
        """Profil vidéo affiché, ou None tant que la liste n'est pas construite."""
        pid = str(self._combos["video"].currentData() or "")
        if not pid:
            return None
        try:
            return get_profile(pid)
        except KeyError:
            return None

    def _hardware_video_selected(self) -> bool:
        """Vrai si le codec vidéo choisi est un encodeur GPU qui répond réellement."""
        profile = self._current_video_profile()
        return (profile is not None and profile.id not in self._unavailable
                and bool(advanced.hardware_kind(profile)))

    def _refresh_parallel_rows(self) -> None:
        """Auto : valeurs calculées et curseurs bloqués ; manuel : les valeurs choisies reviennent."""
        auto = self.chk_parallel_auto.isChecked()
        threads = advanced.clamp_threads(self.slider_threads.value())
        hardware = self._hardware_video_selected()
        for kind, row in self.parallel_rows.items():
            row.set_read_only(auto)
            row.set_value(advanced.auto_parallel(kind, threads, hardware_video=hardware) if auto
                          else self._manual_parallel.get(kind, 1))

    def set_parallel(self, mode: str, values: Mapping[str, Any] | None = None) -> None:
        """Charge le mode et les valeurs manuelles depuis les préférences, sans émettre de signal."""
        self._manual_parallel = {kind: advanced.clamp_parallel((values or {}).get(kind, 1), kind)
                                 for kind in advanced.PARALLEL_KINDS}
        self.chk_parallel_auto.blockSignals(True)
        try:
            self.chk_parallel_auto.setChecked(mode != "manual")
        finally:
            self.chk_parallel_auto.blockSignals(False)
        self._refresh_parallel_rows()

    def parallel_mode(self) -> str:
        return "auto" if self.chk_parallel_auto.isChecked() else "manual"

    def parallel_values(self) -> dict[str, int]:
        """Parallélisme effectif par famille, prêt pour le worker."""
        return advanced.parallel_counts(self.parallel_mode(), self._manual_parallel,
                                        advanced.clamp_threads(self.slider_threads.value()),
                                        hardware_video=self._hardware_video_selected())

    # ── Listes ──────────────────────────────────────────────────────────────
    def set_hardware_status(self, text: str) -> None:
        """Ligne « matériel détecté » affichée au-dessus de la liste des codecs vidéo."""
        self.hardware_label.setText(text)

    def set_quality_hint_visible(self, visible: bool) -> None:
        """Rappel CPU/GPU affiché seulement quand une accélération matérielle est disponible."""
        self.quality_hint.setVisible(visible)

    def _label(self, pid: str) -> str:
        label = self._labels.get(pid, pid)
        if advanced.hardware_kind(get_profile(pid)):
            label = f"{label} · {GPU_TAG}"
        reason = self._unavailable.get(pid)
        return label if reason is None else f"{label} — {reason}"

    def _description(self, profile: Profile) -> str:
        """Description du codec, traduite à l'affichage (le profil reste une donnée pure)."""
        text = QCoreApplication.translate("Profiles", profile.description)
        if profile.kind == "video" and advanced.hardware_kind(profile):
            text += " " + self.tr("Conversion GPU : plus rapide, mais à qualité visuelle égale le fichier est "
                                  "souvent plus gros qu'avec un encodeur CPU.")
        return text

    def profile_is_visible(self, profile: Profile) -> bool:
        """Un profil `only_if_encoder` n'est proposé que si FFmpeg expose l'un de ses encodeurs."""
        return (not profile.only_if_encoder
                or (self._encoders is not None
                    and any(e in self._encoders for e in profile.video_encoders)))

    def _visible_video_profiles(self) -> list[Profile]:
        return [p for p in profiles_for("video") if self.profile_is_visible(p)]

    def set_visible_encoders(self, encoders: Collection[str] | None) -> None:
        """Met à jour la liste vidéo quand la détection FFmpeg arrive (AV1 matériel conditionnel)."""
        self._encoders = encoders
        combo = self._combos.get("video")
        if combo is None:
            return
        visible = self._visible_video_profiles()
        if [combo.itemData(i) for i in range(combo.count())] == [p.id for p in visible]:
            return                                  # même liste : ne pas perturber la sélection en cours
        previous = str(combo.currentData() or "")
        self._fill_combo(combo, visible)
        index = combo.findData(previous)
        combo.blockSignals(True)
        try:
            combo.setCurrentIndex(index if index >= 0 else 0)
        finally:
            combo.blockSignals(False)
        self._on_kind_combo("video")

    def _fill_combo(self, combo: QComboBox, profiles: Sequence[Profile]) -> None:
        combo.blockSignals(True)
        try:
            combo.clear()
            for profile in profiles:
                combo.addItem(self._label(profile.id), profile.id)
                combo.setItemData(combo.count() - 1, self._description(profile), Qt.ItemDataRole.ToolTipRole)
        finally:
            combo.blockSignals(False)
        self._update_combo_tip(combo)

    def _fill_audio_combo(self, video: Profile, preferred: str | None) -> None:
        profiles = audio_codecs_for(video)
        self._audio_combo.blockSignals(True)
        try:
            self._audio_combo.clear()
            for profile in profiles:
                self._audio_combo.addItem(self._label(profile.id), profile.id)
                self._audio_combo.setItemData(self._audio_combo.count() - 1, self._description(profile),
                                              Qt.ItemDataRole.ToolTipRole)
            index = self._audio_combo.findData(preferred) if preferred else -1
            self._audio_combo.setCurrentIndex(max(0, index))
        finally:
            self._audio_combo.blockSignals(False)
        self._update_audio_tip()

    @staticmethod
    def _update_combo_tip(combo: QComboBox) -> None:
        pid = combo.currentData()
        combo.setToolTip(str(combo.itemData(combo.currentIndex(), Qt.ItemDataRole.ToolTipRole) or pid or ""))

    def _update_audio_tip(self) -> None:
        self._audio_combo.setToolTip(str(
            self._audio_combo.itemData(self._audio_combo.currentIndex(), Qt.ItemDataRole.ToolTipRole) or ""))

    def _on_kind_combo(self, kind: str) -> None:
        pid = str(self._combos[kind].currentData() or "")
        if not pid:
            return
        self._update_combo_tip(self._combos[kind])
        self._reveal(kind, pid)
        if kind == "video":
            self._sync_audio_choices(pid, str(self._audio_combo.currentData() or ""))
            self._sync_hdr()
            if hasattr(self, "parallel_rows"):
                self._refresh_parallel_rows()          # vidéo GPU : deux fichiers en automatique
        self.codecChanged.emit(kind, pid)

    # ── HDR ─────────────────────────────────────────────────────────────────
    def _on_hdr_combo(self, _index: int) -> None:
        if self._hdr_syncing:
            return
        self._hdr_wanted = str(self.hdr.combo.currentData())
        self.hdrChanged.emit(self._hdr_wanted)

    def hdr_mode(self) -> str:
        """Choix HDR de l'utilisateur (« T » ou « H ») ; « H » ne s'applique qu'avec AV1."""
        return self._hdr_wanted

    def set_hdr_mode(self, mode: str) -> None:
        """Pose le choix HDR (préférences) sans émettre ``hdrChanged``."""
        self._hdr_wanted = "H" if mode == "H" else "T"
        self._sync_hdr()

    def _sync_hdr(self) -> None:
        """Autorise « HDR natif » seulement avec AV1 logiciel ; le matériel reste en tone-mapping SDR."""
        profile = self._current_video_profile()
        allowed = (profile is not None and advanced.family(profile) == "av1"
                   and not advanced.hardware_kind(profile))
        self.hdr.set_native_allowed(allowed)
        shown = self._hdr_wanted if allowed else "T"
        self._hdr_syncing = True
        try:
            self.hdr.combo.setCurrentIndex(max(0, self.hdr.combo.findData(shown)))
        finally:
            self._hdr_syncing = False

    def _on_audio_combo(self) -> None:
        pid = str(self._audio_combo.currentData() or "")
        self._update_audio_tip()
        self._reveal_video_audio(pid)
        if pid:
            self.videoAudioChanged.emit(pid)

    def _sync_audio_choices(self, video_pid: str, preferred: str | None) -> None:
        """Liste audio compatible avec le codec vidéo ; conserve le choix s'il existe encore."""
        profile = get_profile(video_pid)
        self._audio_combo.setEnabled(True)
        self._fill_audio_combo(profile, preferred)
        self._reveal_video_audio(str(self._audio_combo.currentData() or ""))

    def _reveal(self, kind: str, pid: str) -> None:
        for profile in profiles_for(kind):
            box = self._containers.get(profile.id)
            if box is not None:
                box.setVisible(profile.id == pid)

    def _reveal_video_audio(self, pid: str) -> None:
        for profile in AUDIO_CODECS:
            box = self._video_audio_containers.get(profile.id)
            if box is not None:
                box.setVisible(profile.id == pid)

    # ── Sélection ───────────────────────────────────────────────────────────
    def selected(self, kind: str) -> str:
        """Identifiant choisi pour la famille (``video_audio`` : codec audio de la vidéo)."""
        if kind == "video_audio":
            return str(self._audio_combo.currentData() or "")
        combo = self._combos.get(kind)
        return str(combo.currentData() or "") if combo is not None else ""

    def set_selected(self, kind: str, profile_id: str) -> None:
        """Sélectionne sans émettre ``codecChanged`` (chargement des préférences)."""
        if kind == "video_audio":
            self.set_video_audio(profile_id)
            return
        combo = self._combos.get(kind)
        if combo is None:
            return
        index = combo.findData(profile_id)
        combo.blockSignals(True)
        try:
            combo.setCurrentIndex(max(0, index))
        finally:
            combo.blockSignals(False)
        pid = str(combo.currentData() or "")
        self._update_combo_tip(combo)
        self._reveal(kind, pid)
        if kind == "video":
            self._sync_audio_choices(pid, str(self._audio_combo.currentData() or ""))
            self._sync_hdr()

    def choose(self, kind: str, profile_id: str) -> None:
        """Sélectionne comme le ferait l'utilisateur (signaux émis, choix enregistré)."""
        combo = self._combos.get(kind)
        if combo is None:
            return
        index = combo.findData(profile_id)
        if index >= 0:
            combo.setCurrentIndex(index)

    def set_video_audio(self, profile_id: str) -> None:
        index = self._audio_combo.findData(profile_id)
        if index < 0:
            return
        self._audio_combo.blockSignals(True)
        try:
            self._audio_combo.setCurrentIndex(index)
        finally:
            self._audio_combo.blockSignals(False)
        self._update_audio_tip()
        self._reveal_video_audio(str(self._audio_combo.currentData() or ""))

    # ── Configuration depuis la fenêtre ─────────────────────────────────────
    def configure(self, base: Profile, effective: Profile, encoders: Collection[str] | None,
                  video_audio_effective: Profile | None = None) -> None:
        """Plages et valeurs des curseurs d'un codec : ``base`` donne les bornes, ``effective`` les positions.

        ``video_audio_effective`` règle séparément la copie affichée sous la liste vidéo ; sans elle,
        cette copie garde les valeurs du profil de base.
        """
        self._configure_rows(self._rows.get(base.id), base, effective, encoders)
        self._configure_rows(self._video_audio_rows.get(base.id), base,
                             video_audio_effective or effective, encoders)

    @staticmethod
    def _configure_rows(rows: dict[str, SliderRow] | None, base: Profile, effective: Profile,
                        encoders: Collection[str] | None) -> None:
        if not rows:
            return
        if "crf" in rows:
            lo, hi = advanced.crf_range(base) or (0, 0)
            rows["crf"].set_choices(list(range(hi, lo - 1, -1)), effective.crf)     # droite = CRF bas = qualité
        if "preset" in rows:
            choices = list(advanced.preset_choices(base, encoders))
            if advanced.family(base) in advanced.REVERSED_PRESETS:
                choices.reverse()                                                   # 0 = lent : à droite
            rows["preset"].set_choices(choices, effective.preset)
        if "quality" in rows:
            lo, hi = advanced.quality_range(base) or (0, 0)
            values = list(range(hi, lo - 1, -1)) if advanced.family(base) in advanced.REVERSED_QUALITY \
                else list(range(lo, hi + 1))
            rows["quality"].set_choices(values, effective.quality)
        if "bitrate" in rows:
            lo, hi = advanced.bitrate_range(base) or (0, 0)
            rows["bitrate"].set_choices([b for b in advanced.BITRATE_STEPS if lo <= b <= hi],
                                        advanced.bitrate_kbps(effective), lambda v: f"{v} kbit/s")

    def read_overrides(self, profile_id: str) -> advanced.Overrides:
        """Valeurs actuellement affichées par les curseurs d'un codec."""
        rows = self._rows.get(profile_id, {})

        def get(name: str) -> Any:
            return rows[name].value() if name in rows else None

        crf, quality, bitrate = get("crf"), get("quality"), get("bitrate")
        return advanced.Overrides(crf=None if crf is None else int(crf), preset=get("preset"),
                                  quality=None if quality is None else int(quality),
                                  bitrate=None if bitrate is None else int(bitrate))

    def read_video_audio_overrides(self, profile_id: str) -> advanced.Overrides:
        """Valeurs affichées sous la liste vidéo pour le codec audio choisi."""
        rows = self._video_audio_rows.get(profile_id, {})

        def get(name: str) -> Any:
            return rows[name].value() if name in rows else None

        crf, quality, bitrate = get("crf"), get("quality"), get("bitrate")
        return advanced.Overrides(crf=None if crf is None else int(crf), preset=get("preset"),
                                  quality=None if quality is None else int(quality),
                                  bitrate=None if bitrate is None else int(bitrate))

    def _on_row_value(self, key: str, _knob: str, _value: Any) -> None:
        """Un curseur a bougé : prévient la fenêtre (les réglages vidéo et audio restent indépendants)."""
        self.qualityEdited.emit(key)

    def set_codec_available(self, profile_id: str, available: bool,
                            reason: str = DEFAULT_MISSING_REASON) -> None:
        """Signale dans les listes un codec impossible ici (il reste sélectionnable)."""
        if available:
            self._unavailable.pop(profile_id, None)
        else:
            self._unavailable[profile_id] = reason
        for combo in (*self._combos.values(), self._audio_combo):
            index = combo.findData(profile_id)
            if index >= 0:
                combo.setItemText(index, self._label(profile_id))
        for _kind, combo in self._combos.items():
            if combo.currentData() == profile_id:
                self._update_combo_tip(combo)
        if self._audio_combo.currentData() == profile_id:
            self._update_audio_tip()
        if self.selected("video") == profile_id and hasattr(self, "parallel_rows"):
            self._refresh_parallel_rows()              # profil GPU devenu indisponible : une seule vidéo

    def rows(self, profile_id: str) -> dict[str, SliderRow]:
        """Curseurs d'un codec (famille d'origine), par nom (``crf``, ``preset``, ``quality``, ``bitrate``)."""
        return self._rows.get(profile_id, {})

    def video_audio_rows(self, profile_id: str) -> dict[str, SliderRow]:
        """Copie des curseurs audio affichée sous la liste vidéo (mêmes valeurs que la famille Audio)."""
        return self._video_audio_rows.get(profile_id, {})

    def reset_button(self, profile_id: str) -> QPushButton | None:
        return self._reset_buttons.get(profile_id)
