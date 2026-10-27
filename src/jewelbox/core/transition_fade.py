"""Fondu entre les morceaux, sans chevauchement : la logique pure, testée.

Le son baisse sur les dernières secondes d'un morceau et remonte au début du
suivant — jamais entre deux pistes qui se suivent sur un même album (un
album live reste sans blanc). Même règle que la PWA et l'appli Android.
"""

TRANSITION_FADE_CHOICES = (0, 2, 4, 6, 8)


def follows_on_album(before, after) -> bool:
    """Deux entrées de file (QueueItem) qui se suivent sur leur album."""
    return (before is not None and after is not None
            and before.album_id is not None and before.album_id == after.album_id
            and before.track_number is not None
            and after.track_number == before.track_number + 1)


def fade_factor(position: float, duration: float, fade: int,
                fade_in: bool = True, fade_out: bool = True) -> float:
    """Multiplicateur de volume à `position` d'un morceau de `duration` (s)."""
    if not fade:
        return 1.0
    factor = 1.0
    if fade_in:
        factor = min(factor, max(0.0, position) / fade)
    if fade_out and duration > 2 * fade:
        factor = min(factor, max(0.0, duration - position) / fade)
    return min(1.0, max(0.0, factor))
