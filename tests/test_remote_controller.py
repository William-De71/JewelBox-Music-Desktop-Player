import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))

from dataclasses import replace  # noqa: E402

from jewelbox.api.models import (  # noqa: E402
    DeviceQueue, QueueTrack, RemoteDevice, RemoteState)
from jewelbox.core.remote_controller import (  # noqa: E402
    apply_watch_event, controllable_others, device_icon_name, live_position,
    mute_command, next_repeat, play_index_command, play_media_command,
    repeat_command, seek_command, shuffle_command, simple_command,
    take_over_queue, volume_command)

DESK = {
    'device_id': 'desk', 'device_label': 'JewelBox bureau · fedora',
    'controllable': True, 'capabilities': ['seek', 'volume', 42],
    'state': {
        'playback_state': 'playing', 'position_sec': 12.5, 'volume': 80,
        'shuffle': False, 'repeat': 'all', 'extra': 1,
        'track': {'id': 7, 'title': 'Leave Me Out', 'artist_name': 'Silverchair'},
    },
}


# ── apply_watch_event ────────────────────────────────────────────────────────

def test_the_list_then_a_device_added_changed_and_gone():
    devices = apply_watch_event((), 'devices', {'data': [DESK]})
    assert len(devices) == 1
    desk = devices[0]
    assert desk.capabilities == ('seek', 'volume')
    assert desk.state.track.title == 'Leave Me Out'
    assert desk.state.volume == 80 and desk.state.repeat == 'all'

    devices = apply_watch_event(devices, 'device',
                                {'device_id': 'web', 'controllable': False})
    assert [d.device_id for d in devices] == ['desk', 'web']

    devices = apply_watch_event(devices, 'device', {
        'device_id': 'desk', 'controllable': True,
        'state': {'playback_state': 'paused'}})
    assert [d.device_id for d in devices] == ['desk', 'web']
    assert devices[0].state.playback_state == 'paused'
    assert devices[0].state.track is None

    devices = apply_watch_event(devices, 'gone', {'device_id': 'web'})
    assert [d.device_id for d in devices] == ['desk']


def test_unknown_events_and_bad_data_leave_the_list():
    devices = (RemoteDevice(device_id='desk'),)
    assert apply_watch_event(devices, 'hello', {'x': 1}) == devices
    assert apply_watch_event(devices, 'device', {'no_id': True}) == devices
    assert apply_watch_event(devices, 'devices', 'not a dict') == devices


def test_a_malformed_track_is_dropped_not_the_device():
    devices = apply_watch_event((), 'device', {
        'device_id': 'desk', 'state': {'playback_state': 'playing',
                                       'track': {'title': 'no id'}}})
    assert devices[0].state.track is None


def test_only_the_other_controllable_players():
    devices = (RemoteDevice('me', controllable=True),
               RemoteDevice('desk', controllable=True),
               RemoteDevice('web', controllable=False))
    assert [d.device_id for d in controllable_others(devices, 'me')] == ['desk']


# ── live_position ────────────────────────────────────────────────────────────

def test_the_position_moves_on_while_playing_up_to_the_end():
    reported = '2026-10-31T13:34:22.588Z'
    at = datetime.fromisoformat('2026-10-31T13:34:22.588+00:00').timestamp()
    playing = RemoteState(playback_state='playing', position_sec=60.0,
                          duration_sec=183.3, reported_at=reported)
    assert abs(live_position(playing, at + 10) - 70.0) < 0.001
    assert live_position(playing, at + 600) == 183.3
    # Horloge de ce poste en retard : jamais en arrière.
    assert live_position(playing, at - 5) == 60.0
    paused = RemoteState(playback_state='paused', position_sec=60.0,
                         reported_at=reported)
    assert live_position(paused, at + 10) == 60.0
    undated = RemoteState(playback_state='playing', position_sec=60.0,
                          reported_at='yesterday')
    assert live_position(undated, at + 10) == 60.0


# ── take_over_queue ──────────────────────────────────────────────────────────

def test_listen_here_starts_at_the_live_track_and_position():
    at = datetime.fromisoformat('2026-10-31T13:34:22.588+00:00').timestamp()
    queue = DeviceQueue(
        device_id='desk', current_index=0, position_sec=5.0,
        tracks=tuple(QueueTrack(id=i, title=f'T{i}', has_file=True)
                     for i in (7, 8, 9)))
    live = RemoteState(playback_state='playing', position_sec=60.0,
                       duration_sec=200.0, current_index=1, track_id=8,
                       reported_at='2026-10-31T13:34:22.588Z')
    taken = take_over_queue(queue, live, at + 10)
    assert taken.current_index == 1
    assert abs(taken.position_sec - 70.0) < 0.001
    assert taken.tracks == queue.tracks

    # Index en direct décalé (aléatoire là-bas), mais celui enregistré colle.
    saved = take_over_queue(
        queue, replace(live, current_index=2, track_id=7), at)
    assert saved.current_index == 0
    assert abs(saved.position_sec - 60.0) < 0.001

    # Piste inconnue de la file enregistrée, ou pas d'état : telle quelle.
    assert take_over_queue(queue, replace(live, track_id=42), at) == queue
    assert take_over_queue(queue, replace(live, track_id=None), at) == queue
    assert take_over_queue(queue, None, at) == queue


# ── Ordres ───────────────────────────────────────────────────────────────────

def test_repeat_cycles_off_all_one():
    assert next_repeat('off') == 'all'
    assert next_repeat('all') == 'one'
    assert next_repeat('one') == 'off'
    assert next_repeat(None) == 'all'


def test_command_bodies_match_the_server_validation():
    assert simple_command('toggle') == {'command': 'toggle'}
    assert seek_command(-3) == {'command': 'seek', 'position_sec': 0.0}
    assert volume_command(140.4) == {'command': 'volume', 'volume': 100}
    assert volume_command(42.6) == {'command': 'volume', 'volume': 43}
    assert mute_command(1) == {'command': 'mute', 'muted': True}
    assert shuffle_command(False) == {'command': 'shuffle', 'shuffle': False}
    assert repeat_command('one') == {'command': 'repeat', 'repeat': 'one'}
    assert play_index_command(4) == {'command': 'play_index', 'index': 4}
    assert play_media_command('album', 587) == {
        'command': 'play_media', 'mode': 'replace',
        'media': {'type': 'album', 'id': 587}}
    assert play_media_command('smart', 'dynamic_mix')['media'] == {
        'type': 'smart', 'id': 'dynamic_mix'}


def test_device_icons():
    assert device_icon_name('JewelBox Android · 2412DPC0AG') == 'phone-symbolic'
    assert device_icon_name('JewelBox bureau · fedora') == 'computer-symbolic'
    assert device_icon_name('JewelBox Windows · pc') == 'computer-symbolic'
    assert device_icon_name('Chrome') == 'web-browser-symbolic'
    assert device_icon_name(None) == 'web-browser-symbolic'
