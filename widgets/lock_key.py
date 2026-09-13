"""Lock-key OSD face
Pure icon + on/off label — no fader. Driven by the ``CapsLock``/``NumLock``
services (evdev LED monitors); the pill swaps this face in when one of those
keys toggles.
"""

from fabric.widgets.box import Box
from fabric.widgets.image import Image
from fabric.widgets.label import Label

_KEY_NAMES = {
    "caps": "CAPS LOCK",
    "num": "NUM LOCK",
    "scroll": "SCROLL LOCK",
}


class LockKeyOsd(Box):
    """Icon + key name + on/off state readout for lock-key flashes."""

    def __init__(self, theme, s: float = 1.0):
        super().__init__(
            spacing=int(12 * s),
            h_align="center",
            v_align="center",
            style=(f"padding-left: {int(24 * s)}px;padding-right: {int(24 * s)}px;"),
        )
        self._theme = theme
        self._s = s

        self._icon = Image(
            icon_name="input-keyboard-symbolic",
            icon_size=int(16 * s),
            style=f"color: {theme.dim};",
        )
        self._name = Label(
            label="CAPS LOCK",
            style=(
                f"font-size: {max(9, int(11 * s))}px;"
                f"font-weight: bold; color: {theme.subtle};"
            ),
        )
        self._state = Label(
            label="OFF",
            style=(
                f"font-size: {max(9, int(13 * s))}px;"
                f"font-weight: bold; color: {theme.dim};"
            ),
        )
        self._state.set_width_chars(4)

        # NO forced face width: unlike audio/brightness (whose hexpand fader
        # balances the layout) this face has nothing to expand, so a fixed
        # width would pin the icon+name+state cluster left of centre. Keep the
        # natural width — the overlay centres it on the pill/screen.
        self.add(self._icon)
        self.add(self._name)
        self.add(self._state)
        self.set_no_show_all(True)
        self.set_visible(False)

    def flash(self, kind: str, is_on: bool):
        """Freshen icon, key name + on/off state for a lock-key event."""
        enabled = bool(is_on)
        color = self._theme.verm_lit if enabled else self._theme.dim
        self._icon.set_style(f"color: {color};")
        self._state.set_style(
            f"font-size: {max(9, int(13 * self._s))}px;"
            f"font-weight: bold; color: {color};"
        )
        self._state.set_label("ON" if enabled else "OFF")
        self._name.set_label(_KEY_NAMES.get(kind, "KEY LOCK"))

    def set_scale(self, s: float):
        self._s = s
        self._icon.set_from_icon_name("input-keyboard-symbolic", int(16 * s))
        self._state.set_style(
            f"font-size: {max(9, int(13 * s))}px;"
            f"font-weight: bold; color: {self._theme.dim};"
        )
        self._name.set_style(
            f"font-size: {max(9, int(11 * s))}px;"
            f"font-weight: bold; color: {self._theme.subtle};"
        )
