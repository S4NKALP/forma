"""OSD orchestration sheet for the pill.

Pure mixin — composed into :class:`~forma.core.pill.Pill`. Owns the service ->
flash wiring (workspace nav, audio, brightness, lock keys, battery, MPRIS) and
the cross-fading ``osd`` face behaviour the core ``_morph_to("osd")`` drives.
"""

import time as _time
import urllib.parse

from fabric.audio.service import Audio
from gi.repository import GLib

from core.motion import FAST, tween
from services.battery import Battery
from services.brightness import Brightness
from services.keyboard_layout import KeyboardLayout
from services.lock_key import CapsLock, NumLock
from services.mpris import PlayerManager
from services.notifs import notifs


class PillOSDMixin:
    def _init_osd_services(self):
        """Wire every OSD source (workspace, audio, brightness, lock keys,
        battery, MPRIS) to this pill's flash orchestrator."""
        # --- workspace OSD -----------------------------------------------------
        self.ws_osd.connect("workspace_activated", self._on_workspace_switch)
        # the connection's synchronous populate (workspace_activated for the
        # already-active ws) fires before this widget can connect — latch its
        # result here so the FIRST real navigation after boot flashes instead
        # of being swallowed as a phantom populate
        self._boot_ws: int | None = self.ws_osd._active_workspace

        # --- audio OSD ---------------------------------------------------------
        # fabric Audio drives the volume/mic flashes. Streams other than the
        # current defaults are rebindable through speaker/microphone_changed.
        self._osd_streams: dict[str, tuple] = {
            "volume": (None, None, None),
            "mic": (None, None, None),
        }
        self._audio = Audio()
        self._audio.connect("speaker_changed", lambda *_a: self._bind_audio("volume"))
        self._audio.connect("microphone_changed", lambda *_a: self._bind_audio("mic"))
        self._bind_audio("volume")
        self._bind_audio("mic")

        # --- brightness OSD -----------------------------------------------------
        # Brightness.screen emits a percent (0-100) on any real change
        # (threshold ≥ 1%), including external ones — always flash, like audio.
        self._brightness = Brightness()
        self._brightness.connect("screen", self._on_brightness_changed)

        # --- lock-key OSD -------------------------------------------------------
        # Caps/Num lock LED monitors emit state_changed only on real toggles.
        self._lock_keys = {
            "caps": CapsLock.get_initial(),
            "num": NumLock.get_initial(),
        }
        for kind, svc in self._lock_keys.items():
            svc.connect(
                "state_changed",
                lambda *_a, k=kind: self._on_lock_changed(k, *_a),
            )
            # the service starts its evdev watches lazily from the `is_on`
            # getter; read it once here or real toggles never arrive
            _ = svc.is_on

        # --- keyboard-layout OSD ------------------------------------------------
        # flashes the layout code whenever the active layout rotates (external
        # switches and switchxkblayout alike, via the activelayout socket)
        self._kblayout = KeyboardLayout.get_initial()
        self._kblayout.connect("layout_changed", self._on_kb_layout_changed)
        # prime the service so its hyprctl seed runs early
        _ = self._kblayout.code

        # --- battery OSD -------------------------------------------------------
        # ukishima flashes the battery OSD only when charging turns on
        # (Osd.qml onChargingChanged -> flash("battery")); the service's single
        # `changed` signal covers every device property update, so gate on a
        # false->true edge of `charging` to avoid re-flashing every charge tick.
        self._battery = Battery.get_initial()
        self._battery_charging = self._battery.charging
        self._battery_discharging = self._battery.discharging
        self._battery.connect("changed", self._on_battery_changed)

        # --- track OSD ----------------------------------------------------------
        # any player's start/pause/track-change flashes the track face (ukishima
        # `announce`); the most recent announcing player is the flash subject.
        self._players = PlayerManager()
        self._player_subject = None
        self._player_flash_ts: dict[str, float] = {}
        self._player_seed: dict[str, str] = {}
        self._last_track_title = ""
        self._players.connect(
            "new_player",
            lambda sender, name, svc, *args: self._on_new_player(name, svc),
        )
        for _svc in self._players.get_all_services().values():
            self._bind_player(_svc)

    # --- gating ---------------------------------------------------------------

    def _interactive(self) -> bool:
        """Expanded pill states that swallow transient OSD flashes."""
        return self.state in ("hover", "pinned", "surface", "dragover")

    def set_osd_suppressed(self, value: bool):
        """Transient OSDs yield while a surface owns the pill (Osd.qml `suppressed`)."""
        self._suppressed = bool(value)

    # --- workspace OSD -------------------------------------------------------

    def _on_workspace_switch(self, _widget, ws_id):
        # special workspaces are negative ids — never flash the indicator for one
        if ws_id is None or ws_id < 1:
            return
        # the connection-time populate latches the boot workspace instead of
        # flashing; every real navigation from here on flashes
        if self._boot_ws is None:
            self._boot_ws = ws_id
            return
        if self._suppressed:
            return
        # skip while the pill is already showing its live dots (expanded pill)
        if self._interactive():
            return
        self._flash_osd("ws")

    # --- audio OSD -----------------------------------------------------------

    def _bind_audio(self, kind: str):
        stream = self._audio.speaker if kind == "volume" else self._audio.microphone
        if stream is None:
            return
        # fabric re-fires speaker/microphone_changed for the same default stream
        # repeatedly — a straight rebind would churn (and can consume a pending
        # is-muted notify by disconnecting right when it lands), so bind each
        # stream exactly once and only swap on a genuinely new object
        if self._osd_streams[kind][0] is stream:
            return
        prev, vol_id, mut_id = self._osd_streams[kind]
        if prev is not None:
            if vol_id is not None:
                prev.handler_disconnect(vol_id)
            if mut_id is not None:
                prev.stream.handler_disconnect(mut_id)
        vol_id = stream.connect(
            "notify::volume", lambda *_a, k=kind: self._on_audio_change(k)
        )
        # fabric's Service exposes `muted` but its GObject prop is `volume`/etc;
        # the raw Cvc stream carries is-muted — connect there so mute flips land
        mut_id = stream.stream.connect(
            "notify::is-muted", lambda *_a, k=kind: self._on_audio_change(k)
        )
        self._osd_streams[kind] = (stream, vol_id, mut_id)

    def _on_audio_change(self, kind: str):
        if self._suppressed:
            return
        if self._interactive():
            return
        stream = self._audio.speaker if kind == "volume" else self._audio.microphone
        if stream is None:
            return
        # no value dedupe: an event at 100% (or a mute toggle at the same
        # volume) must still flash the OSD open
        self._flash_osd(kind, stream.volume / 100.0, bool(stream.muted))

    def _on_brightness_changed(self, *_a, percent=None):
        if self._suppressed:
            return
        if self._interactive():
            return
        if percent is None:
            percent = float(_a[-1]) if _a else 0.0
        # service already dedupes changes below its threshold; always flash here
        self._flash_osd("brightness", percent / 100.0)

    def _on_lock_changed(self, kind: str, *_a):
        if self._suppressed:
            return
        if self._interactive():
            return
        is_on = bool(_a[-1]) if _a else False
        self._flash_osd("lock", is_on, lock_key=kind)

    def _on_kb_layout_changed(self, _svc, layout: str, *_a):
        if self._suppressed:
            return
        if self._interactive():
            return
        self._flash_osd("kb", layout)

    def _on_battery_changed(self, *_a):
        if self._suppressed:
            return
        if self._interactive():
            return
        # flash on the true-edge of either operating mode: plugging in starts
        # charging, unplugging starts discharging. The charge->full stop
        # (charging True->False while still on AC, plus top-off flickers) is
        # neither — skip it so a plugged, full battery never shows a
        # "disconnected" OSD.
        now_charging = self._battery.charging
        now_discharging = self._battery.discharging
        if now_charging and now_charging != self._battery_charging or now_discharging and now_discharging != self._battery_discharging:
            should_flash = True
        else:
            should_flash = False
        self._battery_charging = now_charging
        self._battery_discharging = now_discharging
        if not should_flash:
            return
        self._flash_osd("battery", self._battery.percent / 100.0)

    # --- track OSD -----------------------------------------------------------

    def _on_new_player(self, name: str, svc):
        self._bind_player(svc)

    def _bind_player(self, svc):
        # snapshot this player's title at bind time so the boot-time seed emit
        # (mpris _refresh_initial_state re-announces the current track ~500 ms
        # after forma starts) is suppressed — an already-playing track must not
        # flash the OSD on first init; only real next/prev changes flash.
        seed = getattr(svc, "title", "") or ""
        if seed and not self._player_seed.get(svc.player_name):
            self._player_seed[svc.player_name] = seed
        svc.connect("play", lambda *_a, s=svc: self._on_player_gate(s, "play"))
        svc.connect("pause", lambda *_a, s=svc: self._on_player_gate(s, "pause"))
        svc.connect("meta_change", lambda *_a, s=svc: self._on_player_meta(s))
        svc.connect("artwork_change", lambda *_a, s=svc: self._on_player_art(s))

    def _player_rate_limited(self, svc) -> bool:
        """Collapse bursty player chatter (autoplay churn) to one flash per 400 ms."""
        now = _time.monotonic()
        last = self._player_flash_ts.get(svc.player_name, 0.0)
        if now - last < 0.4:
            return True
        self._player_flash_ts[svc.player_name] = now
        return False

    def _on_player_gate(self, svc, kind):
        # User requested to only show OSD when track changes (next/prev),
        # not on pause/play (which triggers during seeking).
        pass

    def _on_player_meta(self, svc):
        if self._suppressed or self._interactive():
            return
        title = getattr(svc, "title", "") or ""
        if not title or title == self._last_track_title:
            return
        # browser hover/scrape previews (YouTube thumbnails) advertise a bare
        # site root as the track url — never flash those, only real tracks
        if self._is_scrape_preview(svc):
            return
        # the post-boot seed announce re-emits the same title captured at bind —
        # discard it once (a genuine change pops the seed and flashes below)
        seed = self._player_seed.pop(svc.player_name, None)
        if seed is not None and title == seed:
            return
        if self._player_rate_limited(svc):
            return
        self._publish_track(svc)

    def _is_scrape_preview(self, svc) -> bool:
        """A browser hover/scrape advertises the site root as the track URL
        (firefox sets xesam:url to the page, not the hovered video), so
        flicking across YouTube thumbnails churns title + art per preview.
        Real tracks carry a resource path (or no URL at all) — only those
        deserve the OSD; a bare root URL is a preview, never a track."""
        m = getattr(svc, "metadata", None) or {}
        raw = m.get("xesam:url") if isinstance(m, dict) else None
        if raw is None:
            return False
        if isinstance(raw, str):
            url = raw
        else:
            url = raw.get_string() if raw.get_type_string() == "s" else ""
        if not url:
            return False
        parsed = urllib.parse.urlparse(url)
        return parsed.scheme in ("http", "https") and parsed.path in ("", "/")

    def _publish_track(self, svc):
        self._player_subject = svc
        self._last_track_title = getattr(svc, "title", "") or ""
        self.player_osd.flash(svc)
        self._flash_osd("track")

    def _on_player_art(self, svc):
        # a cover that lands a beat after the title (async browser players)
        # still earns its moment — extend the visible flash
        if svc is not self._player_subject:
            return
        if self.state != "osd" or self._osd_kind != "track":
            return
        self.player_osd.update_art(svc.get_artwork())
        self._extend_osd(1300)

    # --- flash / face lifecycle ---------------------------------------------

    def _flash_osd(
        self,
        kind="ws",
        value: float | None = None,
        muted=False,
        lock_key: str = "caps",
    ):
        # an OSD flash covers the toast — retire non-critical popups so the
        # expanded pill doesn't sit on top of a stale bubble (Pill.qml:253)
        if notifs.popups and not notifs.toast_critical:
            notifs.clear_popups()
        self._cancel_osd_hide()
        self._osd_kind = kind
        self.ws_osd.set_visible(False)
        self.audio_osd.set_visible(False)
        self.brightness_osd.set_visible(False)
        self.lock_key_osd.set_visible(False)
        self.keyboard_osd.set_visible(False)
        self.battery_osd.set_visible(False)
        self.player_osd.set_visible(False)
        if kind == "ws":
            self.ws_osd.set_no_show_all(False)
            self.ws_osd.set_visible(True)
            self.ws_osd.show_all()
        elif kind == "kb":
            self.keyboard_osd.set_no_show_all(False)
            self.keyboard_osd.set_visible(True)
            self.keyboard_osd.show_all()
            self.keyboard_osd.flash(str(value or ""))
        elif kind == "brightness":
            self.brightness_osd.set_no_show_all(False)
            self.brightness_osd.set_visible(True)
            self.brightness_osd.show_all()
            self.brightness_osd.flash(value if value is not None else 0.0)
        elif kind == "lock":
            self.lock_key_osd.set_no_show_all(False)
            self.lock_key_osd.set_visible(True)
            self.lock_key_osd.show_all()
            self.lock_key_osd.flash(lock_key, bool(value))
        elif kind == "battery":
            self.battery_osd.set_no_show_all(False)
            self.battery_osd.set_visible(True)
            self.battery_osd.show_all()
            pct = (
                int(round(value * 100)) if value is not None else self._battery.percent
            )
            self.battery_osd.flash(pct, self._battery.charging)
        elif kind == "track":
            self.player_osd.set_no_show_all(False)
            self.player_osd.set_visible(True)
            self.player_osd.show_all()
        else:
            self.audio_osd.set_no_show_all(False)
            self.audio_osd.set_visible(True)
            self.audio_osd.show_all()
            self.audio_osd.flash(kind, value if value is not None else 0.0, muted)
        self._osd_face.set_visible(True)
        self._fade_osd(1.0)
        self._morph_to("osd")
        self._osd_hide_id = GLib.timeout_add(1800, self._hide_osd)

    def _hide_osd(self):
        self._osd_hide_id = None
        if self.state == "osd":
            if notifs.popups:
                # a toasts was waiting under the OSD — hand the pill over
                self._toast.set_notif(notifs.popups[-1])
                self._toast.set_live(True)
                self._morph_to("toast")
            else:
                self._morph_to("pinned" if self._pinned else "rest")
        return GLib.SOURCE_REMOVE

    def _extend_osd(self, ms: int):
        """Extend the visible flash (late artwork landing on the track face)."""
        if self.state != "osd":
            return
        self._cancel_osd_hide()
        self._osd_hide_id = GLib.timeout_add(int(ms), self._hide_osd)

    def _cancel_osd_hide(self):
        if self._osd_hide_id is not None:
            GLib.source_remove(self._osd_hide_id)
            self._osd_hide_id = None

    def _fade_osd(self, target: float):
        if self._osd_fade is not None:
            self._osd_fade.stop()
            self._osd_fade = None
        current = self._osd_face.get_opacity()

        def step(_t, k):
            self._osd_face.set_opacity(
                max(0.0, min(1.0, current + (target - current) * k))
            )
            if target == 0 and k >= 1.0:
                self._osd_face.set_visible(False)

        self._osd_fade = tween(FAST, step)
