"""Minuterie de sommeil : la logique pure, testée.

La lecture se met en pause au bout d'un délai, en baissant le son sur les
dernières secondes, ou à la fin de la piste en cours (sans fondu : la piste
se termine d'elle-même). Mêmes règles que la PWA (client/src/utils/
sleepTimer.js) et l'appli Android ; le contrôleur à distance (Gladys) la
pose avec l'ordre sleep_timer.
"""

from datetime import datetime, timezone

# Durée du fondu avant la pause (minuterie à délai).
FADE_SECONDS = 10.0


def fade_factor(remaining_seconds: float) -> float:
    """Multiplicateur de volume : 1, puis jusqu'à 0 pendant le fondu."""
    if remaining_seconds >= FADE_SECONDS:
        return 1.0
    return max(0.0, remaining_seconds / FADE_SECONDS)


def mode_from_command(command: dict):
    """Ordre sleep_timer → mode de PlaybackSession.set_sleep_timer :
    'track', un nombre de minutes, ou None (0 minute : annulation)."""
    if command.get('end_of_track') is True:
        return 'track'
    minutes = command.get('minutes')
    if isinstance(minutes, int) and not isinstance(minutes, bool) and minutes > 0:
        return minutes
    return None


def live_sleep_timer(mode, ends_at: float):
    """Ce que l'état en direct rapporte : None, {'end_of_track': True} ou
    {'ends_at': date ISO} (ends_at : heure murale, secondes epoch)."""
    if mode == 'track':
        return {'end_of_track': True}
    if mode == 'timer':
        moment = datetime.fromtimestamp(round(ends_at), tz=timezone.utc)
        return {'ends_at': moment.isoformat().replace('+00:00', 'Z')}
    return None
