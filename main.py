"""Entry point — mirrors Modus: application first, stylesheet before windows,
one per-monitor Bar registered afterwards, IPC-friendly add_window flow.

Usage:
    uv run main.py
"""

import os
import sys

from fabric import Application
from fabric.utils import Gio, get_relative_path, monitor_file

from core import motion, shell
from core.config import config_path, generate_default_config
from core.flags import flags
from core.theme import Theme
from services.battery import Battery
from services.battery_notifs import BatteryNotifs
from services.keyboard_layout import KeyboardLayout
from services.notifs import notifs


def _setup_css(app: Application, theme: Theme):
    styles_dir = get_relative_path("styles")
    colors_path = os.path.join(styles_dir, "colors.css")
    dynamic_path = os.path.join(styles_dir, "dynamic_colors.css")

    def regenerate():
        os.makedirs(styles_dir, exist_ok=True)
        content = theme.colors_css()
        tmp = colors_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, colors_path)
        app.set_stylesheet_from_file(get_relative_path("styles/main.css"))

    regenerate()

    monitor = monitor_file(styles_dir)

    _busy = False

    def _on_styles_changed(_m, gfile, _other, event, *_a):
        nonlocal _busy
        if event not in (
            Gio.FileMonitorEvent.CHANGED,
            Gio.FileMonitorEvent.CHANGES_DONE_HINT,
            Gio.FileMonitorEvent.CREATED,
            Gio.FileMonitorEvent.MOVED_IN,
            Gio.FileMonitorEvent.DELETED,
        ):
            return

        # Only the two palette files matter. Anything else — our own
        # colors.css.tmp write/replace, editor swap files, unrelated CSS — must
        # never re-trigger regenerate(): that self-loop starves the main loop
        # and freezes the whole shell.
        path = gfile.get_path() or ""
        if os.path.basename(path) not in ("colors.css", "dynamic_colors.css"):
            return

        # Re-entry guard: monitor events are dispatched from the main loop, so
        # a single coalesced event can't run while we're inside regenerate().
        # Guard anyway to absorb bursts (matugen + post-hook IPC arrive back to
        # back) without stacking full stylesheet recompiles.
        if _busy:
            return
        _busy = True
        try:
            if path == colors_path:
                # regenerate() rewrites colors.css inside this same monitored
                # directory; bail when the on-disk copy already matches ours so
                # the self-write can't re-enter. Hand edits to colors.css still
                # re-apply because the content then differs.
                try:
                    with open(colors_path, encoding="utf-8") as f:
                        if f.read() == theme.colors_css():
                            return
                except OSError:
                    return
            elif path == dynamic_path:
                # matugen just rewrote dynamic_colors.css: pull the new palette
                # in before regenerating the @define-color tokens.
                theme.reload()

            regenerate()
        finally:
            _busy = False

    monitor.connect("changed", _on_styles_changed)
    app.connect("shutdown", lambda *_a: monitor.cancel())

    return regenerate


def launcher():
    """Toggle the launcher pill. IPC: `fabric-cli execute forma "launcher()"`."""
    for bar in getattr(shell, "WINDOWS", ()):
        bar.pill.toggle_launcher()


def launcher_open():
    """Open the launcher on every monitor. IPC: `fabric-cli execute forma "launcher_open()"`."""
    for bar in getattr(shell, "WINDOWS", ()):
        bar.pill.open_surface("launcher")


def clipboard():
    """Toggle the clipboard pill. IPC: `fabric-cli execute forma "clipboard()"`."""
    for bar in getattr(shell, "WINDOWS", ()):
        bar.pill.toggle_clipboard()


def clipboard_open():
    """Open the clipboard on every monitor. IPC: `fabric-cli execute forma "clipboard_open()"`."""
    for bar in getattr(shell, "WINDOWS", ()):
        bar.pill.open_surface("clipboard")


def emoji():
    """Toggle the emoji picker. IPC: `fabric-cli execute forma "emoji()"`."""
    for bar in getattr(shell, "WINDOWS", ()):
        bar.pill.toggle_emoji()


def emoji_open():
    """Open the emoji picker on every monitor. IPC: `fabric-cli execute forma "emoji_open()"`."""
    for bar in getattr(shell, "WINDOWS", ()):
        bar.pill.open_surface("emoji")


def link():
    """Toggle the inbox. IPC: `fabric-cli execute forma "link()"`."""
    for bar in getattr(shell, "WINDOWS", ()):
        bar.pill.toggle_link()


def link_open():
    """Open the inbox on every monitor. IPC: `fabric-cli execute forma "link_open()"`."""
    for bar in getattr(shell, "WINDOWS", ()):
        bar.pill.open_surface("link")


def wallpaper():
    """Toggle the wallpaper switcher. IPC: `fabric-cli execute forma "wallpaper()"`."""
    for bar in getattr(shell, "WINDOWS", ()):
        bar.pill.toggle_wallpaper()


def wallpaper_open():
    """Open the wallpaper switcher on every monitor. IPC: `fabric-cli execute forma "wallpaper_open()"`."""
    for bar in getattr(shell, "WINDOWS", ()):
        bar.pill.open_surface("wallpaper")


def notify(*, summary: str, body: str = "", app_name: str = "Pill", urgency: int = 1):
    """Push a pill-owned notification. IPC: `fabric-cli execute forma 'notify(summary="Hi")'`."""
    notifs.send(app_name, summary, body, urgency=urgency)


def generate_config():
    """Regenerate the default config.toml (backing up the existing one).
    IPC: `fabric-cli execute forma "generate_config()"`.
    """
    import os

    path = config_path()
    if path.exists():
        backup = path.with_suffix(".toml.bak")
        os.replace(path, backup)
    generate_default_config()


# Mutable registry filled by main(): the live apply-palette routine. Kept at
# module level so the matugen post_hook (`fabric-cli exec forma 'set_css()'`)
# can hot-apply a freshly generated dynamic_colors.css through the IPC scope.
_theme_appliers: list = []


def set_css():
    """Re-read the palette (matugen dynamic_colors.css / config.toml) and
    re-apply every bar's stylesheet. IPC: `fabric-cli execute forma "set_css()"`
    — wired as the matugen ``[templates.forma]`` post_hook.
    """
    for apply in _theme_appliers:
        try:
            apply()
        except Exception:
            pass


def _fmt_hm(seconds: int) -> str:
    seconds = int(seconds)
    if seconds <= 0:
        return ""
    h, m = divmod(seconds // 60, 60)
    return f"{h}h {m:02d}m" if h else f"{m}m"


def battery():
    """Show the current battery level as a pill notification.

    IPC: `fabric-cli execute forma "battery()"`.
    """

    b = Battery.get_initial()
    if not b.available:
        notifs.send("Battery", "No battery detected")
        return

    p = b.percent
    if b.charged:
        state, tail = "Fully charged", ""
    elif b.charging:
        t = _fmt_hm(b.time_to_full)
        state, tail = "Charging", f" · {t} to full" if t else ""
    elif b.discharging:
        t = _fmt_hm(b.time_remaining)
        state, tail = "Discharging", f" · {t} left" if t else ""
    else:
        state, tail = "Not charging", ""

    notifs.send("Battery", f"{p}% · {state}{tail}", urgency=1)


def keyboard_layout():
    """Cycle to the next keyboard layout (same as the pill's OSD flash).
    IPC: `fabric-cli execute forma "keyboard_layout()"`.
    """
    KeyboardLayout.switch_keyboard_layout()


class _IpcApp(Application):
    """Application whose fabric-cli exec scope resolves this module's IPC verbs."""

    def do_activate(self):
        super().do_activate()
        if self.dbus_client is not None:
            # under `python -m forma` the hook's FileHook captures fabric/runpy's
            # globals, so `fabric-cli exec forma "launcher()"` etc. would raise
            # NameError. Point the exec scope at this module's namespace.
            scope = sys.modules[__name__].__dict__
            self.dbus_client.hook.global_scope = scope
            self.dbus_client.hook.local_scope = scope


def main():
    import faulthandler
    import signal
    import sys

    def _dump(_signum, _frame):
        faulthandler.dump_traceback(file=sys.stderr, all_threads=True)

    signal.signal(signal.SIGUSR1, _dump)

    theme = Theme(light=flags.get("paletteMode") == "light")

    def _sync_reduced(*_a):
        motion.set_reduced(bool(flags.reduce_motion))

    flags.connect("notify::reduce-motion", _sync_reduced)
    _sync_reduced()

    BatteryNotifs.get_initial()

    app = _IpcApp("forma")

    # styling before windows → no unstyled flash (Modus convention)
    regenerate_css = _setup_css(app, theme)

    bars = shell.build(theme)

    def _apply_palette():
        """Full palette reload: theme + stylesheet + every bar."""
        theme.reload()
        regenerate_css()
        for bar in bars:
            if bar.pill is not None:
                bar.pill.refresh_theme()

    _theme_appliers.append(_apply_palette)

    def _reload_theme(*_a):
        """Rebuild the palette in place and re-apply the stylesheet whenever
        palette mode / manual seed change."""
        _apply_palette()

    flags.connect("notify::palette-mode", _reload_theme)

    # Hot-reload on any config.toml edit that affects the palette (Modus
    # on_config_change parity): color tokens, palette mode, manual seed.
    _THEME_KEYS = {"paletteMode"}

    def _on_config_changed(_flags, old: dict, new: dict):
        if any(
            old.get(k) != new.get(k)
            for k in (_THEME_KEYS.__or__({k for k in new if k.startswith("color.")}))
        ):
            _apply_palette()

    flags.connect("config-changed", _on_config_changed)

    # register windows with the application, then hand off to the main loop
    for bar in bars:
        app.add_window(bar.reserve)
        app.add_window(bar.input)

    app.run()


if __name__ == "__main__":
    main()
