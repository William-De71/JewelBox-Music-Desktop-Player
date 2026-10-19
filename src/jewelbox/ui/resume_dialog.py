"""Dialogue « Reprendre depuis un autre appareil ».

Chaque appareil publie sa file sur le serveur (playback/queue_sync.py côté
bureau, PlayerConnection côté Android, PlayerContext côté web). Ici on liste
celles des AUTRES appareils, la plus récente en tête ; en choisir une la
charge et la lance à la même piste, à la même position.

Code frontière (exclu de la couverture).
"""

import asyncio
from datetime import datetime, timezone
from gettext import gettext as _

from gi.repository import Adw, Gtk

from jewelbox.api.client import ApiError
from jewelbox.core.queue_sync import resume_start


async def resume_device(app, device):
    """Charge la file de device et la lance à la même piste, à la même
    position. Rend 'ok', 'empty' (rien de jouable) ou 'error' (serveur)."""
    client = app.get_client()
    playback = app.playback
    if client is None or playback is None:
        return 'error'
    try:
        queue = await client.device_queue(device.device_id)
    except ApiError:
        return 'error'
    start = resume_start([t.has_file for t in queue.tracks],
                         queue.current_index)
    if start is None:
        return 'empty'
    index, same_track = start
    # La source d'origine (« Mix dynamique », un album…) reste la plus
    # parlante ; à défaut, d'où on a repris.
    playback.play_queue_tracks(
        queue.tracks, start_index=index,
        source_name=queue.source_label or _('Repris de {device}').format(
            device=device.device_label or _('un autre appareil')),
        start_position=queue.position_sec if same_track else 0.0)
    return 'ok'


def time_ago(updated_at):
    """« il y a 5 min » à partir d'un horodatage SQLite (UTC, sans fuseau)."""
    if not updated_at:
        return ''
    try:
        moment = datetime.strptime(updated_at, '%Y-%m-%d %H:%M:%S').replace(
            tzinfo=timezone.utc)
    except ValueError:
        return ''
    minutes = int((datetime.now(timezone.utc) - moment).total_seconds() // 60)
    if minutes < 1:
        return _('à l’instant')
    if minutes < 60:
        return _('il y a {n} min').format(n=minutes)
    if minutes < 24 * 60:
        return _('il y a {n} h').format(n=minutes // 60)
    return _('il y a {n} j').format(n=minutes // (24 * 60))


def device_summary(device):
    """(titre, sous-titre) décrivant la file d'un autre appareil : ce qu'il
    écoutait (piste — artiste, sinon sa source), puis d'où vient la file,
    quel appareil, combien de pistes et quand."""
    name = device.device_label or _('Appareil sans nom')
    if device.current_title:
        title = (f'{device.current_title} — {device.current_artist}'
                 if device.current_artist else device.current_title)
        details = [device.source_label] if device.source_label else []
    else:
        title = device.source_label or name
        details = []
    if title != name:
        details.append(name)
    details.append(_('{count} pistes').format(count=device.track_count)
                   if device.track_count != 1 else _('1 piste'))
    when = time_ago(device.updated_at)
    if when:
        details.append(when)
    return title, ' · '.join(details)


class ResumeDialog(Adw.Dialog):

    def __init__(self, application):
        super().__init__(title=_('Reprendre depuis un autre appareil'),
                         content_width=420, content_height=420)
        self._app = application

        self._stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        loading = Adw.StatusPage()
        loading.set_child(Adw.Spinner(
            width_request=32, height_request=32, halign=Gtk.Align.CENTER))
        self._stack.add_named(loading, 'loading')
        self._message = Adw.StatusPage(icon_name='phone-symbolic')
        self._stack.add_named(self._message, 'message')

        self._list = Gtk.ListBox(
            selection_mode=Gtk.SelectionMode.NONE, css_classes=['boxed-list'],
            margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
        self._list.connect('row-activated', self._on_row_activated)
        self._stack.add_named(Gtk.ScrolledWindow(
            child=self._list, hscrollbar_policy=Gtk.PolicyType.NEVER,
            vexpand=True), 'list')

        toolbar = Adw.ToolbarView(content=self._stack)
        toolbar.add_top_bar(Adw.HeaderBar())
        self.set_child(toolbar)
        self._run(self._load())

    async def _load(self):
        client = self._app.get_client()
        if client is None:
            self._show_message(_('Aucun serveur configuré'), '')
            return
        try:
            devices = await client.queue_devices()
        except ApiError:
            self._show_message(_('Serveur injoignable'), '')
            return
        devices = [d for d in devices if d.track_count > 0]
        if not devices:
            self._show_message(
                _('Rien à reprendre'),
                _('Aucun autre appareil n’a laissé de file d’attente.'))
            return
        for device in devices:
            title, subtitle = device_summary(device)
            row = Adw.ActionRow(title=title, subtitle=subtitle,
                                use_markup=False, activatable=True)
            row.add_suffix(Gtk.Image(icon_name='media-playback-start-symbolic'))
            row._device = device
            self._list.append(row)
        self._stack.set_visible_child_name('list')

    def _show_message(self, title, description):
        self._message.set_title(title)
        self._message.set_description(description)
        self._stack.set_visible_child_name('message')

    def _on_row_activated(self, _listbox, row):
        device = getattr(row, '_device', None)
        if device is not None:
            self._list.set_sensitive(False)
            self._run(self._resume(device))

    async def _resume(self, device):
        outcome = await resume_device(self._app, device)
        if outcome == 'error':
            self._list.set_sensitive(True)
        elif outcome == 'empty':
            self._show_message(_('Rien à reprendre'),
                               _('Aucune piste jouable dans cette file.'))
        else:
            self.close()

    def _run(self, coroutine):
        asyncio.get_event_loop_policy().get_event_loop().create_task(coroutine)
