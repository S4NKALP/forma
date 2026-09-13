"""
Hyprland is the source of truth. Seeds once from ``hyprctl devices -j`` (the
event socket only reports changes, never the current value), then follows
``activelayout`` events. Exposes:
  * ``layouts``  — the per-device ``layout`` field ("us,np" → ["us", "np"]).
  * ``current_layout`` — the active xkb short code ("us").
  * ``code``     — folded display code for the OSD / pill ("US").
  * ``layout_changed(layout)`` — emitted on any real switch (external or via
    :meth:`switch_to_next`).
``switch_to_next()`` cycles via ``hyprctl switchxkblayout all``.
"""

import json

from fabric.core.service import Property, Service, Signal
from fabric.hyprland.service import Hyprland
from fabric.utils import Gio, GLib, logger

HYPRCTL_BIN = "hyprctl"
DEFAULT_LAYOUTS = ["us", "np"]

# active_keymap human name → folded display code (ukishima KbLayout.qml map)
_SHORT = {
    "rus": "RU",
    "ukr": "UA",
    "bel": "BY",
    "kaz": "KZ",
    "german": "DE",
    "deutsch": "DE",
    "deu": "DE",
    "french": "FR",
    "fra": "FR",
    "spanish": "ES",
    "spa": "ES",
    "italian": "IT",
    "ita": "IT",
    "portugu": "PT",
    "por": "PT",
    "polish": "PL",
    "pol": "PL",
    "turkish": "TR",
    "tur": "TR",
    "arabic": "AR",
    "ara": "AR",
    "hebrew": "HE",
    "heb": "HE",
    "japanese": "JA",
    "jpn": "JA",
    "korean": "KO",
    "kor": "KO",
    "chinese": "ZH",
    "chi": "ZH",
    "hindi": "IN",
    "indian": "IN",
    "devanagari": "IN",
    "english": "US",
    "eng": "US",
    "us": "US",
    "british": "UK",
    "uk": "UK",
    "dvorak": "US",
    "colemak": "US",
}


class KeyboardLayout(Service):
    """Hyprland-backed active-keyboard-layout service (singleton)."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @classmethod
    def get_initial(cls) -> KeyboardLayout:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @staticmethod
    def short_code(full: str) -> str:
        """Fold an XKB short name or human keymap name into a display code."""
        f = str(full or "").lower()
        for key, code in _SHORT.items():
            if key in f:
                return code
        import re

        m = re.search(r"[a-z]{2,}", f)
        return (m.group(0).upper()[:2]) if m else "US"

    @Signal
    def layout_changed(self, layout: str) -> None:
        """Emitted when the active keyboard layout changes."""

    def __init__(self, **kwargs):
        if getattr(self, "_initialized", False):
            return
        super().__init__(**kwargs)
        self._initialized = True

        self._layouts: list[str] = []
        self._current_layout: str | None = None
        self._current_index: int = 0
        self._sync_pending: bool = False
        self._seeded_once: bool = False

        self._connect_hyprland_events()
        GLib.idle_add(self._seed)

    # --- hyprctl -----------------------------------------------------------

    def _hyprctl(self, *args, on_reply=None):
        argv = [HYPRCTL_BIN, *[str(arg) for arg in args]]
        try:
            proc = Gio.Subprocess.new(
                argv,
                Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE,
            )
        except GLib.Error as error:
            logger.error(f"[KeyboardLayout] Failed to run {argv}: {error}")
            if on_reply is not None:
                on_reply(None)
            return

        def on_done(process: Gio.Subprocess, task: Gio.Task):
            try:
                _, stdout, _ = process.communicate_utf8_finish(task)
            except GLib.Error:
                stdout = None
            if on_reply is not None:
                on_reply(stdout.strip() if stdout else None)

        proc.communicate_utf8_async(None, None, on_done)

    # --- seeding / sync ---------------------------------------------------

    def _seed(self):
        """Query Hyprland once for the configured layouts + active one."""
        if self._sync_pending:
            return False
        self._sync_pending = True

        def on_reply(stdout: str | None):
            self._sync_pending = False
            if not stdout:
                return
            try:
                devices = json.loads(stdout)
            except json.JSONDecodeError as error:
                logger.error(f"[KeyboardLayout] Failed to parse devices: {error}")
                return

            keyboard = self._pick_keyboard(devices.get("keyboards", []))
            if keyboard is None:
                return

            # layout = "us,np" (comma-joined xkb short codes); on the main
            # device this is the full configured rotation.
            raw_layouts = str(keyboard.get("layout") or "").split(",")
            new_layouts = [str(l).strip() for l in raw_layouts if str(l).strip()]
            if not new_layouts:
                new_layouts = list(DEFAULT_LAYOUTS)
            layouts_changed = new_layouts != self._layouts
            self._layouts = new_layouts

            index = 0
            raw_index = keyboard.get("active_layout_index")
            try:
                index = int(raw_index)
            except TypeError, ValueError:
                index = 0
            index = max(0, min(index, len(self._layouts) - 1))

            layout = self._layouts[index] if self._layouts else None
            boot = self._current_layout is None and not self._seeded_once
            changed = layouts_changed or layout != self._current_layout
            self._current_index = index
            self._current_layout = layout
            self.notify("current_layout", "code", "layouts")
            self._seeded_once = True
            if changed and not boot:
                self.emit("layout_changed", layout or "")

        self._hyprctl("-j", "devices", on_reply=on_reply)
        return False

    def _sync_from_hyprland(self):
        """Refresh active layout without disturbing configured rotation."""
        if self._sync_pending:
            return
        self._sync_pending = True

        def on_reply(stdout: str | None):
            self._sync_pending = False
            if not stdout:
                return
            try:
                devices = json.loads(stdout)
            except json.JSONDecodeError:
                return

            keyboard = self._pick_keyboard(devices.get("keyboards", []))
            if keyboard is None or not self._layouts:
                return

            index = 0
            try:
                index = int(keyboard.get("active_layout_index", 0))
            except TypeError, ValueError:
                index = 0
            index = max(0, min(index, len(self._layouts) - 1))
            if index == self._current_index:
                return
            self._current_index = index
            self._current_layout = self._layouts[index]
            self.notify("current_layout", "code", "layouts")
            self.emit("layout_changed", self._current_layout)

        self._hyprctl("-j", "devices", on_reply=on_reply)

    @staticmethod
    def _pick_keyboard(keyboards: list[dict]) -> dict | None:
        if not keyboards:
            return None
        for kb in keyboards:
            if kb.get("main"):
                return kb
        return keyboards[0]

    def _connect_hyprland_events(self):
        try:
            Hyprland().connect("event::activelayout", self._on_hyprland_layout_event)
        except Exception as error:
            logger.warning(
                f"[KeyboardLayout] Could not subscribe to Hyprland layout "
                f"events (external changes won't be tracked): {error}"
            )

    def _on_hyprland_layout_event(self, _hyprland, event):
        # payload: data=[keyboard name, layout keymap name]; the socket only
        # reports changes, so query hyprctl for the coherent index/layout.
        data = getattr(event, "data", None)
        if not data or len(data) < 2:
            return
        self._sync_from_hyprland()

    # --- public API ---------------------------------------------------------

    def switch_to_next(self) -> bool:
        if not self._layouts:
            logger.warning("[KeyboardLayout] No layouts configured, cannot cycle.")
            return False
        next_index = (self._current_index + 1) % len(self._layouts)
        self._hyprctl("switchxkblayout", "all", str(next_index))
        # hyprctl is done asynchronously; the activelayout event will land and
        # update state. Pre-notify so the OSD doesn't lag the keypress.
        self._current_index = next_index
        self._current_layout = self._layouts[next_index]
        self.notify("current_layout", "code", "layouts")
        self.emit("layout_changed", self._current_layout)
        return True

    @staticmethod
    def switch_keyboard_layout():
        KeyboardLayout.get_initial().switch_to_next()

    # --- properties ---------------------------------------------------------

    @Property(str, "readable", default_value="")
    def current_layout(self) -> str:
        return self._current_layout or ""

    @Property(str, "readable", default_value="")
    def code(self) -> str:
        return self.short_code(self._current_layout or "")

    @Property(list, "readable")
    def layouts(self) -> list[str]:
        return self._layouts

    @Property(int, "readable", default_value=0)
    def current_index(self) -> int:
        return self._current_index
