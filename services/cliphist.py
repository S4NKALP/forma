"""
Keeps a warm in-memory snapshot of ``cliphist list`` so the clipboard surface
opens instantly. A ``Gio.FileMonitor`` on cliphist's BoltDB watches for writes
(the session's ``cliphist store`` daemon — already wired in the user's Hyprland
config — updates it on every copy); a 300 ms debounce re-reads the history into
``entries``. Missing image thumbnails are decoded into the cache before the
list lands, so image rows never bind to a not-yet-existing file.

All work runs on the main loop: refresh/list/delete are fast (~30 ms) and
synchronous, on purpose. No worker-thread ``GLib.idle_add`` is used — in this
process idle callbacks posted from a Python thread are never dispatched by the
fabric GTK loop, so anything threaded would silently never land. The ``changed``
signal fires before the list memory is kept, so a surface can always read the
fresh snapshot in its handler.

NOTE: no ``from __future__ import annotations`` — fabric's @Signal/@Property
read the class ``__annotations__`` for GType typecodes, and a future import
turns them into strings, breaking class creation.
"""

import os
import re
import subprocess
import threading
from pathlib import Path

from fabric.core.service import Property, Service, Signal
from fabric.utils import Gio, GLib, logger

_CACHE_HOME = os.environ.get(
    "XDG_CACHE_HOME",
    str(Path.home() / ".cache"),
)
_THUMB_DIR = Path(_CACHE_HOME) / "forma" / "cliphist-thumbs"
_DB = Path(_CACHE_HOME) / "cliphist" / "db"

_META_RE = re.compile(r"^\[\[ binary data (.*) \]\]$")
_IMG_RE = re.compile(r"\b(png|jpg|jpeg|gif|bmp|webp)\b")
_SPLIT_RE = re.compile(r"^(\S+ \S+) (\w+) (\d+)x(\d+)$")

_DIGITS_RE = re.compile(r"^\d+$")


class Cliphist(Service):
    @Signal
    def changed(self) -> None: ...

    @Property(list)
    def entries(self):
        return self._entries

    @Property(bool, default_value=False)
    def pending(self) -> bool:
        return self._pending

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._entries: list[dict] = []
        self._pending = False
        self._debounce_id = 0

        _THUMB_DIR.mkdir(parents=True, exist_ok=True)
        self._setup_file_monitor()
        self.refresh()

    # --- file watch ------------------------------------------------------------

    def _setup_file_monitor(self):
        f = Gio.File.new_for_path(str(_DB))
        self._monitor = f.monitor_file(Gio.FileMonitorFlags.NONE, None)
        if self._monitor:
            self._monitor.connect("changed", self._on_db_changed)

    def _on_db_changed(self, _monitor, _file, _other_file, event_type):
        if event_type not in (
            Gio.FileMonitorEvent.CHANGES_DONE_HINT,
            Gio.FileMonitorEvent.CREATED,
        ):
            return
        if self._debounce_id:
            GLib.source_remove(self._debounce_id)
        self._debounce_id = GLib.timeout_add(300, self._on_debounced_db_change)

    def _on_debounced_db_change(self):
        self._debounce_id = 0
        self.refresh()
        return GLib.SOURCE_REMOVE

    # --- snapshots ------------------------------------------------------------

    def refresh(self):
        try:
            entries = list_entries()
            _ensure_thumbs(entries)
        except Exception as e:
            logger.warning(f"cliphist refresh: {e}")
            entries = []
        self._set_entries(entries)

    def _set_entries(self, entries: list[dict]):
        self._entries = entries
        self.notify("entries")
        self.changed()

    # --- actions ---------------------------------------------------------------

    def copy(self, entry: dict):
        if not _DIGITS_RE.match(str(entry.get("id") or "")):
            return
        raw = entry.get("raw") or ""
        if not raw:
            return
        threading.Thread(
            target=decode_and_copy,
            args=(raw,),
            daemon=True,
        ).start()

    def wipe(self):
        self._set_entries([])
        threading.Thread(target=clear_cliphist, daemon=True).start()

    def remove(self, entry: dict):
        clip_id = str(entry.get("id") or "")
        if not _DIGITS_RE.match(clip_id):
            return
        raw = entry.get("raw") or f"{clip_id}\t"
        self._entries = [e for e in self._entries if e.get("id") != clip_id]
        self.notify("entries")
        self.changed()
        threading.Thread(
            target=delete_cliphist_item,
            args=(raw,),
            daemon=True,
        ).start()


# --- pure helpers (shared with threads) --------------------------------------


def get_cliphist_items() -> list[dict]:
    """Raw ``cliphist list`` rows -> [{id, content, raw}]."""
    try:
        output = subprocess.check_output(
            ["cliphist", "list"], text=True, stderr=subprocess.DEVNULL
        )
    except OSError, subprocess.SubprocessError:
        return []
    items = []
    for line in output.splitlines():
        if "\t" in line:
            clip_id, content = line.split("\t", 1)
            items.append({"id": clip_id, "content": content, "raw": line})
    return items


def parse_entry(item: dict) -> dict:
    """Enrich a raw row with ukishima's display split + thumb path."""
    clip_id = item["id"]
    preview = item["content"]
    m = _META_RE.match(preview)
    is_image = m is not None and _IMG_RE.search(m.group(1)) is not None
    label = ""
    size_label = ""
    if is_image:
        p = _SPLIT_RE.match(m.group(1))
        label = f"{p.group(2)} {p.group(3)}×{p.group(4)}" if p else m.group(1)
        size_label = p.group(1) if p else ""
    return {
        "id": clip_id,
        "raw": item["raw"],
        "preview": preview,
        "isImage": is_image,
        "label": label,
        "sizeLabel": size_label,
        "thumb": str(_THUMB_DIR / f"{clip_id}.png") if is_image else "",
    }


def list_entries() -> list[dict]:
    return [parse_entry(item) for item in get_cliphist_items()]


def decode_and_copy(raw_line: str):
    try:
        decode_proc = subprocess.Popen(
            ["cliphist", "decode"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        wlcopy_proc = subprocess.Popen(
            ["wl-copy"], stdin=decode_proc.stdout, stderr=subprocess.DEVNULL
        )
        decode_proc.stdin.write(raw_line.encode("utf-8") + b"\n")
        decode_proc.stdin.close()
        wlcopy_proc.communicate()
        decode_proc.wait()
    except OSError, subprocess.SubprocessError:
        pass


def delete_cliphist_item(raw_line: str):
    try:
        proc = subprocess.Popen(
            ["cliphist", "delete"],
            stdin=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        proc.communicate(input=raw_line.encode("utf-8") + b"\n")
    except OSError, subprocess.SubprocessError:
        pass


def clear_cliphist():
    try:
        subprocess.run(
            ["cliphist", "wipe"],
            check=False,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        pass


def _ensure_thumbs(entries: list[dict]):
    """Regenerate missing image previews before the list lands."""
    for entry in entries:
        if not entry["isImage"]:
            continue
        thumb = Path(entry["thumb"])
        if thumb.exists() and thumb.stat().st_size:
            continue
        try:
            data = subprocess.check_output(
                ["cliphist", "decode", entry["id"]],
                stderr=subprocess.DEVNULL,
            )
        except OSError, subprocess.SubprocessError:
            continue
        if not data:
            continue
        with thumb.open("wb") as f:
            f.write(data)


cliphist = Cliphist()
