"""StatusNotifier system tray for the pill glance row.

Wraps fabric's StatusNotifierService (``fabric.system_tray``) in a Box of
``SystemTrayItem`` buttons: left-click activates the item, right-click pops its
DBusMenu. The width is dynamic — items register/leave at runtime — so the owning
pill re-measures after every add/remove through the same watcher signals.
"""

import re

from fabric.system_tray.widgets import SystemTrayItem, get_tray_watcher
from fabric.widgets.box import Box

# applets the pill renders its own icons for (ukishima Tray.qml): bluetooth
# and network-manager duplicates would just sit in the glance twice
_HIDDEN_APPLETS = re.compile(
    r"nm[ _-]?applet|blueman|network[- ]?manager|bluetooth[- ]?manager",
    re.IGNORECASE,
)


class Tray(Box):
    """A StatusNotifier tray. Icons are icon_size px; call ``set_scale(s)``
    after a ui-scale change to rebuild the item buttons at the new size."""

    def __init__(self, icon_size: int = 20, spacing: int = 8, **kwargs):
        super().__init__(spacing=spacing, name="tray-strip", **kwargs)
        self._icon = int(icon_size)
        self._slots: dict[str, SystemTrayItem] = {}

        self._watcher = get_tray_watcher()
        self._watcher.connect("item-added", self._on_item_added)
        self._watcher.connect("item-removed", self._on_item_removed)

        for identifier in self._watcher.items:
            self._on_item_added(None, identifier)

    @property
    def count(self) -> int:
        """Number of visible (non-filtered) tray items."""
        return len(self._slots)

    @property
    def empty(self) -> bool:
        return not self._slots

    def _is_hidden(self, item) -> bool:
        tooltip = getattr(item, "tooltip", None)
        key = " ".join(
            part
            for part in (
                item.identifier,
                item.title,
                getattr(tooltip, "title", "") or "",
                getattr(tooltip, "description", "") or "",
            )
            if part
        )
        return bool(_HIDDEN_APPLETS.search(key))

    def set_scale(self, s: float):
        icon = int(20 * s)
        if icon == self._icon:
            return
        self._icon = icon
        for slot in tuple(self._slots.values()):
            self.remove(slot)
        self._slots.clear()
        for identifier in self._watcher.items:
            self._on_item_added(None, identifier)

    def _on_item_added(self, _watcher, item_identifier: str):
        item = self._watcher.items.get(item_identifier)
        if item is None or item_identifier in self._slots:
            return
        if self._is_hidden(item):
            return
        slot = SystemTrayItem(item, self._icon)
        self.add(slot)
        self._slots[item_identifier] = slot

    def _on_item_removed(self, _watcher, item_identifier: str):
        slot = self._slots.pop(item_identifier, None)
        if slot is not None:
            self.remove(slot)
