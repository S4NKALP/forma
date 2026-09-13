"""Top-level shell orchestrator: per-monitor bar/layer assembly.

Owns the one-per-monitor ``Bar`` (pill + reserve + input windows) and hands
them to the application for registration.
"""


from fabric.utils import Gdk
from fabric.widgets.box import Box
from fabric.widgets.wayland import WaylandWindow

from core.flags import flags
from core.pill import Pill
from core.theme import Theme

WINDOWS: list[Bar] = []


class Bar:
    """One island per monitor. Owns the pill, reserve and input window."""

    def __init__(self, theme: Theme, monitor: Gdk.Monitor, index: int):
        self.theme = theme
        self.monitor = monitor
        self.index = index
        geometry = monitor.get_geometry()
        self.mon_w = geometry.width
        self.mon_h = geometry.height
        self._top_gap = int(flags.top_gap)
        self.pill: Pill | None = None

        connector = (
            monitor.get_connector() if hasattr(monitor, "get_connector") else None
        )
        self.screen_name = connector or monitor.get_model() or f"mon{index}"

        self.reserve = WaylandWindow(
            name=f"forma-reserve-{index}",
            title=f"forma-reserve-{index}",
            layer="top",
            anchor="top",
            margin="0px 0px 0px 0px",
            exclusivity="normal",
            keyboard_mode="none",
            pass_through=True,
            monitor=monitor,
            child=Box(style="background-color: transparent;"),
        )
        self._make_transparent(self.reserve)

        self.input = WaylandWindow(
            name=f"forma-input-{index}",
            title=f"forma-input-{index}",
            layer="top",
            anchor="top",
            margin=f"{self._top_gap}px 0px 0px 0px",
            exclusivity="none",
            keyboard_mode="none",
            pass_through=False,
            monitor=monitor,
            visible=False,
        )
        self._make_transparent(self.input)

        self.pill = Pill(
            theme,
            screen_name=self.screen_name,
            on_geometry=self._on_pill_geometry,
            on_surface_state=self._on_surface_state,
        )
        self.input.add(self.pill)
        self._on_pill_geometry(*self.pill.get_size_request())
        self.input.visible = True
        self.input.show_all()

        self._apply_margin()
        flags.connect("notify::top-gap", self._on_top_gap_changed)
        flags.connect("notify::main-display", self._on_main_display_changed)

    # --- surface keyboard ----------------------------------------------------

    def _on_surface_state(self, open_: bool):
        """While a major surface is open, claim keyboard so Up/Down/Enter and
        typing reach the surface; release it to the OS otherwise."""
        self.input.keyboard_mode = "exclusive" if open_ else "none"

    # --- geometry plumbing ---------------------------------------------------

    @staticmethod
    def _make_transparent(win: WaylandWindow):
        """RGBA visual + app-paintable so GTK never fills the default gray bg."""
        win.set_app_paintable(True)
        screen = win.get_screen()
        visual = screen.get_rgba_visual()
        if visual is None:
            return
        win.set_visual(visual)

    def _on_pill_geometry(self, w: int, h: int):
        if self.pill is None:
            return
        # input window grows/shrinks 1:1 with the pill
        self.input.set_size_request(w, h)

        self._set_reserve(self._top_gap, w, h)

    def _sync_input_region(self, *_a):
        """Confine pointer input to the pill rect so the transparent full-width
        window is click-through outside the island (Quickshell input mask)"""
        w, h = self._pill_w, self._pill_h
        if w <= 0 or h <= 0:
            return
        win = self.input.get_window()
        if win is None:
            return
        win_w = max(win.get_width(), self.mon_w) or self.mon_w
        x = max(0, (win_w - w) // 2)
        region = Gdk.cairo_region_create_rectangle(Gdk.Rectangle(x, 0, w, h))
        win.set_input_region(region)

    def _set_reserve(self, y: int, w: int, h: int):
        zone_h = y + h
        self.reserve.set_size_request(w, zone_h)

    def _on_top_gap_changed(self, *_a):
        self._top_gap = int(flags.top_gap)
        self._apply_margin()
        if self.pill is not None:
            w, h = self.pill.get_size_request()
            self._on_pill_geometry(w, h)

    def _on_main_display_changed(self, *_a):
        self._apply_margin()
        if self.pill is not None:
            w, h = self.pill.get_size_request()
            self._on_pill_geometry(w, h)

    def _apply_margin(self):
        # attached (ukishima stripBar) sits flush against the screen's top
        # edge — no gap, square top corners via the pill's CSS class
        if flags.main_display == "attached":
            self.input.margin = "-1px 0px 0px 0px"
        else:
            self.input.margin = f"{self._top_gap}px 0px 0px 0px"


def build(theme: Theme) -> list[Bar]:
    """One ``Bar`` per monitor, in Gdk monitor order (index = bar index)."""
    display = Gdk.Display.get_default()
    monitors = [display.get_monitor(i) for i in range(display.get_n_monitors())]
    WINDOWS.clear()
    bars = [Bar(theme, mon, i) for i, mon in enumerate(monitors)]
    WINDOWS.extend(bars)
    return bars
