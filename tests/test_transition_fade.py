import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))

from jewelbox.core.queue import Queue, QueueItem  # noqa: E402
from jewelbox.core.transition_fade import fade_factor, follows_on_album  # noqa: E402


def _item(track_id, album_id=None, number=None):
    return QueueItem(track_id=track_id, title='', artist_name='', album_title='',
                     album_id=album_id, track_number=number)


def test_rises_at_the_start_and_falls_at_the_end():
    assert fade_factor(0, 200, 4) == 0
    assert fade_factor(2, 200, 4) == 0.5
    assert fade_factor(100, 200, 4) == 1
    assert fade_factor(199, 200, 4) == 0.25


def test_only_where_allowed_never_off_or_short():
    assert fade_factor(0, 200, 4, fade_in=False) == 1
    assert fade_factor(199, 200, 4, fade_out=False) == 1
    assert fade_factor(0, 200, 0) == 1
    assert fade_factor(6, 7, 4, fade_in=False) == 1


def test_follows_on_album():
    assert follows_on_album(_item(1, 5, 3), _item(2, 5, 4))
    assert not follows_on_album(_item(1, 5, 3), _item(2, 5, 5))
    assert not follows_on_album(_item(1, 5, 3), _item(2, 6, 4))
    assert not follows_on_album(_item(1), _item(2))
    assert not follows_on_album(None, _item(2, 5, 4))


def test_peek_previous_in_play_order():
    queue = Queue()
    items = [_item(1), _item(2), _item(3)]
    queue.load(items, 0)
    assert queue.peek_previous() is None
    queue.next()
    assert queue.peek_previous().track_id == 1
