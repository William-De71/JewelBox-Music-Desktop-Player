import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))

from jewelbox.core import library  # noqa: E402


def test_fetch_limit_covers_a_personal_collection():
    assert library.FETCH_LIMIT >= 10000


# ── sort_params ───────────────────────────────────────────────────────────────

def test_sort_artist_is_ascending():
    assert library.sort_params('artist') == {'sort': 'artist', 'order': 'asc'}


def test_sort_year_is_newest_first():
    assert library.sort_params('year') == {'sort': 'year', 'order': 'desc'}


def test_unknown_sort_falls_back_to_artist():
    assert library.sort_params('n-importe-quoi') == {'sort': 'artist', 'order': 'asc'}


def test_empty_sort_falls_back_to_artist():
    assert library.sort_params('') == {'sort': 'artist', 'order': 'asc'}


# ── positions du menu déroulant ───────────────────────────────────────────────

def test_positions_roundtrip():
    for position, key in enumerate(library.SORTS):
        assert library.sort_position(key) == position
        assert library.sort_from_position(position) == key


def test_unknown_key_gives_position_zero():
    assert library.sort_position('inconnu') == 0


def test_out_of_range_positions_give_default_sort():
    assert library.sort_from_position(-1) == 'artist'
    assert library.sort_from_position(99) == 'artist'
