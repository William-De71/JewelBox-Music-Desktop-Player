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


# ── normalize ─────────────────────────────────────────────────────────────────

def test_normalize_folds_case_and_accents():
    assert library.normalize('Motörhead') == 'motorhead'
    assert library.normalize('ÉLECTRIQUE') == 'electrique'


def test_normalize_collapses_whitespace():
    assert library.normalize('  My   Dying  Bride ') == 'my dying bride'


def test_normalize_handles_missing_text():
    assert library.normalize(None) == ''
    assert library.normalize('') == ''


# ── matches / filter_albums ───────────────────────────────────────────────────

class _FakeArtist:
    def __init__(self, name): self.name = name


class _FakeAlbum:
    def __init__(self, title, artist):
        self.title = title
        self.artist = _FakeArtist(artist)


COLLECTION = [
    _FakeAlbum('Turn Loose the Swans', 'My Dying Bride'),
    _FakeAlbum('Ride the Lightning', 'Metallica'),
    _FakeAlbum('Ace of Spades', 'Motörhead'),
    _FakeAlbum('Rheia', 'Oathbreaker'),
    _FakeAlbum('Archetype of Natural Violence', '7th Nemesis'),
]


def test_matches_is_case_and_accent_insensitive():
    assert library.matches(COLLECTION[2], 'motorhead')
    assert library.matches(COLLECTION[2], 'MOTÖRHEAD')


def test_matches_searches_title_and_artist():
    assert library.matches(COLLECTION[0], 'swans')      # titre
    assert library.matches(COLLECTION[0], 'dying')      # artiste


def test_matches_terms_are_unordered_and_independent():
    assert library.matches(COLLECTION[0], 'bride dying')
    assert library.matches(COLLECTION[0], 'swans bride')


def test_matches_requires_every_term():
    assert not library.matches(COLLECTION[0], 'dying metallica')


def test_matches_finds_substrings():
    # « metal » doit sortir Metallica, sinon le filtre rate la frappe partielle
    assert library.matches(COLLECTION[1], 'metal')


def test_empty_query_matches_everything():
    assert library.matches(COLLECTION[0], '')
    assert library.matches(COLLECTION[0], '   ')
    assert library.filter_albums(COLLECTION, '') == COLLECTION


def test_filter_albums_keeps_source_order():
    result = library.filter_albums(COLLECTION, 'e')
    assert result == [a for a in COLLECTION if a in result]


def test_filter_albums_can_return_nothing():
    assert library.filter_albums(COLLECTION, 'zzzz') == []


# ── initiales ─────────────────────────────────────────────────────────────────

def test_initials_start_with_other_then_a_to_z():
    assert library.INITIALS[0] == library.OTHER_INITIAL
    assert library.INITIALS[1] == 'A'
    assert library.INITIALS[-1] == 'Z'
    assert len(library.INITIALS) == 27


def test_initial_of_uses_the_artist_not_the_title():
    assert library.initial_of(COLLECTION[0]) == 'M'   # My Dying Bride
    assert library.initial_of(COLLECTION[3]) == 'O'   # Oathbreaker


def test_initial_of_strips_accents():
    assert library.initial_of(_FakeAlbum('x', 'Ötzi')) == 'O'


def test_initial_of_groups_digits_and_symbols():
    assert library.initial_of(COLLECTION[4]) == library.OTHER_INITIAL
    assert library.initial_of(_FakeAlbum('x', '!!!')) == library.OTHER_INITIAL


def test_initial_of_handles_a_missing_artist_name():
    assert library.initial_of(_FakeAlbum('x', '')) == library.OTHER_INITIAL


def test_available_initials_reports_what_is_present():
    assert library.available_initials(COLLECTION) == {'M', 'O', '#'}


def test_available_initials_of_nothing_is_empty():
    assert library.available_initials([]) == set()


def test_first_index_for_initial_finds_the_first_one():
    assert library.first_index_for_initial(COLLECTION, 'M') == 0
    assert library.first_index_for_initial(COLLECTION, 'O') == 3


def test_first_index_for_initial_returns_none_when_absent():
    assert library.first_index_for_initial(COLLECTION, 'Z') is None
    assert library.first_index_for_initial([], 'A') is None


# ── vue par artiste ───────────────────────────────────────────────────────────

def test_artist_entries_counts_albums_per_artist():
    albums = [
        _FakeAlbum('a', 'Opeth'),
        _FakeAlbum('b', 'Anathema'),
        _FakeAlbum('c', 'Opeth'),
    ]
    assert library.artist_entries(albums) == [('Anathema', 1), ('Opeth', 2)]


def test_artist_entries_are_sorted_ignoring_accents_and_case():
    albums = [_FakeAlbum('x', n) for n in ('Zebra', 'Ötzi', 'alpha')]
    assert [name for name, _ in library.artist_entries(albums)] == [
        'alpha', 'Ötzi', 'Zebra']


def test_artist_entries_groups_scattered_albums():
    # Le comptage ne dépend pas de l'ordre d'arrivée : un tri par année
    # disperse les albums d'un même artiste, ils doivent quand même compter.
    albums = [_FakeAlbum(t, 'Opeth') for t in 'ab'] + [_FakeAlbum('c', 'Korn')]
    albums.insert(1, _FakeAlbum('d', 'Korn'))
    assert dict(library.artist_entries(albums)) == {'Opeth': 2, 'Korn': 2}


def test_artist_entries_of_nothing_is_empty():
    assert library.artist_entries([]) == []


def test_artist_entries_keeps_a_missing_name():
    assert library.artist_entries([_FakeAlbum('a', '')]) == [('', 1)]


def test_filter_artist_entries_matches_like_the_grid():
    entries = [('My Dying Bride', 17), ('Motörhead', 2), ('Opeth', 12)]
    assert library.filter_artist_entries(entries, 'motorhead') == [
        ('Motörhead', 2)]
    assert library.filter_artist_entries(entries, 'bride dying') == [
        ('My Dying Bride', 17)]


def test_filter_artist_entries_without_query_returns_everything():
    entries = [('Opeth', 12)]
    assert library.filter_artist_entries(entries, '') == entries
    assert library.filter_artist_entries(entries, '  ') == entries


def test_filter_artist_entries_can_return_nothing():
    assert library.filter_artist_entries([('Opeth', 1)], 'zzz') == []


def test_albums_by_artist_selects_only_that_artist():
    albums = [
        _FakeAlbum('a', 'Opeth'),
        _FakeAlbum('b', 'Anathema'),
        _FakeAlbum('c', 'Opeth'),
    ]
    assert library.albums_by_artist(albums, 'Opeth') == [albums[0], albums[2]]


def test_albums_by_artist_keeps_source_order():
    albums = [_FakeAlbum(t, 'Opeth') for t in ('Orchid', 'Morningrise')]
    assert [a.title for a in library.albums_by_artist(albums, 'Opeth')] == [
        'Orchid', 'Morningrise']


def test_albums_by_artist_is_exact_not_fuzzy():
    # Deux orthographes restent deux artistes : c'est le serveur qui fait foi.
    albums = [_FakeAlbum('a', 'Opeth'), _FakeAlbum('b', 'opeth')]
    assert library.albums_by_artist(albums, 'Opeth') == [albums[0]]


def test_albums_by_artist_of_an_unknown_name_is_empty():
    assert library.albums_by_artist([_FakeAlbum('a', 'Opeth')], 'Korn') == []
