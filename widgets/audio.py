"""
One self-contained widget so the pill only swaps it in and out: a symbolic
icon + a horizontal filament (``Gtk.LevelBar`` styled as the thread/fill) +
a percent readout. Muted drops icon and fill to the dim stroke colour.
"""

from fabric.widgets.box import Box
from fabric.widgets.image import Image
from fabric.widgets.label import Label

_CREAM_TIER = (
    "audio-volume-low-symbolic",
    "audio-volume-medium-symbolic",
    "audio-volume-high-symbolic",
)
from components.fader import Fader


class AudioOsd(Box):
    """Icon + filament fill + percent readout for volume / mic flashes."""

    def __init__(self, theme, s: float = 1.0):
        super().__init__(
            spacing=int(12 * s),
            h_align="center",
            v_align="center",
            style=(f"padding-left: {int(24 * s)}px;padding-right: {int(24 * s)}px;"),
        )
        self._theme = theme
        self._s = s
        self._last_icon = "audio-volume-high-symbolic"

        self._icon = Image(
            icon_name=self._last_icon,
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

    def flash(self, kind: str, value: float, muted: bool):
        """Freshen icon, percent + fill for a volume/mic event."""
        pct = min(100.0, round(max(0.0, float(value)) * 100.0))
        color = self._theme.dim if muted else self._theme.cream
        self._icon.set_style(f"color: {color};")
        self._pct.set_style(
            f"font-size: {max(9, int(13 * self._s))}px;"
            f"font-weight: bold; color: {color};"
        )
        self._pct.set_label(f"{int(pct)}%")

        if kind == "volume":
            if muted:
                icon_name = "audio-volume-muted-symbolic"
            else:
                icon_name = (
                    _CREAM_TIER[0]
                    if pct < 33
                    else (_CREAM_TIER[1] if pct < 66 else _CREAM_TIER[2])
                )
        else:
            icon_name = (
                "microphone-sensitivity-muted-symbolic"
                if muted
                else "audio-input-microphone-symbolic"
            )
        self._icon.set_from_icon_name(icon_name, int(16 * self._s))
        self._last_icon = icon_name
        self._fader.set_value(value, on=not muted)

    def set_scale(self, s: float):
        self._s = s
        self.set_size_request(int(248 * s), -1)
        self._icon.set_from_icon_name(self._last_icon, int(16 * s))
        self._pct.set_style(
            f"font-size: {max(9, int(13 * s))}px;"
            f"font-weight: bold; color: {self._theme.cream};"
        )
        self._fader.refresh()
