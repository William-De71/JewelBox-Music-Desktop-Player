import asyncio
from gettext import gettext as _
from pathlib import Path
import sys

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

import gi.events  # noqa: E402

from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from jewelbox import APP_ID, RESOURCE_PREFIX  # noqa: E402
from jewelbox.api.client import JewelBoxClient  # noqa: E402
from jewelbox.core.device import ensure_device_id  # noqa: E402
from jewelbox.window import JewelboxWindow  # noqa: E402


class _MemorySettings:
    """Repli mode développement (schéma GSettings non installé) : mêmes
    accesseurs que Gio.Settings, valeurs en mémoire, rien n'est persisté."""

    _DEFAULTS = {
        'server-url': '', 'server-id': '', 'device-id': '',
        'sort-order': 'artist',
    }

    def __init__(self):
        self._values = dict(self._DEFAULTS)

    def get_string(self, key):
        return self._values[key]

    def set_string(self, key, value):
        self._values[key] = value

    def get_boolean(self, key):
        return bool(self._values.get(key, False))

    def set_boolean(self, key, value):
        self._values[key] = bool(value)


def _load_settings():
    source = Gio.SettingsSchemaSource.get_default()
    if source is not None and source.lookup(APP_ID, True) is not None:
        return Gio.Settings.new(APP_ID)
    print('jewelbox: schéma GSettings absent, réglages non persistés '
          '(mode développement)', file=sys.stderr)
    return _MemorySettings()


class JewelboxApplication(Adw.Application):
    def __init__(self, version: str):
        super().__init__(application_id=APP_ID)
        self.version = version
        self.settings = None
        self.device_id = ''
        self._client = None
        GLib.set_application_name('JewelBox')

        self._add_action('quit', lambda *_a: self.quit(), ['<primary>q'])
        self._add_action('preferences', self._on_preferences,
                         ['<primary>comma'])
        self._add_action('about', self._on_about)

    def do_startup(self):
        Adw.Application.do_startup(self)
        self._load_css()
        self.settings = _load_settings()
        # Identité de cet appareil (en-tête X-Device-Id) : générée une seule
        # fois, jamais régénérée — le serveur y attache la file de lecture.
        self.device_id = ensure_device_id(self.settings.get_string('device-id'))
        self.settings.set_string('device-id', self.device_id)

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
        resource_path = f'{RESOURCE_PREFIX}/style.css'
        try:
            # load_from_resource ne lève pas GLib.Error si la ressource
            # manque (simple warning) : on vérifie l'existence d'abord.
            Gio.resources_get_info(resource_path, Gio.ResourceLookupFlags.NONE)
        except GLib.Error:
            # Mode développement : le gresource n'est pas compilé,
            # on charge le fichier source directement.
            css_file = Path(__file__).resolve().parent.parent / 'style.css'
            if not css_file.exists():
                return
            provider.load_from_path(str(css_file))
        else:
            provider.load_from_resource(resource_path)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

    def get_client(self):
        """Client API partagé, (re)construit paresseusement depuis les réglages.

        Renvoie None tant qu'aucun serveur valide n'est configuré. Tiré à
        chaque accès plutôt que câblé sur un signal GSettings : les pages
        voient ainsi tout changement d'adresse, y compris avec le repli
        mémoire du mode développement.
        """
        url = self.settings.get_string('server-url') if self.settings else ''
        if not url:
            self._client = None
        elif self._client is None or self._client.base_url != url:
            try:
                self._client = JewelBoxClient(url, device_id=self.device_id)
            except ValueError:
                self._client = None
        return self._client

    def _on_preferences(self, *_args):
        from jewelbox.ui.preferences import PreferencesDialog

        window = self.props.active_window
        dialog = PreferencesDialog(self.settings, self.device_id)
        if window is not None and hasattr(window, '_refresh_server_hint'):
            dialog.connect('closed',
                           lambda *_a: window._refresh_server_hint())
        dialog.present(window)

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
    # La boucle GLib devient la boucle asyncio (PyGObject >= 3.50) : les
    # appels async de libsoup s'attendent avec await, sans thread.
    asyncio.set_event_loop_policy(gi.events.GLibEventLoopPolicy())
    app = JewelboxApplication(version)
    return app.run(sys.argv)
