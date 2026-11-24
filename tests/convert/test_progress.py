"""Progression de Convert : une seule barre de 0 à 100 %, découpée selon le nombre de fichiers."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import time
from pathlib import Path

from azimut.modules.convert import engine, profiles
from azimut.modules.convert.engine import SourceItem
from azimut.modules.convert.profiles import OTHER_KIND

ALL_ENC = frozenset({"libsvtav1", "libaom-av1", "libx264", "mjpeg", "libwebp",
                     "libmp3lame", "aac", "libopus"})


def _worker():
    from azimut.modules.convert.workers import ConvertWorker

    by_kind = {k: profiles.default_profile(k) for k in profiles.KINDS}
    by_kind[OTHER_KIND] = profiles.COPY_PROFILE
    return ConvertWorker([], by_kind, Path("/out"), Path("ffmpeg"), Path("ffprobe"), ALL_ENC)


def _many_jobs():
    """10 fichiers : 1 copie, 3 images, 1 audio et 5 vidéos, pour vérifier les proportions."""
    items = [SourceItem(Path("/src/doc.pdf"), Path("/src"), OTHER_KIND, 2_000_000)]
    items += [SourceItem(Path(f"/src/p{n}.jpg"), Path("/src"), "image", 1_000_000) for n in range(3)]
    items.append(SourceItem(Path("/src/a.mp3"), Path("/src"), "audio", 5_000_000))
    items += [SourceItem(Path(f"/src/v{n}.mp4"), Path("/src"), "video", 50_000_000) for n in range(5)]
    by_kind = {k: profiles.default_profile(k) for k in profiles.KINDS}
    by_kind[OTHER_KIND] = profiles.COPY_PROFILE
    return engine.plan_jobs(items, by_kind, Path("/out"), ALL_ENC, check_disk=False)


def _jobs():
    image = SourceItem(Path("/src/a.jpg"), Path("/src"), "image", 1_000_000)
    video = SourceItem(Path("/src/b.mp4"), Path("/src"), "video", 100_000_000)
    audio = SourceItem(Path("/src/c.mp3"), Path("/src"), "audio", 5_000_000)
    other = SourceItem(Path("/src/d.pdf"), Path("/src"), OTHER_KIND, 2_000_000)
    by_kind = {k: profiles.default_profile(k) for k in profiles.KINDS}
    by_kind[OTHER_KIND] = profiles.COPY_PROFILE
    return engine.plan_jobs([image, video, audio, other], by_kind, Path("/out"), ALL_ENC, check_disk=False)


def test_steps_group_copies_images_audio_video(qapp):
    worker = _worker()
    steps = worker._plan_steps(_jobs())
    assert [key for key, _label, _group in steps] == ["copy", "image", "audio", "video"]
    assert [label for _key, label, _group in steps] == ["Copies", "Images", "Audios", "Vidéos"]
    assert [group[0].item.kind for _key, _label, group in steps] == [OTHER_KIND, "image", "audio", "video"]
    assert worker._total_jobs == 4
    video_job = steps[3][2][0]
    assert worker._weights[video_job.index] > sum(
        weight for index, weight in worker._weights.items() if index != video_job.index)


def test_bar_segments_are_proportional_to_file_counts(qapp):
    """3 images, 1 audio, 5 vidéos et 1 copie donnent 30 %, 10 %, 50 % et 10 % de la piste."""
    worker = _worker()
    announced: list[list[tuple[str, str, int]]] = []
    worker.steps_planned.connect(announced.append)
    worker._plan_steps(_many_jobs())
    assert announced and sum(count for _key, _label, count in announced[0]) == 10
    assert {key: count for key, _label, count in announced[0]} == {
        "copy": 1, "image": 3, "audio": 1, "video": 5}


def test_progress_is_one_monotonic_pass_counting_files(qapp):
    worker = _worker()
    steps = worker._plan_steps(_jobs())
    seen: list[int] = []
    worker.progress.connect(lambda done, total, _label: seen.append(done) if total == 100 else None)
    video = steps[3][2][0]
    image = steps[1][2][0]
    worker._publish_progress(video, 0.5)
    time.sleep(0.11)                                    # laisse passer le limiteur d'émission
    worker._publish_progress(image, 1.0)
    time.sleep(0.11)
    worker._publish_progress(video, 0.2)                # un fichier qui revoit son pourcentage ne recule pas
    assert seen == sorted(seen) and len(set(seen)) == len(seen)
    assert all(0 <= pct <= 99 for pct in seen)          # 100 % est réservé à la fin du lot
    assert seen[-1] < 70                                # 1,5 fichier sur 4 : l'image terminée ne suffit pas


def test_eta_weights_files_while_the_bar_counts_them(qapp):
    """La vidéo terminée donne 25 % de barre, mais presque 100 % du temps estimé."""
    worker = _worker()
    steps = worker._plan_steps(_jobs())
    seen: list[int] = []
    worker.progress.connect(lambda done, total, _label: seen.append(done) if total == 100 else None)
    video = steps[3][2][0]
    worker._publish_progress(video, 1.0)
    assert seen == [25]                                 # 1 fichier sur 4
    assert worker._weighted / worker._total_weight > 0.9  # mais la vidéo pèse la majorité du temps


def test_worker_emits_a_single_pass_from_0_to_100(qapp, tmp_path, monkeypatch):
    from azimut.modules.convert.workers import ConvertOptions, ConvertWorker

    jobs = _jobs()
    by_kind = {k: profiles.default_profile(k) for k in profiles.KINDS}
    by_kind[OTHER_KIND] = profiles.COPY_PROFILE

    class NoopWorker(ConvertWorker):
        """Le temps du test, chaque fichier « réussit » sans écrire sur le disque."""

        def _convert_one(self, job):
            return "ok"

        def _verify(self, jobs):
            return 0

        def _write_problems(self, jobs):
            return None

        def _save_manifests(self, jobs, manifests):
            return None

    monkeypatch.setattr("azimut.modules.convert.workers.engine.plan_jobs", lambda *a, **k: jobs)
    worker = NoopWorker([], by_kind, tmp_path / "out", Path("ffmpeg"), Path("ffprobe"), ALL_ENC,
                        options=ConvertOptions(memory_guard=False))
    seen: list[tuple[int, int]] = []
    worker.progress.connect(lambda done, total, _label: seen.append((done, total)))
    segments: list[list[tuple[str, str, int]]] = []
    worker.steps_planned.connect(segments.append)
    worker.do_work()
    assert segments and sum(count for _key, _label, count in segments[0]) == len(jobs)
    assert [total for _done, total in seen] == [100] * len(seen)   # jamais un compteur de fichiers
    values = [done for done, _total in seen]
    assert values[0] == 0 and values[-1] == 100
    assert values == sorted(values) and values.count(100) == 1


def test_window_maps_step_counts_to_bar_segments(qapp):
    from azimut.modules.convert.window import ConvertWindow, SegmentLegend
    from azimut.ui.widgets import LiquidProgressBar

    class Host:
        progress_bar = LiquidProgressBar()
        progress_legend = SegmentLegend()
        _sync_legend_active = ConvertWindow._sync_legend_active

        @staticmethod
        def tr(text: str) -> str:
            return text

    host = Host()
    ConvertWindow._on_segments(host, [("copy", "Copies", 1), ("image", "Images", 3),
                                       ("audio", "Audios", 1), ("video", "Vidéos", 5)])
    assert [round(part, 3) for part, _color in host.progress_bar._segments] == [0.1, 0.3, 0.1, 0.5]
    legend = host.progress_bar.toolTip()
    assert "Légende des couleurs" in legend
    assert "Copies (gris) : 1" in legend and "Images (bleu) : 3" in legend
    assert "Audios (violet) : 1" in legend and "Vidéos (orange) : 5" in legend
    # La légende visible reprend les mêmes effectifs et suit la famille sous le front.
    assert host.progress_legend._entries == [("Copies", "seg_copy", 1), ("Images", "seg_image", 3),
                                             ("Audios", "seg_audio", 1), ("Vidéos", "seg_video", 5)]
    assert host.progress_legend._active == 0
    ConvertWindow._sync_legend_active(host, 45)
    assert host.progress_legend._active == 2
    ConvertWindow._sync_legend_active(host, 100)
    assert host.progress_legend._active == 3
    assert "Images" in host.progress_legend.text() and "3" in host.progress_legend.text()
    ConvertWindow._on_segments(host, [])
    assert host.progress_bar._segments == []
    assert host.progress_legend._entries == [] and host.progress_legend.isHidden()


def test_real_window_legend_follows_bar_value(qapp):
    """La famille en gras suit la valeur réelle de la barre de la vraie ConvertWindow."""
    from azimut.modules.convert.window import ConvertWindow

    win = ConvertWindow()
    win._probe_timer.stop()
    try:
        win.progress_bar.setRange(0, 100)
        win._on_segments([("copy", "Copies", 1), ("image", "Images", 3),
                          ("audio", "Audios", 1), ("video", "Vidéos", 5)])
        expected = [(0, 0), (9, 0), (10, 1), (39, 1), (40, 2),
                    (49, 2), (50, 3), (99, 3), (100, 3)]
        for value, active in expected:
            win.progress_bar.setValue(value)          # valueChanged → _sync_legend_active
            assert win.progress_legend._active == active, value
        win._after_worker()
        assert win.progress_legend.isHidden()
    finally:
        win.close()
        win.deleteLater()
        qapp.processEvents()


def test_parallel_pool_stays_silent_when_the_task_publishes_the_global_bar(qapp):
    from azimut.core.workers import BaseWorker

    class Pool(BaseWorker):
        def run_pool(self):
            return self.map_parallel(lambda x: x, [1, 2, 3], workers=2, report_progress=False)

    worker = Pool()
    seen: list[tuple] = []
    worker.progress.connect(lambda *args: seen.append(args))
    assert worker.run_pool() == [1, 2, 3]
    assert seen == []


def test_status_names_the_step_counts_and_remaining_time(qapp):
    worker = _worker()
    worker._plan_steps(_jobs())
    texts: list[str] = []
    worker.status.connect(texts.append)
    worker._step = ("Images", 2, 4, 10, {"done": 3})
    worker._started = time.monotonic() - 100
    worker._weighted = worker._total_weight / 2
    worker._publish_status(force=True)
    assert texts and "Étape 2/4" in texts[-1] and "Images" in texts[-1] and "3/10" in texts[-1]
    assert "reste" in texts[-1]


def test_status_omits_the_estimate_before_any_progress(qapp):
    worker = _worker()
    worker._plan_steps(_jobs())
    texts: list[str] = []
    worker.status.connect(texts.append)
    worker._step = ("Copies", 1, 2, 4, {"done": 0})
    worker._started = time.monotonic() - 100
    worker._publish_status(force=True)
    assert texts and "reste" not in texts[-1]
