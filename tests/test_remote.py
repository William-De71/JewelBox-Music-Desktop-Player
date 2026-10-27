import sys
from dataclasses import dataclass, replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))

from jewelbox.core.remote import (  # noqa: E402
    HEARTBEAT, LiveReporter, LiveState, SseParser, live_state_of,
    reconnect_delay)


@dataclass(frozen=True)
class _UiState:
    """Les champs de PlaybackUiState dont live_state_of a besoin."""

    has_item: bool = True
    is_playing: bool = True
    current_track_id: int | None = 7
    queue_index: int | None = 2
    position_seconds: float = 12.0
    duration_seconds: float = 200.0
    volume: float = 0.5
    muted: bool = False
    shuffle: bool = False
    repeat: str = 'off'
    sleep_mode: str | None = None
    sleep_ends_at: float = 0.0


# ── SseParser ───────────────────────────────────────────────────────────────

def test_parser_reads_complete_events():
    parser = SseParser()
    assert parser.feed('retry: 3000\n\nevent: hello\ndata: {"device_id": "d"}\n\n') == [
        ('hello', {'device_id': 'd'})]


def test_parser_waits_for_the_end_of_an_event():
    parser = SseParser()
    assert parser.feed('event: command\ndata: {"command":') == []
    assert parser.feed(' "pause"}\n') == []
    assert parser.feed('\n') == [('command', {'command': 'pause'})]


def test_parser_ignores_comments_and_bad_data():
    parser = SseParser()
    assert parser.feed(': ping\n\nevent: command\ndata: nope\n\ndata: {"a": 1}\r\n\r\n') == [
        ('message', {'a': 1})]


# ── live_state_of ───────────────────────────────────────────────────────────

def test_live_state_of_a_playing_session():
    live = live_state_of(_UiState(repeat='all', shuffle=True))
    assert live == LiveState('playing', 7, 2, 12.0, 200.0, 50, False, True, 'all')
    assert live.payload('Bureau') == {
        'playback_state': 'playing', 'track_id': 7, 'current_index': 2,
        'position_sec': 12.0, 'duration_sec': 200.0, 'volume': 50,
        'muted': False, 'shuffle': True, 'repeat': 'all',
        'sleep_timer': None, 'device_label': 'Bureau'}


def test_live_state_carries_the_sleep_timer():
    assert live_state_of(_UiState(sleep_mode='track')).sleep_timer == {'end_of_track': True}
    timed = live_state_of(_UiState(sleep_mode='timer', sleep_ends_at=1793140200.4))
    assert timed.payload('x')['sleep_timer'] == {'ends_at': '2026-10-27T22:30:00Z'}


def test_a_sleep_timer_change_is_reported():
    reporter = LiveReporter()
    reporter.update(_live(), now=0)
    assert reporter.update(_live(sleep_timer={'end_of_track': True}), now=0.5)
    assert not reporter.update(_live(sleep_timer={'end_of_track': True}), now=1)


def test_live_state_of_an_empty_session():
    live = live_state_of(_UiState(has_item=False, is_playing=False,
                                  current_track_id=None, queue_index=None))
    assert live.playback_state == 'idle'
    assert live.current_index == -1
    assert 'track_id' not in live.payload('x')


def test_live_state_of_a_paused_session():
    assert live_state_of(_UiState(is_playing=False)).playback_state == 'paused'


# ── LiveReporter ────────────────────────────────────────────────────────────

def _live(**changes):
    return replace(live_state_of(_UiState()), **changes)


def test_first_state_is_reported():
    assert LiveReporter().update(_live(), now=0)


def test_playing_on_does_not_report():
    reporter = LiveReporter()
    reporter.update(_live(position_sec=10), now=0)
    assert not reporter.update(_live(position_sec=11), now=1)
    assert not reporter.update(_live(position_sec=15), now=5)


def test_a_change_the_controller_shows_is_reported():
    reporter = LiveReporter()
    reporter.update(_live(), now=0)
    assert reporter.update(_live(playback_state='paused'), now=1)
    assert reporter.update(_live(playback_state='paused', volume=80), now=2)
    assert reporter.update(_live(playback_state='paused', volume=80, track_id=8), now=3)
    assert not reporter.update(_live(playback_state='paused', volume=80, track_id=8), now=4)


def test_a_seek_is_reported():
    reporter = LiveReporter()
    reporter.update(_live(position_sec=10), now=0)
    assert reporter.update(_live(position_sec=60), now=1)


def test_a_seek_while_paused_is_reported():
    reporter = LiveReporter()
    reporter.update(_live(playback_state='paused', position_sec=10), now=0)
    assert not reporter.update(_live(playback_state='paused', position_sec=10), now=50)
    assert reporter.update(_live(playback_state='paused', position_sec=90), now=51)


def test_heartbeat_while_playing():
    reporter = LiveReporter()
    reporter.update(_live(position_sec=0), now=0)
    assert not reporter.update(_live(position_sec=HEARTBEAT - 1), now=HEARTBEAT - 1)
    assert reporter.update(_live(position_sec=HEARTBEAT), now=HEARTBEAT)


def test_forget_reports_again():
    reporter = LiveReporter()
    reporter.update(_live(), now=0)
    reporter.forget()
    assert reporter.update(_live(), now=1)


def test_reconnect_delay_grows_and_caps():
    assert [reconnect_delay(n) for n in (1, 2, 3, 6, 20)] == [2, 4, 8, 60, 60]
    assert reconnect_delay(0) == 2
