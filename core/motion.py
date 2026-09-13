"""Motion tokens, easing curves and a frame tween helper."""

import math

from fabric.utils import GLib


class _ReducedMotion:
    _value: bool = False

    @classmethod
    def cell(cls) -> bool:
        return cls._value

    @classmethod
    def on(cls, value: bool) -> None:
        cls._value = bool(value)


def reduced() -> bool:
    return _ReducedMotion.cell()


def set_reduced(value: bool) -> None:
    _ReducedMotion.on(value)


# Durations (ms)
FAST = 80
STANDARD = 150
MORPH = 200
SHAPESHIFT = 400
GLIDE = 150
HEAT = 500
PULSE = 200

# Corner radii (px at scale 1).
R_SMALL = 7
R_TILE = 13


def scale_duration(ms: int) -> int:
    return int(ms * 0.4) if reduced() else ms


# --- easing curves (accept t in 0..1, return eased 0..1) ---------------------


def ease_in_out_cubic(t: float) -> float:
    t *= 2
    if t < 1:
        return 0.5 * t * t * t
    t -= 2
    return 0.5 * (t * t * t + 2)


def ease_out_cubic(t: float) -> float:
    t -= 1
    return t * t * t + 1


def ease_out_quad(t: float) -> float:
    return 1 - (1 - t) * (1 - t)


def ease_out_back(t: float) -> float:
    c1 = 1.70158
    c3 = c1 + 1
    x = t - 1
    return 1 + c3 * x * x * x + c1 * x * x


def ease_in_out_sine(t: float) -> float:
    return -(math.cos(math.pi * t) - 1) / 2


def ease_linear(t: float) -> float:
    return t


EASE_STANDARD = ease_out_cubic
EASE_MORPH = ease_out_cubic


# --- tween engine -------------------------------------------------------------


class Tween:
    """A single in-flight tween. Dutifully fires progress callbacks on a
    GLib timeout; self-cancels on any exception or when stopped."""

    def __init__(self, duration_ms: int, on_progress, easing=ease_out_cubic):
        self.duration = max(1, duration_ms)
        self.on_progress = on_progress
        self.easing = easing
        self.elapsed = 0
        self._source = None

    def start(self) -> Tween:
        step_ms = 16
        steps = max(1, math.ceil(self.duration / step_ms))

        def tick():
            self.elapsed += step_ms
            t = min(1.0, self.elapsed / self.duration)
            k = self.easing(t) if self.easing else t
            try:
                self.on_progress(t, k)
            except Exception:  # noqa: BLE001 - a dead tween must not kill the loop
                return GLib.SOURCE_REMOVE
            if t >= 1.0:
                return GLib.SOURCE_REMOVE
            return GLib.SOURCE_CONTINUE

        self._source = GLib.timeout_add(step_ms, tick)
        return self

    def stop(self):
        if self._source is not None:
            GLib.source_remove(self._source)
            self._source = None


def tween(duration_ms, on_progress, easing=ease_out_cubic) -> Tween:
    return Tween(duration_ms, on_progress, easing).start()


def lerp(a: float, b: float, k: float) -> float:
    return a + (b - a) * k
