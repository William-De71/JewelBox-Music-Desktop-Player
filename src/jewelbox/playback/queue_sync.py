"""Publie la file de cet appareil sur le serveur, pour qu'un autre appareil
puisse la reprendre (et inversement, voir ui/resume_dialog.py).

Code frontière (exclu de la couverture) : les décisions — quand envoyer
quoi — vivent dans core.queue_sync.QueueSyncPlanner, pur et testé ; ici on
ne fait qu'écouter la session et exécuter les appels réseau, best-effort
(un serveur ancien sans ces routes, ou injoignable, n'empêche rien).
"""

import asyncio
import time
from dataclasses import replace

from gi.repository import GLib

from jewelbox.api.client import ApiError
from jewelbox.core.queue_sync import QueueSyncPlanner

# Un PUT complet est regroupé sur ce délai : choisir un album puis passer
# trois pistes ne doit faire qu'un appel, pas quatre.
_SAVE_DELAY_MS = 1500


def device_label() -> str:
    """Nom affiché aux autres appareils (« Reprendre depuis… »)."""
    host = GLib.get_host_name() or ''
    return f'JewelBox bureau · {host}' if host else 'JewelBox bureau'


class QueueSync:

    def __init__(self, app, playback):
        self._app = app
        self._playback = playback
        self._planner = QueueSyncPlanner()
        self._pending_save = None   # dernière SyncAction 'save' en attente
        self._save_source = 0

    def start(self):
        self._playback.add_listener(self._on_playback_state)

    def stop(self):
        self._playback.remove_listener(self._on_playback_state)
        self._cancel_save()

    def _on_playback_state(self, state):
        action = self._planner.update(
            [item.track_id for item in state.queue_items],
            state.queue_index, state.position_seconds, state.is_playing,
            now=time.monotonic())
        if action is None:
            return
        if action.kind == 'save':
            self._pending_save = action
            if not self._save_source:
                self._save_source = GLib.timeout_add(
                    _SAVE_DELAY_MS, self._on_save_due)
            return
        if action.kind == 'state' and self._pending_save is not None:
            # Un PUT part bientôt : il portera l'état le plus récent.
            self._pending_save = replace(
                self._pending_save, current_index=action.current_index,
                position_sec=action.position_sec)
            return
        if action.kind == 'clear':
            self._cancel_save()
        self._send(action)

    def _on_save_due(self):
        self._save_source = 0
        self._flush_save()
        return GLib.SOURCE_REMOVE

    def _flush_save(self):
        action, self._pending_save = self._pending_save, None
        if action is not None:
            self._send(action)

    def _cancel_save(self):
        if self._save_source:
            GLib.source_remove(self._save_source)
            self._save_source = 0
        self._pending_save = None

    def _send(self, action):
        client = self._app.get_client()
        if client is None:
            return
        asyncio.get_event_loop_policy().get_event_loop().create_task(
            self._call(client, action))

    @staticmethod
    async def _call(client, action):
        try:
            if action.kind == 'save':
                await client.save_queue(
                    action.track_ids, action.current_index,
                    action.position_sec, device_label())
            elif action.kind == 'state':
                await client.update_queue_state(
                    action.current_index, action.position_sec)
            else:
                await client.clear_queue()
        except ApiError:
            pass
