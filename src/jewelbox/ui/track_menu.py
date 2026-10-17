"""Menu « ⋮ » d'une piste ou d'un album : lire ensuite, ajouter à la file
d'attente, ajouter à une playlist.

Un Gtk.MenuButton à popover de simples boutons plats (pas de Gio.Menu) : les
actions sont des fermetures Python propres à chaque ligne, sans groupe
d'actions à déclarer ni identifiant à faire transiter en paramètre.

Code frontière (exclu de la couverture).
"""

from gettext import gettext as _

from gi.repository import Gtk


def menu_button(entries, icon_name='view-more-symbolic', tooltip=None,
                label=None):
    """entries : [(libellé, rappel sans argument)]. Un None insère un
    séparateur. Le popover se ferme avant d'exécuter le rappel (qui peut
    ouvrir un dialogue)."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    popover = Gtk.Popover(child=box)
    for entry in entries:
        if entry is None:
            box.append(Gtk.Separator(margin_top=4, margin_bottom=4))
            continue
        text, callback = entry
        item = Gtk.Button(css_classes=['flat'], halign=Gtk.Align.FILL)
        item.set_child(Gtk.Label(label=text, xalign=0))

        def on_clicked(_button, callback=callback):
            popover.popdown()
            callback()

        item.connect('clicked', on_clicked)
        box.append(item)
    button = Gtk.MenuButton(
        popover=popover, valign=Gtk.Align.CENTER, css_classes=['flat'],
        tooltip_text=tooltip or _('Plus d’actions'))
    if label is not None:
        content = Gtk.Box(spacing=6)
        content.append(Gtk.Image(icon_name=icon_name))
        content.append(Gtk.Label(label=label))
        button.set_child(content)
    else:
        button.set_icon_name(icon_name)
    return button


def queue_entries(on_play_next, on_add_to_queue, on_add_to_playlist):
    """Les trois actions communes à une piste ou un album."""
    return [
        (_('Lire ensuite'), on_play_next),
        (_('Ajouter à la file d’attente'), on_add_to_queue),
        None,
        (_('Ajouter à une playlist…'), on_add_to_playlist),
    ]
