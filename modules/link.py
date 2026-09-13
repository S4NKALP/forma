"""Link inbox surface
INBOX — the notification center. Grouped per app with critical rows pinned
above the fold (flagged by a vermilion hairline), duplicate summary+body rows
coalesced into a ×N badge, an inline clear-all in the header, and a silence
empty state. Opening marks all notifications seen after a short beat so the
unread ember registers first. Width 330·s; height adapts to the content (scroll
clamped to 320·s), margins 13/16/16/13.
"""

from collections.abc import Callable

from fabric.utils import Gdk, GLib
from fabric.widgets.box import Box
from fabric.widgets.eventbox import EventBox
from fabric.widgets.image import Image
from fabric.widgets.label import Label
from fabric.widgets.scrolledwindow import ScrolledWindow
from gi.repository import Pango

from components.notif_tile import NotifTile
from core.theme import Theme
from services.notifs import notifs

_LINK_W = 330
_HEADER_H = 24
_HAIR = 1
_GROUP_H = 32
_ROW_H = 26
_SCROLL_MAX = 320
_SCROLL_MIN = 80
_MT = 13
_MLR = 16
_MB = 13
_CRITICAL = 2


def _caps(text: str) -> str:
    return (text or "").upper()


class NotifRow(EventBox):
    """Single inbox entry row (or coalesced entry): icon, text, age→dismiss."""

    def __init__(
        self,
        theme: Theme,
        s: float,
        entry: dict,
        critical: bool,
        svc,
        on_close: Callable[[], None],
        **kwargs,
    ):
        h = int(_ROW_H * s)
        super().__init__(
            events=(
                Gdk.EventMask.BUTTON_PRESS_MASK
                | Gdk.EventMask.ENTER_NOTIFY_MASK
                | Gdk.EventMask.LEAVE_NOTIFY_MASK
            ),
            size=(-1, h),
            **kwargs,
        )
        self._theme = theme
        self._s = s
        self._entry = entry
        self._critical = critical
        self._svc = svc
        self._on_close = on_close
        self._hovered = False

        n = entry["n"]

        self._bg = Box(
            hexpand=True, size=(-1, h), style=f"border-radius: {max(5, int(7 * s))}px;"
        )

        if critical:
            self._bg.add(
                Box(
                    size=(max(1, int(2 * s)), int(h - 10 * s)),
                    style=f"background: {theme.verm}; border-radius: 999px;",
                    v_align="center",
                )
            )

        self._tile = NotifTile(theme, s=s, size=16, critical=critical)
        self._tile.set_margin_left(int(8 * s))
        self._tile.set_margin_right(int(8 * s))
        self._tile.set_v_align("center")
        self._bg.add(self._tile)
        self._tile.set_from_notif(n)

        text = (
            n.body
            if hasattr(n, "body") and n.body
            else (n.get("body", "") if isinstance(n, dict) else "")
        )
        if not text:
            text = n.summary if hasattr(n, "summary") else n.get("summary", "")
        self._text = Label(
            label=str(text or ""),
            style=(
                f"color: {theme.cream if critical else theme.subtle};"
                f"font-size: {max(10, int(10.5 * s))}px;"
                + ("font-weight: 600;" if critical else "font-weight: 500;")
            ),
            h_expand=True,
            h_align="start",
            v_align="center",
        )
        self._text.set_ellipsize(Pango.EllipsizeMode.END)
        self._bg.add(self._text)

        right = Box(spacing=int(6 * s), v_align="center")
        count = entry.get("count", 1)
        self._count_badge = Label(
            label=f"×{count}" if count > 1 else "",
            visible=count > 1,
            style=(
                f"color: {theme.verm_lit if critical else theme.verm_dim};"
                f"font-size: {max(8, int(9 * s))}px;"
                f"font-weight: 600;"
            ),
        )
        right.add(self._count_badge)

        self._age = Label(
            label=self._svc.age_label(n),
            style=f"color: {theme.faint}; font-size: {max(8, int(9 * s))}px;",
        )
        self._x = EventBox(events=Gdk.EventMask.BUTTON_PRESS_MASK)
        self._x_label = Image(
            icon_name="window-close-symbolic",
            icon_size=max(14, int(14 * s)),
            style=f"color: {theme.dim};",
        )
        self._x.add(self._x_label)
        self._x.set_margin_left(int(8 * s))
        self._x.set_margin_right(int(8 * s))
        self._x.connect(
            "button-press-event",
            lambda _w, _e: GLib.idle_add(self._svc.dismiss_entry, self._entry) or True,
        )
        self._x.set_no_show_all(True)
        self._x.set_visible(False)
        right.add(self._age)
        right.add(self._x)
        self._bg.add(right)

        self.add(self._bg)
        self.set_hexpand(True)
        self.connect("button-press-event", self._on_press)
        self.connect("enter-notify-event", self._on_enter)
        self.connect("leave-notify-event", self._on_leave)

    # --- callbacks ---------------------------------------------------------------

    def _on_hover_style(self):
        self._bg.set_style(
            f"border-radius: {max(5, int(7 * self._s))}px;"
            + (f"background: {self._theme.frame_bg};" if self._hovered else "")
        )
        self._age.set_visible(not self._hovered)
        self._x.set_visible(self._hovered)

    def _on_enter(self, _w, event):
        if event.detail == Gdk.NotifyType.INFERIOR:
            return False
        self._hovered = True
        self._on_hover_style()
        return False

    def _on_leave(self, _w, event):
        if event.detail == Gdk.NotifyType.INFERIOR:
            return False
        self._hovered = False
        self._on_hover_style()
        return False

    def _on_press(self, *_a):
        self._svc.activate_entry(self._entry)
        self._on_close()
        return True

    def refresh_age(self):
        self._age.set_label(self._svc.age_label(self._entry["n"]))


class GroupHead(EventBox):
    """App group header: tile, caps name, count, preview, chevron + dismiss-X."""

    def __init__(self, theme: Theme, s: float, group: dict, svc: Notifs, **kwargs):
        h = int(_GROUP_H * s)
        super().__init__(
            events=(
                Gdk.EventMask.BUTTON_PRESS_MASK
                | Gdk.EventMask.ENTER_NOTIFY_MASK
                | Gdk.EventMask.LEAVE_NOTIFY_MASK
            ),
            size=(-1, h),
            **kwargs,
        )
        self._theme = theme
        self._s = s
        self._group = group
        self._svc = svc
        self._hovered = False

        self._bg = Box(
            hexpand=True, size=(-1, h), style=f"border-radius: {max(6, int(8 * s))}px;"
        )

        self._tile = NotifTile(theme, s=s, size=20)
        self._tile.set_margin_left(int(6 * s))
        self._tile.set_margin_right(int(8 * s))
        self._tile.set_v_align("center")
        self._bg.add(self._tile)
        self._tile.set_from_notif(group["newest"])

        self._name = Label(
            label=_caps(group["app"]),
            style=(
                f"color: {theme.subtle};"
                f"font-size: {max(8, int(9 * s))}px;"
                f"font-weight: 600;"
                f"letter-spacing: 1.2px;"
            ),
            v_align="center",
            h_expand=False,
        )
        self._bg.add(self._name)

        self._count = Label(
            label=f"· {group['count']}",
            style=f"color: {theme.faint}; font-size: {max(8, int(9 * s))}px;",
            v_align="center",
        )
        self._name.set_margin_right(int(5 * s))
        self._bg.add(self._count)

        preview = group["preview"]
        ptext = (
            preview.body
            if hasattr(preview, "body") and preview.body
            else preview.summary
            if hasattr(preview, "summary")
            else preview.get("body", "")
            if isinstance(preview, dict)
            else ""
        )
        if not ptext and isinstance(preview, dict):
            ptext = preview.get("summary", "")
        self._preview = Label(
            label=str(ptext or ""),
            style=f"color: {theme.dim}; font-size: {max(9, int(10 * s))}px;",
            h_expand=True,
            h_align="start",
            v_align="center",
        )
        self._preview.set_ellipsize(Pango.EllipsizeMode.END)
        self._preview.set_margin_left(int(8 * s))
        self._bg.add(self._preview)

        expanded = svc.expanded(group["app"])

        self._x = EventBox(events=Gdk.EventMask.BUTTON_PRESS_MASK)
        self._x_label = Image(
            icon_name="window-close-symbolic",
            icon_size=max(14, int(14 * s)),
            style=f"color: {theme.dim};",
        )
        self._x.add(self._x_label)
        self._x.set_margin_right(int(8 * s))
        self._x.connect(
            "button-press-event",
            lambda _w, _e: (
                GLib.idle_add(self._svc.dismiss_app, self._group["app"]) or True
            ),
        )
        self._x.set_no_show_all(True)
        self._x.set_visible(False)
        self._bg.add(self._x)

        self._chev = Image(
            icon_name="pan-down-symbolic" if expanded else "pan-end-symbolic",
            icon_size=max(14, int(14 * s)),
            style=f"color: {theme.faint};",
            v_align="center",
        )
        self._chev.set_margin_right(int(7 * s))
        self._bg.add(self._chev)

        self.add(self._bg)
        self.set_hexpand(True)
        self.connect("button-press-event", self._on_press)
        self.connect("enter-notify-event", self._on_enter)
        self.connect("leave-notify-event", self._on_leave)

    def _on_hover_style(self):
        self._bg.set_style(
            f"border-radius: {max(6, int(8 * self._s))}px;"
            + (f"background: {self._theme.frame_bg};" if self._hovered else "")
        )
        self._x.set_visible(self._hovered)

    def _on_enter(self, _w, event):
        if event.detail == Gdk.NotifyType.INFERIOR:
            return False
        self._hovered = True
        self._on_hover_style()
        return False

    def _on_leave(self, _w, event):
        if event.detail == Gdk.NotifyType.INFERIOR:
            return False
        self._hovered = False
        self._on_hover_style()
        return False

    def _on_press(self, _w, event):
        self._svc.toggle_expanded(self._group["app"])
        return True

    def refresh_chev(self):
        self._chev.set_from_icon_name(
            "pan-down-symbolic"
            if self._svc.expanded(self._group["app"])
            else "pan-end-symbolic",
            max(14, int(14 * self._s)),
        )


class Link(Box):
    """The inbox surface hosted in the pill's surface face."""

    def __init__(
        self,
        theme: Theme,
        s: float = 1.0,
        on_close: Callable[[], None] | None = None,
        on_size_change: Callable[[], None] | None = None,
        **kwargs,
    ):
        super().__init__(
            name="link-surface",
            orientation="v",
            style=(
                f"padding-top: {int(_MT * s)}px;"
                f"padding-left: {int(_MLR * s)}px;"
                f"padding-right: {int(_MLR * s)}px;"
                f"padding-bottom: {int(_MB * s)}px;"
            ),
            **kwargs,
        )
        self._theme = theme
        self._s = s
        self._on_close = on_close
        self._on_size_change = on_size_change
        self._svc = notifs
        self._seen_source: int | None = None
        self._last_tick = notifs.tick
        self._age_rows: list[NotifRow] = []
        self._view_h = int(_SCROLL_MIN * s)
        self._total_h = int((_MT + _HEADER_H + _HAIR + _SCROLL_MIN + _MB) * s)

        self.set_can_focus(True)
        self.connect("key-press-event", self._on_key)

        # header: INBOX | (ember N NEW) (CLEAR)
        self._title = Label(
            label="INBOX",
            style=(
                f"color: {theme.subtle};"
                f"font-size: {max(9, int(10 * s))}px;"
                f"font-weight: 600;"
                f"letter-spacing: 1.6px;"
            ),
        )
        left = Box(spacing=int(8 * s))
        left.add(self._title)

        self._ember = Label(
            label="●",
            style=f"color: {theme.flame_glow}; font-size: {max(5, int(6 * s))}px;",
        )
        self._new_count = Label(
            label="",
            style=f"color: {theme.dim}; font-size: {max(9, int(9.5 * s))}px; font-weight: 600;",
        )
        self._unread = Box(spacing=int(6 * s))
        self._unread.add(self._ember)
        self._unread.add(self._new_count)
        self._unread.set_no_show_all(True)
        self._unread.set_visible(False)

        self._clear_text = Label(
            label="CLEAR",
            style=f"color: {theme.verm_dim}; font-size: {max(8, int(9 * s))}px; font-weight: 600; letter-spacing: 1.4px;",
        )
        self._clear = EventBox(
            events=Gdk.EventMask.BUTTON_PRESS_MASK,
            child=Box(spacing=int(4 * s)),
        )
        self._clear.get_child().add(self._clear_text)
        self._clear.connect("button-press-event", lambda *_a: self._svc.clear_all())

        right = Box(spacing=int(10 * s))
        right.add(self._unread)
        right.add(self._clear)
        self._clear.set_no_show_all(True)
        self._clear.set_visible(False)

        header = Box(
            v_align="center",
            hexpand=True,
            size=(-1, int(_HEADER_H * s)),
        )
        header.pack_start(left, False, False, 0)
        header.pack_end(right, False, False, 0)
        self.add(header)

        hairline = Box(
            size=(-1, _HAIR),
            hexpand=True,
            style=f"background: {theme.hair};",
        )
        self.add(hairline)

        # scroll of groups
        self._col = Box(orientation="v", spacing=int(6 * s), hexpand=True)
        self._scroll = ScrolledWindow(
            min_content_size=(int((_LINK_W - 2 * _MLR) * s), int(_SCROLL_MIN * s)),
            max_content_size=(int((_LINK_W - 2 * _MLR) * s), int(_SCROLL_MAX * s)),
            h_scrollbar_policy="never",
            v_scrollbar_policy="automatic",
            overlay_scroll=True,
            child=self._col,
            h_expand=True,
        )
        self.add(self._scroll)
        self._scroll.set_size_request(int((_LINK_W - 2 * _MLR) * s), self._view_h)

        # silence empty state
        self._silence_text = Label(
            label="No notifications to display",
            style=f"color: {theme.faint}; font-size: {max(8, int(9 * s))}px; font-weight: 600;",
            v_align="center",
            h_align="center",
        )
        self._silence = Box(
            orientation="v",
            spacing=int(4 * s),
            visible=False,
            v_expand=True,
            style=f"padding-top: {int(14 * s)}px; padding-bottom: {int(14 * s)}px;",
        )
        self._silence.add(self._silence_text)
        self.add(self._silence)

        self._svc.connect("changed", self._on_changed)

    # --- open/close ---------------------------------------------------------------

    def open(self):
        self._last_tick = self._svc.tick
        self._rebuild()
        self._schedule_seen()
        GLib.idle_add(self.grab_focus)

    def close(self):
        self._cancel_seen()
        if self._on_close:
            self._on_close()

    def surface_size(self):
        return int(_LINK_W * self._s), self._total_h

    # --- reactivity ---------------------------------------------------------------

    def _on_changed(self, *_a):
        if self._svc.tick != self._last_tick:
            self._last_tick = self._svc.tick
            for row in self._age_rows:
                row.refresh_age()
            return
        self._rebuild()

    def _rebuild(self):
        show_silence = self._svc.count <= 0
        self._silence.set_visible(show_silence)
        self._scroll.set_visible(not show_silence)

        self._new_count.set_label(f"{self._svc.unread} NEW")
        self._unread.set_visible(self._svc.unread > 0)
        self._clear.set_visible(self._svc.count > 0)

        for child in self._col.get_children():
            self._col.remove(child)
        self._age_rows.clear()

        for group in self._svc.groups:
            self._col.add(self._group_block(group))

        self._measure()

    def _group_block(self, group: dict) -> Box:
        block = Box(orientation="v", spacing=2 * self._s, hexpand=True)
        block.add(GroupHead(self._theme, self._s, group, self._svc))
        for entry in group["criticals"]:
            block.add(self._make_row(entry, True))
        if self._svc.expanded(group["app"]):
            for entry in group["entries"]:
                block.add(self._make_row(entry, False))
        return block

    def _make_row(self, entry: dict, critical: bool) -> NotifRow:
        row = NotifRow(
            self._theme,
            self._s,
            entry,
            critical,
            self._svc,
            on_close=self._close,
        )
        if not isinstance(entry["n"], dict):
            self._age_rows.append(row)
        return row

    def _close(self):
        if self._on_close:
            self._on_close()

    def _measure(self):
        nat = self._col.get_preferred_height()[1]
        if self._svc.count <= 0:
            nat = self._silence.get_preferred_height()[1]
        view = int(
            min(
                max(nat + 2 * self._s, int(_SCROLL_MIN * self._s)),
                int(_SCROLL_MAX * self._s),
            )
        )
        self._view_h = view
        self._scroll.set_size_request(int((_LINK_W - 2 * _MLR) * self._s), view)
        self._total_h = int((_MT + _HEADER_H + _HAIR + view + _MB) * self._s)
        self.set_size_request(int(_LINK_W * self._s), self._total_h)
        if self._on_size_change:
            self._on_size_change()

    # --- keyboard -----------------------------------------------------------------

    def _on_key(self, _w, event):
        if event.keyval in (Gdk.KEY_Escape,):
            self._close()
            return True
        return False

    # --- seen marking ----------------------------------------------------------------

    def _schedule_seen(self):
        self._cancel_seen()

        def mark():
            self._seen_source = None
            self._svc.mark_all_seen()
            return GLib.SOURCE_REMOVE

        self._seen_source = GLib.timeout_add(600, mark)

    def _cancel_seen(self):
        if self._seen_source is not None:
            GLib.source_remove(self._seen_source)
            self._seen_source = None

    # --- scale ----------------------------------------------------------------------

    def set_scale(self, s: float):
        self._s = s
        self._measure()
