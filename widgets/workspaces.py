"""Per-island workspace dot strip .

Built on fabric's HyprlandWorkspaces so socket IPC, workspace creation/activation
and the hl.dsp.focus click dispatch come free. The strip is restyled as the
ukishima signature: a dim cream dot per workspace, the active one a wider
vermillion stick, brightening on hover. All width/color styling and the
width transition live in CSS (#ws-strip > button), so workspace
initialization is instant — no manual factory bookkeeping or per-button
tweens.

The widget is global to the session (every workspace gets a dot); a `screen`
label is carried for the ukishima per-monitor rule range later.
"""

from fabric.hyprland.widgets import HyprlandWorkspaces, WorkspaceButton
from gi.repository import Gdk

STICK_W = 17
DOT_W = 5
GAP = 4

pointer_cursor: Gdk.Cursor | None = None  # lazily created, shared by all buttons


class Workspaces(HyprlandWorkspaces):
    """CSS-styled dot strip based on HyprlandWorkspaces."""

    def __init__(
        self,
        scale: float = 1.0,
        gap: float | None = None,
        interactive: bool = True,
        screen: str = "",
        presets: tuple[int, ...] = (),
        **kwargs,
    ):
        self.scale = float(scale or 1.0)
        self.gap = gap if gap is not None else GAP * self.scale
        self.dot_w = DOT_W * self.scale
        self.stick_w = STICK_W * self.scale
        self.interactive = interactive
        self.screen = screen

        buttons = [self.make_button(ws_id) for ws_id in presets]
        super().__init__(
            buttons=buttons,
            buttons_factory=self.make_button,
            invert_scroll=False,
            spacing=self.gap,
            name="ws-strip",
            **kwargs,
        )
        if not interactive:
            self.set_can_focus(False)

    def make_button(self, ws_id: int) -> WorkspaceButton | None:
        # special workspaces carry negative ids — never render a dot for them
        if ws_id < 1:
            return None
        button = WorkspaceButton(id=ws_id, label=None, name="ws-dot")
        if not self.interactive:
            button.set_sensitive(False)
            button.set_can_focus(False)
            return button  # non-interactive: leave default cursor, don't imply clickability

        def on_realize(b: WorkspaceButton) -> None:
            global pointer_cursor
            window = b.get_window()
            if window is None:
                return
            if pointer_cursor is None:
                pointer_cursor = Gdk.Cursor.new_from_name(
                    Gdk.Display.get_default(), "pointer"
                )
            window.set_cursor(pointer_cursor)

        button.connect(
            "realize", on_realize
        )  # keep the click dispatcher's pointer cursor
        return button

    # --- geometry helpers ------------------------------------------------------

    def strip_width(self) -> int:
        """Current laid-out strip width (used for the OSD morph target).

        The buttons carry no intrinsic size (CSS sets it), so estimate from the
        authored constants — the morph target only needs an accurate rest width.
        Accounts for the active button rendering as a wider stick, not a dot.
        """
        buttons = self._buttons.values()
        if not buttons:
            return 0
        widths = [self.stick_w if b.active else self.dot_w for b in buttons]
        return int(sum(widths) + self.gap * (len(widths) - 1))

    def slot_center_x(self, idx: int) -> float:
        """Centre x of the idx-th slot from target widths (not the live rules),
        matching Workspaces.slotCenterX — the OSD focuses the settled position."""
        ordered = sorted(self._buttons.values(), key=lambda b: b.id)
        if not ordered:
            return 0.0
        x = 0.0
        for i, button in enumerate(ordered):
            w = self.stick_w if button.active else self.dot_w
            if i == idx:
                return x + w / 2
            x += w + self.gap
        return x
