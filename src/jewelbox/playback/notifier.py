"""Notification système au changement de piste (titre, artiste, pochette).

Seulement quand la fenêtre n'a pas le focus (masquée en arrière-plan, ou
une autre application au premier plan) : sous les yeux, le mini-lecteur
suffit. Désactivable dans les Préférences (clé notify-track-change). Une
notification unique (id fixe) est remplacée à chaque piste plutôt
qu'empilée ; un clic dessus ramène la fenêtre (action par défaut :
activation de l'application).

Code frontière (exclu de la couverture) : traduit PlaybackUiState en
Gio.Notification, rien de plus.
"""

from pathlib import Path

from gi.repository import Gio, GLib

from jewelbox.playback.mpris import cover_cache_path

NOTIFICATION_ID = 'now-playing'

# Laisse à MPRIS le temps de mettre la pochette en cache (téléchargée au
# changement de piste) avant de la joindre à la notification.
_DELAY_MS = 1200

# Côté de l'icône jointe : sous la limite de 512 px du portail, et bien
# assez pour la vignette d'une bulle de notification.
_ICON_SIZE = 256


class TrackNotifier:

    def __init__(self, app, playback):
        self._app = app
        self._playback = playback
        self._last_track_id = None
        self._pending_source = 0

    def start(self):
        self._playback.add_listener(self._on_playback_state)

    def stop(self):
        self._playback.remove_listener(self._on_playback_state)
        self._cancel_pending()

    def _on_playback_state(self, state):
        track_id = state.current_track_id if state.has_item else None
        if track_id == self._last_track_id:
            return
        # Premier état reçu (file restaurée au lancement, en pause) : ce
        # n'est pas un changement de piste, rien à annoncer.
        first = self._last_track_id is None and not state.is_playing
        self._last_track_id = track_id
        self._cancel_pending()
        if track_id is None or first:
            return
        self._pending_source = GLib.timeout_add(
            _DELAY_MS, self._notify, state)

    def _cancel_pending(self):
        if self._pending_source:
            GLib.source_remove(self._pending_source)
            self._pending_source = 0

    def _notify(self, state):
        self._pending_source = 0
        if not self._enabled() or self._window_focused():
            return GLib.SOURCE_REMOVE
        notification = Gio.Notification.new(state.title or '')
        body = ' — '.join(part for part in (state.artist, state.album) if part)
        if body:
            notification.set_body(body)
        icon = self._cover_icon(state.cover_url)
        if icon is not None:
            notification.set_icon(icon)
        self._app.send_notification(NOTIFICATION_ID, notification)
        return GLib.SOURCE_REMOVE

    def _enabled(self) -> bool:
        settings = self._app.settings
        return settings is None or settings.get_boolean('notify-track-change')

    def _window_focused(self) -> bool:
        window = self._app.props.active_window
        return (window is not None and window.get_visible()
                and window.is_active())

    @staticmethod
    def _cover_icon(cover_url):
        """Pochette réduite, en PNG, pour la notification — ou None.

        BytesIcon plutôt que FileIcon : le portail de notification (Flatpak)
        ne lit pas un fichier du bac à sable, il veut les octets. Et il valide
        l'icône : au-delà de 512×512 il rejette la notification ENTIÈRE (pas
        seulement l'icône) — or une pochette d'album dépasse souvent 1000 px.
        D'où la réduction systématique à _ICON_SIZE."""
        if not cover_url:
            return None
        path: Path = cover_cache_path(cover_url)
        if not path.exists():
            return None
        try:
            from gi.repository import GdkPixbuf
            pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                str(path), _ICON_SIZE, _ICON_SIZE, True)
            ok, data = pixbuf.save_to_bufferv('png', [], [])
        except (GLib.Error, ImportError, ValueError):
            return None
        if not ok:
            return None
        return Gio.BytesIcon.new(GLib.Bytes.new(data))
