import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))

from jewelbox.core.queue_sync import (  # noqa: E402
    PROGRESS_INTERVAL, QueueSyncPlanner, SyncAction)


def test_new_queue_is_saved_whole():
    planner = QueueSyncPlanner()
    action = planner.update([1, 2, 3], 1, 12.5, True, now=0)
    assert action == SyncAction('save', (1, 2, 3), 1, 12.5)


def test_nothing_new_sends_nothing():
    planner = QueueSyncPlanner()
    planner.update([1, 2], 0, 0, True, now=0)
    assert planner.update([1, 2], 0, 5, True, now=5) is None


def test_track_change_sends_state():
    planner = QueueSyncPlanner()
    planner.update([1, 2], 0, 0, True, now=0)
    assert planner.update([1, 2], 1, 0.4, True, now=3) == SyncAction(
        'state', current_index=1, position_sec=0.4)


def test_progress_is_pinged_periodically_while_playing():
    planner = QueueSyncPlanner()
    planner.update([1], 0, 0, True, now=0)
    assert planner.update([1], 0, 10, True, now=PROGRESS_INTERVAL - 1) is None
    action = planner.update([1], 0, 31, True, now=PROGRESS_INTERVAL + 1)
    assert action == SyncAction('state', current_index=0, position_sec=31)
    # Et pas de nouveau ping juste après.
    assert planner.update([1], 0, 32, True, now=PROGRESS_INTERVAL + 2) is None


def test_no_periodic_ping_while_paused():
    planner = QueueSyncPlanner()
    planner.update([1], 0, 0, False, now=0)
    assert planner.update([1], 0, 0, False, now=10 * PROGRESS_INTERVAL) is None


def test_pause_freezes_the_position():
    planner = QueueSyncPlanner()
    planner.update([1], 0, 0, True, now=0)
    assert planner.update([1], 0, 7.25, False, now=4) == SyncAction(
        'state', current_index=0, position_sec=7.25)


def test_changed_tracks_save_again():
    planner = QueueSyncPlanner()
    planner.update([1, 2], 0, 0, True, now=0)
    assert planner.update([1, 2, 9], 0, 3, True, now=1).kind == 'save'


def test_emptied_queue_is_cleared_once():
    planner = QueueSyncPlanner()
    planner.update([1], 0, 0, True, now=0)
    assert planner.update([], None, 0, False, now=1) == SyncAction('clear')
    assert planner.update([], None, 0, False, now=2) is None


def test_empty_from_the_start_sends_nothing():
    assert QueueSyncPlanner().update([], None, 0, False, now=0) is None


def test_exhausted_queue_reports_no_current_and_clamps_position():
    planner = QueueSyncPlanner()
    planner.update([1], 0, 0, True, now=0)
    assert planner.update([1], None, -3, False, now=1) == SyncAction(
        'state', current_index=-1, position_sec=0.0)


from jewelbox.core.queue_sync import resume_start  # noqa: E402


def test_resume_at_the_same_playable_track():
    assert resume_start([True, False, True, True], 2) == (1, True)


def test_resume_skips_an_unplayable_current_track():
    assert resume_start([True, False, True], 1) == (1, False)


def test_resume_unplayable_last_track_falls_back_to_last_playable():
    assert resume_start([True, True, False], 2) == (1, False)


def test_resume_unknown_index_starts_at_the_top():
    assert resume_start([True, True], -1) == (0, False)
    assert resume_start([True, True], 9) == (0, False)
    assert resume_start([True], None) == (0, False)


def test_resume_nothing_playable():
    assert resume_start([False, False], 0) is None
    assert resume_start([], 0) is None
