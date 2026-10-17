"""Fenêtre d'aide « Raccourcis clavier » (Ctrl+? ou menu principal).

Les raccourcis de lecture viennent de main.PLAYBACK_SHORTCUTS, ceux qui
les déclarent réellement : l'aide ne peut donc pas se désynchroniser.
Adw.ShortcutsDialog (libadwaita >= 1.8, runtime GNOME 49+) ; sur une
libadwaita plus ancienne (mode développement), simple liste dans un
Adw.AlertDialog.

Code frontière (exclu de la couverture).
"""

from gettext import gettext as _

from gi.repository import Adw, Gtk


def _sections():
    from jewelbox.main import PLAYBACK_SHORTCUTS

    playback_titles = {
        'play-pause': _('Lecture / pause'),
        'next': _('Piste suivante'),
        'previous': _('Piste précédente'),
        'toggle-shuffle': _('Lecture aléatoire'),
        'cycle-repeat': _('Mode répétition'),
        'toggle-favorite': _('Ajouter / retirer des favoris'),
    }
    playback = [(playback_titles[name], accels[0])
                for name, accels in PLAYBACK_SHORTCUTS]
    general = [
        (_('Rechercher'), '<primary>f'),
        (_('Préférences'), '<primary>comma'),
        (_('Raccourcis clavier'), '<primary>question'),
        (_('Quitter'), '<primary>q'),
    ]
    return ((_('Lecture'), playback), (_('Général'), general))


def present_shortcuts(parent):
    if hasattr(Adw, 'ShortcutsDialog'):
        dialog = Adw.ShortcutsDialog()
        for title, items in _sections():
            section = Adw.ShortcutsSection(title=title)
            for label, accel in items:
                section.add(Adw.ShortcutsItem.new(label, accel))
            dialog.add(section)
        dialog.present(parent)
        return

    lines = []
    for title, items in _sections():
        lines.append(f'<b>{title}</b>')
        for label, accel in items:
            key, mods = Gtk.accelerator_parse(accel)
            lines.append(f'{Gtk.accelerator_get_label(key, mods)}  —  {label}')
        lines.append('')
    dialog = Adw.AlertDialog(heading=_('Raccourcis clavier'),
                             body='\n'.join(lines).strip(),
                             body_use_markup=True)
    dialog.add_response('close', _('Fermer'))
    dialog.present(parent)
