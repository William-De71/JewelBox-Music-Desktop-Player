"""Contrôle à distance : ce lecteur piloté par un autre client (Gladys…).

Code frontière (exclu de la couverture) : les décisions — quand rapporter
l'état, comment lire le flux — vivent dans core.remote, pur et testé ; ici
on écoute la session, on garde le flux d'ordres ouvert (libsoup) et on
exécute les ordres reçus sur la PlaybackSession, comme le ferait MPRIS.

Tout est best-effort : un serveur < 1.18 (404 sur le flux) ou injoignable
n'empêche rien, on réessaie simplement de plus en plus rarement.
"""

import asyncio
import codecs
import time
from gettext import gettext as _

from gi.repository import Gio, GLib, Soup

from jewelbox.api.client import ApiError
from jewelbox.core import remote
from jewelbox.playback.queue_sync import device_label
from jewelbox.ui.smart_specs import smart_spec

# Le serveur écrit un commentaire toutes les 20 s : sans rien pendant 60 s,
# la connexion est morte (veille, Wi-Fi coupé) et on la rouvre.
_STREAM_TIMEOUT_SECONDS = 60


class RemoteControl:

    def __init__(self, app, playback):
        self._app = app
        self._playback = playback
        self._reporter = remote.LiveReporter()
        self._session = Soup.Session(timeout=_STREAM_TIMEOUT_SECONDS)
        self._cancellable = None
        self._task = None
        self._handlers = []

    def start(self):
        self._playback.add_listener(self._on_playback_state)
        settings = self._app.settings
        for key in ('remote-control', 'server-url'):
            self._handlers.append(
                settings.connect(f'changed::{key}', self._on_settings_changed))
        self._restart()

    def stop(self):
        self._playback.remove_listener(self._on_playback_state)
        for handler in self._handlers:
            self._app.settings.disconnect(handler)
        self._handlers = []
        self._stop_stream()

    # ── État en direct ───────────────────────────────────────────────────────

    def _on_playback_state(self, state):
        live = remote.live_state_of(state)
        if self._reporter.update(live, time.monotonic()):
            self._report(live)

    def _report(self, live):
        client = self._app.get_client()
        if client is None:
            return
        self._loop().create_task(self._send_report(client, live))

    @staticmethod
    async def _send_report(client, live):
        try:
            await client.report_live_state(live.payload(device_label()))
        except ApiError:
            pass

    # ── Flux d'ordres ────────────────────────────────────────────────────────

    def _on_settings_changed(self, *_args):
        self._restart()

    def _restart(self):
        self._stop_stream()
        if self._app.settings.get_boolean('remote-control'):
            self._cancellable = Gio.Cancellable()
            self._task = self._loop().create_task(self._run(self._cancellable))

    def _stop_stream(self):
        if self._cancellable is not None:
            self._cancellable.cancel()
            self._cancellable = None
        if self._task is not None:
            self._task.cancel()
            self._task = None

    async def _run(self, cancellable):
        attempt = 0
        while not cancellable.is_cancelled():
            client = self._app.get_client()
            if client is not None:
                if await self._listen(client, cancellable):
                    attempt = 0
            attempt += 1
            await asyncio.sleep(remote.reconnect_delay(attempt))

    async def _listen(self, client, cancellable) -> bool:
        """Lit le flux jusqu'à sa fin. Vrai s'il a été ouvert (la prochaine
        reconnexion repart alors du délai le plus court)."""
        try:
            message = client.remote_events_message(device_label(),
                                                   remote.CAPABILITIES)
            stream = await self._session.send_async(
                message, GLib.PRIORITY_DEFAULT, cancellable)
        except (ApiError, GLib.Error):
            return False
        if message.get_status() != 200:
            return False
        parser = remote.SseParser()
        decoder = codecs.getincrementaldecoder('utf-8')('replace')
        # Le contrôleur vient de nous voir apparaître : il doit savoir tout
        # de suite où en est la lecture.
        self._reporter.forget()
        self._on_playback_state(self._playback._build_state())
        try:
            while True:
                chunk = await stream.read_bytes_async(
                    4096, GLib.PRIORITY_DEFAULT, cancellable)
                if chunk.get_size() == 0:
                    break
                for event, data in parser.feed(decoder.decode(chunk.get_data())):
                    if event == 'command' and isinstance(data, dict):
                        self._execute(client, data)
        except GLib.Error:
            pass
        finally:
            try:
                stream.close(None)
            except GLib.Error:
                pass
        return True

    # ── Exécution ────────────────────────────────────────────────────────────

    def _execute(self, client, command):
        playback = self._playback
        name = command.get('command')
        simple = {
            'play': playback.play,
            'pause': playback.pause,
            'toggle': playback.toggle_play_pause,
            'next': playback.next,
            'previous': playback.previous,
        }
        if name in simple:
            simple[name]()
        elif name == 'seek':
            playback.seek(float(command.get('position_sec', 0)))
        elif name == 'volume':
            playback.set_volume(float(command.get('volume', 100)) / 100)
        elif name == 'mute':
            playback.set_muted(bool(command.get('muted')))
        elif name == 'shuffle':
            playback.set_shuffle(bool(command.get('shuffle')))
        elif name == 'repeat' and command.get('repeat') in ('off', 'all', 'one'):
            playback.set_repeat(command['repeat'])
        elif name == 'play_index':
            self._play_index(int(command.get('index', -1)))
        elif name == 'play_media':
            self._loop().create_task(self._play_media(
                client, command.get('media') or {}, command.get('mode', 'replace')))

    def _play_index(self, index):
        state = self._playback._build_state()
        if index == state.queue_index:
            # jump_to bascule lecture/pause sur la piste en cours : un
            # contrôleur qui demande cette piste veut l'entendre.
            self._playback.play()
        else:
            self._playback.jump_to(index)

    async def _play_media(self, client, media, mode):
        playback = self._playback
        kind, ref = media.get('type'), media.get('id')
        enqueue = mode in ('next', 'append')
        play_next = mode == 'next'
        try:
            if kind == 'album':
                album = await client.album(ref)
                if enqueue:
                    playback.enqueue_album(album, play_next=play_next)
                else:
                    first = next((t for t in album.tracks if t.has_file), None)
                    if first is not None:
                        playback.play_album(album, first.id)
                return
            if kind == 'playlist':
                playlist = await client.playlist(ref)
                tracks, options = playlist.tracks, {
                    'report_playlist_id': playlist.id, 'source_name': playlist.name}
            elif kind == 'smart':
                smart = await client.smart_playlist(
                    ref, ranking_size=self._app.smart_ranking_size())
                spec = smart_spec(ref)
                tracks, options = smart.tracks, {
                    'report_smart_key': ref,
                    'source_name': spec.label if spec is not None else ref}
            elif kind == 'track':
                tracks, options = (await client.track(ref),), {}
            elif kind in ('album_radio', 'artist_radio'):
                radio = await client.radio(
                    album_id=ref if kind == 'album_radio' else None,
                    artist_id=ref if kind == 'artist_radio' else None)
                tracks, options = radio.tracks, {
                    'source_name': _('Radio · {name}').format(name=radio.seed_name)}
            else:
                return
        except ApiError:
            return
        if not tracks:
            return
        if enqueue:
            playback.enqueue_tracks(tracks, play_next=play_next)
        else:
            playback.play_queue_tracks(tracks, **options)

    @staticmethod
    def _loop():
        return asyncio.get_event_loop_policy().get_event_loop()
