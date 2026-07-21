"""Page Recherche : recherche instantanée dans la bibliothèque du serveur.

Parité avec l'écran Recherche du client Android (SearchScreen) : un champ de
recherche alimente GET /api/player/search (serveur >= 1.7) dès deux caractères
saisis, et affiche deux sections plafonnées côté serveur — les albums (grille
de pochettes carrées, comme la Bibliothèque) puis les pistes (liste jouable).

Un clic sur un album ouvre sa fiche ; un clic sur une piste lance la lecture de
la liste des pistes trouvées à partir de celle-ci (les autres pistes forment la
file), exactement comme une playlist. Les pistes sans fichier audio sont
estompées et non cliquables (classe .track-unavailable partagée).

Frappe au clavier : la requête est temporisée (voir _DEBOUNCE_MS) pour ne pas
lancer un appel réseau à chaque caractère ; seule la dernière saisie compte
(compteur de génération, comme la Bibliothèque et l'Accueil).

Code frontière (exclu de la couverture) : cette page ne fait qu'afficher des
SearchResults chargés par api.client et déléguer l'ouverture à la fenêtre / la
lecture à PlaybackSession, tous testés séparément.
"""

import asyncio
from gettext import gettext as _

from gi.repository import Adw, Gdk, GLib, GObject, Gio, Gtk, Pango

from jewelbox.api.client import ApiError
from jewelbox.core.formats import format_duration

# Le serveur exige q >= 2 caractères ; plus court, on n'appelle pas.
_MIN_QUERY = 2
# Temporisation de frappe : on attend une courte pause avant d'appeler, pour
# ne pas lancer une requête par caractère quand on tape vite.
_DEBOUNCE_MS = 350
_ALBUM_COVER_SIZE = 160
_TRACK_COVER_SIZE = 48


class _AlbumItem(GObject.Object):
    """Enveloppe GObject d'un modèle Album pour Gio.ListStore (même motif que
    la Bibliothèque et l'Accueil)."""

    def __init__(self, album):
        super().__init__()
        self.album = album


class SearchPage(Gtk.Stack):
    """États : invite (champ vide), chargement, aucun résultat, résultats.
    Un champ de recherche persistant coiffe tous les états. Sans serveur
    configuré, la fenêtre masque l'onglet au profit de la page « Aucun serveur »
    commune, donc cette page suppose toujours un client disponible."""

    def __init__(self, application):
        super().__init__(
            transition_type=Gtk.StackTransitionType.CROSSFADE)
        self._app = application
        self._textures = {}          # url → Gdk.Texture (cache session)
        self._search_generation = 0
        self._debounce_source = None  # id du timeout GLib en attente, sinon None
        self._current_tracks = ()     # pistes du dernier résultat, pour la file
        # Appelé avec l'id de l'album activé (clic sur une carte).
        self.on_album_activated = None

        # ── Champ de recherche (coiffe tous les états) ───────────────────────
        self._entry = Gtk.SearchEntry(
            placeholder_text=_('Rechercher un album, un artiste, un titre…'),
            hexpand=True)
        self._entry.connect('search-changed', self._on_search_changed)
        # Entrée relance immédiatement (annule la temporisation en cours).
        self._entry.connect('activate', lambda *_a: self._run_search_now())
        search_bar = Gtk.Box(margin_start=12, margin_end=12,
                             margin_top=6, margin_bottom=6)
        search_bar.append(self._entry)

        # ── Corps commutable sous le champ ───────────────────────────────────
        self._body = Gtk.Stack(
            transition_type=Gtk.StackTransitionType.CROSSFADE, vexpand=True)

        self._prompt = Adw.StatusPage(
            icon_name='system-search-symbolic',
            title=_('Rechercher dans votre bibliothèque'),
            description=_('Tapez au moins deux caractères pour trouver un '
                          'album, un artiste ou un titre.'))
        self._body.add_named(self._prompt, 'prompt')

        # État « sans serveur », identique aux autres onglets. La fenêtre le
        # déclenche via reload() ; ici on désactive aussi le champ, inutile
        # tant qu'aucun serveur n'est joignable.
        no_server = Adw.StatusPage(
            icon_name='network-server-symbolic',
            title=_('Aucun serveur configuré'),
            description=_('Indiquez l’adresse de votre serveur JewelBox '
                          'pour parcourir votre collection.'))
        no_server_button = Gtk.Button(
            label=_('Ouvrir les Préférences'), halign=Gtk.Align.CENTER,
            css_classes=['pill', 'suggested-action'],
            action_name='app.preferences')
        no_server.set_child(no_server_button)
        self._body.add_named(no_server, 'no-server')

        loading = Adw.StatusPage(title=_('Recherche en cours…'))
        loading.set_child(Adw.Spinner(
            width_request=48, height_request=48, halign=Gtk.Align.CENTER))
        self._body.add_named(loading, 'loading')

        self._empty = Adw.StatusPage(
            icon_name='system-search-symbolic',
            title=_('Aucun résultat'))
        self._body.add_named(self._empty, 'empty')

        self._error = Adw.StatusPage(
            icon_name='network-error-symbolic',
            title=_('Recherche impossible'))
        self._body.add_named(self._error, 'error')

        self._body.add_named(self._build_results(), 'results')
        self._body.set_visible_child_name('prompt')

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        outer.append(search_bar)
        outer.append(self._body)
        self.add_named(outer, 'main')
        self.set_visible_child_name('main')

    def reload(self):
        """Appelée par la fenêtre (construction, fermeture des Préférences).
        Sans serveur, bascule vers l'état « Aucun serveur configuré » et
        désactive le champ. Avec serveur, revient à l'invite (champ vidé) : on
        ne relance pas la dernière recherche, l'utilisateur repart de zéro."""
        self._cancel_debounce()
        self._search_generation += 1
        connected = self._app.get_client() is not None
        self._entry.set_sensitive(connected)
        if not connected:
            self._body.set_visible_child_name('no-server')
            return
        # Vider le champ réémet « search-changed », mais _on_search_changed
        # voit alors une requête trop courte et bascule seul vers l'invite sans
        # lancer d'appel — d'où pas de handler à suspendre ici.
        self._entry.set_text('')
        self._body.set_visible_child_name('prompt')

    def _build_results(self):
        # Section Albums : même grille de pochettes carrées que la Bibliothèque.
        factory = Gtk.SignalListItemFactory()
        factory.connect('setup', self._on_card_setup)
        factory.connect('bind', self._on_card_bind)
        self._albums_store = Gio.ListStore(item_type=_AlbumItem)
        self._albums_grid = Gtk.GridView(
            model=Gtk.NoSelection(model=self._albums_store),
            factory=factory,
            min_columns=2,
            max_columns=8,
            single_click_activate=True,
            vexpand=False)
        self._albums_grid.add_css_class('navigation-sidebar')
        self._albums_grid.connect('activate', self._on_album_activated)
        self._albums_group = self._build_group(_('Albums'), self._albums_grid)

        # Section Pistes : liste jouable, une ligne par piste.
        self._tracks_box = Gtk.ListBox(
            selection_mode=Gtk.SelectionMode.NONE,
            css_classes=['boxed-list'])
        self._tracks_box.connect('row-activated', self._on_track_activated)
        self._tracks_group = self._build_group(_('Pistes'), self._tracks_box)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18,
                          margin_start=18, margin_end=18,
                          margin_top=18, margin_bottom=18)
        content.append(self._albums_group)
        content.append(self._tracks_group)

        return Gtk.ScrolledWindow(
            child=content, hscrollbar_policy=Gtk.PolicyType.NEVER,
            vexpand=True)

    def _build_group(self, title, child):
        """Un titre de section au-dessus de son contenu (parité SectionHeader
        Android, même motif que l'Accueil)."""
        header = Gtk.Label(label=title, xalign=0, css_classes=['title-4'])
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.append(header)
        box.append(child)
        return box

    # ── Saisie et temporisation ──────────────────────────────────────────────

    def _on_search_changed(self, _entry):
        # Chaque frappe reporte l'appel : on annule la temporisation en attente
        # et on en repose une neuve, pour ne lancer la requête qu'à la pause.
        self._cancel_debounce()
        query = self._entry.get_text().strip()
        if len(query) < _MIN_QUERY:
            # Trop court (ou vidé) : retour à l'invite, aucun appel. Un résultat
            # tardif d'une requête plus longue est ignoré grâce à la génération.
            self._search_generation += 1
            self._body.set_visible_child_name('prompt')
            return
        self._debounce_source = GLib.timeout_add(
            _DEBOUNCE_MS, self._on_debounce_elapsed)

    def _on_debounce_elapsed(self):
        self._debounce_source = None
        self._run_search_now()
        return GLib.SOURCE_REMOVE

    def _cancel_debounce(self):
        if self._debounce_source is not None:
            GLib.source_remove(self._debounce_source)
            self._debounce_source = None

    def _run_search_now(self):
        self._cancel_debounce()
        query = self._entry.get_text().strip()
        if len(query) < _MIN_QUERY:
            return
        client = self._app.get_client()
        if client is None:
            return
        self._search_generation += 1
        task = self._search(client, query, self._search_generation)
        asyncio.get_event_loop_policy().get_event_loop().create_task(task)

    # ── Requête ───────────────────────────────────────────────────────────────

    async def _search(self, client, query, generation):
        self._body.set_visible_child_name('loading')
        try:
            results = await client.search(query)
        except ApiError as error:
            if generation != self._search_generation:
                return
            self._error.set_description(GLib.markup_escape_text(str(error)))
            self._body.set_visible_child_name('error')
            return
        if generation != self._search_generation:
            return

        if not results.albums and not results.tracks:
            # description recalculée à chaque fois : elle cite la requête.
            self._empty.set_description(GLib.markup_escape_text(
                _('Rien ne correspond à « {query} ».').format(query=query)))
            self._body.set_visible_child_name('empty')
            return

        self._populate_albums(results.albums)
        self._populate_tracks(client, results.tracks)
        self._body.set_visible_child_name('results')

    def _populate_albums(self, albums):
        self._albums_group.set_visible(bool(albums))
        self._albums_store.remove_all()
        for album in albums:
            self._albums_store.append(_AlbumItem(album))

    def _populate_tracks(self, client, tracks):
        self._current_tracks = tracks
        self._tracks_group.set_visible(bool(tracks))
        while (row := self._tracks_box.get_row_at_index(0)) is not None:
            self._tracks_box.remove(row)
        for index, track in enumerate(tracks):
            self._tracks_box.append(
                self._build_track_row(client, track, index))

    # ── Section Albums ────────────────────────────────────────────────────────

    def _on_card_setup(self, _factory, list_item):
        cover = Gtk.Picture(
            content_fit=Gtk.ContentFit.COVER,
            width_request=_ALBUM_COVER_SIZE, height_request=_ALBUM_COVER_SIZE,
            halign=Gtk.Align.CENTER,
            overflow=Gtk.Overflow.HIDDEN)
        cover.add_css_class('jewelbox-cover')

        title = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END,
                          max_width_chars=18,
                          css_classes=['caption-heading'])
        artist = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END,
                           max_width_chars=18,
                           css_classes=['caption', 'dim-label'])

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2,
                       width_request=_ALBUM_COVER_SIZE, halign=Gtk.Align.CENTER)
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

    def _on_album_activated(self, _grid, position):
        item = self._albums_store.get_item(position)
        if item is not None and self.on_album_activated is not None:
            self.on_album_activated(item.album.id)

    # ── Section Pistes ────────────────────────────────────────────────────────

    def _build_track_row(self, client, track, index):
        cover = Gtk.Picture(
            content_fit=Gtk.ContentFit.COVER,
            width_request=_TRACK_COVER_SIZE, height_request=_TRACK_COVER_SIZE,
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
        # album_title / artist_name portés par la QueueTrack : sous-titre riche
        # comme sur mobile (« Artiste · Album ») pour distinguer les homonymes.
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

        row_box = Gtk.Box(spacing=12, margin_top=6, margin_bottom=6,
                          margin_start=8, margin_end=12)
        row_box.append(cover)
        row_box.append(text)
        row_box.append(duration_label)

        row = Gtk.ListBoxRow(activatable=track.has_file, child=row_box)
        if not track.has_file:
            row.add_css_class('track-unavailable')
        else:
            # L'index dans la liste des résultats est porté par la row : au
            # clic, on lance la file entière à partir de cette piste (les autres
            # forment la suite), même motif que la fiche album.
            row._track_index = index
        return row

    def _on_track_activated(self, _listbox, row):
        index = getattr(row, '_track_index', None)
        if index is None:
            return
        playback = self._app.playback
        if playback is None:
            return
        # Toute la liste des pistes trouvées devient la file, démarrée sur la
        # piste cliquée. play_queue_tracks filtre d'abord les pistes sans
        # fichier, PUIS applique start_index sur la liste filtrée : on convertit
        # donc l'index (dans la liste complète) en index parmi les seules pistes
        # jouables, sinon des pistes non jouables situées avant décaleraient la
        # cible.
        playable_before = sum(
            1 for track in self._current_tracks[:index] if track.has_file)
        playback.play_queue_tracks(
            self._current_tracks, start_index=playable_before)

    # ── Pochettes ─────────────────────────────────────────────────────────────

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
        # La cellule a pu être recyclée pour un autre élément entre-temps.
        if getattr(picture, '_wanted_url', None) == url:
            picture.set_paintable(texture)
