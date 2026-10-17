"""Raccourcis clavier : la table de référence, les infobulles qui les
rappellent, et la fenêtre d'aide (Ctrl+? ou menu principal).

PLAYBACK_SHORTCUTS est la seule source : main.py y lit les raccourcis qu'il
déclare, les boutons de lecture les affichent dans leur infobulle
(« Suivant (Ctrl+N) ») et la fenêtre d'aide les liste — rien ne peut se
désynchroniser.

Adw.ShortcutsDialog (libadwaita >= 1.8, runtime GNOME 49+) ; sur une
libadwaita plus ancienne (mode développement), simple liste dans un
Adw.AlertDialog.

Code frontière (exclu de la couverture).
"""

from gettext import gettext as _

from gi.repository import Adw, Gtk


# (nom d'action app.*, raccourcis) — mêmes combinaisons que GNOME Musique.
PLAYBACK_SHORTCUTS = (
    ('play-pause', ['<primary>space']),
    ('next', ['<primary>n']),
    ('previous', ['<primary>b']),
    ('toggle-shuffle', ['<primary>s']),
    ('cycle-repeat', ['<primary>r']),
    ('toggle-favorite', ['<primary>d']),
)
_ACCELS = dict(PLAYBACK_SHORTCUTS)


def accel_label(accel: str) -> str:
    """« <primary>n » → « Ctrl+N », dans la langue du bureau."""
    # GTK 4 : (analysé ?, touche, modificateurs).
    ok, key, mods = Gtk.accelerator_parse(accel)
    return Gtk.accelerator_get_label(key, mods) if ok else accel


def with_shortcut(text: str, action: str) -> str:
    """Infobulle d'un bouton suivie de son raccourci : « Suivant (Ctrl+N) »,
    pour qu'on le redécouvre au survol sans ouvrir l'aide."""
    accels = _ACCELS.get(action)
    if not accels:
        return text
    return f'{text} ({accel_label(accels[0])})'


def _sections():
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
            lines.append(f'{accel_label(accel)}  —  {label}')
        lines.append('')
    dialog = Adw.AlertDialog(heading=_('Raccourcis clavier'),
                             body='\n'.join(lines).strip(),
                             body_use_markup=True)
    dialog.add_response('close', _('Fermer'))
    dialog.present(parent)
