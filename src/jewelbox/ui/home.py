"""Page Accueil : reprise d'écoute et suggestions.

Parité avec l'écran d'accueil du client Android (HomeScreen) : deux sections
alimentées par GET /api/player/home (serveur >= 1.9). D'abord « Récemment
écouté » — les 8 derniers albums/playlists joués, en tuiles horizontales
compactes (petite pochette, titre, sous-titre). Ensuite « Suggestions » —
une grille de pochettes carrées d'albums, comme la Bibliothèque.

Un clic sur un album (récent ou suggéré) ouvre sa fiche ; un clic sur une
playlist récente ouvre sa fiche (onglet Playlists). Le flux se recharge à
chaque affichage (la fenêtre le déclenche) et après le début d'une nouvelle
file, pour rester à jour.

Code frontière (exclu de la couverture) : cette page ne fait qu'afficher un
Home chargé par api.client et déléguer l'ouverture à la fenêtre / la lecture
à PlaybackSession, tous testés séparément.
"""

import asyncio
from gettext import gettext as _

from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk, Pango

from jewelbox.api.client import ApiError
from jewelbox.ui.smart_specs import smart_spec

_RECENT_COVER_SIZE = 56
_SUGGESTION_COVER_SIZE = 160
_LATEST_COVER_SIZE = 120
# Nombre de derniers albums enregistrés affichés en tête des suggestions.
_LATEST_COUNT = 5


class _AlbumItem(GObject.Object):
    """Enveloppe GObject d'un modèle Album pour Gio.ListStore (même motif que
    la Bibliothèque)."""

    def __init__(self, album):
        super().__init__()
        self.album = album


class _SuggestionsClamp(Gtk.Widget):
    """Enveloppe du GridView des suggestions, pour deux corrections de
    géométrie que le widget ne sait pas faire lui-même :

    - la largeur minimale rapportée est ramenée à deux colonnes : sans ça, le
      min_columns dynamique (voir _fit_suggestions_columns) verrouillerait la
      largeur minimale de la fenêtre au nombre de colonnes atteint en grand —
      impossible ensuite de la rétrécir, le contenu débordant à droite
      (warnings AdwToolbarView « exceeds width ») ;
    - chaque allocation signale sa largeur à la page : c'est le seul point
      fiable pour suivre un redimensionnement en GTK4 (aucun signal ni
      propriété « width » sur les widgets). La largeur du GridView lui-même
      peut être momentanément plus grande (clampée à son minimum le temps
      qu'une réduction converge) : celle de l'enveloppe fait foi.

    Gtk.Widget nu, sans gestionnaire de disposition : un conteneur à layout
    manager (Box…) court-circuite les vfuncs do_measure / do_size_allocate."""

    def __init__(self, grid, on_width_allocated):
        super().__init__(overflow=Gtk.Overflow.HIDDEN)
        self._grid = grid
        self._on_width_allocated = on_width_allocated
        grid.set_parent(self)

    def do_get_request_mode(self):
        return self._grid.get_request_mode()

    def do_measure(self, orientation, for_size):
        minimum, natural, min_baseline, nat_baseline = self._grid.measure(
            orientation, for_size)
        if orientation == Gtk.Orientation.HORIZONTAL:
            columns = max(1, self._grid.get_min_columns())
            minimum = min(minimum, (minimum // columns) * 2)
        return minimum, natural, min_baseline, nat_baseline

    def do_size_allocate(self, width, height, baseline):
        self._grid.allocate(width, height, baseline, None)
        self._on_width_allocated(width)

    def do_dispose(self):
        if self._grid is not None:
            self._grid.unparent()
            self._grid = None
        Gtk.Widget.do_dispose(self)


class HomePage(Gtk.Stack):
    """États : message (sans serveur / erreur / vide), chargement, contenu.
    Le rechargement est déclenché par la fenêtre (affichage de l'onglet,
    fermeture des Préférences, début d'une nouvelle file)."""

    def __init__(self, application):
        super().__init__(
            transition_type=Gtk.StackTransitionType.CROSSFADE)
        self._app = application
        self._textures = {}          # url → Gdk.Texture (cache session)
        self._load_generation = 0
        # Appelés avec l'identifiant de l'élément activé (id, ou clé pour smart).
        self.on_album_activated = None
        self.on_playlist_activated = None
        self.on_smart_activated = None

        # ── État « message » (sans serveur, erreur, accueil vide) ────────────
        self._status = Adw.StatusPage()
        self._status_action = None   # action à activer, sinon reload()
        self._status_button = Gtk.Button(
            halign=Gtk.Align.CENTER, css_classes=['pill', 'suggested-action'])
        self._status_button.connect('clicked', self._on_status_clicked)
        self._status.set_child(self._status_button)
        self.add_named(self._status, 'status')

        # ── État « chargement » ──────────────────────────────────────────────
        loading = Adw.StatusPage(title=_('Chargement de l’accueil…'))
        loading.set_child(Adw.Spinner(
            width_request=48, height_request=48, halign=Gtk.Align.CENTER))
        self.add_named(loading, 'loading')

        # ── État « contenu » ─────────────────────────────────────────────────
        # Deux colonnes de tuiles compactes (parité Android : chaque récent
        # occupe la moitié de la largeur). Un FlowBox plutôt qu'une ListBox,
        # qui resterait monocolonne. min = max = 2 pour tenir exactement deux
        # tuiles par rangée quelle que soit la largeur.
        self._recent_box = Gtk.FlowBox(
            selection_mode=Gtk.SelectionMode.NONE,
            min_children_per_line=2,
            max_children_per_line=2,
            column_spacing=6,
            row_spacing=4,
            homogeneous=True)
        self._recent_box.connect('child-activated', self._on_recent_activated)

        self._recent_group = self._build_group(
            _('Récemment écouté'), self._recent_box)

        factory = Gtk.SignalListItemFactory()
        factory.connect('setup', self._on_card_setup)
        factory.connect('bind', self._on_card_bind)
        self._suggestions_store = Gio.ListStore(item_type=_AlbumItem)
        self._suggestions_grid = Gtk.GridView(
            model=Gtk.NoSelection(model=self._suggestions_store),
            factory=factory,
            min_columns=2,
            max_columns=8,
            single_click_activate=True,
            vexpand=False,
        )
        self._suggestions_grid.add_css_class('navigation-sidebar')
        self._suggestions_grid.connect('activate', self._on_suggestion_activated)
        # Un GtkGridView calcule sa hauteur naturelle sur min_columns (2), donc
        # pour beaucoup d'albums il réserve bien plus de lignes que la largeur
        # n'en affiche — d'où un grand vide scrollable sous les suggestions. En
        # alignant min_columns sur le nombre de colonnes réellement affichées, sa
        # hauteur naturelle devient celle du nombre de lignes réel. La grille
        # n'est PAS dans un ScrolledWindow (le scroll de la page marche partout)
        # et sa disposition ne change pas — on ne corrige que le calcul de
        # hauteur. L'enveloppe _SuggestionsClamp déclenche le recalcul à chaque
        # allocation et garde la fenêtre rétrécissable (voir sa docstring).
        self._fit_pending = False
        self._fit_width = 0
        self._suggestions_clamp = _SuggestionsClamp(
            self._suggestions_grid, self._schedule_fit_suggestions)

        self._suggestions_group = self._build_group(
            _('Suggestions'), self._suggestions_clamp)

        # Ligne « Derniers ajouts » : les albums les plus récemment enregistrés
        # sur le serveur, en rangée horizontale de pochettes carrées. Elle est
        # placée juste sous le titre Suggestions (parité avec l'idée d'une
        # étagère de nouveautés en tête de la section suggestions).
        self._latest_box = Gtk.Box(spacing=12)
        # Même retrait à gauche que les cellules du GridView Suggestions, pour
        # que les pochettes des deux sections s'alignent (voir style.css).
        self._latest_box.add_css_class('jewelbox-latest-row')
        latest_scroller = Gtk.ScrolledWindow(
            child=self._latest_box,
            vscrollbar_policy=Gtk.PolicyType.NEVER,
            propagate_natural_height=True)
        self._latest_group = self._build_group(
            _('Derniers ajouts'), latest_scroller)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18,
                          margin_start=18, margin_end=18,
                          margin_top=18, margin_bottom=18)
        content.append(self._recent_group)
        content.append(self._latest_group)
        content.append(self._suggestions_group)

        self.add_named(
            Gtk.ScrolledWindow(child=content,
                               hscrollbar_policy=Gtk.PolicyType.NEVER,
                               vexpand=True),
            'content')
        # Pas de reload() ici : la fenêtre le déclenche une fois toute l'UI
        # construite (comme la Bibliothèque).

    def _build_group(self, title, child):
        """Un titre de section au-dessus de son contenu (parité SectionHeader
        Android)."""
        header = Gtk.Label(label=title, xalign=0,
                           css_classes=['title-4'])
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.append(header)
        box.append(child)
        return box

    # ── Chargement ───────────────────────────────────────────────────────────

    def reload(self):
        """(Re)charge le flux d'accueil depuis le serveur configuré."""
        client = self._app.get_client()
        if client is None:
            self._show_status(
                icon='network-server-symbolic',
                title=_('Aucun serveur configuré'),
                description=_('Indiquez l’adresse de votre serveur JewelBox '
                              'pour retrouver vos écoutes récentes.'),
                button_label=_('Ouvrir les Préférences'),
                button_action='app.preferences')
            return
        self._load_generation += 1
        task = self._load(client, self._load_generation)
        asyncio.get_event_loop_policy().get_event_loop().create_task(task)

    async def _load(self, client, generation):
        self.set_visible_child_name('loading')
        try:
            home = await client.home()
        except ApiError as error:
            if generation != self._load_generation:
                return
            # 404 : serveur trop ancien (pas d'endpoint /home avant 1.9).
            if error.status == 404:
                self._show_status(
                    icon='network-server-symbolic',
                    title=_('Serveur trop ancien'),
                    description=_('L’accueil nécessite un serveur JewelBox '
                                  '1.9 ou plus récent.'),
                    button_label=_('Réessayer'),
                    button_action=None)
            else:
                self._show_status(
                    icon='network-error-symbolic',
                    title=_('Serveur injoignable'),
                    description=str(error),
                    button_label=_('Réessayer'),
                    button_action=None)
            return
        if generation != self._load_generation:
            return

        # Derniers albums enregistrés sur le serveur (best-effort : la ligne
        # reste masquée si l'appel échoue, sans compromettre le reste de
        # l'accueil). Tri par date d'ajout décroissante.
        try:
            latest_page = await client.albums(
                page=1, limit=_LATEST_COUNT,
                sort='created_at', order='desc')
            latest = latest_page.data
        except ApiError:
            latest = ()
        if generation != self._load_generation:
            return

        # Ne garder que les entrées récentes réellement ouvrables (album,
        # playlist ou liste intelligente), plafonnées à 8 comme le client
        # Android.
        recent = [item for item in home.recent
                  if item.album is not None or item.playlist is not None
                  or item.smart is not None][:8]

        if not recent and not home.suggestions and not latest:
            self._show_status(
                icon='user-home-symbolic',
                title=_('Rien à afficher pour l’instant'),
                description=_('Vos écoutes récentes et des suggestions '
                              'apparaîtront ici une fois la musique lancée.'),
                button_label=_('Actualiser'),
                button_action=None)
            return

        self._populate_recent(client, recent)
        self._populate_latest(client, latest)
        self._populate_suggestions(home.suggestions)
        self.set_visible_child_name('content')

    def _populate_recent(self, client, recent):
        self._recent_group.set_visible(bool(recent))
        while (child := self._recent_box.get_child_at_index(0)) is not None:
            self._recent_box.remove(child)
        for item in recent:
            self._recent_box.append(self._build_recent_tile(client, item))

    def _populate_latest(self, client, latest):
        self._latest_group.set_visible(bool(latest))
        while (child := self._latest_box.get_first_child()) is not None:
            self._latest_box.remove(child)
        for album in latest:
            self._latest_box.append(self._build_latest_tile(client, album))

    def _populate_suggestions(self, suggestions):
        self._suggestions_group.set_visible(bool(suggestions))
        self._suggestions_store.remove_all()
        for album in suggestions:
            self._suggestions_store.append(_AlbumItem(album))
        self._schedule_fit_suggestions()

    def _show_status(self, icon, title, description, button_label,
                     button_action):
        self._status.set_icon_name(icon)
        self._status.set_title(title)
        # description interprétée comme markup Pango : échappée pour qu'une
        # URL ou un message serveur avec « & » ne casse pas l'affichage
        # (même précaution que la Bibliothèque).
        self._status.set_description(GLib.markup_escape_text(description))
        self._status_button.set_label(button_label)
        self._status_action = button_action
        self.set_visible_child_name('status')

    def _on_status_clicked(self, _button):
        if self._status_action:
            self.activate_action(self._status_action, None)
        else:
            self.reload()

    # ── Section « Récemment écouté » ──────────────────────────────────────────

    def _build_recent_tile(self, client, item):
        """Tuile horizontale compacte : petite pochette (ou icône dédiée pour
        une liste intelligente), titre, sous-titre (artiste pour un album,
        nombre de pistes pour une playlist / liste intelligente)."""
        album = item.album
        playlist = item.playlist
        smart = item.smart
        # Une liste intelligente n'a pas de pochette : son libellé et son icône
        # sont résolus côté client à partir de la clé (miroir de l'app Android).
        spec = smart_spec(smart.key) if smart is not None else None

        def _track_count_label(count):
            return (_('{count} pistes').format(count=count)
                    if count != 1 else _('1 piste'))

        if album is not None:
            title = album.title
            subtitle = album.artist.name
            cover_url = client.resolve_cover(album.cover_url)
        elif playlist is not None:
            title = playlist.name
            subtitle = _track_count_label(playlist.track_count)
            cover_url = client.resolve_cover(playlist.cover_url)
        else:
            title = spec.label if spec is not None else smart.key
            subtitle = _track_count_label(smart.track_count)
            cover_url = None

        if smart is not None:
            # Vignette = icône symbolique de la liste intelligente, dans un
            # cadre de la même taille que les pochettes voisines.
            cover = Gtk.Image(
                icon_name=(spec.icon if spec is not None
                           else 'view-list-symbolic'),
                pixel_size=_RECENT_COVER_SIZE // 2,
                width_request=_RECENT_COVER_SIZE,
                height_request=_RECENT_COVER_SIZE,
                valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER)
            cover.add_css_class('jewelbox-cover')
            cover.add_css_class('jewelbox-smart-tile')
        else:
            cover = Gtk.Picture(
                content_fit=Gtk.ContentFit.COVER,
                width_request=_RECENT_COVER_SIZE,
                height_request=_RECENT_COVER_SIZE,
                overflow=Gtk.Overflow.HIDDEN,
                valign=Gtk.Align.CENTER)
            cover.add_css_class('jewelbox-cover')

        if cover_url:
            cover._wanted_url = cover_url
            cover.set_paintable(self._textures.get(cover_url))
            if cover_url not in self._textures:
                task = self._load_cover(cover, cover_url)
                asyncio.get_event_loop_policy().get_event_loop().create_task(task)

        # Bouton « Lire » en surimpression, pour un album avec des pistes
        # jouables ou une liste intelligente (une playlist utilisateur n'en a
        # pas ici). Variante compacte (.small) adaptée à la pochette de 56px.
        cover_widget = cover
        if album is not None and album.has_audio:
            play = Gtk.Button(
                icon_name='media-playback-start-symbolic',
                halign=Gtk.Align.END, valign=Gtk.Align.END,
                margin_end=2, margin_bottom=2,
                tooltip_text=_('Lire l’album'),
                css_classes=['circular', 'jewelbox-cover-play',
                             'jewelbox-cover-play-small'])
            play.connect('clicked', self._on_recent_play_clicked, album.id)
            cover_widget = Gtk.Overlay(child=cover, valign=Gtk.Align.CENTER)
            cover_widget.add_overlay(play)
        elif smart is not None and smart.track_count > 0:
            play = Gtk.Button(
                icon_name='media-playback-start-symbolic',
                halign=Gtk.Align.END, valign=Gtk.Align.END,
                margin_end=2, margin_bottom=2,
                tooltip_text=_('Lire'),
                css_classes=['circular', 'jewelbox-cover-play',
                             'jewelbox-cover-play-small'])
            play.connect('clicked', self._on_recent_smart_play_clicked, smart.key)
            cover_widget = Gtk.Overlay(child=cover, valign=Gtk.Align.CENTER)
            cover_widget.add_overlay(play)

        title_label = Gtk.Label(
            label=title, xalign=0, hexpand=True,
            ellipsize=Pango.EllipsizeMode.END,
            css_classes=['heading'])
        subtitle_label = Gtk.Label(
            label=subtitle, xalign=0,
            ellipsize=Pango.EllipsizeMode.END,
            css_classes=['caption', 'dim-label'])
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2,
                       valign=Gtk.Align.CENTER, hexpand=True)
        text.append(title_label)
        text.append(subtitle_label)

        tile_box = Gtk.Box(spacing=12, margin_top=8, margin_bottom=8,
                           margin_start=8, margin_end=12)
        tile_box.append(cover_widget)
        tile_box.append(text)
        # .card donne le fond et la bordure arrondie que la boxed-list offrait
        # gratuitement — chaque tuile est une carte cliquable distincte.
        tile_box.add_css_class('card')
        tile_box.add_css_class('activatable')

        child = Gtk.FlowBoxChild(child=tile_box)
        # Portés par le child pour que « child-activated » retrouve la cible au
        # clic (même motif que la fiche album).
        child._album_id = album.id if album is not None else None
        child._playlist_id = playlist.id if playlist is not None else None
        child._smart_key = smart.key if smart is not None else None
        return child

    def _on_recent_activated(self, _flowbox, child):
        album_id = getattr(child, '_album_id', None)
        playlist_id = getattr(child, '_playlist_id', None)
        smart_key = getattr(child, '_smart_key', None)
        if album_id is not None and self.on_album_activated is not None:
            self.on_album_activated(album_id)
        elif playlist_id is not None and self.on_playlist_activated is not None:
            self.on_playlist_activated(playlist_id)
        elif smart_key is not None and self.on_smart_activated is not None:
            self.on_smart_activated(smart_key)

    def _on_recent_play_clicked(self, _button, album_id):
        self._play_album(album_id)

    def _on_recent_smart_play_clicked(self, _button, key):
        self._play_smart(key)

    # ── Section « Suggestions » (grille d'albums) ─────────────────────────────

    def _schedule_fit_suggestions(self, width=None):
        """Planifie _fit_suggestions_columns en idle : appelé pendant
        l'allocation (via _SuggestionsClamp) ou au peuplement, moments où
        changer min_columns déclencherait une mise en page dans la mise en
        page. Dédoublonné (plusieurs allocations par frame possibles)."""
        if width is not None:
            self._fit_width = width
        if self._fit_pending:
            return
        self._fit_pending = True

        def apply():
            self._fit_pending = False
            self._fit_suggestions_columns()
            return False
        GLib.idle_add(apply)

    def _fit_suggestions_columns(self):
        """Aligne min_columns du GridView sur le nombre de colonnes réellement
        affichées, pour que sa hauteur naturelle corresponde aux lignes réelles
        (sinon un grand vide subsiste sous les suggestions ; voir _build_content).
        Ne change pas la disposition : la grille affiche déjà autant de colonnes
        que la largeur permet."""
        # Largeur de l'enveloppe, pas de la grille : pendant qu'une réduction
        # converge, la grille reste clampée à son ancien minimum (plus large).
        width = self._fit_width or self._suggestions_clamp.get_width()
        if width <= 0 or self._suggestions_store.get_n_items() == 0:
            return
        grid = self._suggestions_grid
        # Largeur d'une colonne mesurée sur la grille (min = min_columns
        # colonnes), pour compter exactement comme GTK plutôt qu'estimer.
        min_width = grid.measure(Gtk.Orientation.HORIZONTAL, -1)[0]
        cell = max(1, min_width // max(1, grid.get_min_columns()))
        columns = max(1, min(8, width // cell))
        if grid.get_min_columns() != columns:
            grid.set_min_columns(columns)

    def _on_card_setup(self, _factory, list_item):
        cover = Gtk.Picture(
            content_fit=Gtk.ContentFit.COVER,
            width_request=_SUGGESTION_COVER_SIZE,
            height_request=_SUGGESTION_COVER_SIZE,
            halign=Gtk.Align.START,
            overflow=Gtk.Overflow.HIDDEN,
        )
        cover.add_css_class('jewelbox-cover')

        # Bouton rond bleu « Lire l'album » en surimpression, comme la
        # Bibliothèque. Un clic lance l'album sans remonter jusqu'à la carte
        # (l'ouverture de la fiche par simple clic reste distincte).
        play = Gtk.Button(
            icon_name='media-playback-start-symbolic',
            halign=Gtk.Align.END, valign=Gtk.Align.END,
            margin_end=6, margin_bottom=6,
            tooltip_text=_('Lire l’album'),
            css_classes=['circular', 'jewelbox-cover-play'])
        play.connect('clicked', self._on_suggestion_play_clicked, list_item)

        overlay = Gtk.Overlay(child=cover, halign=Gtk.Align.START)
        overlay.add_overlay(play)

        title = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END,
                          max_width_chars=18,
                          css_classes=['caption-heading'])
        artist = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END,
                           max_width_chars=18,
                           css_classes=['caption', 'dim-label'])

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2,
                       width_request=_SUGGESTION_COVER_SIZE,
                       halign=Gtk.Align.START)
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
        list_item.play.set_visible(album.has_audio)

        cover = list_item.cover
        client = self._app.get_client()
        url = client.resolve_cover(album.cover_url) if client else None
        cover._wanted_url = url
        cover.set_paintable(self._textures.get(url))
        if url and url not in self._textures:
            task = self._load_cover(cover, url)
            asyncio.get_event_loop_policy().get_event_loop().create_task(task)

    def _on_suggestion_activated(self, _grid, position):
        item = self._suggestions_store.get_item(position)
        if item is not None and self.on_album_activated is not None:
            self.on_album_activated(item.album.id)

    def _on_suggestion_play_clicked(self, _button, list_item):
        item = list_item.get_item()
        if item is not None:
            self._play_album(item.album.id)

    # ── Ligne « Derniers ajouts » ─────────────────────────────────────────────

    def _build_latest_tile(self, client, album):
        """Carte carrée d'un album récemment enregistré : pochette cliquable
        (ouvre la fiche) avec bouton « Lire l'album » en surimpression. Même
        présentation que les suggestions, mais en widget statique posé dans une
        rangée horizontale plutôt qu'une cellule de GridView recyclée.

        Le bouton-carte et le bouton lecture sont frères dans l'Overlay (jamais
        imbriqués : GTK interdit un bouton dans un bouton). Le bouton lecture,
        posé au-dessus, capte son propre clic ; ailleurs sur la pochette, c'est
        le bouton-carte qui ouvre la fiche."""
        cover = Gtk.Picture(
            content_fit=Gtk.ContentFit.COVER,
            width_request=_LATEST_COVER_SIZE,
            height_request=_LATEST_COVER_SIZE,
            overflow=Gtk.Overflow.HIDDEN)
        cover.add_css_class('jewelbox-cover')

        if album.cover_url:
            url = client.resolve_cover(album.cover_url)
            cover._wanted_url = url
            cover.set_paintable(self._textures.get(url))
            if url and url not in self._textures:
                task = self._load_cover(cover, url)
                asyncio.get_event_loop_policy().get_event_loop().create_task(task)

        # Bouton-carte : la pochette entière est cliquable et ouvre la fiche.
        cover_button = Gtk.Button(
            child=cover, css_classes=['flat', 'jewelbox-cover-button'])
        cover_button.connect('clicked', self._on_latest_activated, album.id)

        overlay = Gtk.Overlay(child=cover_button, halign=Gtk.Align.START)
        if album.has_audio:
            play = Gtk.Button(
                icon_name='media-playback-start-symbolic',
                halign=Gtk.Align.END, valign=Gtk.Align.END,
                margin_end=6, margin_bottom=6,
                tooltip_text=_('Lire l’album'),
                css_classes=['circular', 'jewelbox-cover-play'])
            play.connect('clicked', self._on_latest_play_clicked, album.id)
            overlay.add_overlay(play)

        title = Gtk.Label(
            label=album.title, xalign=0,
            ellipsize=Pango.EllipsizeMode.END, max_width_chars=14,
            tooltip_text=album.title,
            css_classes=['caption-heading'])
        artist = Gtk.Label(
            label=album.artist.name, xalign=0,
            ellipsize=Pango.EllipsizeMode.END, max_width_chars=14,
            css_classes=['caption', 'dim-label'])

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2,
                       width_request=_LATEST_COVER_SIZE,
                       halign=Gtk.Align.START)
        card.append(overlay)
        card.append(title)
        card.append(artist)
        return card

    def _on_latest_activated(self, _button, album_id):
        if self.on_album_activated is not None:
            self.on_album_activated(album_id)

    def _on_latest_play_clicked(self, _button, album_id):
        self._play_album(album_id)

    # ── Lecture d'un album depuis une carte ──────────────────────────────────

    def _play_album(self, album_id):
        # Les albums de l'accueil ne portent que leurs métadonnées (pas les
        # pistes) : on charge l'album complet avant de lancer sa lecture,
        # comme la Bibliothèque.
        task = self._load_and_play(album_id)
        asyncio.get_event_loop_policy().get_event_loop().create_task(task)

    async def _load_and_play(self, album_id):
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

    # ── Lecture d'une liste intelligente depuis une tuile ─────────────────────

    def _play_smart(self, key):
        # Les tuiles smart de l'accueil ne portent que la clé : on charge les
        # pistes avant de lancer, en signalant l'historique par la clé.
        task = self._load_and_play_smart(key)
        asyncio.get_event_loop_policy().get_event_loop().create_task(task)

    async def _load_and_play_smart(self, key):
        client = self._app.get_client()
        playback = self._app.playback
        if client is None or playback is None:
            return
        try:
            smart = await client.smart_playlist(key)
        except ApiError:
            return  # best-effort, comme le reste de l'app
        if smart.tracks:
            spec = smart_spec(key)
            playback.play_queue_tracks(
                smart.tracks, report_smart_key=key,
                source_name=spec.label if spec is not None else key)

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
