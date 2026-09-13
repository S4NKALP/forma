"""Filesystem paths and Hyprland paths for Forma."""

import os
import tempfile
from pathlib import Path
from typing import Any

import tomlkit
from fabric.utils import GLib

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SOURCE_REF = PROJECT_ROOT / "forma"


def _ensure(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_home() -> Path:
    return Path(GLib.get_user_config_dir())


def cache_home() -> Path:
    return Path(GLib.get_user_cache_dir())


def state_home() -> Path:
    return Path(GLib.get_user_data_dir()) / "state"


def forma_config_dir() -> Path:
    return _ensure(config_home() / "forma")


def forma_cache_dir() -> Path:
    return _ensure(cache_home() / "forma")


def forma_state_dir() -> Path:
    return _ensure(state_home() / "forma")


def config_path() -> Path:
    return PROJECT_ROOT / "config" / "config.toml"


def state_file(name: str) -> Path:
    return forma_state_dir() / name


def hypr_path(*parts: str) -> Path:
    return SOURCE_REF.joinpath(*parts)


def load_config() -> tomlkit.TOMLDocument:
    path = config_path()

    if not path.exists():
        return tomlkit.document()

    return tomlkit.parse(path.read_text())


def save_config(config: tomlkit.TOMLDocument) -> None:
    config_path().write_text(tomlkit.dumps(config))


def ui_scale() -> int:
    return 1


def generate_default_config() -> None:
    """Write a fully-commented config.toml with every setting.

    Called once when config.toml does not exist. Subsequent edits are
    preserved because flags.py does atomic read/write.
    """
    from .flags import _DEFAULTS

    path = config_path()
    if path.exists():
        return

    lines: list[str] = [
        "# forma configuration — auto-generated on first run.",
        "# Edit values below; changes are hot-reloaded at runtime.",
        '# Run `fabric-cli execute forma "generate_config()"` to recreate.',
        "",
    ]

    _sections: list[tuple[str, list[str]]] = [
        (
            "General",
            [
                "dnd",
                "reduceMotion",
                "autoHide",
                "pillOpacity",
                "pillBlur",
                "unloadSec",
            ],
        ),
        (
            "Display",
            [
                "time12h",
                "clockSeconds",
                "mainDisplay",
                "uiScale",
                "topGap",
            ],
        ),
        (
            "Theme",
            [
                "paletteMode",
            ],
        ),
        (
            "Wallpaper",
            [
                "wallpaperDir",
                "wallpaperFit",
                "randomScope",
            ],
        ),
        (
            "Recording",
            [
                "recordCountdown",
                "recordDir",
                "recordFps",
                "recordQuality",
                "recordCursor",
                "recordMic",
                "recordDesktop",
                "recordClearedBefore",
            ],
        ),
        (
            "Colors",
            [k for k in _DEFAULTS if k.startswith("color.")],
        ),
    ]

    _comments: dict[str, str] = {
        "dnd": "Global do-not-disturb",
        "reduceMotion": "Scale all animation durations x0.4",
        "autoHide": "Hide pill when a window overlaps",
        "pillOpacity": "0.0 – 1.0",
        "pillBlur": "Layer blur behind pill",
        "unloadSec": "Memory-saver cooldown after surface close",
        "time12h": "Use 12-hour clock",
        "clockSeconds": "Show seconds tick",
        "mainDisplay": "floating | classic | system | attached",
        "uiScale": "Global scale factor",
        "topGap": "Gap from top of screen (px)",
        "paletteMode": "dark | light | dynamic",
        "wallpaperDir": "Wallpaper folder (empty = ~/Pictures/Wallpapers)",
        "wallpaperFit": "cover | contain | center",
        "randomScope": "Random wallpaper scope",
        "recordCountdown": "Seconds before recording starts",
        "recordDir": "Recording output directory",
        "recordFps": "Recording frame rate",
        "recordQuality": "medium | high | ultra | lossless",
        "recordCursor": "Capture cursor in recording",
        "recordMic": "Capture microphone audio",
        "recordDesktop": "Capture desktop audio",
        "recordClearedBefore": "Watermark for clearing recent recordings",
    }

    for section_name, keys in _sections:
        lines.append(f"# ── {section_name} {'─' * max(1, 56 - len(section_name))}")
        lines.append(f"[{section_name.lower()}]")
        lines.append("")

        for key in keys:
            val = _DEFAULTS[key]
            comment = _comments.get(key, "")
            suffix = f"  # {comment}" if comment else ""
            # Strip "color." prefix for keys inside [colors] section
            toml_key = key.removeprefix("color.")
            lines.append(f"{toml_key} = {_toml_value(val)}{suffix}")

        lines.append("")

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        dir=path.parent,
        prefix=".config.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            raise


def _toml_value(val: Any) -> str:
    """Format a Python value as a TOML literal."""
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, int):
        return str(val)
    if isinstance(val, float):
        return f"{val}"
    if isinstance(val, str):
        return f'"{val}"'
    return repr(val)
