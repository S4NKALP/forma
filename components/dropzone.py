"""DropZone — the drag-over face of the resting pill.

Four vermillion corner brackets frame a centre column that walks through the
drop lifecycle: "drop to install" -> spinner + pct + elapsed -> checkmark
(installed/updated/reinstalled) or a red rejection for a non-AppImage drop.

"""

from fabric.widgets.box import Box
from fabric.widgets.label import Label
from fabric.widgets.overlay import Overlay

from core.theme import Theme

_BAD = "#e0533f"
_SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


class DropZone(Box):
    def __init__(self, theme: Theme, s: float = 1.0, **kwargs):
        super().__init__(**kwargs)
        self._theme = theme
        self._s = s

        self._overlay = Overlay(name="drop-zone")
        self.add(self._overlay)
        self._overlay.set_hexpand(True)
        self._overlay.set_vexpand(True)

        self._accent = theme.verm_lit
        self._corners: list[Box] = []
        for halign, valign in (
            ("start", "start"),
            ("end", "start"),
            ("start", "end"),
            ("end", "end"),
        ):
            corner = self._corner(halign, valign)
            self._corners.append(corner)
            self._overlay.add_overlay(corner)

        self._glyph = Label(
            label="↓",
            style=(
                f"color: {self._accent};"
                f"font-size: {max(12, int(22 * s))}px;"
                f"font-weight: 600;"
            ),
        )
        self._title = Label(
            label="Drop to install",
            style=(
                f"color: {theme.cream};"
                f"font-size: {max(10, int(13 * s))}px;"
                f"font-weight: 500;"
            ),
        )
        self._name = Label(
            label="",
            style=(f"color: {theme.faint};font-size: {max(8, int(10 * s))}px;"),
        )
        center = Box(
            orientation="v", h_align="center", v_align="center", spacing=int(4 * s)
        )
        center.add(self._glyph)
        center.add(self._title)
        center.add(self._name)

        # Overlay's size and overlay positioning is based on the main child.
        # We use a dummy expanding box as the main child so corners go to the edges.
        dummy = Box(h_expand=True, v_expand=True)
        self._overlay.add(dummy)
        self._overlay.add_overlay(center)

        self.set_no_show_all(True)
        self.set_visible(False)

    def _corner(self, halign: str, valign: str) -> Box:
        s = self._s
        br_len = max(6, int(15 * s))
        br_thick = max(1, int(2 * s))
        radius = max(1, br_thick // 2)
        v = Overlay()
        v.set_size_request(br_len, br_len)
        v.add(
            Box(
                h_align=halign,
                v_align=valign,
                size=(br_len, br_thick),
                style=f"background: {self._accent}; border-radius: {radius}px;",
            )
        )
        v.add_overlay(
            Box(
                h_align=halign,
                v_align=valign,
                size=(br_thick, br_len),
                style=f"background: {self._accent}; border-radius: {radius}px;",
            )
        )
        c = Box(h_align=halign, v_align=valign, style=f"margin: {int(12 * s)}px;")
        c.add(v)
        return c

    def set_stage(
        self,
        stage: str,
        name: str = "",
        pct: str = "",
        seconds: int = 0,
        title: str = "",
    ):
        bad = stage in ("bad", "fail")
        accent = _BAD if bad else self._theme.verm_lit
        for corner in self._corners:
            box = corner.get_children()[0]
            for bar in box.get_children():
                bar.set_style(
                    f"background: {accent}; border-radius: {max(1, int(2 * self._s)) // 2}px;"
                )
        if stage == "installing":
            self._glyph.set_label(_SPINNER[seconds % len(_SPINNER)])
            t = "Installing"
            if pct:
                t += f"  {pct}"
            if seconds >= 3:
                mm = seconds // 60
                t += f"  {mm}:{seconds % 60:02d}"
            self._title.set_label(title or t)
        elif stage == "done":
            self._glyph.set_label("✓")
            self._title.set_label(title or "Installed")
        elif stage == "bad":
            self._glyph.set_label("✕")
            self._title.set_label(title or "Can't install this")
        elif stage == "fail":
            self._glyph.set_label("✕")
            self._title.set_label(title or "Install failed")
        else:
            self._glyph.set_label("↓")
            self._title.set_label(title or "Drop to install")
        self._glyph.set_style(
            f"color: {accent}; font-size: {max(12, int(22 * self._s))}px; font-weight: 600;"
        )
        self._name.set_label(name)
        self._name.set_visible(bool(name))

    # --- scale ----------------------------------------------------------------

    def set_scale(self, s: float):
        self._s = s
