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
        'background-playback': True, 'notify-track-change': True,
        'smart-ranking-size': 100,
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

    def get_int(self, key):
        return int(self._values.get(key, 0))

    def set_int(self, key, value):
        self._values[key] = int(value)


def _load_settings():
    source = Gio.SettingsSchemaSource.get_default()
    if source is not None and source.lookup(APP_ID, True) is not None:
        return Gio.Settings.new(APP_ID)
    print('jewelbox: schéma GSettings absent, réglages non persistés '
          '(mode développement)', file=sys.stderr)
    return _MemorySettings()


# (nom d'action app.*, raccourcis) — repris tels quels par la fenêtre
# d'aide (ui/shortcuts.py) pour qu'elle ne puisse pas diverger.
PLAYBACK_SHORTCUTS = (
    ('play-pause', ['<primary>space']),
    ('next', ['<primary>n']),
    ('previous', ['<primary>b']),
    ('toggle-shuffle', ['<primary>s']),
    ('cycle-repeat', ['<primary>r']),
    ('toggle-favorite', ['<primary>d']),
)


class JewelboxApplication(Adw.Application):
    def __init__(self, version: str):
        super().__init__(application_id=APP_ID)
        self.version = version
        self.settings = None
        self.device_id = ''
        self._client = None
        self.playback = None
        self._mpris = None
        self._notifier = None
        # Fenêtre masquée (lecture en arrière-plan) : minuterie de sortie
        # quand plus rien ne joue, et demande au portail faite une seule fois.
        self._idle_quit_source = 0
        self._background_requested = False
        GLib.set_application_name('JewelBox Music Player')

        self._add_action('quit', lambda *_a: self.quit(), ['<primary>q'])
        self._add_action('preferences', self._on_preferences,
                         ['<primary>comma'])
        self._add_action('about', self._on_about)
        self._add_action('shortcuts', self._on_shortcuts,
                         ['<primary>question'])

        # Commandes de lecture au clavier : mêmes combinaisons que GNOME
        # Musique (Ctrl+Espace, Ctrl+N / Ctrl+B, Ctrl+S, Ctrl+R) — pas de
        # Ctrl+←/→, qui déplacent le curseur par mot dans le champ de
        # recherche. Ctrl+D (comme « marque-page ») bascule le favori.
        for name, accels in PLAYBACK_SHORTCUTS:
            self._add_action(name, self._on_playback_action, accels)

    def do_startup(self):
        Adw.Application.do_startup(self)
        self._load_css()
        self._load_dev_icons()
        # GStreamer/playbin3 : construit une seule fois, indépendant de la
        # fenêtre (la lecture pourrait un jour continuer fenêtre fermée).
        from jewelbox.core.playback_state import PlaybackStateStore
        from jewelbox.playback.session import PlaybackSession
        self.playback = PlaybackSession(
            self.get_client,
            state_store=PlaybackStateStore(GLib.get_user_data_dir()))
        self.settings = _load_settings()
        # Identité de cet appareil (en-tête X-Device-Id) : générée une seule
        # fois, jamais régénérée — le serveur y attache la file de lecture.
        self.device_id = ensure_device_id(self.settings.get_string('device-id'))
        self.settings.set_string('device-id', self.device_id)
        # Reprise là où l'écoute s'était arrêtée : la file revient chargée et
        # en pause. Après les réglages (restore() a besoin du serveur courant
        # via get_client) et avant la fenêtre, pour que le mini-lecteur soit
        # déjà correct à l'affichage.
        self.playback.restore()
        # Lecteur du menu système GNOME, touches multimédia, GSConnect… :
        # publié après restore() pour annoncer d'emblée la piste reprise.
        from jewelbox.playback.mpris import MprisService
        self._mpris = MprisService(self, self.playback)
        self._mpris.start()
        from jewelbox.playback.notifier import TrackNotifier
        self._notifier = TrackNotifier(self, self.playback)
        self._notifier.start()
        self.playback.add_listener(self._on_playback_state)

    def do_activate(self):
        # Une fenêtre masquée (lecture en arrière-plan) reste la fenêtre
        # active de l'application : present() la réaffiche.
        window = self.props.active_window
        if window is None:
            window = JewelboxWindow(application=self)
        window.present()
        self._cancel_idle_quit()

    def do_shutdown(self):
        # Dernier instantané avant de quitter : les sauvegardes en cours de
        # route se font aux changements de piste et aux pauses, celle-ci
        # capte la position exacte d'une lecture interrompue par la fermeture.
        if self.playback is not None:
            self.playback.save_now()
        if self._mpris is not None:
            self._mpris.stop()
        if self._notifier is not None:
            self._notifier.stop()
        self._cancel_idle_quit()
        Adw.Application.do_shutdown(self)

    # ── Lecture en arrière-plan ──────────────────────────────────────────────

    def keeps_playing_in_background(self) -> bool:
        """Fermer la fenêtre pendant une lecture la masque au lieu de quitter
        (réglage background-playback) : la musique continue, pilotable depuis
        le menu système GNOME (MPRIS) ; rouvrir l'app ramène la fenêtre."""
        if self.settings is None or self.playback is None:
            return False
        if not self.settings.get_boolean('background-playback'):
            return False
        return self.playback.is_playing

    def window_hidden(self):
        """Appelé par la fenêtre quand elle se masque au lieu de se fermer."""
        self._request_background()
        self._update_idle_quit()

    def _on_playback_state(self, _state):
        self._update_idle_quit()

    def _update_idle_quit(self):
        """Fenêtre masquée et plus rien ne joue (pause depuis le menu
        système, fin de file) : on quitte au bout de _IDLE_QUIT_SECONDS plutôt
        que de rester indéfiniment en arrière-plan. La reprise de la lecture
        ou le retour de la fenêtre annulent l'échéance."""
        window = self.props.active_window
        hidden = window is not None and not window.get_visible()
        playing = self.playback is not None and self.playback.is_playing
        if hidden and not playing:
            if not self._idle_quit_source:
                self._idle_quit_source = GLib.timeout_add_seconds(
                    self._IDLE_QUIT_SECONDS, self._on_idle_quit)
        else:
            self._cancel_idle_quit()

    def _cancel_idle_quit(self):
        if self._idle_quit_source:
            GLib.source_remove(self._idle_quit_source)
            self._idle_quit_source = 0

    def _on_idle_quit(self):
        self._idle_quit_source = 0
        window = self.props.active_window
        if window is not None and not window.get_visible():
            self.quit()
        return GLib.SOURCE_REMOVE

    _IDLE_QUIT_SECONDS = 10 * 60

    def _request_background(self):
        """Flatpak : déclare au portail Background que l'app tourne sans
        fenêtre (GNOME l'affiche alors comme « application en arrière-plan »
        au lieu de la traiter comme figée). Best-effort, une fois par session ;
        hors Flatpak, rien à demander."""
        if self._background_requested or not Path('/.flatpak-info').exists():
            return
        self._background_requested = True
        connection = self.get_dbus_connection()
        if connection is None:
            return
        options = {
            'reason': GLib.Variant(
                's', _('Continuer la lecture fenêtre fermée')),
            'autostart': GLib.Variant('b', False),
        }
        connection.call(
            'org.freedesktop.portal.Desktop', '/org/freedesktop/portal/desktop',
            'org.freedesktop.portal.Background', 'RequestBackground',
            GLib.Variant('(sa{sv})', ('', options)), None,
            Gio.DBusCallFlags.NONE, -1, None, self._on_background_reply)

    @staticmethod
    def _on_background_reply(connection, result):
        try:
            connection.call_finish(result)
        except GLib.Error as error:
            print(f'jewelbox: portail Background indisponible ({error.message})',
                  file=sys.stderr)

    # ── Raccourcis ───────────────────────────────────────────────────────────

    def _on_playback_action(self, action, _param):
        playback = self.playback
        # File vide : rien à lire, mettre en pause ni passer.
        if playback is None or not playback.has_item:
            return
        {
            'play-pause': playback.toggle_play_pause,
            'next': playback.next,
            'previous': playback.previous,
            'toggle-shuffle': playback.toggle_shuffle,
            'cycle-repeat': playback.cycle_repeat,
            'toggle-favorite': playback.toggle_favorite,
        }[action.get_name()]()

    def _on_shortcuts(self, *_args):
        from jewelbox.ui.shortcuts import present_shortcuts
        present_shortcuts(self.props.active_window)

    def _add_action(self, name, callback, accels=None):
        action = Gio.SimpleAction.new(name, None)
        action.connect('activate', callback)
        self.add_action(action)
        if accels:
            self.set_accels_for_action(f'app.{name}', accels)

    def _load_dev_icons(self):
        """Rend les icônes maison visibles en mode développement.

        Les symboliques du projet (jewelbox-favorite-symbolic, etc.) sont
        installées par Meson dans le thème hicolor du préfixe — donc présentes
        dans le Flatpak, mais introuvables sous `make run`, qui lance les
        sources sans passer par l'installation. GTK affichait alors l'icône
        « image manquante » (une enveloppe) à la place du cœur des favoris.
        On ajoute donc data/icons au chemin de recherche quand ce dossier est
        là ; installé, le thème système fournit déjà les mêmes fichiers et ce
        chemin n'existe pas."""
        icons_dir = Path(__file__).resolve().parents[2] / 'data' / 'icons'
        if not icons_dir.is_dir():
            return
        display = Gdk.Display.get_default()
        if display is None:
            return
        Gtk.IconTheme.get_for_display(display).add_search_path(str(icons_dir))

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

    def smart_ranking_size(self) -> int | None:
        """Taille choisie des classements intelligents (Préférences)."""
        if self.settings is None:
            return None
        return self.settings.get_int('smart-ranking-size') or None

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
            application_name='JewelBox Music Player',
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
