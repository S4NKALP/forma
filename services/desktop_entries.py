"""
Scans the freedesktop application dirs ($XDG_DATA_HOME/DIRS + applications),
parses each .desktop with GLib.KeyFile and exposes the entries as plain
objects with an ``execute()`` that expands Exec field codes. Mirrors the
fabric service pattern: one long-lived singleton, signal-driven (dir watch),
no GTK inside.

AppImage entries installed by scripts/appimage-install.sh are identified by
their ``pill-`` desktop id prefix; their Icon field is usually an absolute
path, which the icon loader must use as-is.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from fabric import Service, Signal
from fabric.utils import DesktopApp, Gio, GioUnix, GLib, get_desktop_applications

from core.config import state_file


class DesktopEntryWrapper:
    """Wrapper around fabric's DesktopApp to match the expected interface."""

    def __init__(self, app):
        self.app = app
        app_info = app._app
        app_id = app_info.get_id() or ""
        self.id = app_id.removesuffix(".desktop")
        self.name = app.name or self.id
        self.icon = app_info.get_string("Icon") or app.icon_name or ""

        # We don't have exec_line and terminal, but execute() can just use app.launch()
        # unless it fails.
        self.generic_name = app.generic_name or ""
        self.comment = app.description or ""

        cats = app_info.get_categories() or ""
        self.categories = [c for c in cats.split(";") if c]
        self.keywords = []
        self.startup_wm_class = app.window_class or ""

    @property
    def appimage_slug(self) -> str:
        if self.id.startswith("forma-"):
            return self.id[6:]
        if self.id.startswith("pill-"):
            return self.id[5:]
        return ""

    def execute(self):
        try:
            self.app.launch()
        except Exception as e:
            from fabric.utils import logger

            logger.warning(f"launcher: failed to launch {self.id}: {e}")


class DesktopEntries(Service):
    """Parse/refresh desktop-entry applications using fabric's utils."""

    refreshed = Signal("refreshed")

    def __init__(self):
        super().__init__()
        self.applications = []
        self._monitors = []
        self._debounce = None
        self._scan()
        self._watch_dirs()

    def refresh(self):
        """Force a synchronous rescan (e.g. after a drop completes)."""
        if self._debounce is not None:
            GLib.source_remove(self._debounce)
            self._debounce = None
        self._scan()

    def _scan(self):
        seen = {}
        for app in get_desktop_applications():
            if app.hidden or not app._app:
                continue
            wrapper = DesktopEntryWrapper(app)
            if not wrapper.id:
                continue
            seen[wrapper.id] = wrapper

        for app in self._appimage_apps():
            wrapper = DesktopEntryWrapper(app)
            if wrapper.id:
                seen[wrapper.id] = wrapper

        self.applications = list(seen.values())
        self.applications.sort(key=lambda e: (e.name or "").lower())
        self.refreshed.emit()

    def _appimage_apps(self) -> list:
        # GLib's DesktopAppInfo.get_all() is cached per-process and can serve a
        # stale list, hiding AppImage entries installed earlier. Parse the
        # AppImage desktop files (forma-/pill- prefix) explicitly so those rows
        # (and their rename/delete slug) are always present.
        out = []
        for directory in self._data_app_dirs():
            if not directory.is_dir():
                continue
            try:
                files = list(directory.glob("forma-*.desktop"))
                files.extend(directory.glob("pill-*.desktop"))
            except OSError:
                continue
            for f in files:
                # new_from_filename raises TypeError when a desktop file is
                # malformed (e.g. a hand-renamed AppImage desktop); gi raises
                # instead of returning None, so catch both paths
                try:
                    content = f.read_text(encoding="utf-8", errors="ignore")
                    if not content.lstrip().startswith("[Desktop Entry]"):
                        continue
                    info = GioUnix.DesktopAppInfo.new_from_filename(str(f))
                    if info is None:
                        continue
                    out.append(DesktopApp(info))
                except OSError, TypeError:
                    continue
        return out

    def _data_app_dirs(self) -> list[Path]:
        dirs = []
        data_home = os.environ.get("XDG_DATA_HOME") or str(
            Path.home() / ".local" / "share"
        )
        data_dirs = os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
        dirs.append(data_home)
        dirs.extend(p for p in data_dirs.split(":") if p)
        return [Path(d) / "applications" for d in dirs]

    def _watch_dirs(self):
        for directory in self._data_app_dirs():
            if not directory.is_dir():
                continue
            monitor = Gio.File.new_for_path(str(directory)).monitor_file(
                Gio.FileMonitorFlags.NONE, None
            )
            monitor.connect("changed", self._on_dir_changed)
            self._monitors.append(monitor)

    def _on_dir_changed(self, *_a):
        if self._debounce is not None:
            GLib.source_remove(self._debounce)
        self._debounce = GLib.timeout_add(300, self._debounced_scan)

    def _debounced_scan(self):
        self._debounce = None
        self._scan()
        return GLib.SOURCE_REMOVE

    @staticmethod
    def usage_file() -> Path:
        return state_file("launcher-usage.json")

    def bump_usage(self, entry):
        path = self.usage_file()
        usage = {}
        if path.exists():
            try:
                usage = json.loads(path.read_text(encoding="utf-8"))
            except OSError, ValueError, json.JSONDecodeError:
                pass
        usage[entry.id] = usage.get(entry.id, 0) + 1
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(usage), encoding="utf-8")
        except OSError:
            pass

    @staticmethod
    def load_usage():
        path = DesktopEntries.usage_file()
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except OSError, ValueError, json.JSONDecodeError:
            return {}


desktop_entries = DesktopEntries()
