"""Track (music-player) OSD face  ``trackRow``.

Album cover + scrolling title/artist + app icon, sized 344x64·s (ukishima
``desiredW/H`` for the ``track`` kind). Mirrors the reference MediaOSD layout
(art | meta | app icon), the two long text fields are ``ScrollingLabel`` so a
long track title marquees instead of truncating. A press anywhere toggles the
source player (PlayPause).
"""

from fabric.widgets.box import Box
from fabric.widgets.image import Image
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk

from components.scrolling_label import ScrollingLabel

_FALLBACK_ICON = "audio-x-generic-symbolic"
_COVER_S = 44  # ukishima coverBox 44 x 44 * s


class PlayerOsd(Box):
    """344-wide track flash: cover + scrolling metadata + app icon."""

    def __init__(self, theme, s: float = 1.0):
        super().__init__(
            spacing=int(12 * s),
            h_align="center",
            v_align="center",
            style=(f"padding-left: {int(24 * s)}px;padding-right: {int(24 * s)}px;"),
        )
        self.set_name("media-osd")
        self._theme = theme
        self._s = s
        self._player = None
        self._last_art = ""

        self.set_events(self.get_events() | Gdk.EventMask.BUTTON_PRESS_MASK)
        self.connect("button-press-event", self._on_press)

        # album art: 44·s square plain image, no backdrop
        self._cover = Image(icon_name=_FALLBACK_ICON)
        self._cover.get_style_context().add_class("osd-album-art")
        self._cover.set_size_request(int(_COVER_S * s), int(_COVER_S * s))

        self._title = ScrollingLabel(
            text="No Media",
            speed=0.8,
            pause_ms=2000,
            max_width=int(170 * s),
            name="osd-track-title",
        )
        self._title.set_halign(Gtk.Align.START)
        self._title.get_style_context().add_class("osd-media-title")

        self._artist = ScrollingLabel(
            text="...",
            speed=0.8,
            pause_ms=2000,
            max_width=int(170 * s),
            name="osd-track-artist",
        )
        self._artist.set_halign(Gtk.Align.START)
        self._artist.get_style_context().add_class("osd-media-artist")
        self._artist.set_visible(False)

        meta = Box(
            orientation="v",
            spacing=int(3 * s),
            h_expand=True,
            v_align="center",
        )
        meta.add(self._title)
        meta.add(self._artist)

        self._app_icon = Image(
            icon_name="multimedia-audio-player", icon_size=int(24 * s)
        )
        self._app_icon.get_style_context().add_class("osd-media-app")
        self._app_icon.set_opacity(0.85)

        self.set_size_request(int(344 * s), int(64 * s))
        self.add(self._cover)
        self.add(meta)
        self.add(self._app_icon)

        self._title_provider = Gtk.CssProvider()
        self._artist_provider = Gtk.CssProvider()
        self._title.get_style_context().add_provider(
            self._title_provider, Gtk.STYLE_PROVIDER_PRIORITY_USER
        )
        self._artist.get_style_context().add_provider(
            self._artist_provider, Gtk.STYLE_PROVIDER_PRIORITY_USER
        )
        self._refresh_fonts()

        self.set_no_show_all(True)
        self.set_visible(False)

    def _refresh_fonts(self):
        s = self._s
        title_css = (
            f"#osd-track-title {{"
            f"color: {self._theme.cream};"
            f"font-size: {int(14 * s)}px;"
            f"font-weight: 600;"
            f"}}"
        )
        artist_css = (
            f"#osd-track-artist {{"
            f"color: {self._theme.dim};"
            f"font-size: {int(11 * s)}px;"
            f"}}"
        )
        self._title_provider.load_from_data(title_css.encode())
        self._artist_provider.load_from_data(artist_css.encode())

    # --- data -----------------------------------------------------------------

    @staticmethod
    def _join_artist(player) -> str:
        artists = getattr(player, "artist", None)
        if isinstance(artists, str):
            return artists
        if isinstance(artists, (list, tuple)):
            return ", ".join(str(a) for a in artists)
        return ""

    def _set_app_icon(self, player_name: str):
        """Brand icon from the player bus name; unknown → generic audio."""
        name = (player_name or "").lower()
        icon = "multimedia-audio-player"
        for probe, cand in (
            ("spotify", "spotify"),
            ("firefox", "firefox"),
            ("vlc", "vlc"),
            ("mpv", "mpv"),
            ("chromium", "chromium"),
            ("brave", "web-browser"),
            ("chrome", "web-browser"),
        ):
            if probe in name:
                icon = cand
                break
        theme = Gtk.IconTheme.get_default()
        if not theme.has_icon(icon):
            icon = "multimedia-audio-player"
        self._app_icon.set_from_icon_name(icon, int(24 * self._s))

    def flash(self, player):
        """Snapshot a PlayerService into the track face."""
        self._player = player
        title = getattr(player, "title", "") or "No Media"
        artist = self._join_artist(player)

        self._title.set_label(title)
        if artist:
            self._artist.set_label(artist)
            self._artist.set_visible(True)
        else:
            self._artist.set_visible(False)

        self._set_app_icon(
            getattr(player, "player_name", "") or getattr(player, "app", "")
        )

        art = getattr(player, "get_artwork", lambda: "")()
        self.update_art(art)

    def update_art(self, path: str):
        """Freshen the cover from a local art path (no-op for pathless art)."""
        if not path or path == self._last_art:
            return
        self._last_art = path
        size = int(_COVER_S * self._s)
        try:
            pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, size, size, True)
        except GLib.Error:
            pixbuf = None
        if pixbuf is not None:
            self._cover.set_from_pixbuf(pixbuf)
        else:
            self._cover.set_from_icon_name(_FALLBACK_ICON, size // 3)
        self._cover.set_size_request(size, size)
        self._cover.show_all()

    def _on_press(self, _widget, _event):
        if self._player is not None:
            try:
                self._player.play_pause()
            except Exception:
                pass
        return False

    def set_scale(self, s: float):
        self._s = s
        self.set_size_request(int(344 * s), int(64 * s))
        self._cover.set_size_request(int(_COVER_S * s), int(_COVER_S * s))
        self._title.max_width_limit = int(170 * s)
        self._artist.max_width_limit = int(170 * s)
        art = self._last_art
        if art:
            self._last_art = ""
            self.update_art(art)
        self._set_app_icon(getattr(self._player, "player_name", "") or "")
        self._refresh_fonts()
