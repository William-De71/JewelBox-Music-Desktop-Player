"""File de lecture pure — navigation, lecture aléatoire/répétition, et
sérialisation pour la reprise au prochain lancement.

Miroir des responsabilités de PlayerConnection côté Android (méthodes
playAlbum/playQueue/next/previous/toggleShuffle/cycleRepeat) et de la forme
persistée SavedQueue/SavedTrack (data/PlaybackStateStore.kt), sans aucune
dépendance à GStreamer ou GTK — c'est le moteur playback qui pilote un
objet Queue, jamais l'inverse.

Le shuffle ne mélange pas la liste sous-jacente : il tire un ordre de
lecture séparé (comme ExoPlayer/Media3), pour que désactiver le mode
aléatoire retrouve l'ordre d'origine sans surprise.
"""

import random
from dataclasses import dataclass, field, replace
from enum import Enum


class RepeatMode(Enum):
    OFF = 'off'
    ALL = 'all'
    ONE = 'one'

    def next(self) -> 'RepeatMode':
        """OFF → ALL → ONE → OFF, le cycle habituel d'un lecteur."""
        order = (RepeatMode.OFF, RepeatMode.ALL, RepeatMode.ONE)
        return order[(order.index(self) + 1) % len(order)]


@dataclass(frozen=True)
class QueueItem:
    """Une piste telle que gardée dans la file — assez d'information pour
    rejouer l'affichage (mini-lecteur, sérialisation) sans redemander le
    serveur. Miroir de SavedTrack."""

    track_id: int
    title: str
    artist_name: str
    album_title: str
    cover_url: str | None = None
    is_favorite: bool = False
    stream_url: str = ''


@dataclass
class QueueState:
    """Résultat exposé à l'appelant après chaque mutation : de quoi mettre
    à jour l'UI et savoir quoi charger dans le moteur playback."""

    items: tuple[QueueItem, ...] = ()
    current_index: int | None = None
    shuffle: bool = False
    repeat: RepeatMode = RepeatMode.OFF

    @property
    def current(self) -> QueueItem | None:
        if self.current_index is None:
            return None
        return self.items[self.current_index]

    @property
    def has_item(self) -> bool:
        return self.current_index is not None


class Queue:
    """File de lecture : indices, navigation, shuffle/repeat.

    N'importe quoi qui touche au réseau ou à GStreamer reste hors de cette
    classe — playback/player.py l'utilise pour savoir quel morceau charger.
    """

    def __init__(self):
        self._items: list[QueueItem] = []
        self._order: list[int] = []      # ordre de lecture (index dans _items)
        self._position = 0                # position dans _order
        self._shuffle = False
        self._repeat = RepeatMode.OFF

    # ── Chargement ───────────────────────────────────────────────────────────

    def load(self, items, start_index: int = 0) -> QueueState:
        """Remplace la file. start_index se réfère à la liste donnée, dans
        son ordre d'origine (pas encore mélangée)."""
        self._items = list(items)
        self._rebuild_order(keep_current=False)
        if self._items:
            start_index = max(0, min(start_index, len(self._items) - 1))
            self._position = self._order.index(start_index) \
                if start_index in self._order else 0
        else:
            self._position = 0
        return self.state()

    def clear(self) -> QueueState:
        self._items = []
        self._order = []
        self._position = 0
        return self.state()

    # ── Navigation ───────────────────────────────────────────────────────────

    def has_next(self) -> bool:
        if not self._items:
            return False
        if self._repeat in (RepeatMode.ALL, RepeatMode.ONE):
            return True
        return self._position < len(self._order) - 1

    def has_previous(self) -> bool:
        return bool(self._items) and (
            self._repeat == RepeatMode.ALL or self._position > 0)

    def next(self) -> QueueState:
        """Piste suivante. En mode ONE, avance quand même (répéter la même
        piste après une fin naturelle est géré par le lecteur, pas ici)."""
        if not self._items:
            return self.state()
        if self._position < len(self._order) - 1:
            self._position += 1
        elif self._repeat == RepeatMode.ALL:
            self._position = 0
        return self.state()

    def previous(self) -> QueueState:
        if not self._items:
            return self.state()
        if self._position > 0:
            self._position -= 1
        elif self._repeat == RepeatMode.ALL:
            self._position = len(self._order) - 1
        return self.state()

    def peek_next(self) -> QueueItem | None:
        """La piste qui suivrait sur une fin naturelle, SANS avancer la file.

        Sert à l'enchaînement sans coupure : le moteur doit précharger l'URI
        exacte que track_ended() rendra ensuite courante. On raisonne donc
        dans l'ordre de LECTURE (_order) — pas l'ordre d'affichage — sinon,
        en mode aléatoire, l'audio préchargé et la piste affichée divergent.

        En mode ONE, la fin naturelle rejoue la même piste (comme
        track_ended()), donc on renvoie la piste courante."""
        if not self._items or not (0 <= self._position < len(self._order)):
            return None
        if self._repeat == RepeatMode.ONE:
            return self._items[self._order[self._position]]
        if self._position < len(self._order) - 1:
            return self._items[self._order[self._position + 1]]
        if self._repeat == RepeatMode.ALL:
            return self._items[self._order[0]]
        return None

    def track_ended(self) -> QueueState:
        """La piste courante est arrivée à sa fin naturelle (à distinguer
        d'un next() manuel : en mode ONE elle rejoue, en fin de file sans
        ALL la lecture s'arrête)."""
        if not self._items:
            return self.state()
        if self._repeat == RepeatMode.ONE:
            return self.state()
        if self._position < len(self._order) - 1:
            self._position += 1
        elif self._repeat == RepeatMode.ALL:
            self._position = 0
        else:
            self._position = len(self._order)  # au-delà : plus rien à jouer
        return self.state()

    def update_favorite(self, track_id: int, is_favorite: bool) -> QueueState:
        """Reflète un changement de favori (optimiste ou confirmé par le
        serveur) sur l'item concerné, où qu'il soit dans la file."""
        for i, item in enumerate(self._items):
            if item.track_id == track_id and item.is_favorite != is_favorite:
                self._items[i] = replace(item, is_favorite=is_favorite)
                break
        return self.state()

    def remove(self, track_id: int) -> QueueState:
        """Retire une piste (ex. retrait manuel du mix dynamique), même si
        c'est celle en cours — la position se recale sur la piste qui
        suivait, comme Media3 le fait en pareil cas."""
        try:
            item_index = next(
                i for i, item in enumerate(self._items)
                if item.track_id == track_id)
        except StopIteration:
            return self.state()

        current_item = (self._items[self._order[self._position]]
                        if self._position < len(self._order) else None)
        del self._items[item_index]
        self._rebuild_order(keep_current=False)
        if current_item is not None and current_item.track_id != track_id:
            # La piste qui jouait existe toujours (ce n'est pas elle qu'on
            # retire) : elle est forcément encore dans _items après le
            # rebuild, on retrouve sa nouvelle position.
            new_item_index = next(
                i for i, item in enumerate(self._items)
                if item.track_id == current_item.track_id)
            self._position = self._order.index(new_item_index)
        else:
            self._position = min(item_index, max(0, len(self._order) - 1))
        return self.state()

    # ── Shuffle / repeat ─────────────────────────────────────────────────────

    def set_shuffle(self, enabled: bool) -> QueueState:
        if enabled == self._shuffle:
            return self.state()
        self._shuffle = enabled
        self._rebuild_order(keep_current=True)
        return self.state()

    def cycle_repeat(self) -> QueueState:
        self._repeat = self._repeat.next()
        return self.state()

    def _rebuild_order(self, keep_current: bool):
        current_item_index = (
            self._order[self._position]
            if keep_current and self._position < len(self._order)
            else None)
        indices = list(range(len(self._items)))
        if self._shuffle:
            random.shuffle(indices)
            if current_item_index is not None and indices:
                indices.remove(current_item_index)
                indices.insert(0, current_item_index)
        self._order = indices
        if current_item_index is not None:
            self._position = self._order.index(current_item_index)
        else:
            self._position = 0

    # ── État / sérialisation ─────────────────────────────────────────────────

    def state(self) -> QueueState:
        current_index = (
            self._order[self._position]
            if self._items and 0 <= self._position < len(self._order)
            else None)
        return QueueState(
            items=tuple(self._items),
            current_index=current_index,
            shuffle=self._shuffle,
            repeat=self._repeat,
        )

    def to_saved(self, server_url: str, source_type=None, source_id=None,
                dynamic_mix: bool = False, position_ms: int = 0) -> dict:
        """Snapshot pour la reprise au prochain lancement (miroir SavedQueue).
        L'ordre sauvegardé est celui d'affichage d'origine (_items), l'index
        pointe sur la piste courante dans cet ordre — le shuffle repart
        neutre à la restauration, comme sur Android."""
        current = self.state().current
        index = 0
        if current is not None:
            index = next((i for i, item in enumerate(self._items)
                          if item.track_id == current.track_id), 0)
        return {
            'server_url': server_url,
            'tracks': [vars(item) for item in self._items],
            'index': index,
            'position_ms': position_ms,
            'source_type': source_type,
            'source_id': source_id,
            'dynamic_mix': dynamic_mix,
        }

    @classmethod
    def from_saved(cls, saved: dict) -> 'Queue':
        """Reconstruit une file depuis to_saved() ; le shuffle/repeat
        repartent à OFF (comme Android : seuls l'ordre et l'index survivent)."""
        queue = cls()
        items = [QueueItem(**track) for track in saved.get('tracks', [])]
        queue.load(items, start_index=saved.get('index', 0))
        return queue
