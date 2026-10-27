import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))

from jewelbox.core.lyrics import (  # noqa: E402
    LyricLine, Lyrics, current_line_index, parse_lyrics)

LINES = (LyricLine(10.0, 'One'), LyricLine(20.0, 'Two'), LyricLine(30.0, ''))


def test_parse_synced_lines_sorted_bad_ones_skipped():
    lyrics = parse_lyrics({
        'source': 'lrclib', 'synced': True, 'plain': 'One\nTwo',
        'lines': [{'time': 20, 'text': 'Two'}, {'time': 10, 'text': 'One'}, {'text': 'no time'}, None],
    })
    assert lyrics.lines == (LyricLine(10.0, 'One'), LyricLine(20.0, 'Two'))
    assert lyrics.synced and lyrics.source == 'lrclib' and not lyrics.empty


def test_parse_plain_instrumental_and_nothing():
    assert parse_lyrics({'synced': False, 'plain': 'Words'}).plain == 'Words'
    assert parse_lyrics({'instrumental': True}).instrumental
    assert parse_lyrics({'synced': False, 'plain': '  '}).empty
    assert parse_lyrics(None) == Lyrics()
    assert parse_lyrics({'source': 3}).source is None


def test_current_line_a_little_ahead():
    assert current_line_index(LINES, 0) == -1
    assert current_line_index(LINES, 9.8) == 0
    assert current_line_index(LINES, 25) == 1
    assert current_line_index(LINES, 300) == 2
    assert current_line_index((), 5) == -1
