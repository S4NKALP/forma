from fabric.utils import Gdk, Gtk


class Fader(Gtk.LevelBar):
    """Horizontal LevelBar styled as a thin filament thread + vermilion fill.

    Optional ``gradient_colors`` ``(from_hex, to_hex)`` swaps the flat
    ``verm_lit`` fill for a horizontal gradient (ukishima battery OSD fill is
    ``vermDeep -> flameGlow``).
    """

    def __init__(
        self, theme, s: float = 1.0, gradient_colors=None, css_name="osd-fader"
    ):
        super().__init__(min_value=0.0, max_value=100.0)
        self.set_name(css_name)
        self._css_name = css_name
        self.set_hexpand(True)
        self.set_valign(Gtk.Align.CENTER)
        self._theme = theme
        self._s = s
        self._gradient_colors = gradient_colors
        self._value = 0.0
        self._muted = False
        self._provider = Gtk.CssProvider()
        # widget-level providers do not cascade to the trough/blocks, so the
        # filament CSS is registered at screen scope under the unique id; USER
        # priority beats fabric's own APP-priority providers
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), self._provider, Gtk.STYLE_PROVIDER_PRIORITY_USER
        )
        self.refresh()
        Gtk.LevelBar.set_value(self, 0.0)

    def refresh(self):
        """Regenerate the CSS from the current theme + scale factor."""
        th = max(4.0, 7.0 * self._s)
        rad = th / 2.0
        thread = str(self._theme.thread_bg)
        verm = str(self._theme.verm_lit)
        dim = str(self._theme.dim)
        if self._gradient_colors:
            g_from, g_to = self._gradient_colors
            fill_bg = f"background-image: linear-gradient(to right, {g_from}, {g_to});"
        else:
            fill_bg = f"background: {verm};"
        css = (
            f"#{self._css_name} trough {{\n"
            f"    background: {thread};\n"
            f"    min-height: {th}px;\n"
            f"    border-radius: {rad}px;\n"
            f"}}\n"
            f"#{self._css_name} trough block {{\n"
            f"    min-height: {th}px;\n"
            f"    border-radius: {rad}px;\n"
            f"}}\n"
            f"#{self._css_name} trough block.filled {{\n"
            f"    {fill_bg}\n"
            f"    background-color: {verm};\n"
            f"    border-color: {verm};\n"
            f"}}\n"
            f"#{self._css_name} trough block.empty {{\n"
            f"    background: transparent;\n"
            f"}}\n"
            f"#{self._css_name}.muted trough block.filled {{\n"
            f"    background: {dim};\n"
            f"    border-color: {dim};\n"
            f"}}\n"
        )
        self._provider.load_from_data(css.encode())

    def set_value(self, value: float, on: bool = True):
        value = max(0.0, min(1.0, float(value)))
        muted = not bool(on)
        if abs(value - self._value) < 0.004 and muted == self._muted:
            return
        self._value = value
        self._muted = muted
        Gtk.LevelBar.set_value(self, float(value * 100.0))
        ctx = self.get_style_context()
        if muted:
            ctx.add_class("muted")
        else:
            ctx.remove_class("muted")
