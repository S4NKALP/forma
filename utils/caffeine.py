"""
Virtual application entries surfaced at the top of the launcher whenever the
query mentions caffeine. Keeps the screen awake via
forma/services/inhibit.py (org.freedesktop.ScreenSaver Inhibit).

Rows:
  * ``caffeine``            — keep awake until toggled off
  * ``caffeine <dur>``      — one-shot equivalent, duration parsed from the
                              query (``90m``, ``1h30m``, ``2h``, ``45s``)
  * ``caffeine off``        — explicit disable, only shown while active

No preset rows: durations are always dynamic from the typed query.
"""

from services.inhibit import get_inhibit_service
from services.notifs import notifs

ICON_KEEP = "caffeine"
ICON_TIMED = "chronometer"

_UNITS = [("d", 86400), ("h", 3600), ("m", 60), ("s", 1)]


def parse_duration(token: str) -> int | None:
    """Parse ``90m``, ``1h30m``, ``2h``, ``45s`` → seconds or None."""
    token = (token or "").strip().lower()
    if not token:
        return None
    i = 0
    seconds = 0
    matched = False
    n = len(token)
    while i < n:
        if not token[i].isdigit():
            return None
        j = i
        while j < n and token[j].isdigit():
            j += 1
        value = int(token[i:j])
        if j >= n:
            return None
        unit = token[j]
        for name, mult in _UNITS:
            if unit == name:
                seconds += value * mult
                matched = True
                break
        else:
            return None
        i = j + 1
    return seconds if matched else None


def fmt_duration(seconds: int) -> str:
    parts = []
    for name, mult in (("d", 86400), ("h", 3600), ("m", 60), ("s", 1)):
        if seconds >= mult:
            q, seconds = divmod(seconds, mult)
            parts.append(f"{q}{name}")
    return "".join(parts) if parts else "0m"


class CaffeineEntry:
    """DesktopEntryWrapper-shaped virtual row backed by InhibitService."""

    appimage_slug = ""
    no_display = False

    def __init__(
        self,
        *,
        id: str,
        name: str,
        icon: str,
        seconds: int | None,
        comment: str,
    ):
        self.id = id
        self.name = name
        self.icon = icon
        self.seconds = seconds
        self.generic_name = comment
        self.comment = comment
        self.categories = []
        self.keywords = ["caffeine", "keep awake", "awake"]

    def execute(self):
        service = get_inhibit_service()
        if self.seconds is None:
            service.enable()
            notifs.send(
                "Caffeine",
                "Keep awake until toggled off",
            )
        elif self.seconds <= 0:
            service.disable()
            notifs.send(
                "Caffeine",
                "Keep awake off",
            )
        else:
            service.enable_timed(self.seconds)
            notifs.send(
                "Caffeine",
                f"Keep awake for {fmt_duration(self.seconds)}",
            )


def caffeine_entries(query: str) -> list[CaffeineEntry]:
    """Virtual caffeine rows for a launcher query (empty list for blank query)."""
    service = get_inhibit_service()
    active = bool(service.active)

    q = (query or "").strip().lower()

    # typed duration → single dynamic row, e.g. ``caffeine 90m``
    tail = q
    for key in ("caffeine", "caff"):
        idx = q.find(key)
        if idx >= 0:
            tail = q[idx + len(key) :].strip()
            break
    if tail:
        seconds = parse_duration(tail.replace("+", " ").replace(" ", ""))
        if seconds:
            label = fmt_duration(seconds)
            return [
                CaffeineEntry(
                    id=f"caffeine-{label}",
                    name=f"Caffeine {label}",
                    icon=ICON_TIMED,
                    seconds=seconds,
                    comment=f"Keep awake for {label}",
                )
            ]

    # bare caffeine query → keep-awake toggle (+ off row while active)
    entries = [
        CaffeineEntry(
            id="caffeine",
            name="Caffeine",
            icon=ICON_KEEP,
            seconds=None,
            comment="Keep awake until toggled off",
        )
    ]
    if active:
        entries.append(
            CaffeineEntry(
                id="caffeine-off",
                name="Turn caffeine off",
                icon="action-unavailable",
                seconds=0,
                comment="Stop keep-awake",
            )
        )
    return entries


def matches(query: str) -> bool:
    """Does this query ask for caffeine?"""
    q = (query or "").strip().lower()
    idx = q.find("caff")
    return idx >= 0 and idx <= 1
