"""AZ-C03 : une fenêtre de module ne dépasse jamais la zone utile de l'écran."""
from __future__ import annotations

from azimut.core.screenfit import FLOOR_H, FLOOR_W, fit_to_screen


def test_large_screen_keeps_the_wanted_size():
    assert fit_to_screen(1180, 780, (1920, 1040)) == (1180, 780)
    assert fit_to_screen(960, 640, (2560, 1400)) == (960, 640)


def test_laptop_1366x768_at_125_percent_is_reduced():
    width, height = fit_to_screen(1180, 780, (1093, 614))
    assert width <= 1093 and height <= 614
    min_w, min_h = fit_to_screen(960, 640, (1093, 614))
    assert (min_w, min_h) == (960, 558)                       # le minimum se réduit aussi : plus de bouton caché


def test_1080p_at_175_percent():
    assert fit_to_screen(1180, 780, (1097, 617))[1] <= 617


def test_unknown_screen_keeps_the_wanted_size():
    assert fit_to_screen(960, 640, None) == (960, 640)


def test_tiny_or_broken_screen_never_goes_below_the_floor():
    assert fit_to_screen(960, 640, (300, 200)) == (FLOOR_W, FLOOR_H)
    assert fit_to_screen(960, 640, (0, 0)) == (960, 640)      # zone vide : donnée invalide, taille voulue conservée
