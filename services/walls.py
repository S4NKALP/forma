import collections
import queue
import threading
from pathlib import Path

from fabric.core.service import Property, Service, Signal
from fabric.utils import GLib, logger

from core.flags import flags
from services import wallpaper_app as app

_POLL_MS = 50


class Walls(Service):
    @Signal
    def changed(self) -> None: ...

    @Signal
    def entriesChanged(self) -> None: ...

    @Signal
    def refreshDone(self) -> None: ...

    @Property(list)
    def entries(self) -> list:
        return self._entries

    @Property(int)
    def count(self) -> int:
        return len(self._entries)

    @Property(str)
    def current(self) -> str:
        return self._current

    @Property(str)
    def wpdir(self) -> str:
        return self._wpdir

    @Property(bool, default_value=False)
    def refreshing(self) -> bool:
        return self._refreshing

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._entries: list[dict] = []
        self._current: str = ""
        self._wpdir: str = ""
        self._refreshing = False
        self._pending = False
        self._deleting = 0
        self._pump = threading.Lock()
        self._delete_pump: collections.deque = collections.deque()
        self._trash_after: set[str] = set()
        self._apply_key: tuple | None = None
        self._queued_apply: tuple | None = None
        self._q: queue.Queue = queue.Queue()
        GLib.timeout_add(_POLL_MS, self._drain)

    # --- main-loop delivery ---------------------------------------------------

    def _drain(self):
        try:
            while True:
                msg = self._q.get_nowait()
                self._handle(msg)
        except queue.Empty:
            pass
        return GLib.SOURCE_CONTINUE

    def _handle(self, msg: dict):
        kind = msg.get("kind")
        if kind == "refreshing":
            self._refreshing = bool(msg["value"])
            self.notify("refreshing")
            if not self._refreshing and not self._deleting:
                if self._pending:
                    self._pending = False
                    threading.Thread(target=self._refresh_worker, daemon=True).start()
        elif kind == "delete":
            self._deleting -= 1
            if not self._deleting:
                self._pump.acquire()
                try:
                    next_path = (
                        self._delete_pump.popleft() if self._delete_pump else None
                    )
                finally:
                    self._pump.release()
                if next_path:
                    threading.Thread(
                        target=self._delete_pump_worker, args=(next_path,), daemon=True
                    ).start()
                elif self._pending and not self._refreshing:
                    self._pending = False
                    threading.Thread(target=self._refresh_worker, daemon=True).start()
        elif kind == "refresh":
            self._wpdir = msg["wpdir"]
            self._current = msg["current"]
            entries = msg["entries"]
            if self._deleting or self._trash_after:
                entries = [e for e in entries if e.get("path") not in self._trash_after]
                if not self._deleting:
                    self._trash_after.clear()
            self._entries = entries
            self.notify("entries")
            self.notify("count")
            self.notify("wpdir")
            self.notify("current")
            self.changed()
            self.entriesChanged()
            self.refreshDone()
        elif kind == "apply-state":
            self._apply_key = msg.get("key")
            self._queued_apply = msg.get("queued")

    # --- warm / refresh -------------------------------------------------------

    def warm(self):
        if not self._entries:
            self.refresh()
            return
        if not app.warm_probe(self._entries):
            self.refresh()

    def refresh(self):
        if self._refreshing or self._deleting > 0:
            self._pending = True
            return
        threading.Thread(target=self._refresh_worker, daemon=True).start()

    def _refresh_worker(self):
        self._q.put({"kind": "refreshing", "value": True})
        try:
            explicit = flags.get("wallpaperDir") or ""
            wpdir, entries, current = app.refresh_pipeline(explicit, explicit)
        except Exception as e:  # noqa: BLE001 - a dead pipeline must not kill
            logger.warning(f"walls refresh: {e}")
            wpdir, entries, current = self._wpdir, [], self._current
            if not wpdir:
                wpdir = flags.get("wallpaperDir") or str(
                    Path.home() / "Pictures" / "Wallpapers"
                )
        finally:
            self._q.put({"kind": "refreshing", "value": False})
        self._q.put(
            {"kind": "refresh", "wpdir": wpdir, "entries": entries, "current": current}
        )

    # --- apply / fit / trash --------------------------------------------------

    def apply(self, path: str, output: str | None = None):
        key = (path, output or "")
        if self._apply_key == key:
            return
        if self._apply_key is not None:
            self._queued_apply = key
            return
        self._apply_key = key
        self._q.put({"kind": "apply-state", "key": key, "queued": None})
        threading.Thread(target=self._apply_worker, args=(key,), daemon=True).start()

    def _apply_worker(self, key: tuple):
        path, output = key
        queued = None
        try:
            fit = _fit_flag()
            app.apply_pipeline(path, output, fit)
            self.refresh()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"walls apply {path}: {e}")
        finally:
            if self._apply_key == key:
                self._apply_key = None
                queued = self._queued_apply
                self._queued_apply = None
            self._q.put({"kind": "apply-state", "key": None, "queued": queued})
        if queued:
            self.apply(*queued)

    def apply_fit(self, fit: str):
        key = ("_fit_", fit)
        if self._apply_key == key:
            return
        if self._apply_key is not None:
            self._queued_apply = key
            return
        self._apply_key = key
        threading.Thread(target=self._fit_worker, args=(fit,), daemon=True).start()

    def _fit_worker(self, fit: str):
        queued = None
        try:
            app.fit_pipeline(fit)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"walls fit {fit}: {e}")
        finally:
            if self._apply_key == ("_fit_", fit):
                self._apply_key = None
                queued = self._queued_apply
                self._queued_apply = None
                self._q.put({"kind": "apply-state", "key": None, "queued": None})
            if queued:
                self.apply(*queued)

    def trash(self, path: str):
        self._entries = [e for e in self._entries if e.get("path") != path]
        self._trash_after.add(path)
        self.notify("entries")
        self.notify("count")
        self.changed()
        self._deleting += 1
        self._pump.acquire()
        try:
            self._delete_pump.append(path)
        finally:
            self._pump.release()
        if self._deleting == 1:
            threading.Thread(
                target=self._delete_pump_worker, args=(path,), daemon=True
            ).start()

    def _delete_pump_worker(self, path: str):
        try:
            app.trash_file(path)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"walls trash {path}: {e}")
        self._q.put({"kind": "delete"})


def _fit_flag() -> str:
    fit = flags.get("wallpaperFit") or "cover"
    return fit if fit in app.FIT_MODES else "cover"


walls = Walls()
