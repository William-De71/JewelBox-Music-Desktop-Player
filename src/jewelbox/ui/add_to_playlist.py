"""Dialogue « Ajouter à une playlist » : choisir une playlist existante ou en
créer une à la volée.

Parité avec AddToPlaylistSheetContent côté Android (et AddToPlaylistModal de
la PWA) : une piste (track_id) ou un album entier (album_id) ; la liste des
playlists se charge à l'ouverture, une ligne « Nouvelle playlist… » en fin
de liste demande un nom, crée la playlist puis y ajoute.

Code frontière (exclu de la couverture) : ne fait qu'enchaîner des appels
JewelBoxClient déjà testés.
"""

import asyncio
from gettext import gettext as _

from gi.repository import Adw, Gtk

from jewelbox.api.client import ApiError


class AddToPlaylistDialog(Adw.Dialog):
    """Exactement un de track_id / album_id. on_added(nom_playlist, ajoutées)
    est appelé après un ajout réussi (la fenêtre en fait un toast)."""

    def __init__(self, application, track_id=None, album_id=None):
        super().__init__(
            title=(_('Ajouter à une playlist') if track_id is not None
                   else _('Ajouter l’album à une playlist')),
            content_width=380, content_height=440)
        self._app = application
        self._track_id = track_id
        self._album_id = album_id
        self.on_added = None

        self._stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        loading = Adw.StatusPage()
        loading.set_child(Adw.Spinner(
            width_request=32, height_request=32, halign=Gtk.Align.CENTER))
        self._stack.add_named(loading, 'loading')

        self._error = Adw.StatusPage(
            icon_name='network-error-symbolic',
            title=_('Playlists indisponibles'))
        self._stack.add_named(self._error, 'error')

        self._list = Gtk.ListBox(
            selection_mode=Gtk.SelectionMode.NONE,
            css_classes=['boxed-list'],
            margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
        self._list.connect('row-activated', self._on_row_activated)
        self._stack.add_named(Gtk.ScrolledWindow(
            child=self._list, hscrollbar_policy=Gtk.PolicyType.NEVER,
            vexpand=True), 'list')

        toolbar = Adw.ToolbarView(content=self._stack)
        toolbar.add_top_bar(Adw.HeaderBar())
        self.set_child(toolbar)

        self._run(self._load())

    # ── Chargement ───────────────────────────────────────────────────────────

    async def _load(self):
        client = self._app.get_client()
        if client is None:
            self._stack.set_visible_child_name('error')
            return
        try:
            playlists = await client.playlists()
        except ApiError:
            self._stack.set_visible_child_name('error')
            return
        for playlist in playlists:
            row = Adw.ActionRow(
                title=playlist.name, activatable=True,
                subtitle=(_('{count} pistes').format(count=playlist.track_count)
                          if playlist.track_count != 1 else _('1 piste')))
            row.set_title_lines(1)
            row.add_prefix(Gtk.Image(icon_name='view-list-symbolic'))
            row._playlist = playlist
            self._list.append(row)
        create = Adw.ActionRow(
            title=_('Nouvelle playlist…'), activatable=True,
            css_classes=['accent'])
        create.add_prefix(Gtk.Image(icon_name='list-add-symbolic'))
        create._create = True
        self._list.append(create)
        self._stack.set_visible_child_name('list')

    # ── Choix ────────────────────────────────────────────────────────────────

    def _on_row_activated(self, _listbox, row):
        if getattr(row, '_create', False):
            self._prompt_create()
            return
        playlist = getattr(row, '_playlist', None)
        if playlist is not None:
            self._list.set_sensitive(False)
            self._run(self._add(playlist.id, playlist.name))

    def _prompt_create(self):
        dialog = Adw.AlertDialog(heading=_('Créer une playlist'))
        entry = Gtk.Entry(activates_default=True)
        dialog.set_extra_child(entry)
        dialog.add_response('cancel', _('Annuler'))
        dialog.add_response('save', _('Créer et ajouter'))
        dialog.set_response_appearance(
            'save', Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response('save')
        dialog.set_close_response('cancel')
        dialog.connect('response', self._on_create_response, entry)
        dialog.present(self)

    def _on_create_response(self, _dialog, response, entry):
        name = entry.get_text().strip()
        if response != 'save' or not name:
            return
        self._list.set_sensitive(False)
        self._run(self._create_and_add(name))

    async def _create_and_add(self, name):
        client = self._app.get_client()
        if client is None:
            return
        try:
            playlist = await client.create_playlist(name)
        except ApiError:
            self._list.set_sensitive(True)
            return
        await self._add(playlist.id, playlist.name)

    async def _add(self, playlist_id, name):
        client = self._app.get_client()
        if client is None:
            return
        try:
            if self._track_id is not None:
                result = await client.add_track_to_playlist(
                    playlist_id, self._track_id)
            else:
                result = await client.add_album_to_playlist(
                    playlist_id, self._album_id)
        except ApiError:
            self._list.set_sensitive(True)
            return
        self.close()
        if self.on_added is not None:
            self.on_added(name, result.added)

    def _run(self, coroutine):
        asyncio.get_event_loop_policy().get_event_loop().create_task(coroutine)


def present_add_to_playlist(application, parent, track_id=None, album_id=None):
    """Ouvre le dialogue sur `parent` ; le résultat s'affiche en toast dans la
    fenêtre principale (show_toast) quand elle en propose un."""
    dialog = AddToPlaylistDialog(application, track_id=track_id,
                                 album_id=album_id)
    root = parent.get_root() if parent is not None else None

    def on_added(name, added):
        if root is None or not hasattr(root, 'show_toast'):
            return
        if album_id is not None:
            text = (_('{count} pistes ajoutées à « {name} »').format(
                        count=added, name=name) if added != 1
                    else _('1 piste ajoutée à « {name} »').format(name=name))
        else:
            text = _('Ajoutée à « {name} »').format(name=name)
        root.show_toast(text)

    dialog.on_added = on_added
    dialog.present(root)
    return dialog

