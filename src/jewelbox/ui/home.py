"""Page Accueil : reprise d'écoute et suggestions.

Parité avec l'écran d'accueil du client Android (HomeScreen) : deux sections
alimentées par GET /api/player/home (serveur >= 1.9). D'abord « Récemment
écouté » — les 8 derniers albums/playlists joués, en tuiles horizontales
compactes (petite pochette, titre, sous-titre). Ensuite « Suggestions » —
une grille de pochettes carrées d'albums, comme la Bibliothèque.

Un clic sur un album (récent ou suggéré) ouvre sa fiche ; un clic sur une
playlist récente lance sa lecture (le desktop n'a pas encore de fiche
playlist). Le flux se recharge à chaque affichage (la fenêtre le déclenche)
et après le début d'une nouvelle file, pour rester à jour.

Code frontière (exclu de la couverture) : cette page ne fait qu'afficher un
Home chargé par api.client et déléguer l'ouverture à la fenêtre / la lecture
à PlaybackSession, tous testés séparément.
"""

import asyncio
from gettext import gettext as _

from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk, Pango

from jewelbox.api.client import ApiError

_RECENT_COVER_SIZE = 56
_SUGGESTION_COVER_SIZE = 160


class _AlbumItem(GObject.Object):
    """Enveloppe GObject d'un modèle Album pour Gio.ListStore (même motif que
    la Bibliothèque)."""

    def __init__(self, album):
        super().__init__()
        self.album = album


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
        # Appelés avec l'id de l'élément activé.
        self.on_album_activated = None
        self.on_playlist_activated = None

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
            column_spacing=12,
            row_spacing=8,
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

        self._suggestions_group = self._build_group(
            _('Suggestions'), self._suggestions_grid)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18,
                          margin_start=18, margin_end=18,
                          margin_top=18, margin_bottom=18)
        content.append(self._recent_group)
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

        # Ne garder que les entrées récentes réellement ouvrables (album ou
        # playlist présent), plafonnées à 8 comme le client Android.
        recent = [item for item in home.recent
                  if item.album is not None or item.playlist is not None][:8]

        if not recent and not home.suggestions:
            self._show_status(
                icon='user-home-symbolic',
                title=_('Rien à afficher pour l’instant'),
                description=_('Vos écoutes récentes et des suggestions '
                              'apparaîtront ici une fois la musique lancée.'),
                button_label=_('Actualiser'),
                button_action=None)
            return

        self._populate_recent(client, recent)
        self._populate_suggestions(home.suggestions)
        self.set_visible_child_name('content')

    def _populate_recent(self, client, recent):
        self._recent_group.set_visible(bool(recent))
        while (child := self._recent_box.get_child_at_index(0)) is not None:
            self._recent_box.remove(child)
        for item in recent:
            self._recent_box.append(self._build_recent_tile(client, item))

    def _populate_suggestions(self, suggestions):
        self._suggestions_group.set_visible(bool(suggestions))
        self._suggestions_store.remove_all()
        for album in suggestions:
            self._suggestions_store.append(_AlbumItem(album))

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
        """Tuile horizontale compacte : petite pochette, titre, sous-titre
        (artiste pour un album, nombre de pistes pour une playlist)."""
        album = item.album
        playlist = item.playlist

        cover = Gtk.Picture(
            content_fit=Gtk.ContentFit.COVER,
            width_request=_RECENT_COVER_SIZE,
            height_request=_RECENT_COVER_SIZE,
            overflow=Gtk.Overflow.HIDDEN,
            valign=Gtk.Align.CENTER)
        cover.add_css_class('jewelbox-cover')

        if album is not None:
            title = album.title
            subtitle = album.artist.name
            cover_url = client.resolve_cover(album.cover_url)
        else:
            title = playlist.name
            subtitle = (_('{count} pistes').format(count=playlist.track_count)
                        if playlist.track_count != 1 else _('1 piste'))
            cover_url = client.resolve_cover(playlist.cover_url)

        if cover_url:
            cover._wanted_url = cover_url
            cover.set_paintable(self._textures.get(cover_url))
            if cover_url not in self._textures:
                task = self._load_cover(cover, cover_url)
                asyncio.get_event_loop_policy().get_event_loop().create_task(task)

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
        tile_box.append(cover)
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
        return child

    def _on_recent_activated(self, _flowbox, child):
        album_id = getattr(child, '_album_id', None)
        playlist_id = getattr(child, '_playlist_id', None)
        if album_id is not None and self.on_album_activated is not None:
            self.on_album_activated(album_id)
        elif playlist_id is not None and self.on_playlist_activated is not None:
            self.on_playlist_activated(playlist_id)

    # ── Section « Suggestions » (grille d'albums) ─────────────────────────────

    def _on_card_setup(self, _factory, list_item):
        cover = Gtk.Picture(
            content_fit=Gtk.ContentFit.COVER,
            width_request=_SUGGESTION_COVER_SIZE,
            height_request=_SUGGESTION_COVER_SIZE,
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
                       width_request=_SUGGESTION_COVER_SIZE,
                       halign=Gtk.Align.CENTER)
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

    def _on_suggestion_activated(self, _grid, position):
        item = self._suggestions_store.get_item(position)
        if item is not None and self.on_album_activated is not None:
            self.on_album_activated(item.album.id)

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
