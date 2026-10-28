"""Fenêtre d'Azimut Convert."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QFormLayout, QLabel, QMessageBox, QPushButton, QSpinBox, QToolButton,
    QVBoxLayout, QWidget,
)

from azimut.core import binaries
from azimut.core.binaries import BinaryNotFoundError
from azimut.core.fsutil import format_size
from azimut.core.module_spec import ModuleSpec
from azimut.core.prefs import Preferences
from azimut.modules.convert import MODULE, advanced
from azimut.modules.convert.engine import (
    STATE_CANCELLED, STATE_RUNNING, SourceItem, initial_state, missing_encoder_message, path_key, state_tag,
)
from azimut.modules.convert.profiles import (
    ALL_EXTS, KIND_LABELS, KINDS, Profile, default_profile, get_profile, profiles_for,
)
from azimut.modules.convert.workers import ConvertWorker, ProbeWorker, ScanWorker
from azimut.ui.module_window import ModuleWindow
from azimut.ui.widgets import Card, Column, DataTable, DropZone, PathField

COLUMNS = [
    Column("file", "Fichier", width=320, stretch=True),
    Column("kind", "Type", width=80),
    Column("size", "Taille", width=90, align="right"),
    Column("profile", "Profil", width=230),
    Column("state", "État", width=150),
    Column("progress", "Progression", width=100, align="center"),
]
KIND_TITLES = {"image": "Images", "video": "Vidéos", "audio": "Audio"}


class ConvertWindow(ModuleWindow):
    """File d'attente de conversion avec profils par famille de fichiers."""

    def __init__(self, spec: ModuleSpec = MODULE, parent: QWidget | None = None) -> None:
        super().__init__(spec, parent)
        self._items: list[SourceItem] = []
        self._keys: set[str] = set()
        self._encoders: frozenset[str] | None = None
        self._encoders_for: Path | None = None
        self._ffmpeg: Path | None = None
        self._ffprobe: Path | None = None
        self._bins_ok = False
        self._pending: list[list[str]] = []
        self._loading_adv = False
        self._build_ui()
        self._locate_binaries(log=True)
        self.show_binary_status(("ffmpeg", "ffprobe"))
        self.set_run_label("Convertir")
        QTimer.singleShot(0, self._probe_encoders)

    # ── Interface ───────────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        central = QWidget()
        lay = QVBoxLayout(central)
        self.drop = DropZone("Glissez ici des fichiers ou des dossiers à convertir\n(ou cliquez pour parcourir)")
        self.drop.pathsDropped.connect(self.open_paths)
        lay.addWidget(self.drop)
        self.table = DataTable(COLUMNS)
        lay.addWidget(self.table, 1)
        self.set_content(central)

        side = QWidget()
        sl = QVBoxLayout(side)
        card_p = Card("Profils")
        self._combos: dict[str, QComboBox] = {}
        self._descs: dict[str, QLabel] = {}
        for kind in KINDS:
            card_p.body_layout.addWidget(QLabel(KIND_TITLES[kind]))
            combo = QComboBox()
            for p in profiles_for(kind):
                combo.addItem(p.label, p.id)
            idx = combo.findData(self.prefs.get(f"profile_{kind}", ""))
            if idx >= 0:
                combo.setCurrentIndex(idx)
            combo.currentIndexChanged.connect(lambda _i, k=kind: self._on_profile_changed(k))
            desc = QLabel()
            desc.setWordWrap(True)
            card_p.body_layout.addWidget(combo)
            card_p.body_layout.addWidget(desc)
            self._combos[kind], self._descs[kind] = combo, desc
            self._update_desc(kind)
        sl.addWidget(card_p)
        sl.addWidget(self._build_advanced())

        card_o = Card("Sortie")
        self.chk_recursive = QCheckBox("Sous-dossiers")
        self.chk_recursive.setChecked(bool(self.prefs.get("recursive", True)))
        self.chk_recursive.toggled.connect(lambda v: self.prefs.set("recursive", bool(v)))
        self.out_field = PathField("Dossier de sortie", mode="dir")
        self.out_field.pathChanged.connect(self._on_out_changed)
        hint = QLabel("Laisser vide : « <source>_Converted » à côté de chaque dossier ajouté. "
                      "Obligatoire pour les fichiers ajoutés isolément.")
        hint.setWordWrap(True)
        card_o.body_layout.addWidget(self.chk_recursive)
        card_o.body_layout.addWidget(self.out_field)
        card_o.body_layout.addWidget(hint)
        sl.addWidget(card_o)

        card_q = Card("File d'attente")
        self.btn_remove = QPushButton("Retirer la sélection")
        self.btn_clear = QPushButton("Vider la liste")
        self.btn_remove.clicked.connect(self._on_remove)
        self.btn_clear.clicked.connect(self._on_clear)
        card_q.body_layout.addWidget(self.btn_remove)
        card_q.body_layout.addWidget(self.btn_clear)
        sl.addWidget(card_q)
        sl.addStretch(1)
        self.set_side_panel(side, "Réglages")
        self._lockables: list[QWidget] = [*self._combos.values(), self.chk_recursive, self.out_field,
                                          self.btn_remove, self.btn_clear, self._adv_body]
        self._annotate_profiles()
        self._load_advanced()

    def _update_desc(self, kind: str) -> None:
        eff, base = self._profile(kind), self._base_profile(kind)
        diff = advanced.summarize(eff, base)
        self._descs[kind].setText(base.description + (f"\nRéglages personnalisés : {diff}." if diff else ""))

    def _profile(self, kind: str) -> Profile:
        """Profil effectif : profil choisi + réglages avancés (CRF, preset, qualité, threads)."""
        base = self._base_profile(kind)
        store = self._overrides_store()
        ov = advanced.Overrides.from_dict(store.get(base.id))
        threads = advanced.clamp_threads(self.prefs.get("threads", 0)) if hasattr(self, "spin_threads") else 0
        return advanced.apply_overrides(base, ov, threads, self._encoders)

    def _profiles(self) -> dict[str, Profile]:
        return {k: self._profile(k) for k in KINDS}

    def _annotate_profiles(self) -> None:
        """Marque dans les listes les profils impossibles avec ce ffmpeg."""
        for kind, combo in self._combos.items():
            for i, p in enumerate(profiles_for(kind)):
                bad = missing_encoder_message(p, self._encoders)
                combo.setItemText(i, p.label + (" — encodeur manquant" if bad else ""))


    # ── Réglages avancés (CRF, preset, qualité, threads, parallélisme) ──────
    def _build_advanced(self) -> QWidget:
        card = Card()
        self._adv_toggle = QToolButton()
        self._adv_toggle.setText("Réglages avancés")
        self._adv_toggle.setCheckable(True)
        self._adv_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._adv_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self._adv_toggle.setStyleSheet("QToolButton { border: none; font-weight: bold; }")
        card.body_layout.addWidget(self._adv_toggle)

        self._adv_body = QWidget()
        body = QVBoxLayout(self._adv_body)
        body.setContentsMargins(0, 0, 0, 0)
        hint = QLabel("Les valeurs ci-dessous remplacent celles du profil choisi (conservées par profil). "
                      "« Rétablir » revient aux valeurs d'origine.")
        hint.setWordWrap(True)
        body.addWidget(hint)

        self._adv_rows: dict[str, dict[str, object]] = {}
        for kind in ("image", "video"):
            title = QLabel(KIND_TITLES[kind])
            title.setStyleSheet("font-weight: bold;")
            form = QFormLayout()
            form.setContentsMargins(0, 0, 0, 0)
            crf = QSpinBox()
            crf.setToolTip("Constant Rate Factor : plus bas = meilleure qualité, fichier plus gros.")
            preset = QComboBox()
            preset.setToolTip("Compromis vitesse / compression : les presets lents compressent mieux.")
            quality = QSpinBox()
            quality.setToolTip("Qualité de sortie (JPEG : 2 = meilleure, 31 = pire ; WebP : 100 = meilleure).")
            lab_crf, lab_preset, lab_quality = QLabel("CRF"), QLabel("Preset"), QLabel("Qualité")
            form.addRow(lab_crf, crf)
            form.addRow(lab_preset, preset)
            form.addRow(lab_quality, quality)
            reset = QPushButton("Rétablir")
            reset.setProperty("kind", "secondary")
            for w in (crf, quality):
                w.valueChanged.connect(lambda _v, k=kind: self._on_advanced_changed(k))
            preset.currentIndexChanged.connect(lambda _i, k=kind: self._on_advanced_changed(k))
            reset.clicked.connect(lambda _c=False, k=kind: self._reset_advanced(k))
            box = QWidget()
            bl = QVBoxLayout(box)
            bl.setContentsMargins(0, 0, 0, 0)
            bl.addWidget(title)
            bl.addLayout(form)
            bl.addWidget(reset)
            body.addWidget(box)
            self._adv_rows[kind] = {"box": box, "crf": crf, "preset": preset, "quality": quality,
                                    "labels": (lab_crf, lab_preset, lab_quality), "reset": reset}

        form2 = QFormLayout()
        form2.setContentsMargins(0, 0, 0, 0)
        self.spin_threads = QSpinBox()
        self.spin_threads.setRange(0, advanced.MAX_THREADS)
        self.spin_threads.setSpecialValueText("Auto")
        self.spin_threads.setToolTip("Threads par encodage (images et vidéos). « Auto » : laisse l'encodeur décider "
                                     "(ou partage les cœurs si plusieurs fichiers sont traités en parallèle).")
        self.spin_parallel = QSpinBox()
        self.spin_parallel.setRange(1, advanced.MAX_PARALLEL)
        self.spin_parallel.setToolTip("Nombre de fichiers convertis en même temps. Réduisez-le si la mémoire manque.")
        form2.addRow("Threads", self.spin_threads)
        form2.addRow("Fichiers en parallèle", self.spin_parallel)
        body.addLayout(form2)
        self.spin_threads.setValue(advanced.clamp_threads(self.prefs.get("threads", 0)))
        self.spin_parallel.setValue(advanced.clamp_parallel(self.prefs.get("parallel", 1)))
        self.spin_threads.valueChanged.connect(self._on_perf_changed)
        self.spin_parallel.valueChanged.connect(self._on_perf_changed)

        card.body_layout.addWidget(self._adv_body)
        open_ = bool(self.prefs.get("advanced_open", False))
        self._adv_body.setVisible(open_)
        self._adv_toggle.setChecked(open_)
        self._adv_toggle.setArrowType(Qt.ArrowType.DownArrow if open_ else Qt.ArrowType.RightArrow)
        self._adv_toggle.toggled.connect(self._on_adv_toggled)
        return card

    def _on_adv_toggled(self, open_: bool) -> None:
        self._adv_body.setVisible(open_)
        self._adv_toggle.setArrowType(Qt.ArrowType.DownArrow if open_ else Qt.ArrowType.RightArrow)
        self.prefs.set("advanced_open", bool(open_))

    def _overrides_store(self) -> dict:
        data = self.prefs.get("advanced", {})
        return dict(data) if isinstance(data, dict) else {}

    def _base_profile(self, kind: str) -> Profile:
        try:
            return get_profile(self._combos[kind].currentData())
        except KeyError:
            return default_profile(kind)

    def _load_advanced(self) -> None:
        """Remplit les champs de chaque famille avec le profil courant (valeurs d'origine ou personnalisées)."""
        self._loading_adv = True
        try:
            store = self._overrides_store()
            for kind, row in self._adv_rows.items():
                base = self._base_profile(kind)
                ov = advanced.Overrides.from_dict(store.get(base.id))
                eff = advanced.apply_overrides(base, ov, 0, self._encoders)
                crf, preset, quality = row["crf"], row["preset"], row["quality"]
                lab_crf, lab_preset, lab_quality = row["labels"]
                r = advanced.crf_range(base)
                crf.setVisible(r is not None)
                lab_crf.setVisible(r is not None)
                if r:
                    crf.setRange(*r)
                    crf.setValue(eff.crf)
                choices = advanced.preset_choices(base, self._encoders)
                preset.setVisible(bool(choices))
                lab_preset.setVisible(bool(choices))
                preset.clear()
                for c in choices:
                    preset.addItem(c, c)
                if choices:
                    preset.setCurrentIndex(max(0, preset.findData(eff.preset)))
                q = advanced.quality_range(base)
                quality.setVisible(q is not None)
                lab_quality.setVisible(q is not None)
                if q:
                    quality.setRange(*q)
                    quality.setValue(eff.quality)
                row["box"].setVisible(r is not None or bool(choices) or q is not None)
        finally:
            self._loading_adv = False

    def _on_advanced_changed(self, kind: str) -> None:
        if self._loading_adv:
            return
        base = self._base_profile(kind)
        row = self._adv_rows[kind]
        ov = advanced.Overrides(
            crf=int(row["crf"].value()) if advanced.crf_range(base) else None,
            preset=row["preset"].currentData() if advanced.preset_choices(base, self._encoders) else None,
            quality=int(row["quality"].value()) if advanced.quality_range(base) else None)
        store = self._overrides_store()
        diff = {k: v for k, v in ov.to_dict().items() if getattr(base, k) != v}
        if diff:
            store[base.id] = diff
        else:
            store.pop(base.id, None)
        self.prefs.set("advanced", store)
        self._update_desc(kind)
        self._refresh_states()

    def _reset_advanced(self, kind: str) -> None:
        store = self._overrides_store()
        store.pop(self._base_profile(kind).id, None)
        self.prefs.set("advanced", store)
        self._load_advanced()
        self._update_desc(kind)
        self._refresh_states()

    def _on_perf_changed(self, _value: int) -> None:
        self.prefs.set("threads", int(self.spin_threads.value()))
        self.prefs.set("parallel", int(self.spin_parallel.value()))
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
        self.set_run_enabled(self._bins_ok and not self.is_busy())
        return self._bins_ok

    def _probe_encoders(self) -> None:
        if not self._bins_ok or self._ffmpeg is None:
            return
        if self.is_busy():
            QTimer.singleShot(100, self._probe_encoders)
            return
        self.run_worker(ProbeWorker(self._ffmpeg), on_completed=self._on_encoders,
                        on_failed=lambda _m: self._after_worker(), on_cancelled=self._after_worker)

    def _on_encoders(self, result: object) -> None:
        self._encoders = result if isinstance(result, frozenset) else None
        self._encoders_for = self._ffmpeg
        self._annotate_profiles()
        self._load_advanced()
        self._refresh_states()
        if self._encoders is not None:
            bad = [p.label for p in (get_profile(i) for i in
                                     [c.itemData(n) for c in self._combos.values() for n in range(c.count())])
                   if missing_encoder_message(p, self._encoders)]
            if bad:
                self.log_panel.append_log("WARN", "Profils indisponibles avec ce FFmpeg : " + " ; ".join(bad) + ".")
            else:
                self.log_panel.append_log("INFO", f"{len(self._encoders)} encodeurs détectés : tous les profils sont disponibles.")
        self._after_worker()

    # ── Gestion de la file ──────────────────────────────────────────────────
    def _row_for(self, item: SourceItem, profiles: dict[str, Profile], state: str) -> dict:
        shown = item.path.name if item.root is None else f"{item.root.name}/{item.rel.as_posix()}"
        return {"file": shown, "kind": KIND_LABELS[item.kind], "size": format_size(item.size),
                "profile": profiles[item.kind].label, "state": state, "progress": "", "_tag": state_tag(state)}

    def _refresh_states(self) -> None:
        if self.is_busy():
            return
        profiles = self._profiles()
        rows = [self._row_for(it, profiles, initial_state(it, profiles[it.kind], self._encoders))
                for it in self._items]
        self.table.data_model.set_rows(rows)

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
        worker = ScanWorker(paths, self.chk_recursive.isChecked())
        self.run_worker(worker, on_completed=self._on_scanned,
                        on_failed=lambda _m: self._after_worker(), on_cancelled=self._after_worker)

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
            if self.out_field.path().strip() == "" and any(i.root is None for i in new):
                last = str(self.prefs.get("output_dir", "") or "")
                if last:
                    self.out_field.set_path(last)       # fichiers isolés : dernier dossier utilisé
            profiles = self._profiles()
            rows = list(self.table.data_model.rows())
            for it in new:
                self._items.append(it)
                rows.append(self._row_for(it, profiles, initial_state(it, profiles[it.kind], self._encoders)))
            self.table.data_model.set_rows(rows)
        self.log_panel.append_log("INFO", f"{len(new)} fichier(s) ajouté(s) à la file"
                                          + (f" ({dup} déjà présent(s))." if dup else "."))
        self._after_worker()

    def _on_remove(self) -> None:
        if self.is_busy():
            return
        sel = set(self.table.selected_rows())
        if not sel:
            return
        rows = list(self.table.data_model.rows())
        keep = [i for i in range(len(self._items)) if i not in sel]
        self._items = [self._items[i] for i in keep]
        self._keys = {path_key(i.path) for i in self._items}
        self.table.data_model.set_rows([rows[i] for i in keep])

    def _on_clear(self) -> None:
        if self.is_busy():
            return
        self._items.clear()
        self._keys.clear()
        self.table.data_model.clear()

    def _on_profile_changed(self, kind: str) -> None:
        self.prefs.set(f"profile_{kind}", self._combos[kind].currentData())
        self._load_advanced()
        self._update_desc(kind)
        self._refresh_states()

    def _on_out_changed(self, text: str) -> None:
        if text.strip():
            self.prefs.set("output_dir", text.strip())

    # ── Ouverture ───────────────────────────────────────────────────────────
    def on_open(self) -> None:
        box = QMessageBox(self)
        box.setWindowTitle("Ajouter à la file")
        box.setText("Que souhaitez-vous ajouter ?")
        b_files = box.addButton("Fichiers…", QMessageBox.ButtonRole.AcceptRole)
        b_dir = box.addButton("Dossier…", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Annuler", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        prefs = Preferences.instance()
        start = str(prefs.get("last_dir", "") or "")
        chosen: list[str] = []
        if clicked is b_files:
            filt = "Médias (" + " ".join(f"*{e}" for e in sorted(ALL_EXTS)) + ");;Tous les fichiers (*)"
            chosen, _ = QFileDialog.getOpenFileNames(self, "Choisir des fichiers", start, filt)
        elif clicked is b_dir:
            d = QFileDialog.getExistingDirectory(self, "Choisir un dossier", start)
            chosen = [d] if d else []
        if chosen:
            first = Path(chosen[0])
            prefs.set("last_dir", str(first if first.is_dir() else first.parent))
            self.open_paths(chosen)

    # ── Conversion ──────────────────────────────────────────────────────────
    def on_run(self) -> None:
        if self.is_busy():
            return
        if not self._items:
            self.log_panel.append_log("WARN", "La file est vide : ajoutez des fichiers ou des dossiers.")
            return
        if not self._locate_binaries(log=True) or self._ffmpeg is None or self._ffprobe is None:
            return
        txt = self.out_field.path().strip()
        out_dir = Path(txt) if txt else None
        if out_dir is None and any(i.root is None for i in self._items):
            self.log_panel.append_log("ERR", "Choisissez un dossier de sortie : il est obligatoire pour les "
                                             "fichiers ajoutés isolément.")
            return
        if out_dir is not None and out_dir.exists() and not out_dir.is_dir():
            self.log_panel.append_log("ERR", f"Le dossier de sortie est un fichier : {out_dir}")
            return
        encoders = self._encoders if self._encoders_for == self._ffmpeg else None
        worker = ConvertWorker(self._items, self._profiles(), out_dir, self._ffmpeg, self._ffprobe, encoders,
                               parallel=int(self.spin_parallel.value()))
        worker.job_updated.connect(self._on_job_updated)
        self.run_worker(worker, on_completed=lambda _r: self._after_worker(),
                        on_failed=lambda _m: self._after_worker(), on_cancelled=self._on_cancelled,
                        lock_widgets=self._lockables, history_action="Conversion")

    def _on_job_updated(self, index: int, values: object) -> None:
        if not isinstance(values, dict) or not 0 <= index < len(self._items):
            return
        upd: dict = {}
        if "state" in values:
            upd["state"] = values["state"]
            upd["_tag"] = state_tag(values["state"])
        if "progress" in values:
            upd["progress"] = "" if values["progress"] is None else f"{values['progress']} %"
        if upd:
            self.table.data_model.update_row(index, **upd)

    def _on_cancelled(self) -> None:
        for i, row in enumerate(self.table.data_model.rows()):
            if row.get("state") == STATE_RUNNING:
                self.table.data_model.update_row(i, state=STATE_CANCELLED, _tag=state_tag(STATE_CANCELLED))
        self._after_worker()

    # ── Fin de traitement ───────────────────────────────────────────────────
    def _after_worker(self) -> None:
        QTimer.singleShot(0, self._drain_pending)

    def _drain_pending(self) -> None:
        if self.is_busy():
            QTimer.singleShot(100, self._drain_pending)
            return
        self.set_run_enabled(self._bins_ok)
        if self._pending:
            paths = [p for lst in self._pending for p in lst]
            self._pending.clear()
            self._start_scan(paths)
