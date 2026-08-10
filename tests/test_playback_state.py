import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))

from jewelbox.core.playback_state import PlaybackStateStore  # noqa: E402
from jewelbox.core.queue import Queue, QueueItem, RepeatMode  # noqa: E402


def make_snapshot(count=2, index=0, position_ms=0, server='http://s:3001'):
    return {
        'server_url': server,
        'tracks': [
            {'track_id': i, 'title': f'T{i}', 'artist_name': 'A',
             'album_title': 'Alb', 'cover_url': None, 'is_favorite': False,
             'stream_url': f'{server}/stream/{i}'}
            for i in range(1, count + 1)
        ],
        'index': index,
        'position_ms': position_ms,
        'source_type': 'album',
        'source_id': '7',
        'dynamic_mix': False,
    }


# ── aller-retour ───────────────────────────────────────────────────────────────

def test_save_then_load_returns_the_snapshot(tmp_path):
    store = PlaybackStateStore(tmp_path)
    snapshot = make_snapshot(position_ms=42000)
    assert store.save(snapshot)
    assert store.load() == snapshot


def test_load_without_any_save_returns_none(tmp_path):
    assert PlaybackStateStore(tmp_path).load() is None


def test_save_creates_the_directory_if_missing(tmp_path):
    store = PlaybackStateStore(tmp_path / 'nested' / 'data')
    assert store.save(make_snapshot())
    assert store.load() is not None


def test_second_save_replaces_the_first(tmp_path):
    store = PlaybackStateStore(tmp_path)
    store.save(make_snapshot(position_ms=1000))
    store.save(make_snapshot(position_ms=9000))
    assert store.load()['position_ms'] == 9000


def test_save_leaves_no_temporary_file_behind(tmp_path):
    store = PlaybackStateStore(tmp_path)
    store.save(make_snapshot())
    assert [p.name for p in tmp_path.iterdir()] == [store.path.name]


# ── file vide et effacement ────────────────────────────────────────────────────

def test_saving_an_empty_queue_clears_the_file(tmp_path):
    store = PlaybackStateStore(tmp_path)
    store.save(make_snapshot())
    assert store.save({'server_url': 'http://s:3001', 'tracks': []})
    assert not store.path.exists()
    assert store.load() is None


def test_clear_removes_the_file(tmp_path):
    store = PlaybackStateStore(tmp_path)
    store.save(make_snapshot())
    assert store.clear()
    assert store.load() is None


def test_clear_without_a_file_is_not_an_error(tmp_path):
    assert PlaybackStateStore(tmp_path).clear()


# ── robustesse ─────────────────────────────────────────────────────────────────

def test_corrupted_json_loads_as_none(tmp_path):
    store = PlaybackStateStore(tmp_path)
    store.path.write_text('{ this is not json', encoding='utf-8')
    assert store.load() is None


def test_json_that_is_not_an_object_loads_as_none(tmp_path):
    store = PlaybackStateStore(tmp_path)
    store.path.write_text('[1, 2, 3]', encoding='utf-8')
    assert store.load() is None


def test_snapshot_without_tracks_loads_as_none(tmp_path):
    store = PlaybackStateStore(tmp_path)
    store.path.write_text(json.dumps({'server_url': 'x'}), encoding='utf-8')
    assert store.load() is None


def test_snapshot_with_empty_tracks_loads_as_none(tmp_path):
    store = PlaybackStateStore(tmp_path)
    store.path.write_text(json.dumps({'tracks': []}), encoding='utf-8')
    assert store.load() is None


def test_snapshot_with_tracks_not_a_list_loads_as_none(tmp_path):
    store = PlaybackStateStore(tmp_path)
    store.path.write_text(json.dumps({'tracks': 'nope'}), encoding='utf-8')
    assert store.load() is None


def test_unserializable_snapshot_is_reported_as_failure(tmp_path):
    store = PlaybackStateStore(tmp_path)
    assert not store.save({'server_url': 'x', 'tracks': [{'bad': object()}]})


def test_directory_in_place_of_the_file_fails_gracefully(tmp_path):
    store = PlaybackStateStore(tmp_path)
    store.path.mkdir()
    assert not store.save(make_snapshot())
    assert store.load() is None
    assert not store.clear()


def test_unwritable_directory_fails_gracefully(tmp_path):
    target = tmp_path / 'readonly'
    target.mkdir(mode=0o500)
    try:
        assert not PlaybackStateStore(target).save(make_snapshot())
    finally:
        target.chmod(0o700)


# ── reprise complète : file → disque → file ────────────────────────────────────

def test_queue_survives_a_full_round_trip_through_the_store(tmp_path):
    store = PlaybackStateStore(tmp_path)
    queue = Queue()
    queue.load([QueueItem(track_id=i, title=f'T{i}', artist_name='A',
                          album_title='Alb', stream_url=f'http://s/{i}')
                for i in range(1, 4)], start_index=1)
    store.save(queue.to_saved('http://s:3001', source_type='playlist',
                              source_id='3', position_ms=12000))

    saved = store.load()
    restored = Queue.from_saved(saved)
    assert restored.state().current.track_id == 2
    assert [i.track_id for i in restored.state().items] == [1, 2, 3]
    assert saved['position_ms'] == 12000
    assert saved['source_type'] == 'playlist'
    assert restored.state().repeat == RepeatMode.OFF
