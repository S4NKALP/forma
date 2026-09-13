from fabric.utils import Gdk, GLib
from fabric.widgets.box import Box
from fabric.widgets.eventbox import EventBox
from fabric.widgets.label import Label
from fabric.widgets.scrolledwindow import ScrolledWindow
from gi.repository import Pango

from components.search_field import SearchField
from core.theme import Theme
from services.emoji import emoji_service
from utils.autopaste import trigger_autopaste
from utils.fuzzy import rank


class EmojiRow(EventBox):
    def __init__(
        self,
        theme: Theme,
        s: float,
        width: int,
        entry,
        selected: bool,
        on_click,
        on_select=None,
    ):
        super().__init__(
            events=(
                Gdk.EventMask.BUTTON_PRESS_MASK,
                Gdk.EventMask.ENTER_NOTIFY_MASK,
                Gdk.EventMask.LEAVE_NOTIFY_MASK,
            ),
            size=(width, int(38 * s)),
        )
        self._theme = theme
        self._s = s
        self.entry = entry
        self.index = -1
        self._selected = selected
        self._hovered = False
        self._on_click = on_click
        self._on_select = on_select
        self._destroyed = False

        self._bg = Box(
            size=(width, int(38 * s)),
            style=self._row_style(selected, False),
        )
        row = Box(
            v_align="center",
            h_expand=True,
            spacing=int(10 * s),
            style=f"padding-left: {int(11 * s)}px; padding-right: {int(11 * s)}px;",
        )

        self._emoji = Label(
            label=entry.char,
            style=f"font-size: {max(16, int(18 * s))}px;",
        )
        row.add(self._emoji)

        self._name = Label(
            label=entry.name,
            h_align="start",
            h_expand=True,
            style=f"color: {theme.cream if selected else theme.subtle}; font-size: {max(10, int(12 * s))}px; font-weight: {600 if selected else 500};",
        )
        self._name.set_ellipsize(Pango.EllipsizeMode.END)
        row.add(self._name)

        self._ret = Label(
            label="↵",
            visible=selected,
            style=f"color: {theme.verm_lit}; font-size: {max(10, int(12 * s))}px;",
        )
        row.add(self._ret)

        self._bg.add(row)
        self.add(self._bg)

        self.connect("destroy", self._on_destroy)
        self.connect("button-press-event", self._on_press)
        self.connect("enter-notify-event", self._on_enter)
        self.connect("leave-notify-event", self._on_leave)

    def _on_destroy(self, *_a):
        self._destroyed = True

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

    def set_selected(self, selected: bool):
        self._selected = selected
        self._bg.set_style(self._row_style(selected, self._hovered))
        self._name.set_style(
            f"color: {self._theme.cream if selected else self._theme.subtle}; font-size: {max(10, int(12 * self._s))}px; font-weight: {600 if selected else 500};"
        )
        self._ret.set_visible(selected and not self._hovered)

    def _on_press(self, _w, event):
        if self._destroyed:
            return True
        if event.window != self.get_window():
            return False
        if self._on_click:
            self._on_click(self)
        return True

    def _on_enter(self, _w, event):
        if event.detail in (Gdk.NotifyType.INFERIOR, Gdk.NotifyType.NONLINEAR_VIRTUAL):
            return False
        self._hovered = True
        self._ret.set_visible(False)
        self._bg.set_style(self._row_style(self._selected, True))
        if self._on_select is not None:
            self._on_select(self, event.x_root, event.y_root)
        return False

    def _on_leave(self, _w, event):
        if event.detail in (Gdk.NotifyType.INFERIOR, Gdk.NotifyType.NONLINEAR_VIRTUAL):
            return False
        self._hovered = False
        self._ret.set_visible(self._selected)
        self._bg.set_style(self._row_style(self._selected, False))
        return False


class EmojiPicker(Box):
    def __init__(
        self, theme: Theme, s: float = 1.0, on_close=None, on_size_change=None
    ):
        super().__init__(
            name="emoji-surface",
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
        self._rows = []
        self._selected = 0
        self._last_pointer = (-1, -1)
        self._rebuild_pending = False

        self.search = SearchField(theme, s=s, placeholder="Search emoji")
        self.search.on_moved = self.move
        self.search.on_accepted = self.activate
        self.search.on_dismissed = self.close

        self._list = Box(
            orientation="v",
            spacing=int(5 * s),
            h_expand=True,
            v_align="start",
            name="emoji-list",
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
            label="Loading emojis...",
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
        emoji_service.connect("ready", lambda *_a: self._schedule_rebuild())

        self.show_all()

    def open(self):
        self._query = ""
        self.search.text = ""
        self._selected = 0
        self._last_pointer = (-1, -1)
        self._rebuild()
        GLib.idle_add(self.search.focus)

    def close(self):
        if self._on_close:
            self._on_close()

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

    def _results(self):
        res = rank(emoji_service.emojis, self._query, emoji_service.usage)
        if not self._query:
            res = [e for e in res if emoji_service.usage.get(e.char, 0) > 0]
        return res[:50]

    def _schedule_rebuild(self):
        if not self._rebuild_pending:
            self._rebuild_pending = True
            GLib.idle_add(self._idle_rebuild)

    def _idle_rebuild(self):
        self._rebuild_pending = False
        self._rebuild()
        return GLib.SOURCE_REMOVE

    def _rebuild(self):
        for row in self._rows:
            self._list.remove(row)
            row.destroy()
        self._rows.clear()

        if not emoji_service.emojis:
            self._empty.set_label("Loading emojis...")
            self._empty.set_visible(True)
            self._scroll.set_visible(False)
            return

        results = self._results()
        if not results:
            self._empty.set_label("No matches")
            self._empty.set_visible(bool(self._query))
            self._scroll.set_visible(False)
        else:
            self._empty.set_visible(False)
            self._scroll.set_visible(True)

        inner_w = int(338 * self._s)
        for entry in results:
            row = EmojiRow(
                self._theme,
                self._s,
                inner_w,
                entry,
                selected=False,
                on_click=self._on_row_click,
                on_select=self._on_row_select,
            )
            row.index = len(self._rows)
            self._rows.append(row)
            self._list.add(row)

        self._list.show_all()
        self.search.set_counter(f"{len(results)} / {len(emoji_service.emojis)}")
        self._sync_selected()

        if self._on_size_change:
            self._on_size_change()

    def surface_size(self):
        h = 73

        if self._empty.get_visible():
            h += 40

        if self._scroll.get_visible():
            num_rows = len(self._rows)
            list_h = num_rows * 38 + max(0, num_rows - 1) * 5
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
        self._copy_and_paste(row.entry)

    def _on_row_click(self, row):
        if row not in self._rows:
            return
        self._selected = self._rows.index(row)
        self._copy_and_paste(row.entry)

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

    def _copy_and_paste(self, entry):
        import subprocess

        # Copy to clipboard
        try:
            subprocess.run(
                ["wl-copy"], input=entry.char.encode("utf-8"), stderr=subprocess.DEVNULL
            )
            emoji_service.record_usage(entry)
        except Exception:
            pass
        self.close()
        trigger_autopaste()

    def set_scale(self, s: float):
        self._s = s
