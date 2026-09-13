"""Wallpaper surface
Fixed 720x172·s filmstrip over the wallpaper folder (Walls snapshot, newest
first). The focused thumb is large and fully lit; neighbours shrink, dim and
desaturate as they slide under it so the strip reads as depth. Arrow keys and
wheel move focus, clicking a neighbour glides to it, Enter/tap applies the pick
via the awww/matugen pipeline (strip stays open); holding the focused thumbnail
for the heat duration trashes the file with a press-and-hold confirm whose
progress sweeps along the tile's lower edge.

The whole strip is one cairo canvas: tiles are laid out from the single
position-chaser ``_pos`` (QML FrameAnimation, tau 70ms) with the verbatim
``slotW/slotH/slotCX`` tables and slotLerp interpolation. Thumbnails decode to
``GdkPixbuf`` on demand (LRU-capped) outside the hot path; live previews keep the
static thumb except focused gifs, which play in place. Type-to-filter, wallhaven
browse + download, the per-output monitor picker, the fit/kind dropdowns, the
folder row and the gesture legend are wired to match.

Local key routing mirrors shell.qml Appendix C: Escape closes menu → search →
surface; arrows/wheel move the strip (or the open dropdown's sliding bar);
Return/Space pick; printable chars seed the type-to-filter field; in wallhaven
browse mode the same strokes go to the persisted search field.
"""

import io
import json
import math
import queue
import threading
import urllib.parse
import urllib.request
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path

import cairo
from fabric.utils import Gdk, GdkPixbuf, GLib, Gtk
from fabric.widgets.box import Box
from fabric.widgets.centerbox import CenterBox
from fabric.widgets.entry import Entry
from fabric.widgets.eventbox import EventBox
from fabric.widgets.label import Label
from gi.repository import Pango, PangoCairo
from PIL import Image, ImageSequence

from components.dropdown import Dropdown
from components.glyph_icon import GlyphIcon
from components.search_field import SearchField
from core.flags import flags
from core.theme import Theme
from services import wallpaper_app as app
from services.walls import walls

_W = 720
_H = 172

_SLOT_W = [196, 126, 104, 88, 74]
_SLOT_H = [110, 71, 59, 50, 42]
_SLOT_CX = [0, 143, 244, 326, 393]
_SLOT_BRIGHT = [1.0, 0.56, 0.42, 0.30, 0.22]
_SLOT_SAT = [1.0, 0.65, 0.55, 0.45, 0.40]

_FIT_OPTIONS = [
    {"label": "Cover", "value": "cover", "desc": "Fill the screen, cropping overflow"},
    {"label": "Contain", "value": "contain", "desc": "Fit the whole image"},
    {"label": "Stretch", "value": "stretch", "desc": "Stretch to fill"},
    {"label": "Center", "value": "center", "desc": "Native size, centered"},
]
_KIND_OPTIONS = [
    {"label": "all", "value": "all"},
    {"label": "still", "value": "still"},
    {"label": "live", "value": "motion"},
]
_WH_SORT = [
    {"label": "Hot", "value": "hot"},
    {"label": "Latest", "value": "latest"},
    {"label": "Top", "value": "top"},
    {"label": "Random", "value": "random"},
    {"label": "Top Liked", "value": "favorites"},
]

_HEAT_MS = 1100
_TAP_FRAC = 0.25
_HINT_DWELL_MS = 600
_PREVIEW_ARM_MS = 300
_DIMS_DEBOUNCE_MS = 320
_MAX_THUMB_W = 512
_MAX_THUMB_H = 220
_GIF_FRAME_CAP = 24
_PIXBUF_LRU = 12


def _rr(cr, x: float, y: float, w: float, h: float, r: float):
    r = max(0.0, min(r, min(w, h) / 2))
    cr.new_sub_path()
    half = math.pi / 2
    cr.arc(x + w - r, y + r, r, -half, 0.0)
    cr.arc(x + w - r, y + h - r, r, 0.0, half)
    cr.arc(x + r, y + h - r, r, half, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 1.5 * math.pi)
    cr.close_path()


def _rgba(hex_color: str, alpha: float = 1.0) -> tuple[float, float, float, float]:
    h = hex_color.lstrip("#")
    if not h or len(h) < 6:
        return (1, 1, 1, alpha)
    return (
        int(h[0:2], 16) / 255.0,
        int(h[2:4], 16) / 255.0,
        int(h[4:6], 16) / 255.0,
        max(0.0, min(1.0, alpha)),
    )


def _lerp_from(arr, ao: float) -> float:
    if ao >= 4:
        return arr[4]
    i = int(ao)
    f = ao - i
    return arr[i] + (arr[i + 1] - arr[i]) * f


class FilmCanvas(Gtk.DrawingArea):
    """The one-canvas filmstrip: widgets-free drawing from the position chaser."""


class WallpaperSurface(Box):
    """The pill's wallpaper switcher. Fixed 720x172·s; no scrollbars."""

    def __init__(
        self,
        theme: Theme,
        s: float = 1.0,
        on_close: Callable[[], None] | None = None,
        on_size_change: Callable[[], None] | None = None,
        **kwargs,
    ):
        self._theme = theme
        self._s = s
        self._on_close = on_close
        self._on_size_change = on_size_change
        super().__init__(
            name="wallpaper-surface",
            size=(int(_W * s), int(_H * s)),
            style=(
                f"background-color: transparent;"
                f"background-image: linear-gradient(to bottom, {theme.card_top}, {theme.card_bot});"
                f"border: 1px solid {theme.border};"
                f"border-radius: {max(6, int(22 * s))}px;"
            ),
            **kwargs,
        )

        self._entries: list[dict] = []
        self.wall_results: list[dict] = []
        self.focus_index = 0
        self.pos = 0.0
        self.searching = False
        self.query = ""
        self.wh_source = False
        self.wh_page = 1
        self.wh_sort = "hot"
        self.kind_filter = "all"
        self.editing_dir = False
        self.preview_armed = True
        self.hint_shown = False
        self.mon_hover = ""
        self._anchor_path = walls.current
        self._searching_proc = False
        self._dl_target = ""
        self._dl_failed = ""
        self._hold = 0.0
        self._holding = False
        self._hold_id: int | None = None
        self._wheel_acc = 0.0
        self._gif_frames: list[GdkPixbuf.Pixbuf] = []
        self._gif_idx = 0
        self._gif_id: int | None = None
        self._dims_cache: dict[str, str] = {}
        self._pixbufs: OrderedDict[str, GdkPixbuf.Pixbuf] = OrderedDict()
        self._fetching: set[str] = set()
        self._q: queue.Queue = queue.Queue()
        self._tick_id: int | None = None
        self._hint_id: int | None = None
        self._arm_id: int | None = None
        self._dims_id: int | None = None
        self._mon_map = {"w": 0, "h": 0, "tiles": []}

        # --- canvas -------------------------------------------------------------
        self._canvas = FilmCanvas()
        self._canvas.set_size_request(int(_W * s), int(_H * s))
        self._canvas.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK
            | Gdk.EventMask.SCROLL_MASK
            | Gdk.EventMask.SMOOTH_SCROLL_MASK
            | Gdk.EventMask.LEAVE_NOTIFY_MASK
        )
        self._canvas.connect("draw", self._on_draw)
        self._canvas.connect("button-press-event", self._on_press)
        self._canvas.connect("button-release-event", self._on_release)
        self._canvas.connect("motion-notify-event", self._on_motion)
        self._canvas.connect("scroll-event", self._on_scroll)
        self._canvas.connect(
            "leave-notify-event", lambda *_a: self._canvas.queue_draw()
        )

        # every layout widget sits absolutely via Gtk.Layout, matching QML anchors
        self._lay = Gtk.Layout()
        self._lay.set_size(int(_W * s), int(_H * s))
        self._lay.set_hexpand(True)
        self._lay.set_vexpand(True)
        super().add(self._lay)
        self._lay.put(self._canvas, 0, 0)

        self._search = SearchField(
            theme,
            s=s,
            placeholder="Filter wallpapers",
            show_separator=False,
        )
        self._search.on_moved = self.move
        self._search.on_accepted = self._search_accepted
        self._search.on_dismissed = self.exit_search
        self._search.entry.connect("changed", self._on_query_changed)
        self._search.entry.set_has_frame(False)
        # override entry background — SearchField default is transparent which
        # is fine inside the launcher pill body, but here we need a solid fill
        self._search.entry.set_style(
            f"color: {theme.cream};"
            f"font-size: {max(10, int(12 * s))}px;"
            f"background: {theme.tile_bg};"
            f"border: none;"
            f"padding: {int(4 * s)}px {int(2 * s)}px;"
            f"caret-color: {theme.verm_lit};"
        )
        self._search.set_no_show_all(True)
        self._search.set_visible(False)

        # folder row: caption + inline edit
        self._folder_label = Label(
            label="",
            h_align="start",
            style=(f"color: {theme.faint};font-size: {max(8, int(9.5 * s))}px;"),
        )
        self._dir_field = Entry(
            h_expand=True,
            style=(
                f"color: {theme.cream};"
                f"font-size: {max(9, int(11 * s))}px;"
                f"background: {theme.tile_bg};"
                f"border-radius: {int(16 * s)}px;"
                f"padding: 0 {int(12 * s)}px;"
                f"border: none;"
            ),
        )
        self._dir_field.set_no_show_all(True)
        self._dir_field.set_visible(False)
        self._dir_field.connect("key-press-event", self._on_dir_key)
        self._folder_row = EventBox(
            events=Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.ENTER_NOTIFY_MASK,
            size=(int(120 * s), int(30 * s)),
            style="",
        )
        self._folder_row.connect("button-press-event", self._on_folder_click)
        self._folder_row.add(self._folder_label)

        # refresh chip
        self._ref_icon = GlyphIcon(name="refresh", size=int(13 * s), stroke=1.8)
        self._ref_icon.set_color(theme.icon_dim)
        self._refresh = EventBox(
            events=Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.ENTER_NOTIFY_MASK,
            size=(int(22 * s), int(22 * s)),
            v_align="center",
        )

        def _do_refresh():
            walls.refresh()
            self._start_ref_spin()
            return True

        self._refresh.connect("button-press-event", lambda *_a: _do_refresh())
        ref_body = Box(
            size=(int(22 * s), int(22 * s)), h_align="center", v_align="center"
        )
        ref_body.add(self._ref_icon)
        self._refresh.add(ref_body)

        # wallhaven chip: a Dropdown-style glyph chip (22·s) toggling whSource
        self._wh_glyph = GlyphIcon(name="wallpaper", size=int(13 * s), stroke=1.8)
        self._wh_glyph.set_color(theme.icon_dim)
        self._wh_chip = EventBox(
            events=Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.ENTER_NOTIFY_MASK,
            size=(int(22 * s), int(22 * s)),
            v_align="center",
        )
        self._wh_chip.connect(
            "button-press-event", lambda *_a: (self.toggle_wallhaven(), True)[1]
        )
        wh_body = Box(
            size=(int(22 * s), int(22 * s)), h_align="center", v_align="center"
        )
        wh_body.add(self._wh_glyph)
        self._wh_chip.add(wh_body)

        # three dropdowns (kind / wh-sort / fit) + refresh + wh chip, right-aligned
        def _toggle(slot):
            was = slot.open
            for d in (self._kind_dd, self._sort_dd, self._fit_dd):
                d.set_open(False)
            slot.set_open(not was)

        self._defer = {}
        self._kind_dd = Dropdown(
            theme,
            s=s,
            options=_KIND_OPTIONS,
            value=self.kind_filter,
            title="Filter",
            desc="Show every, still-only or live-only wallpaper",
            on_pick=self._on_kind_picked,
            on_chip_clicked=lambda: _toggle(self._kind_dd),
            v_align="center",
        )
        self._sort_dd = Dropdown(
            theme,
            s=s,
            options=_WH_SORT,
            value=self.wh_sort,
            title="Sort: Hot",
            desc="How wallhaven orders the browse feed",
            on_pick=self._on_sort_picked,
            on_chip_clicked=lambda: _toggle(self._sort_dd),
            v_align="center",
        )
        self._fit_dd = Dropdown(
            theme,
            s=s,
            glyph="scaling",
            options=_FIT_OPTIONS,
            value={"crop": "cover", "fit": "contain", "no": "center"}.get(
                flags.get("wallpaperFit", ""),
                flags.get("wallpaperFit", "cover") or "cover",
            ),
            title="Fit",
            desc="How the wallpaper fills the screen",
            on_pick=self._on_fit_picked,
            on_chip_clicked=lambda: _toggle(self._fit_dd),
            v_align="center",
        )
        for _d in (self._kind_dd, self._sort_dd, self._fit_dd):
            _d.set_no_show_all(True)
            _d.set_visible(False)

        # wallhaven page chevrons
        self._prev_glyph = GlyphIcon(name="chevron-left", size=int(12 * s), stroke=2)
        self._prev_glyph.set_color(theme.icon_dim)
        self._prev_chip = EventBox(
            events=Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.ENTER_NOTIFY_MASK,
            size=(int(22 * s), int(22 * s)),
        )
        self._prev_chip.connect(
            "button-press-event", lambda *_a: (self.wh_page_move(-1), True)[1]
        )
        p_body = Box(
            size=(int(22 * s), int(22 * s)), h_align="center", v_align="center"
        )
        p_body.add(self._prev_glyph)
        self._prev_chip.add(p_body)
        self._prev_chip.set_no_show_all(True)
        self._prev_chip.set_visible(False)

        self._next_glyph = GlyphIcon(name="chevron-right", size=int(12 * s), stroke=2)
        self._next_glyph.set_color(theme.icon_dim)
        self._next_chip = EventBox(
            events=Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.ENTER_NOTIFY_MASK,
            size=(int(22 * s), int(22 * s)),
        )
        self._next_chip.connect(
            "button-press-event", lambda *_a: (self.wh_page_move(1), True)[1]
        )
        n_body = Box(
            size=(int(22 * s), int(22 * s)), h_align="center", v_align="center"
        )
        n_body.add(self._next_glyph)
        self._next_chip.add(n_body)
        self._next_chip.set_no_show_all(True)
        self._next_chip.set_visible(False)

        for _w in (self._prev_chip, self._next_chip):
            self._lay.put(_w, 0, 0)

        # assemble top bar
        self._right_tools = Box(
            spacing=int(8 * s), orientation="horizontal", v_align="center"
        )
        self._right_tools.add(self._wh_chip)
        self._right_tools.add(self._kind_dd)
        self._right_tools.add(self._sort_dd)
        self._right_tools.add(self._fit_dd)
        self._right_tools.add(self._refresh)

        self._left_tools = Box(orientation="horizontal")
        self._left_tools.add(self._folder_row)
        self._left_tools.add(self._dir_field)
        self._left_tools.add(self._search)

        self._top_bar = CenterBox(
            start_children=self._left_tools,
            center_children=None,
            end_children=self._right_tools,
        )
        self._top_bar.set_can_focus(False)
        self._top_bar.set_focus_on_click(False)
        self._left_tools.set_can_focus(False)
        self._right_tools.set_can_focus(False)
        self._lay.put(self._top_bar, 0, 0)

        self.set_can_focus(True)
        self.connect("key-press-event", self._on_key)

        # keep one set of connects alive forever (cheap idle)
        GLib.timeout_add(40, self._drain_q)
        walls.connect("refreshdone", self._on_refresh_done)
        walls.connect("entrieschanged", self._on_entries_changed)

    def set_scale(self, s: float):
        self._s = s

    def surface_size(self) -> tuple[int, int]:
        """Pill body target when this surface is open (§7.2a fixed 720x172·s)."""
        return int(_W * self._s), int(_H * self._s)

    def _layout(self):
        s = self._s
        W = int(_W * s)
        H = int(_H * s)
        top_y = int(9 * s)
        chip_h = int(22 * s)

        # surface body must request its boxed size or Gtk.Layout collapses
        self.set_size_request(W, H)
        self._lay.set_size(W, H)
        self._canvas.set_size_request(W, H)

        self._top_bar.set_size_request(W - int(34 * s), int(30 * s))
        self._lay.move(self._top_bar, int(20 * s), int(8 * s))

        # ensure dropdown stable width by setting both to max before toggling visibility
        sort_w = max(int(22 * s), self._sort_dd.get_preferred_width()[1])
        kind_w = max(int(22 * s), self._kind_dd.get_preferred_width()[1])
        slot_w = max(sort_w, kind_w)
        self._sort_dd.set_size_request(slot_w, -1)
        self._kind_dd.set_size_request(slot_w, -1)

        # toggles
        self._sort_dd.set_visible(self.wh_source)
        self._kind_dd.set_visible(not self.wh_source)
        self._fit_dd.set_visible(True)
        self._wh_glyph.set_color(
            self._theme.verm_lit if self.wh_source else self._theme.icon_dim
        )

        self._search.set_visible(self.searching or self.wh_source)
        self._folder_row.set_visible(
            not self.searching and not self.wh_source and not self.editing_dir
        )
        self._dir_field.set_visible(self.editing_dir)

        left_w = W - self._right_tools.get_preferred_width()[1] - int(40 * s)
        self._search.set_size_request(left_w, int(30 * s))
        self._dir_field.set_size_request(left_w, int(30 * s))

        self._lay.move(self._prev_chip, int(8 * s), int((_H * s - chip_h) / 2))
        self._lay.move(self._next_chip, W - int(30 * s), int((_H * s - chip_h) / 2))
        self._prev_chip.set_visible(self.wh_source and self.wh_page > 1)
        self._next_chip.set_visible(self.wh_source)

        self._folder_label.set_label(
            walls.wpdir if (not self.searching and not self.wh_source) else ""
        )
        self._folder_label.set_style(
            f"color: {self._theme.faint};font-size: {max(8, int(9.5 * s))}px;"
        )
        self._lay.queue_draw()

    # -------------------------------------------------------------------------
    # open / close
    # -------------------------------------------------------------------------

    def open(self):
        """The pill just morphed to this surface — warm the snapshot, reset, focus."""
        self._scan_monitors()
        self._entries = walls.entries
        self.searching = False
        self.editing_dir = False
        self.query = ""
        self._search.text = ""
        self.menu_close()
        walls.warm()
        if self.wh_source:
            self.focus_index = 0
            self.pos = 0.0
            self.refresh_wallhaven()
        else:
            self.center_on_current()
        self.hint_shown = False
        self._hint_id = self._arm_id = None
        self._arm_id = GLib.timeout_add(_PREVIEW_ARM_MS, self._arm_preview)
        self._hint_id = GLib.timeout_add(_HINT_DWELL_MS, self._show_hint)
        self._start_tick()
        # no_show_all on the surface suppresses self.show_all(); reveal the
        # layout + children explicitly (set_visible in the morph loop only flips
        # the wrapper, not its subtree)
        self.show()
        self._lay.show_all()
        self._layout()
        self.grab_focus()

    def close(self):
        """Morph back to rest — drop the browsing session's caches (QML onActive)."""
        self.menu_close()
        self.searching = False
        self.query = ""
        self._search.text = ""
        self.wall_results = []
        self.wh_source = False
        self.wh_page = 1
        self._searching_proc = False
        self._stop_tick()
        self._cancel_hold()
        self._stop_gif()
        self._dims_cache = {}
        for _id in (self._hint_id, self._arm_id, self._dims_id):
            if _id is not None:
                GLib.source_remove(_id)
        self._hint_id = self._arm_id = self._dims_id = None
        self._pixbufs.clear()

    # -------------------------------------------------------------------------
    # tickers
    # -------------------------------------------------------------------------

    def _start_tick(self):
        if self._tick_id is None:
            self._tick_id = GLib.timeout_add(16, self._on_tick)

    def _stop_tick(self):
        if self._tick_id is not None:
            GLib.source_remove(self._tick_id)
            self._tick_id = None

    def _on_tick(self):
        target = float(self.focus_index)
        if abs(self.pos - target) > 0.001:
            k = 1 - math.exp(-0.016 / 0.07)
            nxt = self.pos + (target - self.pos) * k
            self.pos = nxt if abs(nxt - target) >= 0.001 else target
            self._canvas.queue_draw()
        if self._holding:
            nxt = min(1.0, self._hold + 0.016 * 1000 / _HEAT_MS)
            if nxt != self._hold:
                self._hold = nxt
                if self._hold >= 1.0:
                    self._commit_trash()
                else:
                    self._canvas.queue_draw()
        return GLib.SOURCE_CONTINUE

    def _arm_preview(self):
        self.preview_armed = True
        self._dims_probe()
        self._canvas.queue_draw()
        self._arm_id = None
        return GLib.SOURCE_REMOVE

    def _show_hint(self):
        self.hint_shown = True
        self._canvas.queue_draw()
        self._hint_id = None
        return GLib.SOURCE_REMOVE

    # -------------------------------------------------------------------------
    # model
    # -------------------------------------------------------------------------

    def _items(self) -> list[dict]:
        if self.wh_source:
            return self.wall_results
        if self.searching and self.query.strip():
            q = self.query.strip().lower()
            return [e for e in self.local_items() if q in e.get("name", "").lower()]
        return self.local_items()

    def local_items(self) -> list[dict]:
        if self.kind_filter == "all":
            return self._entries
        want_motion = self.kind_filter == "motion"
        return [
            e for e in self._entries if app.is_motion(e.get("path", "")) == want_motion
        ]

    def _on_entries_changed(self, *_a):
        self._entries = walls.entries
        if (
            not self.wh_source
            and not self.searching
            and self.focus_index >= len(self._entries)
        ):
            self.focus_index = max(0, len(self._entries) - 1)
        self._canvas.queue_draw()

    def _on_refresh_done(self, *_a):
        if (
            self.get_visible()
            and not self.wh_source
            and not (self.searching and self.query.strip())
        ):
            self._entries = walls.entries
            # keep the user exactly where they are: re-anchor focus by path so a
            # post-apply refresh can't yank the strip back to `walls.current`
            if not self._anchor_path:
                self.center_on_current()
            else:
                self._recenter_anchor()
            self._layout()
            self._canvas.queue_draw()

    def _start_ref_spin(self):
        """Spin the refresh glyph — at least one full turn, more while still refreshing."""
        if getattr(self, "_ref_spinning", False):
            return
        self._ref_spinning = True
        self._ref_icon._spin_angle = 0.0

        TWO_PI = 2 * 3.14159265

        def _tick():
            angle = getattr(self._ref_icon, "_spin_angle", 0.0) + 0.18
            self._ref_icon._spin_angle = angle % TWO_PI
            self._ref_icon.queue_draw()
            # stop only once we've done at least one full turn AND refreshing is done
            if angle >= TWO_PI and not walls.refreshing:
                self._ref_spinning = False
                self._ref_icon._spin_angle = 0.0
                self._ref_icon.queue_draw()
                return GLib.SOURCE_REMOVE
            return GLib.SOURCE_CONTINUE

        GLib.timeout_add(16, _tick)

    def _anchor(self, path: str | None):
        self._anchor_path = path or ""

    def _recenter_anchor(self):
        if not self._anchor_path:
            return
        items = self.local_items()
        for i, e in enumerate(items):
            if e.get("path") == self._anchor_path:
                self.focus_index = i
                self.pos = float(i)
                return

    def center_on_current(self):
        idx = 0
        cur = walls.current
        items = self.local_items()
        for i, e in enumerate(items):
            if e.get("path") == cur:
                idx = i
                break
        self._anchor_path = cur
        if self.searching:
            # local filter mode recentres on the snapshot position too
            for i, e in enumerate(self._entries):
                if e.get("path") == cur:
                    idx = i
                    break
        if idx < len(self._items()) or not self._items():
            self.focus_index = idx
            self.pos = float(idx)

    def move(self, delta: int):
        n = len(self._items())
        if n == 0:
            return
        self.focus_index = max(0, min(n - 1, int(self.focus_index) + int(delta)))
        self._anchor_path = self._items()[self.focus_index].get("path", "")
        self._on_focus_changed()
        self._canvas.queue_draw()

    def _on_focus_changed(self):
        self.hint_shown = False
        if self._hint_id is not None:
            GLib.source_remove(self._hint_id)
        self._hint_id = GLib.timeout_add(_HINT_DWELL_MS, self._show_hint)
        self.preview_armed = False
        if self._arm_id is not None:
            GLib.source_remove(self._arm_id)
        self._arm_id = GLib.timeout_add(_PREVIEW_ARM_MS, self._arm_preview)
        self._dims_debounce()
        self._reset_gif_focus()
        self._canvas.queue_draw()

    # -------------------------------------------------------------------------
    # activation / trash
    # -------------------------------------------------------------------------

    def activate(self):
        items = self._items()
        if not (0 <= self.focus_index < len(items)):
            return
        entry = items[self.focus_index]
        if "image" in entry and entry.get("image"):
            if self._dl_target:
                return
            self._dl_target = entry["image"]
            threading.Thread(target=self._dl_worker, args=(entry,), daemon=True).start()
            self._canvas.queue_draw()
        else:
            self._anchor_path = entry.get("path", "")
            walls.apply(entry.get("path", ""))

    def _dl_worker(self, entry: dict):
        url = entry["image"]
        name = Path(urllib.parse.urlparse(url).path).name or "wallhaven.jpg"
        dest = Path(self._resolve_dl_dir()) / name
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "Mozilla/5.0 forma"}
            )
            with urllib.request.urlopen(req, timeout=40) as r:
                data = r.read()
            dest.write_bytes(data)
            saved = str(dest)
            self._post(lambda: self._dl_done(saved))
            from ..services.walls import walls as _w

            self._post(lambda: _w.refresh())
            self._post(lambda: _w.apply(saved))
        except Exception as e:  # noqa: BLE001
            from fabric.utils import logger

            logger.warning(f"wallhaven download {url}: {e}")
            self._post(lambda: self._dl_fail(url))
        finally:
            self._post(lambda: setattr(self, "_dl_target", ""))

    def _dl_done(self, saved: str):
        self._dl_failed = ""
        self._canvas.queue_draw()

    def _dl_fail(self, url: str):
        self._dl_failed = url
        self._canvas.queue_draw()

    def _resolve_dl_dir(self) -> str:
        try:
            return app.resolve_wpdir(flags.get("wallpaperDir") or "")
        except Exception:  # noqa: BLE001
            return str(Path.home() / "Pictures" / "Wallpapers")

    def _commit_trash(self):
        entry = self._items()[self.focus_index] if self._items() else None
        if entry and not entry.get("image"):
            walls.trash(entry.get("path", ""))
        self._cancel_hold()
        self._canvas.queue_draw()

    def _cancel_hold(self):
        self._holding = False
        self._hold = 0.0

    # -------------------------------------------------------------------------
    # canvas events
    # -------------------------------------------------------------------------

    def _tile_box(
        self, index: int, items: list[dict]
    ) -> tuple[float, float, float, float]:
        off = index - self.pos
        ao = abs(off)
        w = _lerp_from(_SLOT_W, ao) * self._s
        h = _lerp_from(_SLOT_H, ao) * self._s
        cx = _lerp_from(_SLOT_CX, ao) if ao <= 4 else _SLOT_CX[4] + (ao - 4) * 60
        dx = cx * self._s * (1.0 if off > 0 else -1.0)
        x = (_W * self._s) / 2 + dx - w / 2
        y = (_H * self._s - h) / 2
        return x, y, w, h

    def _hit_index(self, gx: float, gy: float) -> int:
        items = self._items()
        if not items:
            return -1
        lo = max(0, int(self.pos) - 6)
        hi = min(len(items), int(self.pos) + 7)
        for i in range(hi - 1, lo - 1, -1):
            x, y, w, h = self._tile_box(i, items)
            if x <= gx <= x + w and y <= gy <= y + h:
                return i
        return -1

    def _mon_rects(self, x: float, y: float, w: float, h: float):
        rw = min(self._mon_map["w"] or 0, w * 0.6)
        rh = min(self._mon_map["h"] or 0, h * 0.5)
        return (x + w - rw - int(5 * self._s), y + int(5 * self._s), rw, rh)

    def _hit_mon(self, gx: float, gy: float) -> str | None:
        items = self._items()
        if len(self._mon_map.get("tiles", [])) < 2 or not (
            0 <= self.focus_index < len(items)
        ):
            return None
        entry = items[self.focus_index]
        if entry.get("image"):
            return None
        x, y, w, h = self._tile_box(self.focus_index, items)
        rx, ry, rw, rh = self._mon_rects(x, y, w, h)
        if not (rx <= gx <= rx + rw and ry <= gy <= ry + rh):
            return None
        kx = max(self._mon_map["w"], 1)
        ky = max(self._mon_map["h"], 1)
        relx = (gx - rx) / rw * kx
        rely = (gy - ry) / rh * ky
        for t in self._mon_map["tiles"]:
            if t["x"] <= relx <= t["x"] + t["w"] and t["y"] <= rely <= t["y"] + t["h"]:
                return t["name"]
        return None

    def _on_press(self, _w, event):
        idx = self._hit_index(event.x, event.y)
        mon = self._hit_mon(event.x, event.y)
        if mon:
            items = self._items()
            if 0 <= self.focus_index < len(items):
                walls.apply(items[self.focus_index].get("path", ""), mon)
            return True
        if idx < 0:
            return True
        items = self._items()
        entry = items[idx]
        if idx != self.focus_index:
            self.focus_index = idx
            self._anchor_path = entry.get("path", "")
            self._on_focus_changed()
            return True
        if entry.get("image"):
            self.activate()
        else:
            self._holding = True
            self._hold = 0.0
        return True

    def _on_release(self, _w, _e):
        if self._holding:
            self._holding = False
            if self._hold < _TAP_FRAC:
                self.activate()
            self._hold = 0.0
            self._canvas.queue_draw()

    def _on_motion(self, _w, event):
        mon = self._hit_mon(event.x, event.y) or ""
        if mon != self.mon_hover:
            self.mon_hover = mon
            self._canvas.queue_draw()

    def _on_scroll(self, _w, event):
        direction = event.direction
        if direction in (Gdk.ScrollDirection.UP, Gdk.ScrollDirection.LEFT):
            self.move(-1)
            return True
        if direction in (Gdk.ScrollDirection.DOWN, Gdk.ScrollDirection.RIGHT):
            self.move(1)
            return True
        if direction == Gdk.ScrollDirection.SMOOTH:
            _, dx, dy = event.get_scroll_deltas()
            # If the user swipes horizontally, dx dominates. If vertically, dy dominates.
            delta = dx if abs(dx) > abs(dy) else dy
            self._wheel_acc += delta
            notches = int(self._wheel_acc)
            if notches != 0:
                self.move(notches)
                self._wheel_acc -= notches
        return True

    # -------------------------------------------------------------------------
    # keys (shell.qml routing)
    # -------------------------------------------------------------------------

    def _on_key(self, _w, event):
        keyval = event.keyval
        shift = bool(event.state & Gdk.ModifierType.SHIFT_MASK)
        if keyval == Gdk.KEY_Escape:
            if self._menu_open():
                self.menu_close()
            elif self.editing_dir:
                self.editing_dir = False
                self._layout()
                from gi.repository import GLib

                GLib.idle_add(self.grab_focus)
            elif self.wh_source:
                self.toggle_wallhaven()
            else:
                self._trigger_close()
            return True
        if self.editing_dir:
            return False
        if self._menu_open():
            if keyval in (Gdk.KEY_Up, Gdk.KEY_Left):
                self.menu_move(-1)
            elif keyval in (Gdk.KEY_Down, Gdk.KEY_Right):
                self.menu_move(1)
            elif keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_space):
                self.menu_pick()
            return True
        field_focus = self._search.entry.has_focus() or self._dir_field.has_focus()
        if keyval in (Gdk.KEY_Up, Gdk.KEY_Left):
            if not field_focus:
                self.move(-1)
            return True
        if keyval in (Gdk.KEY_Down, Gdk.KEY_Right):
            if not field_focus:
                self.move(1)
            return True
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            if not field_focus:
                self.activate()
            return True
        if keyval == Gdk.KEY_space and not field_focus:
            self.activate()
            return True
        if keyval == Gdk.KEY_BackSpace and self.wh_source and not field_focus:
            self.wh_backspace()
            return True
        if (
            keyval in (Gdk.KEY_BackSpace, Gdk.KEY_Delete)
            and self.searching
            and not field_focus
        ):
            self.query = ""
            self._search.text = ""
            return True
        # printable → seed type-to-filter / wallhaven field
        ch = chr(Gdk.keyval_to_unicode(keyval))
        printable = len(ch) == 1 and ch >= "  " and not shift
        if printable and not self.mon_hover:
            if self.wh_source:
                if not field_focus:
                    self.wh_type_char(ch)
                    return True
            elif not self.searching:
                self.start_search(ch)
                return True
        return False

    def _trigger_close(self):
        if self._on_close:
            self._on_close()

    # --- dropdown coordination ---------------------------------------------------

    def _menu_open(self) -> bool:
        return self._kind_dd.open or self._sort_dd.open or self._fit_dd.open

    def menu_close(self):
        for d in (self._kind_dd, self._sort_dd, self._fit_dd):
            d.set_open(False)

    def menu_move(self, direction: int):
        if self._fit_dd.open:
            self._fit_dd.move_sel(direction)
        elif self._sort_dd.open:
            self._sort_dd.move_sel(direction)
        elif self._kind_dd.open:
            self._kind_dd.move_sel(direction)

    def menu_pick(self):
        if self._fit_dd.open:
            self._fit_dd.pick_sel()
        elif self._sort_dd.open:
            self._sort_dd.pick_sel()
        elif self._kind_dd.open:
            self._kind_dd.pick_sel()

    def _on_kind_picked(self, value: str):
        self.kind_filter = value
        GLib.idle_add(self.center_on_current)

    def _on_fit_picked(self, value: str):
        flags.set_raw("wallpaperFit", value)
        walls.apply_fit(value)

    def _on_sort_picked(self, value: str):
        self.wh_sort = value
        self.wh_page = 1
        self.wall_results = []
        self.focus_index = 0
        self.pos = 0.0
        self.refresh_wallhaven(1)

    # --- search ------------------------------------------------------------------

    def _on_query_changed(self, *_a):
        self.query = self._search.text
        if not self.wh_source:
            self.focus_index = 0
            self.pos = 0.0
        else:
            if hasattr(self, "_wh_search_debounce") and self._wh_search_debounce:
                GLib.source_remove(self._wh_search_debounce)
            self._wh_search_debounce = GLib.timeout_add(700, self.search_wallhaven_now)
        self._canvas.queue_draw()

    def _search_accepted(self):
        if self.wh_source:
            self.search_wallhaven_now()
        else:
            self.activate()

    def start_search(self, ch: str):
        self.searching = True
        self.editing_dir = False
        self.focus_index = 0
        self.pos = 0.0
        self.menu_close()
        self._search.text = ch
        self._layout()
        GLib.idle_add(self._search.focus)

    def exit_search(self):
        self.searching = False
        self.query = ""
        self._search.text = ""
        if self.wh_source:
            # in wallhaven mode: just clear the query and reload the default feed
            self.wall_results = []
            self._searching_proc = False
            self.wh_page = 1
            self.refresh_wallhaven()
        else:
            self.center_on_current()
        self.menu_close()
        self.editing_dir = False
        self._layout()
        from gi.repository import GLib

        GLib.idle_add(self.grab_focus)

    def wh_type_char(self, ch: str):
        ent = self._search.entry
        pos = ent.get_position()
        txt = self._search.text
        ent.set_text(txt[:pos] + ch + txt[pos:])
        ent.set_position(pos + 1)
        if not ent.has_focus():
            GLib.idle_add(self._search.focus)

    def wh_backspace(self):
        ent = self._search.entry
        pos = ent.get_position()
        txt = self._search.text
        if pos > 0:
            ent.set_text(txt[: pos - 1] + txt[pos:])
            ent.set_position(pos - 1)
        else:
            ent.set_position(0)
        if not ent.has_focus():
            GLib.idle_add(self._search.focus)

    def search_wallhaven_now(self):
        if hasattr(self, "_wh_search_debounce"):
            self._wh_search_debounce = None
        if not self.wh_source:
            return
        self.wh_page = 1
        self.wall_results = []
        self.focus_index = 0
        self.pos = 0.0
        self.refresh_wallhaven(1)
        GLib.idle_add(self.grab_focus)
        return False

    # --- wallhaven ---------------------------------------------------------------

    def toggle_wallhaven(self):
        self.menu_close()
        self.editing_dir = False
        if self.wh_source:
            self.wh_source = False
            self.wall_results = []
            self._searching_proc = False
            if self.searching:
                self.exit_search()
            else:
                self.center_on_current()
                self._layout()
                GLib.idle_add(self.grab_focus)
        else:
            self.searching = False
            self.query = ""
            self._search.text = ""
            self.wh_source = True
            self.wall_results = []
            self.wh_page = 1
            self.focus_index = 0
            self.pos = 0.0
            self._searching_proc = False
            self.refresh_wallhaven()
            self._layout()
        self._canvas.queue_draw()

    def wh_page_move(self, direction: int):
        if not self.wh_source or self._dl_target:
            return
        nxt = self.wh_page + direction
        if nxt < 1:
            return
        self.wh_page = nxt
        self.wall_results = []
        self.focus_index = 0
        self.pos = 0.0
        self.refresh_wallhaven(nxt)

    def refresh_wallhaven(self, page: int | None = None):
        if self._searching_proc:
            return
        self._searching_proc = True
        query = self.query.strip()
        pg = page if page is not None else self.wh_page
        threading.Thread(
            target=self._wh_fetch, args=(query, pg, self.wh_sort), daemon=True
        ).start()
        self._canvas.queue_draw()

    def _wh_fetch(self, query: str, page: int, sort: str):
        try:
            params = {
                "categories": "010",
                "purity": "100",
                "sorting": sort,
                "order": "desc",
                "page": str(page),
            }
            if query:
                params["q"] = query
            url = "https://wallhaven.cc/api/v1/search?" + urllib.parse.urlencode(params)
            req = urllib.request.Request(
                url, headers={"User-Agent": "Mozilla/5.0 forma"}
            )
            with urllib.request.urlopen(req, timeout=15) as r:
                data = json.loads(r.read().decode("utf-8", "replace"))
            out = []
            for item in data.get("data", []) or []:
                t = item.get("thumbs", {}) or {}
                thumb = t.get("large") or t.get("small") or ""
                if not thumb:
                    continue
                out.append(
                    {
                        "name": item.get("id", ""),
                        "thumb": thumb,
                        "image": item.get("path", ""),
                        "w": int(item.get("dimension_x") or 0),
                        "h": int(item.get("dimension_y") or 0),
                        "mtime": 0,
                    }
                )
            self._post(lambda: self._wh_land(out))
        except Exception as e:  # noqa: BLE001
            from fabric.utils import logger

            logger.warning(f"wallhaven fetch: {e}")
            self._post(lambda: self._wh_land([]))
        finally:
            self._post(lambda: setattr(self, "_searching_proc", False))

    def _wh_land(self, results: list):
        self.wall_results = results
        self.focus_index = 0
        self.pos = 0.0
        self._canvas.queue_draw()

    # --- folder row --------------------------------------------------------------

    def _on_folder_click(self, *_a):
        if self.editing_dir:
            return
        self.editing_dir = True
        self._dir_field.set_text(flags.get("wallpaperDir") or "")
        self._layout()
        GLib.idle_add(self._dir_field.grab_focus)
        return True

    def _on_dir_key(self, _w, event):
        if event.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            flags.set_raw("wallpaperDir", self._dir_field.get_text().strip())
            walls.refresh()
            self.editing_dir = False
            self._layout()
            return True
        if event.keyval == Gdk.KEY_Escape:
            self.editing_dir = False
            self._layout()
            return True
        return False

    # --- probe / decode ----------------------------------------------------------

    def _dims_debounce(self):
        if self._dims_id is not None:
            GLib.source_remove(self._dims_id)
        self._dims_id = GLib.timeout_add(_DIMS_DEBOUNCE_MS, self._dims_probe)

    def _dims_probe(self):
        self._dims_id = None
        items = self._items()
        if not self.preview_armed or not (0 <= self.focus_index < len(items)):
            return GLib.SOURCE_REMOVE
        entry = items[self.focus_index]
        path = entry.get("path", "")
        if not path or entry.get("image") or path in self._dims_cache:
            return GLib.SOURCE_REMOVE
        threading.Thread(target=self._probe_worker, args=(path,), daemon=True).start()
        return GLib.SOURCE_REMOVE

    def _probe_worker(self, path: str):
        dims = app.probe_dims(path)
        if dims:
            self._post(lambda: self._probe_land(path, dims))

    def _probe_land(self, path: str, dims: str):
        if len(self._dims_cache) > 400:
            self._dims_cache = {}
        self._dims_cache[path] = dims
        items = self._items()
        if items and items[self.focus_index].get("path") == path:
            self._canvas.queue_draw()

    def _pixbuf_for(self, entry: dict) -> GdkPixbuf.Pixbuf | None:
        thumb = entry.get("thumb", "")
        key = f"{thumb}?v={int(entry.get('mtime') or 0)}"
        if key in self._pixbufs:
            self._pixbufs.move_to_end(key)
            return self._pixbufs[key]
        if not thumb:
            return None
        if thumb.startswith("http"):
            # never block the draw path on the network: kick the fetch to a
            # worker thread and repaint once it lands (the key appears in the
            # LRU cache on that next paint, so _pixbuf_for returns immediately)
            if key not in self._fetching:
                self._fetching.add(key)
                threading.Thread(
                    target=self._http_thumb_worker,
                    args=(thumb, key),
                    daemon=True,
                ).start()
            return None
        try:
            pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                thumb, _MAX_THUMB_W, _MAX_THUMB_H, True
            )
        except GLib.Error:
            return None
        if pb is None:
            return None
        self._pixbufs[key] = pb
        if len(self._pixbufs) > _PIXBUF_LRU:
            self._pixbufs.popitem(last=False)
        return pb

    def _http_thumb_worker(self, thumb: str, key: str):
        pb = None
        try:
            req = urllib.request.Request(
                thumb, headers={"User-Agent": "Mozilla/5.0 forma"}
            )
            with urllib.request.urlopen(req, timeout=10) as r:
                data = r.read()
            loader = GdkPixbuf.PixbufLoader.new()
            loader.write(data)
            loader.close()
            pb = loader.get_pixbuf()
            if pb is not None:
                pb = self._fit_pixbuf(pb)
        except Exception:  # noqa: BLE001
            pb = None
        self._post(lambda: self._http_thumb_land(key, pb))

    def _http_thumb_land(self, key: str, pb: GdkPixbuf.Pixbuf | None):
        self._fetching.discard(key)
        if pb is not None:
            self._pixbufs[key] = pb
            if len(self._pixbufs) > _PIXBUF_LRU:
                self._pixbufs.popitem(last=False)
            self._canvas.queue_draw()

    @staticmethod
    def _fit_pixbuf(pb: GdkPixbuf.Pixbuf) -> GdkPixbuf.Pixbuf:
        w = pb.get_width()
        h = pb.get_height()
        if w <= 0 or h <= 0:
            return pb
        k = min(1.0, _MAX_THUMB_W / w, _MAX_THUMB_H / h)
        if k >= 1.0:
            return pb
        return pb.scale_simple(
            max(1, int(w * k)), max(1, int(h * k)), GdkPixbuf.InterpType.BILINEAR
        )

    def _gif_frames_for(self, entry: dict) -> list[GdkPixbuf.Pixbuf]:
        if self._gif_frames:
            return self._gif_frames
        path = entry.get("path", "")
        try:
            with Image.open(path) as im:
                if getattr(im, "is_animated", False):
                    frames = []
                    for i, f in enumerate(ImageSequence.Iterator(im)):
                        if i >= _GIF_FRAME_CAP:
                            break
                        f = f.convert("RGBA")
                        f.thumbnail((_MAX_THUMB_W, _MAX_THUMB_H))
                        buf = io.BytesIO()
                        f.save(buf, format="PNG")
                        frames.append(
                            GdkPixbuf.Pixbuf.new_from_stream(
                                io.BytesIO(buf.getvalue()), None
                            )
                        )
                    self._gif_frames = frames
        except Exception:  # noqa: BLE001
            self._gif_frames = []
        return self._gif_frames

    def _reset_gif_focus(self):
        self._stop_gif()
        items = self._items()
        if not (0 <= self.focus_index < len(items)):
            return
        entry = items[self.focus_index]
        p = entry.get("path", "")
        if entry.get("image") or not str(p).lower().endswith(".gif"):
            return
        self._gif_frames = []
        self._gif_idx = 0
        threading.Thread(target=self._gif_worker, args=(entry,), daemon=True).start()

    def _gif_worker(self, entry: dict):
        frames = self._gif_frames_for(entry)
        if frames:
            self._post(lambda: self._gif_start())

    def _gif_start(self):
        if len(self._gif_frames) <= 1:
            return
        self._gif_idx = 0
        self._gif_id = GLib.timeout_add(80, self._gif_advance)

    def _gif_advance(self):
        self._gif_idx = (self._gif_idx + 1) % len(self._gif_frames)
        items = self._items()
        can_play = (
            self.preview_armed
            and 0 <= self.focus_index < len(items)
            and abs(self.focus_index - self.pos) < 0.5
        )
        if can_play:
            self._canvas.queue_draw()
            return GLib.SOURCE_CONTINUE
        return GLib.SOURCE_CONTINUE

    def _stop_gif(self):
        if self._gif_id is not None:
            GLib.source_remove(self._gif_id)
            self._gif_id = None

    # --- thread → main-loop hand-off ---------------------------------------------

    def _post(self, fn: Callable[[], None]):
        self._q.put(fn)

    def _drain_q(self):
        try:
            while True:
                fn = self._q.get_nowait()
                try:
                    fn()
                except Exception as e:  # noqa: BLE001
                    from fabric.utils import logger

                    logger.warning(f"wallpaper surface callback error: {e}")
        except queue.Empty:
            pass
        return GLib.SOURCE_CONTINUE

    # --- monitors -----------------------------------------------------------------

    def _scan_monitors(self):
        mons = app.hyprctl_monitors()
        if len(mons) < 2:
            self._mon_map = {"w": 0, "h": 0, "tiles": []}
            return
        min_x = min(m.get("x", 0) for m in mons)
        max_x = max(m.get("x", 0) + m.get("width", 0) for m in mons)
        min_y = min(m.get("y", 0) for m in mons)
        max_y = max(m.get("y", 0) + m.get("height", 0) for m in mons)
        w_span = max(1, max_x - min_x)
        h_span = max(1, max_y - min_y)
        k = min(26 * len(mons), 120) * self._s / w_span if w_span else 1
        k = min(k, (22 * self._s) / h_span)
        tiles = [
            {
                "name": m.get("name", ""),
                "x": (m.get("x", 0) - min_x) * k,
                "y": (m.get("y", 0) - min_y) * k,
                "w": m.get("width", 0) * k,
                "h": m.get("height", 0) * k,
            }
            for m in mons
        ]
        self._mon_map = {"w": w_span * k + 2 * self._s, "h": h_span * k, "tiles": tiles}

    # -------------------------------------------------------------------------
    # drawing
    # -------------------------------------------------------------------------

    def _draw_text(
        self,
        cr,
        x: float,
        y: float,
        text: str,
        px: int,
        color: str,
        anchor: str = "middle",
        weight: int = Pango.Weight.MEDIUM,
    ):
        if not text:
            return
        layout = PangoCairo.create_layout(cr)
        desc = Pango.FontDescription.from_string("Inter")
        desc.set_size(max(1, int(px)) * Pango.SCALE)
        desc.set_weight(weight)
        layout.set_font_description(desc)
        layout.set_text(text, -1)
        tw, th = layout.get_pixel_size()
        if anchor == "middle":
            tx = x - tw / 2
            ty = y - th / 2
        elif anchor == "right":
            tx = x - tw
            ty = y - th / 2
        else:
            tx = x
            ty = y - th / 2
        cr.set_source_rgba(*_rgba(color))
        cr.move_to(tx, ty)
        PangoCairo.show_layout(cr, layout)

    def _on_draw(self, _da, cr: cairo.Context):
        theme = self._theme
        W = _W * self._s
        H = _H * self._s
        items = self._items()

        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()

        # tiles
        lo = max(0, int(self.pos) - 6)
        hi = min(len(items), int(self.pos) + 7)
        for i in sorted(range(lo, hi), key=lambda x: abs(x - self.pos), reverse=True):
            entry = items[i]
            off = i - self.pos
            ao = abs(off)
            if ao > 5:
                continue
            x, y, w, h = self._tile_box(i, items)
            # edge fade
            soft = 70 * self._s
            gap = min(x, W - (x + w))
            edge = max(0.0, min(1.0, gap / soft)) if soft else 1.0
            fade = edge * (1.0 if ao <= 4 else max(0.0, 5 - ao))
            if fade <= 0:
                continue
            focused = i == self.focus_index
            bright = _lerp_from(_SLOT_BRIGHT, ao)
            sat = _lerp_from(_SLOT_SAT, ao)
            corner = (8 + 2 * max(0.0, 1 - ao)) * self._s

            # card backing
            cr.save()
            _rr(cr, x, y, w, h, corner)
            cr.clip()
            cr.set_source_rgba(*_rgba(theme.tile_bg))
            cr.paint()

            pb = self._pixbuf_for(entry)
            if pb is not None:
                pw, ph = pb.get_width(), pb.get_height()
                if pw > 0 and ph > 0:
                    k = max(w / pw, h / ph)
                    dw, dh = pw * k, ph * k
                    tx = x + (w - dw) / 2
                    ty = y + (h - dh) / 2
                    cr.save()
                    cr.rectangle(x, y, w, h)
                    cr.clip()
                    cr.translate(tx, ty)
                    cr.scale(k, k)
                    Gdk.cairo_set_source_pixbuf(cr, pb, 0, 0)
                    cr.paint()
                    cr.restore()

            # desaturate (approx) + dim
            if sat < 1.0:
                cr.set_source_rgba(0.5, 0.5, 0.5, (1 - sat) * 0.45)
                cr.paint()
            if bright < 1.0:
                cr.set_source_rgba(0, 0, 0, 1 - bright)
                cr.paint()

            # commit sweep (trash hold)
            progress = 0.0
            if self._holding and focused:
                progress = max(0.0, (self._hold - _TAP_FRAC) / (1 - _TAP_FRAC))
            if progress > 0:
                gh = h * progress
                g = cairo.LinearGradient(0, y + h - gh, 0, y + h)
                g.add_color_stop_rgba(0.0, *_rgba(theme.flame_glow, 0.66))
                g.add_color_stop_rgba(0.74, *_rgba(theme.verm_lit, 0.30))
                g.add_color_stop_rgba(1.0, *_rgba(theme.flame_glow, 0.0))
                cr.set_source(g)
                cr.rectangle(x, y + h - gh, w, gh)
                cr.fill()
                cr.set_source_rgba(*_rgba(theme.flame_glow, min(1.0, progress * 3)))
                cr.rectangle(x, y + h - gh, w, max(1, 2 * self._s))
                cr.fill()

            # motion badge
            is_motion = app.is_motion(entry.get("path", "")) or entry.get("preview")
            if is_motion and not (focused and self._gif_frames):
                pw = int(12 * self._s)
                ph = max(6, int(11 * self._s))
                bx = x + 5 * self._s
                by = y + 5 * self._s
                cr.set_source_rgba(0, 0, 0, 0.55)
                _rr(cr, bx, by, pw, ph, ph / 2)
                cr.fill()
                self._draw_text(
                    cr,
                    bx + pw / 2,
                    by + ph / 2 - 1,
                    "▶",
                    max(6, int(7.5 * self._s)),
                    theme.cream,
                    weight=Pango.Weight.NORMAL,
                )

            # resolution badge
            res = ""
            if entry.get("image"):
                if entry.get("w"):
                    res = f"{entry['w']}×{entry['h']}"
            else:
                res = self._dims_cache.get(entry.get("path", ""), "")
            if (
                focused
                and res
                and not (entry.get("image") and self._dl_target == entry.get("image"))
            ):
                pw = max(14, int((len(res) * 5.5 + 12) * self._s))
                ph = max(8, int(13 * self._s))
                bx = x + w / 2 - pw / 2
                by = y + h - ph - 6 * self._s
                cr.set_source_rgba(0, 0, 0, 0.55)
                _rr(cr, bx, by, pw, ph, ph / 2)
                cr.fill()
                self._draw_text(
                    cr,
                    x + w / 2,
                    by + ph / 2 - 1,
                    res,
                    max(7, int(9.5 * self._s)),
                    theme.cream,
                    weight=Pango.Weight.SEMIBOLD,
                )

            if focused and entry.get("image") and self._dl_target == entry.get("image"):
                self._draw_text(
                    cr,
                    x + w / 2,
                    y + h / 2,
                    "saving…",
                    max(8, int(11 * self._s)),
                    theme.cream,
                    weight=Pango.Weight.SEMIBOLD,
                )
            cr.restore()

            # border
            cr.save()
            _rr(cr, x + 0.5, y + 0.5, w - 1, h - 1, corner)
            commit = progress > 0
            remote_fail = bool(entry.get("image")) and self._dl_failed == entry.get(
                "image"
            )
            bcol = theme.verm_lit if (commit or remote_fail) else theme.border
            cr.set_source_rgba(*_rgba(bcol))
            cr.set_line_width(1.0)
            cr.stroke()
            cr.restore()

        # focused gif preview (drawn on top)
        if self._gif_frames and self.preview_armed:
            i = self.focus_index
            if 0 <= i < len(items):
                x, y, w, h = self._tile_box(i, items)
                frame = self._gif_frames[self._gif_idx % len(self._gif_frames)]
                cr.save()
                _rr(cr, x, y, w, h, (8 + 2) * self._s)
                cr.clip()
                pw, ph = frame.get_width(), frame.get_height()
                if pw and ph:
                    k = max(w / pw, h / ph)
                    dw, dh = pw * k, ph * k
                    cr.translate(x + (w - dw) / 2, y + (h - dh) / 2)
                    cr.scale(k, k)
                    Gdk.cairo_set_source_pixbuf(cr, frame, 0, 0)
                    cr.paint()
                cr.restore()

        # mon picker on the focused tile
        if len(self._mon_map.get("tiles", [])) >= 2 and 0 <= self.focus_index < len(
            items
        ):
            entry = items[self.focus_index]
            if not entry.get("image"):
                x, y, w, h = self._tile_box(self.focus_index, items)
                rx, ry, rw, rh = self._mon_rects(x, y, w, h)
                mw = self._mon_map["w"] or 0
                if mw > 0:
                    cr.set_source_rgba(0, 0, 0, 0.62)
                    _rr(cr, rx, ry, rw, rh, 6 * self._s)
                    cr.fill()
                    for t in self._mon_map["tiles"]:
                        hovered = self.mon_hover == t["name"]
                        tx = rx + t["x"] + 0.75 * self._s
                        ty = ry + t["y"] + 0.75 * self._s
                        tw = max(2, t["w"] - 1.5 * self._s)
                        th = max(2, t["h"] - 1.5 * self._s)
                        cr.set_source_rgba(
                            *_rgba(theme.verm_lit, 0.45 if hovered else 0)
                        )
                        _rr(cr, tx, ty, tw, th, 3 * self._s)
                        cr.fill()
                        cr.set_source_rgba(
                            *_rgba(
                                theme.verm_lit if hovered else "#ffffff",
                                1.0 if hovered else 0.35,
                            )
                        )
                        _rr(cr, tx, ty, tw, th, 3 * self._s)
                        cr.set_line_width(1.0)
                        cr.stroke()

        # empty / searching states
        if not items and not self._searching_proc:
            if self.wh_source:
                msg = (
                    "no wallhaven results" if self.query else "no wallhaven wallpapers"
                )
            elif self.searching and self.query:
                msg = "no wallpapers match"
            elif self.kind_filter == "motion":
                msg = "no live wallpapers yet"
            elif self.kind_filter == "still":
                msg = "no still wallpapers"
            else:
                msg = f"No wallpapers in {walls.wpdir or 'the folder'}"
            self._draw_text(
                cr, W / 2, H / 2, msg, max(8, int(10.5 * self._s)), theme.faint
            )
        elif self._searching_proc and not items:
            self._draw_text(
                cr, W / 2, H / 2, "searching…", max(8, int(10.5 * self._s)), theme.faint
            )

        # gesture legend / mon hover caption
        if items and not self.searching and not self.wh_source:
            if self.mon_hover:
                self._draw_text(
                    cr,
                    W / 2,
                    H - 16 * self._s,
                    f"set on {self.mon_hover} only",
                    max(8, int(9 * self._s)),
                    theme.cream,
                    weight=Pango.Weight.SEMIBOLD,
                )
            else:
                one = len(self._mon_map.get("tiles", [])) > 0
                legend = "tap " + ("set all" if one else "set")
                if one:
                    legend += "   corner one screen"
                legend += "   hold delete"
                self._draw_text(
                    cr,
                    W / 2,
                    H - 16 * self._s,
                    legend,
                    max(7, int(8 * self._s)),
                    theme.verm_lit,
                )

        # wh page badge
        if self.wh_source and items:
            self._draw_text(
                cr,
                W / 2,
                int(12 * self._s),
                f"page {self.wh_page}",
                max(7, int(9 * self._s)),
                theme.faint,
            )

        return False
