"""Clipboard surface
Search field over the cliphist history snapshot. Typing filters by substring;
Return copies the selected entry and closes, hovering a row (from a genuinely
moved pointer) moves the selection, clicking copies, the ✕ dismiss deletes it.
Image entries render their cached thumbnail beside the label/size split.
Fixed 360x332·s, margins 15/17/17/14.
"""

import os
from pathlib import Path

from fabric.utils import Gdk, GdkPixbuf, GLib
from fabric.widgets.box import Box
from fabric.widgets.eventbox import EventBox
from fabric.widgets.image import Image
from fabric.widgets.label import Label
from fabric.widgets.scrolledwindow import ScrolledWindow
from gi.repository import Pango

from components.search_field import SearchField
from core.theme import Theme
from services.cliphist import cliphist
from utils.autopaste import trigger_autopaste

_CACHE_HOME = os.environ.get(
    "XDG_CACHE_HOME",
    str(Path.home() / ".cache"),
)


class ClipboardRow(EventBox):
    """One history row: thumb tile (image) + label + size tag + dismiss tail."""

    def __init__(
        self,
        theme: Theme,
        s: float,
        width: int,
        entry: dict,
        selected: bool,
        on_click,
        on_dismiss,
        on_select=None,
        on_right_click=None,
    ):
        super().__init__(
            events=(
                Gdk.EventMask.BUTTON_PRESS_MASK,
                Gdk.EventMask.ENTER_NOTIFY_MASK,
                Gdk.EventMask.LEAVE_NOTIFY_MASK,
            ),
            size=(width, (44 if entry["isImage"] else 38) * s),
        )
        self._theme = theme
        self._s = s
        self.entry = entry
        self.index = -1
        self._selected = selected
        self._hovered = False
        self._on_click = on_click
        self._on_dismiss = on_dismiss
        self._on_select = on_select
        self._on_right_click = on_right_click
        self._destroyed = False

        self._bg = Box(
            size=(width, (44 if entry["isImage"] else 38) * s),
            style=self._row_style(selected, False),
        )
        row = Box(
            v_align="center",
            h_expand=True,
            spacing=int(10 * s),
            style=f"padding-left: {int(11 * s)}px; padding-right: {int(11 * s)}px;",
        )

        # thumb tile
        self._thumb_box = Box(
            size=(int(52 * s), int(32 * s)) if entry["isImage"] else (0, 0),
            style=(
                f"background: {theme.tile_bg};"
                f"border: 1px solid {theme.border};"
                f"border-radius: {max(3, int(6 * s))}px;"
                f"margin-right: {int(9 * s)}px;"
                if entry["isImage"]
                else "margin-right: 0px;"
            ),
        )
        self._thumb_img = Image()
        if entry["isImage"]:
            self._thumb_box.add(self._thumb_img)
            self._load_thumb()
        row.add(self._thumb_box)

        preview = entry.get("preview") or ""
        self._text = Label(
            label=(
                entry.get("label")
                if entry["isImage"]
                else (preview[:250] + "..." if len(preview) > 250 else preview)
            ),
            h_align="start",
            h_expand=True,
            style=self._text_style(selected),
        )
        self._text.set_ellipsize(Pango.EllipsizeMode.END)
        row.add(self._text)

        self._size = Label(
            label=entry.get("sizeLabel") or "",
            h_align="end",
            visible=bool(entry.get("sizeLabel")),
            style=f"color: {theme.dim}; font-size: {max(9, int(10 * s))}px;",
        )
        row.add(self._size)

        self._ret = Label(
            label="↵",
            visible=selected,
            style=f"color: {theme.verm_lit}; font-size: {max(10, int(12 * s))}px;",
        )
        row.add(self._ret)

        self._dismiss = Label(
            label="✕",
            visible=False,
            style=f"color: {theme.dim}; font-size: {max(9, int(11 * s))}px;",
        )
        row.add(self._dismiss)

        self._bg.add(row)
        self.add(self._bg)

        self.connect("destroy", self._on_destroy)
        self.connect("button-press-event", self._on_press)
        self.connect("enter-notify-event", self._on_enter)
        self.connect("leave-notify-event", self._on_leave)

    def _on_destroy(self, *_a):
        self._destroyed = True

    # --- visuals ---------------------------------------------------------------

    def _row_style(self, selected: bool, hovered: bool) -> str:
        theme = self._theme
        radius = f"border-radius: {max(6, int(9 * self._s))}px;"
        if selected:
            return (
                f"background: {theme.frame_bg};"
                f"border: 1px solid {theme.frame_border};"
                f"{radius}"
            )
        if hovered:
            return f"background: rgba(240,224,214,0.03); {radius}"
        return radius

    def _text_style(self, selected: bool) -> str:
        theme = self._theme
        if self.entry["isImage"]:
            color = theme.dim if selected else theme.faint
        else:
            color = theme.cream if selected else theme.subtle
        weight = 600 if selected else 500
        return (
            f"color: {color}; font-size: {max(9, int(11.5 * self._s))}px;"
            f"font-weight: {weight};"
        )

    def _load_thumb(self):
        path = self.entry.get("thumb") or ""
        if not path or not os.path.exists(path):
            return
        try:
            pb = GdkPixbuf.Pixbuf.new_from_file(path)
        except GLib.Error:
            return
        out_w, out_h = int(52 * self._s), int(32 * self._s)
        w, h = pb.get_width(), pb.get_height()
        if w <= 0 or h <= 0:
            return
        scale = max(out_w / w, out_h / h)
        tw, th = max(1, int(w * scale)), max(1, int(h * scale))
        try:
            pb = pb.scale_simple(tw, th, GdkPixbuf.InterpType.BILINEAR)
            x = (tw - out_w) // 2
            y = (th - out_h) // 2
            pb = GdkPixbuf.Pixbuf.new_subpixbuf(pb, x, y, out_w, out_h)
        except GLib.Error:
            return
        self._thumb_img.set_from_pixbuf(pb)

    def set_selected(self, selected: bool):
        self._selected = selected
        self._bg.set_style(self._row_style(selected, self._hovered))
        self._text.set_style(self._text_style(selected))
        # show ↵ only when selected by keyboard (not hovering)
        self._ret.set_visible(selected and not self._hovered)
        self._dismiss.set_visible(self._hovered)

    # --- events ----------------------------------------------------------------

    def _on_press(self, _w, event):
        if self._destroyed:
            return True
        # Only handle events that originated on THIS widget, not children
        if event.window != self.get_window():
            return False
        if event.button == 3:
            if self._on_right_click:
                self._on_right_click(self)
        elif self._hovered and event.x >= (
            self.get_allocated_width() - int(24 * self._s)
        ):
            # click landed on the dismiss icon area
            if self._on_dismiss:
                self._on_dismiss(self)
        else:
            if self._on_click:
                self._on_click(self)
        return True

    def _on_enter(self, _w, event):
        # Ignore synthetic enter events from child widgets
        if event.detail in (Gdk.NotifyType.INFERIOR, Gdk.NotifyType.NONLINEAR_VIRTUAL):
            return False
        self._hovered = True
        self._ret.set_visible(False)
        self._dismiss.set_visible(True)
        self._bg.set_style(self._row_style(self._selected, True))
        if self._on_select is not None:
            self._on_select(self, event.x_root, event.y_root)
        return False

    def _on_leave(self, _w, event):
        # Ignore synthetic leave events when entering a child widget
        if event.detail in (Gdk.NotifyType.INFERIOR, Gdk.NotifyType.NONLINEAR_VIRTUAL):
            return False
        self._hovered = False
        self._dismiss.set_visible(False)
        self._ret.set_visible(self._selected)
        self._bg.set_style(self._row_style(self._selected, False))
        return False


class Clipboard(Box):
    """The pill's clipboard surface: search + filtered history rows."""

    def __init__(
        self, theme: Theme, s: float = 1.0, on_close=None, on_size_change=None
    ):
        super().__init__(
            name="clipboard-surface",
            orientation="v",
            size=(int(360 * s), -1),
            style=(
                f"padding-top: {int(15 * s)}px;"
                f"padding-left: {int(11 * s)}px;"
                f"padding-right: {int(11 * s)}px;"
                f"padding-bottom: {int(14 * s)}px;"
            ),
        )
        self._theme = theme
        self._s = s
        self._on_close = on_close
        self._on_size_change = on_size_change
        self._query = ""
        self._entries: list[dict] = cliphist.entries
        self._rows: list[ClipboardRow] = []
        self._selected = 0
        self._last_pointer = (-1, -1)
        self._rebuild_pending = False

        self.search = SearchField(theme, s=s, placeholder="Search clipboard")
        self.search.on_moved = self.move
        self.search.on_accepted = self.activate
        self.search.on_dismissed = self.close
        self.search.on_shift_delete = lambda: self._schedule_remove(self._selected)

        self._trash = EventBox(
            events=Gdk.EventMask.BUTTON_PRESS_MASK,
            child=Image(
                icon_name="user-trash-symbolic",
                icon_size=max(14, int(14 * s)),
                style=f"color: {theme.dim};",
            ),
        )
        self._trash.connect(
            "button-press-event", lambda *_a: self._clear_history() or True
        )
        self.search.set_action_widget(self._trash)

        self._list = Box(
            orientation="v",
            spacing=int(5 * s),
            h_expand=True,
            v_align="start",
            name="clipboard-list",
        )
        self._scroll = ScrolledWindow(
            min_content_size=(int(338 * s), -1),
            max_content_size=(int(338 * s), int(260 * s)),
            h_scrollbar_policy="never",
            v_scrollbar_policy="automatic",
            overlay_scroll=True,
            child=self._list,
            h_expand=True,
            v_expand=True,
        )

        self._empty = Label(
            label="",
            h_align="center",
            visible=False,
            style=(
                f"color: {theme.faint};"
                f"font-size: {max(9, int(10.5 * s))}px;"
                f"margin-top: {int(40 * s)}px;"
            ),
        )

        self.add(self.search)
        self.add(self._scroll)
        self.add(self._empty)

        self.search.entry.connect("changed", self._on_query_changed)
        cliphist.connect("changed", lambda *_a: self._on_cliphist_changed())

        self.show_all()

    # --- open/close -----------------------------------------------------------

    def open(self):
        self._query = ""
        self.search.text = ""
        self._selected = 0
        self._last_pointer = (-1, -1)
        self._entries = cliphist.entries
        self._rebuild()
        # kick a background refresh; when it lands _on_cliphist_changed rebuilds again
        cliphist.refresh()
        GLib.idle_add(self.search.focus)

    def close(self):
        if self._on_close:
            self._on_close()

    def _on_cliphist_changed(self):
        self._entries = cliphist.entries
        if self.get_visible():
            self._schedule_rebuild()

    # --- filtering -----------------------------------------------------------

    def _on_query_changed(self, *_a):
        self._query = self.search.text
        self._selected = 0
        if getattr(self, "_debounce_id", None):
            GLib.source_remove(self._debounce_id)
        self._debounce_id = GLib.timeout_add(50, self._do_query_changed)

    def _do_query_changed(self):
        self._debounce_id = None
        self._rebuild()
        return GLib.SOURCE_REMOVE

    def _results(self) -> list[dict]:
        q = self._query.strip().lower()
        if not q:
            return list(self._entries)[:50]
        out = []
        for entry in self._entries:
            hay = (
                f"{entry.get('label')} {entry.get('sizeLabel')}"
                if entry["isImage"]
                else entry.get("preview") or ""
            ).lower()
            if q in hay:
                out.append(entry)
                if len(out) >= 50:
                    break
        return out

    def _schedule_rebuild(self):
        """Defer rebuild to next idle tick — safe to call from event handlers."""
        if not self._rebuild_pending:
            self._rebuild_pending = True
            GLib.idle_add(self._idle_rebuild)

    def _idle_rebuild(self):
        self._rebuild_pending = False
        self._rebuild()
        return GLib.SOURCE_REMOVE

    def _rebuild(self):
        # Remove old rows from list widget and clear our tracking list
        for row in self._rows:
            self._list.remove(row)
            row.destroy()
        self._rows.clear()

        results = self._results()
        if not results:
            self._empty.set_label("No matches" if self._query else "History empty")
            self._empty.set_visible(True)
            self._scroll.set_visible(False)
        else:
            self._empty.set_visible(False)
            self._scroll.set_visible(True)

        inner_w = int(338 * self._s)
        for entry in results:
            row = ClipboardRow(
                self._theme,
                self._s,
                inner_w,
                entry,
                selected=False,
                on_click=self._on_row_click,
                on_dismiss=self._on_row_dismiss,
                on_select=self._on_row_select,
                on_right_click=self._on_row_dismiss,
            )
            row.index = len(self._rows)
            self._rows.append(row)
            self._list.add(row)

        self._list.show_all()
        self.search.set_counter(f"{len(results)} / {len(self._entries)}")
        self._sync_selected()
        if self._on_size_change:
            self._on_size_change()

    def surface_size(self):
        h = 73

        if self._empty.get_visible():
            h += 40

        if self._scroll.get_visible():
            list_h = 0
            for i, row in enumerate(self._rows):
                rh = 44 if row.entry.get("isImage") else 38
                list_h += rh
                if i > 0:
                    list_h += 5
            h += min(list_h, 260)

        return int(360 * self._s), int(h * self._s)

    def _sync_selected(self):
        for i, row in enumerate(self._rows):
            row.set_selected(i == self._selected)
        self._scroll_to()

    def _scroll_to(self):
        if not self._rows:
            return
        row = self._rows[min(self._selected, len(self._rows) - 1)]
        adj = self._scroll.get_vadjustment()
        alloc = row.get_allocation()
        page = adj.get_page_size()
        val = adj.get_value()
        if alloc.y < val:
            adj.set_value(alloc.y)
        elif alloc.y + alloc.height > val + page:
            adj.set_value(alloc.y + alloc.height - page)

    # --- actions ---------------------------------------------------------------

    def _clear_history(self):
        cliphist.wipe()
        self._schedule_rebuild()

    def move(self, delta: int):
        total = len(self._rows)
        if total == 0:
            return
        self._selected = max(0, min(total - 1, self._selected + delta))
        self._sync_selected()

    def activate(self):
        if not (0 <= self._selected < len(self._rows)):
            return
        row = self._rows[self._selected]
        cliphist.copy(row.entry)
        self.close()
        trigger_autopaste()

    def _schedule_remove(self, index: int):
        """Defer removal to next idle tick — safe from event handlers."""
        if 0 <= index < len(self._rows):
            entry = self._rows[index].entry
            GLib.idle_add(self._do_remove, entry, index)

    def _do_remove(self, entry: dict, index: int):
        cliphist.remove(entry)
        self._selected = max(0, min(self._selected, len(self._rows) - 2))
        # _rebuild will be triggered via cliphist.changed signal
        return GLib.SOURCE_REMOVE

    # --- row interactions -------------------------------------------------------

    def _on_row_click(self, row):
        if row not in self._rows:
            return
        self._selected = self._rows.index(row)
        cliphist.copy(row.entry)
        self.close()
        trigger_autopaste()

    def _on_row_dismiss(self, row):
        if row not in self._rows:
            return
        self._schedule_remove(self._rows.index(row))

    def _on_row_select(self, row, x_root: float, y_root: float):
        key = (int(x_root), int(y_root))
        if key == self._last_pointer:
            return
        self._last_pointer = key
        if row not in self._rows:
            return
        idx = self._rows.index(row)
        if idx != self._selected:
            self._selected = idx
            self._sync_selected()

    # --- scale ----------------------------------------------------------------

    def set_scale(self, s: float):
        self._s = s
