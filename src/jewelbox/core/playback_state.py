"""Persistance de la file de lecture entre deux lancements (logique pure).

Miroir de data/PlaybackStateStore.kt côté Android : un seul instantané JSON,
écrit à chaque changement de file et relu au démarrage pour reprendre la
musique là où elle s'était arrêtée.

Fichier dédié plutôt que GSettings : c'est de la comptabilité de lecture
bavarde (réécrite à chaque piste, chaque pause), potentiellement plusieurs
centaines de pistes — dconf est fait pour des réglages stables, pas pour ça.
Android sépare pour la même raison ce store de ses ServerPrefs.

Le chemin est injecté par l'appelant (GLib.get_user_data_dir() côté app) :
ce module reste testable sans gi ni environnement de bureau.
"""

import json
import os
import tempfile
from pathlib import Path

FILENAME = 'playback-state.json'


class PlaybackStateStore:
    """Lecture/écriture de l'instantané. Toute erreur d'E/S ou de format est
    avalée : une reprise ratée ne doit jamais empêcher l'app de démarrer, ni
    une écriture ratée interrompre la lecture en cours."""

    def __init__(self, directory):
        self._path = Path(directory) / FILENAME

    @property
    def path(self) -> Path:
        return self._path

    def save(self, snapshot: dict) -> bool:
        """Écrit l'instantané. Une file vide efface le fichier plutôt que de
        garder un état sans piste (parité avec le clear() d'Android).
        Renvoie True si l'état sur disque reflète bien la demande."""
        if not snapshot.get('tracks'):
            return self.clear()
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            # Écriture atomique : un arrêt brutal en plein enregistrement ne
            # doit pas laisser un JSON tronqué qui ferait perdre la file.
            handle, temporary = tempfile.mkstemp(
                dir=self._path.parent, prefix=f'.{FILENAME}.')
            try:
                with os.fdopen(handle, 'w', encoding='utf-8') as stream:
                    json.dump(snapshot, stream)
                os.replace(temporary, self._path)
            except OSError:
                # Le fichier temporaire n'a pas pu remplacer la cible : le
                # laisser traîner polluerait le dossier de données.
                Path(temporary).unlink(missing_ok=True)
                raise
        except (OSError, TypeError, ValueError):
            return False
        return True

    def load(self) -> dict | None:
        """L'instantané du dernier lancement, ou None s'il n'y en a pas, s'il
        est illisible, ou si sa forme n'est plus celle attendue."""
        try:
            with self._path.open(encoding='utf-8') as stream:
                snapshot = json.load(stream)
        except (OSError, ValueError):
            return None
        if not isinstance(snapshot, dict):
            return None
        tracks = snapshot.get('tracks')
        if not isinstance(tracks, list) or not tracks:
            return None
        return snapshot

    def clear(self) -> bool:
        try:
            self._path.unlink(missing_ok=True)
        except OSError:
            return False
        return True
