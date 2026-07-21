"""Session de lecture : colle entre la file pure (core.queue.Queue), le
moteur GStreamer (playback.player.Player) et les appels réseau qui
accompagnent la lecture (compteur local, Last.fm, historique d'accueil).

Code frontière (exclu de la couverture) : les décisions elles-mêmes
(navigation de file, règles de scrobbling) vivent dans jewelbox.core, purs
et testés — cette classe ne fait qu'enchaîner les appels au bon moment.

Pas de mix dynamique ni de reprise persistée pour cette première itération
(clé GSettings dédiée, phases séparées de la feuille de route) : la session
commence toujours vide, elle vit le temps du lancement de l'app.
"""

import asyncio
import time
from dataclasses import dataclass
from gettext import gettext as _

from jewelbox.api.client import ApiError
from jewelbox.core.queue import Queue, QueueItem
from jewelbox.core.scrobble import ScrobbleTracker


@dataclass(frozen=True)
class PlaybackUiState:
    """Ce que l'UI a besoin d'afficher (mini-lecteur, surbrillance de piste).
    Recalculé et republié à chaque évènement pertinent."""

    has_item: bool = False
    is_playing: bool = False
    current_track_id: int | None = None
    title: str | None = None
    artist: str | None = None
    album: str | None = None
    cover_url: str | None = None
    is_favorite: bool = False
    has_next: bool = False
    has_previous: bool = False
    shuffle: bool = False
    repeat: str = 'off'
    position_seconds: float = 0.0
    duration_seconds: float = 0.0
    volume: float = 1.0
    error: str | None = None


def _item_from_track(track, album_title, artist_name, stream_url, cover_url):
    return QueueItem(
        track_id=track.id,
        title=track.title,
        artist_name=artist_name,
        album_title=album_title,
        cover_url=cover_url,
        is_favorite=track.is_favorite,
        stream_url=stream_url,
    )


class PlaybackSession:
    """Une par application. on_state_changed(state) est appelé à chaque
    évènement qui touche l'UI ; get_client est celui de JewelboxApplication
    (peut renvoyer None si le serveur a été retiré des Préférences)."""

    def __init__(self, get_client, loop=None):
        from jewelbox.playback.player import Player  # importé ici : GStreamer

        self._get_client = get_client
        self._loop = loop or asyncio.get_event_loop_policy().get_event_loop()
        self._queue = Queue()
        self._scrobbler = ScrobbleTracker()
        self._player = Player()
        self._player.on_position = self._on_position
        self._player.on_track_ended = self._on_track_ended
        self._player.on_about_to_finish = self._on_about_to_finish
        self._player.on_error = self._on_error
        self._player.on_state_changed = lambda _playing: self._publish()

        # Plusieurs pages peuvent vouloir suivre l'état en même temps (la
        # barre de lecture persistante ET la fiche album ouverte) : liste
        # d'abonnés plutôt qu'un callback unique. on_state_changed reste un
        # raccourci pratique pour un abonné unique (scripts, tests).
        self._listeners: list = []
        self.on_state_changed = None
        self._last_error: str | None = None
        # Posé par _on_about_to_finish quand playbin3 a déjà reçu l'URI
        # suivante (enchaînement gapless) : l'EOS qui suit ne doit alors que
        # faire avancer la file, sans recharger le player (déjà en train de
        # jouer la bonne piste) ni redemander now_playing en double.
        self._gapless_next_started = False

    # ── Démarrage d'une lecture ──────────────────────────────────────────────

    def play_album(self, album, start_track_id: int):
        """Met en file les pistes jouables de l'album et démarre à
        start_track_id (ou à la première jouable si l'id n'y est pas)."""
        client = self._get_client()
        if client is None:
            return
        playable = [t for t in album.tracks if t.has_file]
        if not playable:
            return
        cover_url = client.resolve_cover(album.cover_url)
        items = [_item_from_track(track, album.title, album.artist.name,
                                  client.stream_url(track.id), cover_url)
                for track in playable]
        start_index = next(
            (i for i, t in enumerate(playable) if t.id == start_track_id), 0)
        self._queue.load(items, start_index=start_index)
        self._start_current(client)
        self._report_play_started('album', album.id)

    def play_queue_tracks(self, tracks, start_index: int = 0):
        """Pour playlists / smart playlists : tracks au format QueueTrack
        (déjà porteurs d'album/artiste), seules les pistes jouables gardées."""
        client = self._get_client()
        if client is None:
            return
        playable = [t for t in tracks if t.has_file]
        if not playable:
            return
        items = [_item_from_track(
                    track, track.album_title, track.artist_name,
                    client.stream_url(track.id),
                    client.resolve_cover(track.cover_url))
                for track in playable]
        self._queue.load(items, start_index=start_index)
        self._start_current(client)

    def _start_current(self, client):
        current = self._queue.state().current
        if current is None:
            return
        self._player.load(current.stream_url, play=True)
        self._scrobbler.track_started(current.track_id)
        self._run(client.now_playing(current.track_id))
        self._publish()

    # ── Contrôles ────────────────────────────────────────────────────────────

    def toggle_play_pause(self):
        if self._player.is_playing:
            self._player.pause()
        else:
            self._player.play()

    def next(self):
        state = self._queue.next()
        self._load_from_state(state)

    def previous(self):
        """Comportement standard d'un lecteur : redémarre la piste courante
        au-delà des 3 premières secondes, sinon recule vraiment."""
        if self._player.position() > 3.0:
            self._player.seek(0)
            return
        state = self._queue.previous()
        self._load_from_state(state)

    def seek(self, position_seconds: float):
        self._player.seek(position_seconds)

    def set_volume(self, volume: float):
        self._player.set_volume(volume)
        self._publish()

    def close(self):
        """Arrête la lecture et vide la file : le mini-lecteur se masque
        (parité avec le bouton fermer du mini-lecteur web)."""
        self._player.stop()
        self._scrobbler.track_started(None)
        self._publish(self._queue.clear())

    def toggle_shuffle(self):
        self._publish(self._queue.set_shuffle(not self._queue.state().shuffle))

    def cycle_repeat(self):
        self._publish(self._queue.cycle_repeat())

    def toggle_favorite(self):
        """Bascule optimiste : la file (et donc l'UI) reflète tout de
        suite le nouveau statut ; en cas de refus serveur, on revient à
        l'ancien exactement comme PlayerConnection.toggleFavorite côté
        Android."""
        client = self._get_client()
        current = self._queue.state().current
        if client is None or current is None:
            return
        next_value = not current.is_favorite
        self._publish(self._queue.update_favorite(current.track_id, next_value))
        self._run(self._set_favorite(client, current.track_id, next_value))

    async def _set_favorite(self, client, track_id, value):
        try:
            await client.set_favorite(track_id, value)
        except ApiError:
            self._publish(self._queue.update_favorite(track_id, not value))

    def _load_from_state(self, state, already_playing: bool = False):
        current = state.current
        if current is None:
            self._player.stop()
            self._publish(state)
            return
        if not already_playing:
            self._player.load(current.stream_url, play=True)
        self._scrobbler.track_started(current.track_id)
        client = self._get_client()
        if client is not None:
            self._run(client.now_playing(current.track_id))
        self._publish(state)

    # ── Évènements du moteur ─────────────────────────────────────────────────

    def _on_position(self, position_seconds, duration_seconds):
        self._publish(position=position_seconds, duration=duration_seconds)
        due = self._scrobbler.tick(position_seconds, duration_seconds)
        if due is not None:
            client = self._get_client()
            if client is not None:
                self._run(self._scrobble(client, due.track_id, due.started_at))

    async def _scrobble(self, client, track_id, started_at):
        try:
            await client.mark_played(track_id)
        except ApiError:
            pass
        try:
            await client.scrobble(track_id, started_at)
        except ApiError:
            pass

    def _on_track_ended(self):
        already_playing = self._gapless_next_started
        self._gapless_next_started = False
        state = self._queue.track_ended()
        self._load_from_state(state, already_playing=already_playing)

    def _on_about_to_finish(self):
        """playbin3 va manquer de données : lui donner tout de suite l'URI
        suivante pour un enchaînement sans coupure (l'EOS qui suivra ne fera
        alors qu'avancer la file, sans recharger un player déjà en train de
        jouer la bonne piste — voir _on_track_ended)."""
        peek = self._queue.state()
        if peek.repeat.value == 'one':
            current = peek.current
            uri = current.stream_url if current else None
        else:
            upcoming = self._peek_next(peek)
            uri = upcoming.stream_url if upcoming else None
        self._player.set_next_uri(uri)
        self._gapless_next_started = uri is not None

    def _peek_next(self, state):
        items = state.items
        if not items or state.current_index is None:
            return None
        index = state.current_index
        if index < len(items) - 1:
            return items[index + 1]
        if state.repeat.value == 'all':
            return items[0]
        return None

    def _on_error(self, message):
        self._last_error = message
        self._publish()

    # ── Historique d'accueil ─────────────────────────────────────────────────

    def _report_play_started(self, item_type, item_id):
        client = self._get_client()
        if client is not None:
            self._run(self._report_play(client, item_type, item_id))

    async def _report_play(self, client, item_type, item_id):
        try:
            await client.report_play(item_type, item_id)
        except ApiError:
            pass

    # ── Diffusion d'état ─────────────────────────────────────────────────────

    def add_listener(self, listener):
        """Abonne un callback(PlaybackUiState) ; lui envoie l'état actuel
        tout de suite pour qu'une page ouverte après coup (fiche album)
        parte à jour sans attendre le prochain évènement."""
        self._listeners.append(listener)
        listener(self._build_state())

    def remove_listener(self, listener):
        if listener in self._listeners:
            self._listeners.remove(listener)

    def _publish(self, queue_state=None, position=None, duration=None):
        state = self._build_state(queue_state, position, duration)
        if self.on_state_changed is not None:
            self.on_state_changed(state)
        for listener in list(self._listeners):
            listener(state)
        self._last_error = None

    def _build_state(self, queue_state=None, position=None, duration=None):
        state = queue_state or self._queue.state()
        current = state.current
        return PlaybackUiState(
            has_item=state.has_item,
            is_playing=self._player.is_playing,
            current_track_id=current.track_id if current else None,
            title=current.title if current else None,
            artist=current.artist_name if current else None,
            album=current.album_title if current else None,
            cover_url=current.cover_url if current else None,
            is_favorite=current.is_favorite if current else False,
            has_next=self._queue.has_next(),
            has_previous=self._queue.has_previous(),
            shuffle=state.shuffle,
            repeat=state.repeat.value,
            position_seconds=(position if position is not None
                              else self._player.position()),
            duration_seconds=(duration if duration is not None
                              else self._player.duration()),
            volume=self._player.get_volume(),
            error=self._last_error,
        )

    def _run(self, coroutine):
        self._loop.create_task(coroutine)
