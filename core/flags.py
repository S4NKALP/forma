"""Persistent settings service for Forma."""

import os
import tempfile
from typing import Any

import tomlkit
from fabric import Service, Signal
from fabric.core.service import Property
from fabric.utils import Gio, GLib, GObject

from core.config import config_path, generate_default_config

_RELOAD_DELAY_MS = 100

_DEFAULTS: dict[str, Any] = {
    # ── General ──────────────────────────────────────────────────────────────
    "dnd": False,
    "reduceMotion": False,
    "autoHide": False,
    "pillOpacity": 1.0,
    "pillBlur": True,
    "unloadSec": 60,
    # ── Display ──────────────────────────────────────────────────────────────
    "time12h": True,
    "clockSeconds": False,
    "mainDisplay": "floating",
    "uiScale": 1.0,
    "topGap": 12,
    # ── Theme / Palette ──────────────────────────────────────────────────────
    "paletteMode": "dark",
    # ── Wallpaper ────────────────────────────────────────────────────────────
    "wallpaperDir": "",
    "wallpaperFit": "center",
    "randomScope": "",
    # ── Recording ────────────────────────────────────────────────────────────
    "recordCountdown": 3,
    "recordDir": "~/Videos/Recordings",
    "recordFps": 60,
    "recordQuality": "medium",
    "recordCursor": True,
    "recordMic": False,
    "recordDesktop": False,
    "recordClearedBefore": 0,
    # ── Colors (dynamic palette source — values from matugen / manual) ───────
    "color.foreground": "#dee4e1",
    "color.background": "#0e1513",
    "color.cursor": "#dee4e1",
    "color.primary": "#83d5c6",
    "color.onPrimary": "#003730",
    "color.secondary": "#b1ccc5",
    "color.onSecondary": "#1c3530",
    "color.tertiary": "#accae5",
    "color.onTertiary": "#133348",
    "color.surface": "#0e1513",
    "color.surfaceBright": "#343b39",
    "color.error": "#ffb4ab",
    "color.errorDim": "#000000",
    "color.onError": "#690005",
    "color.errorContainer": "#93000a",
    "color.outline": "#899390",
    "color.shadow": "#000000",
    "color.red": "#ffb595",
    "color.redDim": "#000000",
    "color.green": "#95d5a7",
    "color.greenDim": "#000000",
    "color.yellow": "#b8cf84",
    "color.yellowDim": "#000000",
    "color.blue": "#afc6ff",
    "color.blueDim": "#000000",
    "color.magenta": "#e4b7f3",
    "color.magentaDim": "#000000",
    "color.cyan": "#81d5cd",
    "color.cyanDim": "#000000",
    "color.white": "#82d3e0",
}


_WATCHED_PROPERTIES = {
    "main_display": ("mainDisplay", str, "floating"),
    "time12h": ("time12h", bool, True),
    "clock_seconds": ("clockSeconds", bool, False),
    "auto_hide": ("autoHide", bool, False),
    "palette_mode": ("paletteMode", str, "dark"),
    "ui_scale": ("uiScale", float, 1.0),
    "top_gap": ("topGap", int, 12),
    "reduce_motion": ("reduceMotion", bool, False),
}

# Pspec names to use with GObject.notify() — must match connect() strings
# elsewhere (main.py, pill.py, shell.py). Most are straightforward kebab
# but "time12h" is kept as-is for backwards compatibility.
_PROP_NAMES: dict[str, str] = {
    "main_display": "main-display",
    "time12h": "time12h",
    "clock_seconds": "clock-seconds",
    "auto_hide": "auto-hide",
    "palette_mode": "palette-mode",
    "ui_scale": "ui-scale",
    "top_gap": "top-gap",
    "reduce_motion": "reduce-motion",
}

# flat key → (ptype) for coercion in _set_flag
_KEY_TO_PTYPE: dict[str, type] = {
    _k: _p for _sl, (_k, _p, _d) in _WATCHED_PROPERTIES.items()
}

# flat key → pspec name for explicit notify from _set_flag
_KEY_TO_PROP: dict[str, str] = {
    _k: _PROP_NAMES[_sl] for _sl, (_k, _p, _d) in _WATCHED_PROPERTIES.items()
}


def _coerce(ptype: type, value: Any) -> Any:
    if ptype is bool:
        return bool(value)

    return ptype(value)


# GObject pspec types accepted by fabric's Property decorator
_PROP_TYPES = {
    bool: bool,
    int: int,
    float: float,
    str: str,
}

# pspec flags for the typed flags below: read-write + explicit notify so we can
# emit notify only when the value actually moved (the pre-pspec descriptors did
# exactly that; GObject's implicit notify would fire on every no-op set).
_RW_EXPLICIT = GObject.ParamFlags.READWRITE | GObject.ParamFlags.EXPLICIT_NOTIFY


_SECTION_PREFIX: dict[str, str] = {
    "colors": "color.",
}

# Which TOML section each flat key belongs to (empty = top-level).
_FLAT_TO_SECTION: dict[str, str] = {
    # ── general
    "dnd": "general",
    "reduceMotion": "general",
    "autoHide": "general",
    "pillOpacity": "general",
    "pillBlur": "general",
    "unloadSec": "general",
    # ── display
    "time12h": "display",
    "clockSeconds": "display",
    "mainDisplay": "display",
    "uiScale": "display",
    "topGap": "display",
    # ── theme
    "paletteMode": "theme",
    # ── wallpaper
    "wallpaperDir": "wallpaper",
    "wallpaperFit": "wallpaper",
    "randomScope": "wallpaper",
    # ── recording
    "recordCountdown": "recording",
    "recordDir": "recording",
    "recordFps": "recording",
    "recordQuality": "recording",
    "recordCursor": "recording",
    "recordMic": "recording",
    "recordDesktop": "recording",
    "recordClearedBefore": "recording",
}
# Auto-map color.* keys to [colors] section
for _k in _DEFAULTS:
    if _k.startswith("color."):
        _FLAT_TO_SECTION[_k] = "colors"
del _k


def _flatten_config(doc: tomlkit.TOMLDocument) -> dict[str, Any]:
    """Flatten TOML sections into dot-prefixed keys.

    ``[colors] foreground = "#..."`` → ``{"color.foreground": "#..."}``
    ``[display] time12h = true``    → ``{"time12h": true}``
    ``dnd = false`` (top-level)     → ``{"dnd": false}``
    """
    flat: dict[str, Any] = {}

    for key, value in doc.items():
        if hasattr(value, "items"):
            prefix = _SECTION_PREFIX.get(key, "")
            for sub_key, sub_val in value.items():
                flat[f"{prefix}{sub_key}"] = sub_val
        else:
            flat[key] = value

    return flat


def _unflatten_data(data: dict[str, Any]) -> tomlkit.TOMLDocument:
    """Rebuild a TOMLDocument with sections from flat dot-prefixed keys."""
    doc = tomlkit.document()

    section_tables: dict[str, tomlkit.TOMLTable] = {}

    for key, value in data.items():
        section = _FLAT_TO_SECTION.get(key, "")
        if section:
            if section not in section_tables:
                section_tables[section] = tomlkit.table()
                doc.add(section, section_tables[section])
            flat_key = key[len(_SECTION_PREFIX.get(section, "")) :]
            section_tables[section].add(flat_key, value)
        else:
            doc.add(key, value)

    return doc


class Flags(Service):
    """Persistent Forma settings with debounced atomic saves."""

    save_requested = Signal("save-requested")

    # old/new carry flat (dot-prefixed) config key maps; the GObject "object"
    # typecode passes them straight through (PYOBJECT). (The @Signal-decorator
    # form can't be used here: "from __future__ import annotations" turns the
    # param annotations into strings.) Mirrors Modus' on_config_change(old, new).
    config_changed = Signal(
        "config-changed",
        GObject.SignalFlags.RUN_FIRST,
        None,
        (GObject.TYPE_PYOBJECT, GObject.TYPE_PYOBJECT),
    )

    # ── typed, GObject-notified properties ─────────────────────────────────
    # fabric's @Property installs real pspecs, so notify::<pspec> signals
    # actually dispatch (bare python property() has no pspec → dead notify).

    @Property(str, _RW_EXPLICIT, "main-display", default_value="floating")
    def main_display(self) -> str:
        """floating | classic | system | attached."""
        mode = self._data.get("mainDisplay", "floating")
        if mode == "minimal":
            return "floating"
        if mode == "strip":
            return "attached"
        return mode

    @main_display.setter
    def main_display(self, value: str) -> None:
        self._set_flag("mainDisplay", value)

    @Property(bool, _RW_EXPLICIT, "time12h", default_value=True)
    def time12h(self) -> bool:
        """Use 12-hour clock."""
        return self._data.get("time12h", True)

    @time12h.setter
    def time12h(self, value: bool) -> None:
        self._set_flag("time12h", value)

    @Property(bool, _RW_EXPLICIT, "clock-seconds", default_value=False)
    def clock_seconds(self) -> bool:
        """Show seconds tick."""
        return self._data.get("clockSeconds", False)

    @clock_seconds.setter
    def clock_seconds(self, value: bool) -> None:
        self._set_flag("clockSeconds", value)

    @Property(bool, _RW_EXPLICIT, "auto-hide", default_value=False)
    def auto_hide(self) -> bool:
        """Hide pill when a window overlaps."""
        return self._data.get("autoHide", False)

    @auto_hide.setter
    def auto_hide(self, value: bool) -> None:
        self._set_flag("autoHide", value)

    @Property(str, _RW_EXPLICIT, "palette-mode", default_value="dark")
    def palette_mode(self) -> str:
        """dark | light | dynamic."""
        return self._data.get("paletteMode", "dark")

    @palette_mode.setter
    def palette_mode(self, value: str) -> None:
        self._set_flag("paletteMode", value)

    @Property(float, _RW_EXPLICIT, "ui-scale", default_value=1.0)
    def ui_scale(self) -> float:
        """Global scale factor."""
        return self._data.get("uiScale", 1.0)

    @ui_scale.setter
    def ui_scale(self, value: float) -> None:
        self._set_flag("uiScale", value)

    @Property(int, _RW_EXPLICIT, "top-gap", default_value=12)
    def top_gap(self) -> int:
        """Gap from top of screen (px)."""
        return self._data.get("topGap", 12)

    @top_gap.setter
    def top_gap(self, value: int) -> None:
        self._set_flag("topGap", value)

    @Property(bool, _RW_EXPLICIT, "reduce-motion", default_value=False)
    def reduce_motion(self) -> bool:
        """Scale all animation durations x0.4."""
        return self._data.get("reduceMotion", False)

    @reduce_motion.setter
    def reduce_motion(self, value: bool) -> None:
        self._set_flag("reduceMotion", value)

    def __init__(self, path=None):
        super().__init__()

        self._path = path or config_path()
        self._data: dict[str, Any] = {}
        self._config = tomlkit.document()
        self._last_external: dict[str, Any] = {}

        self._loaded = False
        self._save_debounce: int | None = None
        self._reload_debounce: int | None = None
        self._monitors: list[Gio.FileMonitor] = []

        generate_default_config()
        self._load()
        self._watch()

    # normalized setter shared by the GObject properties above. Notifies only
    # when the value actually moved (EXPLICIT_NOTIFY pspecs elsewhere).
    def _set_flag(self, key: str, value: Any) -> None:
        ptype = _KEY_TO_PTYPE.get(key)
        normalized = _coerce(ptype, value) if ptype is not None else value

        if self._data.get(key) == normalized:
            return

        self._data[key] = normalized
        self._dirty_key(key)
        self.notify(_KEY_TO_PROP.get(key, key))

    def _load(self):
        self._config = tomlkit.document()

        try:
            if self._path.exists():
                self._config = tomlkit.parse(self._path.read_text(encoding="utf-8"))
        except OSError, ValueError, tomlkit.exceptions.ParseError:
            self._config = tomlkit.document()

        self._data = _flatten_config(self._config)
        self._last_external = self._data.copy()

        for sl, (key, ptype, default) in _WATCHED_PROPERTIES.items():
            if key in self._data:
                try:
                    self._data[key] = _coerce(ptype, self._data[key])
                except TypeError, ValueError:
                    self._data[key] = default
            else:
                self._data[key] = default

        for key, default in _DEFAULTS.items():
            self._data.setdefault(key, default)

        self._loaded = True

    def _dirty_key(self, key: str):
        if key in _WATCHED_PROPERTIES or key in _DEFAULTS:
            self._schedule_save()

    def _schedule_save(self):
        if self._save_debounce is not None:
            GLib.source_remove(self._save_debounce)

        self._save_debounce = GLib.timeout_add(
            250,
            self._flush,
        )

    def _flush(self):
        self._save_debounce = None

        try:
            self._path.parent.mkdir(
                parents=True,
                exist_ok=True,
            )

            config = _unflatten_data(self._data)

            fd, tmp = tempfile.mkstemp(
                dir=self._path.parent,
                prefix=".config.",
                suffix=".tmp",
            )

            try:
                with os.fdopen(
                    fd,
                    "w",
                    encoding="utf-8",
                ) as file:
                    file.write(tomlkit.dumps(config))
                    file.flush()
                    os.fsync(file.fileno())

                os.replace(tmp, self._path)

            except Exception:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise

            self._config = config
            self._last_external = _flatten_config(config)

        except OSError:
            return GLib.SOURCE_REMOVE

        self.save_requested.emit()

        return GLib.SOURCE_REMOVE

    _VALID_EVENTS = (
        Gio.FileMonitorEvent.CHANGED,
        Gio.FileMonitorEvent.CHANGES_DONE_HINT,
        Gio.FileMonitorEvent.CREATED,
        Gio.FileMonitorEvent.MOVED_IN,
        Gio.FileMonitorEvent.DELETED,
    )

    def _watch(self):
        self._path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        # Watch both the file itself and its parent directory. Editors that
        # save via write+rename update the file's identity, so a monitor on
        # the old inode misses the new content; the dir monitor catches the
        # MOVED_IN. (Same two-watch strategy as Modus ConfigService.)
        for target in (str(self._path), str(self._path.parent)):
            if not os.path.exists(target):
                continue
            try:
                file = Gio.File.new_for_path(target)
                monitor = file.monitor_file(
                    Gio.FileMonitorFlags.NONE,
                    None,
                )
                monitor.connect("changed", self._on_file_changed)
                self._monitors.append(monitor)
            except Exception:
                continue

    def _on_file_changed(self, _monitor, file, _other, event):
        if not self._loaded:
            return

        # The parent-dir monitor fires for every write in the config dir
        # (.config.* tmp files, back-ups...). Only the config.toml itself is
        # interesting.
        if file.get_path() != str(self._path):
            return

        if event not in self._VALID_EVENTS:
            return

        if self._reload_debounce is not None:
            GLib.source_remove(self._reload_debounce)

        self._reload_debounce = GLib.timeout_add(
            _RELOAD_DELAY_MS,
            self._reload_external,
        )

    def _reload_external(self):
        self._reload_debounce = None

        try:
            config = tomlkit.parse(self._path.read_text(encoding="utf-8"))
        except (
            OSError,
            ValueError,
            tomlkit.exceptions.ParseError,
        ):
            return GLib.SOURCE_REMOVE

        external = _flatten_config(config)

        for sl, (key, ptype, default) in _WATCHED_PROPERTIES.items():
            if key not in external:
                continue

            try:
                external[key] = _coerce(
                    ptype,
                    external[key],
                )
            except TypeError, ValueError:
                external[key] = default

        if external == self._last_external:
            # Nothing actually changed (mtime-only touch or our own save).
            return GLib.SOURCE_REMOVE

        old_data = self._data.copy()
        old_external = self._last_external

        self._config = config
        self._last_external = external.copy()
        self._data.update(external)

        for sl, (_key, _ptype, _default) in _WATCHED_PROPERTIES.items():
            if old_data.get(_key) != self._data.get(_key):
                self.notify(_PROP_NAMES[sl])

        self.config_changed(old_external, self._last_external)

        return GLib.SOURCE_REMOVE

    def get(
        self,
        key: str,
        default: Any = None,
    ) -> Any:
        return self._data.get(key, default)

    def set_raw(
        self,
        key: str,
        value: Any,
    ):
        if self._data.get(key) == value:
            return

        try:
            normalized = _coerce(
                type(value),
                value,
            )
        except TypeError, ValueError:
            normalized = value

        self._data[key] = normalized
        self._dirty_key(key)

        for sl, (k, _ptype, _default) in _WATCHED_PROPERTIES.items():
            if k == key:
                self.notify(_PROP_NAMES[sl])


flags = Flags()
