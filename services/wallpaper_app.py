import hashlib
import json
import os
import re
import subprocess
import time
from pathlib import Path

from core import config
from utils.command import run_command

MOTION_EXT = (".gif", ".mp4", ".webm", ".mkv", ".mov")
VIDEO_EXT = (".mp4", ".webm", ".mkv", ".mov")
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".gif", ".webp")
ALL_EXT = IMAGE_EXT + (".mp4", ".webm", ".mkv", ".mov")
FIT_MODES = ("center", "cover", "contain", "stretch")

_THUMB_WIDTH = 512

# --- state paths -------------------------------------------------------------


def state_path(name: str) -> Path:
    return config.state_file(name)


STATE = lambda: state_path("wallpaper")
MAP = lambda: state_path("wallpaper-map")
FIT_STATE = lambda: state_path("wallpaper-fit")
STILL = lambda: state_path("wallpaper-still.png")


def still_for(out: str) -> Path:
    return state_path(f"wallpaper-still-{out}.png") if out else STILL()


def thumb_cache_dir(wpdir: Path) -> Path:
    """Per-folder preview cache keyed on the md5 of the resolved folder path."""
    key = hashlib.md5(str(wpdir).rstrip("/").encode()).hexdigest()
    return config.forma_cache_dir() / "wp-thumbs" / key


# --- predicates --------------------------------------------------------------


def is_video(path: str) -> bool:
    return Path(path).suffix.lower() in VIDEO_EXT


def is_motion(path: str) -> bool:
    return Path(path).suffix.lower() in MOTION_EXT


# --- folder resolution -------------------------------------------------------


def resolve_wpdir(explicit: str = "") -> str:
    """The wallpaper folder, newest decision beats oldest: explicit flag, then
    the resolved state file, then an existing collection in the usual spots."""
    if explicit and Path(explicit).is_dir():
        return explicit
    try:
        resolved = STATE_DIR().read_text().strip()
        if resolved and Path(resolved).is_dir():
            return resolved
    except OSError:
        pass
    for cand in (
        Path.home() / "Pictures" / "Wallpapers",
        Path.home() / "Pictures" / "wallpapers",
        Path.home() / "Wallpapers",
        Path.home() / "wallpapers",
    ):
        if cand.is_dir() and len(list_entries(cand)) >= 2:
            return str(cand)
    return str(Path.home() / "Pictures" / "Wallpapers")


def STATE_DIR() -> Path:
    return state_path("wallpaper-dir")


def write_state_dir(wpdir: str):
    STATE_DIR().write_text(wpdir + "\n")


# --- listing ----------------------------------------------------------------


def _list_sources(wpdir: Path) -> list[Path]:
    sources = []
    try:
        for child in wpdir.iterdir():
            if child.is_file() and child.suffix.lower() in ALL_EXT:
                sources.append(child)
    except OSError:
        return sources
    return sources


def list_entries(wpdir: str | Path) -> list[dict]:
    """Newest-first snapshot: [{path, name, mtime, thumb, kind}]."""
    wpdir = Path(wpdir)
    cache = thumb_cache_dir(wpdir)
    entries = []
    for src in _list_sources(wpdir):
        try:
            st = src.stat()
        except OSError:
            continue
        entries.append(
            {
                "path": str(src),
                "name": src.name,
                "mtime": st.st_mtime,
                "thumb": str(cache / f"{src.name}.png"),
                "motion": is_motion(str(src)),
            }
        )
    entries.sort(key=lambda e: e["mtime"], reverse=True)
    return entries


def read_current() -> str:
    """The currently applied wallpaper path from the global state file."""
    try:
        return STATE().read_text().strip()
    except OSError:
        return ""


# --- thumbnails --------------------------------------------------------------


def _gen_image_thumb(src: Path, cache: Path):
    from PIL import Image, ImageOps

    im = Image.open(src)
    im = ImageOps.exif_transpose(im)
    im.seek(0)
    im.load()
    im = im.convert("RGB")
    im.thumbnail((_THUMB_WIDTH, 4096))
    tmp = cache.with_suffix(".tmp.png")
    im.save(tmp, "PNG", optimize=False)
    os.replace(tmp, cache)


def _gen_video_thumb(src: Path, cache: Path):
    tmp = cache.with_suffix(".tmp.png")
    result = run_command(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(src),
            "-frames:v",
            "1",
            "-vf",
            f"scale={_THUMB_WIDTH}:-2",
            "-f",
            "image2",
            "-c:v",
            "png",
            str(tmp),
        ],
        timeout=60,
    )
    if not result.ok:
        try:
            tmp.unlink()
        except OSError:
            pass
        return
    os.replace(tmp, cache)


def ensure_thumb(src: Path, thumb: Path) -> bool:
    """Generate a missing or stale preview. Returns True when one is present
    afterwards, whether freshly generated or already on disk."""
    try:
        if thumb.exists() and thumb.stat().st_mtime >= src.stat().st_mtime:
            return True
    except OSError:
        return False

    thumb.parent.mkdir(parents=True, exist_ok=True)
    try:
        if is_video(str(src)):
            _gen_video_thumb(src, thumb)
        else:
            _gen_image_thumb(src, thumb)
    except Exception:  # noqa: BLE001 - one bad file must not kill the sweep
        return False
    return thumb.exists()


def _prune_cache(cache: Path, wpdir: Path):
    marker = cache / ".srcdir"
    try:
        if not marker.exists():
            return
        if not (wpdir.exists() and wpdir.is_dir()):
            import shutil

            shutil.rmtree(cache, ignore_errors=True)
            return
        for thumb in cache.glob("*.png"):
            if not (wpdir / thumb.stem).exists():
                try:
                    thumb.unlink()
                except OSError:
                    pass
    except OSError:
        pass


def ensure_thumbs(wpdir: str | Path) -> int:
    """Regenerate every missing or stale preview and prune the cache dirs.

    Returns the number of thumbs present after the sweep (0 when the folder
    does not exist). Sibling cache folders pointing at removed source dirs are
    dropped the way wallpaper-thumbs.sh swept them.
    """
    wpdir = Path(wpdir)
    cache = thumb_cache_dir(wpdir)
    cache.mkdir(parents=True, exist_ok=True)
    try:
        (cache / ".srcdir").write_text(str(wpdir) + "\n")
    except OSError:
        pass
    roots = config.forma_cache_dir() / "wp-thumbs"
    if roots.is_dir():
        for child in roots.iterdir():
            if child.is_dir() and child != cache:
                _prune_cache(child, wpdir)
    for src in _list_sources(wpdir):
        ensure_thumb(src, cache / f"{src.name}.png")
    return len(list(cache.glob("*.png")))


def probe_dims(path: str) -> str:
    """'WxH' resolution badge for a wallpaper (images and videos alike)."""
    try:
        if not Path(path).is_file():
            return ""
        if is_video(path):
            result = run_command(
                [
                    "ffprobe",
                    "-v",
                    "quiet",
                    "-select_streams",
                    "v:0",
                    "-show_entries",
                    "stream=width,height",
                    "-of",
                    "csv=s=x:p=0",
                    path,
                ],
                timeout=10,
            )
            return (result.stdout or "").strip()
        from PIL import Image

        with Image.open(path) as im:
            return f"{im.width}x{im.height}"
    except Exception:  # noqa: BLE001
        return ""


# --- hyprland monitors -------------------------------------------------------


def hyprctl_monitors() -> list[dict]:
    result = run_command(["hyprctl", "monitors", "-j"], timeout=5)
    if not result.ok:
        return []
    try:
        mon = json.loads(result.stdout)
        return mon if isinstance(mon, list) else []
    except ValueError:
        return []


def outputs() -> list[str]:
    return [m.get("name", "") for m in hyprctl_monitors() if m.get("name")]


def focused_output() -> str:
    for m in hyprctl_monitors():
        if m.get("focused"):
            return m.get("name", "")
    return ""


def cursor_output() -> str:
    result = run_command(["hyprctl", "cursorpos"], timeout=5)
    if not result.ok:
        return focused_output()
    match = re.match(r"\s*([-\d.]+)\s*,\s*([-\d.]+)", result.stdout or "")
    if not match:
        return focused_output()
    cx, cy = float(match.group(1)), float(match.group(2))
    for m in hyprctl_monitors():
        w = m.get("width", 0)
        h = m.get("height", 0)
        if int(m.get("transform", 0)) % 2:
            w, h = h, w
        w = w / m.get("scale", 1)
        h = h / m.get("scale", 1)
        if m.get("x", 0) <= cx < m["x"] + w and m.get("y", 0) <= cy < m["y"] + h:
            return m.get("name", "")
    return focused_output()


# --- per-output map ----------------------------------------------------------


def map_get(out: str) -> str:
    try:
        for line in MAP().read_text().splitlines():
            if "\t" in line:
                o, pic = line.split("\t", 1)
                if o == out:
                    return pic
    except OSError:
        pass
    return ""


def map_put(out: str, pic: str):
    lines = []
    try:
        lines = [
            line
            for line in MAP().read_text().splitlines()
            if "\t" in line and line.split("\t", 1)[0] != out
        ]
    except OSError:
        pass
    lines.append(f"{out}\t{pic}")
    MAP().parent.mkdir(parents=True, exist_ok=True)
    tmp = MAP().with_suffix(".tmp")
    tmp.write_text("\n".join(lines) + "\n")
    os.replace(tmp, MAP())


def map_put_all(pic: str):
    MAP().parent.mkdir(parents=True, exist_ok=True)
    tmp = MAP().with_suffix(".tmp")
    content = "".join(f"{o}\t{pic}\n" for o in outputs())
    tmp.write_text(content)
    os.replace(tmp, MAP())


# --- mpvpaper control (videos) -----------------------------------------------

MPV_PAT = "[m]pvpaper"


def mpv_running() -> bool:
    result = run_command(["pgrep", "-f", MPV_PAT], timeout=5)
    return result.ok


def mpv_list() -> list[str]:
    result = run_command(["pgrep", "-af", MPV_PAT], timeout=5)
    return (result.stdout or "").splitlines() if result.ok else []


def _pkill(sig: str):
    run_command(["pkill", f"-{sig}", "-f", MPV_PAT], timeout=5)


def stop_mpv():
    if not mpv_running():
        return
    _pkill("INT")
    for _ in range(10):
        if not mpv_running():
            return
        time.sleep(0.1)
    _pkill("KILL")
    for _ in range(10):
        if not mpv_running():
            return
        time.sleep(0.1)


def _opts_for_fit(fit: str) -> str:
    return {
        "center": "no-audio loop-file=inf hwdec=auto video-unscaled=yes panscan=0",
        "contain": "no-audio loop-file=inf hwdec=auto panscan=0",
        "stretch": "no-audio loop-file=inf hwdec=auto keepaspect=no panscan=0",
        "cover": "no-audio loop-file=inf hwdec=auto panscan=1.0",
    }.get(fit, "no-audio loop-file=inf hwdec=auto panscan=1.0")


def spawn_mpv(out: str, pic: str, opts: str):
    try:
        subprocess.Popen(
            ["mpvpaper", "-p", "-o", opts, out, pic],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        pass


def sync_videos(fit: str, output: str = ""):
    """Re-arm mpvpaper to the desired per-output video set (the map collapsed
    to one shared '*'). Skipped when a video applies to a single output only
    and that output is not the target — a still change on another monitor
    must never restart this monitor's video."""
    outs = outputs()
    if output and output not in outs:
        outs.append(output)
    desired: list[tuple[str, str]] = []
    for o in outs:
        pic = map_get(o)
        if pic and Path(pic).is_file() and is_video(pic):
            desired.append((o, pic))
    n_vid = len(desired)
    shared = n_vid > 0 and n_vid == len(outputs()) and len({p for _, p in desired}) == 1
    if shared:
        desired = [("*", desired[0][1])]

    actual: dict[str, str] = {}
    for line in mpv_list():
        parts = line.split()
        fields = parts[1:] if len(parts) > 1 else []
        match_idx = max(
            (i for i, f in enumerate(fields) if f in outs or f == "*"),
            default=-1,
        )
        if match_idx >= 0:
            actual[fields[match_idx]] = " ".join(fields[match_idx + 1 :])
    if shared and len(set(actual.values())) == 1 and "" not in actual.values():
        actual = {"*": next(iter(actual.values()))}

    wanted = sorted(desired)
    cur = sorted(actual.items())
    opts = _opts_for_fit(fit)
    try:
        if FIT_STATE().read_text() != opts:
            if mpv_running():
                stop_mpv()
                cur = []
            FIT_STATE().parent.mkdir(parents=True, exist_ok=True)
            tmp = FIT_STATE().with_suffix(".tmp")
            tmp.write_text(opts + "\n")
            os.replace(tmp, FIT_STATE())
    except OSError:
        pass

    if [tuple(x) for x in wanted] == cur:
        return
    if mpv_running():
        stop_mpv()
    if not wanted:
        return
    time.sleep(0.8)
    for o, pic in wanted:
        spawn_mpv(o, pic, opts)


# --- awww (stills / gifs) ----------------------------------------------------


def _make_still(src: Path, dst: Path) -> bool:
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(".tmp.png")
    try:
        from PIL import Image

        im = Image.open(src)
        im.seek(0)
        im.load()
        im = im.convert("RGB")
        im.save(tmp, "PNG")
        os.replace(tmp, dst)
        return True
    except Exception:  # noqa: BLE001 - plain fallback below
        try:
            tmp.unlink()
        except OSError:
            pass
    result = run_command(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(src),
            "-frames:v",
            "1",
            "-f",
            "image2",
            "-c:v",
            "png",
            str(tmp),
        ],
        timeout=60,
    )
    if not result.ok:
        try:
            tmp.unlink()
        except OSError:
            pass
        return False
    os.replace(tmp, dst)
    return True


def awww_query() -> bool:
    return run_command(["awww", "query"], timeout=5).ok


def ensure_daemon() -> bool:
    if awww_query():
        return True
    for _ in range(5):
        try:
            subprocess.Popen(
                ["awww-daemon"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError:
            return False
        for _ in range(15):
            time.sleep(0.2)
            if awww_query():
                return True
    return False


def apply_visual(pic: str, output: str = "", fit: str = "cover"):
    """Have aww wave in ``pic`` onto every output (or one) with the current fit."""
    if not ensure_daemon():
        return
    show = pic
    oflag = ["--outputs", output] if output else []
    if is_motion(pic):
        st = still_for(output)
        if _make_still(Path(pic), st):
            show = str(st)
    swww_fit = {
        "center": "no",
        "contain": "fit",
        "cover": "crop",
        "stretch": "stretch",
    }.get(fit, "crop")
    run_command(
        [
            "awww",
            "img",
            *oflag,
            show,
            "--resize",
            swww_fit,
            "--transition-type",
            "wave",
            "--transition-angle",
            "30",
            "--transition-wave",
            "60,30",
            "--transition-fps",
            "60",
            "--transition-step",
            "90",
        ],
        timeout=30,
    )
    if show != pic and not is_video(pic):
        time.sleep(0.9)
        run_command(
            [
                "awww",
                "img",
                *oflag,
                pic,
                "--resize",
                swww_fit,
                "--transition-type",
                "none",
            ],
            timeout=30,
        )


def silent_reapply(fit: str):
    """Refit every output's still without animating (a v--resize change)."""
    if not awww_query():
        return
    swww_fit = {
        "center": "no",
        "contain": "fit",
        "cover": "crop",
        "stretch": "stretch",
    }.get(fit, "crop")
    for o in outputs():
        pic = map_get(o) or read_current()
        if not pic or not Path(pic).is_file():
            continue
        show = pic
        if is_video(pic):
            st = still_for(o)
            if _make_still(Path(pic), st):
                show = str(st)
        run_command(
            [
                "awww",
                "img",
                "--outputs",
                o,
                "--resize",
                swww_fit,
                "--transition-type",
                "none",
                show,
            ],
            timeout=30,
        )


# --- palette / current -------------------------------------------------------

# matugen JSON output shape: {"colors": {<token>: {"dark": {"color": ...}}, ...}}
_MAT_CACHE_DIR = Path.home() / ".cache" / "ukishima"

# ukishima Dyn colors.json key -> matugen material token. The surface ramp and
# accent copy through verbatim; the text tiers are a pragmatic mapping onto the
# on-*/outline roles (outline doubles as the dim tier, outline_variant as faint).
_DYN_MAP = {
    "surface": "surface",
    "surface_container": "surface_container",
    "surface_container_low": "surface_container_low",
    "surface_container_high": "surface_container_high",
    "surface_container_highest": "surface_container_highest",
    "primary": "primary",
    "primary_container": "primary_container",
    "on_primary_container": "on_primary_container",
    "outline": "outline",
    "outline_variant": "outline_variant",
    "cream": "on_surface",
    "bright": "on_primary_fixed",
    "subtle": "on_surface_variant",
    "dim": "outline",
    "faint": "outline_variant",
    "icon_dim": "on_surface_variant",
    "tick_rest": "surface_container_high",
}


def _matugen_args(show: str) -> list[str]:
    """matugen argv (the caller appends ``-j hex``): paletteMode chooses the
    subcommand/mode."""
    from ..config.flags import flags

    mode = flags.get("paletteMode") or "dark"
    if mode == "light":
        return ["image", "-m", "light", "--prefer", "lightness", show]
    if mode == "dynamic":
        return ["image", "-m", "smart", "--prefer", "darkness", show]
    return ["image", "-m", "dark", "--prefer", "darkness", show]


def _matugen_run(args: list[str]) -> dict | None:
    """Run matugen, return its first JSON object (the palette). The user's
    matugen.toml template post-hooks print after the JSON, so raw-decode only
    the head object and ignore the rest."""
    result = run_command(["matugen", *args, "-j", "hex"], timeout=120)
    if not result.ok:
        return None
    try:
        obj, _ = json.JSONDecoder().raw_decode((result.stdout or "").strip())
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


def _write_dyn_colors(obj: dict):
    """Mirror the matugen material tokens into the ukishima colors.json the Dyn
    watcher (and any other ukishima consumer) reads."""
    colors = obj.get("colors") or {}
    mode = obj.get("mode") or ("dark" if obj.get("is_dark_mode") else "light")
    if not isinstance(mode, str):
        return
    out = {}
    for key, tok in _DYN_MAP.items():
        variants = colors.get(tok)
        if isinstance(variants, dict):
            variant = variants.get(mode)
            if isinstance(variant, dict) and variant.get("color"):
                out[key] = variant["color"]
    try:
        _MAT_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = _MAT_CACHE_DIR / "colors.json.tmp"
        tmp.write_text(json.dumps(out, indent=2) + "\n")
        os.replace(tmp, _MAT_CACHE_DIR / "colors.json")
    except OSError:
        pass


def palette_update():
    """matugen the focused monitor's wallpaper (directly), persist the global
    current and poke the apps that read the palette the way wallpaper.sh did."""
    focused = focused_output()
    pic = map_get(focused) if focused else ""
    if not pic or not Path(pic).is_file():
        pic = read_current()
    if not pic or not Path(pic).is_file():
        return

    show = pic
    if is_video(pic):
        st = STILL()
        if not _make_still(Path(pic), st):
            return
        show = str(st)

    STATE().parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE().with_suffix(".tmp")
    tmp.write_text(pic + "\n")
    os.replace(tmp, STATE())

    try:
        obj = _matugen_run(_matugen_args(show))
        if obj is not None:
            _write_dyn_colors(obj)
    except Exception:  # noqa: BLE001, S110 - palette pokes must never block the pick
        pass
    run_command(["hyprctl", "reload"], timeout=5)
    run_command(
        [
            "busctl",
            "--user",
            "call",
            "com.mitchellh.ghostty",
            "/com/mitchellh/ghostty",
            "org.gtk.Actions",
            "Activate",
            "sava{sv}",
            "reload-config",
            "0",
            "0",
        ],
        timeout=5,
    )


# --- public pipeline steps (called by the Walls service threads) --------------


def refresh_pipeline(
    explicit_dir: str,
    flags_dir_setting: str,
) -> tuple[str, list[dict], str]:
    """Full run: resolve folder → generate previews → list newest-first → re-read
    the current marker. Pure, returns (wpdir, entries, current)."""
    wpdir = resolve_wpdir(explicit_dir or flags_dir_setting)
    write_state_dir(wpdir)
    ensure_thumbs(wpdir)
    return wpdir, list_entries(wpdir), read_current()


def warm_probe(entries: list[dict]) -> bool:
    """Cheap liveness probe for the warm path: the newest thumb on disk."""
    if not entries:
        return False
    newest = entries[0]
    return Path(newest["thumb"]).exists()


def apply_pipeline(pic: str, output: str = "", fit: str = "crop") -> None:
    """Do what ``wallpaper.sh set`` did: map ∪ aww → videos → palette."""
    if output:
        map_put(output, pic)
    else:
        map_put_all(pic)
    apply_visual(pic, output, fit)
    sync_videos(fit, output)
    palette_update()


def fit_pipeline(fit: str) -> None:
    """`wallpaper.sh fit` — refit every output in place, respawn videos."""
    fit = fit if fit in FIT_MODES else "cover"
    silent_reapply(fit)
    sync_videos(fit)


def trash_file(path: str) -> bool:
    """Move a wallpaper to the trash via GIO (works on Wayland)."""
    result = run_command(["gio", "trash", path], timeout=15)
    return result.ok
