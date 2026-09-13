"""AppImage drag-and-drop installer for the resting pill.

Pure mixin — composed into :class:`~forma.core.pill.Pill`. Owns the DnD
protocol handlers (Wayland only hands URI data over at drop time) and the
serial install worker queue that drives the drop-zone face.
"""

import threading
import time as _time

from gi.repository import Gdk, GLib, Gtk

from services.appimage_installer import appimage_service

_DROP_TARGET_URI = 1


class PillDropMixin:
    def _init_drop(self):
        self._drag_active = False
        self._drag_stage = ""
        self._drag_name = ""
        self._drag_files: list[str] = []
        self._drag_queue: list[str] = []
        self._drag_installed_any = False
        self._drag_installed_app = False
        self._drag_install_failed = False
        self._drag_action = "new"
        self._drag_pct = ""
        self._drag_seconds = 0
        self._drag_spin_id = None
        self._drag_seconds_id = None
        self._drag_done_id = None
        self._drag_bad_id = None
        self._drop_pending = False
        self._drop_watchdog_id = None
        self._drop_cooldown_until = 0.0
        self._install_thread = None
        self._setup_drop_target()

    def _setup_drop_target(self):
        target = Gtk.TargetEntry.new("text/uri-list", 0, _DROP_TARGET_URI)
        self.drag_dest_set(Gtk.DestDefaults.ALL, [target], Gdk.DragAction.COPY)
        self.connect("drag-motion", self._on_drag_motion)
        self.connect("drag-leave", self._on_drag_leave)
        self.connect("drag-drop", self._on_drag_drop)
        self.connect("drag-data-received", self._on_drag_data)

    @staticmethod
    def _uri_to_path(uri: str) -> str:
        try:
            from gi.repository import Gio

            path = Gio.File.new_for_uri(uri).get_path()
            return str(path) if path else ""
        except GLib.Error:
            return ""

    def _droppable(self, uris: list[str]) -> list[str]:
        out = []
        for uri in uris:
            path = self._uri_to_path(uri)
            if path and path.lower().endswith(".appimage"):
                out.append(path)
        return out

    def _drop_name(self, uris: list[str]) -> str:
        if not uris:
            return ""
        path = self._uri_to_path(uris[0])
        name = path.rsplit("/", 1)[-1]
        if name.lower().endswith(".appimage"):
            name = name[:-9]
        return name

    def _on_drag_motion(self, _widget, ctx, _x, _y, time):
        # an open surface turns the pill into a modal — nothing can drop on it
        if self.state == "surface" or self._drag_stage in ("installing", "done"):
            Gdk.drag_status(ctx, 0, time)
            return False
        targets = ctx.list_targets()
        has_uri = any("text/uri-list" in str(t) for t in targets)
        if not has_uri:
            Gdk.drag_status(ctx, 0, time)
            return False
        Gdk.drag_status(ctx, Gdk.DragAction.COPY, time)
        # Grow the drop-zone face as soon as a uri drag hovers the pill. On
        # Wayland the data is only transferable at drop time, so the hover is
        # generic (no per-file bad state until the drop delivers the uris).
        if self.state != "dragover" or self._drag_stage not in ("hover",):
            self._drag_active = True
            self._drag_stage = "hover"
            if self.state != "dragover":
                self._morph_to("dragover")
            self._drag_zone.set_stage("hover")
        return True

    def _on_drag_leave(self, _widget, _ctx, _evtime):
        self._clear_drop_watchdog()
        self._drop_pending = False
        self._drop_cooldown_until = _time.monotonic() + 0.4
        if self._drag_stage in ("hover", "bad"):
            self._drag_active = False
            self._drag_stage = ""
            if self.state == "dragover":
                self._morph_to("rest")

    def _on_drag_drop(self, _widget, ctx, _x, _y, time):
        target = self.drag_dest_find_target(ctx, None)
        if target is None:
            return False
        self._drop_pending = True
        self._drop_watchdog_id = GLib.timeout_add(2500, self._drop_watchdog)
        self.drag_get_data(ctx, target, time)
        return True

    def _drop_watchdog(self):
        # if the data offer never lands (XDND/wayland abort) recover to rest
        self._drop_pending = False
        if self._drag_stage in ("hover", "bad") or not self._drag_active:
            self._drag_active = False
            self._drag_stage = ""
            if self.state == "dragover":
                self._morph_to("rest")
        return GLib.SOURCE_REMOVE

    def _on_drag_data(self, _widget, ctx, _x, _y, data, info, time):
        self._clear_drop_watchdog()
        self._drop_pending = False
        if info != _DROP_TARGET_URI or data is None:
            Gtk.drag_finish(ctx, False, False, time)
            return
        uris = data.get_uris()
        files = self._droppable(uris)
        name = self._drop_name(uris)
        if not files:
            self._drag_active = True
            self._drag_stage = "bad"
            if self.state != "dragover":
                self._morph_to("dragover")
            self._drag_zone.set_stage("bad", name)
            Gtk.drag_finish(ctx, False, False, time)
            self._arm_bad_timer()
            return
        self._drag_name = name
        self._begin_install(files)
        Gtk.drag_finish(ctx, True, False, time)

    def _begin_install(self, files: list[str]):
        self._drag_active = True
        self._drag_stage = "installing"
        self._drag_files = list(files)
        self._drag_queue = list(files)
        self._drag_installed_any = False
        self._drag_installed_app = False
        self._drag_install_failed = False
        self._drag_action = "new"
        self._drag_pct = ""
        self._drag_seconds = 0
        self._install_thread = None
        if self.state != "dragover":
            self._morph_to("dragover")
        self._drag_zone.set_stage("installing", self._drag_name)
        self._drag_seconds_id = GLib.timeout_add(1000, self._tick_drag_seconds)
        self._drag_spin_id = GLib.timeout_add(100, self._tick_drag_spin)
        self._run_next_install()

    def _clear_drop_watchdog(self):
        if self._drop_watchdog_id is not None:
            GLib.source_remove(self._drop_watchdog_id)
            self._drop_watchdog_id = None

    def _tick_drag_seconds(self):
        if self._drag_stage != "installing":
            return GLib.SOURCE_REMOVE
        self._drag_seconds += 1
        self._drag_zone.set_stage(
            "installing", self._drag_name, self._drag_pct, self._drag_seconds
        )
        return GLib.SOURCE_CONTINUE

    def _tick_drag_spin(self):
        if self._drag_stage != "installing":
            return GLib.SOURCE_REMOVE
        self._drag_zone.set_stage(
            "installing", self._drag_name, self._drag_pct, self._drag_seconds
        )
        return GLib.SOURCE_CONTINUE

    def _run_next_install(self):
        if not self._drag_queue:
            self._cancel_drag_timers()
            self._drag_stage = "done" if self._drag_installed_any else "fail"
            action_title = ""
            if self._drag_installed_any:
                if getattr(self, "_drag_action", "") == "updated":
                    action_title = "Updated"
                elif getattr(self, "_drag_action", "") == "reinstalled":
                    action_title = "Reinstalled"
            self._drag_zone.set_stage(
                self._drag_stage, self._drag_name, title=action_title
            )
            if self._drag_installed_any:
                self._drag_done_id = GLib.timeout_add(1100, self._after_drop_done)
            else:
                self._arm_bad_timer()
            return
        path = self._drag_queue.pop(0)
        thread = threading.Thread(
            target=self._install_worker,
            args=(path,),
            name="appimage-install",
            daemon=True,
        )
        self._install_thread = thread
        thread.start()

    def _install_worker(self, path: str):
        result = {"slug": "", "name": "", "action": "", "pct": "", "ok": False}
        try:
            out = appimage_service.install(path)
            parts = out.split("\t")
            result = {
                "slug": parts[0] if len(parts) > 0 else "",
                "name": parts[1] if len(parts) > 1 else "",
                "action": parts[2] if len(parts) > 2 else "",
                "pct": "",
                "ok": True,
            }
        except Exception:
            result = {"slug": "", "name": "", "action": "", "pct": "", "ok": False}
        GLib.idle_add(self._finish_install, result)

    def _finish_install(self, result: dict):
        if result.get("ok"):
            self._drag_installed_any = True
            self._drag_installed_app = True
            self._drag_action = result.get("action", "new")
            self._drag_pct = ""

            # Immediately refresh the launcher so the new app shows up
            try:
                from .surfaces.launcher import desktop_entries
            except ImportError:
                try:
                    from ..services.desktop_entries import desktop_entries
                except ImportError:
                    desktop_entries = None
            if desktop_entries:
                desktop_entries.refresh()

        else:
            self._drag_install_failed = True
        self._run_next_install()

    def _arm_bad_timer(self):
        if self._drag_bad_id is not None:
            GLib.source_remove(self._drag_bad_id)
        self._drag_bad_id = GLib.timeout_add(1300, self._after_drop_bad)

    def _after_drop_done(self):
        self._cancel_drag_timers()
        self._drag_active = False
        self._drag_stage = ""
        self._drop_cooldown_until = _time.monotonic() + 0.4
        if self.state == "dragover":
            self._morph_to("rest")
        if self._drag_installed_app:
            self.open_surface("launcher")
        return GLib.SOURCE_REMOVE

    def _after_drop_bad(self):
        self._cancel_drag_timers()
        self._drag_active = False
        self._drag_stage = ""
        self._drop_cooldown_until = _time.monotonic() + 0.4
        if self.state == "dragover":
            self._morph_to("rest")
        return GLib.SOURCE_REMOVE

    def _cancel_drag_timers(self):
        for attr in (
            "_drag_spin_id",
            "_drag_seconds_id",
            "_drag_done_id",
            "_drag_bad_id",
        ):
            src = getattr(self, attr)
            if src is not None:
                GLib.source_remove(src)
                setattr(self, attr, None)
