"""
Hand-drawn wifi glyph: three concentric arcs over a base dot, the lit-arc count
standing in for signal strength (>0.66 lights all three, >0.33 two, >0 one).
Lit strokes use iconDim, unlit a dim icon tint, so a radio-on-but-unconnected
glyph reads as a dim fan. When the radio is off the glyph fades further and a
diagonal slash crosses it.

Paths live in a 24x24 space; the arcs-and-dot bounding box is centred within the
widget on both axes, and the off-state slash rides the same transform so it stays
registered with them. Every stroke weight scales by ``s``.
"""

import cairo
from fabric.utils import Gtk
from fabric.widgets.widget import Widget

from components.glyph_icon import _draw_path

_ARC_LINES = (
    ("M9.17 13.17 A4 4 0 0 1 14.83 13.17", 1),
    ("M6.34 10.34 A8 8 0 0 1 17.66 10.34", 2),
    ("M3.5 7.5 A12 12 0 0 1 20.5 7.5", 3),
)
_DOT = "M12 14.1 A1.5 1.5 0 0 1 12 17.1 A1.5 1.5 0 0 1 12 14.1z"
_SLASH = "M4 3 L20 19"


def _rgba(hex_color: str, alpha: float) -> str:
    r = int(hex_color[1:3], 16)
    g = int(hex_color[3:5], 16)
    b = int(hex_color[5:7], 16)
    return f"rgba({r},{g},{b},{alpha})"


def _rgba_parts(c: str) -> tuple[float, float, float, float]:
    if c.startswith("#"):
        return (
            int(c[1:3], 16) / 255,
            int(c[3:5], 16) / 255,
            int(c[5:7], 16) / 255,
            1.0,
        )
    nums = c.replace("rgba(", "").replace(")", "").split(",")
    return (
        float(nums[0]) / 255,
        float(nums[1]) / 255,
        float(nums[2]) / 255,
        float(nums[3]) if len(nums) > 3 else 1.0,
    )


class WifiGlyph(Gtk.DrawingArea, Widget):
    """Three concentric wifi arcs with a lit-count bound to ``level``.

    ``level`` 0..1 (signal strength fraction), ``on`` False adds the slash and
    dims the fan, ``stroke`` overrides the arc weight in 24-space (use 1.7 next
    to GlyphIcon family members, whose stroke is 1.7); 0 keeps the legacy
    screen-constant 2px weight.
    """

    def __init__(
        self,
        level: float = 0,
        on: bool = True,
        stroke: float = 0,
        s: float = 1.0,
        color: str | None = None,
        **kwargs,
    ):
        Gtk.DrawingArea.__init__(self)  # type: ignore
        Widget.__init__(self, **kwargs)
        self._s = s
        self._level = level
        self._on = bool(on)
        self._stroke = stroke
        self._color = color
        self.connect("draw", self._on_draw)
        self.set_size_request(int(17 * s), int(17 * s))

    # --- API -----------------------------------------------------------------

    def set_level(self, level: float):
        level = max(0.0, min(1.0, float(level)))
        if level == self._level:
            return
        self._level = level
        self.queue_draw()

    def set_on(self, on: bool):
        on = bool(on)
        if on == self._on:
            return
        self._on = on
        self.queue_draw()

    @property
    def level(self) -> float:
        return self._level

    @property
    def on(self) -> bool:
        return self._on

    def lit_count(self) -> int:
        if not self._on:
            return 0
        if self._level > 0.66:
            return 3
        if self._level > 0.33:
            return 2
        return 1 if self._level > 0 else 0

    # --- rendering ------------------------------------------------------------

    def _bounds(self, stroke: float):
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
        cc = cairo.Context(surf)
        for path, _n in _ARC_LINES:
            _draw_path(cc, path)
        _draw_path(cc, _DOT)
        cc.set_line_width(stroke)
        return cc.path_extents()

    def _on_draw(self, _widget, cr: cairo.Context):
        alloc = self.get_allocation()
        w, h = alloc.width, alloc.height
        if w <= 1 or h <= 1:
            return False

        color = self._color
        if color is None:
            color = "#bdbdbd"  # iconDim fallback; normally passed by owner
        lit = self.lit_count()
        lit_color = _rgba_parts(color)
        off_color = _rgba_parts(_rgba(color, 0.4 if self._on else 0.18))

        u = min(w, h) / 24.0
        stroke = self._stroke if self._stroke > 0 else 2.0 * self._s / u
        x0, y0, bw, bh = self._bounds(stroke)
        gx = (w - bw * u) / 2 - x0 * u
        gy = (h - bh * u) / 2 - y0 * u

        cr.save()
        cr.translate(gx, gy)
        cr.scale(u, u)
        cr.set_line_join(cairo.LINE_JOIN_ROUND)
        cr.set_antialias(cairo.ANTIALIAS_BEST)

        for path, needed in _ARC_LINES:
            cr.save()
            _draw_path(cr, path)
            cr.set_line_width(stroke)
            cr.set_line_cap(cairo.LINE_CAP_ROUND)
            if lit >= needed:
                cr.set_source_rgba(*lit_color)
            else:
                cr.set_source_rgba(*off_color)
            cr.stroke()
            cr.restore()

        cr.save()
        _draw_path(cr, _DOT)
        if lit >= 1:
            cr.set_source_rgba(*lit_color)
        else:
            cr.set_source_rgba(*off_color)
        cr.fill()
        cr.restore()

        if not self._on:
            cr.save()
            _draw_path(cr, _SLASH)
            slash_stroke = self._stroke if self._stroke > 0 else 1.7
            cr.set_line_width(slash_stroke)
            cr.set_line_cap(cairo.LINE_CAP_ROUND)
            cr.set_source_rgba(*_rgba_parts("#6a6a6a"))  # Theme.faint
            cr.stroke()
            cr.restore()

        cr.restore()
        return False
