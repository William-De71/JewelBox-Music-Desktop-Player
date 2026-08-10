"""Session de lecture : colle entre la file pure (core.queue.Queue), le
moteur GStreamer (playback.player.Player) et les appels réseau qui
accompagnent la lecture (compteur local, Last.fm, historique d'accueil).

Code frontière (exclu de la couverture) : les décisions elles-mêmes
(navigation de file, règles de scrobbling) vivent dans jewelbox.core, purs
et testés — cette classe ne fait qu'enchaîner les appels au bon moment.

La file survit à la fermeture de l'app : elle est enregistrée par
core.playback_state.PlaybackStateStore à chaque changement, et rechargée au
démarrage en PAUSE à sa position exacte (parité avec restoreSavedQueue()
côté Android — l'utilisateur appuie sur lecture pour repartir).

Pas de mix dynamique pour cette itération.
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
    # Nom de la source d'où vient la file (playlist utilisateur ou liste
    # intelligente) ; None pour un album ou une piste seule.
    source_name: str | None = None
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

    def __init__(self, get_client, loop=None, state_store=None):
        from jewelbox.playback.player import Player  # importé ici : GStreamer

        self._get_client = get_client
        self._state_store = state_store
        self._loop = loop or asyncio.get_event_loop_policy().get_event_loop()
        self._queue = Queue()
        self._scrobbler = ScrobbleTracker()
        self._player = Player()
        self._player.on_position = self._on_position
        self._player.on_track_ended = self._on_track_ended
        self._player.on_stream_started = self._on_stream_started
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
        # suivante (enchaînement gapless) : le STREAM_START qui suit doit
        # alors faire avancer la file sans recharger le player (déjà en train
        # de jouer la bonne piste). Décrémenté par _on_stream_started.
        self._pending_gapless = 0
        # Ignore le tout premier STREAM_START, celui du load initial : la file
        # est déjà positionnée sur la bonne piste, rien à avancer.
        self._stream_started_once = False
        # Nom de la source de la file en cours (playlist ou liste intelligente),
        # affiché dans les lecteurs ; None pour un album ou une piste seule.
        self._source_name: str | None = None
        # Origine de la file ('album' / 'playlist' / 'smart' + référence),
        # enregistrée avec l'instantané comme le fait SavedQueue côté Android.
        self._source_type: str | None = None
        self._source_id: str | None = None
        # Une file restaurée est chargée en pause : rien n'a encore été joué,
        # donc pas de now_playing ni de report de lecture tant que
        # l'utilisateur n'a pas appuyé sur lecture. Voir _start_resumed_track.
        self._resume_pending = False
        # Position du point de reprise, tant que la lecture n'a pas repris et
        # que playbin3 ne la rapporte donc pas encore lui-même.
        self._resume_position = 0.0
        # Numéro de la piste chargée, incrémenté à chaque load() : le
        # repositionnement différé d'une reprise ne doit s'appliquer que tant
        # qu'on est encore sur CETTE piste-là (l'utilisateur peut passer à la
        # suivante avant que le flux n'ait fini de se mettre en tampon).
        self._load_generation = 0
        # Vrai pendant la restauration : les évènements du player ne doivent
        # pas réenregistrer l'instantané par-dessus lui-même (parité avec le
        # drapeau `restoring` d'Android).
        self._restoring = False

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
        # Un album n'affiche pas de nom de source dans les lecteurs.
        self._source_name = None
        self._set_source('album', album.id)
        self._queue.load(items, start_index=start_index)
        self._start_current(client)
        self._report_play_started('album', album.id)

    def play_queue_tracks(self, tracks, start_index: int = 0,
                          report_playlist_id: int | None = None,
                          report_smart_key: str | None = None,
                          source_name: str | None = None):
        """Pour playlists / smart playlists : tracks au format QueueTrack
        (déjà porteurs d'album/artiste), seules les pistes jouables gardées.

        report_playlist_id (playlist utilisateur) ou report_smart_key (liste
        intelligente), s'il est fourni, signale au serveur le début de lecture
        pour alimenter les récents de l'accueil. Un seul des deux à la fois.

        source_name est le nom affiché dans les lecteurs (playlist ou liste
        intelligente en cours)."""
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
        self._source_name = source_name
        if report_playlist_id is not None:
            self._set_source('playlist', report_playlist_id)
        elif report_smart_key is not None:
            self._set_source('smart', report_smart_key)
        else:
            self._set_source(None, None)
        self._queue.load(items, start_index=start_index)
        self._start_current(client)
        if report_playlist_id is not None:
            self._report_play_started('playlist', report_playlist_id)
        elif report_smart_key is not None:
            self._report_play_started('smart', report_smart_key)

    def _set_source(self, source_type, source_ref):
        self._source_type = source_type
        self._source_id = None if source_ref is None else str(source_ref)

    def _start_current(self, client):
        current = self._queue.state().current
        if current is None:
            return
        # Chargement direct : le STREAM_START qui suivra correspond à cette
        # piste-ci (déjà courante dans la file), pas à un enchaînement gapless.
        self._pending_gapless = 0
        self._stream_started_once = False
        self._resume_pending = False
        self._load_generation += 1
        self._player.load(current.stream_url, play=True)
        self._scrobbler.track_started(current.track_id)
        self._run(client.now_playing(current.track_id))
        self._publish()
        self._save_state()

    # ── Contrôles ────────────────────────────────────────────────────────────

    def toggle_play_pause(self):
        if self._player.is_playing:
            self._player.pause()
            # Mettre en pause précède souvent la fermeture de l'app : on prend
            # le point de reprise tout de suite (comme onIsPlayingChanged
            # côté Android) plutôt que d'attendre le prochain évènement.
            self._save_state()
        else:
            self._player.play()
            self._start_resumed_track()

    def _start_resumed_track(self):
        """Première lecture d'une file restaurée : la piste était chargée mais
        silencieuse, le serveur n'a donc rien vu. C'est maintenant qu'elle
        commence vraiment — scrobbler, now_playing et récents d'accueil."""
        if not self._resume_pending:
            return
        self._resume_pending = False
        self._resume_position = 0.0
        current = self._queue.state().current
        if current is None:
            return
        self._scrobbler.track_started(current.track_id)
        client = self._get_client()
        if client is not None:
            self._run(client.now_playing(current.track_id))
        if self._source_type == 'smart':
            self._report_play_started('smart', self._source_id)
        elif self._source_type in ('album', 'playlist'):
            # album/playlist attendent un id numérique côté API ; l'instantané
            # les garde en texte (forme JSON commune avec la clé des smart).
            try:
                self._report_play_started(self._source_type,
                                          int(self._source_id))
            except (TypeError, ValueError):
                pass

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
        (parité avec le bouton fermer du mini-lecteur web). Fermer le lecteur
        vaut « repartir de zéro » : l'instantané de reprise est effacé."""
        self._player.stop()
        self._scrobbler.track_started(None)
        self._resume_pending = False
        self._set_source(None, None)
        self._source_name = None
        self._publish(self._queue.clear())
        self._save_state()

    def toggle_shuffle(self):
        self._publish(self._queue.set_shuffle(not self._queue.state().shuffle))
        self._save_state()

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
            # File épuisée : la lecture s'arrête, mais l'instantané garde les
            # pistes — au prochain lancement on repart de la dernière piste.
            self._player.stop()
            self._resume_pending = False
            self._publish(state)
            self._save_state()
            return
        if not already_playing:
            # Saut explicite (suivant/précédent) : chargement direct, le
            # prochain STREAM_START ne doit pas re-avancer la file.
            self._pending_gapless = 0
            self._stream_started_once = False
            self._load_generation += 1
            self._player.load(current.stream_url, play=True)
        # Changer de piste depuis une file restaurée la démarre pour de bon :
        # plus rien à différer.
        self._resume_pending = False
        self._scrobbler.track_started(current.track_id)
        client = self._get_client()
        if client is not None:
            self._run(client.now_playing(current.track_id))
        self._publish(state)
        self._save_state()

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

    def _on_stream_started(self):
        """Nouveau flux démarré. En gapless (about-to-finish a fourni l'URI
        suivante), c'est ici — et non à l'EOS, qui ne survient plus — que la
        file avance et que l'UI se met à jour sur la nouvelle piste. Le tout
        premier STREAM_START (load initial) et ceux des sauts explicites sont
        ignorés : la file est déjà sur la bonne piste."""
        if not self._stream_started_once:
            self._stream_started_once = True
            return
        if self._pending_gapless <= 0:
            return
        self._pending_gapless -= 1
        state = self._queue.track_ended()
        # already_playing : playbin3 joue déjà le bon flux, on ne recharge pas.
        self._load_from_state(state, already_playing=True)

    def _on_track_ended(self):
        """EOS : atteint seulement quand aucune URI suivante n'avait été
        fournie (dernière piste, ou file épuisée). L'enchaînement gapless,
        lui, passe par _on_stream_started."""
        state = self._queue.track_ended()
        self._load_from_state(state, already_playing=False)

    def _on_about_to_finish(self):
        """playbin3 va manquer de données : lui donner tout de suite l'URI
        suivante pour un enchaînement sans coupure. Le STREAM_START qui suivra
        fera avancer la file (voir _on_stream_started) ; si aucune URI n'est
        fournie, c'est un EOS qui clôturera (voir _on_track_ended)."""
        # La file décide de la piste suivante dans l'ordre de LECTURE : ainsi
        # l'URI préchargée est exactement celle que track_ended() rendra
        # courante au prochain STREAM_START (pas de décalage audio/affichage
        # en mode aléatoire). peek_next() gère aussi le mode ONE (rejoue).
        upcoming = self._queue.peek_next()
        uri = upcoming.stream_url if upcoming else None
        self._player.set_next_uri(uri)
        if uri is not None:
            self._pending_gapless += 1

    def _on_error(self, message):
        self._last_error = message
        self._publish()

    # ── Historique d'accueil ─────────────────────────────────────────────────

    def _report_play_started(self, item_type, item_ref):
        # item_ref : id numérique pour album/playlist, clé texte pour smart.
        client = self._get_client()
        if client is not None:
            self._run(self._report_play(client, item_type, item_ref))

    async def _report_play(self, client, item_type, item_ref):
        try:
            if item_type == 'smart':
                await client.report_smart_play(item_ref)
            else:
                await client.report_play(item_type, item_ref)
        except ApiError:
            pass

    # ── Reprise entre deux lancements ────────────────────────────────────────

    def save_now(self):
        """Prend un instantané tout de suite — appelée à la fermeture de
        l'app, pour capter la position d'une lecture en cours."""
        self._save_state()

    def _save_state(self):
        """Instantané de la file et du point de lecture, pour le prochain
        lancement. Sans store (tests, mode dégradé) ou pendant la restauration
        elle-même, ne fait rien."""
        if self._state_store is None or self._restoring:
            return
        client = self._get_client()
        # server_url identifie le serveur d'où viennent les URLs de flux : une
        # file enregistrée contre un autre serveur ne sera pas restaurée.
        base_url = client.base_url if client is not None else ''
        # Sur une reprise pas encore lancée, playbin3 rapporte 0 tant que le
        # seek différé n'a pas eu lieu : garder la position restaurée, sinon
        # fermer l'app sans appuyer sur lecture perdrait le point de reprise.
        position = self._player.position()
        if self._resume_pending and position <= 0:
            position = self._resume_position
        self._state_store.save(self._queue.to_saved(
            base_url,
            source_type=self._source_type,
            source_id=self._source_id,
            source_name=self._source_name,
            position_ms=int(max(0.0, position) * 1000),
        ))

    def restore(self):
        """Recharge la file du dernier lancement, en PAUSE à sa position.

        Rien ne démarre : l'utilisateur appuie sur lecture (parité avec
        restoreSavedQueue() côté Android). Sans effet si une lecture a déjà
        commencé entre-temps, ou si l'instantané vient d'un autre serveur —
        ses URLs de flux ne pointeraient plus vers rien de valide."""
        if self._state_store is None or self._queue.state().has_item:
            return
        saved = self._state_store.load()
        if saved is None:
            return
        client = self._get_client()
        if client is None or saved.get('server_url') != client.base_url:
            return

        self._restoring = True
        try:
            restored = Queue.from_saved(saved)
            state = restored.state()
            if not state.has_item:
                return
            self._queue = restored
            self._source_type = saved.get('source_type')
            self._source_id = saved.get('source_id')
            # Le nom de la playlist / liste intelligente reste affiché sous le
            # titre après la reprise, comme avant la fermeture.
            self._source_name = saved.get('source_name') or None
            self._pending_gapless = 0
            self._stream_started_once = False
            # Chargé sans jouer : prêt à repartir, silencieux jusqu'à ce que
            # l'utilisateur le demande — c'est alors seulement que la lecture
            # sera signalée au serveur (voir _start_resumed_track).
            self._resume_pending = True
            self._resume_position = max(0, saved.get('position_ms') or 0) / 1000
            self._load_generation += 1
            self._player.load(state.current.stream_url, play=False)
            self._publish(state, position=self._resume_position)
            # La durée n'arrive qu'une fois le flux en tampon, et le seek doit
            # l'attendre : on republie les deux dès que playbin3 les connaît,
            # sinon le lecteur afficherait « 0:00 » de durée jusqu'à la lecture.
            self._pending_seek(self._resume_position)
        finally:
            self._restoring = False

    def _pending_seek(self, position_seconds: float):
        """Repositionne dès que le flux est prêt, et republie position/durée.

        Un seek juste après load() arriverait avant que playbin3 ne connaisse
        la durée et serait ignoré : on réessaie jusqu'à ce qu'elle soit
        connue (flux mis en mémoire tampon), puis on abandonne au bout de
        quelques secondes."""
        from gi.repository import GLib

        attempts = [0]
        generation = self._load_generation

        def try_seek():
            attempts[0] += 1
            if generation != self._load_generation:
                return False  # une autre piste a été chargée entre-temps
            duration = self._player.duration()
            if duration <= 0:
                return attempts[0] < self._SEEK_ATTEMPTS
            if position_seconds > 0:
                self._player.seek(position_seconds)
            self._publish(position=position_seconds, duration=duration)
            return False

        GLib.timeout_add(self._SEEK_RETRY_MS, try_seek)

    # Repositionnement d'une file restaurée : ~5 s de tentatives, largement
    # de quoi laisser un flux HTTP se mettre en tampon sans boucler si le
    # serveur ne répond pas.
    _SEEK_RETRY_MS = 100
    _SEEK_ATTEMPTS = 50

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
            source_name=self._source_name if state.has_item else None,
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
