"""Le parsing doit avaler les payloads réels du serveur, les payloads
partiels (vieux serveurs), les champs null et les clés inconnues — et ne
lever ParseError que quand un champ requis manque vraiment."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))

from jewelbox.api import parsing  # noqa: E402
from jewelbox.api.parsing import ParseError  # noqa: E402


FULL_ALBUM = {
    'id': 7,
    'title': 'Moon Safari',
    'year': 1998,
    'genre': 'Electro',
    'rating': 5,
    'total_duration': '43:47',
    'ean': '724384497820',
    'notes': 'Édition originale',
    'cover_url': '/covers/7.jpg',
    'has_audio': True,
    'artist': {'id': 3, 'name': 'Air'},
    'label': {'id': 2, 'name': 'Virgin'},
    'tracks': [
        {'id': 71, 'position': 1, 'title': 'La femme d’argent',
         'duration': '7:11', 'has_file': True, 'play_count': 4,
         'is_favorite': True},
        {'id': 72, 'position': 2, 'title': 'Sexy Boy', 'duration': '4:58'},
    ],
}

QUEUE_TRACK = {
    'id': 71,
    'entry_id': 12,
    'position': 1,
    'title': 'La femme d’argent',
    'duration': '7:11',
    'has_file': True,
    'play_count': 4,
    'is_favorite': True,
    'album_id': 7,
    'album_title': 'Moon Safari',
    'artist_name': 'Air',
    'cover_url': '/covers/7.jpg',
}


# ── server-info ───────────────────────────────────────────────────────────────

def test_server_info_full():
    info = parsing.parse_server_info({
        'app': 'jewelbox', 'name': 'Salon', 'version': '1.12.0',
        'server_id': 'abc-def', 'api': 'v1', 'collection': 'CD',
    })
    assert info.app == 'jewelbox'
    assert info.name == 'Salon'
    assert info.version == '1.12.0'
    assert info.server_id == 'abc-def'
    assert info.api == 'v1'
    assert info.collection == 'CD'
    assert info.is_jewelbox


def test_server_info_empty_payload_gives_defaults():
    info = parsing.parse_server_info({})
    assert info.app == ''
    assert info.collection is None
    assert not info.is_jewelbox


def test_server_info_squatter_is_not_jewelbox():
    info = parsing.parse_server_info({'app': 'autre-chose'})
    assert not info.is_jewelbox


def test_server_info_non_dict_raises():
    with pytest.raises(ParseError):
        parsing.parse_server_info('<html>pas du JSON utile</html>')


# ── album, artist, label, track ───────────────────────────────────────────────

def test_album_full():
    album = parsing.parse_album(FULL_ALBUM)
    assert album.id == 7
    assert album.title == 'Moon Safari'
    assert album.year == 1998
    assert album.artist.name == 'Air'
    assert album.label.name == 'Virgin'
    assert album.cover_url == '/covers/7.jpg'
    assert album.has_audio
    assert len(album.tracks) == 2
    assert album.tracks[0].is_favorite
    assert album.tracks[1].play_count == 0


def test_album_minimal_list_shape():
    album = parsing.parse_album({
        'id': 1, 'title': 'X', 'artist': {'id': 2, 'name': 'Y'},
    })
    assert album.year is None
    assert album.label is None
    assert album.tracks == ()
    assert not album.has_audio


def test_album_null_optionals_fall_back():
    album = parsing.parse_album({
        'id': 1, 'title': 'X', 'artist': {'id': 2, 'name': 'Y'},
        'year': None, 'label': None, 'tracks': None, 'has_audio': None,
        'cover_url': None,
    })
    assert album.year is None
    assert album.label is None
    assert album.tracks == ()
    assert album.cover_url is None


def test_album_unknown_keys_ignored():
    album = parsing.parse_album({
        'id': 1, 'title': 'X', 'artist': {'id': 2, 'name': 'Y'},
        'is_wanted': 1, 'nouveau_champ': {'a': 1},
    })
    assert album.id == 1


def test_album_missing_id_raises():
    with pytest.raises(ParseError):
        parsing.parse_album({'title': 'X', 'artist': {'id': 2, 'name': 'Y'}})


def test_album_missing_title_raises():
    with pytest.raises(ParseError):
        parsing.parse_album({'id': 1, 'artist': {'id': 2, 'name': 'Y'}})


def test_album_missing_artist_raises():
    with pytest.raises(ParseError):
        parsing.parse_album({'id': 1, 'title': 'X'})


def test_artist_bool_id_rejected():
    # true est un int pour Python, pas pour nous : un id booléen est cassé.
    with pytest.raises(ParseError):
        parsing.parse_artist({'id': True, 'name': 'Y'})


def test_track_float_id_truncated():
    track = parsing.parse_track({'id': 3.0, 'title': 'T'})
    assert track.id == 3


def test_track_missing_title_raises():
    with pytest.raises(ParseError):
        parsing.parse_track({'id': 3})


def test_label_missing_name_raises():
    with pytest.raises(ParseError):
        parsing.parse_label({'id': 1})


# ── queue track ───────────────────────────────────────────────────────────────

def test_queue_track_full():
    track = parsing.parse_queue_track(QUEUE_TRACK)
    assert track.entry_id == 12
    assert track.album_title == 'Moon Safari'
    assert track.artist_name == 'Air'
    assert track.is_favorite


def test_queue_track_minimal():
    track = parsing.parse_queue_track({'id': 5, 'title': 'T'})
    assert track.entry_id is None
    assert track.album_id == 0
    assert track.album_title == ''
    assert track.cover_url is None


# ── playlists ─────────────────────────────────────────────────────────────────

def test_playlists_rows_under_data_key():
    rows = parsing.parse_playlists({'data': [
        {'id': 1, 'name': 'Route', 'track_count': 12,
         'total_duration_seconds': 2520},
    ]})
    assert len(rows) == 1
    assert rows[0].name == 'Route'
    assert rows[0].total_duration_seconds == 2520
    assert rows[0].cover_url is None


def test_playlists_empty():
    assert parsing.parse_playlists({'data': []}) == ()
    assert parsing.parse_playlists({}) == ()


def test_playlist_with_tracks_and_added():
    playlist = parsing.parse_playlist({
        'id': 1, 'name': 'Route', 'tracks': [QUEUE_TRACK], 'added': 2,
    })
    assert playlist.tracks[0].id == 71
    assert playlist.added == 2


def test_playlist_missing_name_raises():
    with pytest.raises(ParseError):
        parsing.parse_playlist({'id': 1})


# ── smart playlists ───────────────────────────────────────────────────────────

def test_smart_playlists():
    metas = parsing.parse_smart_playlists({'data': [
        {'key': 'dynamic_mix', 'track_count': 50},
        {'key': 'favorites'},
    ]})
    assert metas[0].key == 'dynamic_mix'
    assert metas[0].track_count == 50
    assert metas[1].track_count == 0


def test_smart_playlists_missing_key_raises():
    with pytest.raises(ParseError):
        parsing.parse_smart_playlists({'data': [{'track_count': 3}]})


def test_smart_playlist_with_tracks():
    playlist = parsing.parse_smart_playlist({
        'key': 'dynamic_mix', 'tracks': [QUEUE_TRACK],
    })
    assert playlist.key == 'dynamic_mix'
    assert len(playlist.tracks) == 1


def test_dynamic_mix_played():
    result = parsing.parse_dynamic_mix_played({
        'removed': True, 'tracks': [QUEUE_TRACK],
    })
    assert result.removed
    assert result.tracks[0].id == 71


def test_dynamic_mix_played_defaults():
    result = parsing.parse_dynamic_mix_played({})
    assert not result.removed
    assert result.tracks == ()


# ── recherche ─────────────────────────────────────────────────────────────────

def test_search_results_both_sections():
    results = parsing.parse_search_results({
        'albums': [FULL_ALBUM], 'tracks': [QUEUE_TRACK],
    })
    assert results.albums[0].title == 'Moon Safari'
    assert results.tracks[0].title == 'La femme d’argent'


def test_search_results_empty():
    results = parsing.parse_search_results({})
    assert results.albums == ()
    assert results.tracks == ()


# ── accueil ───────────────────────────────────────────────────────────────────

def test_home_recent_album_and_playlist():
    home = parsing.parse_home({
        'recent': [
            {'item_type': 'album', 'played_at': '2026-07-19T10:00:00Z',
             'album': FULL_ALBUM},
            {'item_type': 'playlist',
             'playlist': {'id': 1, 'name': 'Route', 'cover_url': '/covers/7.jpg'}},
        ],
        'suggestions': [FULL_ALBUM],
    })
    assert home.recent[0].album.id == 7
    assert home.recent[0].playlist is None
    assert home.recent[1].playlist.name == 'Route'
    assert home.recent[1].album is None
    assert home.suggestions[0].id == 7


def test_home_empty():
    home = parsing.parse_home({})
    assert home.recent == ()
    assert home.suggestions == ()


# ── pagination ────────────────────────────────────────────────────────────────

def test_albums_page_full():
    page = parsing.parse_albums_page({
        'data': [FULL_ALBUM],
        # totalPages en camelCase, fidèle au serveur (queries.js).
        'pagination': {'total': 90, 'page': 2, 'limit': 24, 'totalPages': 4},
    })
    assert page.data[0].id == 7
    assert page.pagination.total == 90
    assert page.pagination.page == 2
    assert page.pagination.total_pages == 4


def test_albums_page_without_pagination():
    page = parsing.parse_albums_page({'data': []})
    assert page.data == ()
    assert page.pagination.page == 1
    assert page.pagination.limit == 24
