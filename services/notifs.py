"""
Owns the org.freedesktop.Notifications daemon through fabric's ``Notifications``
server and rebuilds the ukishima inbox semantics on top of each incoming
``Notification``: tracked popups/history with read marks, per-app grouping with
critical rows pinned and same-summary/body coalescing, DND gating and the
transient OSD retirement flow.

The API mirrors Notifs.qml so the Link/Toast surfaces port 1:1: ``popups``,
``history``, ``groups``, ``unread``, ``count``, ``toast_critical`` plus the
``dismiss*``/``activate*``/``raiseWindow``/``markAllSeen``/``clear*`` mutations
and ``icon_for``/``age_label`` helpers.
"""

import time as _time

from fabric import Service, Signal
from fabric.core.service import Property
from fabric.notifications.service import (
    Notification,
    NotificationCloseReason,
    Notifications,
)
from fabric.utils import GLib

from core.flags import flags

# ukishima threshold: Low (burn-in-read) deadlines are shorter than normal ones.
_POPUP_LOW_MS = 2500
_POPUP_STD_MS = 3000
_HISTORY_MAX = 50
_POPUP_MAX = 3

CRITICAL = 2
LOW = 0


def _n_attr(n, name: str, default=""):
    """Read an attribute off a live Notification or a history-dict entry."""
    if isinstance(n, dict):
        return n.get(name, default)
    return getattr(n, name, default)


def _n_urgency(n) -> int:
    if isinstance(n, dict):
        return int(n.get("urgency", 1))
    return int(getattr(n, "urgency", 1) or 1)


def _n_text(n, name: str) -> str:
    return str(_n_attr(n, name, "") or "")


class Notifs(Service):
    """Stateful notify server wrapper exposing the ukishima inbox model."""

    @Signal
    def changed(self) -> None: ...

    @Property(dict)
    def tracked(self) -> dict[int, Notification]:
        return self._server.notifications

    @Property(int, "readable", default_value=0)
    def count(self) -> int:
        return len(self._server.notifications) + len(self._history)

    @Property(int, "readable", default_value=0)
    def unread(self) -> int:
        return sum(
            1 for n in self._server.notifications.values() if not self._seen.get(n.id)
        )

    @Property(list)
    def popups(self) -> list[Notification]:
        return self._popups

    @Property(list)
    def history(self) -> list[dict]:
        return self._history

    @Property(list)
    def groups(self) -> list[dict]:
        return self._build_groups()

    @Property(bool, "readable", default_value=False)
    def toast_critical(self) -> bool:
        return bool(self._popups) and _n_urgency(self._popups[-1]) == CRITICAL

    @Property(int, "readable", default_value=0)
    def tick(self) -> int:
        return self._tick

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        self._server = Notifications()
        self._seen: dict[int, bool] = {}
        self._arrival_ms: dict[int, float] = {}
        self._expire_at: dict[int, float] = {}
        self._user_dismissed: dict[int, bool] = {}
        self._hooked: dict[int, bool] = {}
        self._expanded_apps: dict[str, bool] = {}
        self._popups: list[Notification] = []
        self._history: list[dict] = []
        self._tick = 0
        self._tick_source: int | None = None

        # any notification arriving while a non-critical toast is up only
        # refreshes the toast; the deadline/tick machinery stays independent of
        # individual widget lifetimes.
        self._server.connect("notification_added", self._on_notification_added)
        # the server owns the D-Bus name; joining names means another daemon was
        # already present so this inbox simply stays empty (fabric logs a warning)
        self._sync_tick()

    # --- server ingestion ------------------------------------------------------

    def _on_notification_added(self, _srv, notification_id: int):
        n = self._server.get_notification_from_id(notification_id)
        if n is None:
            return
        self._arrival_ms[n.id] = _time.time() * 1000
        self._expire_at[n.id] = self._arrival_ms[n.id] + (
            _POPUP_LOW_MS if _n_urgency(n) == LOW else _POPUP_STD_MS
        )
        self._hook_closed(n)
        critical = _n_urgency(n) == CRITICAL
        if not flags.get("dnd") or critical:
            self._popups = (self._popups + [n])[-_POPUP_MAX:]
        self._bump()
        self._sync_tick()

    def _hook_closed(self, n: Notification):
        if self._hooked.get(n.id):
            return
        self._hooked[n.id] = True
        n.connect("closed", lambda *_a, nn=n: self._on_notif_closed(nn))

    def _on_notif_closed(self, n: Notification):
        if not self._user_dismissed.get(n.id):
            self._history = [self._history_entry(n), *self._history][:_HISTORY_MAX]
        else:
            self._user_dismissed.pop(n.id, None)
        self.remove_popup(n)
        self._arrival_ms.pop(n.id, None)
        self._expire_at.pop(n.id, None)
        self._hooked.pop(n.id, None)
        self._bump()
        self._sync_tick()

    def _history_entry(self, n: Notification) -> dict:
        return {
            "app": n.app_name or "System",
            "summary": n.summary,
            "body": n.body,
            "appIcon": n.app_icon or "",
            "desktopEntry": self._desktop_entry(n),
            "image": self._image_path(n),
            "urgency": _n_urgency(n),
            "ts": self._arrival_ms.get(n.id) or _time.time() * 1000,
            "id": f"h{n.id}-{int(_time.time() * 1000)}",
        }

    # --- state publication -----------------------------------------------------

    def _bump(self):
        for prop in (
            "tracked",
            "count",
            "unread",
            "popups",
            "history",
            "groups",
            "toast_critical",
        ):
            self.notify(prop)
        self.emit("changed")

    def _sync_tick(self):
        """Run the 30 s age-refresh timer only while something is tracked."""
        keep = self.count > 0
        if keep and self._tick_source is None:

            def tick():
                if self.count <= 0:
                    self._tick_source = None
                    return GLib.SOURCE_REMOVE
                self._tick += 1
                self.notify("tick")
                self.emit("changed")
                return GLib.SOURCE_CONTINUE

            self._tick_source = GLib.timeout_add(30000, tick)
        elif not keep and self._tick_source is not None:
            GLib.source_remove(self._tick_source)
            self._tick_source = None

    # --- group model ------------------------------------------------------------

    @staticmethod
    def _coalesce(list_: list, it: dict):
        last = list_[-1] if list_ else None
        if (
            last
            and _n_text(last["n"], "summary") == _n_text(it["n"], "summary")
            and _n_text(last["n"], "body") == _n_text(it["n"], "body")
        ):
            last["count"] += 1
            last["items"].append(it["n"])
        else:
            list_.append(
                {"live": it["live"], "n": it["n"], "count": 1, "items": [it["n"]]}
            )

    def _build_groups(self) -> list[dict]:
        by_app: dict[str, list[dict]] = {}
        order: list[str] = []
        for n in self._server.notifications.values():
            app = (n.app_name or "System") or "System"
            by_app.setdefault(app, [])
            if app not in order:
                order.append(app)
            by_app[app].append(
                {"live": True, "n": n, "t": self._arrival_ms.get(n.id, 0)}
            )
        for h in self._history:
            app = (h.get("app") or "System") or "System"
            by_app.setdefault(app, [])
            if app not in order:
                order.append(app)
            by_app[app].append({"live": False, "n": h, "t": h.get("ts", 0)})

        groups = []
        for app in order:
            items = sorted(by_app[app], key=lambda it: it["t"], reverse=True)
            criticals: list[dict] = []
            entries: list[dict] = []
            for it in items:
                target = criticals if _n_urgency(it["n"]) == CRITICAL else entries
                self._coalesce(target, it)
            preview = next(
                (it for it in items if _n_urgency(it["n"]) != CRITICAL), None
            )
            groups.append(
                {
                    "app": app,
                    "count": len(items),
                    "t": items[0]["t"],
                    "newest": items[0]["n"],
                    "preview": preview["n"] if preview else items[0]["n"],
                    "criticals": criticals,
                    "entries": entries,
                }
            )
        groups.sort(key=lambda g: g["t"], reverse=True)
        return groups

    # --- helpers ---------------------------------------------------------------

    @staticmethod
    def _desktop_entry(n) -> str:
        getter = getattr(n, "do_get_hint_entry", None)
        if getter is None:
            return ""
        try:
            return str(getter("desktop-entry") or "")
        except GLib.Error:
            return ""

    @staticmethod
    def _image_path(n) -> str:
        if isinstance(n, dict):
            return str(n.get("image", "") or n.get("image_file", "") or "")
        return str(_n_attr(n, "image_file", "") or "")

    @staticmethod
    def _resolve_icon(name: str) -> str:
        """Look a named icon up in the desktop icon theme (sizes to 56 like QML)."""
        if not name:
            return ""
        from gi.repository import Gtk

        try:
            info = Gtk.IconTheme.get_default().lookup_icon(name, 56, 0)
        except GLib.Error:
            return ""
        if info is None:
            return ""
        try:
            return info.get_filename() or ""
        except GLib.Error:
            return ""

    def icon_for(self, n) -> str:
        """Best candidate icon path for a notification ('' when none)."""
        if n is None:
            return ""
        img = self._image_path(n)
        names: list[str] = []
        if img.startswith("image://icon/"):
            names.append(img[len("image://icon/") :])
        elif img and not img.lower().endswith(".svg"):
            return img
        names.append(self._field(n, "app_icon", "appIcon") or "")
        names.append(
            self._field(n, "desktopEntry", "desktopEntry") or self._desktop_entry(n)
        )
        names.append((self._field(n, "app_name", "app") or "").lower())
        for nm in names:
            if not nm:
                continue
            if nm.startswith("/"):
                return nm
            if nm.startswith("file://"):
                return nm[len("file://") :]
            resolved = self._resolve_icon(nm)
            if resolved:
                return resolved
        return ""

    @staticmethod
    def _field(n, live_key: str, dict_key: str) -> str:
        val = _n_attr(n, live_key, "")
        if not val and isinstance(n, dict):
            val = n.get(dict_key, "")
        return str(val or "")

    def age_label(self, n) -> str:
        t = self._arrival_ms.get(getattr(n, "id", None)) or (
            n.get("ts", 0) if isinstance(n, dict) else 0
        )
        void = self._tick
        if not t:
            return ""
        m = int((_time.time() * 1000 - t) / 60000)
        if m < 1:
            return "now"
        if m < 60:
            return f"{m}m"
        return f"{int(m / 60)}h"

    def deadline_for(self, n) -> float:
        """Epoch-ms the toast should retire itself at (snapshot on arrival)."""
        nid = getattr(n, "id", None)
        return self._expire_at.get(nid) or (_time.time() * 1000 + _POPUP_STD_MS)

    # --- mutations -------------------------------------------------------------

    def dismiss_entry(self, entry: dict):
        if not entry or not entry.get("items"):
            return
        gone: dict[int, bool] = {}
        live: list[Notification] = []
        for n in entry["items"]:
            if not isinstance(n, dict):
                self._user_dismissed[n.id] = True
                live.append(n)
            else:
                gone.setdefault(_history_key(n), True)
        for n in live:
            n.close(NotificationCloseReason.DISMISSED_BY_USER)
        self._history = [h for h in self._history if not gone.get(_history_key(h))]
        self._bump()

    def activate_notif(self, n):
        if n is None:
            return
        for act in getattr(n, "actions", None) or []:
            if act.identifier == "default":
                try:
                    act.invoke()
                except GLib.Error:
                    pass
                break
        self.raise_window(n)

    def activate_entry(self, entry: dict):
        n = entry.get("n") if entry else None
        self.activate_notif(n)
        self.dismiss_entry(entry)

    def dismiss_app(self, app: str):
        doomed = [
            n
            for n in self._server.notifications.values()
            if ((n.app_name or "System") or "System") == app
        ]
        for n in doomed:
            self._user_dismissed[n.id] = True
            n.close(NotificationCloseReason.DISMISSED_BY_USER)
        self._history = [h for h in self._history if (h.get("app") or "System") != app]
        self._bump()

    def mark_all_seen(self):
        self._seen = {n.id: True for n in self._server.notifications.values()}
        self.notify("unread")
        self.emit("changed")

    def clear_all(self):
        doomed = list(self._server.notifications.values())
        for n in doomed:
            self._user_dismissed[n.id] = True
            n.close(NotificationCloseReason.DISMISSED_BY_USER)
        self._history = []
        self._popups = []
        self._bump()
        self._sync_tick()

    def remove_popup(self, n):
        if n in self._popups:
            self._popups = [p for p in self._popups if p is not n]
            self._bump()

    def clear_popups(self):
        """Retire every pending popup at once (transient OSD covers them)."""
        if not self._popups:
            return
        self._popups = []
        self._bump()

    def toggle_expanded(self, app: str):
        self._expanded_apps[app] = not self._expanded_apps.get(app)
        self.emit("changed")

    def expanded(self, app: str) -> bool:
        return self._expanded_apps.get(app) is True

    def raise_window(self, n):
        if n is None:
            return
        token = (
            _n_attr(n, "desktopEntry", "")
            or self._desktop_entry(n)
            or _n_attr(n, "app_name", "")
            or ""
        ).lower()
        if not token:
            return
        script = (
            'addr=$(hyprctl clients -j | jq -r --arg q "$1" \'first(.[] | '
            'select(((.class | if . then ascii_downcase else "" end) | contains($q)) '
            'or ((.initialClass | if . then ascii_downcase else "" end) | '
            "contains($q))) | .address)'); "
            '[ -n "$addr" ] && hyprctl dispatch "hl.dsp.focus({ window = '
            '\\"address:$addr\\" })"'
        )
        try:
            GLib.spawn_async(["sh", "-c", script, "sh", token])
        except GLib.Error:
            pass

    def send(
        self,
        app_name: str,
        summary: str,
        body: str = "",
        timeout: int = -1,
        urgency: int = 1,
    ):
        """Push our own notification through the D-Bus ``Notify`` path.

        ``urgency`` (0 low / 1 normal / 2 critical) is carried in the hints so
        fabric's ``Notification`` exposes it — critical pops anyway under DND.
        """
        hints: dict[str, GLib.Variant] = {}
        if urgency in (0, 1, 2):
            hints = {"urgency": GLib.Variant("y", urgency)}
        params = GLib.Variant(
            "(susssasa{sv}i)",
            (app_name, 0, "", summary, body, [], hints, timeout),
        )
        nid = self._server.new_notification_id()
        self._server._notifications[nid] = Notification(
            id=nid,
            raw_variant=params,
            on_closed=self._server.do_handle_notification_closed,
            on_action_invoked=self._server.do_handle_notification_action_invoke,
        )
        self._server.notification_added(nid)


def _history_key(n) -> str:
    return str(n.get("id", "")) if isinstance(n, dict) else str(getattr(n, "id", ""))


notifs = Notifs()
