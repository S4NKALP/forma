"""Power surface
A row of themed session icons split by a hairline into a safe group
(lock, logout, sleep; fire on tap) and a destructive group (restart,
shutdown; press-and-hold). Holding a destructive tile ramps a bottom-up heat
fill; releasing early drains it, so a stray click can never reboot the machine.

Tiles are plain GTK buttons: the ``-symbolic`` icon is a ``Gtk.Image`` tinted
through CSS ``color`` (same family the clipboard/launcher use), the rounded
body/border come from CSS classes, the heat fill is a bottom-anchored box with
a ``linear-gradient`` background whose height tweens 0→full. No custom canvas
drawing, which also means native button events (hover, press, release, focus)
just work.

Keyboard navigation mirrors the pointer: Left/Right slide the focus across the
tiles and the wheel cycles them the same way, wrapping at the ends; the focused
tile lights exactly like a hovered one. Return/Space fires a safe tile at once;
on a destructive tile a single press only arms it (the heat fill primes
part-way) and a second press confirms — the keyboard path can never reboot on
one keystroke either. Escape closes the surface (shell.qml route).

Fixed 330x150·s, margins 15/17/17/14.
"""

import shutil
import subprocess
from collections.abc import Callable
from typing import Optional

from fabric.utils import Gdk, GLib, logger
from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.image import Image
from fabric.widgets.label import Label
from fabric.widgets.overlay import Overlay

from core.config import hypr_path
from core.motion import FAST, STANDARD, lerp, tween
from core.theme import Theme

_W = 330
_H = 150
_MT = 15
_MLR = 17
_MB = 14

_HDR = 22
_TILE = 50
_TILE_R = 13
_SPACING = 12
_HAIR_H = 26

_HEAT_MS = 1100
_DRAIN_MS = 180
_ARM_MS = 2000
_PRIMED = 0.55

_ICON = 22

_ENTER_KEYS = (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_ISO_Enter, Gdk.KEY_space)
_LEFT_KEYS = (Gdk.KEY_Left, Gdk.KEY_KP_Left)
_RIGHT_KEYS = (Gdk.KEY_Right, Gdk.KEY_KP_Right)

# Theme `-symbolic` icon names (monochrome, recolored to the tile state; same
# family the clipboard/launcher use for their dismiss/trash glyphs).
_ACTION_ICONS = {
    "lock": "system-lock-screen-symbolic",
    "logout": "system-log-out-symbolic",
    "suspend": "system-suspend-symbolic",
    "reboot": "system-reboot-symbolic",
    "shutdown": "system-shutdown-symbolic",
}

_SPLIT_AFTER = 2


def _lock_argv() -> list[str]:
    script = hypr_path("scripts", "lock.sh")
    if script.exists():
        return [str(script)]
    if shutil.which("hyprlock"):
        return ["hyprlock"]
    return []


_ACTIONS: tuple[dict, ...] = (
    {
        "key": "lock",
        "label": "Lock",
        "confirm": False,
        "argv": _lock_argv(),
    },
    {
        "key": "logout",
        "label": "Logout",
        "confirm": True,
        "argv": ["hyprctl", "dispatch", "exit"],
    },
    {
        "key": "suspend",
        "label": "Sleep",
        "confirm": False,
        "argv": ["systemctl", "suspend"],
    },
    {
        "key": "reboot",
        "label": "Restart",
        "confirm": True,
        "argv": ["systemctl", "reboot"],
    },
    {
        "key": "shutdown",
        "label": "Shutdown",
        "confirm": True,
        "argv": ["systemctl", "poweroff"],
    },
)


# --- color helpers ----------------------------------------------------------------


def _parse_color(c: str) -> tuple[float, float, float, float]:
    """'#rrggbb' or 'rgba(r,g,b,a)' → (r, g, b, a), each 0..1."""
    if c.startswith("#"):
        return (
            int(c[1:3], 16) / 255,
            int(c[3:5], 16) / 255,
            int(c[5:7], 16) / 255,
            1.0,
        )
    nums = c.replace("rgba(", "").replace(")", "").split(",")
    return (
        float(nums[0]) / 255,
        float(nums[1]) / 255,
        float(nums[2]) / 255,
        float(nums[3]) if len(nums) > 3 else 1.0,
    )


def _rgba(c: str, alpha: float) -> str:
    """'#hex' → 'rgba(r,g,b,a)' with a forced alpha, for CSS gradient stops."""
    r, g, b, _ = _parse_color(c)
    return f"rgba({int(r * 255)},{int(g * 255)},{int(b * 255)},{alpha})"


def _mix_style(a: str, b: str, t: float) -> str:
    """Linear mix of two CSS colors (RGB *and* alpha) → rgba() string."""
    ar, ag, ab, aa = _parse_color(a)
    br, bg, bb, ba = _parse_color(b)
    return (
        f"rgba({int(lerp(ar, br, t) * 255)},{int(lerp(ag, bg, t) * 255)},"
        f"{int(lerp(ab, bb, t) * 255)},{round(lerp(aa, ba, t), 4)})"
    )


# --- one tile ---------------------------------------------------------------------


class _PowerTile(Button):
    """A single 50x50·s session tile:

    - a ``Gtk.Button`` with CSS classes for the rounded body/border (``.power-tile``,
      ``.focused`` while lit);
    - a ``Gtk.Image`` rendering the themed ``-symbolic`` icon, tinted per state via
      CSS ``color`` (flameCore while holding, accent when lit, iconDim idle);
    - a bottom-anchored ``Box`` whose ``linear-gradient`` heat fill tweens its
      height 0→full while holding a destructive tile.

    Native button events give us hover/press/release for free; the only manual
    wiring is relaying them to the owning surface for the bottom readout and the
    heat hold.
    """

    def __init__(
        self,
        theme: Theme,
        s: float,
        action: dict,
        index: int,
        surface: Power,
    ):
        self._theme = theme
        self._s = s
        self.action = action
        self.index = index
        self._surface = surface
        self._hold = 0.0
        self._pressed = False
        self._hold_t: Optional = None

        self._icon = Image(
            icon_name=_ACTION_ICONS[action["key"]],
            icon_size=int(_ICON * s),
            h_align="center",
            v_align="center",
            style=f"color: {theme.icon_dim};",
        )
        self._heat = Box(
            orientation="h",
            h_align="fill",
            v_align="end",
            h_expand=True,
            style=self._heat_style(theme, s),
        )
        self._heat.set_size_request(-1, 0)
        body = Overlay(
            child=self._icon,
            overlays=[self._heat],
            name="power-tile-body",
        )
        super().__init__(
            child=body,
            name="power-tile-button",
            style=self._style(theme, s),
        )
        self.add_style_class("power-tile")
        self.set_can_focus(False)
        self.set_focus_on_click(False)
        self.set_size_request(int(_TILE * s), int(_TILE * s))

        self.connect("enter-notify-event", self._on_enter)
        self.connect("leave-notify-event", self._on_leave)
        self.connect("button-press-event", self._on_press)
        self.connect("button-release-event", self._on_release)

    # --- styling ---------------------------------------------------------------

    def _style(self, theme: Theme, s: float) -> str:
        r = int(_TILE_R * s)
        hover = _mix_style(theme.frame_bg, theme.cream, 0.08)
        return f""".power-tile {{
  background-color: transparent;
  border: 1px solid {theme.border};
  border-bottom: none;
  border-radius: {r}px;
  box-shadow: none;
  padding: 0;
}}
.power-tile:hover, .power-tile.focused {{
  background-color: {hover};
  border-color: {theme.frame_border};
  border-bottom-color: transparent;
}}
.power-tile image {{
  -gtk-icon-shadow: none;
}}
"""

    def _heat_style(self, theme: Theme, s: float) -> str:
        r = int(_TILE_R * s)
        return f"""background-image: linear-gradient(to top,
  {_rgba(theme.verm, 0.7)}, {_rgba(theme.verm_lit, 0.15)} );
border-radius: 0 0 {r}px {r}px;"""

    # --- state -----------------------------------------------------------------

    def lit(self) -> bool:
        return (
            self._surface._hovered_key == self.action["key"]
            or self._surface._focus_index == self.index
        )

    def holding(self) -> bool:
        return self._hold > 0.001

    def set_lit(self, lit: bool):
        if lit:
            self.add_style_class("focused")
        else:
            self.remove_style_class("focused")
        self._recolor()

    def _recolor(self):
        theme = self._theme
        if self.holding():
            color = theme.flame_core
        elif self.lit():
            color = theme.verm_lit if self.action["confirm"] else theme.cream
        else:
            color = theme.icon_dim
        self._icon.set_style(f"color: {color};")

    def start_primed(self):
        self._hold_to(_PRIMED, STANDARD)

    def start_hold(self):
        self._hold_to(1.0, _HEAT_MS, self._on_full)

    def drain(self):
        self._hold_to(0.0, _DRAIN_MS)

    def cancel_all(self):
        self._stop_hold()
        self._hold = 0.0
        self._heat.set_size_request(-1, 0)
        self._pressed = False
        self._recolor()

    # --- heat fill ---------------------------------------------------------------

    def _hold_to(self, to: float, ms: int, on_done: Callable | None = None):
        self._stop_hold()
        start = self._hold
        if abs(start - to) < 0.0001:
            self._hold = to
            self._set_heat_height()
            self._recolor()
            if on_done is not None:
                on_done()
            return
        done = False

        def step(_t, k):
            nonlocal done
            self._hold = lerp(start, to, k)
            if k >= 1.0:
                self._hold = to
                if not done:
                    done = True
                    if on_done is not None:
                        on_done()
            self._set_heat_height()
            self._recolor()

        self._hold_t = tween(ms, step)

    def _set_heat_height(self):
        self._heat.set_size_request(-1, int(_TILE * self._s * self._hold))

    def _stop_hold(self):
        if self._hold_t is not None:
            self._hold_t.stop()
            self._hold_t = None

    def _on_full(self):
        self._surface._on_tile_confirmed(self)

    # --- pointer ---------------------------------------------------------------

    def _on_enter(self, _w, _ev):
        self._surface._on_tile_enter(self)
        return True

    def _on_leave(self, _w, _ev):
        self._surface._on_tile_leave(self)
        return True

    def _on_press(self, _w, ev):
        if ev.button != 1:
            return True
        self._surface._on_tile_press(self)
        return True

    def _on_release(self, _w, ev):
        if ev.button != 1:
            return True
        self._surface._on_tile_release(self)
        return True


# --- the surface -----------------------------------------------------------------


class Power(Box):
    """The power surface — a row of session tiles with a double-enter
    keyboard confirm for the destructive group."""

    def __init__(
        self,
        theme: Theme,
        s: float = 1.0,
        on_close: Callable[[], None] | None = None,
        on_size_change: Callable[[], None] | None = None,
        **kwargs,
    ):
        super().__init__(
            name="power-surface",
            orientation="v",
            style=self._pad_style(theme, s),
            **kwargs,
        )
        self._theme = theme
        self._s = s
        self._on_close = on_close
        self._on_size_change = on_size_change

        self._focus_index = -1
        self._hovered_key = ""
        self._key_down = False
        self._armed = -1  # keyboard-armed destructive tile, -1 none
        self._mouse_hold = -1  # mouse-held destructive tile, -1 none
        self._arm_source: int | None = None
        self._label_tween: Optional = None

        self.set_can_focus(True)
        self.connect("key-press-event", self._on_key)
        self.connect("key-release-event", self._on_key_release)
        self.connect("scroll-event", self._on_scroll)
        self._wheel_acc = 0.0

        # header: POWER
        self._title = Label(
            label="POWER",
            style=(
                f"color: {theme.subtle};"
                f"font-size: {max(9, int(10 * s))}px;"
                f"font-weight: 600;"
                f"letter-spacing: 1.6px;"
            ),
        )
        self._head = Box(orientation="h", spacing=0, v_align="center")
        self._head.set_size_request(-1, int(_HDR * s))
        self._head.add(self._title)
        self.add(self._head)

        # tiles row, split by a hairline after index `splitAfter`
        self._tiles: list[_PowerTile] = []
        self._hairline: Box | None = None
        self._tiles_row = Box(
            orientation="h",
            spacing=int(_SPACING * s),
            h_align="center",
            style=f"margin-top: {int(14 * s)}px;",
        )
        for i, action in enumerate(_ACTIONS):
            cell = Box(orientation="h", spacing=int(_SPACING * s))
            if i == _SPLIT_AFTER:
                self._hairline = Box(
                    size=(1, int(_HAIR_H * s)),
                    style=f"background: {theme.hair};",
                )
                cell.add(self._hairline)
            tile = _PowerTile(theme, s, action, i, self)
            self._tiles.append(tile)
            cell.add(tile)
            self._tiles_row.add(cell)
        self.add(self._tiles_row)

        # bottom readout (fades with FAST); sits under the tiles
        self._label = Label(
            label="",
            h_align="center",
            style=self._label_style(),
            opacity=0.0,
        )
        self.add(self._label)

        self.set_size_request(int(_W * s), int(_H * s))

    # --- scale / styling ---------------------------------------------------------

    def _pad_style(self, theme: Theme, s: float) -> str:
        return (
            f"padding-top: {int(_MT * s)}px;"
            f"padding-left: {int(_MLR * s)}px;"
            f"padding-right: {int(_MLR * s)}px;"
            f"padding-bottom: {int(_MB * s)}px;"
        )

    def _label_style(self, overrides: str = "") -> str:
        return (
            f"margin-top: {int(12 * self._s)}px;"
            f"color: {self._theme.subtle};"
            f"font-size: {max(9, int(11 * self._s))}px;"
            f"font-weight: 500;"
            f"letter-spacing: 0.4px;" + (overrides or "")
        )

    # --- open/close --------------------------------------------------------------

    def open(self):
        self._focus_index = -1
        self._hovered_key = ""
        self._key_down = False
        self._armed = -1
        self._mouse_hold = -1
        self._cancel_arm_timeout()
        for tile in self._tiles:
            tile.cancel_all()
        self._sync_lit()
        self._update_label()
        self.show()
        self.show_all()
        GLib.idle_add(self.grab_focus)

    def close(self):
        self._cancel_arm_timeout()
        self._armed = -1
        self._mouse_hold = -1
        self._key_down = False
        if self._label_tween is not None:
            self._label_tween.stop()
            self._label_tween = None
        for tile in self._tiles:
            tile.cancel_all()
        if self._on_close:
            self._on_close()

    def surface_size(self) -> tuple[int, int]:
        return int(_W * self._s), int(_H * self._s)

    def set_scale(self, s: float):
        self._s = s
        self.set_style(self._pad_style(self._theme, s))
        self.set_size_request(int(_W * s), int(_H * s))
        self._title.set_style(
            f"color: {self._theme.subtle};"
            f"font-size: {max(9, int(10 * s))}px;"
            f"font-weight: 600;"
            f"letter-spacing: 1.6px;"
        )
        self._head.set_size_request(-1, int(_HDR * s))
        self._tiles_row.set_spacing(int(_SPACING * s))
        self._tiles_row.set_style(f"margin-top: {int(14 * s)}px;")
        for tile in self._tiles:
            tile._s = s
            tile.set_size_request(int(_TILE * s), int(_TILE * s))
            tile.set_style(tile._style(self._theme, s))
            tile._icon.set_size_request(int(_ICON * s), int(_ICON * s))
            tile._icon.set_pixel_size(int(_ICON * s))
            tile._heat.set_style(tile._heat_style(self._theme, s))
            tile._set_heat_height()
            tile._recolor()
        if self._hairline is not None:
            self._hairline.set_size_request(1, int(_HAIR_H * s))
        if self._label_tween is not None:
            self._label_tween.stop()
            self._label_tween = None
        self._label.set_style(self._label_style())
        self._label.set_opacity(0.0)

    # --- keyboard ----------------------------------------------------------------

    def move(self, dir_: int):
        """Slide the focus across the tiles (+1 right / -1 left), wrapping
        around at the ends; releases any armed keyboard heat so focus never
        drags a primed tile with it."""
        self._key_down = False
        self._disarm_kb()
        n = len(_ACTIONS)
        if self._focus_index < 0:
            idx = 0 if dir_ > 0 else n - 1
        else:
            idx = (self._focus_index + dir_) % n
        self._focus_index = idx
        self._hovered_key = _ACTIONS[self._focus_index]["key"]
        self._sync_lit()
        self._update_label()

    def press_focused(self) -> bool:
        """Return/Space on the focused tile. A safe tile fires at once; on a
        destructive tile the first press arms it and the second confirms
        (double-enter). Returns True when a tile consumed the key."""
        if not (0 <= self._focus_index < len(_ACTIONS)):
            return False
        act = _ACTIONS[self._focus_index]
        if self._key_down:
            return True
        self._key_down = True
        if not act["confirm"]:
            self._fire(act)
            return True
        if self._armed == self._focus_index:
            self._fire(act)
        else:
            self._arm()
        return True

    def release_focused(self):
        self._key_down = False

    def _on_key(self, _w, ev):
        key = ev.keyval
        if key in (Gdk.KEY_Escape,):
            self.close()
            return True
        if key in _RIGHT_KEYS:
            self.move(1)
            return True
        if key in _LEFT_KEYS:
            self.move(-1)
            return True
        if key in _ENTER_KEYS:
            return self.press_focused()
        return False

    def _on_key_release(self, _w, ev):
        if ev.keyval in _ENTER_KEYS:
            self.release_focused()
            return True
        return False

    # --- wheel ------------------------------------------------------------------

    def _on_scroll(self, _w, event):
        """Wheel cycles the focus like wallpaper's strip: notch up/left steps -1,
        notch down/right steps +1, smooth/gesture scrolls accumulate deltas."""
        direction = event.direction
        if direction in (Gdk.ScrollDirection.UP, Gdk.ScrollDirection.LEFT):
            self.move(-1)
            return True
        if direction in (Gdk.ScrollDirection.DOWN, Gdk.ScrollDirection.RIGHT):
            self.move(1)
            return True
        if direction == Gdk.ScrollDirection.SMOOTH:
            _, dx, dy = event.get_scroll_deltas()
            delta = dx if abs(dx) > abs(dy) else dy
            self._wheel_acc += delta
            notches = int(self._wheel_acc)
            if notches != 0:
                self.move(notches)
                self._wheel_acc -= notches
        return True

    # --- destructive escalation ----------------------------------------------------

    def _arm(self):
        idx = self._focus_index
        self._armed = idx
        self._tiles[idx].start_primed()
        self._cancel_arm_timeout()
        self._arm_source = GLib.timeout_add(_ARM_MS, self._on_arm_timeout)
        self._update_label()

    def _on_arm_timeout(self):
        self._arm_source = None
        self._disarm_kb()
        return GLib.SOURCE_REMOVE

    def _disarm_kb(self):
        self._cancel_arm_timeout()
        idx = self._armed
        if idx < 0:
            return
        self._armed = -1
        if idx != self._mouse_hold:
            self._tiles[idx].drain()
        self._update_label()

    def _cancel_arm_timeout(self):
        if self._arm_source is not None:
            GLib.source_remove(self._arm_source)
            self._arm_source = None

    # --- pointer handlers ------------------------------------------------------------

    def _on_tile_enter(self, tile: _PowerTile):
        self._disarm_kb()
        self._hovered_key = tile.action["key"]
        self._sync_lit()
        self._update_label()

    def _on_tile_leave(self, tile: _PowerTile):
        if self._mouse_hold == tile.index:
            self._mouse_hold = -1
            tile.drain()
        if self._hovered_key == tile.action["key"]:
            self._hovered_key = ""
        self._sync_lit()
        self._update_label()

    def _on_tile_press(self, tile: _PowerTile):
        self._disarm_kb()
        tile._pressed = True
        if tile.action["confirm"]:
            self._mouse_hold = tile.index
            tile.start_hold()
            self._update_label()

    def _on_tile_release(self, tile: _PowerTile):
        was_pressed = tile._pressed
        tile._pressed = False
        if self._mouse_hold == tile.index:
            self._mouse_hold = -1
            tile.drain()
            self._update_label()
        elif was_pressed and not tile.action["confirm"]:
            self._fire(tile.action)

    def _on_tile_confirmed(self, tile: _PowerTile):
        if self._mouse_hold == tile.index:
            self._mouse_hold = -1
        self._fire(tile.action)

    # --- run / readout --------------------------------------------------------------

    def _fire(self, action: dict):
        self._cancel_arm_timeout()
        self._armed = -1
        self._mouse_hold = -1
        self._key_down = False
        argv = action.get("argv") or []
        if argv:
            try:
                subprocess.Popen([str(a) for a in argv], start_new_session=True)
            except OSError as exc:
                logger.warning("power: failed to launch %s: %s", argv, exc)
        self.close()

    def _sync_lit(self):
        for tile in self._tiles:
            tile.set_lit(tile.lit())

    def _update_label(self):
        act = None
        armed = False
        if self._armed >= 0:
            act = _ACTIONS[self._armed]
            armed = True
        elif self._mouse_hold >= 0:
            act = _ACTIONS[self._mouse_hold]
        elif self._hovered_key:
            act = next((a for a in _ACTIONS if a["key"] == self._hovered_key), None)
        if act is None:
            self._label.set_text("")
            self._fade_label(0.0)
            return
        if armed:
            text = act["label"] + " — press again"
        elif act["confirm"]:
            text = act["label"] + " — hold"
        else:
            text = act["label"]
        self._label.set_text(text)
        self._label.set_style(self._label_style())
        self._fade_label(1.0)

    def _fade_label(self, target: float):
        if self._label_tween is not None:
            self._label_tween.stop()
        cur = self._label.get_opacity()
        if abs(cur - target) < 0.01:
            self._label.set_opacity(target)
            return

        def step(_t, k):
            self._label.set_opacity(lerp(cur, target, k))

        self._label_tween = tween(FAST, step)
