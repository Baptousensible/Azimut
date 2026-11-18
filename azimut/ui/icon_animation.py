"""Animations de survol du thème « icônes animées » (lucide-animated).

Les composants de https://github.com/pqoqubbw/icons décrivent chaque mouvement avec
Motion (React) : rotations du SVG entier, translation d'un tracé, scale d'une forme,
opacité d'un élément. Qt ne sait pas jouer ces composants ; ce module traduit les
variantes en pistes de valeurs normalisées (0 → 1), que :mod:`azimut.ui.icons`
applique aux différents éléments du SVG au moment du rendu.

Chaque piste est une liste de valeurs aux temps indiqués. Les temps manquants sont
répartis régulièrement ; l'interpolation est lissée (smoothstep) pour retrouver le
naturel des ressorts de Motion sans en simuler la physique.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Track:
    """Suite de valeurs atteintes aux temps donnés (0 → 1), interpolées en douceur."""

    values: tuple[float, ...]
    times: tuple[float, ...] = ()

    def __post_init__(self) -> None:
        if not self.values:
            raise ValueError("une piste doit contenir au moins une valeur")
        if self.times and len(self.times) != len(self.values):
            raise ValueError("autant de temps que de valeurs sont nécessaires")

    def at(self, progress: float) -> float:
        values = self.values
        if len(values) == 1:
            return values[0]
        times = self.times or tuple(index / (len(values) - 1) for index in range(len(values)))
        t = max(0.0, min(1.0, float(progress)))
        if t <= times[0]:
            return values[0]
        if t >= times[-1]:
            return values[-1]
        for index in range(len(times) - 1):
            start, end = times[index], times[index + 1]
            if start <= t <= end:
                span = end - start
                if span <= 0.0:
                    return values[index + 1]
                k = (t - start) / span
                k = k * k * (3.0 - 2.0 * k)          # smoothstep, proche de Motion
                return values[index] + (values[index + 1] - values[index]) * k
        return values[-1]


@dataclass(frozen=True)
class Part:
    """Mouvement d'un élément SVG (``id``) ou du pictogramme entier (``*``)."""

    element: str = "*"
    x: Track | None = None
    y: Track | None = None
    rotate: Track | None = None
    scale: Track | None = None
    scale_x: Track | None = None
    scale_y: Track | None = None
    opacity: Track | None = None

    def state(self, progress: float) -> "State":
        """Valeurs du mouvement à la progression donnée (identité par défaut)."""
        uniform = self.scale.at(progress) if self.scale is not None else 1.0
        return State(
            x=self.x.at(progress) if self.x is not None else 0.0,
            y=self.y.at(progress) if self.y is not None else 0.0,
            rotate=self.rotate.at(progress) if self.rotate is not None else 0.0,
            scale_x=(self.scale_x.at(progress) if self.scale_x is not None else 1.0) * uniform,
            scale_y=(self.scale_y.at(progress) if self.scale_y is not None else 1.0) * uniform,
            opacity=self.opacity.at(progress) if self.opacity is not None else 1.0,
        )


@dataclass(frozen=True)
class State:
    """Transformation appliquée à une couche de l'icône."""

    x: float = 0.0
    y: float = 0.0
    rotate: float = 0.0
    scale_x: float = 1.0
    scale_y: float = 1.0
    opacity: float = 1.0

    @property
    def identity(self) -> bool:
        return (abs(self.x) < 1e-6 and abs(self.y) < 1e-6 and abs(self.rotate) < 1e-6
                and abs(self.scale_x - 1.0) < 1e-6 and abs(self.scale_y - 1.0) < 1e-6
                and abs(self.opacity - 1.0) < 1e-6)

    def combine(self, other: "State") -> "State":
        """Composition de deux mouvements : le mouvement global puis celui de l'élément."""
        return State(
            x=self.x + other.x,
            y=self.y + other.y,
            rotate=self.rotate + other.rotate,
            scale_x=self.scale_x * other.scale_x,
            scale_y=self.scale_y * other.scale_y,
            opacity=self.opacity * other.opacity,
        )


@dataclass(frozen=True)
class Animation:
    """Mouvement d'une icône et durée de survol associée."""

    parts: tuple[Part, ...]
    duration_ms: int = 420


def _t(*values: float, times: tuple[float, ...] | None = None) -> Track:
    return Track(tuple(float(value) for value in values), times or ())


# ─────────────────────────────────────────────────────────────────────────────
# Mouvements repris de lucide-animated (les noms d'éléments sont ceux des SVG de
# `resources/icons_animated/`). Les icônes absentes ont une respiration discrète.
# ─────────────────────────────────────────────────────────────────────────────
ANIMATIONS: dict[str, Animation] = {
    "alert-triangle": Animation((Part("*", rotate=_t(0, -3, 3, 0, times=(0, 0.3, 0.6, 1))),), 460),
    "archive": Animation((
        Part("p1", y=_t(0, -1.5)),
        Part("p3", y=_t(0, 3)),
    ), 340),
    "arrow-left": Animation((Part("p1", x=_t(0, 3, 0, times=(0, 0.5, 1))),), 420),
    "arrow-right": Animation((Part("p2", x=_t(0, -3, 0, times=(0, 0.5, 1))),), 420),
    "arrow-up": Animation((Part("p1", y=_t(0, 3, 0, times=(0, 0.5, 1))),), 420),
    "bookmark": Animation((Part("p1",
                                scale_x=_t(1, 0.9, 1.1, 0.95, 1, times=(0, 0.25, 0.5, 0.75, 1)),
                                scale_y=_t(1, 1.3, 0.9, 1.05, 1, times=(0, 0.25, 0.5, 0.75, 1))),), 620),
    "check": Animation((Part("p1",
                             opacity=_t(0, 1, 1, times=(0, 0.3, 1)),
                             scale=_t(0.5, 1, times=(0, 1))),), 420),
    "chevron-down": Animation((Part("p1", y=_t(0, 2, 0, times=(0, 0.4, 1))),), 500),
    "chevron-right": Animation((Part("p1", x=_t(0, 2, 0, times=(0, 0.4, 1))),), 500),
    "chevron-up": Animation((Part("p1", y=_t(0, -2, 0, times=(0, 0.4, 1))),), 500),
    "cross": Animation((
        Part("p1", opacity=_t(0, 1, 1, times=(0, 0.2, 0.45)), scale=_t(0.7, 1, 1, times=(0, 0.5, 1))),
        Part("p2", opacity=_t(0, 0, 1, times=(0, 0.35, 0.6)), scale=_t(0.7, 1, 1, times=(0, 0.5, 1))),
    ), 460),
    "folder": Animation((Part("*", y=_t(0, -1.2, 0, times=(0, 0.4, 1))),), 420),
    "gallery": Animation((Part("*", scale=_t(1, 1.16, 1, times=(0, 0.45, 1))),), 420),
    "gears": Animation((Part("*", rotate=_t(0, 180)),), 760),
    "ghost": Animation((Part("*", y=_t(0, -1.5, 0, times=(0, 0.4, 1))),), 500),
    "globe": Animation((Part("p4",
                             opacity=_t(0, 1, times=(0, 0.35)),
                             scale=_t(0.8, 1, times=(0, 0.55))),), 460),
    "grid": Animation((
        Part("p1", x=_t(0, 11, 11, 0, times=(0, 0.4, 0.6, 1))),
        Part("p2", y=_t(0, 11, 11, 0, times=(0, 0.4, 0.6, 1))),
        Part("p3", x=_t(0, -11, -11, 0, times=(0, 0.4, 0.6, 1))),
        Part("p4", y=_t(0, -11, -11, 0, times=(0, 0.4, 0.6, 1))),
    ), 820),
    "info": Animation((
        Part("*", scale=_t(1, 1.08, 1, times=(0, 0.45, 1))),
        Part("p2", y=_t(0, -1.5, 0, times=(0, 0.4, 1))),
    ), 440),
    "list": Animation((
        Part("p1", x=_t(0, 3, 0, times=(0, 0.4, 1))),
        Part("p2", x=_t(0, 3, 0, times=(0.05, 0.45, 1))),
        Part("p3", x=_t(0, 3, 0, times=(0.1, 0.5, 1))),
    ), 480),
    "lock": Animation((Part("*",
                             rotate=_t(-3, 1, -2, 0, times=(0, 0.33, 0.66, 1)),
                             scale=_t(0.95, 1.05, 0.98, 1, times=(0, 0.33, 0.66, 1))),), 900),
    "map-pin": Animation((
        Part("*", y=_t(0, -5, -3, times=(0, 0.6, 1))),
        Part("p2", opacity=_t(0, 1, times=(0, 0.6)), scale=_t(0.5, 1, times=(0, 0.6))),
    ), 600),
    "pause": Animation((
        Part("p1", y=_t(0, 2, 0, 0, times=(0, 0.2, 0.5, 1))),
        Part("p2", y=_t(0, 0, 2, 0, times=(0, 0.2, 0.5, 1))),
    ), 500),
    "pen-alt": Animation((Part("*", rotate=_t(0, -12, 4, 0, times=(0, 0.25, 0.6, 1))),), 480),
    "pie": Animation((Part("p1", x=_t(0, 1.1), y=_t(0, -1.1)),), 420),
    "play": Animation((Part("p1",
                            x=_t(0, -1, 2, 0, times=(0, 0.2, 0.5, 1)),
                            rotate=_t(0, -10, 0, 0, times=(0, 0.2, 0.5, 1))),), 500),
    "search": Animation((Part("*",
                              x=_t(0, 0, -3, 0, times=(0, 0.25, 0.6, 1)),
                              y=_t(0, -4, 0, 0, times=(0, 0.25, 0.6, 1))),), 900),
    "sparkles": Animation((
        Part("p1", y=_t(0, -1.5, 0, 0, times=(0, 0.25, 0.5, 1)),
             scale=_t(1, 1.15, 1, times=(0, 0.3, 0.6))),
        Part("p2", opacity=_t(0, 1, 0, times=(0, 0.2, 0.4))),
        Part("p3", opacity=_t(0, 1, 0, times=(0.15, 0.35, 0.55))),
        Part("p4", opacity=_t(0, 1, 0, times=(0.3, 0.5, 0.7))),
        Part("p5", opacity=_t(0, 1, 0, times=(0.45, 0.65, 0.85))),
    ), 1000),
    "swap-horizontal": Animation((
        Part("p1", x=_t(0, 1.5, 0, times=(0, 0.4, 1))),
        Part("p3", x=_t(0, -1.5, 0, times=(0, 0.4, 1))),
    ), 500),
    "tasks": Animation((
        Part("p1", scale=_t(1, 1.15, 1, times=(0, 0.4, 1))),
        Part("p2", scale=_t(1, 1.15, 1, times=(0, 0.4, 1))),
    ), 460),
    "trash": Animation((
        Part("p1", y=_t(0, -1.1)),
        Part("p2", y=_t(0, -1.1)),
    ), 320),
    "volume": Animation((
        Part("p2", opacity=_t(0, 1, 1, times=(0, 0.15, 0.4))),
        Part("p3", opacity=_t(0, 0, 1, times=(0, 0.3, 0.55))),
    ), 520),
    "volume-cross": Animation((
        Part("p2", opacity=_t(0, 1, 1, times=(0, 0.15, 0.4)), scale=_t(0.7, 1, times=(0, 0.45))),
        Part("p3", opacity=_t(0, 1, 1, times=(0, 0.15, 0.4)), scale=_t(0.7, 1, times=(0, 0.45))),
    ), 460),
    "volume-half": Animation((Part("p2", opacity=_t(0, 1, times=(0, 0.3))),), 460),
}
