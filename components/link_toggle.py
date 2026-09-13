"""

Switch (squarish track + blocky knob, NOT a pill): tile-bg socket off, terracotta
fill on, cream knob slides on the fast motion token. Shared by the wifi,
bluetooth and hotspot controls.
"""

from collections.abc import Callable

from fabric.utils import Gdk
from fabric.widgets.box import Box
from fabric.widgets.eventbox import EventBox
from fabric.widgets.fixed import Fixed

from core.motion import FAST, lerp, tween
from core.theme import Theme


def _rgba(hex_color: str, alpha: float) -> str:
    r = int(hex_color[1:3], 16)
    g = int(hex_color[3:5], 16)
    b = int(hex_color[5:7], 16)
    return f"rgba({r},{g},{b},{alpha})"


_TRACK_W = 30
_TRACK_H = 18
_KNOB = 12
_KNOB_M = 2  # horizontal inset inside the track
_KNOB_TOP = 3  # vertical inset ((18-12)/2)
_RACKET_R = 8  # track corner radius — rounded switch, not a pill
_KNOB_R = 4  # knob corner radius


class LinkToggle(EventBox):
    """30x18*s switch; ``set_on()`` slides the blocky knob with Motion.fast."""

    def __init__(
        self,
        theme: Theme,
        s: float = 1.0,
        on: bool = False,
        on_toggled: Callable[[bool], None] | None = None,
        **kwargs,
    ):
        super().__init__(
            events=Gdk.EventMask.BUTTON_PRESS_MASK,
            size=(int(_TRACK_W * s), int(_TRACK_H * s)),
            style=self._track_style(theme, s, on),
            **kwargs,
        )
        self._theme = theme
        self._s = s
        self._on = bool(on)
        self._on_toggled = on_toggled
        self._knob_tween = None

        self._knob = Box(
            size=(int(_KNOB * s), int(_KNOB * s)),
            style=f"background: {theme.cream}; border-radius: {max(2, int(_KNOB_R * s))}px;",
        )
        self._track = Fixed(
            h_expand=True,
            v_expand=True,
            size=(int(_TRACK_W * s), int(_TRACK_H * s)),
        )
        self._track.put(self._knob, self._knob_x(), int(_KNOB_TOP * s))
        self.add(self._track)

        self.connect("button-press-event", self._on_press)

    # --- geometry ------------------------------------------------------------

    def _knob_x(self) -> int:
        return (
            int((_TRACK_W - _KNOB - _KNOB_M) * self._s)
            if self._on
            else int(_KNOB_M * self._s)
        )

    def _track_style(self, theme: Theme, s: float, on: bool) -> str:
        if on:
            return (
                f"background: {theme.verm};"
                f"border: 1px solid {_rgba(theme.verm_deep, 0.55)};"
                f"box-shadow: inset 0 0 3px 1px rgba(0,0,0,0.22);"
                f"border-radius: {max(3, int(_RACKET_R * s))}px;"
            )
        return (
            f"background: {theme.tile_bg};"
            f"border: 1px solid {theme.border};"
            f"border-radius: {max(3, int(_RACKET_R * s))}px;"
        )

    # --- state ----------------------------------------------------------------

    @property
    def on(self) -> bool:
        return self._on

    def set_on(self, on: bool, animate: bool = True):
        on = bool(on)
        if on == self._on and not animate:
            self._on = on
            self.set_style(self._track_style(self._theme, self._s, on))
            return
        if on == self._on:
            return
        from_x = self._knob_x()
        self._on = on
        self.set_style(self._track_style(self._theme, self._s, on))
        to_x = self._knob_x()
        if self._knob_tween is not None:
            self._knob_tween.stop()
            self._knob_tween = None

        def step(_t, k):
            self._track.move(
                self._knob, int(lerp(from_x, to_x, k)), int(_KNOB_TOP * self._s)
            )

        self._knob_tween = tween(FAST, step)

    def toggle(self):
        self.set_on(not self._on)
        if self._on_toggled is not None:
            self._on_toggled(self._on)

    def _on_press(self, _w, ev):
        if ev.button != 1:
            return True
        self.toggle()
        return True

    def set_scale(self, s: float):
        self._s = s
        self.set_size_request(int(_TRACK_W * s), int(_TRACK_H * s))
        self.set_style(self._track_style(self._theme, s, self._on))
        self._track.set_size_request(int(_TRACK_W * s), int(_TRACK_H * s))
        self._knob.set_size_request(int(_KNOB * s), int(_KNOB * s))
        self._knob.set_style(
            f"background: {self._theme.cream}; border-radius: {max(2, int(_KNOB_R * s))}px;"
        )
        self._track.move(self._knob, self._knob_x(), int(_KNOB_TOP * s))
