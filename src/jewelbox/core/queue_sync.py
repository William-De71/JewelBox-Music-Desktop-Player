"""Synchronisation de la file avec le serveur : quand envoyer quoi.

Logique pure (testée) derrière playback/queue_sync.py, qui ne fait
qu'exécuter les décisions. Le serveur garde une file par appareil
(en-tête X-Device-Id) ; c'est ce qui permet de reprendre ailleurs ce qui
jouait ici. Protocole de la PWA (PlayerContext.jsx) :

- les pistes changent (nouvel album, ajout, retrait…) → PUT complet ;
- seule la piste courante change → PATCH de l'état ;
- en lecture, un PATCH de la position toutes les PROGRESS_INTERVAL
  secondes (assez pour reprendre au bon endroit, sans marteler SQLite) ;
- passage en pause → PATCH, pour figer la position exacte ;
- file vidée → DELETE.
"""

from dataclasses import dataclass
from datetime import datetime, timezone

PROGRESS_INTERVAL = 30.0
# Ancienneté maximale d'une file d'un autre appareil proposée à l'accueil.
RESUME_WINDOW = 24 * 3600


@dataclass(frozen=True)
class SyncAction:
    # 'save' (PUT pistes + état), 'state' (PATCH) ou 'clear' (DELETE).
    kind: str
    track_ids: tuple[int, ...] = ()
    current_index: int = -1
    position_sec: float = 0.0


class QueueSyncPlanner:

    def __init__(self):
        self._track_ids: tuple[int, ...] = ()
        self._index = -1
        self._playing = False
        self._last_push = 0.0

    def resend(self):
        """La prochaine mise à jour renverra la file entière, même si ses
        pistes n'ont pas changé (sa source, elle, a changé)."""
        self._track_ids = ()

    def update(self, track_ids, current_index, position_sec, playing,
               now) -> SyncAction | None:
        """Nouvel état de la file ; rend l'appel à faire, ou None."""
        track_ids = tuple(track_ids)
        index = -1 if current_index is None else current_index
        position = max(0.0, float(position_sec or 0.0))
        was_playing = self._playing
        self._playing = bool(playing)

        if not track_ids:
            had_queue = bool(self._track_ids)
            self._track_ids, self._index = (), -1
            return SyncAction('clear') if had_queue else None

        if track_ids != self._track_ids:
            self._track_ids, self._index = track_ids, index
            self._last_push = now
            return SyncAction('save', track_ids, index, position)

        paused_now = was_playing and not self._playing
        progress_due = (self._playing
                        and now - self._last_push >= PROGRESS_INTERVAL)
        if index != self._index or paused_now or progress_due:
            self._index = index
            self._last_push = now
            return SyncAction('state', current_index=index,
                              position_sec=position)
        return None


def resume_start(playable, current_index):
    """Où reprendre la file d'un autre appareil, une fois écartées les pistes
    sans fichier (la lecture ne garde que les jouables).

    playable : un booléen par piste de la file d'origine. Rend
    (indice parmi les jouables, même_piste), ou None si rien n'est jouable.
    même_piste dit si la position enregistrée s'applique : faux quand la
    piste courante d'origine n'est pas jouable ici (on repart alors du début
    de la suivante) ou quand l'indice est inconnu."""
    playable = list(playable)
    total = sum(1 for flag in playable if flag)
    if total == 0:
        return None
    if current_index is None or not 0 <= current_index < len(playable):
        return 0, False
    before = sum(1 for flag in playable[:current_index] if flag)
    if playable[current_index]:
        return before, True
    return min(before, total - 1), False


def parse_server_timestamp(value):
    """Horodatage SQLite datetime('now') (UTC, sans fuseau) → secondes
    epoch, ou None s'il est absent ou illisible."""
    if not value:
        return None
    try:
        moment = datetime.strptime(value.strip(), '%Y-%m-%d %H:%M:%S')
    except ValueError:
        return None
    return moment.replace(tzinfo=timezone.utc).timestamp()


def pick_resumable(devices, now, dismissed=(), own_updated_at=None):
    """L'autre appareil à proposer à l'accueil : le plus récemment mis à
    jour qui a encore des pistes, s'il a servi dans les RESUME_WINDOW
    dernières secondes (la file du mois dernier n'intéresse personne).
    Même règle que pickResumable côté Android.

    devices : des QueueDevice ; now : secondes epoch ; dismissed : couples
    (device_id, updated_at) écartés par l'utilisateur — un appareil écarté
    revient dès qu'il a rejoué quelque chose (updated_at change) ;
    own_updated_at : le updated_at de la file de CET appareil. Un appareil
    qui n'a rien joué depuis que celui-ci a servi n'est plus proposé : sa
    file a déjà été reprise ici (ou écoutée ici après lui), et une reprise
    ne doit pas revenir à chaque morceau ni après un redémarrage."""
    own_at = parse_server_timestamp(own_updated_at)
    best, best_at = None, None
    for device in devices:
        if device.track_count <= 0:
            continue
        if (device.device_id, device.updated_at) in dismissed:
            continue
        at = parse_server_timestamp(device.updated_at)
        if at is None or not 0 <= now - at <= RESUME_WINDOW:
            continue
        if own_at is not None and at <= own_at:
            continue
        if best_at is None or at > best_at:
            best, best_at = device, at
    return best
