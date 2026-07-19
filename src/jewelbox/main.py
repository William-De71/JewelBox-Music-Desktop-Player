from gettext import gettext as _
from pathlib import Path
import sys

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from jewelbox import APP_ID, RESOURCE_PREFIX  # noqa: E402
from jewelbox.window import JewelboxWindow  # noqa: E402


class JewelboxApplication(Adw.Application):
    def __init__(self, version: str):
        super().__init__(application_id=APP_ID)
        self.version = version
        GLib.set_application_name('JewelBox')

        self._add_action('quit', lambda *_a: self.quit(), ['<primary>q'])
        self._add_action('about', self._on_about)

    def do_startup(self):
        Adw.Application.do_startup(self)
        self._load_css()

    def do_activate(self):
        window = self.props.active_window
        if window is None:
            window = JewelboxWindow(application=self)
        window.present()

    def _add_action(self, name, callback, accels=None):
        action = Gio.SimpleAction.new(name, None)
        action.connect('activate', callback)
        self.add_action(action)
        if accels:
            self.set_accels_for_action(f'app.{name}', accels)

    def _load_css(self):
        provider = Gtk.CssProvider()
        try:
            provider.load_from_resource(f'{RESOURCE_PREFIX}/style.css')
        except GLib.Error:
            # Mode développement : le gresource n'est pas compilé,
            # on charge le fichier source directement.
            css_file = Path(__file__).resolve().parent.parent / 'style.css'
            if not css_file.exists():
                return
            provider.load_from_path(str(css_file))
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

    def _on_about(self, *_args):
        dialog = Adw.AboutDialog(
            application_name='JewelBox',
            application_icon=APP_ID,
            developer_name='William Deren',
            version=self.version,
            license_type=Gtk.License.MIT_X11,
            website='https://github.com/William-De71/JewelBox-Music-Desktop-Player',
            comments=_('Client de streaming pour le serveur JewelBox Music Library'),
        )
        dialog.present(self.props.active_window)


def main(version: str = 'dev') -> int:
    app = JewelboxApplication(version)
    return app.run(sys.argv)
