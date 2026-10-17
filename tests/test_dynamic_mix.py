import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))

from jewelbox.core.dynamic_mix import MixSyncPlan, plan  # noqa: E402


def test_played_track_removed_and_refill_appended():
    result = plan([1, 2, 3], [2, 3, 4])
    assert result == MixSyncPlan(remove_ids=(1,), append_ids=(4,))


def test_identical_lists_need_nothing():
    assert plan([1, 2], [1, 2]) == MixSyncPlan()


def test_append_keeps_server_order():
    assert plan([5], [5, 9, 7, 8]).append_ids == (9, 7, 8)


def test_remove_keeps_local_order():
    assert plan([3, 1, 2], [2]).remove_ids == (3, 1)


def test_empty_server_list_removes_everything():
    assert plan([1, 2], []) == MixSyncPlan(remove_ids=(1, 2))


def test_empty_local_queue_appends_everything():
    assert plan([], [1, 2]) == MixSyncPlan(append_ids=(1, 2))
