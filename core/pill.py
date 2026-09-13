"""The island
Rest/hover/pinned states, a 420 ms morph between them, the rest clock face and
the hover glance row. Geometry changes are reported to the owning Bar (input
mask + reserve window) through `on_geometry(x, y, w, h)`, the pill itself
living inside a Gtk.Fixed.

The heavy behaviour mixes in from two sibling mixins:

- :class:`~forma.widgets.pill_osd.PillOSDMixin` — OSD service wiring + flash.
- :class:`~forma.widgets.pill_drop.PillDropMixin` — AppImage drag & drop install.

Everything here is the island core: widget anatomy, states, morph, clock, scale
and surface launch.
"""

import time as _time
from collections.abc import Callable
from typing import Optional

from fabric.utils import Gdk, GLib, Gtk
from fabric.widgets.box import Box
from fabric.widgets.datetime import DateTime
from fabric.widgets.label import Label
from fabric.widgets.overlay import Overlay

from components.dropzone import DropZone
from core.flags import flags
from core.motion import FAST, MORPH, STANDARD, lerp, tween
from core.theme import Theme
from modules.bluetooth import BtSurface
from modules.clipboard import Clipboard
from modules.emoji import EmojiPicker
from modules.launcher import Launcher
from modules.link import Link
from modules.power import Power
from modules.toast import Toast
from modules.wallpaper import WallpaperSurface
from modules.wifi import WifiSurface
from services.notifs import notifs
from services.workspacerules import workspacerules
from widgets.audio import AudioOsd
from widgets.battery import BatteryOsd
from widgets.brightness import BrightnessOsd
from widgets.drop import PillDropMixin
from widgets.keyboard import KeyboardOsd
from widgets.lock_key import LockKeyOsd
from widgets.osd import PillOSDMixin
from widgets.player import PlayerOsd
from widgets.tray import Tray
from widgets.workspaces import Workspaces

_REST_W = 160
_REST_H = 38
_HOVER_H = 58
_HOVER_PAD = 20


class Pill(PillOSDMixin, PillDropMixin, Box):
    def __init__(
        self,
        theme: Theme,
        screen_name: str = "",
        on_geometry: Callable[[int, int, int, int], None] | None = None,
        on_surface_state: Callable[[bool], None] | None = None,
        **kwargs,
    ):
        super().__init__(
            name="pill-root",
            h_expand=False,
            v_expand=False,
            valign="start",
            **kwargs,
        )
        self._theme = theme
        self._screen_name = screen_name
        self._on_geometry = on_geometry
        self._on_surface_state = on_surface_state
        self._s = flags.ui_scale or 1.0

        self.state = "rest"  # rest | hover | pinned | osd | surface | dragover
        self.rest_w = int(_REST_W * self._s)
        self.rest_h = int(_REST_H * self._s)
        self.hover_h = int(_HOVER_H * self._s)
        self._pinned = False
        self._hovering = False
        self._morph_closeness = 0.0
        self._tween: Optional = None
        self._grace: int | None = None
        self._osd_hide_id: int | None = None
        self._osd_fade: Optional = None
        self._suppressed = False
        self._toast_count = 0
        self._toast_reveal = None
        self._surface_reveal = None
        self._toast_fade_out = None
        self._osd_kind = "ws"  # ws | volume | mic | brightness | lock | battery | track

        # --- body ------------------------------------------------------------
        self._body = Box(name="pill-body", style_classes=["pill-body"])
        self._body.set_events(
            self._body.get_events()
            | Gdk.EventMask.ENTER_NOTIFY_MASK
            | Gdk.EventMask.LEAVE_NOTIFY_MASK
            | Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
        )
        self._body.connect("enter-notify-event", self._on_enter)
        self._body.connect("leave-notify-event", self._on_leave)
        self._body.connect("button-press-event", self._on_press)

        # rest face: content varies with flags.mainDisplay (ukishima restRow);
        # built in _build_rest_face() once OSD services + ws strip exist.
        self._rest_date: DateTime | None = None
        self._rest_time: DateTime | None = None
        self._rest_weekday: DateTime | None = None
        self._rest_ws: Label | None = None
        self._rest_kb: Label | None = None
        self._rest_battery: Label | None = None
        self._kb_linked: bool = False
        self._rest_system_clock: int | None = None
        self.rest_face = Box(spacing=int(9 * self._s), h_align="center")

        # glance row (hover/pinned) — hidden until the morph reveals it so it
        # contributes no height while the pill is at rest
        self.glance = Box(
            spacing=int(20 * self._s),
            h_align="center",
            style=f"margin-left: {_HOVER_PAD}px; margin-right: {_HOVER_PAD}px;",
        )
        self.glance.set_opacity(0.0)
        self.glance.set_no_show_all(True)
        self.glance.set_visible(False)
        self.tray = Tray(icon_size=int(20 * self._s))
        self.glance.add(self.tray)
        self.glance_w = self._measure_glance()
        self.tray._watcher.connect("item-added", lambda *_a: self._reflow_glance())
        self.tray._watcher.connect("item-removed", lambda *_a: self._reflow_glance())

        # vertical layout: face on top, glance below (the pill grows downward);
        # inner takes the full body width so child boxes can centre themselves
        self._inner = Box(
            orientation=Gtk.Orientation.VERTICAL, h_expand=True, v_align="center"
        )
        self._inner.pack_start(self.rest_face, False, False, 0)
        self._inner.pack_start(self.glance, False, False, int(6 * self._s))

        center = Box(h_expand=True, v_expand=True)
        center.pack_start(self._inner, False, False, 0)

        # workspace OSD strip, cross-faded in when the pill morphs to `osd`
        self.ws_osd = Workspaces(
            scale=self._s,
            gap=8 * self._s,
            interactive=False,
            screen=screen_name,
            presets=tuple(workspacerules.rules_for(screen_name)),
        )

        # volume / mic OSD face — symbolic icon + horizontal fill + percent in its
        # own widget; the pill only swaps it in above the workspace strip
        self.audio_osd = AudioOsd(theme, s=self._s)
        # screen-brightness OSD face, driven by the Brightness service
        self.brightness_osd = BrightnessOsd(theme, s=self._s)
        # lock-key OSD face (caps/num), icon + on/off label, no fader
        self.lock_key_osd = LockKeyOsd(theme, s=self._s)
        # battery OSD face — bolt/level icon + gradient filament + percent,
        # driven by the UPower-backed Battery service
        self.battery_osd = BatteryOsd(theme, s=self._s)
        # keyboard-layout OSD face — icon + layout code, flashed on switches
        self.keyboard_osd = KeyboardOsd(theme, s=self._s)
        # track OSD face — cover + scrolling title/artist + play glyph, driven
        # by the MPRIS PlayerManager (ukishima trackRow, 344 x 64)
        self.player_osd = PlayerOsd(theme, s=self._s)

        self._osd_face = Box(h_align="center", v_align="center")
        self._osd_face.set_no_show_all(True)
        self._osd_face.set_visible(False)
        self._osd_face.add(self.ws_osd)
        self._osd_face.add(self.audio_osd)
        self._osd_face.add(self.brightness_osd)
        self._osd_face.add(self.lock_key_osd)
        self._osd_face.add(self.battery_osd)
        self._osd_face.add(self.keyboard_osd)
        self._osd_face.add(self.player_osd)

        # major surfaces (launcher, clipboard, …) — hosted in their own face,
        # cross-faded in when the pill morphs to `surface`; one is visible at a
        # time, keyed by `_surface_kind`. The face is anchored to the body's TOP
        # (v_align start) so a growing body never re-centres (and thus slides)
        # the surface content while it animates height — the header keeps its
        # position at the pill top and the body grows downward underneath it.
        self._surface_face = Box(h_align="center", v_align="start")
        self._surface_face.set_no_show_all(True)
        self._surface_face.set_visible(False)
        self._surface_kind = "launcher"
        self._launcher = Launcher(
            theme,
            s=self._s,
            on_close=self._close_surface,
            on_size_change=self._retarget_surface,
        )
        self._surface_face.add(self._launcher)
        self._clipboard = Clipboard(
            theme,
            s=self._s,
            on_close=self._close_surface,
            on_size_change=self._retarget_surface,
        )
        self._clipboard.set_visible(False)
        self._clipboard.set_no_show_all(True)
        self._surface_face.add(self._clipboard)
        self._emoji = EmojiPicker(
            theme,
            s=self._s,
            on_close=self._close_surface,
            on_size_change=self._retarget_surface,
        )
        self._emoji.set_visible(False)
        self._emoji.set_no_show_all(True)
        self._surface_face.add(self._emoji)
        self._link = Link(
            theme,
            s=self._s,
            on_close=self._close_surface,
            on_size_change=self._retarget_surface,
        )
        self._link.set_visible(False)
        self._link.set_no_show_all(True)
        self._link.set_hexpand(False)
        self._surface_face.add(self._link)
        self._wallpaper = WallpaperSurface(
            theme, s=self._s, on_close=self._close_surface
        )
        self._wallpaper.set_visible(False)
        self._wallpaper.set_no_show_all(True)
        self._surface_face.add(self._wallpaper)
        self._power = Power(theme, s=self._s, on_close=self._close_surface)
        self._power.set_visible(False)
        self._power.set_no_show_all(True)
        self._surface_face.add(self._power)
        self._wifi = WifiSurface(
            theme,
            s=self._s,
            on_close=self._close_surface,
            on_size_change=self._retarget_surface,
        )
        self._wifi.set_visible(False)
        self._wifi.set_no_show_all(True)
        self._wifi.set_hexpand(False)
        self._surface_face.add(self._wifi)
        self._bt = BtSurface(
            theme,
            s=self._s,
            on_close=self._close_surface,
            on_size_change=self._retarget_surface,
        )
        self._bt.set_visible(False)
        self._bt.set_no_show_all(True)
        self._bt.set_hexpand(False)
        self._surface_face.add(self._bt)
        self._surfaces = {
            "launcher": self._launcher,
            "clipboard": self._clipboard,
            "emoji": self._emoji,
            "link": self._link,
            "wallpaper": self._wallpaper,
            "power": self._power,
            "wifi": self._wifi,
            "bt": self._bt,
        }

        # toast face — newest notification popup, shown while popups remain and
        # no surface/drag owns the pill (ukishima toastLoader, 342 x content+24)
        self._toast = Toast(theme, s=self._s, notifs=notifs)

        self._toast_face = Gtk.Fixed(valign=Gtk.Align.START)
        self._toast_face.set_no_show_all(True)
        self._toast_face.set_visible(False)
        self._toast_face.put(self._toast, 16 * self._s, 12 * self._s)

        # drag-over drop-zone face (only meaningful on the resting pill)
        self._drag_zone = DropZone(theme, s=self._s)
        self._drag_zone.set_no_show_all(True)
        self._drag_zone.set_visible(False)

        layout = Overlay(
            child=center,
            overlays=[
                self._osd_face,
                self._surface_face,
                self._drag_zone,
                self._toast_face,
            ],
        )
        layout.set_hexpand(True)
        layout.set_vexpand(True)

        self._body.add(layout)
        self.add(self._body)

        # --- clock ------------------------------------------------------------
        # fabric DateTime drives the rest-face clock (Clock.qml equivalent);
        # _time_fmt() reflects the time12h / clockSeconds flags.
        self._refresh_clock()

        # --- OSD + drop subsystems (mixed in) ----------------------------------
        self._init_osd_services()
        self._init_drop()

        # --- reactivity -------------------------------------------------------
        flags.connect("notify::ui-scale", self._on_ui_scale_changed)
        flags.connect("notify::time12h", lambda *_a: self._refresh_clock())
        flags.connect("notify::clock-seconds", lambda *_a: self._refresh_clock())
        flags.connect("notify::main-display", self._on_main_display_changed)
        notifs.connect("changed", self._on_notifs_changed)
        self._toast_count = len(notifs.popups)

        self._build_rest_face()
        self._apply_rest()
        self._report()

    # --- events ---------------------------------------------------------------

    def _on_enter(self, _widget, _event):
        if self.state in ("surface", "dragover", "toast"):
            return False
        self._hovering = True
        self._cancel_grace()
        # nothing to show in the glance without tray items — stay at rest
        if self.state == "rest" and self.tray.empty:
            self._hovering = False
            return False
        if self.state == "rest":
            self._morph_to("hover" if not self._pinned else "pinned")
        return False

    def _on_leave(self, _widget, _event):
        if self.state in ("surface", "dragover", "toast"):
            return False
        self._hovering = False
        self._start_grace()
        return False

    def _on_press(self, _widget, _event):
        if self.state == "toast":
            # the toast row itself consumes the press (dismisses the popup); a
            # press on the pill body around it retires the whole toast the same way
            if notifs.popups:
                notifs.remove_popup(notifs.popups[-1])
            return False
        if self.state == "surface":
            if _time.monotonic() < self._drop_cooldown_until:
                return False
            self._close_surface()
            return False
        if self.state == "dragover":
            return False
        self._pinned = not self._pinned
        self._cancel_grace()
        if not self._pinned and notifs.popups:
            # unpinning with a toast waiting hands the pill to the toast
            self._morph_to("toast")
        elif self._pinned and self.tray.empty:
            # nothing to pin to without tray items
            self._pinned = False
            self._morph_to("rest")
        else:
            self._morph_to("pinned" if self._pinned else "rest")
        return False

    # --- surfaces --------------------------------------------------------------

    def open_surface(self, kind: str = "launcher"):
        """Morph to a major surface and hand it keyboard focus."""
        surf = self._surfaces.get(kind)
        if surf is None:
            return
        if self.state == "surface":
            if self._surface_kind == kind:
                search = getattr(surf, "search", None)
                if search is not None:
                    search.focus()
            else:
                self._swap_surface(kind)
            return
        self._cancel_osd_hide()
        if self._osd_face.get_visible():
            self._osd_face.set_visible(False)
            self._osd_face.set_opacity(0.0)
        self._cancel_grace()
        self._hovering = False
        self._pinned = False
        self._surface_kind = kind
        surf.open()
        self._morph_to("surface")
        # keyboard travels after the window has regrown to surface size so the
        # compositor grants the exclusive grab to this layer surface
        GLib.idle_add(self._say_surface, True)

    def _swap_surface(self, kind: str):
        """Switch the open surface while already morphed (same body size)."""
        self._surface_kind = kind
        for name, surf in self._surfaces.items():
            surf.set_visible(name == kind)
        self._surfaces[kind].open()
        self._retarget_surface()

    def toggle_launcher(self):
        """Flip the launcher surface open/closed (keybind + IPC entry point)."""
        if self.state == "surface" and self._surface_kind == "launcher":
            self._close_surface()
        else:
            self.open_surface("launcher")

    def toggle_clipboard(self):
        """Flip the clipboard surface open/closed (keybind + IPC entry point)."""
        if self.state == "surface" and self._surface_kind == "clipboard":
            self._close_surface()
        else:
            self.open_surface("clipboard")

    def _close_surface(self):
        if self.state != "surface":
            return
        self._surface_face.set_visible(False)
        self._surface_face.set_opacity(0.0)
        self._say_surface(False)
        if notifs.popups:
            self._toast.set_notif(notifs.popups[-1])
            self._toast.set_live(True)
            self._morph_to("toast")
        else:
            self._morph_to("rest")

    def toggle_link(self):
        """Flip the inbox surface open/closed (keybind + IPC entry point)."""
        if self.state == "surface" and self._surface_kind == "link":
            self._close_surface()
        else:
            self.open_surface("link")

    def toggle_emoji(self):
        """Flip the emoji picker open/closed."""
        if self.state == "surface" and self._surface_kind == "emoji":
            self._close_surface()
        else:
            self.open_surface("emoji")

    def toggle_wallpaper(self):
        """Flip the wallpaper switcher open/closed (keybind + IPC entry point)."""
        if self.state == "surface" and self._surface_kind == "wallpaper":
            self._close_surface()
        else:
            self.open_surface("wallpaper")

    def toggle_power(self):
        """Flip the power menu open/closed (keybind + IPC entry point)."""
        if self.state == "surface" and self._surface_kind == "power":
            self._close_surface()
        else:
            self.open_surface("power")

    def toggle_wifi(self):
        """Flip the wifi surface open/closed (keybind + IPC entry point)."""
        if self.state == "surface" and self._surface_kind == "wifi":
            self._close_surface()
        else:
            self.open_surface("wifi")

    def toggle_bt(self):
        """Flip the bluetooth surface open/closed (keybind + IPC entry point)."""
        if self.state == "surface" and self._surface_kind == "bt":
            self._close_surface()
        else:
            self.open_surface("bt")

    def _retarget_surface(self):
        """Surface content grew/shrunk while open — settle the body on the new size."""
        if self.state == "surface":
            self._morph_to("surface", duration_ms=FAST, reveal=False)

    # --- toast reactivity --------------------------------------------------

    def _on_notifs_changed(self, *_a):
        popups = notifs.popups
        p = len(popups)
        prev_count = self._toast_count
        self._toast_count = p
        if not popups:
            self._toast.set_live(False)
            if self.state == "toast":
                self._retire_toast()
            return
        self._toast.set_notif(popups[-1])
        self._toast.set_live(True)
        # toast yields to an open surface, drag, OSD or a held (pinned) pill
        if self.state in ("surface", "dragover", "osd") or self._pinned:
            return
        if self.state != "toast":
            self._cancel_grace()
            self._hovering = False
            # Show the toast face immediately (invisible) so GTK has one full
            # frame to realize, allocate, and measure all the label widgets.
            # After 16ms (one frame), content_height() reads real post-layout
            # sizes and we morph to the correct height with no initial jump.
            self._toast_face.set_visible(True)
            self._toast_face.set_opacity(0.0)

            def _do_morph():
                self._morph_to("toast")
                return GLib.SOURCE_REMOVE

            GLib.timeout_add(32, _do_morph)
        elif p != prev_count:

            def _retarget_toast():
                self._morph_to("toast", reveal=False)
                return GLib.SOURCE_REMOVE

            GLib.idle_add(_retarget_toast)

    def _retire_toast(self):
        """Toast expired/dismissed — fade the toast face away, then shrink the pill."""
        if self._toast_fade_out is not None:
            self._toast_fade_out.stop()
        target = "pinned" if self._pinned else "hover" if self._hovering else "rest"

        def fade(_t, k):
            self._toast_face.set_opacity(max(0.0, min(1.0, 1.0 - k)))
            if _t >= 1.0:
                self._toast_fade_out = None
                if self.state == "toast" and not notifs.popups:
                    self._morph_to(target)

        self._toast_fade_out = tween(STANDARD, fade)

    def _say_surface(self, open_: bool):
        if self._on_surface_state:
            self._on_surface_state(open_)

    # --- states -----------------------------------------------------------------

    def _morph_to(self, state: str, duration_ms: int = MORPH, reveal: bool = True):
        self.state = state
        target_w, target_h = self._target_size(state)

        if state == "osd":
            self._osd_face.set_visible(True)
            self._fade_osd(1.0)
            self.rest_face.set_visible(False)
            if self._toast_face.get_visible():
                self._toast_face.set_visible(False)
                self._toast_face.set_opacity(0.0)
        elif state == "dragover":
            if self._osd_face.get_visible():
                self._osd_face.set_visible(False)
                self._osd_face.set_opacity(0.0)
            if self._toast_face.get_visible():
                self._toast_face.set_visible(False)
                self._toast_face.set_opacity(0.0)
            self._drag_zone.set_visible(True)
            self._drag_zone.set_opacity(1.0)
            self.rest_face.set_visible(False)
            self.glance.set_visible(False)
            self.glance.set_opacity(0.0)
        elif state == "surface":
            self._surface_face.set_visible(True)
            # open morphs gate their content behind a post-morph fade (the
            # 360x332 surfaces dwarf the 38px rest body); retargets keep it
            # visible so paging/filtering never blinks the whole face
            self._surface_face.set_opacity(0.0 if reveal else 1.0)
            for name, surf in self._surfaces.items():
                surf.set_visible(name == self._surface_kind)
            self.rest_face.set_visible(False)
            self.glance.set_visible(False)
            self.glance.set_opacity(0.0)
            if self._toast_face.get_visible():
                self._toast_face.set_visible(False)
                self._toast_face.set_opacity(0.0)
        elif state == "toast":
            if self._osd_face.get_visible():
                self._fade_osd(0.0)
            if self._surface_face.get_visible():
                self._surface_face.set_visible(False)
                self._surface_face.set_opacity(0.0)
            if self._drag_zone.get_visible():
                self._drag_zone.set_visible(False)
                self._drag_zone.set_opacity(0.0)
            self._toast_face.set_visible(True)
            # opacity is driven from step() below so the toast cross-fades in
            # WITH the growing pill instead of popping out of the 38px rest
            # body at full size (content overflows until the pill is ~toast-sized)
            self._toast_face.set_opacity(0.0 if reveal else 1.0)
            self.rest_face.set_visible(False)
            self.glance.set_visible(False)
            self.glance.set_opacity(0.0)
        else:
            if self._osd_face.get_visible():
                self._fade_osd(0.0)
            if self._surface_face.get_visible():
                self._surface_face.set_visible(False)
                self._surface_face.set_opacity(0.0)
            if self._drag_zone.get_visible():
                self._drag_zone.set_visible(False)
                self._drag_zone.set_opacity(0.0)
            if self._toast_face.get_visible():
                self._toast_face.set_visible(False)
                self._toast_face.set_opacity(0.0)
            self.rest_face.set_visible(True)

        if self._tween is not None:
            self._tween.stop()
        if self._toast_reveal is not None:
            self._toast_reveal.stop()
            self._toast_reveal = None
        if self._surface_reveal is not None:
            self._surface_reveal.stop()
            self._surface_reveal = None
        if self._toast_fade_out is not None:
            self._toast_fade_out.stop()
            self._toast_fade_out = None
        start_w, start_h = self._body.get_size_request()

        def step(_t, k):
            self._morph_closeness = k
            # only animate an axis that actually moves; an un-changed axis must
            # stay pinned at target or the out-back ease overshoots it (a
            # height-only retarget would briefly widen the pill + reflow every
            # label). Values are ints so the equality is exact here.
            w = target_w if start_w == target_w else int(lerp(start_w, target_w, k))
            h = target_h if start_h == target_h else int(lerp(start_h, target_h, k))
            self._apply_size(w, h)
            if self.state == "osd":
                return
            face_on = self.state in ("hover", "pinned")
            # glow the glance into view as the pill settles (closeness ^ 1.2;
            # never while returning from an OSD flash back to rest)
            self.glance.set_opacity(max(0.0, min(1.0, k**1.2)) if face_on else 0.0)
            self.glance.set_visible(k > 0.1 and face_on)
            # rest/glance faces keep their own instantaneous visibility rules
            if _t >= 1.0 and reveal:
                # the toast content (310+16 wide, ~37 tall) only fits inside the
                # pill once the body is ~318 wide / ~49 tall — nearly at the end
                # of a toast morph. Disclose it with a short fade on completion
                # so nothing ever overhangs the still-growing body outline.
                if self.state == "toast" and self._toast_reveal is None:

                    def reveal_toast(_t, rk):
                        self._toast_face.set_opacity(max(0.0, min(1.0, rk)))

                    self._toast_reveal = tween(STANDARD, reveal_toast)
                elif self.state == "surface" and self._surface_reveal is None:

                    def reveal_surface(_t, rk):
                        self._surface_face.set_opacity(max(0.0, min(1.0, rk)))

                    self._surface_reveal = tween(STANDARD, reveal_surface)

        self._tween = tween(duration_ms, step)

    def _cancel_grace(self):
        if self._grace is not None:
            GLib.source_remove(self._grace)
            self._grace = None

    def _start_grace(self):
        self._cancel_grace()

        def retract():
            self._grace = None
            if self._hovering or self._pinned:
                return GLib.SOURCE_REMOVE
            # wait for the morph to settle before truly retracting (morphCloseness 0.95)
            if self._morph_closeness < 0.95:
                self._start_grace()
                return GLib.SOURCE_REMOVE
            if notifs.popups and self.state == "toast":
                return GLib.SOURCE_REMOVE
            self._morph_to("rest")
            return GLib.SOURCE_REMOVE

        self._grace = GLib.timeout_add(300, retract)

    # --- geometry ------------------------------------------------------------

    def _target_size(self, state: str):
        if state == "rest":
            return self.rest_w, self.rest_h
        if state == "dragover":
            return int(300 * self._s), int(126 * self._s)
        if state == "surface":
            surf = self._surfaces.get(self._surface_kind)
            if hasattr(surf, "surface_size"):
                return surf.surface_size()
            return int(360 * self._s), int(332 * self._s)
        if state == "toast":
            return int(342 * self._s), self._toast_body_h()
        if state == "osd":
            if self._osd_kind == "track":
                return int(344 * self._s), int(64 * self._s)
            if self._osd_kind != "ws":
                return int(248 * self._s), int(44 * self._s)
            ws_w = self.ws_osd.strip_width()
            return (
                max(int(120 * self._s), ws_w + int(40 * self._s)),
                int(44 * self._s),
            )
        return max(self.rest_w, self.glance_w), self.hover_h

    def _toast_body_h(self) -> int:
        return self._toast.content_height() + int(24 * self._s)

    def _measure_glance(self) -> int:
        """Glance width = tray natural width + hover padding (min rest width)."""
        tray_w = 0
        try:
            tray_w = self.tray.get_preferred_width()[-1] if self.tray else 0
        except Exception:
            tray_w = 0
        return max(int(self.rest_w), int(tray_w) + 2 * _HOVER_PAD * int(self._s))

    def _reflow_glance(self, *_a):
        """Re-measure the glance after tray items come/go; if hovered/pinned,
        morph the pill to the new width in place. If the tray emptied, collapse
        the glance back to rest (nothing left to show)."""
        self.glance_w = self._measure_glance()
        if self.state in ("hover", "pinned") and self.tray.empty:
            self._hovering = False
            self._pinned = False
            self._morph_to("rest")
            return
        if self.state in ("hover", "pinned"):
            self._morph_to(self.state)
        else:
            self._apply_size(self.rest_w, self.rest_h)

    def _apply_rest(self):
        self.rest_w = int(_REST_W * self._s)
        self.rest_h = int(_REST_H * self._s)
        self.hover_h = int(_HOVER_H * self._s)
        self.glance_w = self._measure_glance()
        if self.state != "pinned":
            self._apply_size(self.rest_w, self.rest_h)
        else:
            self._apply_size(max(self.rest_w, self.glance_w), self.hover_h)

    def _apply_size(self, w: int, h: int):
        self._body.set_size_request(w, h)
        self.set_size_request(w + 1, h + 1)
        self._report()

    def _time_fmt(self) -> str:
        if flags.time12h:
            core = "%-I:%M"
            return (core + ":%S" if flags.clock_seconds else core) + " %p"
        return "%H:%M:%S" if flags.clock_seconds else "%H:%M"

    def _refresh_clock(self, *_a, **_k):
        if self._rest_time is not None:
            self._rest_time.formatters = self._time_fmt()
            self._rest_time.do_update_label()
        for widget, fmt in (
            (self._rest_date, "%a %-d %b"),
            (self._rest_weekday, "%a"),
        ):
            if widget is not None:
                widget.formatters = fmt
                widget.do_update_label()

    # --- rest-face display modes (ukishima restRow) -------------------------

    def _on_main_display_changed(self, *_a, **_k):
        self._build_rest_face()
        self._apply_rest()
        self._report()

    def _build_rest_face(self):
        for child in tuple(self.rest_face.get_children()):
            self.rest_face.remove(child)

        mode = flags.main_display
        attached = mode == "attached"

        self._rest_time = self._make_clock()
        self.rest_face.add(self._rest_time)

        if mode == "classic":
            self._rest_date = DateTime(
                name="date",
                formatters="%a %-d %b",
                style_classes=["clock-meta"],
            )
            self.rest_face.pack_start(self._rest_date, False, False, 0)
            self.rest_face.reorder_child(self._rest_date, 0)

        elif mode == "system":
            self._rest_weekday = DateTime(
                name="weekday",
                formatters="%a",
                style_classes=["clock-meta"],
            )
            self.rest_face.pack_start(self._rest_weekday, False, False, 0)
            self.rest_face.reorder_child(self._rest_weekday, 0)

            self._rest_ws = Label(
                label=str(getattr(self.ws_osd, "_active_workspace", "") or ""),
                style_classes=["clock-ws"],
            )
            self.rest_face.add(self._rest_ws)

            self._rest_kb = Label(
                label=self._kb_layout_code(),
                style_classes=["clock-meta"],
            )
            self.rest_face.add(self._rest_kb)
            if not getattr(self, "_kb_linked", False):
                from ..services.keyboard_layout import KeyboardLayout

                keyboard = KeyboardLayout.get_initial()
                keyboard.connect(
                    "layout_changed",
                    lambda *_a: self._refresh_kb_readout(),
                )
                self._kb_linked = True

            self._rest_battery = Label(
                label=self._battery_label(),
                style_classes=["clock-battery"],
            )
            self.rest_face.add(self._rest_battery)
            self._start_system_tick()

        # attached (ukishima stripBar) squares the top corners so the pill
        # reads as a notch fused to the screen's edge
        body_classes = self._body.get_style_context().list_classes()
        if attached and "pill-attached" not in body_classes:
            self._body.get_style_context().add_class("pill-attached")
        elif not attached and "pill-attached" in body_classes:
            self._body.get_style_context().remove_class("pill-attached")

    def _make_clock(self):
        return DateTime(
            name="time",
            formatters=self._time_fmt(),
            style_classes=["clock-time"],
            h_expand=False,
        )

    def _kb_layout_code(self) -> str:
        try:
            from ..services.keyboard_layout import KeyboardLayout

            return KeyboardLayout.get_initial().code
        except Exception:
            return ""

    def _refresh_kb_readout(self):
        if self._rest_kb is not None:
            self._rest_kb.set_label(self._kb_layout_code())

    def _battery_label(self) -> str:
        battery = getattr(self, "_battery", None)
        if battery is None:
            return ""
        if battery.charging:
            return f"{battery.percent}%"
        return f"{battery.percent}%"

    def _start_system_tick(self):
        if self._rest_system_clock is not None:
            return
        self._rest_system_clock = GLib.timeout_add(1000, self._tick_system)

    def _tick_system(self) -> bool:
        if flags.main_display != "system":
            self._rest_system_clock = None
            return GLib.SOURCE_REMOVE
        if self._rest_ws is not None:
            self._rest_ws.set_label(
                str(getattr(self.ws_osd, "_active_workspace", "") or "")
            )
        if self._rest_battery is not None:
            self._rest_battery.set_label(self._battery_label())
        return GLib.SOURCE_CONTINUE

    def refresh_theme(self, *_a):
        """Re-apply stylesheet-derived colouring after a palette reload.

        Cheap path: the whole island + surfaces restyle through the regenerated
        ``colors.css`` when the app stylesheet is re-applied; we only need to
        force a redraw so GTK re-reads the new @define-color tokens.
        """
        self.queue_resize()
        self.queue_draw()
        for child in (
            getattr(self, "audio_osd", None),
            getattr(self, "brightness_osd", None),
            getattr(self, "lock_key_osd", None),
            getattr(self, "keyboard_osd", None),
            getattr(self, "battery_osd", None),
            getattr(self, "player_osd", None),
            getattr(self, "_launcher", None),
            getattr(self, "_clipboard", None),
            getattr(self, "_wallpaper", None),
            getattr(self, "_power", None),
            getattr(self, "_wifi", None),
            getattr(self, "_bt", None),
            getattr(self, "_drag_zone", None),
            getattr(self, "_link", None),
            getattr(self, "_toast", None),
        ):
            if child is not None and hasattr(child, "queue_draw"):
                child.queue_draw()

    def _on_ui_scale_changed(self, *_a):
        self._s = flags.ui_scale or 1.0
        self.audio_osd.set_scale(self._s)
        self.brightness_osd.set_scale(self._s)
        self.lock_key_osd.set_scale(self._s)
        self.keyboard_osd.set_scale(self._s)
        self.battery_osd.set_scale(self._s)
        self.player_osd.set_scale(self._s)
        if self.tray is not None:
            self.tray.set_scale(self._s)
            self._reflow_glance()
        self._launcher.set_scale(self._s)
        self._clipboard.set_scale(self._s)
        self._wallpaper.set_scale(self._s)
        self._power.set_scale(self._s)
        self._wifi.set_scale(self._s)
        self._bt.set_scale(self._s)
        self._drag_zone.set_scale(self._s)
        self._link.set_scale(self._s)
        self._toast.set_scale(self._s)
        self._apply_rest()
        if self.state == "surface" and self._surface_kind in ("link", "wifi", "bt"):
            self._morph_to("surface")
        elif self.state == "toast":
            self._morph_to("toast")

    def _report(self):
        if self._on_geometry:
            w, h = self._body.get_size_request()
            self._on_geometry(w, h)
