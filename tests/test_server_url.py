import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))

from jewelbox.core.server_url import (  # noqa: E402
    api_url,
    normalize,
    resolve_url,
    stream_url,
)


# ── normalize ─────────────────────────────────────────────────────────────────

def test_normalize_adds_http_scheme():
    assert normalize('192.168.1.10:3001') == 'http://192.168.1.10:3001'


def test_normalize_keeps_https():
    assert normalize('https://music.local') == 'https://music.local'


def test_normalize_trims_whitespace():
    assert normalize('  192.168.1.10:3001  ') == 'http://192.168.1.10:3001'


def test_normalize_strips_trailing_slashes():
    assert normalize('http://music.local:3001///') == 'http://music.local:3001'


def test_normalize_keeps_hostname():
    assert normalize('mon-nas.home:3001') == 'http://mon-nas.home:3001'


def test_normalize_empty_raises():
    with pytest.raises(ValueError):
        normalize('')


def test_normalize_none_raises():
    with pytest.raises(ValueError):
        normalize(None)


def test_normalize_whitespace_only_raises():
    with pytest.raises(ValueError):
        normalize('   ')


def test_normalize_rejects_other_schemes():
    with pytest.raises(ValueError):
        normalize('ftp://music.local')


def test_normalize_rejects_missing_host():
    with pytest.raises(ValueError):
        normalize('http://')


# ── api_url ───────────────────────────────────────────────────────────────────

def test_api_url_joins_path():
    assert api_url('http://s:3001', '/api/health') == 'http://s:3001/api/health'


def test_api_url_tolerates_slash_mismatch():
    assert api_url('http://s:3001/', 'api/health') == 'http://s:3001/api/health'


def test_api_url_encodes_query():
    url = api_url('http://s:3001', '/api/player/search', {'q': 'aïr & co'})
    assert url == 'http://s:3001/api/player/search?q=a%C3%AFr+%26+co'


def test_api_url_bool_becomes_lowercase():
    url = api_url('http://s:3001', '/api/albums', {'wanted': False})
    assert url == 'http://s:3001/api/albums?wanted=false'


def test_api_url_none_values_omitted():
    url = api_url('http://s:3001', '/api/albums', {'page': 2, 'sort': None})
    assert url == 'http://s:3001/api/albums?page=2'


def test_api_url_all_none_gives_no_query():
    url = api_url('http://s:3001', '/api/albums', {'sort': None})
    assert url == 'http://s:3001/api/albums'


def test_api_url_empty_query_dict():
    assert api_url('http://s:3001', '/api/albums', {}) == 'http://s:3001/api/albums'


# ── resolve_url ───────────────────────────────────────────────────────────────

def test_resolve_url_relative_cover():
    assert resolve_url('http://s:3001', '/covers/42.jpg') == 'http://s:3001/covers/42.jpg'


def test_resolve_url_keeps_absolute():
    absolute = 'https://i.discogs.com/cover.jpg'
    assert resolve_url('http://s:3001', absolute) == absolute


def test_resolve_url_none_stays_none():
    assert resolve_url('http://s:3001', None) is None


def test_resolve_url_empty_stays_none():
    assert resolve_url('http://s:3001', '') is None


# ── stream_url ────────────────────────────────────────────────────────────────

def test_stream_url():
    assert stream_url('http://s:3001', 42) == 'http://s:3001/api/player/tracks/42/stream'
