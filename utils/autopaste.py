import subprocess

from fabric.utils import GLib, logger


def trigger_autopaste(delay_ms: int = 150) -> None:
    """Paste the clipboard into the currently focused Wayland surface."""

    def do_paste() -> bool:
        try:
            subprocess.run(
                ["wtype", "-M", "ctrl", "-k", "v", "-m", "ctrl"],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError, subprocess.SubprocessError:
            try:
                subprocess.run(
                    ["ydotool", "key", "29:1", "47:1", "47:0", "29:0"],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except (FileNotFoundError, subprocess.SubprocessError) as exc:
                logger.warning("Autopaste failed: %s", exc)

        return False

    GLib.timeout_add(delay_ms, do_paste)
