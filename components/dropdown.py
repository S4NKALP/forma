"""Dropdown
A compact chip that opens a Gtk.Popover menu, so the menu floats over the UI
without affecting the layout of sibling widgets.
"""

from collections.abc import Callable

import cairo
from fabric.utils import Gdk, Gtk
from fabric.widgets.box import Box
from fabric.widgets.eventbox import EventBox
from fabric.widgets.label import Label
from gi.repository import Pango, PangoCairo

from components.glyph_icon import GlyphIcon
from core.theme import Theme


def text_width(text: str, px: int) -> int:
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
    context = cairo.Context(surface)
    layout = PangoCairo.create_layout(context)
    layout.set_font_description(
        Pango.FontDescription.from_string(f"Inter {max(1, int(px))}")
    )
    layout.set_text(text, -1)
    w, _ = layout.get_pixel_size()
    return w


class DropdownRow(EventBox):
    """One option row: flame tick when current, colour-coded label."""

    def __init__(
        self, theme: Theme, s: float, label: str, index: int, on_pick, on_hover
    ):
        super().__init__(
            events=(
                Gdk.EventMask.BUTTON_PRESS_MASK
                | Gdk.EventMask.ENTER_NOTIFY_MASK
                | Gdk.EventMask.LEAVE_NOTIFY_MASK
            ),
        )
        self._theme = theme
        self._s = s
        self.index = index
        box = Box(
            h_expand=True,
            v_expand=True,
            style=(f"padding-left: {int(16 * s)}px;padding-right: {int(8 * s)}px;"),
        )
        self._tick = Box(size=(int(2 * s), max(2, int(10 * s))))
        box.add(self._tick)
        self._label = Label(label=label, h_expand=True, x_align=0, y_align=0.5)
        box.add(self._label)
        self.add(box)
        self._on_pick = on_pick
        self.connect(
            "button-press-event", lambda *_a: (self._on_pick(self.index), True)[1]
        )
        self.connect("enter-notify-event", lambda *_a: on_hover(self.index))

    def paint(self, current: bool, selected: bool):
        theme = self._theme
        px = max(9, int(10.5 * self._s))
        self._tick.set_visible(current)
        if current:
            self._tick.set_style(
                f"background: {theme.verm_lit}; border-radius: {int(self._s)}px;"
            )
            self._label.set_style(
                f"color: {theme.verm_lit}; font-size: {px}px; font-weight: 700;"
            )
        elif selected:
            self._label.set_style(
                f"color: {theme.cream}; font-size: {px}px; font-weight: 600;"
            )
        else:
            self._label.set_style(
                f"color: {theme.subtle}; font-size: {px}px; font-weight: 500;"
            )


class Dropdown(EventBox):
    """Chip + Gtk.Popover menu; floats freely without displacing siblings."""

    def __init__(
        self,
        theme: Theme,
        s: float = 1.0,
        options: list[dict] | None = None,
        value: str = "",
        glyph: str = "",
        title: str = "",
        desc: str = "",
        on_pick: Callable[[str], None] | None = None,
        on_chip_clicked: Callable[[], None] | None = None,
        **kwargs,
    ):
        self._theme = theme
        self._s = s
        self.options = options or []
        self.value = str(value)
        self.glyph = glyph
        self.title = title
        self.desc = desc
        self.on_pick = on_pick
        self.on_chip_clicked = on_chip_clicked
        self.sel_index = max(0, self.cur_index)
        self._open = False

        self.chip_h = int(22 * s)
        self.row_h = int(21 * s)
        self._label_px = max(9, int(10 * s))

        # --- chip body ----------------------------------------------------------
        self._glyph_icon = GlyphIcon(name=glyph, size=int(13 * s), stroke=1.8)
        self._label = Label(label=self.cur_label)
        self._chevron = GlyphIcon(name="chevron-down", size=int(8 * s), stroke=1.8)
        self._chip_body = Box(h_align="center", v_align="center")
        if glyph:
            self._chip_body.set_size_request(self.chip_h, self.chip_h)
            self._chip_body.add(self._glyph_icon)
        else:
            self._chip_body.set_size_request(int(30 * s), self.chip_h)
            row = Box(spacing=int(3 * s), h_align="center", v_align="center")
            row.add(self._label)
            row.add(self._chevron)
            self._chip_body.add(row)

        super().__init__(
            events=Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.ENTER_NOTIFY_MASK,
            child=self._chip_body,
            **kwargs,
        )
        self.connect("button-press-event", lambda *_a: (self._chip_click(), True)[1])

        # --- popover ------------------------------------------------------------
        meas = max(
            (
                text_width(str(o.get("label", "")), max(9, int(10.5 * s)))
                for o in self.options
            ),
            default=int(40 * s),
        )
        card_w = int(meas + 30 * s)
        card_h = int(len(self.options) * self.row_h + int(4 * s))

        self._card = Box(
            orientation="v",
            size=(card_w, card_h),
            style=(
                f"background-image: linear-gradient(to bottom, {theme.card_top}, {theme.card_bot});"
                f"border: 1px solid {theme.frame_border};"
                f"border-radius: {max(4, int(9 * s))}px;"
            ),
        )
        self._rows: list[DropdownRow] = []
        for i, opt in enumerate(self.options):
            row = DropdownRow(
                theme,
                s,
                str(opt.get("label", "")),
                i,
                on_pick=self._pick_row,
                on_hover=self._hover_row,
            )
            row.set_size_request(card_w - int(4 * s), self.row_h)
            self._rows.append(row)
            self._card.add(row)

        self._popover = Gtk.Popover()
        # defer anchoring until realized so GTK can resolve the screen coords
        self.connect("realize", lambda *_: self._popover.set_relative_to(self))
        self._popover.set_position(Gtk.PositionType.BOTTOM)
        # No arrow, no default padding/border — we draw our own card style
        self._popover.set_modal(False)
        ctx = self._popover.get_style_context()
        ctx.add_class("dropdown-popover")
        self._card.show_all()
        self._popover.add(self._card)

        # strip GTK popover chrome (arrow, padding, border, background) via CSS
        css = b"""
        .dropdown-popover,
        .dropdown-popover > .background,
        .dropdown-popover contents {
            background: transparent;
            border: none;
            padding: 0;
            margin: 0;
            box-shadow: none;
        }
        .dropdown-popover > arrow { opacity: 0; }
        """
        provider = Gtk.CssProvider()
        provider.load_from_data(css)
        self._popover.get_style_context().add_provider(
            provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        self._popover.connect("closed", self._on_popover_closed)

        self._size_chip()
        self._paint()

    # --- state ------------------------------------------------------------------

    @property
    def cur_index(self) -> int:
        for i, o in enumerate(self.options):
            if str(o.get("value", "")) == self.value:
                return i
        return -1

    @property
    def cur_label(self) -> str:
        i = self.cur_index
        return str(self.options[i].get("label", "")) if i >= 0 else ""

    @property
    def open(self) -> bool:
        return self._open

    def set_open(self, value: bool):
        value = bool(value)
        if value == self._open:
            return
        self._open = value
        if value:
            self.sel_index = max(0, self.cur_index)
            self._popover.popup()
        else:
            self._popover.popdown()
        self._paint()

    def _on_popover_closed(self, *_a):
        if self._open:
            self._open = False
            self._paint()
            if self.on_chip_clicked:
                # notify host to sync its open-tracking
                pass

    def update_value(self, value: str):
        self.value = str(value)
        self._label.set_label(self.cur_label)
        self._size_chip()
        self._paint()

    # --- keyboard/pointer entry points (host routes keys) -----------------------

    def move_sel(self, direction: int):
        if not self._open or not self.options:
            return
        self.sel_index = max(
            0, min(len(self.options) - 1, self.sel_index + int(direction))
        )
        self._paint()

    def pick_sel(self):
        if 0 <= self.sel_index < len(self.options):
            self.pick_value(self.options[self.sel_index].get("value", ""))

    def pick_value(self, value: str):
        self.set_open(False)
        self.value = str(value)
        self._label.set_label(self.cur_label)
        self._size_chip()
        self._paint()
        if self.on_pick:
            self.on_pick(self.value)

    def _pick_row(self, index: int):
        if 0 <= index < len(self.options):
            self.pick_value(self.options[index].get("value", ""))

    def _hover_row(self, index: int):
        if self._open:
            self.sel_index = index
            self._paint()

    def _chip_click(self):
        if self.on_chip_clicked:
            self.on_chip_clicked()

    def _size_chip(self):
        if not self.glyph:
            w = text_width(self.cur_label, self._label_px) + int(26 * self._s)
            self._chip_body.set_size_request(max(self.chip_h, w), self.chip_h)

    # --- painting ---------------------------------------------------------------

    def _paint(self):
        theme = self._theme
        open_ = self._open
        if self.glyph:
            self._glyph_icon.set_color(theme.verm_lit if open_ else theme.icon_dim)
        else:
            self._label.set_style(
                f"color: {theme.verm_lit if open_ else theme.subtle};"
                f"font-size: {self._label_px}px;font-weight: 600;"
            )
            self._chevron.set_color(theme.verm_lit if open_ else theme.faint)
            self._chevron.set_glyph("chevron-up" if open_ else "chevron-down")
        for row in self._rows:
            is_current = str(self.options[row.index].get("value", "")) == self.value
            row.paint(
                current=is_current, selected=(row.index == self.sel_index and open_)
            )
