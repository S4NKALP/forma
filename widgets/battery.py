"""
Mirrors the volume/mic/brightness faces: battery icon + ``Fader`` filament +
percent readout. Faithful to ukishima's ``batteryRow``: the fill is a
``vermDeep -> flameGlow`` horizontal gradient (charging), the icon reads
vermillion-flame while charging, low percent tints vermilion. ukishima also
sweeps a sheen highlight across the fill while charging and shows a bolt
glyph instead of a level icon — the icon here is the UPower-provided level
icon (which carries the bolt for the charging tier) and the sheen is skipped
until a full animation clock lands.
"""

from fabric.widgets.box import Box
from fabric.widgets.image import Image
from fabric.widgets.label import Label
from gi.repository import Gtk

from components.fader import Fader

_LEVEL_STEPS = (0, 20, 40, 60, 80, 100)


def _tier(pct: int) -> int:
    """Round percent down to a battery-level tier (0/20/40/60/80/100)."""
    tier = 0
    for step in _LEVEL_STEPS:
        if pct >= step:
            tier = step
    return tier


def _resolve_icon(*candidates: str) -> str:
    """First candidate present in the active icon theme, else the first.

    The service reports UPower's two-digit names (``battery-level-04-…``)
    which are missing from most shipped themes, so always ask the live
    ``Gtk.IconTheme`` — Adwaita/hicolor spell them single-digit
    (``battery-level-40-charging-symbolic``), Papirus as zero-padded
    (``battery-040-charging-symbolic``).
    """
    theme = Gtk.IconTheme.get_default()
    for candidate in candidates:
        if theme.has_icon(candidate):
            return candidate
    return candidates[0]


class BatteryOsd(Box):
    """Icon + gradient filament fill + percent readout for battery flashes."""

    _LOW_PCT = 30  # at or below: richen to vermilion (ukishima glance low rule)

    def __init__(self, theme, s: float = 1.0):
        super().__init__(
            spacing=int(12 * s),
            h_align="center",
            v_align="center",
            style=(f"padding-left: {int(24 * s)}px;padding-right: {int(24 * s)}px;"),
        )
        self._theme = theme
        self._s = s
        self._last_icon = "battery-level-08-symbolic"

        self._icon = Image(
            icon_name=self._last_icon,
            icon_size=int(17 * s),
            style=f"color: {theme.cream};",
        )
        # gradient fill matches ukishima battery OSD (vermDeep -> flameGlow);
        # scoped to its own css name so the shared #osd-fader keeps solid verm
        self._fader = Fader(
            theme,
            s=s,
            gradient_colors=(str(theme.verm_deep), str(theme.flame_glow)),
            css_name="battery-fader",
        )
        self._pct = Label(
            label="0%",
            style=(
                f"font-size: {max(9, int(13 * s))}px;"
                f"font-weight: bold; color: {theme.cream};"
            ),
        )
        self._pct.set_width_chars(4)
        self._pct.set_xalign(1.0)

        # fixed face width so icon + fader + pct never shrink-wrap the centred
        # face onto just the icon, and the fader gets a flex allocation
        self.set_size_request(int(248 * s), -1)
        self.add(self._icon)
        self.add(self._fader)
        self.add(self._pct)
        self.set_no_show_all(True)
        self.set_visible(False)

    def flash(self, pct: int, charging: bool):
        """Freshen icon, percent + fill for a battery change (percent 0..100)."""
        pct = min(100, max(0, int(pct)))
        if charging:
            color = self._theme.flame_glow
            lvl = max(_tier(pct), 20)  # 0%-charging is an empty outline
            icon_name = _resolve_icon(
                f"battery-level-{lvl}-charging-symbolic",
                f"battery-{lvl:03d}-charging-symbolic",
            )
        elif pct >= 100:
            color = self._theme.cream
            icon_name = _resolve_icon(
                "battery-level-100-charged-symbolic",
                "battery-full-charged-symbolic",
                "battery-100-charged",
            )
        elif pct <= self._LOW_PCT:
            color = self._theme.verm_lit
            icon_name = _resolve_icon(
                "battery-level-0-symbolic",
                "battery-000-symbolic",
            )
        else:
            color = self._theme.cream
            lvl = _tier(pct)
            icon_name = _resolve_icon(
                f"battery-level-{lvl}-symbolic",
                f"battery-{lvl:03d}-symbolic",
            )
        self._icon.set_from_icon_name(icon_name, int(17 * self._s))
        self._icon.set_style(f"color: {color};")
        self._last_icon = icon_name
        self._pct.set_style(
            f"font-size: {max(9, int(13 * self._s))}px;"
            f"font-weight: bold; color: {color};"
        )
        self._pct.set_label(f"{int(pct)}%")
        self._fader.set_value(pct / 100.0, on=True)

    def set_scale(self, s: float):
        self._s = s
        self.set_size_request(int(248 * s), -1)
        self._icon.set_from_icon_name(self._last_icon, int(17 * s))
        self._pct.set_style(
            f"font-size: {max(9, int(13 * s))}px;"
            f"font-weight: bold; color: {self._theme.cream};"
        )
        self._fader.refresh()
