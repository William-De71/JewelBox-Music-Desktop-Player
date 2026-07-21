"""Onglet Playlists : listes intelligentes puis playlists utilisateur.

Parité avec PlaylistsScreen côté Android : deux sections empilées. D'abord
« Listes intelligentes » — une ligne par liste renvoyée par le serveur, dans
l'ordre canonique (voir smart_specs), avec son icône, son libellé et le nombre
de pistes. Ensuite « Mes playlists » — les playlists utilisateur, chacune avec
un menu (renommer / supprimer) ; un bouton « + » dans la barre d'outils crée
une playlist. Un clic ouvre la fiche correspondante (déléguée à la fenêtre).

Les deux listes sont chargées en parallèle : GET /api/smart-playlists et
GET /api/playlists. Une liste intelligente absente de la réponse serveur n'est
pas affichée (le serveur reste la source de vérité des clés existantes).

Code frontière (exclu de la couverture) : cette page ne fait qu'afficher des
listes chargées par api.client et déléguer l'ouverture à la fenêtre / les
mutations à JewelBoxClient, tous testés séparément.
"""

import asyncio
from gettext import gettext as _

from gi.repository import Adw, Gio, GLib, Gtk, Pango

from jewelbox.api.client import ApiError
from jewelbox.core.formats import format_duration
from jewelbox.ui.smart_specs import smart_specs


class PlaylistsPage(Gtk.Stack):
    """États : message (sans serveur / erreur), chargement, contenu. Le
    rechargement est déclenché par la fenêtre (affichage de l'onglet, fermeture
    des Préférences) et après une mutation (création)."""

    def __init__(self, application):
        super().__init__(
            transition_type=Gtk.StackTransitionType.CROSSFADE)
        self._app = application
        self._load_generation = 0
        # Appelés avec l'identifiant de l'élément activé.
        self.on_playlist_activated = None   # (playlist_id: int)
        self.on_smart_activated = None      # (key: str)

        # ── État « message » (sans serveur, erreur) ──────────────────────────
        self._status = Adw.StatusPage()
        self._status_action = None
        self._status_button = Gtk.Button(
            halign=Gtk.Align.CENTER, css_classes=['pill', 'suggested-action'])
        self._status_button.connect('clicked', self._on_status_clicked)
        self._status.set_child(self._status_button)
        self.add_named(self._status, 'status')

        # ── État « chargement » ──────────────────────────────────────────────
        loading = Adw.StatusPage(title=_('Chargement des playlists…'))
        loading.set_child(Adw.Spinner(
            width_request=48, height_request=48, halign=Gtk.Align.CENTER))
        self.add_named(loading, 'loading')

        # ── État « contenu » ─────────────────────────────────────────────────
        self._smart_box = Gtk.ListBox(
            selection_mode=Gtk.SelectionMode.NONE, css_classes=['boxed-list'])
        self._smart_box.connect('row-activated', self._on_smart_row_activated)
        self._smart_group = self._build_group(
            _('Listes intelligentes'), self._smart_box)

        self._playlists_box = Gtk.ListBox(
            selection_mode=Gtk.SelectionMode.NONE, css_classes=['boxed-list'])
        self._playlists_box.connect(
            'row-activated', self._on_playlist_row_activated)

        # Bouton « + » aligné à droite du titre « Mes playlists ».
        create = Gtk.Button(
            icon_name='list-add-symbolic', css_classes=['flat'],
            valign=Gtk.Align.CENTER, tooltip_text=_('Nouvelle playlist'))
        create.connect('clicked', lambda *_a: self._prompt_create())

        # Message affiché à la place de la liste quand aucune playlist.
        self._playlists_empty = self._build_empty_playlists()

        self._playlists_stack = Gtk.Stack(
            transition_type=Gtk.StackTransitionType.CROSSFADE)
        self._playlists_stack.add_named(self._playlists_box, 'list')
        self._playlists_stack.add_named(self._playlists_empty, 'empty')
        self._playlists_group = self._build_group(
            _('Mes playlists'), self._playlists_stack, action=create)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18,
                          margin_start=18, margin_end=18,
                          margin_top=18, margin_bottom=18)
        content.append(self._smart_group)
        content.append(self._playlists_group)

        self.add_named(
            Gtk.ScrolledWindow(child=content,
                               hscrollbar_policy=Gtk.PolicyType.NEVER,
                               vexpand=True),
            'content')

    def _build_group(self, title, child, action=None):
        """Un titre de section au-dessus de son contenu (parité SectionHeader,
        même motif que l'Accueil et la Recherche). action, s'il est fourni, est
        un bouton posé à droite du titre."""
        header = Gtk.Label(label=title, xalign=0, hexpand=True,
                           css_classes=['title-4'])
        title_row = Gtk.Box(spacing=8)
        title_row.append(header)
        if action is not None:
            title_row.append(action)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.append(title_row)
        box.append(child)
        return box

    def _build_empty_playlists(self):
        title = Gtk.Label(label=_('Aucune playlist'),
                          css_classes=['title-4'])
        subtitle = Gtk.Label(
            label=_('Créez votre première playlist pour organiser vos '
                    'écoutes'),
            wrap=True, justify=Gtk.Justification.CENTER,
            css_classes=['dim-label'])
        button = Gtk.Button(
            label=_('Nouvelle playlist'), halign=Gtk.Align.CENTER,
            css_classes=['pill', 'suggested-action'])
        button.connect('clicked', lambda *_a: self._prompt_create())
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                      margin_top=24, margin_bottom=24,
                      halign=Gtk.Align.CENTER)
        box.append(title)
        box.append(subtitle)
        box.append(button)
        return box

    # ── Chargement ───────────────────────────────────────────────────────────

    def reload(self):
        client = self._app.get_client()
        if client is None:
            self._show_status(
                icon='network-server-symbolic',
                title=_('Aucun serveur configuré'),
                description=_('Indiquez l’adresse de votre serveur JewelBox '
                              'pour retrouver vos playlists.'),
                button_label=_('Ouvrir les Préférences'),
                button_action='app.preferences')
            return
        self._load_generation += 1
        task = self._load(client, self._load_generation)
        asyncio.get_event_loop_policy().get_event_loop().create_task(task)

    async def _load(self, client, generation):
        self.set_visible_child_name('loading')
        try:
            playlists = await client.playlists()
        except ApiError as error:
            if generation != self._load_generation:
                return
            self._show_status(
                icon='network-error-symbolic',
                title=_('Serveur injoignable'),
                description=str(error),
                button_label=_('Réessayer'),
                button_action=None)
            return
        if generation != self._load_generation:
            return

        # Les listes intelligentes sont best-effort : sur un serveur qui ne les
        # expose pas (ou en cas d'erreur), la section reste simplement masquée
        # sans compromettre l'affichage des playlists utilisateur.
        try:
            smart = await client.smart_playlists()
        except ApiError:
            smart = ()
        if generation != self._load_generation:
            return

        self._populate_smart(smart)
        self._populate_playlists(playlists)
        self.set_visible_child_name('content')

    def _populate_smart(self, smart_metas):
        # On n'affiche que les clés réellement renvoyées par le serveur, dans
        # l'ordre canonique de smart_specs ; le compte vient du serveur.
        counts = {meta.key: meta.track_count for meta in smart_metas}
        while (row := self._smart_box.get_row_at_index(0)) is not None:
            self._smart_box.remove(row)
        shown = 0
        for spec in smart_specs():
            if spec.key not in counts:
                continue
            self._smart_box.append(
                self._build_smart_row(spec, counts[spec.key]))
            shown += 1
        self._smart_group.set_visible(shown > 0)

    def _populate_playlists(self, playlists):
        while (row := self._playlists_box.get_row_at_index(0)) is not None:
            self._playlists_box.remove(row)
        for playlist in playlists:
            self._playlists_box.append(self._build_playlist_row(playlist))
        self._playlists_stack.set_visible_child_name(
            'list' if playlists else 'empty')

    def _show_status(self, icon, title, description, button_label,
                     button_action):
        self._status.set_icon_name(icon)
        self._status.set_title(title)
        self._status.set_description(GLib.markup_escape_text(description))
        self._status_button.set_label(button_label)
        self._status_action = button_action
        self.set_visible_child_name('status')

    def _on_status_clicked(self, _button):
        if self._status_action:
            self.activate_action(self._status_action, None)
        else:
            self.reload()

    # ── Section « Listes intelligentes » ─────────────────────────────────────

    def _build_smart_row(self, spec, count):
        icon = Gtk.Image(icon_name=spec.icon)
        title = Gtk.Label(label=spec.label, xalign=0, hexpand=True,
                          css_classes=['heading'])
        count_label = Gtk.Label(
            label=(_('{count} pistes').format(count=count) if count != 1
                   else _('1 piste')),
            css_classes=['dim-label'])
        row_box = Gtk.Box(spacing=12, margin_top=10, margin_bottom=10,
                          margin_start=12, margin_end=12)
        row_box.append(icon)
        row_box.append(title)
        row_box.append(count_label)
        row = Gtk.ListBoxRow(activatable=True, child=row_box)
        row._smart_key = spec.key
        return row

    def _on_smart_row_activated(self, _listbox, row):
        key = getattr(row, '_smart_key', None)
        if key is not None and self.on_smart_activated is not None:
            self.on_smart_activated(key)

    # ── Section « Mes playlists » ────────────────────────────────────────────

    def _build_playlist_row(self, playlist):
        icon = Gtk.Image(icon_name='view-list-symbolic')

        title = Gtk.Label(label=playlist.name, xalign=0,
                          ellipsize=Pango.EllipsizeMode.END,
                          css_classes=['heading'])
        meta_parts = [
            _('{count} pistes').format(count=playlist.track_count)
            if playlist.track_count != 1 else _('1 piste')]
        if playlist.total_duration_seconds > 0:
            meta_parts.append(format_duration(playlist.total_duration_seconds))
        subtitle = Gtk.Label(label=' · '.join(meta_parts), xalign=0,
                             css_classes=['caption', 'dim-label'])
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2,
                       valign=Gtk.Align.CENTER, hexpand=True)
        text.append(title)
        text.append(subtitle)

        # Menu contextuel : renommer / supprimer, portés par des actions
        # locales à la page (voir install_actions) paramétrées par l'id.
        menu_button = Gtk.MenuButton(
            icon_name='view-more-symbolic', css_classes=['flat'],
            valign=Gtk.Align.CENTER, tooltip_text=_('Options'))
        menu = Gio.Menu()
        menu.append(_('Renommer'), f'playlists.rename::{playlist.id}')
        menu.append(_('Supprimer'), f'playlists.delete::{playlist.id}')
        menu_button.set_menu_model(menu)

        row_box = Gtk.Box(spacing=12, margin_top=8, margin_bottom=8,
                          margin_start=12, margin_end=8)
        row_box.append(icon)
        row_box.append(text)
        row_box.append(menu_button)

        row = Gtk.ListBoxRow(activatable=True, child=row_box)
        row._playlist_id = playlist.id
        row._playlist_name = playlist.name
        return row

    def _on_playlist_row_activated(self, _listbox, row):
        playlist_id = getattr(row, '_playlist_id', None)
        if playlist_id is not None and self.on_playlist_activated is not None:
            self.on_playlist_activated(playlist_id)

    # ── Actions du menu contextuel (renommer / supprimer) ────────────────────

    def install_actions(self):
        """Groupe d'actions « playlists » à insérer sur la page par la fenêtre.
        Chaque action porte l'id de playlist en paramètre chaîne."""
        group = Gio.SimpleActionGroup()
        for name, handler in (
            ('rename', self._on_rename_action),
            ('delete', self._on_delete_action),
        ):
            action = Gio.SimpleAction.new(name, GLib.VariantType.new('s'))
            action.connect('activate', handler)
            group.add_action(action)
        return group

    def _playlist_name(self, playlist_id):
        index = 0
        while (row := self._playlists_box.get_row_at_index(index)) is not None:
            if getattr(row, '_playlist_id', None) == playlist_id:
                return getattr(row, '_playlist_name', '')
            index += 1
        return ''

    def _on_rename_action(self, _action, param):
        playlist_id = int(param.get_string())
        self._prompt_rename(playlist_id, self._playlist_name(playlist_id))

    def _on_delete_action(self, _action, param):
        playlist_id = int(param.get_string())
        self._prompt_delete(playlist_id, self._playlist_name(playlist_id))

    # ── Créer / renommer / supprimer ─────────────────────────────────────────

    def _prompt_create(self):
        dialog = self._name_dialog(
            heading=_('Créer une playlist'), initial='')
        dialog.connect('response', self._on_create_response,
                       dialog.get_extra_child())
        dialog.present(self.get_root())

    def _on_create_response(self, _dialog, response, entry):
        if response != 'save':
            return
        name = entry.get_text().strip()
        if not name:
            return
        task = self._create(name)
        asyncio.get_event_loop_policy().get_event_loop().create_task(task)

    async def _create(self, name):
        client = self._app.get_client()
        if client is None:
            return
        try:
            playlist = await client.create_playlist(name)
        except ApiError:
            return
        # On ouvre la nouvelle playlist tout de suite (parité Android), puis la
        # liste se rafraîchira au retour sur l'onglet.
        if self.on_playlist_activated is not None:
            self.on_playlist_activated(playlist.id)
        self.reload()

    def _prompt_rename(self, playlist_id, current_name):
        dialog = self._name_dialog(
            heading=_('Renommer la playlist'), initial=current_name)
        dialog.connect('response', self._on_rename_response,
                       dialog.get_extra_child(), playlist_id)
        dialog.present(self.get_root())

    def _on_rename_response(self, _dialog, response, entry, playlist_id):
        if response != 'save':
            return
        name = entry.get_text().strip()
        if not name:
            return
        task = self._rename(playlist_id, name)
        asyncio.get_event_loop_policy().get_event_loop().create_task(task)

    async def _rename(self, playlist_id, name):
        client = self._app.get_client()
        if client is None:
            return
        try:
            await client.rename_playlist(playlist_id, name)
        except ApiError:
            return
        self.reload()

    def _prompt_delete(self, playlist_id, name):
        dialog = Adw.AlertDialog(
            heading=_('Supprimer la playlist ?'),
            body=_('« {name} » sera définitivement supprimée.').format(
                name=name))
        dialog.add_response('cancel', _('Annuler'))
        dialog.add_response('delete', _('Supprimer'))
        dialog.set_response_appearance(
            'delete', Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_close_response('cancel')
        dialog.connect('response', self._on_delete_response, playlist_id)
        dialog.present(self.get_root())

    def _on_delete_response(self, _dialog, response, playlist_id):
        if response != 'delete':
            return
        task = self._delete(playlist_id)
        asyncio.get_event_loop_policy().get_event_loop().create_task(task)

    async def _delete(self, playlist_id):
        client = self._app.get_client()
        if client is None:
            return
        try:
            await client.delete_playlist(playlist_id)
        except ApiError:
            return
        self.reload()

    def _name_dialog(self, heading, initial):
        dialog = Adw.AlertDialog(heading=heading)
        entry = Gtk.Entry(text=initial, activates_default=True)
        dialog.set_extra_child(entry)
        dialog.add_response('cancel', _('Annuler'))
        dialog.add_response('save', _('Enregistrer'))
        dialog.set_response_appearance(
            'save', Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response('save')
        dialog.set_close_response('cancel')
        return dialog
