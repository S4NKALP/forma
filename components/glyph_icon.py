import math

import cairo
from fabric.utils import Gtk
from fabric.widgets.widget import Widget

GLYPHS: dict[str, str] = {
    "scaling": "M3 7V3h4 M17 3h4v4 M21 17v4h-4 M7 21H3v-4 M9 12h6",
    "refresh": "M23 4v6h-6 M20.49 15a9 9 0 1 1-2.12-9.36L23 10",
    "chevron-left": "M14 6l-6 6 6 6",
    "chevron-right": "M10 6l6 6-6 6",
    "chevron-down": "M6 10l6 6 6-6",
    "chevron-up": "M6 14l6-6 6 6",
    "wallpaper": (
        "M21 15l-4-4-6.5 6.5 M3 19l5.5-5.5 3 3 "
        "M19 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V5a2 2 0 0 0-2-2z "
        "M16 8h.01"
    ),
    "sun": "M12 7a5 5 0 1 0 5 5 M12 1v2 M12 21v2 M4.22 4.22l1.42 1.42 "
    "M18.36 18.36l1.42 1.42 M1 12h2 M21 12h2 M4.22 19.78l1.42-1.42 "
    "M18.36 5.64l1.42-1.42",
    "moon": "M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z",
    "droplet": "M12 2.69l5.66 5.66a8 8 0 1 1-11.31 0z",
    "lock": "M6 10h12a1.5 1.5 0 0 1 1.5 1.5v6a1.5 1.5 0 0 1-1.5 1.5H6a1.5 1.5 0 0 1-1.5-1.5v-6A1.5 1.5 0 0 1 6 10z M8.5 10V7a3.5 3.5 0 0 1 7 0v3",
    "lock-outline": (
        "M6.4 9.5H17.6A2.4 2.4 0 0 1 20 11.9V17.6A2.4 2.4 0 0 1 17.6 20H6.4A2.4"
        " 2.4 0 0 1 4 17.6V11.9A2.4 2.4 0 0 1 6.4 9.5Z M7.5 9.5V6A4.5 4.5 0 0 1 16.5 6V9.5"
    ),
    "return": "M20 6v6a3 3 0 0 1-3 3H5 M9 11l-4 4 4 4",
    "logout": "M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4 M16 17l5-5-5-5 M21 12H9",
    "suspend": "M21 12.8A9 9 0 1 1 11.2 3 7 7 0 0 0 21 12.8z",
    "reboot": "M21 12a9 9 0 1 1-2.6-6.4 M21 3v5h-5",
    "shutdown": "M12 3v9 M7.8 6.3a8 8 0 1 0 8.4 0",
    "bluetooth": "M12 2.8v18.4 M12 2.8l5.2 4.6-10.4 9 M12 21.2l5.2-4.6-10.4-9",
    "speaker": "M4 9v6h4l5 4V5L8 9z M16 9.5a3 3 0 0 1 0 5 M18.5 7.5a6 6 0 0 1 0 9",
    "mouse": "M12 2a6 6 0 0 0-6 6v8a6 6 0 0 0 12 0V8a6 6 0 0 0-6-6z M12 6v3.5",
    "keyboard": "M2.5 6h19a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1h-19a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1z M6 10h.01 M10 10h.01 M14 10h.01 M18 10h.01 M7.5 14h9",
    "monitor": "M4 4h16a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2h-16a2 2 0 0 1-2-2v-9a2 2 0 0 1 2-2z M8 21h8 M12 17v4 M7 13c1.5-4 3-4 5-1s3.5 2 5-2",
    "music": "M9 18V5l12-2v13 M9 18a3 3 0 1 1-6 0 3 3 0 0 1 6 0z M21 16a3 3 0 1 1-6 0 3 3 0 0 1 6 0z",
    "gamepad": "M7 11h4 M9 9v4 M15.5 10h.01 M17.5 13h.01 M17 7H7a5 5 0 0 0-5 5l-.9 4.5A2.4 2.4 0 0 0 5.7 18L8 15h8l2.3 3a2.4 2.4 0 0 0 4.6-1.5L22 12a5 5 0 0 0-5-5z",
    "play": "M6 4l14 8-14 8z",
    "pause": "M7 4h3.4v16H7z M13.6 4H17v16h-3.4z",
    "prev": "M6 5h1.8v14H6z M18 5l-10.5 7L18 19z",
    "next": "M16.2 5H18v14h-1.8z M6 5l10.5 7L6 19z",
    "wifi": "M9.17 13.17 A4 4 0 0 1 14.83 13.17 M6.34 10.34 A8 8 0 0 1 17.66 10.34 "
    "M3.5 7.5 A12 12 0 0 1 20.5 7.5 M12 14.1 A1.5 1.5 0 0 1 12 17.1 A1.5 1.5 0 0 1 12 14.1z",
    "wifi-off": "M9.17 13.17 A4 4 0 0 1 14.83 13.17 M6.34 10.34 A8 8 0 0 1 17.66 10.34 "
    "M3.5 7.5 A12 12 0 0 1 20.5 7.5 M12 14.1 A1.5 1.5 0 0 1 12 17.1 A1.5 1.5 0 0 1 12 14.1z M4 3 L20 19",
    "layers": "M12 2L2 7l10 5 10-5-10-5z M2 17l10 5 10-5 M2 12l10 5 10-5",
    "headphones": "M3 17a2 2 0 0 0 2 2h2V9H5a2 2 0 0 0-2 2v6z M19 17a2 2 0 0 1-2 2h-2V9h2a2 2 0 0 1 2 2v6z",
}

_FILLED = {"moon", "droplet"}

# --- tiny SVG path tokenizer/parser ------------------------------------------


def _angle(ux, uy, vx, vy) -> float:
    dot = ux * vx + uy * vy
    cross = ux * vy - uy * vx
    return math.atan2(cross, dot)


def _arc(cc, x1, y1, rx, ry, phi_deg, large, sweep, x2, y2):
    """Cubic-free arc: an SVG endpoint arc converted to a centered ellipse
    description and drawn with cairo's native arc transformation."""
    rx, ry = abs(rx), abs(ry)
    if rx == 0 or ry == 0 or (x1, y1) == (x2, y2):
        cc.line_to(x2, y2)
        return
    phi = math.radians(phi_deg)
    cos_p, sin_p = math.cos(phi), math.sin(phi)
    x1p = cos_p * (x1 - x2) / 2 + sin_p * (y1 - y2) / 2
    y1p = -sin_p * (x1 - x2) / 2 + cos_p * (y1 - y2) / 2
    radii = (x1p * x1p) / (rx * rx) + (y1p * y1p) / (ry * ry)
    if radii > 1:
        s = math.sqrt(radii)
        rx, ry = rx * s, ry * s
    num = rx * rx * ry * ry - rx * rx * y1p * y1p - ry * ry * x1p * x1p
    den = rx * rx * y1p * y1p + ry * ry * x1p * x1p
    rad = math.sqrt(max(0.0, num) / den) if den else 0.0
    sign = -1 if large == sweep else 1
    cxp = sign * rad * (rx * y1p) / ry
    cyp = sign * rad * (-ry * x1p) / rx
    cx = cos_p * cxp - sin_p * cyp + (x1 + x2) / 2
    cy = sin_p * cxp + cos_p * cyp + (y1 + y2) / 2
    a1 = _angle(1, 0, (x1p - cxp) / rx, (y1p - cyp) / ry)
    da = _angle(
        (x1p - cxp) / rx, (y1p - cyp) / ry, (-x1p - cxp) / rx, (-y1p - cyp) / ry
    )
    if not sweep and da > 0:
        da -= 2 * math.pi
    elif sweep and da < 0:
        da += 2 * math.pi
    segments = max(1, math.ceil(abs(da) / (math.pi / 2)))
    step = da / segments
    for i in range(segments):
        a0 = a1 + i * step
        a1_ = a0 + step
        cc.save()
        cc.translate(cx, cy)
        cc.rotate(phi)
        cc.scale(rx, ry)
        cc.arc(0, 0, 1, a0, a1_)
        cc.restore()


def _tokenize(d: str) -> list[tuple[str, list[float]]]:
    """Flatten an SVG path into [(cmd, [floats...])] groups (implicit repeats
    stay one group: 'M0 0 1 1' → [("M", [0,0,1,1])])."""
    chars = []
    i, n = 0, len(d)
    while i < n:
        c = d[i]
        if c.isalpha():
            chars.append(("c", c))
        elif c in "-+." or c.isdigit():
            chars.append(("n", c))
        else:
            chars.append(("s", c))
        i += 1
    groups: list[tuple[str, list[float]]] = []
    cur_cmd = ""
    cur_nums: list[float] = []
    num = ""
    for kind, ch in chars:
        if kind == "n":
            if ch in "+-" and num:
                cur_nums.append(float(num))
                num = ""
            num += ch
            continue
        if num:
            cur_nums.append(float(num))
            num = ""
        if kind == "c":
            if cur_cmd and cur_nums:
                groups.append((cur_cmd, cur_nums))
            cur_cmd = ch
            cur_nums = []
    if num:
        cur_nums.append(float(num))
    groups.append((cur_cmd, cur_nums)) if cur_cmd and cur_nums else None
    return groups


def _draw_path(cc, d: str):
    cmds = _tokenize(d)
    sx = sy = 0.0
    px = py = 0.0
    last_cmd = ""
    i = 0
    n = len(cmds)
    while i < n:
        cmd, nums = cmds[i]
        j = 0
        consumed_any = False
        while True:
            if cmd in ("z", "Z"):
                cc.close_path()
                px, py = sx, sy
                consumed_any = True
                break
            m = len(nums)

            def take(k):
                nonlocal j
                if j + k <= m:
                    v = nums[j : j + k]
                    j += k
                    return v
                return None

            if cmd in ("M", "L"):
                p = take(2)
                if p is None:
                    break
                xx, yy = p
                if cmd == "M":
                    sx, sy, px, py = xx, yy, xx, yy
                    cc.move_to(px, py)
                    cmd = "L"
                else:
                    cc.line_to(xx, yy)
                    px, py = xx, yy
            elif cmd in ("m", "l"):
                p = take(2)
                if p is None:
                    break
                dx, dy = p
                if cmd == "m":
                    sx, sy = sx + dx, sy + dy
                    px, py = sx, sy
                    cc.move_to(px, py)
                    cmd = "l"
                else:
                    px, py = px + dx, py + dy
                    cc.line_to(px, py)
            elif cmd in ("H", "V"):
                p = take(1)
                if p is None:
                    break
                if cmd == "H":
                    px = p[0]
                else:
                    py = p[0]
                cc.line_to(px, py)
            elif cmd in ("h", "v"):
                p = take(1)
                if p is None:
                    break
                if cmd == "h":
                    px += p[0]
                else:
                    py += p[0]
                cc.line_to(px, py)
            elif cmd in ("C", "c"):
                p = take(6)
                if p is None:
                    break
                if cmd == "C":
                    c1x, c1y, c2x, c2y, qx, qy = p
                    cc.curve_to(c1x, c1y, c2x, c2y, qx, qy)
                    px, py = qx, qy
                else:
                    c1x, c1y, c2x, c2y, qx, qy = (
                        px + p[0],
                        py + p[1],
                        px + p[2],
                        py + p[3],
                        px + p[4],
                        py + p[5],
                    )
                    cc.curve_to(c1x, c1y, c2x, c2y, qx, qy)
                    px, py = qx, qy
                _last_refs = (c1x, c1y, c2x, c2y)
            elif cmd in ("S", "s"):
                p = take(4)
                if p is None:
                    break
                r1x, r1y, r2x, r2y = (
                    (_last_refs[2], _last_refs[3])
                    if last_cmd in ("C", "c", "S", "s")
                    else (px, py)
                )
                if cmd == "S":
                    c2x, c2y, qx, qy = p
                    cc.curve_to(r1x, r1y, c2x, c2y, qx, qy)
                    px, py = qx, qy
                    _last_refs = (r1x, r1y, c2x, c2y)
                else:
                    c2x, c2y, qx, qy = px + p[0], py + p[1], px + p[2], py + p[3]
                    cc.curve_to(r1x, r1y, c2x, c2y, qx, qy)
                    px, py = qx, qy
                    _last_refs = (r1x, r1y, c2x, c2y)
            elif cmd in ("Q", "q"):
                p = take(4)
                if p is None:
                    break
                if cmd == "Q":
                    c1x, c1y, qx, qy = p
                else:
                    c1x, c1y, qx, qy = px + p[0], py + p[1], px + p[2], py + p[3]
                cc.curve_to(c1x, c1y, c1x, c1y, qx, qy)
                _last_refs = (c1x, c1y, c1x, c1y)
                px, py = qx, qy
            elif cmd in ("T", "t"):
                p = take(2)
                if p is None:
                    break
                c1x, c1y = (
                    (px + (px - _last_refs[0]), py + (py - _last_refs[1]))
                    if last_cmd in ("Q", "q", "T", "t")
                    else (px, py)
                )
                if cmd == "T":
                    qx, qy = p
                else:
                    qx, qy = px + p[0], py + p[1]
                cc.curve_to(c1x, c1y, c1x, c1y, qx, qy)
                _last_refs = (c1x, c1y, c1x, c1y)
                px, py = qx, qy
            elif cmd in ("A", "a"):
                p = take(7)
                if p is None:
                    break
                rx, ry, rot, large, sweep, qx, qy = p
                if cmd == "a":
                    qx, qy = px + qx, py + qy
                _arc(cc, px, py, rx, ry, rot, bool(large), bool(sweep), qx, qy)
                px, py = qx, qy
                _last_refs = (px, py, px, py)
            else:
                break
            consumed_any = True
            last_cmd = cmd
            if j >= m:
                break
        if not consumed_any:
            break
        i += 1
    return cc


# --- rendered glyph widget ----------------------------------------------------


class GlyphIcon(Gtk.DrawingArea, Widget):
    """A stroked 24x24-space path scaled to any box, centred automatically.

    ``name`` picks a verbatim path from :data:`GLYPHS`; ``stroke`` is a
    stroke-width in 1/24ths (ukishima default 1.8). Call ``set_color()`` to
    retint live (hover states), ``set_stroke()`` to reweight.
    """

    def __init__(
        self,
        name: str = "",
        color: str = "#bdbdbd",
        stroke: float = 1.8,
        path: str = "",
        filled: bool = False,
        size: float | tuple | None = None,
        **kwargs,
    ):
        Gtk.DrawingArea.__init__(self)  # type: ignore
        Widget.__init__(self, **kwargs)
        self._name = name
        self._d = path or GLYPHS.get(name, "")
        self._filled = filled or name in _FILLED
        self._color = color
        self._stroke = stroke
        self._extents = None
        self._surface = None
        self.connect("draw", self._on_draw)
        if size is not None:
            if isinstance(size, (tuple, list)):
                self.set_size_request(int(size[0]), int(size[1]))
            else:
                self.set_size_request(int(size), int(size))

    # --- API -----------------------------------------------------------------

    def set_color(self, color: str):
        if color == self._color:
            return
        self._color = color
        self.queue_draw()

    def set_stroke(self, stroke: float):
        if stroke == self._stroke:
            return
        self._stroke = stroke
        self.queue_draw()

    def set_glyph(self, name: str):
        if name == self._name:
            return
        self._name = name
        self._d = GLYPHS.get(name, "")
        self._filled = name in _FILLED
        self._extents = None
        self._surface = None
        self.queue_draw()

    # --- rendering ------------------------------------------------------------

    def _path_bounds(self, stroke: float):
        """User-space bbox of the path incl. its stroke, offscreen."""
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
        cc = cairo.Context(surf)
        self._append_path(cc)
        cc.set_line_width(stroke)
        return cc.path_extents()

    def _append_path(self, cc):
        _draw_path(cc, self._d)

    def spin(self):
        if getattr(self, "_spinning", False) or getattr(self, "_spin_source", None):
            return
        self._spinning = True
        self._spin_angle = 0.0
        from gi.repository import GLib

        def _tick():
            self._spin_angle += 0.2
            if self._spin_angle >= 3.14159 * 2:
                self._spinning = False
                self._spin_angle = 0.0
                self.queue_draw()
                return False
            self.queue_draw()
            return True

        GLib.timeout_add(16, _tick)

    def start_spin(self):
        """Continuous spin (e.g. the wifi reload glyph while a scan runs)."""
        import gi.repository.GLib as _GLib

        if getattr(self, "_spin_source", None) is not None:
            return
        self._spinning = True
        self._spin_angle = 0.0
        self._spin_source = _GLib.timeout_add(16, self._spin_tick)

    def stop_spin(self):
        if getattr(self, "_spin_source", None) is not None:
            import gi.repository.GLib as _GLib

            _GLib.source_remove(self._spin_source)
            self._spin_source = None
        self._spinning = False
        self._spin_angle = 0.0
        self.queue_draw()

    def _spin_tick(self):
        if not getattr(self, "_spinning", False):
            self._spin_source = None
            return False
        self._spin_angle = (getattr(self, "_spin_angle", 0.0) + 0.2) % (2 * math.pi)
        self.queue_draw()
        return True

    def _on_draw(self, _widget, cr: cairo.Context):

        if not self._d:
            return False
        alloc = self.get_allocation()
        w, h = alloc.width, alloc.height
        if w <= 1 or h <= 1:
            return False
        stroke = self._stroke
        u = min(w, h) / 24.0
        if self._extents is None:
            self._extents = self._path_bounds(stroke)
        x0, y0, bw, bh = self._extents
        if bw <= 0 or bh <= 0:
            return False
        color = self._color
        if color.startswith("#"):
            r = int(color[1:3], 16) / 255
            g = int(color[3:5], 16) / 255
            b = int(color[5:7], 16) / 255
            a = 1.0
        else:
            rgba = color.replace("rgba(", "").replace(")", "").split(",")
            r, g, b = float(rgba[0]) / 255, float(rgba[1]) / 255, float(rgba[2]) / 255
            a = float(rgba[3]) if len(rgba) > 3 else 1.0
        cr.save()
        cr.translate(w / 2.0, h / 2.0)
        if getattr(self, "_spin_angle", 0.0) > 0:
            cr.rotate(self._spin_angle)
        cr.translate(-w / 2.0, -h / 2.0)
        cr.translate((w - bw * u) / 2 - x0 * u, (h - bh * u) / 2 - y0 * u)
        cr.scale(u, u)
        cr.set_line_width(stroke)
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.set_line_join(cairo.LINE_JOIN_ROUND)
        cr.set_source_rgba(r, g, b, a)
        self._append_path(cr)
        if self._filled:
            cr.fill_preserve()
        cr.stroke()
        cr.restore()
        return False
