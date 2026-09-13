"""
Mode semantics :
  * dark   — baked dark seed values, no overrides
  * light  — baked light seed values, no overrides
  * dynamic— seed colors from config.toml / dynamic_colors.css (matugen output)

The single Theme instance is shared (mutable) across all widgets/bars; call
``reload()`` after palette flag changes so every widget sees the new palette
without being re-parented.

"""

import os
import re


def _accent(hex_color: str, alpha: float) -> str:
    r = int(hex_color[1:3], 16)
    g = int(hex_color[3:5], 16)
    b = int(hex_color[5:7], 16)
    return f"rgba({r},{g},{b},{alpha})"


def _camel_to_kebab(name: str) -> str:
    """onPrimary → on-primary, surfaceBright → surface-bright."""
    out = []
    for ch in name:
        if ch.isupper():
            out.append("-")
            out.append(ch.lower())
        else:
            out.append(ch)
    return "".join(out)


def _load_config_colors() -> dict[str, str]:
    """Read color.* keys from config.toml (the authoritative source).

    Supports both sectioned [colors] key="..." and legacy flat color.key="...".
    Keys are normalised to CSS kebab-case (surfaceBright → surface-bright) so
    they line up with dynamic_colors.css tokens and the internal maps.
    """
    try:
        import tomlkit

        from .config import config_path

        path = config_path()
        if not path.exists():
            return {}

        doc = tomlkit.parse(path.read_text(encoding="utf-8"))
        colors: dict[str, str] = {}

        # Sectioned format: [colors] foreground = "..."
        if "colors" in doc and hasattr(doc["colors"], "items"):
            for k, v in doc["colors"].items():
                if isinstance(v, str):
                    colors[_camel_to_kebab(k)] = v

        # Legacy flat format: color.foreground = "..."
        for key in doc:
            if key.startswith("color.") and isinstance(doc[key], str):
                colors[_camel_to_kebab(key[len("color.") :])] = doc[key]

        return colors
    except Exception:
        return {}


def _load_dynamic_colors_css() -> dict[str, str]:
    """Fallback: parse CSS variables from dynamic_colors.css."""
    css_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "styles",
        "dynamic_colors.css",
    )
    if not os.path.exists(css_path):
        return {}

    with open(css_path, "r", encoding="utf-8") as f:
        content = f.read()

    return {
        match.group(1): match.group(2)
        for match in re.finditer(r"--([\w-]+):\s*(#[0-9a-fA-F]{6});", content)
    }


class Theme:
    """Palette. Mutable singleton shared across all bars/widgets."""

    def __init__(self, light: bool = False):
        # ``light`` is a convenience for the initial bake; the authoritative
        # paletteMode is read from flags. reload() applies mode semantics.
        self.light = bool(light)
        self._apply_baked(self.light)
        self._finalize()
        self.reload(light=light)

    def reload(self, light: bool | None = None, mode: str | None = None) -> None:
        """Rebuild the whole palette.

        ``light``  — forced light/dark base; None keeps current
        ``mode``   — paletteMode override; None reads from flags
        """
        from .flags import flags

        if mode is None:
            mode = flags.get("paletteMode") or "dark"

        if light is None:
            light = bool(mode == "light")

        self.light = bool(light)
        self._apply_baked(self.light)

        if mode == "dynamic":
            self._apply_dynamic_colors()
        # dark / light → baked seed only, nothing overrides

        self._finalize()

    def _finalize(self) -> None:
        """Derived translucent tokens (alpha-armed versions of cream)."""
        self.cream_menu = _accent(self.cream, 0.82)
        self.frame_bg = _accent(self.cream, 0.055)
        self.frame_border = _accent(self.cream, 0.10)
        self.hair = _accent(self.cream, 0.13)
        self.hair_soft = _accent(self.cream, 0.08)
        self.sheen = _accent(self.cream, 0.07)
        self.thread_bg = _accent(self.cream, 0.13)

    def _apply_baked(self, light: bool) -> None:
        """Reset to the baked seed palette (dark or light, ukishima identity)."""

        # Baked accents — identical in both modes, only surfaces diverge.
        self.on_glow = "#ff9a64"
        self.verm_lit = "#e0563b"
        self.verm = "#c0442b"
        self.verm_deep = "#a3371f"
        self.verm_dim = "#8a5440"
        self.verm_dim_deep = "#5a3526"
        self.flame_core = "#ffd9c2"
        self.flame_glow = "#ff9a64"
        self.today_warm = "#ffb38a"

        if light:
            self.card_top = "#f6f2ec"
            self.card_bot = "#ece6df"
            self.tile_bg = "#e9e3dc"
            self.border = "#d9d1c8"
            self.cream = "#2a241f"
            self.icon_dim = "#5a524b"
            self.subtle = "#5f574f"
            self.dim = "#6b635c"
            self.faint = "#8a8078"
            self.ghost = "#e3ddd5"
        else:
            self.card_top = "#171717"
            self.card_bot = "#0c0c0c"
            self.tile_bg = "#141414"
            self.border = "#2b2b2b"
            self.cream = "#ececec"
            self.icon_dim = "#bdbdbd"
            self.subtle = "#a8a8a8"
            self.dim = "#8c8c8c"
            self.faint = "#6a6a6a"
            self.ghost = "#242424"

        # Dynamic palette tokens (extra fields set by matugen run)
        self.primary = None
        self.secondary = None
        self.tertiary = None
        self.on_primary = None
        self.on_secondary = None
        self.on_tertiary = None
        self.background = None
        self.foreground = None
        self.error = None
        self.error_dim = None
        self.on_error = None
        self.error_container = None
        self.outline = None
        self.shadow = None
        self.cursor = None
        self.surface = None
        self.surface_bright = None

    def _apply_dynamic_colors(self) -> None:
        """Merge dynamic/matugen colors: config.toml wins over CSS file."""
        css_colors = _load_dynamic_colors_css()
        config_colors = _load_config_colors()
        merged = {**css_colors, **config_colors}

        if not merged:
            return

        # Map dynamic CSS keys → internal theme attributes
        _ATTR_MAP = {
            "foreground": "cream",
            "background": "card_bot",
            "surface-bright": "card_top",
            "surface": "tile_bg",
            "outline": "border",
            "primary": "verm_lit",
            "secondary": "verm",
            "tertiary": "verm_deep",
            "yellow": "on_glow",
            "on-primary": "on_primary",
            "on-secondary": "on_secondary",
            "on-tertiary": "on_tertiary",
            "error": "error",
            "shadow": "shadow",
            "cursor": "cursor",
            "red": "red",
            "red-dim": "red_dim",
            "green": "green",
            "green-dim": "green_dim",
            "yellow-dim": "yellow_dim",
            "blue": "blue",
            "blue-dim": "blue_dim",
            "magenta": "magenta",
            "magenta-dim": "magenta_dim",
            "cyan": "cyan",
            "cyan-dim": "cyan_dim",
            "white": "white",
        }

        # Raw matugen tokens (read-only diagnostics; not part of the ramp)
        self.primary = merged.get("primary")
        self.secondary = merged.get("secondary")
        self.tertiary = merged.get("tertiary")
        self.on_primary = merged.get("on-primary")
        self.on_secondary = merged.get("on-secondary")
        self.on_tertiary = merged.get("on-tertiary")
        self.error = merged.get("error")
        self.background = merged.get("background")
        self.foreground = merged.get("foreground")
        self.outline = merged.get("outline")
        self.shadow_color = merged.get("shadow")
        self.cursor = merged.get("cursor")
        self.error_container = merged.get("error-container")
        self.error_dim = merged.get("error-dim")

        for css_key, attr in _ATTR_MAP.items():
            if color_val := merged.get(css_key):
                setattr(self, attr, color_val)

        # Explicit accent overrides. Read from the merged source so the
        # matugen-generated dynamic_colors.css (format: matugen, see
        # config/matugen/templates/forma.css) fills the full forma accent
        # ramp, while any hand-edited [colors] key in config.toml still wins.
        _ACCENT_MAP = {
            "on-glow": "on_glow",
            "verm-lit": "verm_lit",
            "verm": "verm",
            "verm-deep": "verm_deep",
            "verm-dim": "verm_dim",
            "verm-dim-deep": "verm_dim_deep",
            "flame-core": "flame_core",
            "flame-glow": "flame_glow",
            "today-warm": "today_warm",
            "card-top": "card_top",
            "card-bot": "card_bot",
            "tile-bg": "tile_bg",
            "border": "border",
            "cream": "cream",
            "icon-dim": "icon_dim",
            "subtle": "subtle",
            "dim": "dim",
            "faint": "faint",
            "ghost": "ghost",
        }

        for key_name, attr in _ACCENT_MAP.items():
            if val := merged.get(key_name):
                setattr(self, attr, val)

    @property
    def font_jp(self) -> str:
        return "Zen Kaku Gothic New"

    def accent(self, alpha: float = 1.0) -> str:
        return _accent(self.verm_lit, alpha)

    def colors_css(self) -> str:
        """Palette tokens as @define-color for the GTK stylesheet."""
        tokens = {
            "on-glow": self.on_glow,
            "verm-lit": self.verm_lit,
            "verm": self.verm,
            "verm-deep": self.verm_deep,
            "verm-dim": self.verm_dim,
            "card-top": self.card_top,
            "card-bot": self.card_bot,
            "tile-bg": self.tile_bg,
            "border": self.border,
            "cream": self.cream,
            "icon-dim": self.icon_dim,
            "subtle": self.subtle,
            "dim": self.dim,
            "faint": self.faint,
            "ghost": self.ghost,
        }
        return (
            "\n".join(f"@define-color forma-{k} {v};" for k, v in tokens.items()) + "\n"
        )
