"""DND virtual launcher entry — toggle global do-not-disturb.

Virtual application row surfaced at the top of the launcher whenever the query
mentions dnd / do-not-disturb. Flips the persisted ``dnd`` flag
(forma/config/flags.py), which forma/services/notifs.py honours: popups are
suppressed while set (critical urgency always still pushes).
"""

from core.flags import flags
from services.notifs import notifs

ICON_ON = "notification-disabled-symbolic"
ICON_OFF = "preferences-desktop-notification-bell"


class DndEntry:
    """DesktopEntryWrapper-shaped virtual row backed by the dnd flag."""

    appimage_slug = ""
    no_display = False

    def __init__(self, *, active: bool):
        self.active = active
        self.id = "dnd"
        self.name = "Do Not Disturb"
        self.icon = ICON_ON if active else ICON_OFF
        self.generic_name = (
            "Turn do-not-disturb off" if active else "Turn do-not-disturb on"
        )
        self.comment = self.generic_name
        self.categories = []
        self.keywords = ["dnd", "do not disturb", "notifications", "quiet"]

    def execute(self):
        flags.set_raw("dnd", not self.active)
        if not self.active:
            notifs.send(
                "Do Not Disturb",
                "Do-not-disturb on — notifications muted",
                urgency=2,  # critical pops even under its own gate
            )
        else:
            notifs.send(
                "Do Not Disturb",
                "Do-not-disturb off",
            )


def dnd_entry(query: str) -> DndEntry | None:
    """The single dnd row for a matching query (None for blank/other queries)."""
    if not matches(query):
        return None
    return DndEntry(active=bool(flags.get("dnd")))


def matches(query: str) -> bool:
    """Does this query ask for do-not-disturb?"""
    q = (query or "").strip().lower()
    for needle in ("dnd", "do not disturb", "don't disturb"):
        idx = q.find(needle)
        if idx >= 0 and idx <= 1:
            return True
    return False
