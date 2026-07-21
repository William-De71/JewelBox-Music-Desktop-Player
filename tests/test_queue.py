import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))

from jewelbox.core.queue import Queue, QueueItem, RepeatMode  # noqa: E402


def make_items(count):
    return [
        QueueItem(track_id=i, title=f'Piste {i}', artist_name='Air',
                 album_title='Moon Safari', stream_url=f'http://s/{i}')
        for i in range(1, count + 1)
    ]


# ── chargement / état de base ─────────────────────────────────────────────────

def test_empty_queue_has_no_current():
    queue = Queue()
    state = queue.state()
    assert state.current is None
    assert not state.has_item


def test_load_sets_current_to_start_index():
    queue = Queue()
    state = queue.load(make_items(3), start_index=1)
    assert state.current.track_id == 2


def test_load_clamps_out_of_range_start_index():
    queue = Queue()
    state = queue.load(make_items(3), start_index=99)
    assert state.current.track_id == 3


def test_load_empty_list_has_no_current():
    queue = Queue()
    state = queue.load([])
    assert state.current is None


def test_clear_empties_the_queue():
    queue = Queue()
    queue.load(make_items(3))
    state = queue.clear()
    assert state.items == ()
    assert state.current is None


# ── navigation ────────────────────────────────────────────────────────────────

def test_next_advances():
    queue = Queue()
    queue.load(make_items(3), start_index=0)
    state = queue.next()
    assert state.current.track_id == 2


def test_next_at_end_without_repeat_stays():
    queue = Queue()
    queue.load(make_items(2), start_index=1)
    state = queue.next()
    assert state.current.track_id == 2
    assert not queue.has_next()


def test_next_at_end_with_repeat_all_wraps():
    queue = Queue()
    queue.load(make_items(2), start_index=1)
    queue.cycle_repeat()  # OFF -> ALL
    state = queue.next()
    assert state.current.track_id == 1


def test_previous_goes_back():
    queue = Queue()
    queue.load(make_items(3), start_index=2)
    state = queue.previous()
    assert state.current.track_id == 2


def test_previous_at_start_without_repeat_stays():
    queue = Queue()
    queue.load(make_items(2), start_index=0)
    state = queue.previous()
    assert state.current.track_id == 1
    assert not queue.has_previous()


def test_previous_at_start_with_repeat_all_wraps():
    queue = Queue()
    queue.load(make_items(2), start_index=0)
    queue.cycle_repeat()  # ALL
    state = queue.previous()
    assert state.current.track_id == 2


def test_has_next_true_mid_queue():
    queue = Queue()
    queue.load(make_items(3), start_index=0)
    assert queue.has_next()


def test_has_next_false_at_last_item_no_repeat():
    queue = Queue()
    queue.load(make_items(3), start_index=2)
    assert not queue.has_next()


def test_has_next_true_at_last_item_with_repeat_one():
    queue = Queue()
    queue.load(make_items(3), start_index=2)
    queue.cycle_repeat()  # ALL
    queue.cycle_repeat()  # ONE
    assert queue.has_next()


def test_empty_queue_has_no_next_or_previous():
    queue = Queue()
    assert not queue.has_next()
    assert not queue.has_previous()


def test_next_on_empty_queue_is_a_no_op():
    queue = Queue()
    state = queue.next()
    assert state.current is None


def test_previous_on_empty_queue_is_a_no_op():
    queue = Queue()
    state = queue.previous()
    assert state.current is None


# ── fin naturelle d'une piste (track_ended) ───────────────────────────────────

def test_track_ended_advances_like_next():
    queue = Queue()
    queue.load(make_items(3), start_index=0)
    state = queue.track_ended()
    assert state.current.track_id == 2


def test_track_ended_in_repeat_one_stays_on_same_track():
    queue = Queue()
    queue.load(make_items(3), start_index=1)
    queue.cycle_repeat()  # ALL
    queue.cycle_repeat()  # ONE
    state = queue.track_ended()
    assert state.current.track_id == 2


def test_track_ended_at_last_item_no_repeat_stops():
    queue = Queue()
    queue.load(make_items(2), start_index=1)
    state = queue.track_ended()
    assert state.current is None
    assert not state.has_item


def test_track_ended_at_last_item_with_repeat_all_restarts():
    queue = Queue()
    queue.load(make_items(2), start_index=1)
    queue.cycle_repeat()  # ALL
    state = queue.track_ended()
    assert state.current.track_id == 1


def test_track_ended_on_empty_queue_is_a_no_op():
    queue = Queue()
    state = queue.track_ended()
    assert state.current is None


# ── repeat cycle ──────────────────────────────────────────────────────────────

def test_repeat_cycles_off_all_one_off():
    assert RepeatMode.OFF.next() == RepeatMode.ALL
    assert RepeatMode.ALL.next() == RepeatMode.ONE
    assert RepeatMode.ONE.next() == RepeatMode.OFF


def test_cycle_repeat_updates_queue_state():
    queue = Queue()
    queue.load(make_items(2))
    assert queue.cycle_repeat().repeat == RepeatMode.ALL
    assert queue.cycle_repeat().repeat == RepeatMode.ONE
    assert queue.cycle_repeat().repeat == RepeatMode.OFF


# ── shuffle ───────────────────────────────────────────────────────────────────

def test_shuffle_keeps_current_track_playing():
    queue = Queue()
    queue.load(make_items(20), start_index=5)
    current_before = queue.state().current.track_id
    state = queue.set_shuffle(True)
    assert state.current.track_id == current_before
    assert state.shuffle


def test_shuffle_contains_every_item_exactly_once():
    queue = Queue()
    queue.load(make_items(10))
    queue.set_shuffle(True)
    ids = {item.track_id for item in queue.state().items}
    assert ids == {i for i in range(1, 11)}
    assert len(queue.state().items) == 10


def test_disabling_shuffle_restores_original_order_and_track():
    queue = Queue()
    queue.load(make_items(10), start_index=3)
    queue.set_shuffle(True)
    current = queue.state().current.track_id
    state = queue.set_shuffle(False)
    assert not state.shuffle
    assert state.current.track_id == current
    assert [item.track_id for item in state.items] == list(range(1, 11))


def test_setting_shuffle_to_same_value_is_a_no_op():
    queue = Queue()
    queue.load(make_items(3))
    state = queue.set_shuffle(False)  # déjà False
    assert not state.shuffle


def test_enabling_shuffle_on_empty_queue_is_safe():
    queue = Queue()
    state = queue.set_shuffle(True)
    assert state.shuffle
    assert state.current is None


def test_shuffle_survives_current_track_removed_elsewhere():
    # keep_current=False (utilisé par remove()) alors que le shuffle est
    # déjà actif : _rebuild_order ne doit pas tenter de garder une piste
    # courante — c'est remove() qui recale la position après coup.
    queue = Queue()
    queue.load(make_items(5), start_index=0)
    queue.set_shuffle(True)
    state = queue.remove(queue.state().items[0].track_id
                         if queue.state().current.track_id != 1 else 2)
    assert state.shuffle
    assert len(state.items) == 4


# ── favori ────────────────────────────────────────────────────────────────────

def test_update_favorite_flips_the_flag():
    queue = Queue()
    queue.load(make_items(3))
    state = queue.update_favorite(2, True)
    assert next(i for i in state.items if i.track_id == 2).is_favorite


def test_update_favorite_to_same_value_is_a_no_op():
    queue = Queue()
    queue.load(make_items(3))
    before = queue.state().items
    state = queue.update_favorite(2, False)  # déjà False
    assert state.items == before


def test_update_favorite_unknown_track_is_a_no_op():
    queue = Queue()
    queue.load(make_items(3))
    state = queue.update_favorite(999, True)
    assert all(not i.is_favorite for i in state.items)


# ── retrait d'une piste ───────────────────────────────────────────────────────

def test_remove_other_track_keeps_current_playing():
    queue = Queue()
    queue.load(make_items(3), start_index=1)  # piste 2 en cours
    state = queue.remove(3)
    assert state.current.track_id == 2
    assert len(state.items) == 2


def test_remove_current_track_moves_to_the_one_that_followed():
    queue = Queue()
    queue.load(make_items(3), start_index=1)  # piste 2 en cours
    state = queue.remove(2)
    assert state.current.track_id == 3


def test_remove_last_current_track_falls_back_to_new_last():
    queue = Queue()
    queue.load(make_items(3), start_index=2)  # piste 3 (dernière) en cours
    state = queue.remove(3)
    assert state.current.track_id == 2


def test_remove_unknown_track_is_a_no_op():
    queue = Queue()
    queue.load(make_items(3), start_index=0)
    state = queue.remove(999)
    assert len(state.items) == 3
    assert state.current.track_id == 1


def test_remove_only_item_empties_queue():
    queue = Queue()
    queue.load(make_items(1))
    state = queue.remove(1)
    assert state.current is None
    assert state.items == ()


def test_remove_only_item_with_shuffle_enabled_empties_queue():
    # _rebuild_order en shuffle avec indices vides (dernier élément retiré) :
    # ne doit pas planter en tentant indices.remove() sur une liste vide.
    queue = Queue()
    queue.load(make_items(1))
    queue.set_shuffle(True)
    state = queue.remove(1)
    assert state.items == ()
    assert state.current is None


# ── sérialisation / reprise ────────────────────────────────────────────────────

def test_to_saved_roundtrip_preserves_order_and_index():
    queue = Queue()
    queue.load(make_items(4), start_index=2)
    saved = queue.to_saved('http://s:3001', source_type='album', source_id='7',
                           position_ms=15000)
    assert saved['index'] == 2
    assert saved['position_ms'] == 15000
    assert saved['source_type'] == 'album'
    assert saved['source_id'] == '7'
    assert len(saved['tracks']) == 4

    restored = Queue.from_saved(saved)
    assert restored.state().current.track_id == 3
    assert [i.track_id for i in restored.state().items] == [1, 2, 3, 4]


def test_to_saved_survives_shuffle_with_original_display_order():
    queue = Queue()
    queue.load(make_items(5), start_index=0)
    queue.set_shuffle(True)
    current_id = queue.state().current.track_id
    saved = queue.to_saved('http://s:3001')
    # L'ordre sauvegardé reste celui d'affichage (1..5), pas l'ordre mélangé.
    assert [t['track_id'] for t in saved['tracks']] == [1, 2, 3, 4, 5]
    assert saved['index'] == current_id - 1


def test_to_saved_on_empty_queue_gives_index_zero():
    queue = Queue()
    saved = queue.to_saved('http://s:3001')
    assert saved['tracks'] == []
    assert saved['index'] == 0


def test_from_saved_empty_tracks_gives_empty_queue():
    restored = Queue.from_saved({'tracks': [], 'index': 0})
    assert restored.state().current is None


def test_from_saved_starts_with_shuffle_and_repeat_off():
    queue = Queue()
    queue.load(make_items(3))
    queue.set_shuffle(True)
    queue.cycle_repeat()
    saved = queue.to_saved('http://s:3001')

    restored = Queue.from_saved(saved)
    state = restored.state()
    assert not state.shuffle
    assert state.repeat == RepeatMode.OFF
