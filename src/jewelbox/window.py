from gettext import gettext as _

from gi.repository import Adw, Gtk


class JewelboxWindow(Adw.ApplicationWindow):
    """Fenêtre principale : 4 onglets (Accueil, Bibliothèque, Recherche,
    Playlists) commutés par une Adw.ViewSwitcher dans la barre d'en-tête,
    qui bascule en barre du bas quand la fenêtre est étroite."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.set_title('JewelBox')
        self.set_default_size(1000, 700)
        self.set_size_request(360, 294)

        self._stack = Adw.ViewStack()
        self._add_placeholder_page(
            'home', _('Accueil'), 'user-home-symbolic',
            _('Reprendre l’écoute et suggestions arriveront ici.'))
        self._add_placeholder_page(
            'library', _('Bibliothèque'), 'media-optical-symbolic',
            _('La grille des albums de votre serveur arrivera ici.'))
        self._add_placeholder_page(
            'search', _('Recherche'), 'system-search-symbolic',
            _('La recherche dans la bibliothèque arrivera ici.'))
        self._add_placeholder_page(
            'playlists', _('Playlists'), 'view-list-symbolic',
            _('Listes intelligentes et playlists arriveront ici.'))

        switcher = Adw.ViewSwitcher(
            stack=self._stack,
            policy=Adw.ViewSwitcherPolicy.WIDE,
        )
        header_bar = Adw.HeaderBar(title_widget=switcher)

        menu_button = Gtk.MenuButton(
            icon_name='open-menu-symbolic',
            tooltip_text=_('Menu principal'),
        )
        menu_button.set_menu_model(self._build_main_menu())
        header_bar.pack_end(menu_button)

        self._switcher_bar = Adw.ViewSwitcherBar(stack=self._stack)

        toolbar_view = Adw.ToolbarView(content=self._stack)
        toolbar_view.add_top_bar(header_bar)
        toolbar_view.add_bottom_bar(self._switcher_bar)
        self.set_content(toolbar_view)

        # Fenêtre étroite : le commutateur descend en barre du bas et le
        # titre reprend sa place dans la barre d'en-tête. (On ne peut pas
        # passer None à add_setter depuis Python, d'où le Adw.WindowTitle.)
        narrow_title = Adw.WindowTitle(title='JewelBox')
        breakpoint = Adw.Breakpoint.new(
            Adw.BreakpointCondition.parse('max-width: 550sp'))
        breakpoint.add_setter(self._switcher_bar, 'reveal', True)
        breakpoint.add_setter(header_bar, 'title-widget', narrow_title)
        self.add_breakpoint(breakpoint)

    def _add_placeholder_page(self, name, title, icon_name, description):
        page = Adw.StatusPage(
            title=title,
            icon_name=icon_name,
            description=description,
        )
        stack_page = self._stack.add_titled_with_icon(page, name, title, icon_name)
        return stack_page

    def _build_main_menu(self):
        from gi.repository import Gio

        menu = Gio.Menu()
        menu.append(_('À propos de JewelBox'), 'app.about')
        menu.append(_('Quitter'), 'app.quit')
        return menu
