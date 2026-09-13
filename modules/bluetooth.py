"""Bluetooth surface .

BLUETOOTH: "Scan" trigger with a 25 s auto-stop, adapter LinkToggle and the
live device list (connect/disconnect, forget); unpaired devices run the
bluetoothctl pair-trust-connect flow with a pulsing ember while running and a transient red
failure line. Standalone root surface, so Escape and the backdrop dismiss it
like every other surface.

Sizes: width 286·s, height = content + 26·s (13/16/16/13 margins).
"""

import threading
from collections.abc import Callable

from fabric.utils import Gdk, GLib, Gtk, logger
from fabric.widgets.box import Box
from fabric.widgets.eventbox import EventBox
from fabric.widgets.label import Label
from fabric.widgets.overlay import Overlay
from fabric.widgets.scrolledwindow import ScrolledWindow
from gi.repository import Pango

from components.glyph_icon import GlyphIcon
from components.link_toggle import LinkToggle
from core.theme import Theme
from modules.wifi import _PulseDot, _RowButton
from services.bluetooth import BluetoothClient, rfkill_soft_blocked
from utils.command import run_command

_W = 286
_MT = 13
_MLR = 16
_MB = 13

_HDR = 24
_ROW = 38
_ROW_R = 9
_TILE = 26
_SUB = 30
_FAIL_H = 14
_SPACING = 2
_DIV = 9
_DIV_GAP = 8
_LIST_MAX = 200
_LIST_MIN = 24
_SCAN_MS = 25000
_FAIL_TIMEOUT_MS = 4000
_CHANGED_MS = 80
_RESYNC_MS = 450


def _icon_for(icon_name: str) -> str:
    ic = (icon_name or "").lower()
    if ic.startswith("audio-") or ic == "audio-card":
        return "speaker"
    if ic == "input-mouse":
        return "mouse"
    if ic == "input-keyboard":
        return "keyboard"
    if ic == "input-gaming":
        return "gamepad"
    if ic in ("video-display", "computer"):
        return "monitor"
    if ic == "multimedia-player":
        return "music"
    return "bluetooth"


def _battery(dev) -> int:
    if dev is None:
        return -1
    try:
        b = float(dev.battery_percentage or 0.0)
    except TypeError, ValueError:
        return -1
    if b <= 0:
        return -1
    if b <= 1:
        b = b * 100
    return round(b)


def _rank(dev) -> int:
    if dev is None:
        return 3
    if dev.connected:
        return 0
    if dev.paired:
        return 1
    return 2 if (dev.name or "") else 3


def _sort_key(dev):
    return (_rank(dev), (dev.name or "").lower())


class _Filament(Box):
    """ukishima Filament: 22·s x 3·s thread with a vermDim→vermLit fill."""

    def __init__(self, theme: Theme, s: float):
        self._theme = theme
        self._s = s
        w = int(22 * s)
        h = int(3 * s)
        rad = max(1, int(1.5 * s))
        track = Box(
            size=(w, h),
            style=f"background: {theme.thread_bg}; border-radius: {rad}px;",
        )
        self._fill = Box(
            size=(0, h),
            h_align="start",
            style=(
                f"background-image: linear-gradient(to right, {theme.verm_dim}, {theme.verm_lit});"
                f"border-radius: {rad}px;"
            ),
        )
        self._fill.set_no_show_all(True)
        self._fill.set_visible(False)
        overlay = Overlay(child=track)
        overlay.add_overlay(self._fill)
        overlay.set_overlay_pass_through(self._fill, True)
        super().__init__(size=(w, h))
        self.add(overlay)

    def set_level(self, level: float):
        level = max(0.0, min(1.0, level))
        w = int(22 * self._s)
        fill_w = round(w * level)
        self._fill.set_size_request(fill_w, int(3 * self._s))
        self._fill.set_visible(fill_w > 0)


class _PairChip(EventBox):
    """Pill "Pair" chip for unpaired devices (QML radius 999 pill)."""

    def __init__(self, theme: Theme, s: float, on_click: Callable[[], None]):
        self._theme = theme
        self._s = s
        self._hovered = False
        self._on_click = on_click
        super().__init__(
            events=(
                Gdk.EventMask.BUTTON_PRESS_MASK
                | Gdk.EventMask.ENTER_NOTIFY_MASK
                | Gdk.EventMask.LEAVE_NOTIFY_MASK
            ),
        )
        self._label = Label(
            label="Pair",
            h_align="center",
            v_align="center",
            style=self._label_style(),
        )
        body = Box(
            h_align="center",
            v_align="center",
            size=(-1, int(18 * s)),
            style=(
                self._pill_style()
                + f"padding-left: {int(8 * s)}px;"
                + f"padding-right: {int(8 * s)}px;"
            ),
        )
        body.add(self._label)
        self.add(body)
        self.connect("button-press-event", self._on_press)
        self.connect("enter-notify-event", self._hover(True))
        self.connect("leave-notify-event", self._hover(False))

    def _label_style(self) -> str:
        theme = self._theme
        s = self._s
        return (
            f"color: {theme.cream if self._hovered else theme.dim};"
            f"font-size: {max(8, int(9.5 * s))}px;"
            f"font-weight: 600;"
        )

    def _pill_style(self) -> str:
        theme = self._theme
        return (
            f"background: {theme.frame_bg if self._hovered else theme.tile_bg};"
            f"border: 1px solid {theme.verm_dim if self._hovered else theme.border};"
            f"border-radius: 999px;"
        )

    def _hover(self, hover: bool):
        def _cb(*_a):
            self._hovered = hover
            self.get_children()[0].set_style(self._pill_style())
            self._label.set_style(self._label_style())
            return True

        return _cb

    def _on_press(self, _w, ev):
        if ev.button != 1:
            return True
        self._on_click()
        return True

    def paint_bg(self):
        self.get_children()[0].set_style(self._pill_style())
        self._label.set_style(self._label_style())


class _BtRow(Box):
    """One device row + expanded confirm row + transient pairing-failure line."""

    def __init__(self, surface: BtSurface, dev):
        self._surface = surface
        self._theme = surface._theme
        self._s = surface._s
        self.dev = dev
        self._hovered = False
        theme = surface._theme
        s = surface._s
        self._w = int((_W - 2 * _MLR) * s)
        super().__init__(
            orientation="v",
            spacing=int(_SPACING * s),
            h_expand=True,
        )
        self._addr = dev.address or ""

        # --- main row --------------------------------------------------------
        self._bg = Box(size=(self._w, int(_ROW * s)), style=self._row_style())
        self._main = EventBox(
            events=(
                Gdk.EventMask.BUTTON_PRESS_MASK
                | Gdk.EventMask.ENTER_NOTIFY_MASK
                | Gdk.EventMask.LEAVE_NOTIFY_MASK
            ),
            size=(self._w, int(_ROW * s)),
        )

        self._glyph = GlyphIcon(
            name=_icon_for(dev.icon_name),
            color=theme.verm_lit if dev.connected else theme.icon_dim,
            stroke=1.7,
            size=int(15 * s),
        )
        tile = Box(
            size=(int(_TILE * s), int(_TILE * s)),
            style=(
                f"background: {theme.tile_bg};"
                f"border: 1px solid {theme.border};"
                f"border-radius: {max(4, int(8 * s))}px;"
            ),
        )
        _glyph_fixed = Gtk.Fixed()
        _glyph_fixed.set_size_request(int(_TILE * s), int(_TILE * s))
        _glyph_center = (int(_TILE * s) - int(15 * s)) // 2
        _glyph_fixed.put(self._glyph, _glyph_center, _glyph_center)
        tile.add(_glyph_fixed)
        # raw Gtk containers default to hidden; fabric's Box.add never shows
        # them, so the glyph previously got no allocation (1x1, never drawn)
        _glyph_fixed.show()

        self._name = Label(
            label=dev.name or "Unknown",
            h_align="start",
            h_expand=True,
            style=self._name_style(),
        )
        self._name.set_ellipsize(Pango.EllipsizeMode.END)
        self._meta = Label(
            label="",
            h_align="start",
            style=(
                f"color: {theme.faint};"
                f"font-size: {max(8, int(9.5 * s))}px;"
                f"font-weight: 500;"
            ),
        )
        self._meta.set_ellipsize(Pango.EllipsizeMode.END)
        texts = Box(
            orientation="v",
            spacing=int(1 * s),
            h_expand=True,
            v_align="center",
            style=f"padding-left: {int(10 * s)}px; padding-right: {int(8 * s)}px;",
        )
        texts.add(self._name)
        texts.add(self._meta)

        self._dot = _PulseDot(theme, s)
        self._filament = _Filament(theme, s)
        self._filament.set_no_show_all(True)
        self._filament.set_visible(False)
        self._pct = Label(
            label="",
            style=(
                f"color: {theme.faint};"
                f"font-size: {max(8, int(9.5 * s))}px;"
                f"font-weight: 500;"
            ),
        )
        self._pct.set_no_show_all(True)
        self._pct.set_visible(False)
        self._pair_chip = _PairChip(theme, s, self._on_pair_press)
        self._pair_chip.set_no_show_all(True)
        self._pair_chip.set_visible(False)

        right = Box(h_align="end", v_align="center", spacing=int(8 * s))
        right.add(self._dot)
        right.add(self._filament)
        right.add(self._pct)
        right.add(self._pair_chip)

        content = Box(
            h_expand=True,
            v_align="center",
            style=f"padding-left: {int(6 * s)}px; padding-right: {int(8 * s)}px;",
        )
        content.add(tile)
        content.add(texts)
        content.add(right)
        self._bg.add(content)
        self._main.add(self._bg)
        self.add(self._main)
        self._main.connect("button-press-event", self._row_press)
        self._main.connect("enter-notify-event", self._row_hover(True))
        self._main.connect("leave-notify-event", self._row_hover(False))

        # --- confirm row -------------------------------------------------------
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
        self._confirm_hint.set_max_width_chars(18)
        self._btns = Box(h_align="end", v_align="center", spacing=int(6 * s))
        self._primary_btn = _RowButton(theme, s, "Connect", self._on_primary_press)
        self._forget_btn = _RowButton(
            theme, s, "Forget", self._on_forget_press, danger=True
        )
        self._btns.add(self._primary_btn)
        self._btns.add(self._forget_btn)
        self._confirm.add(self._confirm_hint)
        self._confirm.add(self._btns)
        self._confirm.set_no_show_all(True)
        self._confirm.set_visible(False)
        self.add(self._confirm)

        # --- pairing failure line ---------------------------------------------
        self._fail = Label(
            label="Pairing failed",
            h_align="start",
            style=(
                f"color: {theme.verm_lit};"
                f"font-size: {max(8, int(9.5 * s))}px;"
                f"padding-left: {int(42 * s)}px;"
            ),
        )
        self._fail.set_no_show_all(True)
        self._fail.set_visible(False)
        self.add(self._fail)

    # --- visuals ---------------------------------------------------------------

    def _row_style(self) -> str:
        theme = self._theme
        s = self._s
        radius = f"border-radius: {max(6, int(_ROW_R * s))}px;"
        if self._hovered:
            return f"background: {theme.frame_bg}; {radius}"
        return radius

    def _name_style(self) -> str:
        theme = self._theme
        s = self._s
        connected = bool(self.dev.connected)
        return (
            f"color: {theme.cream if connected else theme.subtle};"
            f"font-size: {max(9, int(11.5 * s))}px;"
            f"font-weight: {600 if connected else 500};"
        )

    def _row_hover(self, hover: bool):
        def _cb(*_a):
            self._hovered = hover
            self._bg.set_style(self._row_style())
            return True

        return _cb

    def _row_press(self, _w, ev):
        if ev.button == 1:
            self._surface._activate(self)
        return True

    # --- state sync -----------------------------------------------------------

    @property
    def pairing(self) -> bool:
        return bool(self._addr) and self._surface._pairing_address == self._addr

    @property
    def failed(self) -> bool:
        return bool(self._addr) and self._surface._failed_address == self._addr

    @property
    def confirming(self) -> bool:
        return bool(self._addr) and self._surface._expanded_address == self._addr

    def update_dev(self, dev):
        self.dev = dev
        self._addr = dev.address or ""

    def render(self):
        dev = self.dev
        theme = self._theme
        connected = bool(dev.connected)
        paired = bool(dev.paired)
        busy = pairing = self.pairing
        try:
            busy = busy or bool(dev.connecting)
        except GLib.Error:
            pass

        self._glyph.set_glyph(_icon_for(dev.icon_name))
        self._glyph.set_color(theme.verm_lit if connected else theme.icon_dim)
        self._name.set_label(dev.name or "Unknown")
        self._name.set_style(self._name_style())
        self._meta.set_label(self._surface._meta(dev))
        self._bg.set_style(self._row_style())

        self._dot.set_running(busy or pairing)
        battery = _battery(dev)
        show_battery = connected and battery >= 0
        self._filament.set_visible(show_battery)
        if show_battery:
            self._filament.set_level(battery / 100.0)
            self._pct.set_label(f"{battery}%")
        self._pct.set_visible(show_battery)
        self._pair_chip.set_visible(not paired and not pairing)

        confirm = bool(self._addr) and self._surface._expanded_address == self._addr
        self._confirm.set_visible(confirm)
        if confirm:
            self._confirm_hint.set_label("Connected" if connected else "Paired")
            self._primary_btn.set_label("Disconnect" if connected else "Connect")

        self._fail.set_visible(self.failed)
        if self._pair_chip.get_visible():
            self._pair_chip.paint_bg()

    @property
    def rendered_height(self) -> int:
        parts = [_ROW]
        if self.confirming:
            parts.append(_SUB)
        if self.failed:
            parts.append(_FAIL_H)
        return int((sum(parts) + _SPACING * (len(parts) - 1)) * self._s)

    # --- buttons -----------------------------------------------------------------

    def _on_pair_press(self):
        self._surface._activate(self)

    def _on_primary_press(self):
        if self.dev.connected:
            self._surface._disconnect(self)
        else:
            self._surface._connect(self)

    def _on_forget_press(self):
        self._surface._forget(self)


# --- the surface ---------------------------------------------------------------------


class BtSurface(Box):
    """The bluetooth surface: header, scan trigger, adapter toggle,
    live device list with inline pair/connect/disconnect/forget flows."""

    def __init__(
        self,
        theme: Theme,
        s: float = 1.0,
        on_close: Callable[[], None] | None = None,
        on_size_change: Callable[[], None] | None = None,
        **kwargs,
    ):
        super().__init__(
            name="bt-surface",
            orientation="v",
            style=self._pad_style(theme, s),
            **kwargs,
        )
        self._theme = theme
        self._s = s
        self._on_close = on_close
        self._on_size_change = on_size_change
        self._scanning = False
        self._pairing_address = ""
        self._failed_address = ""
        self._expanded_address = ""
        self._scan_timer: int | None = None
        self._fail_timer: int | None = None
        self._refresh_pending: int | None = None
        self._resync_id: int | None = None

        self._rows: list[_BtRow] = []
        self._list_h = 0

        # --- header ----------------------------------------------------------
        self._bt_label = Label(
            label="BLUETOOTH",
            style=(
                f"color: {theme.subtle};"
                f"font-size: {max(9, int(10 * s))}px;"
                f"font-weight: 600;"
                f"letter-spacing: 1.6px;"
            ),
        )
        left = Box(
            h_align="start",
            h_expand=True,
            spacing=int(8 * s),
            v_align="center",
        )
        left.add(self._bt_label)

        self._scan_label = Label(
            label="Scan",
            style=self._scan_style(),
        )
        self._scan_box = EventBox(
            events=(
                Gdk.EventMask.BUTTON_PRESS_MASK
                | Gdk.EventMask.ENTER_NOTIFY_MASK
                | Gdk.EventMask.LEAVE_NOTIFY_MASK
            ),
            style="",
        )
        self._scan_wrap = Box(
            h_align="center",
            v_align="center",
            style=f"padding: {int(2 * s)}px {int(6 * s)}px;",
        )
        self._scan_wrap.add(self._scan_label)
        self._scan_box.add(self._scan_wrap)
        self._scan_box.connect("button-press-event", self._on_scan_press)
        self._scan_box.connect("enter-notify-event", self._scan_hover(True))
        self._scan_box.connect("leave-notify-event", self._scan_hover(False))
        self._scan_box.set_no_show_all(True)
        self._scan_box.set_visible(False)

        self._toggle = LinkToggle(
            theme, s=s, on=False, on_toggled=self._on_toggle_toggled
        )
        right = Box(h_align="end", spacing=int(10 * s), v_align="center")
        right.add(self._scan_box)
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
            label="No devices found",
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
            v_scrollbar_policy="never",
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
        try:
            self._bt = BluetoothClient()
        except GLib.Error as e:
            logger.warning(f"[Bt] BluetoothClient init failed: {e}")
            self._bt = None
        self._adapter = None
        if self._bt is not None:
            try:
                self._adapter = self._bt.adapters[0] if self._bt.adapters else None
            except GLib.Error as e:
                logger.warning(f"[Bt] adapter lookup failed: {e}")
            self._bt.connect("changed", self._on_bt_changed)
            self._bt.connect("device-added", self._on_dev_event)
            self._bt.connect("device-removed", self._on_dev_event)

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

    def _scan_style(self) -> str:
        theme = self._theme
        s = self._s
        color = theme.verm_lit if self._scanning else theme.dim
        return f"color: {color};font-size: {max(8, int(9.5 * s))}px;font-weight: 600;"

    def _height(self) -> int:
        return int((_MT + _HDR + _DIV + 1 + _DIV_GAP + self._list_h + _MB) * self._s)

    # --- service ------------------------------------------------------------------

    def _adapter_now(self):
        if self._adapter is not None:
            return self._adapter
        if self._bt is not None and self._bt.adapters:
            self._adapter = self._bt.adapters[0]
        return self._adapter

    def _on_bt_changed(self, *_a):
        self._sync_header()
        self._schedule_refresh()

    def _on_dev_event(self, *_a):
        if self._bt is not None:
            self._adapter = self._bt.adapters[0] if self._bt.adapters else None
        self._sync_header()
        self.refresh()

    def _schedule_refresh(self):
        if self._refresh_pending is not None:
            return
        self._refresh_pending = GLib.timeout_add(_CHANGED_MS, self._do_refresh)

    def _do_refresh(self):
        self._refresh_pending = None
        self.refresh()
        return GLib.SOURCE_REMOVE

    # --- open / close -------------------------------------------------------------

    def open(self):
        self._expanded_address = ""
        self._pairing_address = ""
        self._failed_address = ""
        adapter = self._adapter_now()
        if adapter is not None and adapter.enabled:
            self._start_scan()
        else:
            self._scanning = False
        self.refresh()
        self.show()
        self.show_all()
        self.set_can_focus(True)
        GLib.idle_add(self.grab_focus)

    def close(self):
        self._stop_scan()
        self._expanded_address = ""
        self._pairing_address = ""
        self._failed_address = ""
        if self._fail_timer is not None:
            GLib.source_remove(self._fail_timer)
            self._fail_timer = None
        if self._refresh_pending is not None:
            GLib.source_remove(self._refresh_pending)
            self._refresh_pending = None
        if self._resync_id is not None:
            GLib.source_remove(self._resync_id)
            self._resync_id = None
        if self._on_close:
            self._on_close()

    def surface_size(self) -> tuple[int, int]:
        self._compute_list_h()
        return int(_W * self._s), self._height()

    def set_scale(self, s: float):
        self._s = s
        self.set_style(self._pad_style(self._theme, s))
        self.refresh()

    # --- scanning -----------------------------------------------------------------

    def _start_scan(self):
        adapter = self._adapter_now()
        if adapter is None:
            return
        try:
            adapter.scanning = True
        except GLib.Error as e:
            logger.warning(f"[Bt] start scan failed: {e}")
        self._scanning = True
        self._sync_header()
        if self._scan_timer is not None:
            GLib.source_remove(self._scan_timer)
        self._scan_timer = GLib.timeout_add(_SCAN_MS, self._on_scan_timeout)

    def _stop_scan(self):
        self._scanning = False
        if self._scan_timer is not None:
            GLib.source_remove(self._scan_timer)
            self._scan_timer = None
        adapter = self._adapter_now()
        if adapter is not None:
            try:
                if adapter.scanning:
                    adapter.scanning = False
            except GLib.Error as e:
                logger.warning(f"[Bt] stop scan failed: {e}")
        self._sync_header()

    def _on_scan_timeout(self):
        self._scan_timer = None
        adapter = self._adapter_now()
        if adapter is not None:
            try:
                adapter.scanning = False
            except GLib.Error as e:
                logger.warning(f"[Bt] scan timeout stop failed: {e}")
        self._scanning = False
        self._sync_header()
        return GLib.SOURCE_REMOVE

    def _on_scan_press(self, _w, ev):
        if ev.button != 1:
            return True
        adapter = self._adapter_now()
        if adapter is not None and adapter.enabled:
            if self._scanning:
                self._stop_scan()
            else:
                self._start_scan()
        return True

    def _scan_hover(self, hover: bool):
        def _cb(*_a):
            if self._scanning:
                return True
            self._scan_label.set_style(
                f"color: {self._theme.cream if hover else self._theme.dim};"
                f"font-size: {max(8, int(9.5 * self._s))}px;"
                f"font-weight: 600;"
            )
            return True

        return _cb

    # --- adapter toggle ------------------------------------------------------------

    def _on_toggle_toggled(self, _on: bool):
        self._toggle_adapter()

    def _toggle_adapter(self):
        adapter = self._adapter_now()
        if adapter is None:
            return
        if adapter.enabled:
            adapter.enabled = False
            self._resync_later()
            return
        if rfkill_soft_blocked():

            def _run():
                run_command(["rfkill", "unblock", "bluetooth"], timeout=5)
                GLib.idle_add(self._enable_after_rfkill)

            threading.Thread(target=_run, daemon=True).start()
        else:
            adapter.enabled = True
        self._resync_later()

    def _enable_after_rfkill(self):
        adapter = self._adapter_now()
        if adapter is not None:
            adapter.enabled = True
        self._resync_later()

    def _resync_later(self):
        if self._resync_id is not None:
            return
        self._resync_id = GLib.timeout_add(_RESYNC_MS, self._do_resync)

    def _do_resync(self):
        self._resync_id = None
        self._sync_header()
        return GLib.SOURCE_REMOVE

    def _sync_header(self):
        adapter = self._adapter_now()
        enabled = adapter is not None and bool(adapter.enabled)
        self._scanning = bool(adapter.scanning) if adapter else False
        self._scan_box.set_visible(enabled)
        self._scan_label.set_label("Scanning…" if self._scanning else "Scan")
        self._scan_label.set_style(self._scan_style())
        self._toggle.set_on(enabled, animate=False)
        self._empty.set_label("Scanning…" if self._scanning else "No devices found")

    # --- device data -------------------------------------------------------------

    def _meta(self, dev) -> str:
        parts: list[str] = []
        if dev.connected:
            parts.append("connected")
        elif dev.paired:
            parts.append("paired")
        try:
            if dev.connecting:
                parts.append("connecting")
        except GLib.Error:
            pass
        if dev.address:
            parts.append(dev.address)
        return " · ".join(parts)

    # --- rows ---------------------------------------------------------------------

    def refresh(self):
        adapter = self._adapter_now()
        if adapter is None:
            self._rebuild_rows([])
        else:
            try:
                devices = list(adapter.devices)
            except GLib.Error as e:
                logger.warning(f"[Bt] listing devices failed: {e}")
                devices = []
            devices.sort(key=_sort_key)
            self._rebuild_rows(devices)
        self._sync_header()

    def _rebuild_rows(self, devices):
        # in-place reconcile: never remove+re-add rows that already exist, or
        # every BT refresh (incl. the ones fired by our own connect/pair) tears
        # down the whole column and re-lays everything out
        current = {row._addr: row for row in self._rows}
        seen: set[str] = set()
        new_rows: list[_BtRow] = []
        for dev in devices:
            addr = dev.address or ""
            if not addr or addr in seen:
                continue
            seen.add(addr)
            row = current.pop(addr, None)
            if row is None:
                row = _BtRow(self, dev)
                self._col.add(row)
            else:
                row.update_dev(dev)
            row.render()
            new_rows.append(row)
        for stale in current.values():
            try:
                self._col.remove(stale)
                stale.destroy()
            except GLib.Error as e:
                logger.debug(f"[Bt] stale row destroy: {e}")
        self._rows = new_rows
        self._render()

    def _render(self):
        for row in self._rows:
            row.render()
        self._sync_header()
        self._retarget()

    def _compute_list_h(self) -> int:
        if not self._rows:
            self._list_h = int(_LIST_MIN * self._s)
            return self._list_h
        content = sum(row.rendered_height for row in self._rows)
        content += int(_SPACING * self._s * max(0, len(self._rows) - 1))
        self._list_h = int(max(_LIST_MIN * self._s, min(_LIST_MAX * self._s, content)))
        return self._list_h

    def _retarget(self):
        self._compute_list_h()
        w = int((_W - 2 * _MLR) * self._s)
        has_rows = bool(self._rows)
        self._list_area.set_visible(self._bt is not None)
        self._scroll.set_visible(has_rows)
        self._empty.set_visible(not has_rows)
        self._scroll.set_size_request(w, self._list_h)
        nw, nh = int(_W * self._s), self._height()
        old = getattr(self, "_target_size", None)
        if old != (nw, nh):
            self._target_size = (nw, nh)
            self.set_size_request(nw, nh)
            if self._on_size_change:
                self._on_size_change()

    # --- actions -----------------------------------------------------------------

    def _activate(self, row: _BtRow):
        dev = row.dev
        if dev is None:
            return
        if dev.connected or dev.paired:
            addr = dev.address or ""
            self._expanded_address = (
                "" if (addr and self._expanded_address == addr) else addr
            )
            self._render()
            return
        self._pair(row)

    def _connect(self, row: _BtRow):
        self._expanded_address = ""
        try:
            row.dev.connect_device(True)
        except GLib.Error as e:
            logger.warning(f"[Bt] connect failed for {row._addr}: {e}")
        self._render()

    def _disconnect(self, row: _BtRow):
        self._expanded_address = ""
        try:
            row.dev.connect_device(False)
        except GLib.Error as e:
            logger.warning(f"[Bt] disconnect failed for {row._addr}: {e}")
        self._render()

    def _forget(self, row: _BtRow):
        self._expanded_address = ""
        if self._bt is not None:
            try:
                self._bt.remove_device(row.dev)
            except GLib.Error as e:
                logger.warning(f"[Bt] remove failed for {row._addr}: {e}")
        self._render()

    def _pair(self, row: _BtRow):
        addr = row._addr
        if not addr:
            return
        self._pairing_address = addr
        self._failed_address = ""
        self._render()

        def _run():
            result = run_command(
                [
                    "sh",
                    "-c",
                    'timeout 30 bluetoothctl pair "$1" && timeout 30 bluetoothctl trust "$1" && timeout 30 bluetoothctl connect "$1"',
                    "sh",
                    addr,
                ],
                timeout=100,
            )
            GLib.idle_add(self._pair_finished, addr, result.returncode)

        threading.Thread(target=_run, daemon=True).start()

    def _on_fail_timeout(self):
        self._fail_timer = None
        self._failed_address = ""
        self._render()
        return GLib.SOURCE_REMOVE

    def _pair_finished(self, addr: str, code: int):
        if self._pairing_address != addr:
            return
        self._pairing_address = ""
        if code != 0:
            self._failed_address = addr
            self._render()
            if self._fail_timer is not None:
                GLib.source_remove(self._fail_timer)
            self._fail_timer = GLib.timeout_add(_FAIL_TIMEOUT_MS, self._on_fail_timeout)
        self._render()
