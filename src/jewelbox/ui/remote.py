"""Télécommande : piloter un autre lecteur (le téléphone…) depuis celui-ci.

Code frontière (exclu de la couverture) : les décisions vivent dans
core.remote_controller, pur et testé. Ici :

- RemoteWatcher garde ouvert le flux des lecteurs (libsoup), partagé par
  les pages tant que l'une d'elles est affichée ;
- RemoteDevicesPage liste les lecteurs pilotables ;
- RemoteControlPage est la télécommande de l'un d'eux ;
- play_on_button est le bouton « Lire sur… » des fiches album / playlist ;
- listen_here prend la lecture d'un lecteur pour la jouer sur ce poste.

Parité avec ui/remote/ côté Android.
"""

import asyncio
import codecs
import time
from gettext import gettext as _

from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango, Soup

from jewelbox.api.client import ApiError
from jewelbox.core import remote, remote_controller
from jewelbox.core.formats import format_duration
from jewelbox.ui.resume_dialog import resume_device

# Le serveur écrit un commentaire toutes les 20 s : sans rien pendant 60 s,
# la connexion est morte et on la rouvre.
_STREAM_TIMEOUT_SECONDS = 60
# Plus aucune page affichée : on ferme le flux après ce délai seulement, le
# temps qu'une page en remplace une autre (liste → télécommande).
_RELEASE_DELAY_SECONDS = 5


def _loop():
    return asyncio.get_event_loop_policy().get_event_loop()


def device_name(device) -> str:
    return device.device_label or _('Lecteur sans nom')


def _state_text(state) -> str:
    playback = state.playback_state if state is not None else 'idle'
    return {'playing': _('En lecture'), 'paused': _('En pause')}.get(
        playback, _('À l’arrêt'))


def _track_text(track) -> str:
    if track is None:
        return _('Rien en lecture')
    return f'{track.title} — {track.artist_name}' if track.artist_name else track.title


def _toast(widget, text):
    root = widget.get_root()
    if hasattr(root, 'show_toast'):
        root.show_toast(text)


def send_command(app, widget, device_id, command):
    """Envoie un ordre ; un refus (lecteur parti, serveur injoignable) est
    signalé par un toast. Le lecteur confirme par son prochain rapport."""
    client = app.get_client()
    if client is None:
        return

    async def run():
        try:
            await client.remote_command(device_id, command)
        except ApiError:
            _toast(widget, _('La commande n’a pas abouti'))

    _loop().create_task(run())


def listen_here(app, widget, device, on_done):
    """« Écouter ici » : joue la file de device sur ce poste, à sa piste et à
    sa position du moment, puis le met en pause là-bas. on_done() une fois la
    lecture lancée ici ; un échec est signalé par un toast."""
    state = device.state

    async def run():
        if await resume_device(app, device, live_state=state) != 'ok':
            _toast(widget, _('Impossible de reprendre cette lecture ici'))
            return
        if state is not None and state.playback_state == 'playing':
            client = app.get_client()
            try:
                await client.remote_command(
                    device.device_id, remote_controller.simple_command('pause'))
            except ApiError:
                pass
        on_done()

    _loop().create_task(run())


def _listen_here_done(page):
    if page.on_listened_here is not None:
        page.on_listened_here()


async def _load_texture(app, cover_url):
    client = app.get_client()
    url = client.resolve_cover(cover_url) if client is not None else None
    if not url:
        return None
    try:
        data = await client.fetch_bytes(url)
        return Gdk.Texture.new_from_bytes(GLib.Bytes.new(data))
    except (ApiError, GLib.Error):
        return None


class RemoteWatcher:
    """Les lecteurs pilotables, en direct. acquire() / release() par page
    affichée : le flux n'est ouvert que tant que quelqu'un regarde.

    devices vaut None tant que la première liste n'est pas arrivée, puis un
    tuple de RemoteDevice (les autres lecteurs pilotables) ; vide si le flux
    ne s'ouvre pas (serveur < 1.18, injoignable)."""

    def __init__(self, app):
        self._app = app
        self._session = Soup.Session(timeout=_STREAM_TIMEOUT_SECONDS)
        self._all = ()
        self.devices = None
        self._listeners = []
        self._users = 0
        self._cancellable = None
        self._task = None
        self._release_id = None

    def add_listener(self, callback):
        self._listeners.append(callback)

    def remove_listener(self, callback):
        if callback in self._listeners:
            self._listeners.remove(callback)

    def acquire(self):
        self._users += 1
        if self._release_id is not None:
            GLib.source_remove(self._release_id)
            self._release_id = None
        if self._task is None:
            self._cancellable = Gio.Cancellable()
            self._task = _loop().create_task(self._run(self._cancellable))

    def release(self):
        self._users = max(0, self._users - 1)
        if self._users == 0 and self._release_id is None:
            self._release_id = GLib.timeout_add_seconds(
                _RELEASE_DELAY_SECONDS, self._stop)

    def _stop(self):
        self._release_id = None
        if self._users > 0:
            return False
        if self._cancellable is not None:
            self._cancellable.cancel()
            self._cancellable = None
        if self._task is not None:
            self._task.cancel()
            self._task = None
        self._all = ()
        self.devices = None
        return False

    def _publish(self):
        self.devices = remote_controller.controllable_others(
            self._all, self._app.device_id)
        for callback in list(self._listeners):
            callback(self.devices)

    async def _run(self, cancellable):
        attempt = 0
        while not cancellable.is_cancelled():
            client = self._app.get_client()
            if client is not None and await self._listen(client, cancellable):
                attempt = 0
            if self.devices is None:
                self._publish()
            attempt += 1
            await asyncio.sleep(remote.reconnect_delay(attempt))

    async def _listen(self, client, cancellable) -> bool:
        try:
            message = client.remote_watch_message()
            stream = await self._session.send_async(
                message, GLib.PRIORITY_DEFAULT, cancellable)
        except (ApiError, GLib.Error):
            return False
        if message.get_status() != 200:
            return False
        parser = remote.SseParser()
        decoder = codecs.getincrementaldecoder('utf-8')('replace')
        try:
            while True:
                chunk = await stream.read_bytes_async(
                    4096, GLib.PRIORITY_DEFAULT, cancellable)
                if chunk.get_size() == 0:
                    break
                for event, data in parser.feed(decoder.decode(chunk.get_data())):
                    self._all = remote_controller.apply_watch_event(
                        self._all, event, data)
                    self._publish()
        except GLib.Error:
            pass
        finally:
            try:
                stream.close(None)
            except GLib.Error:
                pass
        return True


class _WatchingMixin:
    """Une page qui suit le RemoteWatcher tant qu'elle est affichée."""

    def _watch(self, watcher):
        self._watcher = watcher
        self.connect('map', self._on_map)
        self.connect('unmap', self._on_unmap)

    def _on_map(self, *_args):
        self._watcher.add_listener(self._on_devices)
        self._watcher.acquire()
        self._on_devices(self._watcher.devices)

    def _on_unmap(self, *_args):
        self._watcher.remove_listener(self._on_devices)
        self._watcher.release()

    def _on_devices(self, devices):
        raise NotImplementedError


class RemoteDevicesPage(_WatchingMixin, Gtk.Stack):
    """Les autres lecteurs pilotables ; un clic ouvre leur télécommande."""

    def __init__(self, app, watcher):
        super().__init__(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self._app = app
        self.on_device_activated = None
        self.on_listened_here = None

        self.add_named(Gtk.Spinner(spinning=True, halign=Gtk.Align.CENTER,
                                   valign=Gtk.Align.CENTER), 'loading')
        self.add_named(Adw.StatusPage(
            icon_name='audio-speakers-symbolic',
            title=_('Aucun autre lecteur à piloter'),
            description=_('Sur l’appareil voulu, activez « Autoriser le '
                          'contrôle à distance » et laissez le lecteur ouvert.')),
            'empty')

        self._list = Gtk.ListBox(
            selection_mode=Gtk.SelectionMode.NONE, css_classes=['boxed-list'],
            margin_top=24, margin_bottom=24, margin_start=24, margin_end=24,
            valign=Gtk.Align.START)
        self._list.connect('row-activated', self._on_row_activated)
        clamp = Adw.Clamp(maximum_size=640, child=self._list)
        self.add_named(Gtk.ScrolledWindow(
            child=clamp, hscrollbar_policy=Gtk.PolicyType.NEVER), 'list')
        self._watch(watcher)

    def _on_devices(self, devices):
        if devices is None:
            self.set_visible_child_name('loading')
            return
        if not devices:
            self.set_visible_child_name('empty')
            return
        while (row := self._list.get_row_at_index(0)) is not None:
            self._list.remove(row)
        for device in devices:
            self._list.append(self._build_row(device))
        self.set_visible_child_name('list')

    def _build_row(self, device):
        state = device.state
        track = state.track if state is not None else None
        row = Adw.ActionRow(
            title=device_name(device), use_markup=False, activatable=True,
            subtitle=f'{_track_text(track)}\n{_state_text(state)}')
        row.device_id = device.device_id
        row.add_prefix(Gtk.Image(
            icon_name=remote_controller.device_icon_name(device.device_label),
            pixel_size=32))
        if track is not None:
            listen = Gtk.Button(
                icon_name='audio-headphones-symbolic', tooltip_text=_('Écouter ici'),
                valign=Gtk.Align.CENTER, css_classes=['flat', 'circular'])
            listen.connect('clicked', lambda *_a: listen_here(
                self._app, self, device, lambda: _listen_here_done(self)))
            row.add_suffix(listen)
            playing = state.playback_state == 'playing'
            toggle = Gtk.Button(
                icon_name='media-playback-pause-symbolic' if playing
                else 'media-playback-start-symbolic',
                tooltip_text=_('Pause') if playing else _('Lecture'),
                valign=Gtk.Align.CENTER, css_classes=['flat', 'circular'])
            toggle.connect('clicked', lambda *_a: send_command(
                self._app, self, device.device_id,
                remote_controller.simple_command('toggle')))
            row.add_suffix(toggle)
        row.add_suffix(Gtk.Image(icon_name='go-next-symbolic'))
        return row

    def _on_row_activated(self, _list, row):
        if self.on_device_activated is not None:
            self.on_device_activated(row.device_id)


class RemoteControlPage(_WatchingMixin, Gtk.Stack):
    """La télécommande d'un lecteur : ce qu'il joue, en direct, et les
    commandes que ses capacités permettent ; sa file en dessous (un clic
    lance ce morceau là-bas)."""

    def __init__(self, app, watcher, device_id):
        super().__init__(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self._app = app
        self._device_id = device_id
        self._device = None
        self.on_title_known = None
        self.on_listened_here = None
        self._cover_url = None
        self._queue_key = object()
        self._queue_generation = 0
        self._ticker_id = None
        self._updating = False
        # Glisser en cours (position / volume) : l'affichage ne suit plus les
        # rapports jusqu'à un peu après le dernier mouvement.
        self._seek_target = None
        self._seek_id = None
        self._seek_frozen_until = 0.0
        self._volume_target = None
        self._volume_id = None
        self._volume_frozen_until = 0.0

        self.add_named(Gtk.Spinner(spinning=True, halign=Gtk.Align.CENTER,
                                   valign=Gtk.Align.CENTER), 'loading')
        self.add_named(Adw.StatusPage(
            icon_name='audio-speakers-symbolic',
            title=_('Cet appareil n’est plus disponible'),
            description=_('Son lecteur a peut-être été fermé.')), 'gone')
        self.add_named(self._build_content(), 'content')
        self._watch(watcher)

    # ── Construction ─────────────────────────────────────────────────────────

    def _build_content(self):
        self._cover = Gtk.Picture(content_fit=Gtk.ContentFit.COVER)
        cover_frame = Adw.Bin(
            child=self._cover, overflow=Gtk.Overflow.HIDDEN,
            css_classes=['card'], width_request=260, height_request=260,
            halign=Gtk.Align.CENTER)

        self._title = Gtk.Label(
            wrap=True, justify=Gtk.Justification.CENTER, css_classes=['title-2'])
        self._artist = Gtk.Label(
            ellipsize=Pango.EllipsizeMode.END, css_classes=['title-4', 'accent'])
        self._album = Gtk.Label(
            ellipsize=Pango.EllipsizeMode.END, css_classes=['dim-label'])

        self._seek_scale = Gtk.Scale(
            orientation=Gtk.Orientation.HORIZONTAL, draw_value=False, hexpand=True)
        self._seek_scale.set_range(0, 1)
        self._seek_scale.connect('change-value', self._on_seek_change_value)
        self._position_label = Gtk.Label(
            label='0:00', xalign=0, css_classes=['numeric', 'caption', 'dim-label'])
        self._duration_label = Gtk.Label(
            label='0:00', xalign=1, css_classes=['numeric', 'caption', 'dim-label'])
        times = Gtk.Box()
        times.append(self._position_label)
        times.append(Gtk.Box(hexpand=True))
        times.append(self._duration_label)
        self._seek_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self._seek_box.append(self._seek_scale)
        self._seek_box.append(times)

        self._shuffle_button = Gtk.ToggleButton(
            icon_name='media-playlist-shuffle-symbolic', valign=Gtk.Align.CENTER,
            css_classes=['flat', 'circular'], tooltip_text=_('Lecture aléatoire'))
        self._shuffle_button.connect('toggled', self._on_shuffle_toggled)
        previous = Gtk.Button(
            icon_name='media-skip-backward-symbolic', valign=Gtk.Align.CENTER,
            css_classes=['flat', 'circular'], tooltip_text=_('Précédent'))
        previous.connect('clicked', lambda *_a: self._send(
            remote_controller.simple_command('previous')))
        self._play_button = Gtk.Button(
            icon_name='media-playback-start-symbolic', valign=Gtk.Align.CENTER,
            css_classes=['circular', 'suggested-action'],
            width_request=56, height_request=56)
        self._play_button.connect('clicked', lambda *_a: self._send(
            remote_controller.simple_command('toggle')))
        following = Gtk.Button(
            icon_name='media-skip-forward-symbolic', valign=Gtk.Align.CENTER,
            css_classes=['flat', 'circular'], tooltip_text=_('Suivant'))
        following.connect('clicked', lambda *_a: self._send(
            remote_controller.simple_command('next')))
        self._repeat_button = Gtk.Button(
            icon_name='media-playlist-repeat-symbolic', valign=Gtk.Align.CENTER,
            css_classes=['flat', 'circular'])
        self._repeat_button.connect('clicked', self._on_repeat_clicked)
        controls = Gtk.Box(spacing=12, halign=Gtk.Align.CENTER)
        for widget in (self._shuffle_button, previous, self._play_button,
                       following, self._repeat_button):
            controls.append(widget)

        self._mute_button = Gtk.Button(
            icon_name='audio-volume-high-symbolic', valign=Gtk.Align.CENTER,
            css_classes=['flat', 'circular'])
        self._mute_button.connect('clicked', self._on_mute_clicked)
        self._volume_scale = Gtk.Scale(
            orientation=Gtk.Orientation.HORIZONTAL, draw_value=False, hexpand=True)
        self._volume_scale.set_range(0, 100)
        self._volume_scale.connect('change-value', self._on_volume_change_value)
        self._volume_box = Gtk.Box(spacing=6)
        self._volume_box.append(self._mute_button)
        self._volume_box.append(self._volume_scale)

        self._listen_button = Gtk.Button(
            child=Adw.ButtonContent(icon_name='audio-headphones-symbolic',
                                    label=_('Écouter ici')),
            halign=Gtk.Align.CENTER, margin_top=8, css_classes=['pill'])
        self._listen_button.connect('clicked', self._on_listen_here_clicked)

        self._queue_title = Gtk.Label(
            label=_('File d’attente'), xalign=0, css_classes=['title-4'],
            margin_top=12)
        self._queue_list = Gtk.ListBox(
            selection_mode=Gtk.SelectionMode.NONE, css_classes=['boxed-list'])
        self._queue_list.connect('row-activated', self._on_queue_row_activated)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                      margin_top=24, margin_bottom=24, margin_start=24, margin_end=24)
        box.append(cover_frame)
        box.append(Gtk.Box(height_request=8))
        box.append(self._title)
        box.append(self._artist)
        box.append(self._album)
        box.append(self._seek_box)
        box.append(controls)
        box.append(self._volume_box)
        box.append(self._listen_button)
        box.append(self._queue_title)
        box.append(self._queue_list)
        return Gtk.ScrolledWindow(
            child=Adw.Clamp(maximum_size=560, child=box),
            hscrollbar_policy=Gtk.PolicyType.NEVER)

    # ── Suivi du lecteur ─────────────────────────────────────────────────────

    def _on_map(self, *args):
        super()._on_map(*args)
        self._ticker_id = GLib.timeout_add(500, self._tick)

    def _on_unmap(self, *args):
        super()._on_unmap(*args)
        if self._ticker_id is not None:
            GLib.source_remove(self._ticker_id)
            self._ticker_id = None

    def _on_devices(self, devices):
        if devices is None:
            self.set_visible_child_name('loading')
            return
        device = next((d for d in devices if d.device_id == self._device_id), None)
        self._device = device
        if device is None:
            self.set_visible_child_name('gone')
            return
        if self.on_title_known is not None:
            self.on_title_known(device_name(device))
        self._show(device)
        self.set_visible_child_name('content')

    def _can(self, capability):
        return self._device is not None and capability in self._device.capabilities

    def _show(self, device):
        state = device.state
        track = state.track if state is not None else None
        self._title.set_label(track.title if track else _('Rien en lecture'))
        self._artist.set_label(track.artist_name if track else '')
        self._artist.set_visible(bool(track and track.artist_name))
        self._album.set_label(track.album_title if track else '')
        self._album.set_visible(bool(track and track.album_title))

        cover_url = track.cover_url if track else None
        if cover_url != self._cover_url:
            self._cover_url = cover_url
            self._cover.set_paintable(None)
            if cover_url:
                _loop().create_task(self._load_cover(cover_url))

        playing = state is not None and state.playback_state == 'playing'
        self._play_button.set_icon_name(
            'media-playback-pause-symbolic' if playing else 'media-playback-start-symbolic')
        self._play_button.set_tooltip_text(_('Pause') if playing else _('Lecture'))

        self._seek_box.set_visible(track is not None)
        self._listen_button.set_visible(track is not None)
        self._seek_scale.set_sensitive(self._can('seek') and state.duration_sec > 0
                                       if state is not None else False)
        self._update_position()

        self._updating = True
        self._shuffle_button.set_visible(self._can('shuffle'))
        self._shuffle_button.set_active(bool(state and state.shuffle))
        self._updating = False

        repeat = (state.repeat if state is not None else None) or 'off'
        self._repeat_button.set_visible(self._can('repeat'))
        self._repeat_button.set_icon_name(
            'media-playlist-repeat-song-symbolic' if repeat == 'one'
            else 'media-playlist-repeat-symbolic')
        self._repeat_button.set_tooltip_text(
            {'all': _('Répéter la file'), 'one': _('Répéter la piste')}.get(
                repeat, _('Répétition désactivée')))
        if repeat != 'off':
            self._repeat_button.add_css_class('accent')
        else:
            self._repeat_button.remove_css_class('accent')

        has_volume = self._can('volume') and state is not None and state.volume is not None
        self._volume_box.set_visible(has_volume)
        if has_volume:
            muted = bool(state.muted)
            self._mute_button.set_sensitive(self._can('mute'))
            self._mute_button.set_icon_name(
                'audio-volume-muted-symbolic' if muted else 'audio-volume-high-symbolic')
            self._mute_button.set_tooltip_text(
                _('Rétablir le son') if muted else _('Couper le son'))
            if time.monotonic() >= self._volume_frozen_until:
                self._volume_scale.set_value(state.volume)

        key = (state.track_id, state.current_index, state.shuffle) if state else None
        if key != self._queue_key:
            self._queue_key = key
            self._reload_queue()
        self._highlight_queue()

    def _tick(self):
        self._update_position()
        return True

    def _update_position(self):
        device = self._device
        if device is None or device.state is None:
            return
        if time.monotonic() < self._seek_frozen_until:
            return
        state = device.state
        position = remote_controller.live_position(state, time.time())
        self._seek_scale.set_range(0, max(state.duration_sec, 1))
        self._seek_scale.set_value(position)
        self._position_label.set_label(format_duration(position))
        self._duration_label.set_label(format_duration(state.duration_sec))

    async def _load_cover(self, cover_url):
        texture = await _load_texture(self._app, cover_url)
        if texture is not None and cover_url == self._cover_url:
            self._cover.set_paintable(texture)

    # ── File ─────────────────────────────────────────────────────────────────

    def _reload_queue(self):
        self._queue_generation += 1
        generation = self._queue_generation
        client = self._app.get_client()
        if client is None or not self._can('queue'):
            self._fill_queue(())
            return

        async def run():
            try:
                queue = await client.device_queue(self._device_id)
                tracks = queue.tracks
            except ApiError:
                tracks = ()
            if generation == self._queue_generation:
                self._fill_queue(tracks)

        _loop().create_task(run())

    def _fill_queue(self, tracks):
        while (row := self._queue_list.get_row_at_index(0)) is not None:
            self._queue_list.remove(row)
        for index, track in enumerate(tracks):
            row = Adw.ActionRow(
                title=track.title, subtitle=track.artist_name, use_markup=False,
                activatable=True)
            row.queue_index = index
            if track.duration:
                row.add_suffix(Gtk.Label(
                    label=track.duration, css_classes=['dim-label', 'numeric']))
            self._queue_list.append(row)
        self._queue_title.set_visible(bool(tracks))
        self._queue_list.set_visible(bool(tracks))
        self._highlight_queue()

    def _highlight_queue(self):
        state = self._device.state if self._device is not None else None
        current = state.current_index if state is not None else -1
        index = 0
        while (row := self._queue_list.get_row_at_index(index)) is not None:
            if index == current:
                row.add_css_class('accent')
            else:
                row.remove_css_class('accent')
            index += 1

    def _on_queue_row_activated(self, _list, row):
        self._send(remote_controller.play_index_command(row.queue_index))

    # ── Commandes ────────────────────────────────────────────────────────────

    def _send(self, command):
        send_command(self._app, self, self._device_id, command)

    def _on_listen_here_clicked(self, _button):
        if self._device is not None:
            listen_here(self._app, self, self._device,
                        lambda: _listen_here_done(self))

    def _on_shuffle_toggled(self, button):
        if not self._updating:
            self._send(remote_controller.shuffle_command(button.get_active()))

    def _on_repeat_clicked(self, _button):
        state = self._device.state if self._device is not None else None
        current = state.repeat if state is not None else None
        self._send(remote_controller.repeat_command(
            remote_controller.next_repeat(current)))

    def _on_mute_clicked(self, _button):
        state = self._device.state if self._device is not None else None
        self._send(remote_controller.mute_command(not (state and state.muted)))

    # Glisser : l'ordre part peu après le dernier mouvement (un seul saut ou
    # réglage de volume, pas un par pixel), et l'affichage reste figé le temps
    # que le lecteur rapporte son nouvel état.

    def _on_seek_change_value(self, _scale, _scroll_type, value):
        self._seek_target = value
        self._seek_frozen_until = time.monotonic() + 2
        self._position_label.set_label(format_duration(value))
        if self._seek_id is not None:
            GLib.source_remove(self._seek_id)
        self._seek_id = GLib.timeout_add(250, self._send_seek)
        return False

    def _send_seek(self):
        self._seek_id = None
        if self._seek_target is not None:
            self._send(remote_controller.seek_command(self._seek_target))
            self._seek_target = None
        return False

    def _on_volume_change_value(self, _scale, _scroll_type, value):
        self._volume_target = value
        self._volume_frozen_until = time.monotonic() + 2
        if self._volume_id is not None:
            GLib.source_remove(self._volume_id)
        self._volume_id = GLib.timeout_add(250, self._send_volume)
        return False

    def _send_volume(self):
        self._volume_id = None
        if self._volume_target is not None:
            self._send(remote_controller.volume_command(self._volume_target))
            self._volume_target = None
        return False


def play_on_button(app, media, *, label=True):
    """Bouton « Lire sur… » : lance media() — (type, référence) au format de
    play_media, ou None tant que la fiche n'est pas chargée — sur un autre
    lecteur. Les lecteurs sont listés à l'ouverture du menu : ceux qu'on peut
    piloter et qui savent lancer un média."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    popover = Gtk.Popover(child=box)
    button = Gtk.MenuButton(
        popover=popover, valign=Gtk.Align.CENTER,
        tooltip_text=_('Lire sur un autre appareil'))
    if label:
        button.set_child(Adw.ButtonContent(
            icon_name='audio-speakers-symbolic', label=_('Lire sur…')))
        button.add_css_class('pill')
    else:
        button.set_icon_name('audio-speakers-symbolic')
        button.add_css_class('flat')

    def show_message(text):
        while (child := box.get_first_child()) is not None:
            box.remove(child)
        box.append(Gtk.Label(label=text, css_classes=['dim-label'],
                             margin_top=6, margin_bottom=6,
                             margin_start=12, margin_end=12))

    def show_devices(devices):
        if not devices:
            show_message(_('Aucun lecteur disponible'))
            return
        while (child := box.get_first_child()) is not None:
            box.remove(child)
        for device in devices:
            item = Gtk.Button(css_classes=['flat'])
            content = Gtk.Box(spacing=8)
            content.append(Gtk.Image(
                icon_name=remote_controller.device_icon_name(device.device_label)))
            content.append(Gtk.Label(label=device_name(device), xalign=0))
            item.set_child(content)
            item.connect('clicked', lambda _b, d=device: start(d))
            box.append(item)

    def start(device):
        popover.popdown()
        target = media()
        client = app.get_client()
        if target is None or client is None:
            return

        async def run():
            try:
                await client.remote_command(
                    device.device_id, remote_controller.play_media_command(*target))
            except ApiError:
                _toast(button, _('La commande n’a pas abouti'))
                return
            _toast(button, _('Lecture lancée sur {name}').format(
                name=device_name(device)))

        _loop().create_task(run())

    def on_show(_popover):
        show_message(_('Recherche des lecteurs…'))
        client = app.get_client()
        if client is None:
            show_message(_('Aucun lecteur disponible'))
            return

        async def run():
            try:
                devices = remote_controller.controllable_others(
                    await client.remote_devices(), app.device_id)
            except ApiError:
                devices = ()
            show_devices(tuple(d for d in devices if 'play_media' in d.capabilities))

        _loop().create_task(run())

    popover.connect('show', on_show)
    return button
