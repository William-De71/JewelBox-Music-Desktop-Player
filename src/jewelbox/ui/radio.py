"""Lancer une « radio » autour d'un album ou d'un artiste.

Le serveur (GET /api/player/radio, serveur >= 1.16) tire une file aléatoire :
un tiers de l'artiste, le reste d'autres artistes du même genre. La lecture
démarre aussitôt, nommée « Radio · <album ou artiste> » dans les lecteurs.

Code frontière (exclu de la couverture).
"""

import asyncio
from gettext import gettext as _

from jewelbox.api.client import ApiError


def start_radio(application, parent, album_id=None, artist_id=None):
    asyncio.get_event_loop_policy().get_event_loop().create_task(
        _start(application, parent, album_id, artist_id))


async def _start(application, parent, album_id, artist_id):
    client = application.get_client()
    playback = application.playback
    if client is None or playback is None:
        return
    root = parent.get_root() if parent is not None else None
    try:
        radio = await client.radio(album_id=album_id, artist_id=artist_id)
    except ApiError as error:
        if root is not None and hasattr(root, 'show_toast'):
            # 404 sur la route elle-même : serveur antérieur à la radio.
            root.show_toast(
                _('Radio indisponible : mettez le serveur à jour')
                if error.status == 404 else _('Radio indisponible'))
        return
    if not radio.tracks:
        return
    playback.play_queue_tracks(
        radio.tracks,
        source_name=_('Radio · {name}').format(name=radio.seed_name))
