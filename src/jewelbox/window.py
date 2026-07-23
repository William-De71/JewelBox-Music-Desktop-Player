from gettext import gettext as _

from gi.repository import Adw, Gtk

import asyncio

from jewelbox.api.client import ApiError
from jewelbox.ui.album_detail import AlbumDetailPage
from jewelbox.ui.full_player import FullPlayerPage
from jewelbox.ui.home import HomePage
from jewelbox.ui.library import LibraryPage
from jewelbox.ui.player_bar import PlayerBar
from jewelbox.ui.playlist_detail import PlaylistDetailPage
from jewelbox.ui.playlists import PlaylistsPage
from jewelbox.ui.search import SearchPage
from jewelbox.ui.smart_playlist_detail import SmartPlaylistDetailPage


class JewelboxWindow(Adw.ApplicationWindow):
    """Fenêtre principale : 4 onglets (Accueil, Bibliothèque, Recherche,
    Playlists) commutés par une Adw.ViewSwitcher dans la barre d'en-tête,
    qui bascule en barre du bas quand la fenêtre est étroite."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.set_title('JewelBox Music Player')
        self.set_default_size(1000, 700)
        # Largeur plancher : le contenu le plus contraint (l'accueil, avec ses
        # récents en deux colonnes) demande ~406 px au minimum. Un plancher
        # inférieur laisserait le compositeur rétrécir la fenêtre sous ce
        # minimum — contenu coupé à droite et warnings AdwToolbarView
        # « exceeds width ».
        self.set_size_request(420, 294)

        self._stack = Adw.ViewStack()
        self._home = HomePage(self.get_application())
        self._home.on_album_activated = self._open_album
        self._home.on_playlist_activated = self._open_playlist
        self._home.on_smart_activated = self._open_smart_playlist
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
        self._playlists = PlaylistsPage(self.get_application())
        self._playlists.on_playlist_activated = self._open_playlist
        self._playlists.on_smart_activated = self._open_smart_playlist
        # Groupe d'actions du menu contextuel (renommer / supprimer) : monté
        # sur la page sous le préfixe « playlists » que citent ses items.
        self._playlists.insert_action_group(
            'playlists', self._playlists.install_actions())
        self._stack.add_titled_with_icon(
            self._playlists, 'playlists', _('Playlists'), 'view-list-symbolic')

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

    def _refresh_server_hint(self):
        """Chaque onglet gère seul son état « Aucun serveur configuré » via son
        reload() : on les recharge tous ici (construction, fermeture des
        Préférences), ils basculent d'eux-mêmes selon la présence d'un client."""
        app = self.get_application()
        if app is None:
            return
        self._home.reload()
        self._library.reload()
        self._search.reload()
        self._playlists.reload()

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
        # De même pour les Playlists : une playlist créée/renommée/supprimée
        # depuis une fiche empilée doit se refléter au retour sur l'onglet.
        elif self._stack.get_visible_child() is self._playlists:
            self._playlists.reload()

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

    def _open_playlist(self, playlist_id: int):
        """Empile la fiche d'une playlist utilisateur (depuis l'onglet
        Playlists ou une tuile récente de l'accueil)."""
        page = PlaylistDetailPage(self.get_application(), playlist_id)
        nav_page = Adw.NavigationPage(child=page, title=_('Playlist'))
        page.on_title_known = nav_page.set_title
        page.on_renamed = nav_page.set_title
        # Une suppression depuis la fiche dépile aussitôt vers l'onglet, qui se
        # rafraîchira via _on_tab_changed en révélant la liste à jour.
        page.on_deleted = lambda: self._pop_if_current(nav_page)
        # Menu par entrée (monter / descendre / retirer) : groupe local à la
        # page, sous le préfixe « playlist-entry » que citent ses items.
        page.insert_action_group('playlist-entry', page.install_actions())
        self._nav.push(nav_page)

    def _open_smart_playlist(self, key: str):
        """Empile la fiche d'une liste intelligente (lecture seule ; le mix
        dynamique y ajoute relancer / retirer)."""
        page = SmartPlaylistDetailPage(self.get_application(), key)
        nav_page = Adw.NavigationPage(child=page, title=page.title())
        page.on_title_known = nav_page.set_title
        self._nav.push(nav_page)

    def _pop_if_current(self, nav_page):
        # Ne dépile que si la fiche est bien en tête (l'utilisateur a pu
        # naviguer ailleurs entre-temps) ; pop() serait sinon un mauvais retour.
        if self._nav.get_visible_page() is nav_page:
            self._nav.pop()

    def _build_main_menu(self):
        from gi.repository import Gio

        menu = Gio.Menu()
        menu.append(_('Préférences'), 'app.preferences')
        menu.append(_('À propos de JewelBox'), 'app.about')
        menu.append(_('Quitter'), 'app.quit')
        return menu
