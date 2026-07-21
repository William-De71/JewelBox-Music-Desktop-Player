"""Fiche playlist utilisateur : en-tête, liste des pistes, gestion.

Parité avec PlaylistDetailScreen côté Android : un en-tête (nombre de pistes
et bouton « Écouter »), puis la liste ordonnée des pistes — chacune avec son
numéro, sa pochette, titre/artiste, un bouton favori et un menu par entrée
(monter, descendre, retirer). La barre du shell offre renommer et supprimer.

Lancer la playlist signale le début de lecture au serveur (récents de
l'accueil) via play_queue_tracks(report_playlist_id=…) — contrairement aux
listes intelligentes, qui ne sont délibérément pas des entrées d'historique.

Les mutations (retrait, réordonnancement, renommage, suppression) passent par
le client, puis la fiche se recharge sur la Playlist renvoyée par le serveur :
la source de vérité reste le serveur, jamais un état local reconstruit à la
main. La piste sans fichier est estompée et non jouable (.track-unavailable).

Code frontière (exclu de la couverture) : cette page ne fait qu'afficher une
Playlist chargée par api.client et déléguer les actions à PlaybackSession /
JewelBoxClient, tous testés séparément.
"""

import asyncio
from gettext import gettext as _

from gi.repository import Adw, Gdk, GLib, Gio, Gtk, Pango

from jewelbox.api.client import ApiError
from jewelbox.core.formats import format_duration

_COVER_SIZE = 48


class PlaylistDetailPage(Gtk.Stack):
    """États : chargement, erreur (Réessayer), contenu. Une instance par
    navigation — la fenêtre en recrée une à chaque playlist ouverte.

    on_deleted est appelé après une suppression réussie pour que la fenêtre
    dépile la fiche ; on_renamed(nom) rafraîchit le titre de la NavigationPage."""

    def __init__(self, application, playlist_id: int):
        super().__init__(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self._app = application
        self._playlist_id = playlist_id
        self._playlist = None
        self._textures = {}
        self._row_track_ids = {}   # row → track_id (surbrillance piste courante)
        self._current_track_id = None
        self._is_playing = False
        self.on_deleted = None
        self.on_renamed = None
        self.on_title_known = None

        loading = Adw.StatusPage(title=_('Chargement de la playlist…'))
        loading.set_child(Adw.Spinner(
            width_request=48, height_request=48, halign=Gtk.Align.CENTER))
        self.add_named(loading, 'loading')

        self._error = Adw.StatusPage(
            icon_name='network-error-symbolic', title=_('Playlist introuvable'))
        retry = Gtk.Button(label=_('Réessayer'), halign=Gtk.Align.CENTER,
                           css_classes=['pill', 'suggested-action'])
        retry.connect('clicked', lambda *_a: self.reload())
        self._error.set_child(retry)
        self.add_named(self._error, 'error')

        self._content = self._build_content()
        self.add_named(self._content, 'content')

        # Abonnement à la session lié à l'affichage, pas à la vie de l'objet :
        # dans un Adw.NavigationView, dépiler une page ne la détruit pas (elle
        # reste référencée), donc « destroy » ne se déclenche pas au retour et
        # un abonnement pris dans __init__ ne serait jamais rendu — les
        # listeners s'accumuleraient à chaque ouverture. « map »/« unmap »,
        # eux, suivent fidèlement l'entrée et la sortie d'écran.
        self._playback = application.playback
        self._subscribed = False
        self.connect('map', self._on_map)
        self.connect('unmap', self._on_unmap)

        self.reload()

    def _on_map(self, *_args):
        if self._playback is not None and not self._subscribed:
            self._playback.add_listener(self._on_playback_state)
            self._subscribed = True

    def _on_unmap(self, *_args):
        if self._playback is not None and self._subscribed:
            self._playback.remove_listener(self._on_playback_state)
            self._subscribed = False

    def _build_content(self):
        # En-tête : compteur de pistes à gauche, puis « Écouter » et les
        # boutons de gestion (renommer / supprimer) à droite. La barre du shell
        # étant partagée par toutes les pages, on pose ces actions dans l'en-tête
        # de la fiche plutôt que dans le header global.
        self._count_label = Gtk.Label(
            xalign=0, hexpand=True, css_classes=['dim-label'])
        self._play_button = Gtk.Button(
            css_classes=['pill', 'suggested-action'])
        play_content = Adw.ButtonContent(
            icon_name='media-playback-start-symbolic', label=_('Écouter'))
        self._play_button.set_child(play_content)
        self._play_button.connect('clicked', lambda *_a: self._play(0))

        rename = Gtk.Button(
            icon_name='document-edit-symbolic', valign=Gtk.Align.CENTER,
            tooltip_text=_('Renommer'), css_classes=['flat'])
        rename.connect('clicked', lambda *_a: self._prompt_rename())
        delete = Gtk.Button(
            icon_name='user-trash-symbolic', valign=Gtk.Align.CENTER,
            tooltip_text=_('Supprimer'), css_classes=['flat'])
        delete.connect('clicked', lambda *_a: self._prompt_delete())

        header = Gtk.Box(spacing=8, margin_top=16, margin_bottom=8,
                         margin_start=24, margin_end=24)
        header.append(self._count_label)
        header.append(self._play_button)
        header.append(rename)
        header.append(delete)

        # Message affiché à la place de la liste quand la playlist est vide.
        self._empty_label = Gtk.Label(
            label=_('Aucune piste jouable dans cette playlist'),
            css_classes=['dim-label'], margin_top=24, visible=False)

        self._tracks_box = Gtk.ListBox(
            selection_mode=Gtk.SelectionMode.NONE,
            css_classes=['boxed-list'],
            margin_start=24, margin_end=24, margin_bottom=24)
        self._tracks_box.connect('row-activated', self._on_row_activated)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        outer.append(header)
        outer.append(self._empty_label)
        outer.append(self._tracks_box)

        return Gtk.ScrolledWindow(
            child=outer, hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)

    # ── Chargement ───────────────────────────────────────────────────────────

    def reload(self):
        client = self._app.get_client()
        if client is None:
            self.set_visible_child_name('error')
            return
        self.set_visible_child_name('loading')
        task = self._load(client)
        asyncio.get_event_loop_policy().get_event_loop().create_task(task)

    async def _load(self, client):
        try:
            playlist = await client.playlist(self._playlist_id)
        except ApiError:
            self.set_visible_child_name('error')
            return
        self._apply(client, playlist)
        self.set_visible_child_name('content')

    def _apply(self, client, playlist):
        """Peuple la fiche à partir d'une Playlist (chargement initial ou
        réponse d'une mutation). Réémet aussi la surbrillance de la piste en
        cours, car les lignes viennent d'être reconstruites."""
        self._playlist = playlist
        if self.on_title_known is not None:
            self.on_title_known(playlist.name)

        count = len(playlist.tracks)
        self._count_label.set_label(
            _('{count} pistes').format(count=count) if count != 1
            else _('1 piste'))

        playable = [t for t in playlist.tracks if t.has_file]
        self._play_button.set_visible(bool(playable))
        self._empty_label.set_visible(not playlist.tracks)

        while (row := self._tracks_box.get_row_at_index(0)) is not None:
            self._tracks_box.remove(row)
        self._row_track_ids = {}
        for index, track in enumerate(playlist.tracks):
            self._tracks_box.append(
                self._build_track_row(client, track, index, count))
        if self._playback is not None:
            self._on_playback_state(self._playback._build_state())

    # ── Lecture ──────────────────────────────────────────────────────────────

    def _play(self, start_index: int):
        # start_index est un index parmi les pistes JOUABLES (le bouton Écouter
        # passe 0, une ligne passe son rang filtré) : play_queue_tracks filtre
        # à son tour, l'index reste donc cohérent. report_playlist_id alimente
        # les récents de l'accueil (une playlist utilisateur EST un historique).
        playback = self._app.playback
        if playback is None or self._playlist is None:
            return
        playback.play_queue_tracks(
            self._playlist.tracks, start_index=start_index,
            report_playlist_id=self._playlist_id)

    def _play_or_toggle(self, track_id):
        playback = self._app.playback
        if playback is None or self._playlist is None:
            return
        if track_id == self._current_track_id:
            playback.toggle_play_pause()
            return
        playable = [t for t in self._playlist.tracks if t.has_file]
        start = next((i for i, t in enumerate(playable) if t.id == track_id), 0)
        self._play(start)

    # ── Lignes de piste ──────────────────────────────────────────────────────

    def _build_track_row(self, client, track, index, count):
        position_label = Gtk.Label(
            label=str(index + 1), width_chars=2, css_classes=['dim-label'],
            valign=Gtk.Align.CENTER)

        cover = Gtk.Picture(
            content_fit=Gtk.ContentFit.COVER,
            width_request=_COVER_SIZE, height_request=_COVER_SIZE,
            overflow=Gtk.Overflow.HIDDEN, valign=Gtk.Align.CENTER)
        cover.add_css_class('jewelbox-cover')
        cover_url = client.resolve_cover(track.cover_url)
        if cover_url:
            cover._wanted_url = cover_url
            cover.set_paintable(self._textures.get(cover_url))
            if cover_url not in self._textures:
                task = self._load_cover(cover, cover_url)
                asyncio.get_event_loop_policy().get_event_loop().create_task(task)

        title_label = Gtk.Label(
            label=track.title, xalign=0, hexpand=True,
            ellipsize=Pango.EllipsizeMode.END, css_classes=['heading'])
        subtitle = ' · '.join(
            part for part in (track.artist_name, track.album_title) if part)
        subtitle_label = Gtk.Label(
            label=subtitle, xalign=0, ellipsize=Pango.EllipsizeMode.END,
            css_classes=['caption', 'dim-label'])
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2,
                       valign=Gtk.Align.CENTER, hexpand=True)
        text.append(title_label)
        if subtitle:
            text.append(subtitle_label)

        duration_label = Gtk.Label(
            label=track.duration or format_duration(None),
            css_classes=['dim-label'], valign=Gtk.Align.CENTER)

        favorite_button = Gtk.ToggleButton(
            active=track.is_favorite, valign=Gtk.Align.CENTER,
            css_classes=['flat'], tooltip_text=_('Favori'),
            icon_name=('starred-symbolic' if track.is_favorite
                      else 'non-starred-symbolic'))
        favorite_button.connect(
            'toggled', self._on_favorite_toggled, client, track.id)

        menu = self._build_entry_menu(track, index, count)

        row_box = Gtk.Box(spacing=12, margin_top=6, margin_bottom=6,
                          margin_start=12, margin_end=8)
        row_box.append(position_label)
        row_box.append(cover)
        row_box.append(text)
        row_box.append(duration_label)
        row_box.append(favorite_button)
        row_box.append(menu)

        row = Gtk.ListBoxRow(activatable=track.has_file, child=row_box)
        if not track.has_file:
            row.add_css_class('track-unavailable')
        else:
            row._track_id = track.id
            self._row_track_ids[row] = track.id
        return row

    def _build_entry_menu(self, track, index, count):
        """Menu par entrée : monter / descendre / retirer. entry_id identifie
        l'entrée (une même piste peut figurer deux fois) ; sans lui (payload
        partiel), retirer et réordonner sont désactivés faute d'identifiant."""
        button = Gtk.MenuButton(
            icon_name='view-more-symbolic', css_classes=['flat'],
            valign=Gtk.Align.CENTER, tooltip_text=_('Options'))
        menu = Gio.Menu()
        has_entry = track.entry_id is not None
        if has_entry and index > 0:
            menu.append(_('Monter'), f'playlist-entry.move-up::{track.entry_id}')
        if has_entry and index < count - 1:
            menu.append(_('Descendre'),
                        f'playlist-entry.move-down::{track.entry_id}')
        if has_entry:
            menu.append(_('Retirer de la playlist'),
                        f'playlist-entry.remove::{track.entry_id}')
        button.set_menu_model(menu)
        # Les actions vivent dans un groupe local à la page (voir
        # _install_actions) : le MenuButton hérite du groupe par remontée.
        return button

    def _on_row_activated(self, _listbox, row):
        track_id = getattr(row, '_track_id', None)
        if track_id is not None:
            self._play_or_toggle(track_id)

    # ── Actions d'entrée (retirer / réordonner) ──────────────────────────────

    def install_actions(self):
        """Groupe d'actions « playlist-entry » à insérer sur la page par la
        fenêtre. Chaque action porte l'entry_id en paramètre chaîne."""
        group = Gio.SimpleActionGroup()
        for name, handler in (
            ('move-up', self._on_move_up),
            ('move-down', self._on_move_down),
            ('remove', self._on_remove_entry),
        ):
            action = Gio.SimpleAction.new(name, GLib.VariantType.new('s'))
            action.connect('activate', handler)
            group.add_action(action)
        return group

    def _on_move_up(self, _action, param):
        self._move_entry(int(param.get_string()), -1)

    def _on_move_down(self, _action, param):
        self._move_entry(int(param.get_string()), +1)

    def _move_entry(self, entry_id, delta):
        # Réordonnancement complet : on calcule le nouvel ordre des entry_id
        # localement puis on l'envoie au serveur (PUT), qui renvoie la playlist
        # à jour. La liste des pistes est la source d'ordre.
        if self._playlist is None:
            return
        entry_ids = [t.entry_id for t in self._playlist.tracks
                     if t.entry_id is not None]
        try:
            index = entry_ids.index(entry_id)
        except ValueError:
            return
        target = index + delta
        if not 0 <= target < len(entry_ids):
            return
        entry_ids[index], entry_ids[target] = entry_ids[target], entry_ids[index]
        task = self._mutate(lambda client: client.reorder_playlist(
            self._playlist_id, entry_ids))
        asyncio.get_event_loop_policy().get_event_loop().create_task(task)

    def _on_remove_entry(self, _action, param):
        entry_id = int(param.get_string())
        task = self._mutate(lambda client: client.remove_playlist_entry(
            self._playlist_id, entry_id))
        asyncio.get_event_loop_policy().get_event_loop().create_task(task)

    async def _mutate(self, call):
        """Enchaîne une mutation renvoyant la Playlist à jour, puis rafraîchit
        la fiche avec. Best-effort : un échec laisse l'affichage inchangé."""
        client = self._app.get_client()
        if client is None:
            return
        try:
            playlist = await call(client)
        except ApiError:
            return
        self._apply(client, playlist)

    # ── Renommer / supprimer ─────────────────────────────────────────────────

    def _prompt_rename(self):
        if self._playlist is None:
            return
        dialog = Adw.AlertDialog(
            heading=_('Renommer la playlist'),
            body=_('Nouveau nom de la playlist.'))
        entry = Gtk.Entry(text=self._playlist.name, activates_default=True)
        dialog.set_extra_child(entry)
        dialog.add_response('cancel', _('Annuler'))
        dialog.add_response('save', _('Enregistrer'))
        dialog.set_response_appearance(
            'save', Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response('save')
        dialog.set_close_response('cancel')
        dialog.connect('response', self._on_rename_response, entry)
        dialog.present(self.get_root())

    def _on_rename_response(self, _dialog, response, entry):
        if response != 'save':
            return
        name = entry.get_text().strip()
        if not name:
            return
        task = self._rename(name)
        asyncio.get_event_loop_policy().get_event_loop().create_task(task)

    async def _rename(self, name):
        client = self._app.get_client()
        if client is None:
            return
        try:
            playlist = await client.rename_playlist(self._playlist_id, name)
        except ApiError:
            return
        self._apply(client, playlist)
        if self.on_renamed is not None:
            self.on_renamed(playlist.name)

    def _prompt_delete(self):
        if self._playlist is None:
            return
        dialog = Adw.AlertDialog(
            heading=_('Supprimer la playlist ?'),
            body=_('« {name} » sera définitivement supprimée.').format(
                name=self._playlist.name))
        dialog.add_response('cancel', _('Annuler'))
        dialog.add_response('delete', _('Supprimer'))
        dialog.set_response_appearance(
            'delete', Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_close_response('cancel')
        dialog.connect('response', self._on_delete_response)
        dialog.present(self.get_root())

    def _on_delete_response(self, _dialog, response):
        if response != 'delete':
            return
        task = self._delete()
        asyncio.get_event_loop_policy().get_event_loop().create_task(task)

    async def _delete(self):
        client = self._app.get_client()
        if client is None:
            return
        try:
            await client.delete_playlist(self._playlist_id)
        except ApiError:
            return
        if self.on_deleted is not None:
            self.on_deleted()

    # ── Favoris ──────────────────────────────────────────────────────────────

    def _on_favorite_toggled(self, button, client, track_id):
        is_favorite = button.get_active()
        button.set_icon_name(
            'starred-symbolic' if is_favorite else 'non-starred-symbolic')
        task = self._set_favorite(client, track_id, is_favorite)
        asyncio.get_event_loop_policy().get_event_loop().create_task(task)

    async def _set_favorite(self, client, track_id, is_favorite):
        try:
            await client.set_favorite(track_id, is_favorite)
        except ApiError:
            pass  # best-effort, comme le reste de l'app

    # ── Surbrillance de la piste en cours ────────────────────────────────────

    def _on_playback_state(self, state):
        self._current_track_id = state.current_track_id
        self._is_playing = state.is_playing
        for row, track_id in self._row_track_ids.items():
            if state.current_track_id == track_id:
                row.add_css_class('accent')
            else:
                row.remove_css_class('accent')

    # ── Pochettes ────────────────────────────────────────────────────────────

    async def _load_cover(self, picture, url):
        client = self._app.get_client()
        if client is None:
            return
        try:
            data = await client.fetch_bytes(url)
            texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(data))
        except (ApiError, GLib.Error):
            return
        self._textures[url] = texture
        if getattr(picture, '_wanted_url', None) == url:
            picture.set_paintable(texture)
