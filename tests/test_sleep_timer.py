import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))

from jewelbox.core.sleep_timer import (  # noqa: E402
    FADE_SECONDS, fade_factor, live_sleep_timer, mode_from_command)


def test_fade_stays_at_one_until_the_last_seconds():
    assert fade_factor(FADE_SECONDS + 1) == 1.0
    assert fade_factor(FADE_SECONDS) == 1.0
    assert fade_factor(FADE_SECONDS / 2) == 0.5
    assert fade_factor(-1) == 0.0


def test_mode_from_the_remote_command():
    assert mode_from_command({'minutes': 30}) == 30
    assert mode_from_command({'end_of_track': True, 'minutes': 5}) == 'track'
    assert mode_from_command({'minutes': 0}) is None
    assert mode_from_command({'minutes': True}) is None
    assert mode_from_command({'minutes': '30'}) is None
    assert mode_from_command({}) is None


def test_live_shape():
    assert live_sleep_timer(None, 0) is None
    assert live_sleep_timer('track', 0) == {'end_of_track': True}
    assert live_sleep_timer('timer', 0) == {'ends_at': '1970-01-01T00:00:00Z'}
