from fabric.utils import GLib, Gtk
from fabric.widgets.widget import Widget
from gi.repository import PangoCairo


class ScrollingLabel(Gtk.DrawingArea, Widget):
    def __init__(
        self,
        label="",
        text="",
        speed=0.8,
        pause_ms=2000,
        max_width=200,
        name="scrolling-label",
        style_classes=None,
        style="",
        visible=True,
        h_align="start",
        ellipsize="none",
        hexpand=False,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.set_name(name)

        self.text = label or text
        self.speed = speed
        self.max_width_limit = max_width
        self.pause_ms = pause_ms

        if style:
            self.set_style(style)

        if visible:
            self.show()
        else:
            self.hide()

        if style_classes:
            style_context = self.get_style_context()
            if isinstance(style_classes, str):
                style_context.add_class(style_classes)
            else:
                for sc in style_classes:
                    style_context.add_class(sc)

        if h_align == "start":
            self.set_halign(Gtk.Align.START)
        elif h_align == "center":
            self.set_halign(Gtk.Align.CENTER)
        elif h_align == "end":
            self.set_halign(Gtk.Align.END)
        else:
            self.set_halign(Gtk.Align.FILL)
        self.set_hexpand(True)

        self._anim_value = 0.0
        self._anim_direction = 1
        self._anim_source_id = None
        self._pause_source_id = None

        self.connect("destroy", self._on_destroy)

    def set_label(self, new_text):
        if self.text == str(new_text):
            return
        self.text = str(new_text)
        self._stop_animation()
        self._anim_value = 0.0
        self._anim_direction = 1
        self.queue_resize()
        self.queue_draw()

    def _stop_animation(self):
        if self._anim_source_id is not None:
            GLib.source_remove(self._anim_source_id)
            self._anim_source_id = None
        if self._pause_source_id is not None:
            GLib.source_remove(self._pause_source_id)
            self._pause_source_id = None

    def _start_animation(self):
        if self._anim_source_id is None and self._pause_source_id is None:
            self._anim_source_id = GLib.timeout_add(16, self._on_tick)

    def _on_tick(self):
        self._anim_value += self.speed * 0.0025
        if self._anim_value > 1.0:
            self._anim_value = 0.0
        self.queue_draw()
        return GLib.SOURCE_CONTINUE

    def _resume_after_pause(self):
        return GLib.SOURCE_REMOVE

    def do_get_preferred_width(self):
        layout = self.create_pango_layout(self.text)
        style_context = self.get_style_context()
        layout.set_font_description(style_context.get_font(Gtk.StateFlags.NORMAL))
        text_w, _ = layout.get_pixel_size()
        natural = min(text_w, self.max_width_limit)
        return natural, natural

    def do_get_preferred_height(self):
        layout = self.create_pango_layout(self.text)
        style_context = self.get_style_context()
        layout.set_font_description(style_context.get_font(Gtk.StateFlags.NORMAL))
        _, text_h = layout.get_pixel_size()
        return text_h, text_h

    def do_draw(self, cr):
        width = self.get_allocated_width()
        height = self.get_allocated_height()

        style_context = self.get_style_context()
        rgba = style_context.get_color(Gtk.StateFlags.NORMAL)
        font_desc = style_context.get_font(Gtk.StateFlags.NORMAL)

        layout = self.create_pango_layout(self.text)
        layout.set_font_description(font_desc)
        text_w, text_h = layout.get_pixel_size()

        cr.rectangle(0, 0, width, height)
        cr.clip()

        y_pos = (height - text_h) / 2

        cr.set_source_rgba(rgba.red, rgba.green, rgba.blue, rgba.alpha)

        if text_w > width:
            add_text = "   " + self.text
            add_layout = self.create_pango_layout(add_text)
            add_layout.set_font_description(font_desc)
            add_w, _ = add_layout.get_pixel_size()

            loop_width = add_w
            offset = (self._anim_value * loop_width) % loop_width

            cr.move_to(-offset, y_pos)
            PangoCairo.show_layout(cr, add_layout)
            cr.move_to(loop_width - offset, y_pos)
            PangoCairo.show_layout(cr, add_layout)

            if not self._anim_source_id and self._pause_source_id is None:
                self._start_animation()
        else:
            self._stop_animation()
            self._anim_value = 0.0
            cr.move_to(0, y_pos)
            PangoCairo.show_layout(cr, layout)

        return False

    def _on_destroy(self, _widget):
        self._stop_animation()
