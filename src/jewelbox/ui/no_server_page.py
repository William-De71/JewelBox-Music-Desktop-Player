"""Page d'état « aucun serveur configuré », partagée par tous les onglets
qui n'ont pas encore leur propre logique de chargement (Accueil, Recherche,
Playlists). Bibliothèque a son propre Adw.StatusPage équivalent, avec en
plus les états chargement/erreur/vide — copié ici pour un rendu identique.
"""

from gettext import gettext as _

from gi.repository import Adw, Gtk


def build_no_server_page():
    """Adw.StatusPage identique à celui de LibraryPage pour l'état « pas de
    serveur » : mêmes icône, titre, texte et bouton d'action."""
    page = Adw.StatusPage(
        icon_name='network-server-symbolic',
        title=_('Aucun serveur configuré'),
        description=_('Indiquez l’adresse de votre serveur JewelBox '
                      'pour parcourir votre collection.'),
    )
    button = Gtk.Button(
        label=_('Ouvrir les Préférences'), halign=Gtk.Align.CENTER,
        css_classes=['pill', 'suggested-action'],
        action_name='app.preferences')
    page.set_child(button)
    return page
