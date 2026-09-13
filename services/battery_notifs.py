"""Battery charge alarms — Notifications at the low/critical/full frontiers.

Battery latch model (fire once per discharge/charge cycle,
re-arm once the charge recovers, critical repeats every 10 minutes until
plugged) with the thresholds asked for here:

- 20%  → "Low battery"          normal urgency, once per discharge
- 10%  → "Critical battery"     critical urgency (pops even under DND),
                                then repeats every 10 min until recharged
- 100% → "Fully charged"        once per charge event

Wired at shell startup via ``BatteryNotifs.get_initial()``. Inert on hosts
with no system battery (``Battery.available`` gates every evaluation).
"""

from fabric.utils import GLib

from services.battery import Battery
from services.notifs import CRITICAL, notifs

_LOW_PCT = 20
_CRIT_PCT = 10
_REARM_PCT = 25  # re-arm the alarm latches once the charge recovers this far
_FULL_REDISCHARGE_PCT = 95  # drop the "full" latch after the battery starts draining
_CRIT_REPEAT_MS = 10 * 60 * 1000

_APP = "Battery"


class BatteryNotifs:
    """Singleton observer that turns ``Battery.changed`` into charge alarms."""

    _instance = None

    @classmethod
    def get_initial(cls) -> BatteryNotifs:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self, **kwargs):
        self._battery = Battery.get_initial()
        self._low_fired = False
        self._crit_fired = False
        # a battery that is already full at login is not news — arm the latch
        # so "fully charged" only fires on a fresh *transition* to full
        self._full_fired = bool(self._battery.charged)
        self._crit_source: int | None = None
        self._battery.connect("changed", self._on_changed)
        self._on_changed()

    # --- alarm evaluation -----------------------------------------------------

    def _on_changed(self, *_a):
        b = self._battery
        if not b.available:
            return
        p = b.percent
        charging = b.charging
        discharging = b.discharging

        # re-arm once the charge recovers or a plug event flips the direction
        if charging or p >= _REARM_PCT:
            self._low_fired = False
            self._crit_fired = False
        if discharging or p < _FULL_REDISCHARGE_PCT:
            self._full_fired = False

        if discharging and p <= _LOW_PCT and not self._low_fired:
            self._low_fired = True
            self._send("Low battery", f"{p}% remaining — plug in soon")
        if discharging and p <= _CRIT_PCT and not self._crit_fired:
            self._crit_fired = True
            self._send(
                "Critical battery",
                f"{p}% remaining — plug in now",
                critical=True,
            )
        if b.charged and not self._full_fired:
            self._full_fired = True
            self._send(
                "Battery fully charged",
                "Charge complete — power is full",
            )

        self._sync_crit_repeat(discharging and p <= _CRIT_PCT)

    def _sync_crit_repeat(self, active: bool):
        if active and self._crit_source is None:

            def tick():
                if not (
                    self._battery.available
                    and self._battery.discharging
                    and self._battery.percent <= _CRIT_PCT
                ):
                    self._crit_source = None
                    return GLib.SOURCE_REMOVE
                self._send(
                    "Critical battery",
                    f"{self._battery.percent}% remaining — plug in now",
                    critical=True,
                )
                return GLib.SOURCE_CONTINUE

            self._crit_source = GLib.timeout_add(_CRIT_REPEAT_MS, tick)
        elif not active and self._crit_source is not None:
            GLib.source_remove(self._crit_source)
            self._crit_source = None

    def _send(self, summary: str, body: str, critical: bool = False):
        notifs.send(_APP, summary, body, urgency=CRITICAL if critical else 1)
