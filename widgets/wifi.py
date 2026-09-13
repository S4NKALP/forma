"""Wifi surface
WIFI surface: enable LinkToggle and the live network list sorted by signal
D-Bus service (network.py): clicking a secured unknown network expands an inline
password row that connects with the secret passed over D-Bus (never argv).
Connected or saved networks expand an inline confirm row (disconnect/connect,
show/hide password, forget). Standalone root surface, so Escape and the backdrop
dismiss it like every other surface.

Sizes: width 272·s, height = content + 26·s (13/16/16/13 margins).
"""

import math
import threading
from collections.abc import Callable

from fabric.utils import Gdk, GLib, logger
from fabric.widgets.box import Box
from fabric.widgets.entry import Entry
from fabric.widgets.eventbox import EventBox
from fabric.widgets.label import Label
from fabric.widgets.overlay import Overlay
from fabric.widgets.scrolledwindow import ScrolledWindow
from gi.repository import Pango

from components.glyph_icon import GlyphIcon
from components.link_toggle import LinkToggle
from components.wifi_glyph import WifiGlyph
from core.theme import Theme
from services.network import NetworkClient
from utils.command import run_command

_W = 272
_MT = 13
_MLR = 16
_MB = 13

_HDR = 24
_ROW = 30
_ROW_R = 9
_SUB = 30
_REVEAL = 24
_DETAIL_H = 14
_FAIL_H = 13
_SPACING = 2
_DIV = 9
_DIV_GAP = 8
_LIST_MAX = 280
_LIST_MIN = 26
_SCAN_MS = 10000
_CONN_SPIN_MS = 10000

_SEARCH_MS = 60
_DETAIL_DEBOUNCE_MS = 500


def _rgba(hex_color: str, alpha: float) -> str:
    r = int(hex_color[1:3], 16)
    g = int(hex_color[3:5], 16)
    b = int(hex_color[5:7], 16)
    return f"rgba({r},{g},{b},{alpha})"


def _strip_error(msg: str) -> str:
    msg = msg.strip()
    if msg.startswith("Error:"):
        msg = msg[len("Error:") :].strip()
    return msg.rstrip(".")


class _PulseDot(Box):
    """4x4 flameGlow dot that pulses 0.35→1→0.35 while visible (connecting)."""

    def __init__(self, theme: Theme, s: float):
        super().__init__(
            size=(int(4 * s), int(4 * s)),
            style=f"background: {theme.flame_glow}; border-radius: 999px;",
        )
        self._theme = theme
        self._s = s
        self._phase = 0.0
        self._timer = None
        self.set_opacity(0.35)
        self.set_no_show_all(True)
        self.set_visible(False)

    def set_running(self, running: bool):
        if running:
            if self._timer is not None:
                return
            self.set_opacity(0.35)
            self._phase = 0.0
            self.set_visible(True)
            self._timer = GLib.timeout_add(16, self._tick)
        else:
            if self._timer is not None:
                GLib.source_remove(self._timer)
                self._timer = None
            self.set_visible(False)

    def _tick(self):
        self._phase += 0.04
        if self._phase >= 1.0:
            self._phase = 0.0
        eased = 0.5 - 0.5 * math.cos(math.pi * self._phase)
        self.set_opacity(0.35 + 0.65 * eased)
        return True

    def destroy(self):
        if self._timer is not None:
            GLib.source_remove(self._timer)
            self._timer = None
        super().destroy()


class _RowButton(EventBox):
    """22·s tall mini pill button used in the confirm/password rows."""

    def __init__(
        self,
        theme: Theme,
        s: float,
        label: str,
        on_click: Callable[[], None],
        danger: bool = False,
    ):
        self._theme = theme
        self._s = s
        self._danger = danger
        self._on_click = on_click
        super().__init__(
            events=(
                Gdk.EventMask.BUTTON_PRESS_MASK
                | Gdk.EventMask.ENTER_NOTIFY_MASK
                | Gdk.EventMask.LEAVE_NOTIFY_MASK
            ),
            style=self._style(False),
        )
        self._label = Label(
            label=label,
            h_align="center",
            v_align="center",
            style=(
                f"color: {theme.verm_lit if danger else theme.cream};"
                f"font-size: {max(9, int(10 * s))}px;"
                f"font-weight: 600;"
                f"letter-spacing: 0.3px;"
            ),
        )
        body = Box(
            h_align="center",
            v_align="center",
            style=f"padding-left: {int(8 * s)}px; padding-right: {int(8 * s)}px;",
        )
        body.add(self._label)
        self.add(body)
        self.connect("button-press-event", self._on_press)
        self.connect("enter-notify-event", self._hover(True))
        self.connect("leave-notify-event", self._hover(False))

    def _style(self, hover: bool) -> str:
        theme = self._theme
        s = self._s
        if self._danger:
            return (
                f"background: {_rgba(theme.verm, 0.2) if hover else _rgba(theme.verm, 0.12)};"
                f"border: 1px solid {_rgba(theme.verm_lit, 0.45)};"
                f"border-radius: {max(4, int(7 * s))}px;"
            )
        return (
            f"background: {theme.tile_bg if hover else 'transparent'};"
            f"border: 1px solid {theme.verm_dim if hover else theme.border};"
            f"border-radius: {max(4, int(7 * s))}px;"
        )

    def _hover(self, hover: bool):
        def _cb(*_a):
            self.set_style(self._style(hover))
            return True

        return _cb

    def set_label(self, label: str):
        self._label.set_label(label)

    def get_label(self) -> str:
        return self._label.get_text()

    def set_visible(self, visible: bool):
        super().set_visible(visible)
        self.set_no_show_all(not visible)

    def _on_press(self, _w, ev):
        if ev.button != 1:
            return True
        self._on_click()
        return True


class _WifiRow(Box):
    """One network row + its expanded sub-rows (confirm/reveal/password/fail)."""

    def __init__(self, surface: WifiSurface, ap):
        self._surface = surface
        self._theme = surface._theme
        self._s = surface._s
        theme = surface._theme
        s = surface._s
        self.ssid = ap.ssid
        self.ap = ap
        self._hovered = False
        self._w = int((_W - 2 * _MLR) * s)
        super().__init__(orientation="v", spacing=int(_SPACING * s), h_expand=True)

        # main row ------------------------------------------------------------
        self._bg = Box(size=(self._w, int(_ROW * s)), style=self._row_style())
        self._main = EventBox(
            events=(
                Gdk.EventMask.BUTTON_PRESS_MASK
                | Gdk.EventMask.ENTER_NOTIFY_MASK
                | Gdk.EventMask.LEAVE_NOTIFY_MASK
            ),
            size=(self._w, int(_ROW * s)),
        )
        self._name = Label(
            label=self.ssid or "Hidden",
            h_align="start",
            h_expand=True,
            style=self._name_style(),
        )
        self._name.set_ellipsize(Pango.EllipsizeMode.END)
        self._dot = _PulseDot(theme, s)
        self._lock = GlyphIcon(
            name="lock-outline",
            color=theme.icon_dim,
            stroke=1.9,
            size=int(14 * s),
        )
        self._lock.set_no_show_all(True)
        self._lock.set_visible(False)
        self._glyph = WifiGlyph(
            level=(ap.strength or 0) / 100.0,
            on=True,
            stroke=1.7,
            s=s,
            color=theme.icon_dim,
        )
        right = Box(h_align="end", v_align="center", spacing=int(7 * s))
        right.add(self._dot)
        right.add(self._lock)
        right.add(self._glyph)
        content = Box(
            h_expand=True,
            v_align="center",
            style=(f"padding-left: {int(10 * s)}px;padding-right: {int(10 * s)}px;"),
        )
        content.add(self._name)
        content.add(right)
        self._bg.add(content)
        self._main.add(self._bg)
        self.add(self._main)
        self._main.connect("button-press-event", self._row_press)
        self._main.connect("enter-notify-event", self._row_hover(True))
        self._main.connect("leave-notify-event", self._row_hover(False))

        # connected detail line ----------------------------------------------
        self._detail = Label(
            label="",
            h_align="start",
            style=(
                f"color: {theme.faint};"
                f"font-size: {max(8, int(9.5 * s))}px;"
                f"font-weight: 500;"
                f"padding-left: {int(10 * s)}px;"
                f"padding-bottom: {int(2 * s)}px;"
            ),
        )
        self._detail.set_no_show_all(True)
        self._detail.set_visible(False)
        self.add(self._detail)

        # confirm row (connected / saved) --------------------------------------
        self._confirm = Box(
            orientation="h",
            h_expand=True,
            size=(self._w, int(_SUB * s)),
            v_align="center",
            style=f"padding-left: {int(10 * s)}px; padding-right: {int(10 * s)}px;",
        )
        self._confirm_hint = Label(
            label="",
            h_align="start",
            h_expand=True,
            style=(
                f"color: {theme.faint};"
                f"font-size: {max(8, int(9.5 * s))}px;"
                f"font-weight: 500;"
            ),
        )
        self._confirm_hint.set_ellipsize(Pango.EllipsizeMode.END)
        self._confirm_hint.set_max_width_chars(20)
        self._btns = Box(h_align="end", v_align="center", spacing=int(6 * s))
        self._primary_btn = _RowButton(theme, s, "Connect", self._on_connect_press)
        self._reveal_btn = _RowButton(theme, s, "Show", self._on_reveal_press)
        self._forget_btn = _RowButton(
            theme, s, "Forget", self._on_forget_press, danger=True
        )
        self._btns.add(self._primary_btn)
        self._btns.add(self._reveal_btn)
        self._btns.add(self._forget_btn)
        self._confirm.add(self._confirm_hint)
        self._confirm.add(self._btns)
        self._confirm.set_no_show_all(True)
        self._confirm.set_visible(False)
        self.add(self._confirm)

        # reveal row (saved password) ------------------------------------------
        self._reveal = Box(
            orientation="h",
            h_expand=True,
            v_align="center",
            size=(self._w, int(_REVEAL * s)),
            style=(f"padding-left: {int(10 * s)}px;padding-right: {int(10 * s)}px;"),
        )
        self._reveal_cap = Label(
            label="PASSWORD",
            style=(
                f"color: {theme.faint};"
                f"font-size: {max(8, int(9 * s))}px;"
                f"font-weight: 500;"
            ),
        )
        self._reveal_cap.set_ellipsize(Pango.EllipsizeMode.END)
        self._reveal_cap.set_max_width_chars(8)
        self._reveal_note = Label(
            label="no saved password",
            h_align="end",
            h_expand=True,
            style=(
                f"color: {theme.faint};"
                f"font-size: {max(8, int(10 * s))}px;"
                f"font-weight: 500;"
            ),
        )
        self._reveal_note.set_ellipsize(Pango.EllipsizeMode.END)
        self._reveal_note.set_max_width_chars(20)
        self._reveal_pw = Label(
            label="",
            h_align="end",
            style=(
                f"color: {theme.flame_core};"
                f"font-size: {max(9, int(11.5 * s))}px;"
                f"font-weight: 500;"
            ),
        )
        self._reveal_pw.set_ellipsize(Pango.EllipsizeMode.END)
        self._reveal_pw.set_max_width_chars(18)
        # expanding wrappers keep the labels' natural width out of the row's
        # request, so a long revealed password can never widen the fixed body
        self._reveal_note_wrap = Box(h_expand=True, h_align="end")
        self._reveal_note_wrap.add(self._reveal_note)
        self._reveal_pw_wrap = Box(h_expand=True, h_align="end")
        self._reveal_pw_wrap.add(self._reveal_pw)
        self._reveal.add(self._reveal_cap)
        self._reveal.add(self._reveal_note_wrap)
        self._reveal.add(self._reveal_pw_wrap)
        self._reveal.set_no_show_all(True)
        self._reveal.set_visible(False)
        self.add(self._reveal)

        # password row ----------------------------------------------------------
        self._pw = Box(
            orientation="h",
            h_expand=True,
            v_align="center",
            size=(self._w, int(_SUB * s)),
            style=(f"padding-left: {int(10 * s)}px;padding-right: {int(10 * s)}px;"),
        )
        self._pw_entry = Entry(
            h_expand=True,
            password=True,
            style=(
                f"color: {theme.cream};"
                f"background: transparent;"
                f"border: none;"
                f"box-shadow: none;"
                f"padding: 0;"
                f"font-size: {max(9, int(11.5 * s))}px;"
                f"caret-color: {theme.verm_lit};"
            ),
        )
        self._pw_placeholder = Label(
            label="Password",
            h_align="start",
            style=(f"color: {theme.faint};font-size: {max(9, int(11.5 * s))}px;"),
        )
        self._pw_placeholder.set_no_show_all(True)
        self._pw_entry_overlay = Overlay(child=self._pw_entry, h_expand=True)
        self._pw_entry_overlay.add_overlay(self._pw_placeholder)
        self._pw_entry_overlay.set_overlay_pass_through(self._pw_placeholder, True)
        self._pw_entry.connect("changed", self._on_pw_entry_changed)
        self._pw_entry.connect("activate", self._on_pw_accept)
        self._pw_entry_overlay.show_all()

        self._pw_return_glyph = GlyphIcon(
            name="return", color=theme.verm_lit, stroke=1.8, size=int(14 * s)
        )
        self._pw_return = EventBox(
            events=(
                Gdk.EventMask.BUTTON_PRESS_MASK
                | Gdk.EventMask.ENTER_NOTIFY_MASK
                | Gdk.EventMask.LEAVE_NOTIFY_MASK
            ),
        )
        self._pw_return.add(self._pw_return_glyph)
        self._pw_return.connect("button-press-event", self._on_pw_return_press)
        self._pw_return.connect("enter-notify-event", self._pw_return_hover(True))
        self._pw_return.connect("leave-notify-event", self._pw_return_hover(False))
        pw_right = Box(h_align="end", v_align="center", spacing=int(7 * s))
        self._dot_pw = _PulseDot(theme, s)
        pw_right.add(self._dot_pw)
        pw_right.add(self._pw_return)
        self._pw.add(self._pw_entry_overlay)
        self._pw.add(pw_right)
        self._pw.set_no_show_all(True)
        self._pw.set_visible(False)
        self.add(self._pw)

        # failure line -----------------------------------------------------------
        self._fail = Label(
            label="",
            h_align="start",
            style=(f"color: {theme.verm_lit};font-size: {max(8, int(9.5 * s))}px;"),
        )
        self._fail.set_no_show_all(True)
        self._fail.set_visible(False)
        self.add(self._fail)

    # --- properties -------------------------------------------------------------

    @property
    def is_active(self) -> bool:
        return bool(self.ap.is_active)

    @property
    def known(self) -> bool:
        return self._surface._is_known(self.ssid)

    @property
    def secured(self) -> bool:
        return bool(self.ap.requires_password)

    def expanded(self) -> bool:
        return self._surface._expanded_ssid == self.ssid

    def confirming(self) -> bool:
        return self.expanded() and (self.is_active or self.known)

    def asking(self) -> bool:
        return self.expanded() and not self.confirming()

    # --- visuals ----------------------------------------------------------------

    def _row_style(self) -> str:
        theme = self._theme
        s = self._s
        radius = f"border-radius: {max(6, int(_ROW_R * s))}px;"
        if self.is_active:
            return f"background: {_rgba(theme.verm, 0.14)}; {radius}"
        if self._hovered:
            return f"background: {theme.frame_bg}; {radius}"
        return radius

    def _name_style(self) -> str:
        theme = self._theme
        s = self._s
        return (
            f"color: {theme.verm_lit if self.is_active else theme.subtle};"
            f"font-size: {max(9, int(11.5 * s))}px;"
            f"font-weight: {600 if self.is_active else 500};"
        )

    def _row_hover(self, hover: bool):
        def _cb(*_a):
            self._hovered = hover
            self._bg.set_style(self._row_style())
            return True

        return _cb

    def _row_press(self, _w, ev):
        if ev.button == 1:
            self._surface._activate_network(self)
        return True

    # --- state sync ----------------------------------------------------------------

    def update_ap(self, ap):
        self.ap = ap
        self._glyph.set_level((ap.strength or 0) / 100.0)
        self._name.set_style(self._name_style())
        self._bg.set_style(self._row_style())

    def render(self):
        surface = self._surface
        theme = self._theme
        self._name.set_style(self._name_style())
        self._bg.set_style(self._row_style())
        self._lock.set_visible(self.secured)
        self._lock.set_color(theme.verm_lit if self.is_active else theme.icon_dim)

        confirm = self.confirming()
        asking = self.asking()
        connecting_here = surface._connecting and surface._attempt_ssid == self.ssid
        self._dot.set_running(connecting_here and (confirm or asking))

        self._confirm.set_visible(confirm)
        if confirm:
            self._confirm_hint.set_label(
                "Connected" if self.is_active else "Saved network"
            )
            self._primary_btn.set_label("Disconnect" if self.is_active else "Connect")
            # Show/Forget are saved-network actions; an unsaved network that is
            # merely active gets a bare Disconnect row (ukishima WifiSurface.qml)
            self._reveal_btn.set_visible(self.known)
            self._forget_btn.set_visible(self.known)

        shown_reveal = confirm and surface._revealed_ssid == self.ssid
        self._reveal.set_visible(shown_reveal)
        if shown_reveal:
            no_pw = surface.reveal_resolved and not surface._revealed_pw
            self._reveal_note.set_visible(no_pw)
            self._reveal_pw.set_visible(not no_pw)
            self._reveal_pw.set_label(surface._revealed_pw)
            self._reveal_btn.set_label("Hide")
        else:
            self._reveal_btn.set_label("Show")

        self._pw.set_visible(asking)
        self._dot_pw.set_running(connecting_here and asking)
        if asking:
            if self._pw_entry.get_text() != surface._pw_draft:
                self._pw_entry.set_text(surface._pw_draft)
                self._pw_entry.set_position(-1)
            self._pw_placeholder.set_visible(not bool(surface._pw_draft))

        self._fail.set_visible(asking and surface._connect_failed)
        if asking and surface._connect_failed:
            self._fail.set_label(
                "Connection failed"
                + (
                    " · " + surface._conn_fail_reason
                    if surface._conn_fail_reason
                    else ""
                )
            )

        self._detail.set_visible(self.is_active and bool(surface.conn_detail))
        if self.is_active:
            self._detail.set_label(surface.conn_detail)

    @property
    def rendered_height(self) -> int:
        parts = [_ROW]
        if self.is_active and self._surface.conn_detail:
            parts.append(_DETAIL_H)
        if self.confirming():
            parts.append(_SUB)
            if self._surface._revealed_ssid == self.ssid:
                parts.append(_REVEAL)
        elif self.asking():
            parts.append(_SUB)
            if self._surface._connect_failed:
                parts.append(_FAIL_H)
        return int((sum(parts) + _SPACING * (len(parts) - 1)) * self._s)

    def focus_password(self):
        GLib.idle_add(self._pw_entry.grab_focus)

    # --- entry / buttons --------------------------------------------------------------

    def _on_pw_entry_changed(self, *_a):
        self._surface._pw_draft = self._pw_entry.get_text()
        self._pw_placeholder.set_visible(not bool(self._surface._pw_draft))

    def _on_pw_accept(self, *_a):
        self._on_pw_return_press()

    def _on_pw_return_press(self, _w=None, _ev=None):
        surface = self._surface
        if not self.asking():
            return True
        surface._pw_draft = self._pw_entry.get_text()
        surface._connect_with_pw(self)
        return True

    def _pw_return_hover(self, hover: bool):
        def _cb(*_a):
            self._pw_return_glyph.set_color(
                self._theme.cream if hover else self._theme.verm_lit
            )
            return True

        return _cb

    def _on_connect_press(self):
        if self.is_active:
            self._surface._disconnect_network(self)
        else:
            self._surface._connect_known(self)

    def _on_reveal_press(self):
        if self._surface._revealed_ssid == self.ssid:
            self._surface._hide_password()
        else:
            self._surface._reveal_password(self)
        self._surface._render()

    def _on_forget_press(self):
        self._surface._forget(self)


# --- the surface ----------------------------------------------------------------------


class WifiSurface(Box):
    """The wifi surface: header (status + reload + toggle), hairline,
    and the signal-sorted network list with inline confirm/password rows."""

    def __init__(
        self,
        theme: Theme,
        s: float = 1.0,
        on_close: Callable[[], None] | None = None,
        on_size_change: Callable[[], None] | None = None,
        **kwargs,
    ):
        super().__init__(
            name="wifi-surface",
            orientation="v",
            style=self._pad_style(theme, s),
            **kwargs,
        )
        self._theme = theme
        self._s = s
        self._on_close = on_close
        self._on_size_change = on_size_change
        self._wifi_on = False
        self._scanning = False
        self._connecting = False
        self._connect_failed = False
        self._conn_fail_reason = ""
        self._attempt_ssid = ""
        self._attempt_was_known = False
        self._expanded_ssid = ""
        self._pw_draft = ""
        self._pending_pw = ""
        self._revealed_ssid = ""
        self._revealed_pw = ""
        self.reveal_resolved = False
        self._conn_ip = ""
        self._conn_band_rate = ""
        self._scan_timer: int | None = None
        self._conn_spin_timer: int | None = None
        self._refresh_pending: int | None = None
        self._detail_pending: int | None = None
        self._detail_running = False
        self._detail_wants = False

        self._rows: list[_WifiRow] = []
        self._list_h = 0

        # --- header --------------------------------------------------------------
        self._wifi_label = Label(
            label="WIFI",
            style=(
                f"color: {theme.subtle};"
                f"font-size: {max(9, int(10 * s))}px;"
                f"font-weight: 600;"
                f"letter-spacing: 1.6px;"
            ),
        )
        self._status = Label(
            label="",
            style=self._status_style(),
        )
        left = Box(
            h_align="start",
            h_expand=True,
            spacing=int(8 * s),
            v_align="center",
        )
        left.add(self._wifi_label)
        left.add(self._status)

        self._reload = GlyphIcon(
            name="reboot",
            color=theme.icon_dim,
            stroke=1.8,
            size=int(16 * s),
        )
        self._reload_box = EventBox(
            events=(
                Gdk.EventMask.BUTTON_PRESS_MASK
                | Gdk.EventMask.ENTER_NOTIFY_MASK
                | Gdk.EventMask.LEAVE_NOTIFY_MASK
            ),
            style=f"margin: 0 {int(6 * s)}px;",
        )
        self._reload_box.add(self._reload)
        self._reload_box.connect("button-press-event", self._on_reload_press)
        self._reload_box.connect("enter-notify-event", self._reload_hover(True))
        self._reload_box.connect("leave-notify-event", self._reload_hover(False))

        self._toggle = LinkToggle(
            theme, s=s, on=False, on_toggled=self._on_toggle_toggled
        )
        right = Box(h_align="end", spacing=int(12 * s), v_align="center")
        right.add(self._reload_box)
        right.add(self._toggle)

        self._header = Box(
            orientation="h",
            size=(-1, int(_HDR * s)),
            v_align="center",
            h_expand=True,
        )
        self._header.add(left)
        self._header.add(right)
        self.add(self._header)

        # hairline divider --------------------------------------------------------
        self._divider = Box(
            size=(-1, 1),
            style=f"background: {theme.hair}; margin-top: {int(_DIV * s)}px;",
        )
        self.add(self._divider)

        # list --------------------------------------------------------------------
        self._empty = Label(
            label="Searching networks…",
            h_align="center",
            style=(f"color: {theme.faint};font-size: {max(9, int(10.5 * s))}px;"),
        )
        self._empty.set_no_show_all(True)
        self._empty.set_visible(False)
        self._col = Box(orientation="v", spacing=int(_SPACING * s))
        self._scroll = ScrolledWindow(
            min_content_size=(-1, -1),
            max_content_size=(-1, -1),
            h_scrollbar_policy="never",
            v_scrollbar_policy="automatic",
            overlay_scroll=True,
            child=self._col,
            h_expand=True,
        )
        self._scroll.set_no_show_all(True)
        self._scroll.set_visible(False)
        self._list_area = Box(
            orientation="v",
            h_expand=True,
            style=f"margin-top: {int(_DIV_GAP * s)}px;",
        )
        self._list_area.add(self._scroll)
        self._list_area.add(self._empty)
        self._list_area.set_no_show_all(True)
        self._list_area.set_visible(False)
        self.add(self._list_area)

        # --- service wiring -------------------------------------------------------
        self._net = NetworkClient()
        self._wd = None
        self._wd_changed = None
        self._wd_enabled = None
        self._bind_device(self._net.wifi_device)
        self._net.connect("device-ready", self._on_device_event)
        self._net.connect("device-added", self._on_device_event)

        # surface-level keys: Escape closes (shell.qml route); keys bubble up
        # from the password Entry, which keeps focus for typing
        self.set_can_focus(True)
        self.connect("key-press-event", self._on_key)

        self.set_size_request(int(_W * s), self._height())
        self.refresh()

    def _on_key(self, _w, ev):
        if ev.keyval == Gdk.KEY_Escape:
            self.close()
            return True
        return False

    # --- theme / sizing ---------------------------------------------------------

    def _pad_style(self, theme: Theme, s: float) -> str:
        return (
            f"padding-top: {int(_MT * s)}px;"
            f"padding-left: {int(_MLR * s)}px;"
            f"padding-right: {int(_MLR * s)}px;"
            f"padding-bottom: {int(_MB * s)}px;"
        )

    def _status_style(self) -> str:
        theme = self._theme
        s = self._s
        color = theme.verm_lit if self._active_net_name() else theme.faint
        return f"color: {color}; font-size: {max(8, int(9.5 * s))}px; font-weight: 500;"

    def _height(self) -> int:
        return int((_MT + _HDR + _DIV + 1 + _DIV_GAP + self._list_h + _MB) * self._s)

    @property
    def conn_detail(self) -> str:
        parts = [p for p in (self._conn_ip, self._conn_band_rate) if p]
        return " · ".join(parts)

    # --- service ----------------------------------------------------------------

    def _on_device_event(self, *_a):
        self._bind_device(self._net.wifi_device)

    def _bind_device(self, wd):
        if wd is self._wd:
            return
        if self._wd is not None:
            try:
                if self._wd_changed is not None:
                    self._wd.disconnect(self._wd_changed)
                if self._wd_enabled is not None:
                    self._wd.disconnect(self._wd_enabled)
            except GLib.Error as e:
                logger.debug(f"[Wifi] stale device disconnect: {e}")
        self._wd = wd
        self._wd_changed = None
        self._wd_enabled = None
        if wd is None:
            self._wifi_on = False
            self._sync_header()
            self._retarget()
            return
        self._wd_changed = wd.connect("changed", self._on_net_changed)
        self._wd_enabled = wd.connect("notify::enabled", self._on_wifi_on_changed)
        self._wifi_on = bool(wd.enabled)
        self._sync_header()

    def _wifi_device(self):
        return self._net.wifi_device or self._wd

    # --- open / close -----------------------------------------------------------

    def open(self):
        self._expanded_ssid = ""
        self._connecting = False
        self._connect_failed = False
        self._conn_fail_reason = ""
        self._pw_draft = ""
        self._hide_password()
        self._stop_conn_spinner()
        self.refresh()
        self.start_scan()
        self.show()
        self.show_all()
        self._fetch_detail()
        self.set_can_focus(True)
        GLib.idle_add(self.grab_focus)

    def close(self):
        self.stop_scan(silent=True)
        self._expanded_ssid = ""
        self._connecting = False
        self._stop_conn_spinner()
        self._connect_failed = False
        self._conn_fail_reason = ""
        self._pw_draft = ""
        self._hide_password()
        self._cancel_keys()
        if self._on_close:
            self._on_close()

    def _cancel_keys(self):
        if self._refresh_pending is not None:
            GLib.source_remove(self._refresh_pending)
            self._refresh_pending = None
        if self._detail_pending is not None:
            GLib.source_remove(self._detail_pending)
            self._detail_pending = None
        self._detail_wants = False

    def surface_size(self) -> tuple[int, int]:
        self._compute_list_h()
        return int(_W * self._s), self._height()

    def set_scale(self, s: float):
        self._s = s
        self.set_style(self._pad_style(self._theme, s))
        self.refresh()
        self._retarget()

    # --- scanning ---------------------------------------------------------------

    def start_scan(self):
        if not self._on():
            return
        self._scanning = True
        wd = self._wifi_device()
        if wd is not None:
            wd.scan()
        self._reload.start_spin()
        self._reload.set_color(self._theme.flame_glow)
        if self._scan_timer is not None:
            GLib.source_remove(self._scan_timer)
        self._scan_timer = GLib.timeout_add(_SCAN_MS, self._on_scan_timeout)

    def stop_scan(self, silent: bool = False):
        self._scanning = False
        if self._scan_timer is not None:
            GLib.source_remove(self._scan_timer)
            self._scan_timer = None
        self._reload.stop_spin()
        self._reload.set_color(self._theme.icon_dim)

    def _on_scan_timeout(self):
        self._scan_timer = None
        self.stop_scan()
        return GLib.SOURCE_REMOVE

    def _on_reload_press(self, _w, ev):
        if ev.button != 1:
            return True
        if self._scanning:
            self.stop_scan()
        else:
            self.start_scan()
        return True

    def _reload_hover(self, hover: bool):
        def _cb(*_a):
            if not self._scanning:
                self._reload.set_color(
                    self._theme.cream if hover else self._theme.icon_dim
                )
            return True

        return _cb

    # --- wifi on/off --------------------------------------------------------------

    def _on(self) -> bool:
        return self._wifi_on

    def _on_toggle_toggled(self, _on: bool):
        wd = self._wifi_device()
        if wd is None:
            return
        wd.enabled = not wd.enabled

    def _on_wifi_on_changed(self, *_a):
        wd = self._wifi_device()
        self._wifi_on = bool(wd.enabled) if wd is not None else False
        if not self._wifi_on:
            self.stop_scan(silent=True)
            self._connecting = False
            self._stop_conn_spinner()
        self._sync_header()
        self._retarget()

    def _active_net_name(self) -> str:
        for row in self._rows:
            if row.is_active:
                return row.ssid
        return ""

    def _sync_header(self):
        if not self._on():
            status = "Off"
        else:
            active = self._active_net_name()
            status = active if active else "Not connected"
        self._status.set_label("· " + status)
        self._status.set_style(self._status_style())
        self._toggle.set_on(self._on(), animate=False)

    # --- network data ---------------------------------------------------------------

    def _is_known(self, ssid: str) -> bool:
        try:
            return self._net.is_network_saved(ssid)
        except GLib.Error:
            return False

    def _on_net_changed(self, *_a):
        active = self._active_net_name()
        if active and self._attempt_ssid and active == self._attempt_ssid:
            self._stop_conn_spinner()
            self._connecting = False
        self._schedule_refresh()
        if self._rows:
            self._fetch_detail(debounced=True)

    def _schedule_refresh(self):
        if self._refresh_pending is not None:
            return
        self._refresh_pending = GLib.timeout_add(_SEARCH_MS, self._do_refresh)

    def _do_refresh(self):
        self._refresh_pending = None
        self.refresh()
        return GLib.SOURCE_REMOVE

    # --- rows -------------------------------------------------------------------------

    def refresh(self):
        wd = self._wifi_device()
        if wd is None:
            self._rebuild_rows([])
            return
        try:
            nets = list(wd.access_points)
        except GLib.Error:
            nets = []
        self._rebuild_rows(nets)
        self._sync_header()

    def _rebuild_rows(self, nets):
        current = {row.ssid: row for row in self._rows}
        for row in self._col.get_children():
            self._col.remove(row)
        self._rows = []
        seen: set[str] = set()
        for ap in nets:
            ssid = ap.ssid
            if not ssid or ssid in seen:
                continue
            seen.add(ssid)
            row = current.pop(ssid, None)
            if row is None:
                row = _WifiRow(self, ap)
            else:
                row.update_ap(ap)
            self._rows.append(row)
            self._col.add(row)
        for stale in current.values():
            try:
                stale.destroy()
            except GLib.Error as e:
                logger.debug(f"[Wifi] stale row destroy: {e}")
        self._render()

    def _render(self):
        for row in self._rows:
            row.render()
        self._sync_header()
        self._retarget()

    def _compute_list_h(self) -> int:
        if not self._on() or not self._rows:
            self._list_h = 0
            return 0
        content = sum(row.rendered_height for row in self._rows)
        content += int(_SPACING * self._s * max(0, len(self._rows) - 1))
        self._list_h = int(max(_LIST_MIN * self._s, min(_LIST_MAX * self._s, content)))
        return self._list_h

    def _retarget(self):
        self._compute_list_h()
        w = int((_W - 2 * _MLR) * self._s)
        show_list = self._on() and self._list_h > 0
        self._list_area.set_visible(show_list)
        if show_list:
            has_rows = bool(self._rows)
            self._scroll.set_visible(has_rows)
            self._empty.set_visible(not has_rows)
            self._scroll.set_size_request(w, self._list_h)
        self.set_size_request(int(_W * self._s), self._height())
        if self._on_size_change:
            self._on_size_change()

    # --- detail ---------------------------------------------------------------------

    def _fetch_detail(self, debounced: bool = False):
        # coalesce bursty network change events: schedule one pending call a
        # fixed debounce after the last one, and never overlap with a call
        # already in flight (nmcli list can block on a slow scan).
        if self._detail_pending is not None:
            GLib.source_remove(self._detail_pending)
        if debounced:
            self._detail_pending = GLib.timeout_add(
                _DETAIL_DEBOUNCE_MS, self._fetch_detail
            )
            return
        self._detail_pending = None
        if self._detail_running:
            self._detail_wants = True
            return
        self._detail_running = True
        self._detail_wants = False

        wd = self._wifi_device()
        iface = None
        if wd is not None:
            try:
                iface = wd.get_interface_name()
            except GLib.Error:
                iface = None
        if not iface:
            self._detail_running = False
            return
        # band/rate read on the main thread from the NM D-Bus AP properties (no
        # blocking nmcli scan call); only the IP lookup needs a subprocess.
        band = wd.band
        rate = wd.rate
        br = (
            f"{band} {rate // 1000}Mbps".strip()
            if band
            else (f"{rate // 1000}Mbps" if rate else "")
        )

        def _ip():
            args = ["ip", "-4", "-o", "addr", "show", "dev", iface, "scope", "global"]
            result = run_command(args, timeout=2)
            for line in (result.stdout or "").split("\n"):
                # "3: wlan0    inet 192.168.1.2/24 brd ..."
                if " inet " in line:
                    return line.split(" inet ")[1].split("/")[0].strip()
            return ""

        def _run():
            ip = _ip()
            GLib.idle_add(self._apply_detail, f"BR {br}\nIP {ip}" if br else f"IP {ip}")
            self._detail_running = False
            if self._detail_wants:
                GLib.idle_add(self._fetch_detail)

        threading.Thread(target=_run, daemon=True).start()

    def _apply_detail(self, text: str):
        br = ""
        ip = ""
        for line in text.split("\n"):
            if line.startswith("BR "):
                br = line[3:].strip()
            elif line.startswith("IP "):
                ip = line[3:].strip()
        if br != self._conn_band_rate or ip != self._conn_ip:
            self._conn_band_rate = br
            self._conn_ip = ip
            self._render()

    # --- actions ------------------------------------------------------------------------

    def _activate_network(self, row: _WifiRow):
        ssid = row.ssid
        if not ssid:
            return
        if self._expanded_ssid == ssid:
            self._expanded_ssid = ""
            self._pw_draft = ""
            self._render()
            return
        if row.is_active or self._is_known(ssid):
            self._connect_failed = False
            self._pw_draft = ""
            self._expanded_ssid = ssid
            self._hide_password()
            self._render()
            return
        if not row.secured:
            self._expanded_ssid = ""
            self._connect_open(row)
            return
        self._connect_failed = False
        self._pw_draft = ""
        self._expanded_ssid = ssid
        self._hide_password()
        self._render()
        asking_row = self._row_by_ssid(ssid)
        if asking_row is not None and asking_row.asking():
            asking_row.focus_password()

    def _row_by_ssid(self, ssid: str):
        for row in self._rows:
            if row.ssid == ssid:
                return row
        return None

    def _connect_open(self, row: _WifiRow):
        self._net.connect_wifi(row.ap, lambda _ok, _err: self.refresh())
        self.refresh()

    def _connect_known(self, row: _WifiRow):
        self._expanded_ssid = ""
        self._connecting = True
        self._connect_failed = False
        self._attempt_ssid = row.ssid
        self._attempt_was_known = True
        self._start_conn_spinner()

        def _cb(ok, _err):
            self._stop_conn_spinner()
            self._connecting = False
            self._attempt_ssid = ""
            if ok:
                self._expanded_ssid = ""
                self.refresh()

        self._net.connect_wifi(row.ap, _cb)
        self.refresh()

    def _disconnect_network(self, row: _WifiRow):
        self._expanded_ssid = ""
        self._net.disconnect_wifi()
        self.refresh()

    def _connect_with_pw(self, row: _WifiRow):
        pw = self._pw_draft
        if not pw:
            return
        self._connecting = True
        self._connect_failed = False
        self._conn_fail_reason = ""
        self._attempt_ssid = row.ssid
        self._attempt_was_known = self._is_known(row.ssid)
        self._pending_pw = pw
        self._start_conn_spinner()

        def _cb(ok, err):
            self._stop_conn_spinner()
            self._connecting = False
            self._attempt_ssid = ""
            self._pending_pw = ""
            if ok:
                self._expanded_ssid = ""
                self._pw_draft = ""
                self.refresh()
                return
            self._connect_failed = True
            self._conn_fail_reason = _strip_error(err or "")
            if not self._attempt_was_known:
                self._cleanup_failed(row.ssid)
            self._render()

        self._net.connect_wifi_with_password(row.ap, pw, _cb)

    def _cleanup_failed(self, ssid: str):
        def _run():
            run_command(["nmcli", "connection", "delete", "id", ssid], timeout=6)
            GLib.idle_add(self.refresh)

        threading.Thread(target=_run, daemon=True).start()

    def _forget(self, row: _WifiRow):
        ssid = row.ssid
        if not ssid:
            return
        self._expanded_ssid = ""
        if self._revealed_ssid == ssid:
            self._hide_password()

        def _run():
            run_command(["nmcli", "connection", "delete", "id", ssid], timeout=6)
            GLib.idle_add(self.refresh)

        threading.Thread(target=_run, daemon=True).start()
        self.refresh()

    def _reveal_password(self, row: _WifiRow):
        ssid = row.ssid
        if not ssid:
            return
        self._revealed_ssid = ssid
        self._revealed_pw = ""
        self.reveal_resolved = False

        def _run():
            result = run_command(
                [
                    "nmcli",
                    "-s",
                    "-g",
                    "802-11-wireless-security.psk",
                    "connection",
                    "show",
                    "id",
                    ssid,
                ],
                timeout=6,
            )
            GLib.idle_add(self._apply_reveal, (result.stdout or "").rstrip("\n"))

        threading.Thread(target=_run, daemon=True).start()

    def _apply_reveal(self, pw: str):
        if self._revealed_ssid:
            self._revealed_pw = pw
            self.reveal_resolved = True
            self._render()

    def _hide_password(self):
        self._revealed_ssid = ""
        self._revealed_pw = ""
        self.reveal_resolved = False

    # --- connecting spinners --------------------------------------------------------

    def _start_conn_spinner(self):
        if self._conn_spin_timer is not None:
            GLib.source_remove(self._conn_spin_timer)
        self._conn_spin_timer = GLib.timeout_add(_CONN_SPIN_MS, self._on_conn_timeout)
        self._render()

    def _stop_conn_spinner(self):
        if self._conn_spin_timer is not None:
            GLib.source_remove(self._conn_spin_timer)
            self._conn_spin_timer = None

    def _on_conn_timeout(self):
        self._conn_spin_timer = None
        self._connecting = False
        self._render()
        return GLib.SOURCE_REMOVE
