"""Parsing JSON → modèles (logique pure, testée par tests/test_parsing.py).

Même tolérance que le client Android (kotlinx : ignoreUnknownKeys +
coerceInputValues) : les clés inconnues sont ignorées, un champ optionnel
absent ou null retombe sur sa valeur par défaut, et seuls les champs requis
(id, titre…) lèvent ParseError s'ils manquent. Un payload inattendu doit
donner une erreur claire, jamais un plantage ailleurs dans l'app.
"""

from jewelbox.api.models import (
    Album,
    AlbumsPage,
    Artist,
    DynamicMixPlayed,
    Home,
    HomeRecentItem,
    Label,
    Pagination,
    Playlist,
    PlaylistSummary,
    QueueTrack,
    SearchResults,
    ServerInfo,
    SmartPlaylist,
    SmartPlaylistMeta,
    SmartSummary,
    Track,
)


class ParseError(ValueError):
    """Payload serveur inutilisable (champ requis manquant ou mal typé)."""


def _require_dict(data, what: str) -> dict:
    if not isinstance(data, dict):
        raise ParseError(f'{what} : objet JSON attendu, reçu {type(data).__name__}')
    return data


def _req_int(data: dict, key: str, what: str) -> int:
    value = data.get(key)
    # bool est un sous-type de int en Python : true n'est pas un identifiant.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ParseError(f'{what} : champ requis « {key} » manquant ou non numérique')
    return int(value)


def _req_str(data: dict, key: str, what: str) -> str:
    value = data.get(key)
    if not isinstance(value, str):
        raise ParseError(f'{what} : champ requis « {key} » manquant ou non textuel')
    return value


def _int(data: dict, key: str, default: int = 0) -> int:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return int(value)


def _opt_int(data: dict, key: str) -> int | None:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def _str(data: dict, key: str, default: str = '') -> str:
    value = data.get(key)
    return value if isinstance(value, str) else default


def _opt_str(data: dict, key: str) -> str | None:
    value = data.get(key)
    return value if isinstance(value, str) else None


def _bool(data: dict, key: str, default: bool = False) -> bool:
    value = data.get(key)
    return value if isinstance(value, bool) else default


def _list(data: dict, key: str) -> list:
    value = data.get(key)
    return value if isinstance(value, list) else []


def parse_server_info(data) -> ServerInfo:
    data = _require_dict(data, 'server-info')
    return ServerInfo(
        app=_str(data, 'app'),
        name=_str(data, 'name'),
        version=_str(data, 'version'),
        server_id=_str(data, 'server_id'),
        api=_str(data, 'api'),
        collection=_opt_str(data, 'collection'),
    )


def parse_artist(data) -> Artist:
    data = _require_dict(data, 'artist')
    return Artist(
        id=_req_int(data, 'id', 'artist'),
        name=_req_str(data, 'name', 'artist'),
    )


def parse_label(data) -> Label:
    data = _require_dict(data, 'label')
    return Label(
        id=_req_int(data, 'id', 'label'),
        name=_req_str(data, 'name', 'label'),
    )


def parse_track(data) -> Track:
    data = _require_dict(data, 'track')
    return Track(
        id=_req_int(data, 'id', 'track'),
        title=_req_str(data, 'title', 'track'),
        position=_int(data, 'position'),
        duration=_opt_str(data, 'duration'),
        has_file=_bool(data, 'has_file'),
        play_count=_int(data, 'play_count'),
        is_favorite=_bool(data, 'is_favorite'),
    )


def parse_album(data) -> Album:
    data = _require_dict(data, 'album')
    return Album(
        id=_req_int(data, 'id', 'album'),
        title=_req_str(data, 'title', 'album'),
        artist=parse_artist(data.get('artist')),
        year=_opt_int(data, 'year'),
        genre=_opt_str(data, 'genre'),
        rating=_opt_int(data, 'rating'),
        total_duration=_opt_str(data, 'total_duration'),
        ean=_opt_str(data, 'ean'),
        notes=_opt_str(data, 'notes'),
        cover_url=_opt_str(data, 'cover_url'),
        has_audio=_bool(data, 'has_audio'),
        label=parse_label(data['label']) if isinstance(data.get('label'), dict) else None,
        tracks=tuple(parse_track(track) for track in _list(data, 'tracks')),
    )


def parse_queue_track(data) -> QueueTrack:
    data = _require_dict(data, 'queue-track')
    return QueueTrack(
        id=_req_int(data, 'id', 'queue-track'),
        title=_req_str(data, 'title', 'queue-track'),
        entry_id=_opt_int(data, 'entry_id'),
        position=_int(data, 'position'),
        duration=_opt_str(data, 'duration'),
        has_file=_bool(data, 'has_file'),
        play_count=_int(data, 'play_count'),
        is_favorite=_bool(data, 'is_favorite'),
        album_id=_int(data, 'album_id'),
        album_title=_str(data, 'album_title'),
        artist_name=_str(data, 'artist_name'),
        cover_url=_opt_str(data, 'cover_url'),
    )


def parse_playlist_summary(data) -> PlaylistSummary:
    data = _require_dict(data, 'playlist')
    return PlaylistSummary(
        id=_req_int(data, 'id', 'playlist'),
        name=_req_str(data, 'name', 'playlist'),
        created_at=_opt_str(data, 'created_at'),
        updated_at=_opt_str(data, 'updated_at'),
        track_count=_int(data, 'track_count'),
        total_duration_seconds=_int(data, 'total_duration_seconds'),
        cover_url=_opt_str(data, 'cover_url'),
    )


def parse_playlists(data) -> tuple[PlaylistSummary, ...]:
    """GET /api/playlists : les lignes vivent sous la clé « data »."""
    data = _require_dict(data, 'playlists')
    return tuple(parse_playlist_summary(row) for row in _list(data, 'data'))


def parse_playlist(data) -> Playlist:
    data = _require_dict(data, 'playlist')
    return Playlist(
        id=_req_int(data, 'id', 'playlist'),
        name=_req_str(data, 'name', 'playlist'),
        created_at=_opt_str(data, 'created_at'),
        updated_at=_opt_str(data, 'updated_at'),
        tracks=tuple(parse_queue_track(track) for track in _list(data, 'tracks')),
        added=_int(data, 'added'),
    )


def parse_smart_playlists(data) -> tuple[SmartPlaylistMeta, ...]:
    data = _require_dict(data, 'smart-playlists')
    return tuple(
        SmartPlaylistMeta(
            key=_req_str(row, 'key', 'smart-playlist'),
            track_count=_int(row, 'track_count'),
        )
        for row in (_require_dict(row, 'smart-playlist') for row in _list(data, 'data'))
    )


def parse_smart_playlist(data) -> SmartPlaylist:
    data = _require_dict(data, 'smart-playlist')
    return SmartPlaylist(
        key=_str(data, 'key'),
        tracks=tuple(parse_queue_track(track) for track in _list(data, 'tracks')),
    )


def parse_dynamic_mix_played(data) -> DynamicMixPlayed:
    data = _require_dict(data, 'dynamic-mix')
    return DynamicMixPlayed(
        removed=_bool(data, 'removed'),
        tracks=tuple(parse_queue_track(track) for track in _list(data, 'tracks')),
    )


def parse_search_results(data) -> SearchResults:
    data = _require_dict(data, 'search')
    return SearchResults(
        albums=tuple(parse_album(album) for album in _list(data, 'albums')),
        tracks=tuple(parse_queue_track(track) for track in _list(data, 'tracks')),
    )


def parse_home(data) -> Home:
    data = _require_dict(data, 'home')
    return Home(
        recent=tuple(_parse_home_recent_item(item) for item in _list(data, 'recent')),
        suggestions=tuple(parse_album(album) for album in _list(data, 'suggestions')),
    )


def _parse_home_recent_item(data) -> HomeRecentItem:
    data = _require_dict(data, 'home-recent')
    album = data.get('album')
    playlist = data.get('playlist')
    smart = data.get('smart')
    return HomeRecentItem(
        item_type=_str(data, 'item_type'),
        played_at=_opt_str(data, 'played_at'),
        album=parse_album(album) if isinstance(album, dict) else None,
        playlist=parse_playlist_summary(playlist) if isinstance(playlist, dict) else None,
        smart=parse_smart_summary(smart) if isinstance(smart, dict) else None,
    )


def parse_smart_summary(data) -> SmartSummary:
    data = _require_dict(data, 'smart')
    return SmartSummary(
        key=_str(data, 'key'),
        track_count=_int(data, 'track_count'),
    )


def parse_albums_page(data) -> AlbumsPage:
    data = _require_dict(data, 'albums')
    pagination = data.get('pagination')
    return AlbumsPage(
        data=tuple(parse_album(album) for album in _list(data, 'data')),
        pagination=_parse_pagination(pagination) if isinstance(pagination, dict) else Pagination(),
    )


def _parse_pagination(data: dict) -> Pagination:
    return Pagination(
        total=_int(data, 'total'),
        page=_int(data, 'page', 1),
        limit=_int(data, 'limit', 24),
        # Le serveur envoie ce champ-là en camelCase, fidèle au front web.
        total_pages=_int(data, 'totalPages'),
    )
