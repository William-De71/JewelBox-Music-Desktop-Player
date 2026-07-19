import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))

from jewelbox.core.formats import format_duration  # noqa: E402


def test_zero():
    assert format_duration(0) == '0:00'


def test_less_than_a_minute():
    assert format_duration(7) == '0:07'


def test_minutes_seconds():
    assert format_duration(245) == '4:05'


def test_exact_minute():
    assert format_duration(60) == '1:00'


def test_with_hours():
    assert format_duration(3725) == '1:02:05'


def test_float_truncated():
    assert format_duration(245.9) == '4:05'


def test_none_is_zero():
    assert format_duration(None) == '0:00'


def test_negative_is_zero():
    assert format_duration(-12) == '0:00'


def test_garbage_is_zero():
    assert format_duration('abc') == '0:00'
