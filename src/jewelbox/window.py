from gettext import gettext as _

from gi.repository import Adw, Gtk

import asyncio

from jewelbox.api.client import ApiError
from jewelbox.ui.album_detail import AlbumDetailPage
from jewelbox.ui.full_player import FullPlayerPage
from jewelbox.ui.home import HomePage
from jewelbox.ui.library import LibraryPage
from jewelbox.ui.no_server_page import build_no_server_page
from jewelbox.ui.player_bar import PlayerBar
from jewelbox.ui.search import SearchPage


class JewelboxWindow(Adw.ApplicationWindow):
    """Fenêtre principale : 4 onglets (Accueil, Bibliothèque, Recherche,
    Playlists) commutés par une Adw.ViewSwitcher dans la barre d'en-tête,
    qui bascule en barre du bas quand la fenêtre est étroite."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.set_title('JewelBox Music Player')
        self.set_default_size(1000, 700)
        self.set_size_request(360, 294)

        self._stack = Adw.ViewStack()
        self._pages = {}
        self._home = HomePage(self.get_application())
        self._home.on_album_activated = self._open_album
        self._home.on_playlist_activated = self._play_playlist
        self._stack.add_titled_with_icon(
            self._home, 'home', _('Accueil'), 'user-home-symbolic')
        self._library = LibraryPage(self.get_application())
        self._library.on_album_activated = self._open_album
        self._stack.add_titled_with_icon(
            self._library, 'library', _('Bibliothèque'),
            'media-optical-symbolic')
        self._search = SearchPage(self.get_application())
        self._search.on_album_activated = self._open_album
        self._stack.add_titled_with_icon(
            self._search, 'search', _('Recherche'), 'system-search-symbolic')
        self._add_placeholder_page(
            'playlists', _('Playlists'), 'view-list-symbolic',
            _('Listes intelligentes et playlists arriveront ici.'))

        switcher = Adw.ViewSwitcher(
            stack=self._stack,
            policy=Adw.ViewSwitcherPolicy.WIDE,
        )
        header_bar = Adw.HeaderBar(title_widget=switcher)

        # Le sélecteur d'onglets reste visible sur toutes les pages, y compris
        # la fiche album empilée par-dessus. Comme le header vit dans le
        # ToolbarView externe (au-dessus du NavigationView), la barre interne
        # d'une page poussée n'apparaît pas : ce bouton retour explicite,
        # révélé seulement quand une page est empilée, dépile le NavigationView.
        self._back_button = Gtk.Button(
            icon_name='go-previous-symbolic', visible=False,
            tooltip_text=_('Retour'))
        self._back_button.connect('clicked', lambda *_a: self._nav.pop())
        header_bar.pack_start(self._back_button)

        menu_button = Gtk.MenuButton(
            icon_name='open-menu-symbolic',
            tooltip_text=_('Menu principal'),
        )
        menu_button.set_menu_model(self._build_main_menu())
        header_bar.pack_end(menu_button)

        # NavigationView pour empiler la fiche album par-dessus les onglets
        # UNIQUEMENT — le menu (en-tête + ViewSwitcherBar) et le mini-lecteur
        # doivent rester visibles sur toutes les pages, donc ils vivent dans
        # le ToolbarView externe, pas dans la page racine du NavigationView.
        self._nav = Adw.NavigationView()
        self._root_page = Adw.NavigationPage(child=self._stack, title='JewelBox')
        self._nav.add(self._root_page)
        # Le bouton retour n'apparaît que hors de la page racine (une fiche
        # album empilée). Esc pour dépiler est déjà géré par le NavigationView.
        self._nav.connect('notify::visible-page', self._on_nav_page_changed)

        # Changer d'onglet depuis une fiche album empilée : le ViewStack bascule
        # en arrière-plan sous la fiche, sans effet visible. On dépile jusqu'à
        # la racine pour révéler l'onglet choisi.
        self._stack.connect('notify::visible-child', self._on_tab_changed)

        self._switcher_bar = Adw.ViewSwitcherBar(stack=self._stack)
        self._player_bar = PlayerBar(self.get_application())
        self._player_bar.on_open_full_player = self._open_full_player

        # Grand lecteur : une seule instance (elle s'abonne à la session pour
        # la vie de la fenêtre), poussée/dépilée à la demande. Le mini-lecteur
        # et le sélecteur d'onglets sont masqués tant qu'il est ouvert — il
        # occupe tout l'écran, comme le NowPlayingScreen mobile.
        self._full_player = FullPlayerPage(self.get_application())
        self._full_player.on_closed = self._close_full_player
        self._full_player_page = Adw.NavigationPage(
            child=self._full_player, title=_('Lecture en cours'),
            tag='full-player')

        toolbar_view = Adw.ToolbarView(content=self._nav)
        toolbar_view.add_top_bar(header_bar)
        toolbar_view.add_bottom_bar(self._player_bar)
        toolbar_view.add_bottom_bar(self._switcher_bar)
        self.set_content(toolbar_view)

        # Fenêtre étroite : le commutateur descend en barre du bas et le
        # titre reprend sa place dans la barre d'en-tête. (On ne peut pas
        # passer None à add_setter depuis Python, d'où le Adw.WindowTitle.)
        # En fenêtre étroite le nom complet serait tronqué : forme courte.
        narrow_title = Adw.WindowTitle(title='JewelBox')
        breakpoint = Adw.Breakpoint.new(
            Adw.BreakpointCondition.parse('max-width: 550sp'))
        breakpoint.add_setter(self._switcher_bar, 'reveal', True)
        breakpoint.add_setter(header_bar, 'title-widget', narrow_title)
        self.add_breakpoint(breakpoint)

        self._refresh_server_hint()

    def _add_placeholder_page(self, name, title, icon_name, description):
        """Onglet pas encore développé : bascule entre son propre
        Adw.StatusPage et la page « aucun serveur configuré » commune, pour
        un rendu identique à celui de la Bibliothèque sur tous les onglets."""
        content_page = Adw.StatusPage(
            title=title,
            icon_name=icon_name,
            description=description,
        )
        no_server_page = build_no_server_page()

        tab_stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        tab_stack.add_named(content_page, 'content')
        tab_stack.add_named(no_server_page, 'no-server')

        stack_page = self._stack.add_titled_with_icon(
            tab_stack, name, title, icon_name)
        self._pages[name] = tab_stack
        return stack_page

    def _refresh_server_hint(self):
        """Sans serveur configuré, chaque onglet non développé bascule vers
        la même page « Aucun serveur configuré » que la Bibliothèque, qui
        gère ses propres états et se recharge au même moment.
        Appelé à la construction et à la fermeture des Préférences."""
        app = self.get_application()
        if app is None:
            return
        connected = app.get_client() is not None
        for tab_stack in self._pages.values():
            tab_stack.set_visible_child_name('content' if connected else 'no-server')
        self._home.reload()
        self._library.reload()
        self._search.reload()

    def _on_tab_changed(self, *_args):
        # pop_to_page(racine) est un no-op si on y est déjà : sûr à appeler à
        # chaque changement d'onglet.
        self._nav.pop_to_page(self._root_page)
        # En revenant sur l'Accueil, on recharge le flux : une écoute lancée
        # depuis un autre onglet a pu enrichir les récents entre-temps (parité
        # avec le refresh du HomeViewModel Android sur nouvelle file). Sans
        # serveur configuré, reload() bascule seul vers son état « message ».
        if self._stack.get_visible_child() is self._home:
            self._home.reload()

    def _on_nav_page_changed(self, *_args):
        # get_previous_page renvoie None quand la page visible est la racine :
        # dans ce cas il n'y a rien à dépiler, on masque le bouton retour.
        visible = self._nav.get_visible_page()
        can_go_back = (visible is not None
                       and self._nav.get_previous_page(visible) is not None)
        self._back_button.set_visible(can_go_back)

        # Le grand lecteur occupe tout l'écran : on masque le mini-lecteur et
        # le sélecteur d'onglets tant qu'il est visible, on les rétablit dès
        # qu'on en sort (le mini-lecteur gère seul sa visibilité selon l'état
        # de lecture via son propre _on_state, d'où le rappel manuel ici).
        on_full_player = visible is self._full_player_page
        self._switcher_bar.set_visible(not on_full_player)
        if on_full_player:
            self._player_bar.suppress()
        else:
            self._player_bar.restore_visibility()

    def _open_full_player(self):
        # No-op si déjà ouvert (un second clic ne l'empile pas deux fois).
        if self._nav.get_visible_page() is self._full_player_page:
            return
        self._nav.push(self._full_player_page)

    def _close_full_player(self):
        # La file s'est vidée : dépile le grand lecteur s'il est ouvert.
        if self._nav.get_visible_page() is self._full_player_page:
            self._nav.pop()

    def _open_album(self, album_id: int):
        page = AlbumDetailPage(self.get_application(), album_id)
        nav_page = Adw.NavigationPage(child=page, title=_('Album'))
        page.on_title_known = nav_page.set_title
        self._nav.push(nav_page)

    def _play_playlist(self, playlist_id: int):
        # Le desktop n'a pas encore de fiche playlist : depuis l'accueil, un
        # clic sur une playlist récente en lance directement la lecture. On
        # signale la lecture au serveur pour qu'elle remonte dans les récents.
        app = self.get_application()
        client = app.get_client()
        if client is None or app.playback is None:
            return
        loop = asyncio.get_event_loop_policy().get_event_loop()
        loop.create_task(self._load_and_play_playlist(client, playlist_id))

    async def _load_and_play_playlist(self, client, playlist_id):
        try:
            playlist = await client.playlist(playlist_id)
        except ApiError:
            return
        app = self.get_application()
        if app.playback is None:
            return
        app.playback.play_queue_tracks(playlist.tracks)
        try:
            await client.report_play('playlist', playlist_id)
        except ApiError:
            pass  # best-effort, comme le reste de l'app

    def _build_main_menu(self):
        from gi.repository import Gio

        menu = Gio.Menu()
        menu.append(_('Préférences'), 'app.preferences')
        menu.append(_('À propos de JewelBox'), 'app.about')
        menu.append(_('Quitter'), 'app.quit')
        return menu
