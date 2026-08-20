"""Page Bibliothèque : la grille des albums du serveur.

Parité avec l'écran Albums du client Android (AlbumListScreen) : toute la
collection possédée est chargée en un seul appel (limit=10000, pas de
pagination), chaque carte montre la pochette carrée, le titre puis
l'artiste. Le tri (artiste A→Z ou année récente d'abord) est persisté dans
la clé GSettings sort-order.

Sur une collection de plusieurs centaines d'albums, faire défiler ne suffit
plus — trois aides s'ajoutent à la grille, toutes locales (la collection est
déjà entièrement en mémoire, donc aucune ne redemande le serveur) :

  · un champ de filtrage qui restreint la vue à la frappe, sur le titre et
    l'artiste, sans tenir compte de la casse ni des accents ;
  · une barre d'initiales A–Z qui saute au premier artiste d'une lettre ;
  · une vue « Artistes » : une liste compacte (nom + nombre d'albums) d'où
    un clic ramène à la grille filtrée sur cet artiste.

Grouper les pochettes en sections DANS la grille a été essayé puis écarté :
près de la moitié des artistes d'une collection réelle n'ont qu'un album, et
chaque section finit sa dernière rangée à moitié vide — le défilement double
au lieu de diminuer. D'où la liste d'artistes séparée, qui tient une ligne
par artiste.

La barre A–Z suppose un classement par artiste : elle n'est proposée qu'avec
le tri « Par artiste ».

Code frontière (exclu de la couverture) : les décisions pures — paramètres
de tri, normalisation, filtrage, initiales, découpe en sections — vivent
dans jewelbox.core.library, testé.
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


class _ArtistItem(GObject.Object):
    """Enveloppe GObject d'une entrée (nom, nombre d'albums)."""

    def __init__(self, name, count):
        super().__init__()
        self.name = name
        self.count = count


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
        self._artist_store = Gio.ListStore(item_type=_ArtistItem)
        self._load_generation = 0
        # La collection complète telle que reçue du serveur : le filtrage et la
        # liste d'artistes travaillent dessus, _store ne portant que ce qui est
        # affiché à l'instant T.
        self._albums = []
        self._query = ''
        # Artiste sélectionné depuis la vue Artistes, ou None (toute la
        # collection). Indépendant du filtre texte, qui s'y applique en plus.
        self._artist_filter = None
        self._search_timeout = None
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
        # Gardé : la barre d'initiales lui demande de défiler (scroll_to).
        self._grid = grid

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

        self._count_label = Gtk.Label(css_classes=['dim-label'], xalign=0)

        # Filtre local : la collection entière étant déjà en mémoire, chaque
        # frappe se contente de recalculer quels albums afficher — aucun appel
        # serveur, contrairement à l'onglet Recherche qui interroge le serveur
        # et change de page.
        self._search = Gtk.SearchEntry(
            placeholder_text=_('Filtrer par titre ou artiste…'),
            hexpand=True)
        self._search.connect('search-changed', self._on_search_changed)

        # Bascule Albums / Artistes. La liste d'artistes n'est qu'une façon
        # d'atteindre la grille : en choisir un revient à l'onglet Albums
        # filtré sur lui.
        self._view_switcher = Adw.ToggleGroup()
        self._view_switcher.add(Adw.Toggle(name='albums', label=_('Albums')))
        self._view_switcher.add(Adw.Toggle(name='artists', label=_('Artistes')))
        self._view_switcher.set_active_name('albums')
        self._view_switcher.connect(
            'notify::active-name', self._on_view_changed)

        toolbar = Gtk.Box(spacing=12, margin_start=12, margin_end=12,
                          margin_top=6, margin_bottom=6)
        toolbar.append(self._view_switcher)
        toolbar.append(self._search)
        toolbar.append(self._sort_dropdown)

        # Bandeau de l'artiste sélectionné : sans lui, une grille filtrée sur
        # un artiste serait indiscernable d'une collection qui aurait rétréci.
        self._artist_banner = Adw.Bin(visible=False)
        self._artist_banner_label = Gtk.Label(
            xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END,
            css_classes=['heading'])
        clear_artist = Gtk.Button(
            icon_name='window-close-symbolic', css_classes=['flat', 'circular'],
            valign=Gtk.Align.CENTER, tooltip_text=_('Voir tous les artistes'))
        clear_artist.connect('clicked', lambda *_a: self._select_artist(None))
        banner_box = Gtk.Box(spacing=8, margin_start=12, margin_end=12,
                             margin_bottom=6)
        banner_box.append(self._artist_banner_label)
        banner_box.append(clear_artist)
        self._artist_banner.set_child(banner_box)

        # Barre d'initiales : seulement pertinente sur un classement par
        # artiste (voir _refresh_initials).
        #
        # 27 boutons alignés demandent plus de largeur que le plancher de la
        # fenêtre (420px) : sans précaution, la barre imposerait à elle seule
        # une largeur minimale de près de 1000px et empêcherait de rétrécir la
        # fenêtre. Elle est donc posée dans un ScrolledWindow qui ne défile
        # qu'à l'horizontale et ne réclame rien : sur une fenêtre étroite, on
        # fait glisser les lettres ; sur une large, elles tiennent toutes et
        # restent centrées.
        letters = Gtk.Box(spacing=0, halign=Gtk.Align.CENTER)
        self._initials_bar = Gtk.ScrolledWindow(
            child=letters,
            hscrollbar_policy=Gtk.PolicyType.EXTERNAL,
            vscrollbar_policy=Gtk.PolicyType.NEVER,
            propagate_natural_height=True,
            margin_start=12, margin_end=12, margin_bottom=6)
        self._initial_buttons = {}
        for initial in core.INITIALS:
            button = Gtk.Button(
                label=initial, css_classes=['flat', 'jewelbox-initial'],
                tooltip_text=_('Aller aux artistes en « {letter} »').format(
                    letter=initial))
            button.connect('clicked', self._on_initial_clicked, initial)
            self._initial_buttons[initial] = button
            letters.append(button)

        # « Aucun résultat » pour un filtre trop restrictif : distinct de
        # l'état « bibliothèque vide », qui parle du serveur et non du filtre.
        self._empty_filter = Adw.StatusPage(
            icon_name='system-search-symbolic',
            title=_('Aucun résultat'),
            vexpand=True)

        self._grid_stack = Gtk.Stack(
            transition_type=Gtk.StackTransitionType.CROSSFADE, vexpand=True)
        self._grid_stack.add_named(scrolled, 'results')
        self._grid_stack.add_named(self._empty_filter, 'empty')

        # ── Vue « Artistes » ─────────────────────────────────────────────────
        artist_factory = Gtk.SignalListItemFactory()
        artist_factory.connect('setup', self._on_artist_setup)
        artist_factory.connect('bind', self._on_artist_bind)
        artist_list = Gtk.ListView(
            model=Gtk.NoSelection(model=self._artist_store),
            factory=artist_factory, single_click_activate=True)
        artist_list.add_css_class('navigation-sidebar')
        artist_list.connect('activate', self._on_artist_activated)
        self._artist_scrolled = Gtk.ScrolledWindow(
            child=artist_list, hscrollbar_policy=Gtk.PolicyType.NEVER,
            vexpand=True)

        self._views = Gtk.Stack(
            transition_type=Gtk.StackTransitionType.CROSSFADE, vexpand=True)
        self._views.add_named(self._grid_stack, 'albums')
        self._views.add_named(self._artist_scrolled, 'artists')

        count_row = Gtk.Box(margin_start=12, margin_end=12, margin_bottom=6)
        count_row.append(self._count_label)

        grid_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        grid_page.append(toolbar)
        grid_page.append(self._artist_banner)
        grid_page.append(self._initials_bar)
        grid_page.append(count_row)
        grid_page.append(self._views)
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

        if not page.data:
            self._albums = []
            self._store.remove_all()
            self._show_status(
                icon='media-optical-symbolic',
                title=_('Bibliothèque vide'),
                description=_('Aucun album avec fichiers audio sur ce '
                              'serveur pour le moment.'),
                button_label=_('Actualiser'),
                button_action=None)
            return

        self._albums = list(page.data)
        # Un rechargement (changement de tri, retour des Préférences) repart
        # de la collection entière : l'artiste retenu a pu disparaître, et le
        # tri par année rendrait de toute façon la barre A–Z sans objet.
        self._artist_filter = None
        self._populate_artists()
        self._apply_filter()
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

    def _on_card_play_clicked(self, _button, list_item):
        # Les albums de la grille ne portent que leurs métadonnées (pas les
        # pistes : client.albums() ne les inclut pas). On charge donc l'album
        # complet avant de lancer sa lecture.
        item = list_item.get_item()
        if item is None:
            return
        task = self._play_album(item.album.id)
        asyncio.get_event_loop_policy().get_event_loop().create_task(task)

    async def _play_album(self, album_id):
        client = self._app.get_client()
        playback = self._app.playback
        if client is None or playback is None:
            return
        try:
            album = await client.album(album_id)
        except ApiError:
            return  # best-effort, comme le reste de l'app
        first = next((t for t in album.tracks if t.has_file), None)
        if first is not None:
            playback.play_album(album, first.id)

    def _on_status_clicked(self, _button):
        if self._status_action:
            self.activate_action(self._status_action, None)
        else:
            self.reload()

    # ── Filtrage local ───────────────────────────────────────────────────────

    def _visible_albums(self):
        """Les albums retenus par l'artiste sélectionné puis par le filtre
        texte. Les deux se cumulent : choisir un artiste puis taper affine
        à l'intérieur de sa discographie."""
        albums = self._albums
        if self._artist_filter is not None:
            albums = core.albums_by_artist(albums, self._artist_filter)
        return core.filter_albums(albums, self._query)

    def _apply_filter(self):
        """Reconstruit la grille à partir de la collection en mémoire."""
        visible = self._visible_albums()
        self._store.remove_all()
        for album in visible:
            self._store.append(_AlbumItem(album))
        self._update_count(len(visible))
        self._refresh_initials(visible)
        self._grid_stack.set_visible_child_name(
            'results' if visible else 'empty')
        if not visible:
            self._empty_filter.set_description(
                _('Aucun album ne correspond à « {query} ».').format(
                    query=GLib.markup_escape_text(self._query)))

    def _update_count(self, shown):
        total = len(self._albums)
        # Tant que rien n'est filtré, le compte reste celui de la collection —
        # afficher « 645 sur 645 » serait du bruit.
        if shown == total:
            label = (_('{count} albums').format(count=total) if total > 1
                     else _('1 album'))
        else:
            label = _('{shown} sur {total} albums').format(
                shown=shown, total=total)
        self._count_label.set_label(label)

    def _on_search_changed(self, entry):
        # Anti-rebond : filtrer 645 albums prend quelques millisecondes, et
        # reconstruire le ListStore bien plus — inutile de le refaire à chaque
        # caractère d'une frappe rapide. Gtk.SearchEntry émet déjà « search-
        # changed » avec un léger délai, ce timeout ne fait que l'allonger.
        if self._search_timeout is not None:
            GLib.source_remove(self._search_timeout)
        self._search_timeout = GLib.timeout_add(
            120, self._commit_search, entry.get_text())

    def _commit_search(self, text):
        self._search_timeout = None
        self._query = text
        if self._current_view() == 'artists':
            self._populate_artists()
        else:
            self._apply_filter()
        return False  # one-shot

    # ── Barre d'initiales ────────────────────────────────────────────────────

    def _refresh_initials(self, visible):
        """Active les lettres présentes, désactive les autres.

        La barre entière disparaît hors du tri par artiste : sur un classement
        par année, sauter à « M » atterrirait n'importe où. Elle disparaît
        aussi quand un artiste est déjà sélectionné — il n'y a plus qu'une
        initiale à atteindre.
        """
        relevant = (self._settings_sort() == 'artist'
                    and self._artist_filter is None)
        self._initials_bar.set_visible(relevant)
        if not relevant:
            return
        available = core.available_initials(visible)
        for initial, button in self._initial_buttons.items():
            # Désactivées plutôt que masquées : la rangée garde une largeur
            # stable quand le filtre change, sans sauter sous le curseur.
            button.set_sensitive(initial in available)

    def _on_initial_clicked(self, _button, initial):
        index = core.first_index_for_initial(self._visible_albums(), initial)
        if index is None:
            return
        # scroll_to sur la GridView amène l'album en haut de la zone visible.
        self._grid.scroll_to(index, Gtk.ListScrollFlags.NONE, None)

    # ── Vue « Artistes » ─────────────────────────────────────────────────────

    def _current_view(self):
        return self._view_switcher.get_active_name() or 'albums'

    def _on_view_changed(self, *_args):
        view = self._current_view()
        self._views.set_visible_child_name(view)
        if view == 'artists':
            self._populate_artists()
        else:
            self._apply_filter()
        # La barre A–Z appartient à la grille ; la liste d'artistes est déjà
        # alphabétique et bien plus courte.
        self._initials_bar.set_visible(
            view == 'albums' and self._settings_sort() == 'artist'
            and self._artist_filter is None)

    def _populate_artists(self):
        entries = core.filter_artist_entries(
            core.artist_entries(self._albums), self._query)
        self._artist_store.remove_all()
        for name, count in entries:
            self._artist_store.append(_ArtistItem(name, count))
        if self._current_view() == 'artists':
            self._count_label.set_label(
                _('{count} artistes').format(count=len(entries))
                if len(entries) != 1 else _('1 artiste'))

    def _on_artist_setup(self, _factory, list_item):
        name = Gtk.Label(xalign=0, hexpand=True,
                         ellipsize=Pango.EllipsizeMode.END)
        count = Gtk.Label(css_classes=['dim-label', 'numeric'])
        arrow = Gtk.Image(icon_name='go-next-symbolic',
                          css_classes=['dim-label'])
        row = Gtk.Box(spacing=12, margin_top=8, margin_bottom=8,
                      margin_start=12, margin_end=12)
        row.append(name)
        row.append(count)
        row.append(arrow)
        list_item.set_child(row)
        list_item.name_label, list_item.count_label = name, count

    def _on_artist_bind(self, _factory, list_item):
        item = list_item.get_item()
        list_item.name_label.set_label(item.name or _('Artiste inconnu'))
        list_item.name_label.set_tooltip_text(item.name)
        list_item.count_label.set_label(
            _('{count} albums').format(count=item.count) if item.count > 1
            else _('1 album'))

    def _on_artist_activated(self, _list, position):
        item = self._artist_store.get_item(position)
        if item is not None:
            self._select_artist(item.name)

    def _select_artist(self, name):
        """Bascule sur la grille filtrée sur cet artiste (None = tout).

        Le filtre texte est effacé au passage : il servait à trouver l'artiste
        dans la liste, le garder masquerait une partie de sa discographie.
        """
        self._artist_filter = name
        self._query = ''
        if self._search.get_text():
            self._search.set_text('')   # réémet search-changed, sans effet ici
        self._artist_banner.set_visible(name is not None)
        if name is not None:
            self._artist_banner_label.set_label(name or _('Artiste inconnu'))
        self._view_switcher.set_active_name(
            'albums' if name is not None else 'artists')
        self._apply_filter()

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

        # Bouton rond bleu « Lire l'album » posé en surimpression sur la
        # pochette (parité avec le bouton play flottant des cartes Android).
        # Un clic dessus lance l'album ; il ne remonte pas jusqu'à la carte,
        # donc l'ouverture de la fiche (simple clic) n'est pas déclenchée.
        play = Gtk.Button(
            icon_name='media-playback-start-symbolic',
            halign=Gtk.Align.END, valign=Gtk.Align.END,
            margin_end=6, margin_bottom=6,
            tooltip_text=_('Lire l’album'),
            css_classes=['circular', 'jewelbox-cover-play'])
        play.connect('clicked', self._on_card_play_clicked, list_item)

        overlay = Gtk.Overlay(child=cover, halign=Gtk.Align.CENTER)
        overlay.add_overlay(play)

        title = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END,
                          max_width_chars=18,
                          css_classes=['caption-heading'])
        artist = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END,
                           max_width_chars=18,
                           css_classes=['caption', 'dim-label'])

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2,
                       width_request=160, halign=Gtk.Align.CENTER)
        card.append(overlay)
        card.append(title)
        card.append(artist)

        list_item.set_child(card)
        list_item.cover, list_item.title, list_item.artist = (
            cover, title, artist)
        list_item.play = play

    def _on_card_bind(self, _factory, list_item):
        album = list_item.get_item().album
        list_item.title.set_label(album.title)
        list_item.title.set_tooltip_text(album.title)
        list_item.artist.set_label(album.artist.name)
        # Bouton « Lire l'album » seulement quand l'album a des pistes
        # jouables : sinon un clic ne lancerait rien. has_audio est fourni
        # par le serveur même en liste (voir Album.has_audio). La cellule
        # étant recyclée, on repositionne la visibilité à chaque bind.
        list_item.play.set_visible(album.has_audio)

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
