import struct
from pathlib import Path

from fabric.core.service import Property, Service, Signal
from fabric.utils import GLib, logger, os


class EvdevLEDMonitor:
    """Helper to monitor EV_LED state changes without polling."""

    def __init__(self, led_path: Path, target_code: int, on_state_changed_cb):
        self.led_path = led_path
        self.target_code = target_code
        self.on_state_changed_cb = on_state_changed_cb
        self._fd = None
        self._watch_id = None
        self._last_state = None

    def _get_evdev_path(self) -> Path | None:
        if not self.led_path:
            return None
        try:
            device_path = self.led_path / "device"
            for child in device_path.iterdir():
                if child.name.startswith("event"):
                    return Path("/dev/input") / child.name
        except Exception as e:
            logger.warning(
                f"[utils] device_path = self.led_path / 'device' failed: {e}"
            )
        return None

    def start(self):
        evdev_path = self._get_evdev_path()
        if not evdev_path or not evdev_path.exists():
            logger.warning(f"Evdev path not found for {self.led_path}")
            return

        try:
            self._fd = os.open(evdev_path, os.O_RDONLY | os.O_NONBLOCK)
            self._watch_id = GLib.io_add_watch(
                self._fd,
                GLib.PRIORITY_DEFAULT,
                GLib.IOCondition.IN,
                self._on_evdev_data,
            )
        except PermissionError:
            logger.warning(
                f"Permission denied to read {evdev_path}. Add user to 'input' group."
            )
        except Exception as e:
            logger.error(f"Failed to start evdev monitoring: {e}")

    def _on_evdev_data(self, fd, condition):
        try:
            event_size = struct.calcsize("llHHi")
            while True:
                try:
                    data = os.read(fd, event_size)
                except BlockingIOError:
                    break

                if len(data) == event_size:
                    _tv_sec, _tv_usec, type_, code, value = struct.unpack("llHHi", data)
                    if type_ == 17 and code == self.target_code:
                        current_state = bool(value)
                        if current_state != self._last_state:
                            self._last_state = current_state
                            self.on_state_changed_cb(current_state)
        except Exception as e:
            logger.error(f"Evdev error: {e}")
            self.stop()
            return False
        return True

    def stop(self):
        if self._watch_id is not None:
            GLib.source_remove(self._watch_id)
            self._watch_id = None
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError as e:
                logger.warning(f"[utils] os.close(self._fd) failed: {e}")
            self._fd = None


class EvdevLockService(Service):
    """Base service for keyboard lock-key monitoring via evdev LEDs.

    Subclasses must set **class-level** attributes:

    * ``_LED_GLOB`` – sysfs glob for the LED device
      (e.g. ``"input*::numlock"``).
    * ``_LED_INDEX`` – integer LED code passed to :class:`EvdevLEDMonitor`
      (0 = NUML, 1 = CAPSL).
    * ``_SERVICE_NAME`` – human-readable name for log messages.
    """

    _LED_GLOB: str = ""
    _LED_INDEX: int = 0
    _SERVICE_NAME: str = "EvdevLock"

    _instance = None

    @Signal
    def state_changed(self, is_on: bool) -> None:
        """Emitted when the lock-key state changes."""

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        leds = list(Path("/sys/class/leds").glob(self._LED_GLOB))
        self._led_paths = leds
        self._led_path = leds[0] if leds else None
        self._last_state = None
        self._monitoring_started = False
        self._monitors: list[EvdevLEDMonitor] = []

        if self._led_path is None:
            logger.warning(f"[{self._SERVICE_NAME}] Device not found, service disabled")
            return

        # one monitor per matching LED device — a toggle on ANY keyboard (the
        # box here has input2 + input3::capslock) must reach the service, so
        # watch them all, not just the first glob hit.
        for led_path in self._led_paths:
            self._monitors.append(
                EvdevLEDMonitor(led_path, self._LED_INDEX, self._on_state_changed)
            )

    def _on_state_changed(self, is_on: bool):
        if is_on != self._last_state:
            self._last_state = is_on
            self.emit("state_changed", is_on)

    def stop(self):
        for monitor in self._monitors:
            monitor.stop()
        self._monitors.clear()

    def _ensure_monitoring_started(self):
        if self._monitoring_started or not self._led_path:
            return
        self._monitoring_started = True
        self._last_state = self._get_current_state()
        for monitor in self._monitors:
            monitor._last_state = self._last_state
            monitor.start()

    def _get_current_state(self) -> bool:
        if not self._led_path:
            return False
        # any device's LED lit counts as on
        for led_path in self._led_paths:
            brightness_path = led_path / "brightness"
            if not brightness_path.exists():
                continue
            try:
                if int(brightness_path.read_text().strip()):
                    return True
            except (ValueError, OSError) as e:
                logger.warning(f"[{self._SERVICE_NAME}] Failed to read brightness: {e}")
                return False
        return False

    @Property(bool, "read-write", default_value=False)
    def is_on(self) -> bool:
        self._ensure_monitoring_started()
        return self._get_current_state()


class NumLock(EvdevLockService):
    """Service for monitoring NumLock LED state via evdev."""

    _LED_GLOB = "input*::numlock"
    _LED_INDEX = 0  # LED_NUML
    _SERVICE_NAME = "NumLock"

    _instance = None

    @staticmethod
    def get_initial():
        if NumLock._instance is None:
            NumLock._instance = NumLock()
        return NumLock._instance


class CapsLock(EvdevLockService):
    """Service for monitoring CapsLock LED state via evdev."""

    _LED_GLOB = "input*::capslock"
    _LED_INDEX = 1  # LED_CAPSL
    _SERVICE_NAME = "CapsLock"

    _instance = None

    @staticmethod
    def get_initial():
        if CapsLock._instance is None:
            CapsLock._instance = CapsLock()
        return CapsLock._instance
