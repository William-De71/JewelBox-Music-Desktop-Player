"""Page Bibliothèque : la grille des albums du serveur.

Parité avec l'écran Albums du client Android (AlbumListScreen) : toute la
collection possédée est chargée en un seul appel (limit=10000, pas de
pagination), chaque carte montre la pochette carrée, le titre puis
l'artiste. Le tri (artiste A→Z ou année récente d'abord) est persisté dans
la clé GSettings sort-order.

Code frontière (exclu de la couverture) : les décisions pures — paramètres
de tri, positions du menu — vivent dans jewelbox.core.library, testé.
"""

import asyncio
from gettext import gettext as _

from gi.repository import Adw, Gdk, GLib, GObject, Gio, Gtk, Pango

from jewelbox.api.client import ApiError
from jewelbox.core import library as core


class _AlbumItem(GObject.Object):
    """Enveloppe GObject d'un modèle Album pour Gio.ListStore."""

    def __init__(self, album):
        super().__init__()
        self.album = album


class LibraryPage(Gtk.Stack):
    """Quatre états : message (sans serveur / erreur / vide), chargement,
    grille. Le rechargement est déclenché par la fenêtre (construction,
    fermeture des Préférences) et par le menu de tri."""

    def __init__(self, application):
        super().__init__(
            transition_type=Gtk.StackTransitionType.CROSSFADE)
        self._app = application
        self._textures = {}          # url → Gdk.Texture (cache session)
        self._store = Gio.ListStore(item_type=_AlbumItem)
        self._load_generation = 0
        # Appelé avec l'id de l'album activé (double-clic / Entrée).
        self.on_album_activated = None

        # ── État « message » (sans serveur, erreur, bibliothèque vide) ──────
        self._status = Adw.StatusPage()
        self._status_action = None   # action à activer, sinon reload()
        self._status_button = Gtk.Button(
            halign=Gtk.Align.CENTER, css_classes=['pill', 'suggested-action'])
        self._status_button.connect('clicked', self._on_status_clicked)
        self._status.set_child(self._status_button)
        self.add_named(self._status, 'status')

        # ── État « chargement » ──────────────────────────────────────────────
        loading = Adw.StatusPage(title=_('Chargement de la bibliothèque…'))
        loading.set_child(Adw.Spinner(
            width_request=48, height_request=48, halign=Gtk.Align.CENTER))
        self.add_named(loading, 'loading')

        # ── État « grille » ──────────────────────────────────────────────────
        factory = Gtk.SignalListItemFactory()
        factory.connect('setup', self._on_card_setup)
        factory.connect('bind', self._on_card_bind)

        grid = Gtk.GridView(
            model=Gtk.NoSelection(model=self._store),
            factory=factory,
            min_columns=2,
            max_columns=8,
            # Simple clic pour ouvrir un album : le double-clic par défaut
            # de GridView ne correspond pas à une grille d'albums cliquables.
            single_click_activate=True,
        )
        grid.add_css_class('navigation-sidebar')
        grid.connect('activate', self._on_activate)

        scrolled = Gtk.ScrolledWindow(
            child=grid,
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vexpand=True,
        )

        self._sort_dropdown = Gtk.DropDown.new_from_strings(
            [_('Par artiste'), _('Par année')])
        self._sort_dropdown.set_tooltip_text(_('Tri de la bibliothèque'))
        self._sort_dropdown.set_selected(
            core.sort_position(self._settings_sort()))
        self._sort_handler = self._sort_dropdown.connect(
            'notify::selected', self._on_sort_changed)

        self._count_label = Gtk.Label(css_classes=['dim-label'], xalign=0,
                                      hexpand=True)

        toolbar = Gtk.Box(spacing=12, margin_start=12, margin_end=12,
                          margin_top=6, margin_bottom=6)
        toolbar.append(self._count_label)
        toolbar.append(self._sort_dropdown)

        grid_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        grid_page.append(toolbar)
        grid_page.append(scrolled)
        self.add_named(grid_page, 'grid')
        # Pas de reload() ici : la fenêtre le déclenche via
        # _refresh_server_hint() une fois toute l'UI construite.

    # ── Chargement ───────────────────────────────────────────────────────────

    def reload(self):
        """(Re)charge la collection depuis le serveur configuré."""
        client = self._app.get_client()
        if client is None:
            self._show_status(
                icon='network-server-symbolic',
                title=_('Aucun serveur configuré'),
                description=_('Indiquez l’adresse de votre serveur JewelBox '
                              'pour parcourir votre collection.'),
                button_label=_('Ouvrir les Préférences'),
                button_action='app.preferences')
            return
        self._load_generation += 1
        task = self._load(client, self._load_generation)
        asyncio.get_event_loop_policy().get_event_loop().create_task(task)

    async def _load(self, client, generation):
        self.set_visible_child_name('loading')
        try:
            page = await client.albums(
                page=1, limit=core.FETCH_LIMIT,
                **core.sort_params(self._settings_sort()))
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

        self._store.remove_all()
        for album in page.data:
            self._store.append(_AlbumItem(album))

        if not page.data:
            self._show_status(
                icon='media-optical-symbolic',
                title=_('Bibliothèque vide'),
                description=_('Aucun album avec fichiers audio sur ce '
                              'serveur pour le moment.'),
                button_label=_('Actualiser'),
                button_action=None)
            return

        total = page.pagination.total or len(page.data)
        self._count_label.set_label(
            _('{count} albums').format(count=total) if total > 1
            else _('1 album'))
        self.set_visible_child_name('grid')

    def _show_status(self, icon, title, description, button_label,
                     button_action):
        self._status.set_icon_name(icon)
        self._status.set_title(title)
        # description est interprétée comme du markup Pango : un message
        # d'erreur serveur (URL avec « & », etc.) doit être échappé, sinon
        # Gtk plante l'affichage au lieu de juste montrer du texte brut.
        self._status.set_description(GLib.markup_escape_text(description))
        self._status_button.set_label(button_label)
        self._status_action = button_action
        self.set_visible_child_name('status')

    def _on_activate(self, _grid, position):
        item = self._store.get_item(position)
        if item is not None and self.on_album_activated is not None:
            self.on_album_activated(item.album.id)

    def _on_status_clicked(self, _button):
        if self._status_action:
            self.activate_action(self._status_action, None)
        else:
            self.reload()

    # ── Tri ──────────────────────────────────────────────────────────────────

    def _settings_sort(self):
        return self._app.settings.get_string('sort-order')

    def _on_sort_changed(self, dropdown, _pspec):
        self._app.settings.set_string(
            'sort-order', core.sort_from_position(dropdown.get_selected()))
        self.reload()

    # ── Cartes ───────────────────────────────────────────────────────────────

    def _on_card_setup(self, _factory, list_item):
        # Taille explicite : dans une GridView la hauteur de rangée vient de
        # la demande minimale des cellules, et une Picture vide demande 0 —
        # sans cela les pochettes seraient allouées à hauteur nulle.
        cover = Gtk.Picture(
            content_fit=Gtk.ContentFit.COVER,
            width_request=160, height_request=160,
            halign=Gtk.Align.CENTER,
            overflow=Gtk.Overflow.HIDDEN,
        )
        cover.add_css_class('jewelbox-cover')

        title = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END,
                          max_width_chars=18,
                          css_classes=['caption-heading'])
        artist = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END,
                           max_width_chars=18,
                           css_classes=['caption', 'dim-label'])

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2,
                       width_request=160, halign=Gtk.Align.CENTER)
        card.append(cover)
        card.append(title)
        card.append(artist)

        list_item.set_child(card)
        list_item.cover, list_item.title, list_item.artist = (
            cover, title, artist)

    def _on_card_bind(self, _factory, list_item):
        album = list_item.get_item().album
        list_item.title.set_label(album.title)
        list_item.title.set_tooltip_text(album.title)
        list_item.artist.set_label(album.artist.name)

        cover = list_item.cover
        client = self._app.get_client()
        url = client.resolve_cover(album.cover_url) if client else None
        cover._wanted_url = url
        cover.set_paintable(self._textures.get(url))
        if url and url not in self._textures:
            task = self._load_cover(cover, url)
            asyncio.get_event_loop_policy().get_event_loop().create_task(task)

    async def _load_cover(self, picture, url):
        client = self._app.get_client()
        if client is None:
            return
        try:
            data = await client.fetch_bytes(url)
            texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(data))
        except (ApiError, GLib.Error):
            return  # pas de pochette : le fond neutre reste affiché
        self._textures[url] = texture
        # La cellule a pu être recyclée pour un autre album entre-temps.
        if getattr(picture, '_wanted_url', None) == url:
            picture.set_paintable(texture)
