"""Toast content
Renders the newest notification popup inside the morphing pill body: icon tile,
app eyebrow, summary with a critical ember dot, optional body and action pills.
Owns no background — the pill body behind it provides the washi material. A
press anywhere dismisses the toast (the notification stays tracked in the
inbox); action pills consume their own clicks. Auto-expires on the
``Notifs.expireAt`` deadline unless the notification is critical.
"""

import time as _time

from fabric.utils import Gdk, GLib
from fabric.widgets.box import Box
from fabric.widgets.eventbox import EventBox
from fabric.widgets.label import Label
from fabric.widgets.overlay import Overlay
from gi.repository import Pango

from components.notif_tile import NotifTile
from core.theme import Theme
from services.notifs import Notifs

CRITICAL = 2


def _badge_pill_style(theme: Theme, s: float, primary: bool) -> str:
    return (
        f"background: {theme.tile_bg};"
        f"border: 1px solid {theme.border};"
        f"border-radius: 999px;"
        f"padding: {int(3 * s)}px {int(8 * s)}px;"
        f"color: {theme.verm_lit if primary else theme.dim};"
        f"font-size: {max(9, int(9.5 * s))}px;"
        f"font-weight: 600;"
    )


class Toast(Box):
    """The toast body shown when ``Notifs.popups`` is non-empty."""

    def __init__(
        self, theme: Theme, s: float = 1.0, notifs: Notifs | None = None, **kwargs
    ):
        super().__init__(size=(int(310 * s), -1), **kwargs)
        self._theme = theme
        self._s = s
        self._notifs = notifs
        self._notif = None
        self._deadline = 0.0
        self._live = False
        self._expire_source: int | None = None

        self._tile = NotifTile(theme, s=s, size=28, v_align="start")

        self._eyebrow = Label(
            label="",
            style=(
                f"color: {theme.dim};"
                f"font-size: {max(8, int(8.5 * s))}px;"
                f"font-weight: 600;"
                f"letter-spacing: 1.4px;"
            ),
        )
        self._overflow = Label(
            label="",
            visible=False,
            h_align="end",
            v_align="end",
            style=(
                f"color: {theme.dim};"
                f"font-size: {max(8, int(9 * s))}px;"
                f"font-weight: 600;"
            ),
        )

        eyebrow_row = Box(spacing=int(6 * s), hexpand=True)
        eyebrow_row.pack_start(self._eyebrow, False, False, 0)

        self._ember = Box(
            size=(int(4 * s), int(4 * s)),
            v_align="center",
            style=(
                f"background: {theme.flame_glow};"
                f"border-radius: 999px;"
                f"margin-top: {int(2 * s)}px;"
            ),
        )
        self._ember.set_no_show_all(True)
        self._ember.set_visible(False)
        self._summary = Label(
            label="",
            style=(
                f"color: {theme.cream};"
                f"font-size: {max(11, int(11.5 * s))}px;"
                f"font-weight: 600;"
            ),
        )
        self._summary.set_xalign(0)
        self._summary.set_ellipsize(Pango.EllipsizeMode.END)

        self._body = Label(
            label="",
            style=f"color: {theme.dim}; font-size: {max(10, int(10.5 * s))}px;",
        )
        self._body.set_xalign(0)
        self._body.set_line_wrap(True)
        self._body.set_lines(2)
        self._body.set_ellipsize(Pango.EllipsizeMode.END)
        self._body.set_no_show_all(True)
        self._body.set_visible(False)

        summary_row = Box(spacing=int(5 * s), h_expand=True)
        summary_row.add(self._ember)
        summary_row.add(self._summary)

        self._actions = Box(spacing=int(6 * s))
        self._actions.set_no_show_all(True)
        self._actions.set_visible(False)

        self._col = Box(orientation="v", spacing=int(3 * s), v_align="start")
        # fixed column width keeps the wrapped-body natural height stable while
        # the pill measures the toast body (342·s − 2×16·s margins − 28 tile − 10 gap)
        self._col.set_size_request(int(264 * s), -1)
        self._col.add(eyebrow_row)
        self._col.add(summary_row)
        self._col.add(self._body)
        self._col.add(self._actions)

        self._content = Box(spacing=int(10 * s), h_align="start")
        self._content.add(self._tile)
        self._content.add(self._col)
        self._content.set_hexpand(True)

        layout = Overlay(child=self._content, overlays=[self._overflow])

        self._click = EventBox(
            events=Gdk.EventMask.BUTTON_PRESS_MASK,
        )
        self._click.add(layout)
        self._click.connect("button-press-event", self._on_dismiss)
        self.add(self._click)

    # --- data ------------------------------------------------------------------

    def set_notif(self, n):
        if n is self._notif:
            self._refresh_live()
            return
        self._notif = n
        critical = getattr(n, "urgency", 1) == CRITICAL
        self._tile.set_from_notif(n)
        popups = self._notifs.popups if self._notifs else []
        self._overflow.set_label(f"+{len(popups) - 1}" if len(popups) > 1 else "")
        self._overflow.set_visible(len(popups) > 1)
        self._eyebrow.set_label((getattr(n, "app_name", None) or "System").upper())
        self._ember.set_visible(critical)
        self._summary.set_label(getattr(n, "summary", "") or "")
        body = getattr(n, "body", "") or ""
        self._body.set_label(body)
        if body:
            self._body.show()
        else:
            self._body.hide()
        self._rebuild_actions(n, critical)
        self._deadline = self._notifs.deadline_for(n) if self._notifs else 0.0
        # Show only the always-visible widgets explicitly — do NOT use show_all()
        # as it overrides the carefully managed visibility of _body, _ember, _actions
        self._eyebrow.show()
        self._summary.show()
        self._tile.show()
        self._col.show()
        self._content.show()
        self._click.show()
        self.show()
        self._sync_timer()

    def set_live(self, live: bool):
        self._live = bool(live)
        self._sync_timer()

    # --- internals ---------------------------------------------------------------

    def _refresh_live(self):
        # same popup re-shown (e.g. another arrived behind it) — keep its
        # deadline but make sure the timer state follows the new visibility
        self._sync_timer()

    def _rebuild_actions(self, n, critical: bool):
        for child in self._actions.get_children():
            self._actions.remove(child)
        acts = [a for a in getattr(n, "actions", None) or [] if getattr(a, "label", "")]
        if acts:
            self._actions.show()
        else:
            self._actions.hide()

        for i, act in enumerate(acts):
            pill = EventBox(
                events=Gdk.EventMask.BUTTON_PRESS_MASK,
                child=Label(
                    label=act.label,
                    style=_badge_pill_style(self._theme, self._s, i == 0),
                ),
                visible=False,
            )
            pill.set_no_show_all(True)
            pill.set_visible(True)
            pill.connect(
                "realize",
                lambda w, *_: (
                    w.get_window()
                    and w.get_window().set_cursor(
                        Gdk.Cursor.new_for_display(
                            w.get_display(), Gdk.CursorType.HAND2
                        )
                    )
                ),
            )
            pill.connect(
                "button-press-event",
                lambda *_a, a=act: self._on_action(a),
            )
            self._actions.add(pill)

    def _on_action(self, act):
        if self._notif is None:
            return
        try:
            act.invoke()
        except GLib.Error:
            pass
        if getattr(act, "identifier", "") == "default":
            self._notifs.raise_window(self._notif)
        self._notifs.remove_popup(self._notif)

    def _on_dismiss(self, *_a):
        if self._notif is not None:
            self._notifs.remove_popup(self._notif)
        return True

    # --- expiry ------------------------------------------------------------------

    def _sync_timer(self):
        if self._expire_source is not None:
            GLib.source_remove(self._expire_source)
            self._expire_source = None
        if not self._live or self._notif is None:
            return
        if getattr(self._notif, "urgency", 1) == CRITICAL:
            return
        if self._deadline <= 0:
            self._deadline = self._notifs.deadline_for(self._notif)
        remaining = max(1, int(self._deadline - _time.time() * 1000))
        self._expire_source = GLib.timeout_add(remaining, self._on_expire)

    def _on_expire(self):
        self._expire_source = None
        if self._notif is not None:
            self._notifs.remove_popup(self._notif)
        return GLib.SOURCE_REMOVE

    # --- measurement ---------------------------------------------------------------

    def content_height(self) -> int:
        th = self._tile.get_preferred_height()[1]
        _, ch = self._col.get_preferred_height_for_width(int(264 * self._s))
        nh = max(th, ch)
        with open("/tmp/pill_height.log", "a") as log:
            log.write(f"content_height: th={th}, ch={ch}, nh={nh}\\n")
        return int(nh) if nh > 0 else int(64 * self._s)

    def set_scale(self, s: float):
        """Re-render every part at a new global scale (flags.uiScale change)."""
        self._s = s
        self._col.set_size_request(int(264 * s), -1)
        if self._notif is not None:
            n = self._notif
            self._notif = None
            self.set_notif(n)
