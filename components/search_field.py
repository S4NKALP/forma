"""
Text field + faint counter in one row. Arrow keys route to ``moved`` (so a
horizontal strip can page, or the launcher can move its selection), Return →
``accepted``, Escape → ``dismissed``. The caret is the native GTK3 blink; the
placeholder is drawn as an overlay label so its colour stays theme-controlled.

A hairline separator is drawn automatically as a CSS bottom-border on the
component itself. Pass ``show_separator=False`` to suppress it.
"""

from fabric.utils import Gdk
from fabric.widgets.box import Box
from fabric.widgets.entry import Entry
from fabric.widgets.label import Label

from core.theme import Theme


class SearchField(Box):
    """Entry + counter in a horizontal row; emits moved/accepted/dismissed.

    A hairline separator appears below via ``border-bottom`` CSS — no extra
    widget needed by the caller.
    """

    def __init__(
        self,
        theme: Theme,
        s: float = 1.0,
        placeholder: str = "",
        counter_text: str = "",
        show_separator: bool = True,
        **kwargs,
    ):
        sep_style = (
            f"border-bottom: 1px solid {theme.hair};"
            f"padding-bottom: {int(8 * s)}px;"
            f"margin-bottom: {int(8 * s)}px;"
            if show_separator
            else ""
        )
        super().__init__(
            name="search-field",
            orientation="h",
            h_align="fill",
            v_align="center",
            spacing=int(10 * s),
            style=sep_style,
            **kwargs,
        )
        self._theme = theme
        self._s = s

        self.entry = Entry(
            name="launcher-search",
            h_expand=True,
            style=(
                f"color: {theme.cream};"
                f"font-size: {max(10, int(12 * s))}px;"
                f"background: transparent;"
                f"border: none;"
                f"box-shadow: none;"
                f"padding: {int(4 * s)}px {int(2 * s)}px;"
                f"caret-color: {theme.verm_lit};"
            ),
        )

        self._placeholder_label = Label(
            label=placeholder,
            h_align="start",
            v_align="center",
            style=(
                f"color: {theme.faint};"
                f"font-size: {max(10, int(12 * s))}px;"
                f"margin-left: {int(4 * s)}px;"
            ),
        )
        self._placeholder_label.set_visible(bool(placeholder))

        from fabric.widgets.overlay import Overlay

        self._entry_overlay = Overlay(
            child=self.entry,
            h_expand=True,
        )
        self._entry_overlay.add_overlay(self._placeholder_label)
        self._entry_overlay.set_overlay_pass_through(self._placeholder_label, True)
        self.entry.set_events(self.entry.get_events() | Gdk.EventMask.KEY_PRESS_MASK)
        self.entry.connect("key-press-event", self._on_key)
        self.entry.connect("changed", self._on_changed)

        self._entry_overlay.show_all()
        self._placeholder_label.set_visible(
            not bool(self.entry.get_text()) and bool(placeholder)
        )

        self._counter = Label(
            label=counter_text,
            style_classes=["search-counter"],
            style=(
                f"color: {theme.faint};"
                f"font-size: {max(9, int(10 * s))}px;"
                f"font-feature-settings: 'tnum';"
            ),
        )

        self.add(self._entry_overlay)
        self.add(self._counter)
        self._action_widget = None

    # --- API ------------------------------------------------------------------

    @property
    def text(self) -> str:
        return self.entry.get_text()

    @text.setter
    def text(self, value: str):
        self.entry.set_text(value)

    def focus(self):
        self.entry.grab_focus()

    def set_counter(self, text: str):
        self._counter.set_label(text)
        self._counter.set_visible(bool(text))

    def set_action_widget(self, widget):
        if self._action_widget:
            self.remove(self._action_widget)
        self._action_widget = widget
        if widget:
            self.add(widget)
            widget.show_all()

    # --- callbacks (subclass/pill hooks) ---------------------------------------

    def on_moved(self, delta: int):
        pass

    def on_accepted(self):
        pass

    def on_shift_accepted(self):
        pass

    def on_shift_delete(self):
        pass

    def on_dismissed(self):
        pass

    # --- events ----------------------------------------------------------------

    def _on_changed(self, *_a):
        text = self.entry.get_text()
        if self._placeholder_label.get_label():
            self._placeholder_label.set_visible(not bool(text))

    def _on_key(self, _widget, event):
        keyval = event.keyval
        shift = bool(event.state & Gdk.ModifierType.SHIFT_MASK)

        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            if shift:
                self.on_shift_accepted()
            else:
                self.on_accepted()
            return True
        if keyval == Gdk.KEY_BackSpace and shift:
            self.on_shift_delete()
            return True
        if keyval == Gdk.KEY_Escape:
            self.on_dismissed()
            return True
        if keyval == Gdk.KEY_Up:
            self.on_moved(-1)
            return True
        if keyval == Gdk.KEY_Down:
            self.on_moved(1)
            return True
        return False
