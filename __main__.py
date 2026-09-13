import sys

from gi.repository import GLib

from main import main as _forma_main

# --- fabric-cli IPC helpers ---------------------------------------------------
# `fabric-cli execute forma "launcher()"` — execute runs in the `runpy` module
# scope (the bottom frame of `python -m`), so the helpers below are injected
# into `runpy.__dict__` too. All of them only marshal real work to the main
# loop (D-Bus calls arrive on a worker thread).


def _pills():
    from core import shell

    return tuple(getattr(shell, "WINDOWS", ()))


def launcher():
    GLib.idle_add(_toggle)


def launcher_open():
    GLib.idle_add(_open)


def launcher_close():
    GLib.idle_add(_close)


def clipboard():
    GLib.idle_add(_toggle_clipboard)


def clipboard_open():
    GLib.idle_add(_open_clipboard)


def clipboard_close():
    GLib.idle_add(_close_clipboard)


def _toggle():
    for bar in _pills():
        bar.pill.toggle_launcher()


def _open():
    for bar in _pills():
        bar.pill.open_surface("launcher")


def _close():
    for bar in _pills():
        if bar.pill.state == "surface":
            bar.pill._close_surface()


def _toggle_clipboard():
    for bar in _pills():
        bar.pill.toggle_clipboard()


def _open_clipboard():
    for bar in _pills():
        bar.pill.open_surface("clipboard")


def _close_clipboard():
    for bar in _pills():
        if bar.pill.state == "surface":
            bar.pill._close_surface()


def link():
    GLib.idle_add(_toggle_link)


def link_open():
    GLib.idle_add(_open_link)


def link_close():
    GLib.idle_add(_close_link)


def _toggle_link():
    for bar in _pills():
        bar.pill.toggle_link()


def _open_link():
    for bar in _pills():
        bar.pill.open_surface("link")


def _close_link():
    for bar in _pills():
        if bar.pill.state == "surface":
            bar.pill._close_surface()


def emoji():
    GLib.idle_add(_toggle_emoji)


def emoji_open():
    GLib.idle_add(_open_emoji)


def emoji_close():
    GLib.idle_add(_close_emoji)


def _toggle_emoji():
    for bar in _pills():
        bar.pill.toggle_emoji()


def _open_emoji():
    for bar in _pills():
        bar.pill.open_surface("emoji")


def _close_emoji():
    for bar in _pills():
        if bar.pill.state == "surface":
            bar.pill._close_surface()


def wallpaper():
    GLib.idle_add(_toggle_wallpaper)


def wallpaper_open():
    GLib.idle_add(_open_wallpaper)


def wallpaper_close():
    GLib.idle_add(_close_wallpaper)


def power():
    GLib.idle_add(_toggle_power)


def power_open():
    GLib.idle_add(_open_power)


def power_close():
    GLib.idle_add(_close_power)


def wifi():
    GLib.idle_add(_toggle_wifi)


def wifi_open():
    GLib.idle_add(_open_wifi)


def wifi_close():
    GLib.idle_add(_close_wifi)


def _toggle_power():
    for bar in _pills():
        bar.pill.toggle_power()


def _open_power():
    for bar in _pills():
        bar.pill.open_surface("power")


def _close_power():
    for bar in _pills():
        if bar.pill.state == "surface":
            bar.pill._close_surface()


def _toggle_wifi():
    for bar in _pills():
        bar.pill.toggle_wifi()


def _open_wifi():
    for bar in _pills():
        bar.pill.open_surface("wifi")


def _close_wifi():
    for bar in _pills():
        if bar.pill.state == "surface":
            bar.pill._close_surface()


def bt():
    GLib.idle_add(_toggle_bt)


def bt_open():
    GLib.idle_add(_open_bt)


def bt_close():
    GLib.idle_add(_close_bt)


def _toggle_bt():
    for bar in _pills():
        bar.pill.toggle_bt()


def _open_bt():
    for bar in _pills():
        bar.pill.open_surface("bt")


def _close_bt():
    for bar in _pills():
        if bar.pill.state == "surface":
            bar.pill._close_surface()


bluetooth = bt
bluetooth_open = bt_open
bluetooth_close = bt_close


def _toggle_wallpaper():
    for bar in _pills():
        bar.pill.toggle_wallpaper()


def _open_wallpaper():
    for bar in _pills():
        bar.pill.open_surface("wallpaper")


def _close_wallpaper():
    for bar in _pills():
        if bar.pill.state == "surface":
            bar.pill._close_surface()


def notify(
    *,
    summary: str,
    body: str = "",
    app_name: str = "Pill",
    urgency: int = 1,
):
    from .services.notifs import notifs

    GLib.idle_add(notifs.send, app_name, summary, body, -1, urgency)


def keyboard_layout():
    from .main import keyboard_layout as _kb

    GLib.idle_add(_kb)


def main():
    _forma_main()


# expose the helpers in the scope `fabric-cli execute` actually runs in
for _name in (
    "launcher",
    "launcher_open",
    "launcher_close",
    "clipboard",
    "clipboard_open",
    "clipboard_close",
    "link",
    "link_open",
    "link_close",
    "emoji",
    "emoji_open",
    "emoji_close",
    "wallpaper",
    "wallpaper_open",
    "wallpaper_close",
    "power",
    "power_open",
    "power_close",
    "wifi",
    "wifi_open",
    "wifi_close",
    "bt",
    "bt_open",
    "bt_close",
    "bluetooth",
    "bluetooth_open",
    "bluetooth_close",
    "notify",
    "keyboard_layout",
):
    setattr(sys.modules.get("runpy"), _name, globals()[_name])

main()
