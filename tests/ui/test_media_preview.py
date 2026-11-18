"""Lecteur intégré (Dedup) : aperçu image ou lecteur audio/vidéo, curseur cliquable, volume commun.

Seuls les comportements synchrones sont vérifiés : la lecture réelle dépend du backend multimédia et de la
carte son de la machine de test.
"""
from __future__ import annotations

import os
import wave
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from azimut.core.prefs import Preferences
from azimut.ui import widgets
from azimut.ui.theme import ThemeManager
from azimut.ui.widgets import MediaPreview, SeekSlider, VideoPreview, _AudioBus

needs_multimedia = pytest.mark.skipif(not widgets.MULTIMEDIA_AVAILABLE, reason="QtMultimedia (PySide6-Addons) requis")


def _audio_output_available() -> bool:
    """Une sortie audio est-elle disponible ? Sans serveur de son, Qt ne propage pas le volume."""
    try:
        from PySide6.QtMultimedia import QMediaDevices
    except Exception:                                    # PySide6-Essentials : pas de QtMultimedia
        return False
    QApplication.instance() or QApplication([])           # la sonde exige une application
    return bool(QMediaDevices.audioOutputs())


# Prémisse absente sur une machine sans carte ni serveur de son (CI) : le bus de volume
# n'est alors pas propagé par Qt. Ce test demande donc une machine avec audio.
needs_audio_output = pytest.mark.skipif(
    not _audio_output_available(), reason="aucune sortie audio (CI sans serveur de son)")


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def manager(qt_app, tmp_path, monkeypatch):
    tm = ThemeManager(Preferences(tmp_path / "prefs.json"))
    monkeypatch.setattr(ThemeManager, "_instance", tm)
    yield tm
    bus = _AudioBus.instance()                                   # état commun remis à zéro entre les tests
    bus.level, bus.muted = 100, False


def _wav(path: Path, seconds: float = 0.2) -> Path:
    with wave.open(str(path), "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(8000)
        fh.writeframes(b"\x00\x00" * int(8000 * seconds))
    return path


def _png(path: Path) -> Path:
    from PySide6.QtGui import QColor, QImage
    image = QImage(20, 20, QImage.Format.Format_RGB32)
    image.fill(QColor("red"))
    assert image.save(str(path))
    return path


def test_playable_types():
    for name in ("a.mp3", "a.FLAC", "a.m4a", "a.mp4", "a.MKV", "a.webm"):
        assert MediaPreview.is_playable(name), name
    for name in ("a.jpg", "a.pdf", "a.txt", "a.exe", "a"):
        assert not MediaPreview.is_playable(name), name


def test_without_multimedia_everything_stays_an_image_preview(qt_app, tmp_path, monkeypatch):
    monkeypatch.setattr(widgets, "MULTIMEDIA_AVAILABLE", False)
    preview = MediaPreview(size=120)
    assert preview._player is None
    preview.show_file(_wav(tmp_path / "a.wav"))
    assert not preview.can_play() and not preview.is_playing()
    preview.toggle_playback()                                    # sans effet, sans exception
    preview.pause()
    preview.release()


def test_image_file_uses_the_image_preview(qt_app, tmp_path):
    preview = MediaPreview(size=120)
    preview.show_file(_png(tmp_path / "a.png"))
    assert not preview.can_play()
    assert preview._image.isVisibleTo(preview)
    assert preview.current_path() == tmp_path / "a.png"


def test_text_and_clear_leave_no_current_file(qt_app):
    preview = MediaPreview(size=120)
    preview.show_text("Groupe 3")
    assert preview.current_path() is None and not preview.can_play()
    preview.clear()
    assert preview.current_path() is None


@needs_multimedia
def test_audio_file_opens_the_player_and_release_lets_go(qt_app, tmp_path):
    preview = MediaPreview(size=120)
    wav = _wav(tmp_path / "a.wav")
    preview.show_file(wav)
    assert preview.can_play()
    assert preview._player.isVisibleTo(preview) and not preview._image.isVisibleTo(preview)
    assert preview._player.source() == wav
    preview.release()
    assert preview._player.source() is None and not preview.can_play()
    assert preview.current_path() is None


@needs_multimedia
def test_same_file_twice_does_not_reload_the_player(qt_app, tmp_path, monkeypatch):
    """Garder / Supprimer rafraîchit l'aperçu : la lecture en cours ne doit pas repartir de zéro."""
    preview = MediaPreview(size=120)
    wav = _wav(tmp_path / "a.wav")
    calls: list[Path] = []
    real = preview._player.set_source
    monkeypatch.setattr(preview._player, "set_source", lambda p: (calls.append(Path(p)), real(p)))
    preview.show_file(wav)
    preview.show_file(wav)
    preview.show_file(wav)
    assert calls == [wav]
    other = _wav(tmp_path / "b.wav")
    preview.show_file(other)
    assert calls == [wav, other]


@needs_multimedia
def test_switching_from_audio_to_image_stops_the_player(qt_app, tmp_path):
    preview = MediaPreview(size=120)
    preview.show_file(_wav(tmp_path / "a.wav"))
    preview.show_file(_png(tmp_path / "a.png"))
    assert preview._player.source() is None
    assert preview._image.isVisibleTo(preview) and not preview._player.isVisibleTo(preview)


@needs_multimedia
def test_failed_file_does_not_block_the_next_one(qt_app, tmp_path):
    video = VideoPreview(size=80)
    video.set_source(_wav(tmp_path / "a.wav"))
    video._on_error(None, "codec absent")
    assert video.has_failed() and not video.play_btn.isEnabled()
    video.set_source(_wav(tmp_path / "b.wav"))
    assert not video.has_failed() and video.play_btn.isEnabled()


@needs_multimedia
def test_new_audio_bus_starts_at_full_volume():
    bus = _AudioBus()
    assert bus.level == 100
    assert bus.gain == 1.0


def test_volume_slider_starts_at_full_volume(qt_app):
    bus = _AudioBus.instance()
    bus.set_level(100)
    bus.set_muted(False)
    video = VideoPreview(size=80, volume_controls=True)
    assert video.volume_slider.value() == 100
    video.close()


@needs_audio_output
def test_volume_and_mute_are_shared_between_players(qt_app):
    a, b = VideoPreview(size=80, volume_controls=True), VideoPreview(size=80, volume_controls=True)
    a.volume_slider.setValue(30)
    assert b.volume_slider.value() == 30 and _AudioBus.instance().level == 30
    b.mute_btn.setChecked(True)
    assert a.mute_btn.isChecked() and _AudioBus.instance().muted
    assert a._volume_icon_name() == "volume-cross" and not a.mute_btn.icon().isNull()
    b.mute_btn.setChecked(False)
    a.volume_slider.setValue(100)                             # état partagé remis à zéro pour les tests suivants


def test_default_video_preview_keeps_its_original_look(qt_app):
    video = VideoPreview(size=80)                                # sans option : pas de rail de volume
    assert video._vol_panel is None and video.mute_btn is None


def test_volume_slider_stays_compact_beside_a_large_preview(qt_app):
    video = VideoPreview(size=260, volume_controls=True)
    video.show()
    video._vol_panel.show()
    qt_app.processEvents()
    try:
        assert video.volume_slider.height() <= VideoPreview.VOLUME_MAX_HEIGHT
        assert video.volume_slider.height() < video._box.height() * 0.6
    finally:
        video.close()


def test_volume_rail_sits_right_of_the_media_box(qt_app):
    _AudioBus.instance().set_muted(False)
    video = VideoPreview(size=80, volume_controls=True)
    video.show()
    video._vol_panel.show()
    qt_app.processEvents()
    assert video._vol_panel.geometry().left() >= video._box.geometry().right()
    assert abs(video._box.geometry().center().x() - video.rect().center().x()) <= 1
    assert video._volume_icon_name() == "volume"
    video.mute_btn.setChecked(True)
    assert video._volume_icon_name() == "volume-cross"
    video.mute_btn.setChecked(False)
    video.close()


def test_seek_slider_jumps_to_the_clicked_position(qt_app):
    slider = SeekSlider(Qt.Orientation.Horizontal)
    slider.setRange(0, 1000)
    slider.resize(200, 24)
    slider.show()
    moved: list[int] = []
    slider.sliderMoved.connect(moved.append)
    QTest.mouseClick(slider, Qt.MouseButton.LeftButton, pos=QPoint(150, 12))
    assert 600 < slider.value() < 900
    assert moved, "sliderMoved doit être émis : le lecteur s'y raccorde pour se déplacer"


def test_seek_slider_ignores_clicks_without_a_range(qt_app):
    slider = SeekSlider(Qt.Orientation.Horizontal)
    slider.setRange(0, 0)
    slider.resize(200, 24)
    slider.show()
    QTest.mouseClick(slider, Qt.MouseButton.LeftButton, pos=QPoint(150, 12))
    assert slider.value() == 0
