"""Keyboard-layout OSD face — compact layout code flash on layout switch.

Icon + big layout code (ukishima ``code`` folding, e.g. "US"/"RU"); the pill
swaps this face in whenever the KeyboardLayout service reports a change. No
fader — mirrors the LockKeyOsd shape but shows the live layout code instead of
an on/off state.
"""

from __future__ import annotations

from fabric.widgets.box import Box
from fabric.widgets.label import Label

from components.glyph_icon import GlyphIcon


class KeyboardOsd(Box):
    """Icon + layout-code readout flashed on keyboard-layout switches."""

    def __init__(self, theme, s: float = 1.0):
        super().__init__(
            spacing=int(12 * s),
            h_align="center",
            v_align="center",
            style=(f"padding-left: {int(24 * s)}px;padding-right: {int(24 * s)}px;"),
        )
        self._theme = theme
        self._s = s

        self._icon = GlyphIcon(
            name="keyboard",
            color=theme.dim,
            size=int(18 * s),
        )
        self._code = Label(
            label="US",
            style=(
                f"font-size: {max(10, int(15 * s))}px;"
                f"font-weight: bold; color: {theme.cream};"
                f"font-feature-settings: 'tnum';"
            ),
        )
        self._code.set_width_chars(3)

        self.add(self._icon)
        self.add(self._code)
        self.add(
            Label(
                label="LAYOUT",
                style=(
                    f"font-size: {max(7, int(8.5 * s))}px;"
                    f"font-weight: bold; color: {theme.subtle};"
                ),
            )
        )
        self.set_no_show_all(True)
        self.set_visible(False)

    def flash(self, code: str):
        self._code.set_label((code or "US").upper()[:3])

    def set_scale(self, s: float):
        self._s = s
        self._icon.set_color(self._theme.dim)
        self._code.set_style(
            f"font-size: {max(10, int(15 * s))}px;"
            f"font-weight: bold; color: {self._theme.cream};"
            f"font-feature-settings: 'tnum';"
        )
        self._code.set_width_chars(3)
