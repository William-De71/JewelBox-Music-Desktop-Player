import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))

from jewelbox.core.scrobble import ScrobbleTracker  # noqa: E402


def test_track_started_defaults_to_current_time():
    tracker = ScrobbleTracker()
    before = int(time.time())
    tracker.track_started(1)
    result = None
    for position in range(0, 40):
        result = tracker.tick(float(position), 60.0)
        if result is not None:
            break
    assert result is not None
    assert result.started_at >= before


def test_no_track_started_gives_no_scrobble():
    tracker = ScrobbleTracker()
    assert tracker.tick(10.0, 200.0) is None


def test_track_started_with_none_clears_tracking():
    tracker = ScrobbleTracker()
    tracker.track_started(1, now_epoch_seconds=1000)
    tracker.track_started(None)
    assert tracker.tick(10.0, 200.0) is None


def test_short_track_never_scrobbles():
    tracker = ScrobbleTracker()
    tracker.track_started(1, now_epoch_seconds=1000)
    for position in range(0, 29):
        assert tracker.tick(float(position), 29.0) is None


def test_scrobbles_at_half_duration():
    tracker = ScrobbleTracker()
    tracker.track_started(42, now_epoch_seconds=1000)
    result = None
    for position in range(0, 61):
        result = tracker.tick(float(position), 120.0)
        if result is not None:
            break
    assert result is not None
    assert result.track_id == 42
    assert result.started_at == 1000


def test_scrobbles_at_four_minutes_for_long_track():
    tracker = ScrobbleTracker()
    tracker.track_started(1, now_epoch_seconds=500)
    result = None
    for position in range(0, 241):
        result = tracker.tick(float(position), 1000.0)  # moitié = 500s, jamais atteint sans le plafond
        if result is not None:
            break
    assert result is not None


def test_fires_exactly_once_per_track():
    tracker = ScrobbleTracker()
    tracker.track_started(1, now_epoch_seconds=1000)
    fired = [tracker.tick(float(p), 60.0) for p in range(0, 60)]
    assert sum(1 for r in fired if r is not None) == 1


def test_seek_jump_does_not_count_as_listening():
    tracker = ScrobbleTracker()
    tracker.track_started(1, now_epoch_seconds=1000)
    # Progression normale jusqu'à 10s, puis un saut de +50s (seek).
    for position in range(0, 11):
        tracker.tick(float(position), 100.0)
    result = tracker.tick(60.0, 100.0)
    assert result is None  # 10s jouées, seuil (50s) pas atteint


def test_paused_tick_zero_delta_does_not_count():
    tracker = ScrobbleTracker()
    tracker.track_started(1, now_epoch_seconds=1000)
    tracker.tick(5.0, 100.0)
    result = tracker.tick(5.0, 100.0)  # même position : en pause
    assert result is None


def test_backward_seek_does_not_count():
    tracker = ScrobbleTracker()
    tracker.track_started(1, now_epoch_seconds=1000)
    tracker.tick(20.0, 100.0)
    result = tracker.tick(5.0, 100.0)  # retour en arrière
    assert result is None


def test_restarting_track_rearms_scrobble():
    tracker = ScrobbleTracker()
    tracker.track_started(1, now_epoch_seconds=1000)
    for position in range(0, 40):
        tracker.tick(float(position), 60.0)  # scrobblé
    tracker.track_started(1, now_epoch_seconds=2000)  # replay
    result = None
    for position in range(0, 40):
        result = tracker.tick(float(position), 60.0)
        if result is not None:
            break
    assert result is not None
    assert result.started_at == 2000
