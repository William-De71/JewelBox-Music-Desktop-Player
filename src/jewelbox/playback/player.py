"""Moteur de lecture : playbin3 sur la boucle GLib.

Code frontière (exclu de la couverture, exercé à la main) : toute décision
« quelle piste ensuite » vit dans jewelbox.core.queue.Queue, pur et testé.
Ce module ne fait que piloter GStreamer et retraduire ses évènements en
callbacks Python simples — il ne connaît rien à la file, aux playlists ni
au réseau applicatif (juste une URI de flux HTTP par appel).

Seek HTTP par Range requests (souphttpsrc, utilisé par playbin3 pour
http(s)://) et enchaînement sans coupure entre pistes via le signal
standard "about-to-finish" : quand playbin3 approche la fin du flux
courant, on lui fournit tout de suite l'URI suivante.
"""

import gi

gi.require_version('Gst', '1.0')

from gi.repository import GLib, Gst  # noqa: E402

_POSITION_POLL_MS = 500


class Player:
    """Enveloppe playbin3 : play/pause/seek, position/durée, callbacks.

    Callbacks disponibles (assignables directement, une seule cible
    chacune — ce module n'a qu'un appelant, playback/session.py) :
      - on_position(position_seconds, duration_seconds)
      - on_track_ended()           piste jouée jusqu'au bout (EOS)
      - on_about_to_finish()       à appeler juste avant, pour enchaîner
      - on_error(message)          échec de lecture (réseau, format...)
      - on_state_changed(playing) bascule play/pause détectée par le bus
    """

    def __init__(self):
        Gst.init(None)
        self._playbin = Gst.ElementFactory.make('playbin3', 'jewelbox-player')
        if self._playbin is None:
            raise RuntimeError(
                "GStreamer : l'élément playbin3 est introuvable "
                '(paquet gstreamer-plugins-base manquant ?)')
        self._playbin.connect('about-to-finish', self._on_about_to_finish)

        bus = self._playbin.get_bus()
        bus.add_signal_watch()
        bus.connect('message', self._on_bus_message)

        self._uri: str | None = None
        self._is_playing = False
        self._poll_source_id: int | None = None

        self.on_position = None
        self.on_track_ended = None
        self.on_about_to_finish = None
        self.on_error = None
        self.on_state_changed = None

    # ── Commandes ────────────────────────────────────────────────────────────

    def load(self, uri: str, play: bool = True):
        """Charge une nouvelle URI, en remplaçant la lecture en cours."""
        self._uri = uri
        self._playbin.set_state(Gst.State.NULL)
        self._playbin.set_property('uri', uri)
        self._playbin.set_state(
            Gst.State.PLAYING if play else Gst.State.PAUSED)

    def set_next_uri(self, uri: str | None):
        """À appeler depuis on_about_to_finish : playbin3 enchaîne sans
        coupure. None laisse la lecture s'arrêter naturellement (EOS)."""
        if uri is not None:
            self._playbin.set_property('uri', uri)
            self._uri = uri

    def play(self):
        self._playbin.set_state(Gst.State.PLAYING)

    def pause(self):
        self._playbin.set_state(Gst.State.PAUSED)

    def stop(self):
        self._playbin.set_state(Gst.State.NULL)
        self._uri = None
        self._stop_position_polling()

    def seek(self, position_seconds: float):
        self._playbin.seek_simple(
            Gst.Format.TIME,
            Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT,
            int(position_seconds * Gst.SECOND))

    def set_volume(self, volume: float):
        """Volume linéaire 0.0–1.0 (propriété volume de playbin3)."""
        self._playbin.set_property('volume', max(0.0, min(1.0, volume)))

    def get_volume(self) -> float:
        return self._playbin.get_property('volume')

    @property
    def is_playing(self) -> bool:
        return self._is_playing

    def position(self) -> float:
        """Position courante en secondes (0.0 si indisponible)."""
        ok, position = self._playbin.query_position(Gst.Format.TIME)
        return position / Gst.SECOND if ok else 0.0

    def duration(self) -> float:
        """Durée totale en secondes (0.0 si pas encore connue)."""
        ok, duration = self._playbin.query_duration(Gst.Format.TIME)
        return duration / Gst.SECOND if ok else 0.0

    # ── Bus GStreamer ────────────────────────────────────────────────────────

    def _on_bus_message(self, _bus, message):
        if message.type == Gst.MessageType.EOS:
            self._stop_position_polling()
            if self.on_track_ended:
                self.on_track_ended()
        elif message.type == Gst.MessageType.ERROR:
            error, debug = message.parse_error()
            if self.on_error:
                self.on_error(f'{error.message} ({debug})' if debug
                              else error.message)
        elif (message.type == Gst.MessageType.STATE_CHANGED
              and message.src == self._playbin):
            _old, new, _pending = message.parse_state_changed()
            playing = new == Gst.State.PLAYING
            if playing != self._is_playing:
                self._is_playing = playing
                if playing:
                    self._start_position_polling()
                else:
                    self._stop_position_polling()
                if self.on_state_changed:
                    self.on_state_changed(playing)

    def _on_about_to_finish(self, _playbin):
        if self.on_about_to_finish:
            self.on_about_to_finish()

    # ── Position ─────────────────────────────────────────────────────────────

    def _start_position_polling(self):
        if self._poll_source_id is not None:
            return
        self._poll_source_id = GLib.timeout_add(
            _POSITION_POLL_MS, self._poll_position)

    def _stop_position_polling(self):
        if self._poll_source_id is not None:
            GLib.source_remove(self._poll_source_id)
            self._poll_source_id = None

    def _poll_position(self) -> bool:
        if self.on_position:
            self.on_position(self.position(), self.duration())
        return True  # continue le polling tant que ça joue
