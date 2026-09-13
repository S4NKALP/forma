"""Screen-brightness OSD face
Mirrors the volume/mic ``AudioOsd``: symbolic icon + ``Fader`` filament +
percent readout. The pill swaps it into the OSD face when the
``Brightness.screen`` signal fires.
"""

from fabric.widgets.box import Box
from fabric.widgets.image import Image
from fabric.widgets.label import Label

from components.fader import Fader


class BrightnessOsd(Box):
    """Icon + filament fill + percent readout for screen-brightness flashes."""

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
            icon_name="display-brightness-symbolic",
            icon_size=int(16 * s),
            style=f"color: {theme.cream};",
        )
        self._fader = Fader(theme, s=s)
        self._pct = Label(
            label="0%",
            style=(
                f"font-size: {max(9, int(13 * s))}px;"
                f"font-weight: bold; color: {theme.cream};"
            ),
        )
        self._pct.set_width_chars(4)

        # fixed face width so icon + fader + pct never shrink-wrap the centred
        # face onto just the icon, and the fader gets a flex allocation
        self.set_size_request(int(248 * s), -1)
        self.add(self._icon)
        self.add(self._fader)
        self.add(self._pct)
        self.set_no_show_all(True)
        self.set_visible(False)

    def flash(self, value: float):
        """Freshen percent + fill for a brightness change (0..1 fraction)."""
        pct = min(100.0, round(max(0.0, min(1.0, float(value))) * 100.0))
        self._pct.set_label(f"{int(pct)}%")
        self._fader.set_value(value, on=True)

    def set_scale(self, s: float):
        self._s = s
        self.set_size_request(int(248 * s), -1)
        self._icon.set_from_icon_name("display-brightness-symbolic", int(16 * s))
        self._pct.set_style(
            f"font-size: {max(9, int(13 * s))}px;"
            f"font-weight: bold; color: {self._theme.cream};"
        )
        self._fader.refresh()
