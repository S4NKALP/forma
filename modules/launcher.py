"""Launcher surface

Search field over a fuzzy-ranked application list. Desktop entries are ranked
by subsequence score + prior launch frequency (the usage file shared with the
standalone launcher); Enter/click launches the focused entry, the query can
double as a safe calculator (copy the result with Enter), and AppImage rows
right-click into a rename/delete editor. Fixed 360x332·s, margins 15/11/11/14.
"""

from fabric.utils import Gdk, GdkPixbuf, GLib, Gtk
from fabric.widgets.box import Box
from fabric.widgets.entry import Entry
from fabric.widgets.eventbox import EventBox
from fabric.widgets.image import Image
from fabric.widgets.label import Label
from fabric.widgets.scrolledwindow import ScrolledWindow
from gi.repository import Pango

from components.scrolling_label import ScrollingLabel
from components.search_field import SearchField
from core.theme import Theme
from services.desktop_entries import DesktopEntries, desktop_entries
from utils.caffeine import caffeine_entries
from utils.caffeine import matches as caffeine_matches
from utils.calc import evaluate
from utils.dnd import dnd_entry
from utils.dnd import matches as dnd_matches
from utils.fuzzy import rank

_CATEGORY_MAP = [
    ("TerminalEmulator", "Terminal"),
    ("WebBrowser", "Browser"),
    ("InstantMessaging", "Chat"),
    ("Audio", "Media"),
    ("AudioVideo", "Media"),
    ("Video", "Media"),
    ("Game", "Game"),
    ("Development", "Dev"),
    ("Graphics", "Graphics"),
    ("Office", "Office"),
    ("Settings", "System"),
    ("System", "System"),
    ("Utility", "Tool"),
    ("Network", "Net"),
]


def _map_category(raw: str) -> str:
    cats = set(str(raw).replace(",", ";").split(";"))
    for src, short in _CATEGORY_MAP:
        if src in cats:
            return short
    return ""


def _secondary(entry) -> str:
    if getattr(entry, "generic_name", None):
        return entry.generic_name
    if getattr(entry, "comment", None):
        return entry.comment
    if getattr(entry, "categories", None):
        cats = entry.categories
        if isinstance(cats, list):
            cats = ";".join(cats)
        return _map_category(cats)
    return ""


def _load_icon(entry, size: int):
    icon = entry.icon or ""
    if not icon:
        return None
    if icon.startswith("/"):
        try:
            return GdkPixbuf.Pixbuf.new_from_file_at_size(icon, size, size)
        except GLib.Error:
            return None
    info = Gtk.IconTheme.get_default().lookup_icon(icon, size, 0)
    if info is None:
        return None
    try:
        return info.load_icon()
    except GLib.Error:
        return None


class LauncherRow(EventBox):
    """One result row: icon + name/secondary + right ↵ or edit affordances."""

    def __init__(
        self,
        theme,
        s,
        entry,
        selected: bool,
        editing: bool,
        armed: bool,
        on_click,
        on_right_click,
        on_trash_click,
        on_rename,
        on_select=None,
    ):
        super().__init__(
            events=(
                Gdk.EventMask.BUTTON_PRESS_MASK,
                Gdk.EventMask.ENTER_NOTIFY_MASK,
                Gdk.EventMask.LEAVE_NOTIFY_MASK,
            ),
            size=(int(338 * s), int(38 * s)),
        )
        self._theme = theme
        self._s = s
        self.entry = entry
        self.armed = armed
        self._selected = selected
        self._editing = editing
        self._on_select = on_select

        self._bg = Box(
            size=(int(338 * s), int(38 * s)), style=self._row_style(selected, False)
        )
        row = Box(
            v_align="center",
            h_expand=True,
            spacing=int(10 * s),
            style=f"padding-left: {int(11 * s)}px; padding-right: {int(11 * s)}px;",
        )

        self._icon_holder = Box()
        self._icon_holder.set_size_request(int(22 * s), int(22 * s))
        self._icon_holder.add(self._build_icon(entry))
        row.add(self._icon_holder)

        self._name = Label(
            label=entry.name or "",
            h_align="start",
            h_expand=True,
            style=f"color: {theme.cream}; font-size: {max(10, int(13 * s))}px;"
            + ("font-weight: 600;" if selected else ""),
        )
        self._name.set_ellipsize(Pango.EllipsizeMode.END)
        self._sec = ScrollingLabel(
            label=_secondary(entry),
            visible=False,
            h_align="start",
            ellipsize="end",
            max_width=int(250 * s),
            style=f"color: {theme.faint}; font-size: {max(9, int(10 * s))}px;",
        )
        if _secondary(entry):
            self._sec.set_visible(True)
            if selected:
                self._sec.set_style(
                    f"color: {theme.dim}; font-size: {max(9, int(10 * s))}px;"
                )

        text_col = Box(
            orientation="v", spacing=int(1 * s), h_expand=True, v_align="center"
        )
        text_col.add(self._name)
        text_col.add(self._sec)
        row.add(text_col)

        self._ret = Label(
            label="↵",
            visible=selected and not editing,
            style=f"color: {theme.verm_lit}; font-size: {max(10, int(12 * s))}px;",
        )
        row.add(self._ret)

        self._trash_box = EventBox(
            events=Gdk.EventMask.BUTTON_PRESS_MASK,
            child=Image(
                icon_name="user-trash-symbolic",
                visible=editing,
                icon_size=max(12, int(12 * s)),
                style=f"color: {theme.verm_lit if armed else theme.dim};",
            ),
            visible=editing,
        )
        self._trash_box.connect("button-press-event", self._on_trash)
        row.add(self._trash_box)

        self._rename = Entry(
            text=entry.name or "",
            visible=editing,
            h_expand=False,
            h_align="start",
            width_request=int(120 * s),
            style=(
                f"color: {theme.cream}; background: transparent; border: 1px solid {theme.frame_border};"
                f"border-radius: {max(2, int(4 * s))}px; padding: {int(2 * s)}px {int(4 * s)}px;"
                f"font-size: {max(10, int(13 * s))}px;"
            ),
        )
        self._rename.connect(
            "activate", lambda *_a: on_rename(self, self._rename.get_text())
        )
        self._rename.connect(
            "focus-out-event", lambda *_a: on_rename(self, self._rename.get_text())
        )
        text_col.add(self._rename)
        if editing:
            self._name.set_visible(False)
        else:
            self._rename.set_visible(False)
        if editing:
            self._rename.grab_focus()
            self._rename.select_region(0, -1)

        self._bg.add(row)
        self.add(self._bg)

        self._hovered = False
        self.connect("button-press-event", self._on_press)
        self.connect("enter-notify-event", self._on_enter)
        self.connect("leave-notify-event", self._on_leave)

        self._on_click = on_click
        self._on_right_click = on_right_click
        self._on_trash_click = on_trash_click

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

    def _build_icon(self, entry):
        size = int(22 * self._s)
        pixbuf = _load_icon(entry, size)
        if pixbuf is None:
            from fabric.widgets.image import Image

            fallback = Gtk.IconTheme.get_default()
            icon_name = (
                "application-x-executable"
                if fallback.has_icon("application-x-executable")
                else "image-missing"
            )
            img = Image(icon_name=icon_name, icon_size=int(16 * self._s))
            img.set_size_request(size, size)
            return img
        from fabric.widgets.image import Image

        img = Image()
        img.set_from_pixbuf(pixbuf)
        if img.get_allocated_width() != size:
            img.set_size_request(size, size)
        return img

    def set_selected(self, selected: bool, editing: bool):
        became_edit = editing and not self._editing
        self._selected = selected
        self._editing = editing
        theme = self._theme
        self._name.set_style(
            f"color: {theme.cream}; font-size: {max(10, int(13 * self._s))}px;"
            + ("font-weight: 600;" if selected else "")
        )
        self._sec.set_style(
            f"color: {theme.dim if selected else theme.faint};"
            f"font-size: {max(9, int(10 * self._s))}px;"
        )
        self._ret.set_visible(selected and not editing)
        self._name.set_visible(not editing)
        self._rename.set_visible(editing)
        self._trash_box.set_visible(editing)
        if became_edit:
            self._rename.select_region(0, -1)
            self._rename.grab_focus()
        self._bg.set_style(self._row_style(selected, self._hovered))

    @property
    def index(self) -> int:
        parent = self.get_parent()
        return parent.get_children().index(self) if parent is not None else -1

    def _on_press(self, _w, event):
        if event.button == 3:
            self._on_right_click(self)
        else:
            self._on_click(self)
        return True

    def _on_enter(self, *_a):
        self._hovered = True
        self._bg.set_style(self._row_style(self._selected, True))
        if self._on_select is not None:
            self._on_select(self)
        return False

    def _on_leave(self, *_a):
        self._hovered = False
        self._bg.set_style(self._row_style(self._selected, False))
        return False

    def _on_trash(self, _w, _e):
        self._on_trash_click(self)
        return True


class Launcher(Box):
    """The pill's launcher surface: search + ranked list + calc row."""

    def __init__(
        self, theme: Theme, s: float = 1.0, on_close=None, on_size_change=None
    ):
        super().__init__(
            name="launcher-surface",
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
        self._usage: dict = {}
        self._query = ""
        self._results: list = []
        self._selected = 0
        self._edit_index = -1
        self._calc: dict = {}
        self._calc_copied = False
        self._rows: list = []

        self.search = SearchField(theme, s=s, placeholder="Search apps")
        self.search.on_moved = self.move
        self.search.on_accepted = self.activate
        self.search.on_shift_accepted = self.edit_selected
        self.search.on_shift_delete = self.delete_selected
        self.search.on_dismissed = self.close
        self.search.entry.connect("changed", self._on_query_changed)

        self._calc_row = EventBox(
            events=Gdk.EventMask.BUTTON_PRESS_MASK,
            size=(int(338 * s), int(44 * s)),
            style=(
                f"background: {theme.frame_bg};"
                f"border: 1px solid {theme.frame_border};"
                f"border-radius: {max(6, int(9 * s))}px;"
                f"margin-top: {int(6 * s)}px;"
            ),
        )
        self._calc_row.connect("button-press-event", lambda *_a: self._copy_calc())
        self._calc_result = Label(
            label="=",
            style=f"color: {theme.cream}; font-size: {max(11, int(15 * s))}px; font-weight: 600;",
        )
        self._calc_query = Label(
            label="",
            style=f"color: {theme.faint}; font-size: {max(9, int(10.5 * s))}px;",
        )
        self._calc_hint = Label(
            label="↵ copy",
            style=f"color: {theme.verm_lit}; font-size: {max(9, int(11 * s))}px;",
        )
        calc_text = Box(
            orientation="v", spacing=int(1 * s), h_expand=True, v_align="center"
        )
        calc_text.add(self._calc_result)
        calc_text.add(self._calc_query)
        calc_inner = Box(
            h_expand=True,
            v_expand=True,
            spacing=int(8 * s),
            style=f"padding-left: {int(12 * s)}px; padding-right: {int(12 * s)}px;",
        )
        calc_inner.add(calc_text)
        calc_inner.add(self._calc_hint)
        self._calc_row.add(calc_inner)

        self._list = Box(
            orientation="v",
            spacing=int(5 * s),
            h_expand=True,
            v_align="start",
            name="launcher-list",
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
        self._scroll.connect("button-press-event", self._on_list_press)

        self._hint = Box(
            h_align="center",
            spacing=int(5 * s),
            style=f"margin-top: {int(4 * s)}px;",
        )
        self._hint.add(
            Label(
                label="↓",
                style=f"color: {theme.faint}; font-size: {max(9, int(10.5 * s))}px;",
            )
        )
        self._hint.add(
            Label(
                label="Drag an AppImage onto the pill",
                style=f"color: {theme.faint}; font-size: {max(9, int(10.5 * s))}px;",
            )
        )

        self.add(self.search)
        self.add(self._calc_row)

        self._no_matches = Label(
            label="no matches",
            visible=False,
            v_align="center",
            h_align="center",
            style=f"color: {theme.faint}; font-size: {max(11, int(13 * s))}px;",
        )
        self.add(self._scroll)
        self.add(self._no_matches)
        self.add(self._hint)

        self._calc_row.set_no_show_all(True)
        self._calc_row.set_visible(False)

    # --- open/close -----------------------------------------------------------

    def open(self):
        self._usage = DesktopEntries.load_usage()
        self._query = ""
        self.search.text = ""
        self._selected = 0
        self._edit_index = -1
        self._calc = {}
        self._calc_copied = False
        self._rebuild()
        self.search.focus()

    def close(self):
        if self._on_close:
            self._on_close()

    def _trigger_close(self):
        if self._on_close:
            self._on_close()

    # --- search / rank ----------------------------------------------------------

    def _on_query_changed(self, *_a):
        self._query = self.search.text
        self._selected = 0
        self._edit_index = -1
        if getattr(self, "_debounce_id", None):
            GLib.source_remove(self._debounce_id)
        self._debounce_id = GLib.timeout_add(50, self._do_query_changed)

    def _do_query_changed(self):
        self._debounce_id = None
        res = evaluate(self._query)
        self._calc = res if res["ok"] else {}
        self._calc_copied = False
        self._rebuild()
        return GLib.SOURCE_REMOVE

    def _ranked(self):
        res = rank(desktop_entries.applications, self._query, self._usage)
        if not self._query:
            res = [e for e in res if self._usage.get(e.id, 0) > 0]
        # caffeine / dnd virtual rows float above app results for their queries
        if caffeine_matches(self._query):
            res = list(caffeine_entries(self._query)) + res
        if dnd_matches(self._query):
            row = dnd_entry(self._query)
            if row is not None:
                res = [row] + res
        return res[:50]

    def _rebuild(self):
        self._calc_result.set_label("= " + (self._calc.get("display", "") or ""))
        self._calc_query.set_label(self._query)
        self._calc_hint.set_label("copied" if self._calc_copied else "↵ copy")
        self._calc_hint.set_style(
            f"color: {self._theme.dim if self._calc_copied else self._theme.verm_lit};"
            f"font-size: {max(9, int(11 * self._s))}px;"
        )
        self._calc_row.set_visible(bool(self._calc))

        for row in self._rows:
            self._list.remove(row)
        self._rows.clear()

        self._results = self._ranked()
        for entry in self._results:
            row = LauncherRow(
                self._theme,
                self._s,
                entry,
                selected=False,
                editing=False,
                armed=False,
                on_click=self._on_row_click,
                on_right_click=self._on_row_right_click,
                on_trash_click=self._on_trash_click,
                on_rename=self._on_row_rename,
                on_select=self._on_row_select,
            )
            self._rows.append(row)
            self._list.add(row)

        self.search.set_counter(
            f"{len(self._results)} / {len(desktop_entries.applications)}"
        )
        self._hint.set_visible(not self._query)
        has_results = bool(self._results)
        self._scroll.set_visible(has_results)
        self._no_matches.set_visible(
            bool(self._query) and not has_results and not self._calc
        )
        self._sync_selected()

        if self._on_size_change:
            self._on_size_change()

    def surface_size(self):
        h = 73

        if self._calc_row.get_visible():
            h += 44 + 6  # row size + margin

        if self._no_matches.get_visible():
            h += 30

        if self._hint.get_visible():
            h += 38

        if self._scroll.get_visible():
            num_rows = len(self._rows)
            # Each row is 38px tall, spacing is 5px
            list_h = num_rows * 38 + max(0, num_rows - 1) * 5
            # Cap the list height at the max_content_size (260 for scroll window)
            h += min(list_h, 260)

        return int(360 * self._s), int(h * self._s)

    def _sync_selected(self):
        for i, row in enumerate(self._rows):
            editing = self._edit_index == i and bool(row.entry.appimage_slug)
            row.set_selected(i == self._selected, editing)
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

    # --- actions ----------------------------------------------------------------

    def move(self, delta: int):
        total = len(self._results)
        if total == 0:
            return
        self._selected = max(0, min(total - 1, self._selected + delta))
        self._sync_selected()

    def activate(self):
        if self._calc:
            self._copy_calc()
            return
        total = len(self._results)
        if total == 0 or not 0 <= self._selected < total:
            return
        entry = self._results[self._selected]
        if entry.id:
            desktop_entries.bump_usage(entry)
        entry.execute()
        self._trigger_close()

    def edit_selected(self):
        if not (0 <= self._selected < len(self._rows)):
            return
        row = self._rows[self._selected]
        self._on_row_right_click(row)

    def delete_selected(self):
        if not (0 <= self._selected < len(self._rows)):
            return
        row = self._rows[self._selected]
        if not row.entry.appimage_slug:
            return

        # Bypass armed state for immediate keyboard deletion
        slug = row.entry.appimage_slug
        if slug:
            self._appimage_remove(slug)
            desktop_entries.refresh()
        self._edit_index = -1
        self._rebuild()

    def _copy_calc(self):
        display = self._calc.get("display", "")
        if not display:
            return
        try:
            GLib.spawn_async(["sh", "-c", "printf '%s' \"$1\" | wl-copy", "_", display])
        except GLib.Error:
            pass
        self._calc_copied = True
        self._calc_hint.set_label("copied")
        self._calc_hint.set_style(
            f"color: {self._theme.dim}; font-size: {max(9, int(11 * self._s))}px;"
        )

    # --- row interactions -------------------------------------------------------

    def _on_row_click(self, row):
        if self._edit_index >= 0:
            return
        idx = self._rows.index(row)
        self._selected = idx
        if idx >= len(self._results):
            return
        entry = self._results[idx]
        if entry.id:
            desktop_entries.bump_usage(entry)
        entry.execute()
        self._trigger_close()

    def _on_row_select(self, row):
        idx = self._rows.index(row)
        if idx == self._selected:
            return
        self._selected = idx
        self._sync_selected()

    def _on_row_right_click(self, row):
        entry = row.entry
        if not entry.appimage_slug:
            return
        idx = self._rows.index(row)
        self._selected = idx
        self._edit_index = -1 if self._edit_index == idx else idx
        self._sync_selected()

    def _on_trash_click(self, row):
        idx = self._rows.index(row)
        if self._edit_index != idx:
            return
        if not row.armed:
            row.armed = True
            row._trash_box.get_child().set_style(
                f"color: {self._theme.verm_lit}; font-size: {max(9, int(11 * self._s))}px;"
            )
            return
        slug = row.entry.appimage_slug
        if slug:
            self._appimage_remove(slug)
            desktop_entries.refresh()
        self._edit_index = -1
        self._rebuild()

    def _on_row_rename(self, row, new_name: str):
        if self._edit_index < 0:
            return
        idx = self._rows.index(row)
        if self._edit_index != idx or not row.entry.appimage_slug:
            return
        slug = row.entry.appimage_slug
        name = (new_name or "").strip()
        if slug and name and name != row.entry.name:
            self._appimage_rename(slug, name)
            desktop_entries.refresh()
        self._edit_index = -1
        self._rebuild()

    def _appimage_remove(self, slug: str):
        from loguru import logger

        from ..services.appimage_installer import appimage_service

        try:
            appimage_service.remove(slug)
        except Exception as e:
            logger.warning(f"appimage remove {slug}: {e}")

    def _appimage_rename(self, slug: str, name: str):
        from loguru import logger

        from ..services.appimage_installer import appimage_service

        try:
            appimage_service.rename(slug, name)
        except Exception as e:
            logger.warning(f"appimage rename {slug}: {e}")

    def _on_list_press(self, _w, _e):
        self._trigger_close()
        return True

    # --- scale ----------------------------------------------------------------

    def set_scale(self, s: float):
        self._s = s
