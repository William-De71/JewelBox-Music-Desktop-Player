"""Comptabilité pure du scrobbling Last.fm — miroir de ScrobbleTracker
(client Android, playback/ScrobbleTracker.kt), lui-même fidèle aux règles de
la PWA (client/src/components/PlayerContext.jsx) :

- une piste ne scrobble que si elle dure au moins 30 secondes ;
- elle scrobble une fois la moitié effectivement écoutée, ou 4 minutes ;
- les sauts de position (seeks, |dt| >= 2s entre deux mesures) ne comptent
  pas comme de l'écoute ;
- chaque (re)démarrage d'une piste réarme un unique scrobble.

Aucune dépendance GTK/GStreamer : on branche piste_demarree() à chaque
changement de piste, puis tick() environ une fois par seconde avec la
position de lecture ; un résultat non None signifie « scrobbler
maintenant » (renvoyé exactement une fois par piste démarrée).
"""

import time
from dataclasses import dataclass

# Durée minimale (secondes) en dessous de laquelle une piste ne scrobble
# jamais, même entièrement écoutée.
MIN_DURATION_SECONDS = 30

# Seuil haut : au-delà de 4 minutes écoutées, on scrobble même si la piste
# est plus longue que 8 minutes (moitié non encore atteinte).
MAX_LISTENED_THRESHOLD_SECONDS = 240

# Un saut de position (seek) supérieur ou égal à ce delta ne compte pas
# comme du temps réellement écouté.
SEEK_JUMP_SECONDS = 2


@dataclass
class Scrobble:
    """Ce qu'il faut envoyer au serveur une fois le seuil franchi."""

    track_id: int
    started_at: int


class _State:
    def __init__(self, track_id: int, started_at: int):
        self.track_id = track_id
        self.started_at = started_at
        self.played = 0.0       # secondes réellement écoutées
        self.last_time = 0.0    # dernière position observée, en secondes
        self.fired = False


class ScrobbleTracker:
    """Un tracker par lecteur ; on le réutilise piste après piste."""

    def __init__(self):
        self._state: _State | None = None

    def track_started(self, track_id, now_epoch_seconds=None):
        """À appeler à chaque changement de piste ; None efface le suivi."""
        if track_id is None:
            self._state = None
            return
        if now_epoch_seconds is None:
            now_epoch_seconds = int(time.time())
        self._state = _State(track_id, now_epoch_seconds)

    def tick(self, position_seconds: float, duration_seconds: float):
        """Position/durée courantes (secondes). Renvoie le Scrobble à
        déclencher au franchissement du seuil, sinon None."""
        state = self._state
        if state is None:
            return None

        delta = position_seconds - state.last_time
        if 0 < delta < SEEK_JUMP_SECONDS:
            state.played += delta
        state.last_time = position_seconds

        if state.fired or duration_seconds < MIN_DURATION_SECONDS:
            return None
        if (state.played < duration_seconds / 2
                and state.played < MAX_LISTENED_THRESHOLD_SECONDS):
            return None

        state.fired = True
        return Scrobble(state.track_id, state.started_at)
