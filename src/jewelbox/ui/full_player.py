"""Grand lecteur (« now playing ») : grande pochette, titre/artiste/album,
barre de progression et contrôles de transport.

Parité avec l'écran plein du client Android (NowPlayingScreen) : grande
pochette carrée en haut, titre centré avec le cœur favori épinglé à droite
de la même ligne, artiste (en accent), album, puis la barre de recherche
avec ses deux temps et enfin la rangée de contrôles (aléatoire · précédent ·
lecture · suivant · répétition). Ouvert en cliquant sur le mini-lecteur ;
empilé par-dessus les onglets dans le NavigationView.

Sous les contrôles, la file d'attente : la liste des pistes en file avec la
piste courante mise en avant, dépliable et repliable. Elle évite d'avoir à
retrouver l'album dans la bibliothèque pour savoir ce qui suit — un clic sur
une ligne saute directement à cette piste. La pochette, elle, est cliquable :
elle ramène à la fiche d'où vient la lecture (album, playlist ou liste
intelligente).

Code frontière (exclu de la couverture) : comme le mini-lecteur, cette page
ne fait qu'afficher un PlaybackUiState et déléguer chaque action à
PlaybackSession, déjà testée séparément. Elle partage la même mécanique de
seek que la barre de lecture (voir player_bar.py).
"""

import asyncio
from gettext import gettext as _

from gi.repository import Adw, Gdk, GLib, Gtk, Pango

from jewelbox.api.client import ApiError
from jewelbox.core.formats import format_duration
from jewelbox.core.lyrics import current_line_index, parse_lyrics
from jewelbox.ui.shortcuts import with_shortcut
from jewelbox.ui.track_menu import menu_button
from jewelbox.ui.add_to_playlist import present_add_to_playlist


class FullPlayerPage(Gtk.Box):
    """Une instance vit tant que la fenêtre existe (elle n'est pas recréée à
    chaque ouverture) : elle s'abonne à PlaybackSession à la construction et
    reflète en continu la piste courante."""

    def __init__(self, application):
        super().__init__(
            orientation=Gtk.Orientation.VERTICAL,
            css_classes=['jewelbox-full-player'])
        self._app = application
        self._seeking = False   # vrai pendant un glisser manuel du curseur
        self._release_id = None
        self._cover_url = None
        # Signature de la file actuellement dessinée (ids des pistes) : la file
        # ne se redessine que si elle a vraiment changé, pas à chaque tick de
        # position (_on_state arrive plusieurs fois par seconde).
        self._queue_signature = None
        self._queue_rows = []          # une Gtk.ListBoxRow par piste, dans l'ordre
        self._queue_index = None
        # Dernier état reçu : le bouton « Aller à… » y relit la source au clic
        # plutôt que de dupliquer source_type/source_id en attributs.
        self._last_state = None
        self._last_queue_items = ()
        # Appelé quand la file se vide (plus rien ne joue) : la fenêtre dépile
        # le grand lecteur, sinon il resterait figé sur la dernière piste.
        self.on_closed = None
        # Appelés par le bouton « Aller à l'album / la playlist » : la fenêtre
        # empile la fiche correspondante (voir window.py).
        self.on_open_album = None
        self.on_open_playlist = None
        self.on_open_smart_playlist = None

        content = self._build_content()
        # Plafonné et centré horizontalement : sur une fenêtre large, la
        # pochette et les contrôles restent groupés au milieu (colonne d'au
        # plus 560px) plutôt que de s'étirer sur toute la largeur — parité avec
        # la colonne à marges du grand lecteur mobile. Adw.Clamp s'en charge.
        #
        # Verticalement, en revanche, le contenu est aligné en HAUT : depuis
        # que la file d'attente se déplie sous les contrôles, un centrage ferait
        # remonter puis redescendre tout le lecteur à chaque bascule. Aligné en
        # haut, la pochette et les contrôles ne bougent plus, la file pousse
        # simplement la page vers le bas et le défilement prend le relais.
        content.set_valign(Gtk.Align.START)
        content.set_vexpand(True)
        clamp = Adw.Clamp(
            child=content, maximum_size=560, tightening_threshold=480,
            margin_start=24, margin_end=24, margin_top=16, margin_bottom=24)
        scroller = Gtk.ScrolledWindow(
            child=clamp, hscrollbar_policy=Gtk.PolicyType.NEVER,
            vexpand=True)
        self._scroller = scroller
        self.append(scroller)

        if application.playback is not None:
            application.playback.add_listener(self._on_state)
        self.connect('destroy', self._on_destroy)

    def _on_destroy(self, *_args):
        if self._app.playback is not None:
            self._app.playback.remove_listener(self._on_state)

    def _build_content(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        box.append(self._build_cover())
        box.append(Gtk.Box(height_request=24))
        box.append(self._build_titles())
        box.append(Gtk.Box(height_request=20))
        box.append(self._build_seek())
        box.append(Gtk.Box(height_request=12))
        box.append(self._build_controls())
        box.append(self._build_sleep_timer())
        box.append(self._build_queue())
        box.append(self._build_lyrics())
        return box

    # ── Minuterie de sommeil ─────────────────────────────────────────────────

    @staticmethod
    def _sleep_choices():
        """(libellé, mode passé à PlaybackSession.set_sleep_timer)."""
        return ((_('15 minutes'), 15), (_('30 minutes'), 30),
                (_('45 minutes'), 45), (_('1 heure'), 60), (_('1 h 30'), 90),
                (_('Fin de la piste en cours'), 'track'))

    def _build_sleep_timer(self):
        """Bouton « lune » sous les contrôles : arrête la lecture au bout
        d'un délai ou à la fin de la piste. Son libellé montre le temps
        restant tant que la minuterie tourne."""
        entries = [(label, lambda mode=mode: self._set_sleep(mode))
                   for label, mode in self._sleep_choices()]
        entries += [None, (_('Désactiver la minuterie'),
                           lambda: self._set_sleep(None))]
        self._sleep_button = menu_button(
            entries, icon_name='weather-clear-night-symbolic',
            label=_('Minuterie'), tooltip=_('Minuterie de sommeil'))
        self._sleep_button.set_halign(Gtk.Align.CENTER)
        self._sleep_button.set_margin_top(8)
        # Le libellé est le 2e enfant du contenu posé par menu_button.
        self._sleep_label = self._sleep_button.get_child().get_last_child()
        return self._sleep_button

    def _set_sleep(self, mode):
        if self._app.playback is not None:
            self._app.playback.set_sleep_timer(mode)

    def _update_sleep(self, state):
        if state.sleep_mode == 'timer':
            minutes = max(1, -(-int(state.sleep_remaining_seconds) // 60))
            text = _('Arrêt dans {minutes} min').format(minutes=minutes)
        elif state.sleep_mode == 'track':
            text = _('Arrêt en fin de piste')
        else:
            text = _('Minuterie')
        if self._sleep_label.get_label() != text:
            self._sleep_label.set_label(text)
        if state.sleep_mode is not None:
            self._sleep_button.add_css_class('accent')
        else:
            self._sleep_button.remove_css_class('accent')

    # ── Grande pochette ──────────────────────────────────────────────────────

    def _build_cover(self):
        # Pochette carrée RESPONSIVE : elle grandit jusqu'à un plafond mais
        # rétrécit avec la fenêtre — pas de width/height_request fixe, qui
        # ferait un plancher rigide débordant hors de l'écran quand la fenêtre
        # est petite (bug observé : pochette coupée, contrôles poussés dehors).
        #
        # L'AspectFrame (ratio 1, obey_child=False) dérive sa hauteur de sa
        # largeur : quand la largeur est contrainte (fenêtre étroite ou plafond
        # du Clamp), la hauteur suit, donc la pochette reste carrée sans jamais
        # forcer une hauteur minimale. Un Adw.Clamp interne plafonne la largeur
        # à 440 sur grand écran ; en dessous, tout se réduit proportionnellement.
        #
        # Le placeholder disque reste visible SOUS la Picture tant qu'aucune
        # image n'est chargée : l'AspectFrame est l'enfant PRINCIPAL de
        # l'Overlay (il dimensionne), le placeholder est l'overlay centré. En
        # faire l'enfant principal écraserait tout à la taille de l'icône.
        self._cover = Gtk.Picture(content_fit=Gtk.ContentFit.COVER)
        aspect = Gtk.AspectFrame(
            ratio=1.0, obey_child=False, hexpand=True, vexpand=False,
            overflow=Gtk.Overflow.HIDDEN, child=self._cover)
        self._cover_placeholder = Gtk.Image(
            icon_name='media-optical-symbolic', pixel_size=128,
            css_classes=['dim-label'],
            halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)

        overlay = Gtk.Overlay(hexpand=True)
        overlay.set_child(aspect)
        overlay.add_overlay(self._cover_placeholder)

        # La pochette EST le lien vers l'album (ou la playlist) d'où vient la
        # lecture : c'est la cible la plus évidente, et elle évite un bouton
        # texte de plus sous les contrôles. Même motif que la ligne « Derniers
        # ajouts » de l'accueil — un Gtk.Button dépouillé par
        # .jewelbox-cover-button, dont la pochette est l'unique enfant.
        #
        # .jewelbox-cover passe ici (du parent Overlay au bouton) pour que
        # l'arrondi et le rognage suivent la surface réellement cliquée.
        self._cover_button = Gtk.Button(
            child=overlay, hexpand=True, can_shrink=True,
            css_classes=['flat', 'jewelbox-cover-button', 'jewelbox-cover'])
        self._cover_button.connect('clicked', self._on_open_source)

        return Adw.Clamp(
            child=self._cover_button, maximum_size=440,
            tightening_threshold=440, halign=Gtk.Align.CENTER)

    # ── Titre · artiste · album (titre + cœur sur la même ligne) ─────────────

    def _build_titles(self):
        # Titre centré, cœur épinglé à droite de la même ligne : un Overlay
        # centre le titre sur toute la largeur et pose le cœur en surimpression
        # à droite (parité avec le Box/align.CenterEnd d'Android). Le titre est
        # bordé de marges pour ne pas glisser sous le cœur.
        # Titre sur deux lignes maximum, tronqué au-delà (parité maxLines=2 /
        # Ellipsis d'Android) : wrap + ellipsize END + lines=2 se combinent.
        self._title_label = Gtk.Label(
            wrap=True, justify=Gtk.Justification.CENTER, max_width_chars=28,
            lines=2, ellipsize=Pango.EllipsizeMode.END, halign=Gtk.Align.CENTER,
            css_classes=['title-2'], margin_start=48, margin_end=48)

        self._favorite_button = Gtk.ToggleButton(
            icon_name='jewelbox-not-favorite-symbolic',
            css_classes=['flat', 'circular'],
            valign=Gtk.Align.CENTER, halign=Gtk.Align.END,
            tooltip_text=with_shortcut(_('Favori'), 'toggle-favorite'))
        self._favorite_button.connect('toggled', self._on_favorite_toggled)

        # Pendant du cœur, à gauche : ajouter la piste en cours à une playlist.
        self._add_button = Gtk.Button(
            icon_name='list-add-symbolic', css_classes=['flat', 'circular'],
            valign=Gtk.Align.CENTER, halign=Gtk.Align.START,
            tooltip_text=_('Ajouter à une playlist'))
        self._add_button.connect('clicked', self._on_add_to_playlist)

        title_row = Gtk.Overlay(child=self._title_label)
        title_row.add_overlay(self._add_button)
        title_row.add_overlay(self._favorite_button)

        self._artist_label = Gtk.Label(
            css_classes=['title-4', 'accent'], halign=Gtk.Align.CENTER,
            ellipsize=Pango.EllipsizeMode.END, margin_top=8)
        self._album_label = Gtk.Label(
            css_classes=['dim-label'], halign=Gtk.Align.CENTER, visible=False,
            ellipsize=Pango.EllipsizeMode.END)
        # Nom de la playlist / liste intelligente d'où vient la lecture ; masqué
        # pour un album ou une piste seule.
        self._source_label = Gtk.Label(
            css_classes=['caption', 'dim-label'], halign=Gtk.Align.CENTER,
            visible=False, margin_top=8, ellipsize=Pango.EllipsizeMode.END)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        box.append(title_row)
        box.append(self._artist_label)
        box.append(self._album_label)
        box.append(self._source_label)
        return box

    # ── Barre de recherche + temps ───────────────────────────────────────────

    def _build_seek(self):
        # Même mécanique de seek que la barre de lecture (voir player_bar.py) :
        # « change-value » couvre toute interaction et fournit la cible en
        # direct ; on seek tout de suite et _seeking gèle _on_state le temps
        # du geste, dégelé peu après le dernier mouvement.
        self._seek_scale = Gtk.Scale(
            orientation=Gtk.Orientation.HORIZONTAL,
            draw_value=False, hexpand=True)
        self._seek_scale.set_range(0, 1)
        self._seek_scale.connect('change-value', self._on_seek_change_value)

        self._position_label = Gtk.Label(
            label='0:00', xalign=0, css_classes=['numeric', 'caption', 'dim-label'])
        self._duration_label = Gtk.Label(
            label='0:00', xalign=1, css_classes=['numeric', 'caption', 'dim-label'])
        times = Gtk.Box(hexpand=True)
        times.append(self._position_label)
        times.append(Gtk.Box(hexpand=True))
        times.append(self._duration_label)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.append(self._seek_scale)
        box.append(times)
        return box

    # ── Contrôles de transport ───────────────────────────────────────────────

    def _build_controls(self):
        self._shuffle_button = Gtk.ToggleButton(
            icon_name='media-playlist-shuffle-symbolic', css_classes=['flat'],
            valign=Gtk.Align.CENTER, tooltip_text=with_shortcut(_('Lecture aléatoire'), 'toggle-shuffle'))
        # _updating_shuffle protège le set_active programmatique de _on_state :
        # sinon resynchroniser le bouton réémettrait « toggled » et rappellerait
        # toggle_shuffle → _publish → _on_state en boucle (cf. player_bar).
        self._updating_shuffle = False
        self._shuffle_button.connect('toggled', self._on_shuffle_toggled)

        self._previous_button = Gtk.Button(
            icon_name='media-skip-backward-symbolic', css_classes=['flat'],
            valign=Gtk.Align.CENTER, tooltip_text=with_shortcut(_('Précédent'), 'previous'))
        self._previous_button.connect(
            'clicked', lambda *_a: self._app.playback.previous())

        self._play_pause_button = Gtk.Button(
            icon_name='media-playback-start-symbolic',
            css_classes=['flat', 'circular', 'jewelbox-full-play'],
            valign=Gtk.Align.CENTER, tooltip_text=with_shortcut(_('Lecture/Pause'), 'play-pause'))
        self._play_pause_button.connect(
            'clicked', lambda *_a: self._app.playback.toggle_play_pause())

        self._next_button = Gtk.Button(
            icon_name='media-skip-forward-symbolic', css_classes=['flat'],
            valign=Gtk.Align.CENTER, tooltip_text=with_shortcut(_('Suivant'), 'next'))
        self._next_button.connect(
            'clicked', lambda *_a: self._app.playback.next())

        self._repeat_button = Gtk.Button(
            icon_name='media-playlist-repeat-symbolic', css_classes=['flat'],
            valign=Gtk.Align.CENTER, tooltip_text=with_shortcut(_('Répétition'), 'cycle-repeat'))
        self._repeat_button.connect(
            'clicked', lambda *_a: self._app.playback.cycle_repeat())

        controls = Gtk.Box(spacing=12, halign=Gtk.Align.CENTER)
        controls.append(self._shuffle_button)
        controls.append(self._previous_button)
        controls.append(self._play_pause_button)
        controls.append(self._next_button)
        controls.append(self._repeat_button)
        return controls

    # ── File d'attente + retour à la source ──────────────────────────────────

    def _build_queue(self):
        """La file en cours, dépliable sous les contrôles.

        Repliée par défaut : le lecteur garde son allure « pochette + contrôles »
        habituelle, et la file ne s'ouvre que si on la demande. Le bouton
        d'en-tête porte le nombre de pistes, pour savoir ce qu'on va ouvrir
        avant de cliquer.
        """
        self._queue_toggle = Gtk.ToggleButton(css_classes=['flat'])
        self._queue_toggle_label = Gtk.Label()
        self._queue_toggle_arrow = Gtk.Image(icon_name='pan-down-symbolic')
        toggle_box = Gtk.Box(spacing=6, halign=Gtk.Align.CENTER)
        toggle_box.append(self._queue_toggle_label)
        toggle_box.append(self._queue_toggle_arrow)
        self._queue_toggle.set_child(toggle_box)
        self._queue_toggle.connect('toggled', self._on_queue_toggled)

        self._queue_box = Gtk.ListBox(
            selection_mode=Gtk.SelectionMode.NONE,
            css_classes=['boxed-list'], margin_top=8)
        self._queue_box.connect('row-activated', self._on_queue_row_activated)

        self._queue_revealer = Gtk.Revealer(
            child=self._queue_box,
            transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN)

        self._queue_section = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=0,
            margin_top=16, visible=False)
        self._queue_section.append(self._queue_toggle)
        self._queue_section.append(self._queue_revealer)
        return self._queue_section

    def _on_queue_toggled(self, button):
        expanded = button.get_active()
        self._queue_revealer.set_reveal_child(expanded)
        self._queue_toggle_arrow.set_from_icon_name(
            'pan-up-symbolic' if expanded else 'pan-down-symbolic')
        if expanded:
            # Ouvrir la file doit montrer où on en est : on amène la piste
            # courante à l'écran une fois le dépliage terminé (le Revealer
            # anime, la ligne n'a pas encore sa position finale avant).
            GLib.timeout_add(250, self._scroll_to_current)

    def _scroll_to_current(self):
        row = (self._queue_rows[self._queue_index]
               if self._queue_index is not None
               and 0 <= self._queue_index < len(self._queue_rows) else None)
        # grab_focus() sur une ligne d'une liste défilante demande au
        # ScrolledWindow ancêtre de la rendre visible — plus simple et plus sûr
        # que de calculer nous-mêmes une position d'ajustement.
        if row is not None and self._queue_revealer.get_reveal_child():
            row.grab_focus()
        return False  # one-shot

    # ── Paroles ──────────────────────────────────────────────────────────────

    def _build_lyrics(self):
        """Les paroles de la piste en cours, dépliables sous la file comme
        elle. Synchronisées, elles suivent la musique (la ligne chantée en
        avant, amenée à l'écran) et un clic sur une ligne y saute ; sinon le
        texte brut. Demandées au serveur seulement une fois dépliées."""
        self._lyrics_track_id = None   # piste des paroles demandées / affichées
        self._lyrics = None
        self._lyrics_rows = []
        self._lyrics_index = -1

        self._lyrics_toggle = Gtk.ToggleButton(css_classes=['flat'])
        self._lyrics_toggle_arrow = Gtk.Image(icon_name='pan-down-symbolic')
        toggle_box = Gtk.Box(spacing=6, halign=Gtk.Align.CENTER)
        toggle_box.append(Gtk.Label(label=_('Paroles')))
        toggle_box.append(self._lyrics_toggle_arrow)
        self._lyrics_toggle.set_child(toggle_box)
        self._lyrics_toggle.connect('toggled', self._on_lyrics_toggled)

        self._lyrics_message = Gtk.Label(
            wrap=True, justify=Gtk.Justification.CENTER, selectable=True,
            css_classes=['dim-label'], margin_top=8)
        self._lyrics_list = Gtk.ListBox(
            selection_mode=Gtk.SelectionMode.NONE,
            css_classes=['background'], margin_top=8, visible=False)
        self._lyrics_list.connect('row-activated', self._on_lyric_activated)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content.append(self._lyrics_message)
        content.append(self._lyrics_list)
        self._lyrics_revealer = Gtk.Revealer(
            child=content,
            transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN)

        section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, margin_top=8)
        section.append(self._lyrics_toggle)
        section.append(self._lyrics_revealer)
        return section

    def _on_lyrics_toggled(self, button):
        expanded = button.get_active()
        self._lyrics_revealer.set_reveal_child(expanded)
        self._lyrics_toggle_arrow.set_from_icon_name(
            'pan-up-symbolic' if expanded else 'pan-down-symbolic')
        if expanded and self._last_state is not None:
            self._update_lyrics(self._last_state)

    def _update_lyrics(self, state):
        track_id = state.current_track_id
        if track_id != self._lyrics_track_id:
            self._lyrics_track_id = None
            self._lyrics = None
            self._show_lyrics_message('')
            if track_id is not None and self._lyrics_revealer.get_reveal_child():
                self._lyrics_track_id = track_id
                self._show_lyrics_message(_('Recherche des paroles…'))
                asyncio.get_event_loop_policy().get_event_loop().create_task(
                    self._load_lyrics(track_id))
        if self._lyrics is not None and self._lyrics.synced:
            self._highlight_lyric(current_line_index(
                self._lyrics.lines, state.position_seconds or 0.0))

    async def _load_lyrics(self, track_id):
        client = self._app.get_client()
        try:
            if client is None:
                raise ApiError('no server')
            lyrics = parse_lyrics(await client.lyrics(track_id))
        except ApiError:
            if self._lyrics_track_id == track_id:
                self._show_lyrics_message(
                    _('Paroles indisponibles (serveur injoignable ou antérieur à la 1.20).'))
            return
        if self._lyrics_track_id != track_id:
            return
        self._lyrics = lyrics
        if lyrics.instrumental:
            self._show_lyrics_message(_('Morceau instrumental.'))
        elif lyrics.synced:
            self._show_lyric_lines(lyrics.lines)
        elif lyrics.plain:
            self._show_lyrics_message(lyrics.plain, dim=False)
        else:
            self._show_lyrics_message(_('Pas de paroles trouvées pour ce titre.'))

    def _show_lyrics_message(self, text, dim=True):
        while (row := self._lyrics_list.get_row_at_index(0)) is not None:
            self._lyrics_list.remove(row)
        self._lyrics_rows = []
        self._lyrics_index = -1
        self._lyrics_list.set_visible(False)
        self._lyrics_message.set_label(text)
        self._lyrics_message.set_visible(bool(text))
        self._lyrics_message.set_css_classes(['dim-label'] if dim else [])

    def _show_lyric_lines(self, lines):
        self._show_lyrics_message('')
        for line in lines:
            label = Gtk.Label(
                label=line.text or '♪', wrap=True,
                justify=Gtk.Justification.CENTER, css_classes=['dim-label'],
                margin_top=3, margin_bottom=3)
            row = Gtk.ListBoxRow(child=label, activatable=True)
            row.lyric_time = line.time
            self._lyrics_list.append(row)
            self._lyrics_rows.append(row)
        self._lyrics_list.set_visible(True)
        if self._last_state is not None:
            self._highlight_lyric(current_line_index(
                lines, self._last_state.position_seconds or 0.0))

    def _highlight_lyric(self, index):
        if index == self._lyrics_index:
            return
        for i in (self._lyrics_index, index):
            if 0 <= i < len(self._lyrics_rows):
                self._lyrics_rows[i].get_child().set_css_classes(
                    ['heading'] if i == index else ['dim-label'])
        self._lyrics_index = index
        if 0 <= index < len(self._lyrics_rows) and self._lyrics_revealer.get_reveal_child():
            self._scroll_into_view(self._lyrics_rows[index])

    def _scroll_into_view(self, widget):
        """Centre la ligne chantée dans la page, sans voler le focus."""
        ok, bounds = widget.compute_bounds(self._scroller.get_child())
        if not ok:
            return
        adjustment = self._scroller.get_vadjustment()
        target = bounds.get_y() + bounds.get_height() / 2 - adjustment.get_page_size() / 2
        upper = adjustment.get_upper() - adjustment.get_page_size()
        adjustment.set_value(max(0.0, min(upper, target)))

    def _on_lyric_activated(self, _box, row):
        if self._app.playback is not None and hasattr(row, 'lyric_time'):
            self._app.playback.seek(row.lyric_time)

    def _move(self, from_index, to_index):
        if self._app.playback is not None:
            self._app.playback.move_in_queue(from_index, to_index)

    def _remove_from_queue(self, index):
        if self._app.playback is not None:
            self._app.playback.remove_from_queue(index)

    def _build_queue_rows(self, items):
        self._last_queue_items = tuple(items)
        while (row := self._queue_box.get_row_at_index(0)) is not None:
            self._queue_box.remove(row)
        self._queue_rows = []
        for index, item in enumerate(items):
            self._queue_box.append(self._build_queue_row(index, item))

    def _build_queue_row(self, index, item):
        # Numéro de position dans la file (et non numéro de piste de l'album) :
        # en mode aléatoire comme sur une playlist, c'est l'ordre affiché qui
        # fait sens. Il laisse place à l'indicateur « ▸ » sur la piste courante.
        position_label = Gtk.Label(
            label=str(index + 1), width_chars=3, xalign=1.0,
            css_classes=['numeric', 'dim-label'])
        playing_icon = Gtk.Image(
            icon_name='media-playback-start-symbolic', visible=False,
            css_classes=['accent'])
        leading = Gtk.Box(width_request=28, halign=Gtk.Align.END)
        leading.append(position_label)
        leading.append(playing_icon)

        title_label = Gtk.Label(
            label=item.title, xalign=0, ellipsize=Pango.EllipsizeMode.END)
        # Artiste en légende sous le titre : redondant sur un album (artiste
        # unique) mais discret, et indispensable sur une playlist où chaque
        # piste peut venir d'un artiste différent.
        artist_label = Gtk.Label(
            label=item.artist_name, xalign=0, css_classes=['caption', 'dim-label'],
            ellipsize=Pango.EllipsizeMode.END, visible=bool(item.artist_name))
        text_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, hexpand=True,
            valign=Gtk.Align.CENTER)
        text_box.append(title_label)
        text_box.append(artist_label)

        # Réordonner / retirer : l'indice est celui de l'ordre affiché, les
        # lignes étant reconstruites à chaque changement de la file.
        count = len(self._last_queue_items)
        entries = []
        if index > 0:
            entries.append((_('Monter'), lambda: self._move(index, index - 1)))
        if index < count - 1:
            entries.append((_('Descendre'),
                            lambda: self._move(index, index + 1)))
        entries.append((_('Retirer de la file'),
                        lambda: self._remove_from_queue(index)))
        row_menu = menu_button(entries, tooltip=_('Options de la file'))

        row_box = Gtk.Box(spacing=12, margin_top=6, margin_bottom=6,
                          margin_start=12, margin_end=6)
        row_box.append(leading)
        row_box.append(text_box)
        row_box.append(row_menu)

        row = Gtk.ListBoxRow(activatable=True, child=row_box)
        # L'indice est porté par la row : « row-activated » ne transmet que la
        # ligne, c'est là qu'on retrouve à quelle piste elle correspond.
        row._queue_index = index
        row._position_label = position_label
        row._playing_icon = playing_icon
        self._queue_rows.append(row)
        return row

    def _on_queue_row_activated(self, _listbox, row):
        playback = self._app.playback
        if playback is not None:
            playback.jump_to(row._queue_index)

    def _update_queue_highlight(self, state):
        for index, row in enumerate(self._queue_rows):
            is_current = index == state.queue_index
            # Sur la piste courante, le numéro cède la place à l'indicateur de
            # lecture (▸ en lecture, ⏸ en pause) — le même vocabulaire visuel
            # que la fiche album, où la ligne courante passe aussi en accent.
            row._position_label.set_visible(not is_current)
            row._playing_icon.set_visible(is_current)
            if is_current:
                row._playing_icon.set_from_icon_name(
                    'media-playback-start-symbolic' if state.is_playing
                    else 'media-playback-pause-symbolic')
                row.add_css_class('accent')
            else:
                row.remove_css_class('accent')

    def _update_cover_link(self, state):
        """Règle l'infobulle de la pochette et son activabilité.

        Rien ne signale visuellement qu'elle est cliquable (pas de fond, pas de
        bordure : c'est une pochette, pas un bouton) — l'infobulle nomme donc
        explicitement la destination. Quand l'origine est inconnue, le bouton
        est désensibilisé plutôt que masqué : la pochette doit rester à
        l'écran, elle est le sujet de la page."""
        labels = {
            'album': _('Aller à l’album'),
            'playlist': _('Aller à la playlist'),
            'smart': _('Aller à la liste'),
        }
        label = labels.get(state.source_type)
        can_open = label is not None and state.source_id is not None
        self._cover_button.set_sensitive(can_open)
        if not can_open:
            self._cover_button.set_tooltip_text(None)
            return
        # Un album n'a pas de source_name (voir play_album) : on retombe sur le
        # titre de l'album de la piste courante pour nommer la destination.
        destination = state.source_name or state.album or ''
        self._cover_button.set_tooltip_text(
            _('Ouvrir « {name} »').format(name=destination)
            if destination else label)

    def _on_open_source(self, _button):
        state = self._last_state
        if state is None or state.source_id is None:
            return
        # album/playlist sont identifiés par un id numérique, les listes
        # intelligentes par une clé texte — l'instantané garde les deux sous
        # forme de texte (forme JSON commune), d'où la conversion ici.
        if state.source_type == 'smart':
            if self.on_open_smart_playlist is not None:
                self.on_open_smart_playlist(state.source_id)
            return
        try:
            item_id = int(state.source_id)
        except (TypeError, ValueError):
            return
        if state.source_type == 'album' and self.on_open_album is not None:
            self.on_open_album(item_id)
        elif state.source_type == 'playlist' and self.on_open_playlist is not None:
            self.on_open_playlist(item_id)

    # ── Interactions ─────────────────────────────────────────────────────────

    def _on_seek_change_value(self, _scale, _scroll_type, value):
        self._seeking = True
        self._app.playback.seek(value)
        self._position_label.set_label(format_duration(value))
        if self._release_id is not None:
            GLib.source_remove(self._release_id)
        self._release_id = GLib.timeout_add(250, self._end_seek)
        return False  # laisse GTK bouger le curseur à `value`

    def _end_seek(self):
        self._seeking = False
        self._release_id = None
        return False  # one-shot

    def _on_shuffle_toggled(self, _button):
        # N'agit qu'au clic utilisateur, jamais au set_active programmatique de
        # _on_state (protégé par _updating_shuffle) — voir player_bar.
        if self._updating_shuffle:
            return
        self._app.playback.toggle_shuffle()

    def _on_add_to_playlist(self, _button):
        state = self._last_state
        if state is None or state.current_track_id is None:
            return
        present_add_to_playlist(self._app, self,
                                track_id=state.current_track_id)

    def _on_favorite_toggled(self, _button):
        # set_active programmatique de _on_state protégé par _updating_favorite.
        if getattr(self, '_updating_favorite', False):
            return
        self._app.playback.toggle_favorite()

    # ── État ─────────────────────────────────────────────────────────────────

    def _update_queue(self, state):
        """Reflète la file du state. Reconstruire les lignes coûte cher et
        _on_state arrive plusieurs fois par seconde (position de lecture) : on
        ne redessine que si la file elle-même a changé, et on se contente
        sinon de déplacer la surbrillance."""
        signature = tuple(item.track_id for item in state.queue_items)
        if signature != self._queue_signature:
            self._queue_signature = signature
            self._build_queue_rows(state.queue_items)
            # Le compteur n'a de sens qu'avec plus d'une piste ; sous ce seuil
            # la section entière disparaît (une file d'une seule piste n'a rien
            # à montrer que le lecteur n'affiche déjà).
            self._queue_toggle_label.set_label(
                _('File d’attente ({count})').format(count=len(signature)))
            self._queue_toggle.set_visible(len(signature) > 1)
            if len(signature) <= 1 and self._queue_toggle.get_active():
                self._queue_toggle.set_active(False)
        self._queue_index = state.queue_index
        self._update_queue_highlight(state)
        self._update_cover_link(state)
        self._queue_section.set_visible(self._queue_toggle.get_visible())

    def _on_state(self, state):
        if not state.has_item:
            if self.on_closed is not None:
                self.on_closed()
            return

        self._last_state = state
        self._update_queue(state)
        self._update_lyrics(state)
        self._update_sleep(state)
        self._title_label.set_label(state.title or '')
        self._artist_label.set_label(state.artist or '')
        self._album_label.set_label(state.album or '')
        self._album_label.set_visible(bool(state.album))
        self._source_label.set_label(
            _('Depuis : {name}').format(name=state.source_name)
            if state.source_name else '')
        self._source_label.set_visible(bool(state.source_name))

        self._play_pause_button.set_icon_name(
            'media-playback-pause-symbolic' if state.is_playing
            else 'media-playback-start-symbolic')
        self._previous_button.set_sensitive(state.has_previous)
        self._next_button.set_sensitive(state.has_next)

        self._updating_shuffle = True
        self._shuffle_button.set_active(state.shuffle)
        self._updating_shuffle = False
        self._shuffle_button.set_css_classes(
            ['flat', 'accent'] if state.shuffle else ['flat'])
        self._repeat_button.set_icon_name({
            'off': 'media-playlist-repeat-symbolic',
            'all': 'media-playlist-repeat-symbolic',
            'one': 'media-playlist-repeat-song-symbolic',
        }[state.repeat])
        self._repeat_button.set_css_classes(
            ['flat'] if state.repeat == 'off' else ['flat', 'accent'])

        self._updating_favorite = True
        self._favorite_button.set_active(state.is_favorite)
        self._favorite_button.set_icon_name(
            'jewelbox-favorite-symbolic' if state.is_favorite
            else 'jewelbox-not-favorite-symbolic')
        self._favorite_button.set_css_classes(
            ['flat', 'circular', 'error'] if state.is_favorite
            else ['flat', 'circular'])
        self._updating_favorite = False

        if not self._seeking:
            self._seek_scale.set_range(0, max(state.duration_seconds, 1))
            self._seek_scale.set_value(state.position_seconds)
        self._position_label.set_label(format_duration(state.position_seconds))
        self._duration_label.set_label(format_duration(state.duration_seconds))

        if state.cover_url != self._cover_url:
            self._cover_url = state.cover_url
            self._cover.set_paintable(None)
            self._cover_placeholder.set_visible(True)
            if state.cover_url:
                task = self._load_cover(state.cover_url)
                asyncio.get_event_loop_policy().get_event_loop().create_task(task)

    async def _load_cover(self, url):
        client = self._app.get_client()
        if client is None:
            return
        try:
            data = await client.fetch_bytes(url)
        except ApiError:
            return
        try:
            texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(data))
        except GLib.Error:
            return
        if self._cover_url == url:
            self._cover.set_paintable(texture)
            self._cover_placeholder.set_visible(False)
