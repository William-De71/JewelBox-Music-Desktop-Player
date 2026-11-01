"""Télécommande des autres lecteurs (téléphone, autre bureau, PWA) : la
logique pure, testée.

L'autre moitié de core.remote, où c'est CE lecteur qui est piloté. Ici il
pilote : la liste des lecteurs arrive par GET /api/player/remote/watch
(toute la liste, puis un évènement par changement), les ordres partent par
POST /api/player/remote/devices/:id/commands. Même protocole que
RemoteControllerLogic.kt côté Android ; serveur : utils/remote.js.

ui/remote.py affiche et envoie ; ici on décide :

- apply_watch_event tient la liste à jour ;
- controllable_others dit qui peut être piloté ;
- live_position fait avancer la position entre deux rapports ;
- take_over_queue cale la file reprise par « Écouter ici » sur le direct ;
- les fonctions *_command construisent les corps d'ordres.
"""

from dataclasses import replace
from datetime import datetime

from jewelbox.api import parsing


def apply_watch_event(devices, event: str, data):
    """La liste (tuple de RemoteDevice) après un évènement du flux :
    « devices » (toute la liste, envoyée d'abord), « device » (un lecteur
    ajouté ou changé), « gone » (un lecteur parti). Tout autre évènement, ou
    des données illisibles, laisse la liste telle quelle."""
    try:
        if event == 'devices':
            return parsing.parse_remote_devices(data)
        if event == 'device':
            device = parsing.parse_remote_device(data)
            ids = [d.device_id for d in devices]
            if device.device_id in ids:
                index = ids.index(device.device_id)
                return devices[:index] + (device,) + devices[index + 1:]
            return tuple(devices) + (device,)
        if event == 'gone' and isinstance(data, dict):
            gone = data.get('device_id')
            return tuple(d for d in devices if d.device_id != gone)
    except parsing.ParseError:
        pass
    return tuple(devices)


def controllable_others(devices, own_device_id):
    """Les lecteurs que celui-ci peut piloter : les autres, flux d'ordres ouvert."""
    return tuple(d for d in devices
                 if d.controllable and d.device_id != own_device_id)


def _iso_seconds(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
    except ValueError:
        return None


def live_position(state, now: float) -> float:
    """Où en est le lecteur, en secondes : la position rapportée, avancée
    du temps écoulé depuis le rapport s'il joue (il ne rapporte alors que
    toutes les 30 s), jamais au-delà de la fin de la piste ni en arrière
    (horloge de ce poste en retard sur celle du serveur).

    state : RemoteState ; now : secondes epoch."""
    if state.playback_state != 'playing':
        return state.position_sec
    reported = _iso_seconds(state.reported_at)
    if reported is None:
        return state.position_sec
    position = state.position_sec + max(0.0, now - reported)
    if state.duration_sec > 0:
        position = min(position, state.duration_sec)
    return position


def take_over_queue(queue, state, now: float):
    """« Écouter ici » : la file que ce lecteur a laissée sur le serveur,
    calée sur où il en est maintenant. Sa piste et sa position en direct sont
    plus fraîches que celles enregistrées (toutes les 30 s environ en
    lecture) ; on garde ces dernières si le direct ne correspond pas à la
    file (un changement pas encore enregistré).

    queue : DeviceQueue ; state : RemoteState ou None ; now : secondes epoch."""
    if state is None or state.track_id is None:
        return queue

    def track_id_at(index):
        if 0 <= index < len(queue.tracks):
            return queue.tracks[index].id
        return None

    if track_id_at(state.current_index) == state.track_id:
        index = state.current_index
    elif track_id_at(queue.current_index) == state.track_id:
        index = queue.current_index
    else:
        return queue
    return replace(queue, current_index=index,
                   position_sec=live_position(state, now))


def next_repeat(mode) -> str:
    """Le mode de répétition suivant au clic : off → all → one → off."""
    return {'off': 'all', None: 'all', 'all': 'one'}.get(mode, 'off')


def device_icon_name(label) -> str:
    """Icône d'après le nom que le lecteur s'est donné (« JewelBox Android ·
    modèle », « JewelBox bureau · hôte », un navigateur…)."""
    text = (label or '').lower()
    if 'android' in text:
        return 'phone-symbolic'
    if 'bureau' in text or 'desktop' in text or 'windows' in text:
        return 'computer-symbolic'
    return 'web-browser-symbolic'


# ── Corps des ordres ─────────────────────────────────────────────────────────

def simple_command(name: str) -> dict:
    """play, pause, toggle, next, previous : compris de tous les lecteurs."""
    return {'command': name}


def seek_command(position_sec: float) -> dict:
    return {'command': 'seek', 'position_sec': max(0.0, float(position_sec))}


def volume_command(volume) -> dict:
    return {'command': 'volume', 'volume': max(0, min(100, round(volume)))}


def mute_command(muted: bool) -> dict:
    return {'command': 'mute', 'muted': bool(muted)}


def shuffle_command(shuffle: bool) -> dict:
    return {'command': 'shuffle', 'shuffle': bool(shuffle)}


def repeat_command(mode: str) -> dict:
    return {'command': 'repeat', 'repeat': mode}


def play_index_command(index: int) -> dict:
    return {'command': 'play_index', 'index': int(index)}


def play_media_command(kind: str, ref) -> dict:
    """kind : 'album' | 'playlist' (ref entier) ou 'smart' (ref = clé)."""
    return {'command': 'play_media', 'mode': 'replace',
            'media': {'type': kind, 'id': ref}}
