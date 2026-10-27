"""Client HTTP du serveur JewelBox — libsoup 3 + asyncio sur la boucle GLib.

Code frontière (exclu de la couverture, exercé à la main) : tout le parsing
des réponses vit dans jewelbox.api.parsing, qui lui est testé. Les méthodes
sont des coroutines à attendre depuis la boucle GLib installée par gi.events ;
la surface d'API est le miroir de JewelBoxApi.kt côté Android.

Délais volontairement généreux en lecture : à travers un VPN le lien est plus
lent et moins stable que sur le LAN, et on ne fait ici que de petits JSON.
"""

import json

import gi

gi.require_version('Soup', '3.0')

from gi.repository import GLib, Soup  # noqa: E402

from jewelbox.api import parsing  # noqa: E402
from jewelbox.core import server_url  # noqa: E402

_TIMEOUT_SECONDS = 15


class ApiError(Exception):
    """Échec d'un appel serveur : réseau, HTTP non-2xx ou JSON invalide.

    status vaut 0 quand la requête n'a pas abouti (pas de réponse HTTP).
    """

    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


class JewelBoxClient:
    """Un client = un serveur. On le reconstruit quand l'URL change."""

    def __init__(self, base_url: str, device_id: str = ''):
        self.base_url = server_url.normalize(base_url)
        self._device_id = device_id
        self._session = Soup.Session(timeout=_TIMEOUT_SECONDS)

    # ── Transport ────────────────────────────────────────────────────────────

    async def _request(self, method: str, path: str, *, query=None, body=None):
        """Envoie la requête et rend le JSON décodé (None si corps vide)."""
        url = server_url.api_url(self.base_url, path, query)
        message = Soup.Message.new(method, url)
        if message is None:
            raise ApiError(f'URL invalide : {url}')
        if self._device_id:
            message.get_request_headers().append('X-Device-Id', self._device_id)
        if body is not None:
            payload = json.dumps(body).encode()
            message.set_request_body_from_bytes(
                'application/json', GLib.Bytes.new(payload))

        try:
            response = await self._session.send_and_read_async(
                message, GLib.PRIORITY_DEFAULT, None)
        except GLib.Error as error:
            raise ApiError(f'{url} : {error.message}') from error

        status = message.get_status()
        if not 200 <= status < 300:
            reason = message.get_reason_phrase() or 'erreur HTTP'
            raise ApiError(f'{url} : {status} {reason}', status=status)

        data = response.get_data()
        if not data:
            return None
        try:
            return json.loads(data)
        except ValueError as error:
            raise ApiError(f'{url} : réponse non JSON', status=status) from error

    async def _get(self, path, query=None):
        return await self._request('GET', path, query=query)

    async def _post(self, path, body=None):
        return await self._request('POST', path, body=body)

    async def _patch(self, path, body=None):
        return await self._request('PATCH', path, body=body)

    async def _put(self, path, body=None):
        return await self._request('PUT', path, body=body)

    async def _delete(self, path):
        return await self._request('DELETE', path)

    # ── Serveur ──────────────────────────────────────────────────────────────

    async def health(self) -> bool:
        data = await self._get('/api/health')
        return isinstance(data, dict) and data.get('status') == 'ok'

    async def server_info(self) -> parsing.ServerInfo:
        """Identité du serveur (serveur >= 1.12 ; 404 avant)."""
        return parsing.parse_server_info(await self._get('/api/server-info'))

    # ── Bibliothèque ─────────────────────────────────────────────────────────

    async def albums(self, page=1, limit=24, sort='artist', order='asc',
                     genre: str | None = None):
        """Page d'albums possédés (wanted=false exclut la wishlist).

        Tri artist : le serveur ajoute l'année en clé secondaire, donc un seul
        appel donne l'ordre artiste → date. genre filtre sur un genre exact.
        """
        query = {
            'page': page, 'limit': limit,
            'sort': sort, 'order': order, 'wanted': False,
        }
        if genre:
            query['genre'] = genre
        data = await self._get('/api/albums', query)
        return parsing.parse_albums_page(data)

    async def genres(self):
        return parsing.parse_genres(await self._get('/api/albums/genres'))

    async def radio(self, album_id: int | None = None,
                    artist_id: int | None = None):
        """Radio autour d'un album ou d'un artiste (un seul des deux)."""
        query = ({'album_id': album_id} if album_id is not None
                 else {'artist_id': artist_id})
        return parsing.parse_radio(await self._get('/api/player/radio', query))

    async def album(self, album_id: int):
        return parsing.parse_album(await self._get(f'/api/albums/{album_id}'))

    # ── Accueil, recherche ───────────────────────────────────────────────────

    async def home(self, ranking_size: int | None = None):
        """Flux d'accueil : 8 récents + 12 suggestions (serveur >= 1.9).
        ranking_size : voir smart_playlists() — taille des classements, pour
        le nombre de pistes affiché sur une tuile intelligente récente."""
        query = None if ranking_size is None else {'smart_limit': ranking_size}
        return parsing.parse_home(await self._get('/api/player/home', query))

    async def refresh_suggestions(self):
        """Nouveau tirage des suggestions du jour (serveur >= 1.21, 404
        avant) ; le serveur garde ce tirage jusqu'au lendemain."""
        data = await self._post('/api/player/home/suggestions')
        return parsing.parse_suggestions(data)

    async def search(self, query: str):
        """Recherche bibliothèque (serveur >= 1.7) ; q >= 2 caractères."""
        data = await self._get('/api/player/search', {'q': query})
        return parsing.parse_search_results(data)

    async def report_play(self, item_type: str, item_id: int) -> None:
        """Signale un début de lecture album/playlist (alimente l'accueil)."""
        await self._post('/api/player/history',
                         {'item_type': item_type, 'item_id': item_id})

    async def report_smart_play(self, key: str) -> None:
        """Signale un début de lecture d'une liste intelligente, identifiée par
        sa clé texte (les smart playlists n'ont pas d'id numérique)."""
        await self._post('/api/player/history',
                         {'item_type': 'smart', 'item_key': key})

    # ── Pistes ───────────────────────────────────────────────────────────────

    def stream_url(self, track_id: int) -> str:
        """URL de streaming à donner telle quelle à playbin3."""
        return server_url.stream_url(self.base_url, track_id)

    def resolve_cover(self, cover_url):
        """Résout une cover_url relative (/covers/…) contre ce serveur."""
        return server_url.resolve_url(self.base_url, cover_url)

    async def fetch_bytes(self, url: str) -> bytes:
        """Télécharge une ressource brute (pochette) ; ApiError en cas d'échec."""
        message = Soup.Message.new('GET', url)
        if message is None:
            raise ApiError(f'URL invalide : {url}')
        try:
            response = await self._session.send_and_read_async(
                message, GLib.PRIORITY_DEFAULT, None)
        except GLib.Error as error:
            raise ApiError(f'{url} : {error.message}') from error
        status = message.get_status()
        if not 200 <= status < 300:
            raise ApiError(f'{url} : {status}', status=status)
        return response.get_data() or b''

    async def mark_played(self, track_id: int) -> None:
        """Compteur local (play_count/last_played_at), indépendant de Last.fm."""
        await self._post(f'/api/player/tracks/{track_id}/played')

    async def set_favorite(self, track_id: int, is_favorite: bool) -> None:
        await self._patch(f'/api/player/tracks/{track_id}/favorite',
                          {'is_favorite': is_favorite})

    # ── Playlists utilisateur ────────────────────────────────────────────────

    async def playlists(self):
        return parsing.parse_playlists(await self._get('/api/playlists'))

    async def playlist(self, playlist_id: int):
        return parsing.parse_playlist(await self._get(f'/api/playlists/{playlist_id}'))

    async def create_playlist(self, name: str):
        return parsing.parse_playlist(await self._post('/api/playlists', {'name': name}))

    async def rename_playlist(self, playlist_id: int, name: str):
        data = await self._patch(f'/api/playlists/{playlist_id}', {'name': name})
        return parsing.parse_playlist(data)

    async def delete_playlist(self, playlist_id: int) -> None:
        await self._delete(f'/api/playlists/{playlist_id}')

    async def add_track_to_playlist(self, playlist_id: int, track_id: int):
        data = await self._post(f'/api/playlists/{playlist_id}/tracks',
                                {'track_id': track_id})
        return parsing.parse_playlist(data)

    async def add_album_to_playlist(self, playlist_id: int, album_id: int):
        data = await self._post(f'/api/playlists/{playlist_id}/tracks',
                                {'album_id': album_id})
        return parsing.parse_playlist(data)

    async def remove_playlist_entry(self, playlist_id: int, entry_id: int):
        data = await self._delete(f'/api/playlists/{playlist_id}/tracks/{entry_id}')
        return parsing.parse_playlist(data)

    async def reorder_playlist(self, playlist_id: int, entry_ids):
        """Réordonnancement complet : tous les entry_id dans le nouvel ordre."""
        data = await self._put(f'/api/playlists/{playlist_id}/tracks',
                               {'entry_ids': list(entry_ids)})
        return parsing.parse_playlist(data)

    # ── Smart playlists ──────────────────────────────────────────────────────

    async def smart_playlists(self, ranking_size: int | None = None):
        """ranking_size : taille des classements « top N » (Ajouts récents,
        Écoutées récemment, Les plus écoutées) ; un serveur qui ne connaît pas
        ?limit= l'ignore et garde ses 100 pistes."""
        return parsing.parse_smart_playlists(await self._get(
            '/api/smart-playlists', _ranking_query(ranking_size)))

    async def smart_playlist(self, key: str, ranking_size: int | None = None):
        return parsing.parse_smart_playlist(await self._get(
            f'/api/smart-playlists/{key}', _ranking_query(ranking_size)))

    async def dynamic_mix_played(self, track_id: int):
        """Piste du mix terminée : le serveur tourne et recomplète la liste."""
        data = await self._post('/api/smart-playlists/dynamic_mix/played',
                                {'track_id': track_id})
        return parsing.parse_dynamic_mix_played(data)

    async def dynamic_mix_refresh(self):
        """Jette le mix courant et en tire un tout neuf (serveur >= 1.6)."""
        data = await self._post('/api/smart-playlists/dynamic_mix/refresh')
        return parsing.parse_smart_playlist(data)

    async def dynamic_mix_remove(self, track_id: int):
        """Retrait manuel d'une piste (serveur >= 1.8 ; 404 avant)."""
        data = await self._delete(f'/api/smart-playlists/dynamic_mix/tracks/{track_id}')
        return parsing.parse_dynamic_mix_played(data)

    # ── File partagée entre appareils (X-Device-Id) ──────────────────────────

    async def save_queue(self, track_ids, current_index: int,
                         position_sec: float, device_label: str,
                         source_label: str | None = None) -> None:
        """Remplace la file de CET appareil sur le serveur. source_label :
        d'où vient la file (album, playlist…), affiché aux autres appareils
        (ignoré par un serveur < 1.17)."""
        await self._put('/api/player/queue', {
            'track_ids': list(track_ids), 'current_index': current_index,
            'position_sec': position_sec, 'device_label': device_label,
            'source_label': source_label,
        })

    async def update_queue_state(self, current_index: int,
                                 position_sec: float) -> None:
        """Progression seule (piste courante, position), sans les pistes."""
        await self._patch('/api/player/queue/state', {
            'current_index': current_index, 'position_sec': position_sec,
        })

    async def report_live_state(self, body: dict) -> None:
        """État en direct (lecture/pause, piste, volume, modes) pour le
        contrôle à distance. Même route que la progression : un serveur
        < 1.18 garde la position et ignore le reste."""
        await self._patch('/api/player/queue/state', body)

    async def lyrics(self, track_id: int):
        """Paroles d'une piste (serveur >= 1.20) : la réponse brute, lue par
        core.lyrics.parse_lyrics."""
        return await self._get(f'/api/player/tracks/{track_id}/lyrics')

    async def track(self, track_id: int):
        """Une piste au format QueueTrack (serveur >= 1.18)."""
        return parsing.parse_queue_track(
            await self._get(f'/api/player/tracks/{track_id}'))

    def remote_events_message(self, label: str, capabilities):
        """Requête du flux d'ordres à distance (text/event-stream), ouverte
        et lue par playback/remote_control.py."""
        url = server_url.api_url(self.base_url, '/api/player/remote/events', {
            'label': label, 'capabilities': ','.join(capabilities)})
        message = Soup.Message.new('GET', url)
        if message is None:
            raise ApiError(f'URL invalide : {url}')
        headers = message.get_request_headers()
        headers.append('X-Device-Id', self._device_id)
        headers.append('Accept', 'text/event-stream')
        return message

    async def clear_queue(self) -> None:
        await self._delete('/api/player/queue')

    async def own_queue(self):
        """La file de CET appareil côté serveur."""
        return parsing.parse_device_queue(await self._get('/api/player/queue'))

    async def queue_devices(self):
        """Les files laissées par les AUTRES appareils."""
        return parsing.parse_queue_devices(
            await self._get('/api/player/queue/devices'))

    async def device_queue(self, device_id: str):
        """La file d'un autre appareil, pour la reprendre ici."""
        from urllib.parse import quote
        return parsing.parse_device_queue(await self._get(
            f'/api/player/queue/devices/{quote(device_id, safe="")}'))

    # ── Last.fm (fire-and-forget côté serveur : 204 même sans scrobbling) ────

    async def now_playing(self, track_id: int) -> None:
        await self._post('/api/lastfm/nowplaying', {'track_id': track_id})

    async def scrobble(self, track_id: int, started_at: int) -> None:
        await self._post('/api/lastfm/scrobble',
                         {'track_id': track_id, 'started_at': started_at})


def _ranking_query(ranking_size):
    return None if ranking_size is None else {'limit': ranking_size}
