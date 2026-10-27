"""Contrôle à distance (Gladys…) : la logique pure, testée.

Le serveur (>= 1.18) relaie les ordres d'un autre client vers ce lecteur
sur un flux Server-Sent Events (GET /api/player/remote/events) que le
lecteur garde ouvert tant que le réglage « Autoriser le contrôle à
distance » est actif. En retour, le lecteur rapporte son état en direct
(PATCH /api/player/queue/state avec playback_state…) à chaque changement,
réglage actif ou non : c'est ce qu'affiche le contrôleur.

playback/remote_control.py exécute ; ici on décide :

- SseParser découpe le flux en évènements ;
- LiveReporter dit quand rapporter l'état ;
- reconnect_delay espace les reconnexions.
"""

import json
from dataclasses import dataclass

from jewelbox.core.sleep_timer import live_sleep_timer

# Ce que ce lecteur sait faire au-delà de lecture / pause / suivant /
# précédent (liste du serveur, utils/remote.js).
CAPABILITIES = ('seek', 'volume', 'mute', 'shuffle', 'repeat', 'queue',
                'play_media', 'append', 'sleep_timer')

# En lecture, un rapport au moins toutes les HEARTBEAT secondes : sans flux
# ouvert, le serveur oublie un lecteur muet depuis 90 s.
HEARTBEAT = 30.0
# Écart de position (secondes) au-delà duquel c'est un saut, pas la lecture
# qui avance : même seuil que le SDK Gladys.
SEEK_TOLERANCE = 3.0


class SseParser:
    """Découpe un flux text/event-stream en couples (évènement, données).

    feed() reçoit des morceaux arbitraires (une ligne peut arriver en deux
    fois) et rend les évènements complets. Les commentaires (« : ping »),
    les lignes retry: et id: sont ignorés ; des données non JSON aussi."""

    def __init__(self):
        self._buffer = ''

    def feed(self, chunk: str):
        self._buffer += chunk.replace('\r\n', '\n')
        events = []
        while '\n\n' in self._buffer:
            block, self._buffer = self._buffer.split('\n\n', 1)
            event, data = 'message', []
            for line in block.split('\n'):
                if line.startswith(':'):
                    continue
                field, _, value = line.partition(':')
                value = value[1:] if value.startswith(' ') else value
                if field == 'event':
                    event = value
                elif field == 'data':
                    data.append(value)
            if not data:
                continue
            try:
                events.append((event, json.loads('\n'.join(data))))
            except ValueError:
                continue
        return events


@dataclass(frozen=True)
class LiveState:
    """L'état en direct tel que le serveur l'attend."""

    playback_state: str            # 'playing' | 'paused' | 'idle'
    track_id: int | None = None
    current_index: int = -1
    position_sec: float = 0.0
    duration_sec: float = 0.0
    volume: int = 100              # 0–100
    muted: bool = False
    shuffle: bool = False
    repeat: str = 'off'
    # None, {'end_of_track': True} ou {'ends_at': date ISO}.
    sleep_timer: dict | None = None

    def payload(self, device_label: str) -> dict:
        body = {
            'playback_state': self.playback_state,
            'current_index': self.current_index,
            'position_sec': round(self.position_sec, 1),
            'duration_sec': round(self.duration_sec, 1),
            'volume': self.volume,
            'muted': self.muted,
            'shuffle': self.shuffle,
            'repeat': self.repeat,
            'sleep_timer': self.sleep_timer,
            'device_label': device_label,
        }
        if self.track_id is not None:
            body['track_id'] = self.track_id
        return body


def live_state_of(state) -> LiveState:
    """PlaybackUiState (session) → LiveState."""
    if not state.has_item:
        playback = 'idle'
    else:
        playback = 'playing' if state.is_playing else 'paused'
    return LiveState(
        playback_state=playback,
        track_id=state.current_track_id,
        current_index=-1 if state.queue_index is None else state.queue_index,
        position_sec=max(0.0, state.position_seconds or 0.0),
        duration_sec=max(0.0, state.duration_seconds or 0.0),
        volume=round(max(0.0, min(1.0, state.volume)) * 100),
        muted=state.muted,
        shuffle=state.shuffle,
        repeat=state.repeat,
        sleep_timer=live_sleep_timer(state.sleep_mode, state.sleep_ends_at),
    )


class LiveReporter:
    """Quand rapporter l'état : à chaque changement de ce que le contrôleur
    affiche (lecture/pause, piste, volume, modes…), à un saut de position,
    et en lecture toutes les HEARTBEAT secondes. La session republie l'état
    plusieurs fois par seconde : tout le reste est ignoré."""

    def __init__(self):
        self._last: LiveState | None = None
        self._at = 0.0

    def forget(self):
        """Le prochain update() rapportera, quoi qu'il arrive (reconnexion)."""
        self._last = None

    def update(self, live: LiveState, now: float) -> bool:
        last, self._last = self._last, live
        if last is None:
            self._at = now
            return True
        position_less = {**live.__dict__, 'position_sec': 0, 'duration_sec': 0}
        last_position_less = {**last.__dict__, 'position_sec': 0, 'duration_sec': 0}
        changed = position_less != last_position_less
        if not changed and live.playback_state == 'playing':
            expected = last.position_sec + (now - self._at)
            changed = abs(live.position_sec - expected) > SEEK_TOLERANCE
            changed = changed or now - self._at >= HEARTBEAT
        elif not changed:
            changed = abs(live.position_sec - last.position_sec) > SEEK_TOLERANCE
        if changed:
            self._at = now
        else:
            # Pas de rapport : garder la référence d'extrapolation d'origine.
            self._last = last
        return changed


def reconnect_delay(attempt: int) -> float:
    """Secondes avant la tentative n° attempt (1, 2…) : 2, 4, 8… plafonné à
    60 s. Un serveur ancien (404) ou éteint ne doit pas être martelé."""
    return float(min(60, 2 ** max(1, attempt)))
