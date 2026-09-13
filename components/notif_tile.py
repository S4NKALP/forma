"""
A rounded tile showing the notification's image cropped to fill (Just the qt
``Image.fillMode PreserveAspectCrop``) or a small tilted diamond in the accent
when the notification carries no image. Sizing and radius are caller's choice so
the toast (28·s), inbox row (16·s) and group head (20·s) tiles stay one
component.
"""

from fabric.utils import GdkPixbuf, GLib
from fabric.widgets.box import Box
from fabric.widgets.image import Image
from fabric.widgets.label import Label

from core.theme import Theme


def _crop_fill(pixbuf: GdkPixbuf.Pixbuf, size: int) -> GdkPixbuf.Pixbuf:
    """Center-crop the pixbuf into a ``size`` square (PreserveAspectCrop)."""
    w = pixbuf.get_width()
    h = pixbuf.get_height()
    if w <= 0 or h <= 0:
        return pixbuf
    scale = max(size / w, size / h)
    nw = max(size, int(round(w * scale)))
    nh = max(size, int(round(h * scale)))
    scaled = pixbuf.scale_simple(nw, nh, GdkPixbuf.InterpType.BILINEAR)
    x = (nw - size) // 2
    y = (nh - size) // 2
    return GdkPixbuf.Pixbuf.new_subpixbuf(scaled, x, y, size, size)


class NotifTile(Box):
    """Tiny tile: image (crop-fill) or accent diamond, on ``tileBg``."""

    def __init__(
        self,
        theme: Theme,
        s: float = 1.0,
        size: int = 28,
        critical: bool = False,
        **kwargs,
    ):
        super().__init__(
            size=(int(size * s), int(size * s)),
            style=(
                f"background: {theme.tile_bg};"
                f"border: 1px solid {theme.border};"
                f"border-radius: {max(4, int(9 * s))}px;"
            ),
            **kwargs,
        )
        self._theme = theme
        self._s = s
        self._size = int(size * s)
        self._critical = critical

        self._image = Image()
        self._image.set_size_request(self._size, self._size)
        # Center the image
        self._image.set_halign(3)  # Gtk.Align.CENTER
        self._image.set_valign(3)
        self._image.set_no_show_all(True)
        self.add(self._image)

        self._diamond = Label(
            label="◆",
            h_align="center",
            v_align="center",
            h_expand=True,
            v_expand=True,
            style=(
                f"color: {theme.verm_lit if critical else theme.verm};"
                f"font-size: {max(7, int(0.3 * self._size))}px;"
            ),
        )
        self._diamond.set_no_show_all(True)

    def set_from_notif(self, n):
        """Render a live Notification or a history-dict entry into this tile."""
        pixbuf, icon_name = self._resolve_notif_image(n)

        children = self.get_children()

        if pixbuf is None and not icon_name:
            self._image.clear()
            self._image.hide()
            if self._diamond not in children:
                self.add(self._diamond)
                self._diamond.show()
            return

        if self._diamond in children:
            self.remove(self._diamond)

        self._image.show()

        if pixbuf:
            self._image.set_pixel_size(-1)
            try:
                self._image.set_from_pixbuf(_crop_fill(pixbuf, self._size))
            except GLib.Error:
                self._fallback_to_diamond()
        elif icon_name:
            self._image.set_from_icon_name(icon_name, 1)
            self._image.set_pixel_size(int(16 * self._s))

    def _fallback_to_diamond(self):
        self._image.clear()
        self._image.hide()
        if self._diamond not in self.get_children():
            self.add(self._diamond)
            self._diamond.show()

    def _resolve_notif_image(self, n) -> tuple[GdkPixbuf.Pixbuf | None, str]:
        try:
            img = getattr(n, "image_pixbuf", None)
            if img is not None:
                return img, ""
        except GLib.Error:
            pass

        from ..services.notifs import notifs

        # Check raw app_icon/app_name to see if it's an icon name
        # before we resolve to a file path
        names = []
        app_icon = notifs._field(n, "app_icon", "appIcon")
        if (
            app_icon
            and not app_icon.startswith("/")
            and not app_icon.startswith("file://")
        ):
            names.append(app_icon)

        desktop_entry = notifs._field(
            n, "desktopEntry", "desktopEntry"
        ) or notifs._desktop_entry(n)
        if desktop_entry:
            names.append(desktop_entry)

        app_name = (notifs._field(n, "app_name", "app") or "").lower()
        if app_name:
            names.append(app_name)

        from gi.repository import Gtk

        theme = Gtk.IconTheme.get_default()
        for name in names:
            if theme.has_icon(name):
                return None, name

        # Fall back to notifs.icon_for which resolves file paths
        path = notifs.icon_for(n)
        if not path:
            return None, ""

        if not path.startswith("/"):
            return None, path

        try:
            return GdkPixbuf.Pixbuf.new_from_file(path), ""
        except GLib.Error:
            return None, ""
