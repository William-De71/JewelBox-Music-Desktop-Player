"""Intégration MPRIS2 : expose la lecture sur le bus de session D-Bus.

C'est ce qui fait apparaître JewelBox dans le lecteur multimédia du menu
système de GNOME (titre, artiste, pochette, boutons précédent / lecture /
suivant), comme Spotify ou un navigateur. Les mêmes informations servent
aux touches multimédia du clavier, à KDE Connect / GSConnect, etc.

Code frontière (exclu de la couverture, voir pyproject.toml) : ne fait que
traduire PlaybackUiState en propriétés MPRIS et les appels MPRIS en
commandes de PlaybackSession.

Spécification : https://specifications.freedesktop.org/mpris-spec/latest/
Le nom de bus est réservé dans le manifeste Flatpak (--own-name).
"""

import asyncio
import hashlib
import sys
from pathlib import Path

from gi.repository import Gio, GLib

from jewelbox import APP_ID
from jewelbox.api.client import ApiError

BUS_NAME = 'org.mpris.MediaPlayer2.jewelbox'
OBJECT_PATH = '/org/mpris/MediaPlayer2'
ROOT_IFACE = 'org.mpris.MediaPlayer2'
PLAYER_IFACE = 'org.mpris.MediaPlayer2.Player'
PROPS_IFACE = 'org.freedesktop.DBus.Properties'

# Une piste absente : chemin réservé par la spécification.
_NO_TRACK = '/org/mpris/MediaPlayer2/TrackList/NoTrack'

# Les interfaces Properties sont gérées à la main (Get / GetAll / Set dans
# _on_method_call), comme le faisait GNOME Musique : plus robuste avec
# PyGObject que les rappels get_property/set_property de register_object.
_INTROSPECTION = '''
<node>
  <interface name="org.freedesktop.DBus.Properties">
    <method name="Get">
      <arg name="interface_name" direction="in" type="s"/>
      <arg name="property_name" direction="in" type="s"/>
      <arg name="value" direction="out" type="v"/>
    </method>
    <method name="GetAll">
      <arg name="interface_name" direction="in" type="s"/>
      <arg name="properties" direction="out" type="a{sv}"/>
    </method>
    <method name="Set">
      <arg name="interface_name" direction="in" type="s"/>
      <arg name="property_name" direction="in" type="s"/>
      <arg name="value" direction="in" type="v"/>
    </method>
    <signal name="PropertiesChanged">
      <arg name="interface_name" type="s"/>
      <arg name="changed_properties" type="a{sv}"/>
      <arg name="invalidated_properties" type="as"/>
    </signal>
  </interface>
  <interface name="org.mpris.MediaPlayer2">
    <method name="Raise"/>
    <method name="Quit"/>
    <property name="CanQuit" type="b" access="read"/>
    <property name="CanRaise" type="b" access="read"/>
    <property name="HasTrackList" type="b" access="read"/>
    <property name="Identity" type="s" access="read"/>
    <property name="DesktopEntry" type="s" access="read"/>
    <property name="SupportedUriSchemes" type="as" access="read"/>
    <property name="SupportedMimeTypes" type="as" access="read"/>
  </interface>
  <interface name="org.mpris.MediaPlayer2.Player">
    <method name="Next"/>
    <method name="Previous"/>
    <method name="Pause"/>
    <method name="PlayPause"/>
    <method name="Stop"/>
    <method name="Play"/>
    <method name="Seek">
      <arg name="Offset" direction="in" type="x"/>
    </method>
    <method name="SetPosition">
      <arg name="TrackId" direction="in" type="o"/>
      <arg name="Position" direction="in" type="x"/>
    </method>
    <method name="OpenUri">
      <arg name="Uri" direction="in" type="s"/>
    </method>
    <signal name="Seeked">
      <arg name="Position" type="x"/>
    </signal>
    <property name="PlaybackStatus" type="s" access="read"/>
    <property name="LoopStatus" type="s" access="read"/>
    <property name="Rate" type="d" access="read"/>
    <property name="Shuffle" type="b" access="read"/>
    <property name="Metadata" type="a{sv}" access="read"/>
    <property name="Volume" type="d" access="readwrite"/>
    <property name="Position" type="x" access="read"/>
    <property name="MinimumRate" type="d" access="read"/>
    <property name="MaximumRate" type="d" access="read"/>
    <property name="CanGoNext" type="b" access="read"/>
    <property name="CanGoPrevious" type="b" access="read"/>
    <property name="CanPlay" type="b" access="read"/>
    <property name="CanPause" type="b" access="read"/>
    <property name="CanSeek" type="b" access="read"/>
    <property name="CanControl" type="b" access="read"/>
  </interface>
</node>
'''

# Correspondance du mode répétition interne vers LoopStatus MPRIS.
_LOOP_STATUS = {'off': 'None', 'all': 'Playlist', 'one': 'Track'}


def _microseconds(seconds: float) -> int:
    return int(max(0.0, seconds or 0.0) * 1_000_000)


class MprisService:
    """Publie la session de lecture sur D-Bus. Une par application.

    app sert à Raise / Quit et à récupérer le client API (pochettes) ;
    playback est la PlaybackSession, suivie comme n'importe quelle page via
    add_listener."""

    def __init__(self, app, playback):
        self._app = app
        self._playback = playback
        self._connection = None
        self._registrations = []
        self._owner_id = 0
        self._state = None
        # Dernières valeurs publiées, pour n'émettre PropertiesChanged que sur
        # un vrai changement : la session republie l'état à chaque tick de
        # position (plusieurs fois par seconde).
        self._published = {}
        # Pochette : URL distante de la piste courante → fichier local en
        # cache (GNOME Shell charge mpris:artUrl comme un fichier).
        self._art_source = None
        self._art_uri = ''

    # ── Cycle de vie ─────────────────────────────────────────────────────────

    def start(self):
        connection = self._app.get_dbus_connection()
        if connection is None:
            return
        self._connection = connection
        node = Gio.DBusNodeInfo.new_for_xml(_INTROSPECTION)
        try:
            for interface in node.interfaces:
                self._registrations.append(connection.register_object(
                    OBJECT_PATH, interface, self._on_method_call, None, None))
        except GLib.Error as error:
            print(f'jewelbox: MPRIS indisponible ({error.message})',
                  file=sys.stderr)
            self.stop()
            return
        self._owner_id = Gio.bus_own_name_on_connection(
            connection, BUS_NAME, Gio.BusNameOwnerFlags.NONE, None, None)
        self._playback.add_listener(self._on_playback_state)

    def stop(self):
        self._playback.remove_listener(self._on_playback_state)
        if self._owner_id:
            Gio.bus_unown_name(self._owner_id)
            self._owner_id = 0
        for registration in self._registrations:
            self._connection.unregister_object(registration)
        self._registrations = []

    # ── Propriétés ───────────────────────────────────────────────────────────

    def _root_properties(self):
        return {
            'CanQuit': GLib.Variant('b', True),
            'CanRaise': GLib.Variant('b', True),
            'HasTrackList': GLib.Variant('b', False),
            'Identity': GLib.Variant('s', 'JewelBox Music Player'),
            # Nom du .desktop sans extension : GNOME en tire l'icône et le
            # nom de l'application affichés au-dessus du lecteur.
            'DesktopEntry': GLib.Variant('s', APP_ID),
            'SupportedUriSchemes': GLib.Variant('as', []),
            'SupportedMimeTypes': GLib.Variant('as', []),
        }

    def _player_properties(self):
        state = self._state
        has_item = bool(state and state.has_item)
        if not has_item:
            status = 'Stopped'
        elif state.is_playing:
            status = 'Playing'
        else:
            status = 'Paused'
        return {
            'PlaybackStatus': GLib.Variant('s', status),
            'LoopStatus': GLib.Variant(
                's', _LOOP_STATUS.get(state.repeat if state else 'off', 'None')),
            'Rate': GLib.Variant('d', 1.0),
            'Shuffle': GLib.Variant('b', bool(state and state.shuffle)),
            'Metadata': GLib.Variant('a{sv}', self._metadata()),
            'Volume': GLib.Variant('d', state.volume if state else 1.0),
            'Position': GLib.Variant(
                'x', _microseconds(state.position_seconds) if has_item else 0),
            'MinimumRate': GLib.Variant('d', 1.0),
            'MaximumRate': GLib.Variant('d', 1.0),
            'CanGoNext': GLib.Variant('b', bool(state and state.has_next)),
            'CanGoPrevious': GLib.Variant('b', has_item),
            'CanPlay': GLib.Variant('b', has_item),
            'CanPause': GLib.Variant('b', has_item),
            'CanSeek': GLib.Variant('b', has_item),
            'CanControl': GLib.Variant('b', True),
        }

    def _track_path(self):
        state = self._state
        if state is None or state.current_track_id is None:
            return _NO_TRACK
        return f'/io/github/william_de71/JewelBox/track/{state.current_track_id}'

    def _metadata(self):
        state = self._state
        if state is None or not state.has_item:
            return {'mpris:trackid': GLib.Variant('o', _NO_TRACK)}
        metadata = {
            'mpris:trackid': GLib.Variant('o', self._track_path()),
            'xesam:title': GLib.Variant('s', state.title or ''),
        }
        if state.artist:
            metadata['xesam:artist'] = GLib.Variant('as', [state.artist])
            metadata['xesam:albumArtist'] = GLib.Variant('as', [state.artist])
        if state.album:
            metadata['xesam:album'] = GLib.Variant('s', state.album)
        if state.duration_seconds > 0:
            metadata['mpris:length'] = GLib.Variant(
                'x', _microseconds(state.duration_seconds))
        if self._art_uri:
            metadata['mpris:artUrl'] = GLib.Variant('s', self._art_uri)
        return metadata

    # ── Suivi de l'état de lecture ───────────────────────────────────────────

    def _on_playback_state(self, state):
        self._state = state
        self._update_art(state.cover_url if state.has_item else None)
        self._emit_changes()

    def _emit_changes(self):
        if self._connection is None:
            return
        properties = self._player_properties()
        # Position n'est jamais annoncée par PropertiesChanged (spécification) :
        # les clients la lisent à la demande ; un saut passe par Seeked.
        properties.pop('Position')
        changed = {name: value for name, value in properties.items()
                   if self._published.get(name) != value}
        if not changed:
            return
        self._published.update(changed)
        self._connection.emit_signal(
            None, OBJECT_PATH, PROPS_IFACE, 'PropertiesChanged',
            GLib.Variant('(sa{sv}as)', (PLAYER_IFACE, changed, [])))

    # ── Pochette ─────────────────────────────────────────────────────────────

    def _update_art(self, cover_url):
        if cover_url == self._art_source:
            return
        self._art_source = cover_url
        self._art_uri = ''
        if not cover_url:
            return
        path = self._art_cache_path(cover_url)
        if path.exists():
            self._art_uri = path.as_uri()
            return
        task = self._download_art(cover_url, path)
        asyncio.get_event_loop_policy().get_event_loop().create_task(task)

    @staticmethod
    def _art_cache_path(cover_url) -> Path:
        digest = hashlib.sha1(cover_url.encode('utf-8')).hexdigest()
        return Path(GLib.get_user_cache_dir()) / 'jewelbox' / 'covers' / digest

    async def _download_art(self, cover_url, path):
        client = self._app.get_client()
        if client is None:
            return
        try:
            data = await client.fetch_bytes(cover_url)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        except (ApiError, OSError):
            return
        # La piste a pu changer pendant le téléchargement.
        if cover_url != self._art_source:
            return
        self._art_uri = path.as_uri()
        self._emit_changes()

    # ── Appels D-Bus ─────────────────────────────────────────────────────────

    def _on_method_call(self, _connection, _sender, _path, interface, method,
                        parameters, invocation):
        if interface == PROPS_IFACE:
            self._on_properties_call(method, parameters, invocation)
            return
        args = parameters.unpack()
        handler = {
            'Raise': self._raise,
            'Quit': self._app.quit,
            'Next': self._playback.next,
            'Previous': self._playback.previous,
            'Play': self._play,
            'Pause': self._pause,
            'PlayPause': self._play_pause,
            'Stop': self._pause,
            'Seek': self._seek_by,
            'SetPosition': self._set_position,
            'OpenUri': lambda _uri: None,
        }.get(method)
        if handler is None:
            invocation.return_dbus_error(
                'org.freedesktop.DBus.Error.UnknownMethod', method)
            return
        handler(*args)
        invocation.return_value(None)

    def _on_properties_call(self, method, parameters, invocation):
        interface_name = parameters.unpack()[0]
        properties = self._properties_of(interface_name)
        if properties is None:
            invocation.return_dbus_error(
                'org.freedesktop.DBus.Error.UnknownInterface', interface_name)
            return
        if method == 'GetAll':
            invocation.return_value(GLib.Variant('(a{sv})', (properties,)))
        elif method == 'Get':
            name = parameters.unpack()[1]
            if name not in properties:
                invocation.return_dbus_error(
                    'org.freedesktop.DBus.Error.UnknownProperty', name)
                return
            invocation.return_value(GLib.Variant('(v)', (properties[name],)))
        elif method == 'Set':
            _iface, name, value = parameters.unpack()
            if interface_name == PLAYER_IFACE and name == 'Volume':
                self._playback.set_volume(max(0.0, min(1.0, float(value))))
                invocation.return_value(None)
                return
            invocation.return_dbus_error(
                'org.freedesktop.DBus.Error.PropertyReadOnly', name)
        else:
            invocation.return_dbus_error(
                'org.freedesktop.DBus.Error.UnknownMethod', method)

    def _properties_of(self, interface_name):
        if interface_name == ROOT_IFACE:
            return self._root_properties()
        if interface_name == PLAYER_IFACE:
            return self._player_properties()
        return None

    # ── Commandes ────────────────────────────────────────────────────────────

    def _raise(self):
        self._app.activate()

    def _play(self):
        if self._state and self._state.has_item and not self._state.is_playing:
            self._playback.toggle_play_pause()

    def _pause(self):
        if self._state and self._state.is_playing:
            self._playback.toggle_play_pause()

    def _play_pause(self):
        if self._state and self._state.has_item:
            self._playback.toggle_play_pause()

    def _seek_by(self, offset_us):
        if self._state is None or not self._state.has_item:
            return
        target = self._state.position_seconds + offset_us / 1_000_000
        duration = self._state.duration_seconds
        if duration > 0 and target >= duration:
            # Au-delà de la fin : la spécification demande de passer à la
            # piste suivante.
            self._playback.next()
            return
        self._seek_to(max(0.0, target))

    def _set_position(self, track_id, position_us):
        if track_id != self._track_path() or position_us < 0:
            return
        duration = self._state.duration_seconds if self._state else 0
        if duration > 0 and position_us > _microseconds(duration):
            return
        self._seek_to(position_us / 1_000_000)

    def _seek_to(self, seconds):
        self._playback.seek(seconds)
        if self._connection is not None:
            self._connection.emit_signal(
                None, OBJECT_PATH, PLAYER_IFACE, 'Seeked',
                GLib.Variant('(x)', (_microseconds(seconds),)))
