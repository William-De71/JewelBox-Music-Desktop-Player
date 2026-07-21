"""Barre de lecture persistante (mini-lecteur) : reproduit le mini-lecteur
web de JewelBox (client/src/components/PlayerBar.jsx) — trois zones :

  gauche  : pochette + titre/artiste
  centre  : contrôles (aléatoire · précédent · lecture · suivant · répétition)
            au-dessus de la barre de progression (temps · curseur · temps)
  droite  : favori · volume · fermer

Parité fonctionnelle avec le mini-lecteur Android/web (piloté par
PlaybackSession) mais sans MPRIS ni notification système ici — ça viendra
dans une phase dédiée. Masquée tant qu'aucune piste n'a été chargée
(has_item == False).
"""

import asyncio
from gettext import gettext as _

from gi.repository import Gdk, GLib, Gtk

from jewelbox.api.client import ApiError
from jewelbox.core.formats import format_duration


class PlayerBar(Gtk.CenterBox):

    def __init__(self, application):
        super().__init__(
            visible=False, css_classes=['jewelbox-player-bar'],
            margin_start=16, margin_end=16, margin_top=8, margin_bottom=8)
        self._app = application
        self._seeking = False   # vrai pendant un glisser manuel du curseur
        self._cover_url = None
        self._volume = 1.0      # dernier volume non nul, pour le rétablir
        # Appelé quand l'utilisateur clique la zone info (pochette + titre) :
        # ouvre le grand lecteur, comme un tap sur le mini-lecteur Android.
        self.on_open_full_player = None
        # Dernière valeur de has_item : quand le grand lecteur se ferme, la
        # fenêtre appelle restore_visibility() qui s'y réfère (le mini-lecteur
        # ne doit réapparaître que si une piste est effectivement chargée).
        self._has_item = False
        # Posé par la fenêtre quand le grand lecteur est ouvert : le mini-
        # lecteur reste masqué même si _on_state continue d'arriver (la position
        # avance en continu et rappellerait sinon set_visible(True) à chaque
        # tick, réaffichant le mini-lecteur par-dessus le grand lecteur).
        self._suppressed = False

        self.set_start_widget(self._build_info())
        self.set_center_widget(self._build_center())
        self.set_end_widget(self._build_right())

        if application.playback is not None:
            application.playback.add_listener(self._on_state)

    # ── Zone gauche : pochette + infos ───────────────────────────────────────

    def _build_info(self):
        # Pochette carrée stricte : overflow=HIDDEN recadre la Picture au
        # cadre 96×96, halign/valign=CENTER empêchent la Box parente de
        # l'étirer — sinon COVER produit un rectangle au lieu d'un carré.
        self._cover = Gtk.Picture(
            content_fit=Gtk.ContentFit.COVER,
            width_request=96, height_request=96,
            overflow=Gtk.Overflow.HIDDEN,
            halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        # Icône disque en attendant/à défaut de pochette (parité avec le
        # placeholder <Disc> du mini-lecteur web), sous la Picture.
        placeholder = Gtk.Image(
            icon_name='media-optical-symbolic', pixel_size=40,
            css_classes=['dim-label'])
        cover_frame = Gtk.Overlay(
            width_request=96, height_request=96,
            halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER,
            css_classes=['jewelbox-cover'])
        cover_frame.set_child(placeholder)
        cover_frame.add_overlay(self._cover)

        # Jamais tronqué : pas d'ellipsize ni de max_width_chars, le titre et
        # l'artiste s'affichent en entier (le label prend la largeur qu'il
        # faut, la zone info garde sa taille naturelle dans la CenterBox).
        self._title_label = Gtk.Label(xalign=0, css_classes=['heading'])
        self._artist_label = Gtk.Label(
            xalign=0, css_classes=['caption', 'dim-label'])
        labels = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER)
        labels.append(self._title_label)
        labels.append(self._artist_label)

        info = Gtk.Box(spacing=12, valign=Gtk.Align.CENTER)
        info.append(cover_frame)
        info.append(labels)

        # Un clic sur la zone info ouvre le grand lecteur (parité avec le tap
        # sur le mini-lecteur Android). Les contrôles de transport vivent dans
        # la zone centrale et gardent donc leurs propres clics. Le curseur main
        # signale la zone cliquable.
        info.set_cursor(Gdk.Cursor.new_from_name('pointer', None))
        click = Gtk.GestureClick()
        click.connect('released', self._on_info_clicked)
        info.add_controller(click)
        return info

    def _on_info_clicked(self, *_args):
        if self.on_open_full_player is not None:
            self.on_open_full_player()

    # ── Zone centrale : contrôles + progression ──────────────────────────────

    def _build_center(self):
        self._shuffle_button = Gtk.ToggleButton(
            icon_name='media-playlist-shuffle-symbolic', css_classes=['flat'],
            valign=Gtk.Align.CENTER, tooltip_text=_('Lecture aléatoire'))
        self._shuffle_button.connect(
            'toggled', lambda *_a: self._app.playback.toggle_shuffle())

        self._previous_button = Gtk.Button(
            icon_name='media-skip-backward-symbolic',
            css_classes=['flat'], valign=Gtk.Align.CENTER,
            tooltip_text=_('Précédent'))
        self._previous_button.connect(
            'clicked', lambda *_a: self._app.playback.previous())

        self._play_pause_button = Gtk.Button(
            icon_name='media-playback-start-symbolic',
            css_classes=['flat', 'circular', 'jewelbox-transport-play'],
            valign=Gtk.Align.CENTER, tooltip_text=_('Lecture/Pause'))
        self._play_pause_button.connect(
            'clicked', lambda *_a: self._app.playback.toggle_play_pause())

        self._next_button = Gtk.Button(
            icon_name='media-skip-forward-symbolic',
            css_classes=['flat'], valign=Gtk.Align.CENTER,
            tooltip_text=_('Suivant'))
        self._next_button.connect(
            'clicked', lambda *_a: self._app.playback.next())

        self._repeat_button = Gtk.Button(
            icon_name='media-playlist-repeat-symbolic', css_classes=['flat'],
            valign=Gtk.Align.CENTER, tooltip_text=_('Répétition'))
        self._repeat_button.connect(
            'clicked', lambda *_a: self._app.playback.cycle_repeat())

        controls = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER)
        controls.append(self._shuffle_button)
        controls.append(self._previous_button)
        controls.append(self._play_pause_button)
        controls.append(self._next_button)
        controls.append(self._repeat_button)

        self._position_label = Gtk.Label(
            label='0:00', css_classes=['numeric', 'caption', 'dim-label'],
            width_chars=4)
        self._duration_label = Gtk.Label(
            label='0:00', css_classes=['numeric', 'caption', 'dim-label'],
            width_chars=4)
        self._seek_scale = Gtk.Scale(
            orientation=Gtk.Orientation.HORIZONTAL,
            draw_value=False, hexpand=True)
        self._seek_scale.set_range(0, 1)
        # « change-value » couvre TOUTE interaction utilisateur (clic, glisser,
        # clavier, molette) et fournit la valeur cible en direct. On seek
        # directement ici : le GestureClick released n'est PAS fiable sur un
        # Gtk.Scale (son gestionnaire interne capture le glisser, notre
        # released n'arrive jamais), c'est ce qui laissait le seek sans effet.
        # GStreamer avale sans peine les seeks rapprochés (FLUSH). _release_id
        # lève _seeking un court instant après le dernier mouvement, pour que
        # _on_state reprenne la main sans « sauter » en cours de glisser.
        self._release_id = None
        self._seek_scale.connect('change-value', self._on_seek_change_value)

        progress = Gtk.Box(spacing=8, hexpand=True)
        progress.append(self._position_label)
        progress.append(self._seek_scale)
        progress.append(self._duration_label)

        # Colonne centrale (contrôles au-dessus, progression dessous). Pas de
        # width_request rigide : la CenterBox donne d'abord aux zones start
        # (infos, jamais tronquées) et end (volume) leur taille naturelle,
        # le centre prend le reste — le curseur de progression (hexpand)
        # s'étire ou se réduit sans jamais rogner le titre.
        center = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=2,
            halign=Gtk.Align.FILL, valign=Gtk.Align.CENTER, hexpand=True)
        center.append(controls)
        center.append(progress)
        return center

    # ── Zone droite : favori · volume · fermer ───────────────────────────────

    def _build_right(self):
        self._favorite_button = Gtk.ToggleButton(
            icon_name='non-starred-symbolic', css_classes=['flat'],
            valign=Gtk.Align.CENTER, tooltip_text=_('Favori'))
        self._favorite_button.connect('toggled', self._on_favorite_toggled)

        self._volume_button = Gtk.Button(
            icon_name='audio-volume-high-symbolic', css_classes=['flat'],
            valign=Gtk.Align.CENTER, tooltip_text=_('Couper le son'))
        self._volume_button.connect('clicked', self._on_volume_toggle)

        self._volume_scale = Gtk.Scale(
            orientation=Gtk.Orientation.HORIZONTAL, width_request=130,
            draw_value=False, valign=Gtk.Align.CENTER)
        self._volume_scale.set_range(0, 1)
        self._volume_scale.set_value(1.0)
        self._volume_scale.connect('value-changed', self._on_volume_changed)

        self._close_button = Gtk.Button(
            icon_name='window-close-symbolic', css_classes=['flat'],
            valign=Gtk.Align.CENTER, tooltip_text=_('Fermer le lecteur'))
        self._close_button.connect(
            'clicked', lambda *_a: self._app.playback.close())

        right = Gtk.Box(spacing=4, halign=Gtk.Align.END,
                        valign=Gtk.Align.CENTER)
        right.append(self._favorite_button)
        right.append(self._volume_button)
        right.append(self._volume_scale)
        right.append(self._close_button)
        return right

    # ── Interactions ─────────────────────────────────────────────────────────

    def _on_seek_change_value(self, _scale, _scroll_type, value):
        # Toute interaction (clic, glisser, clavier, molette). On seek tout de
        # suite à la valeur visée et on met à jour le libellé de position ;
        # _seeking gèle _on_state le temps du geste pour éviter que la synchro
        # auto ne rebatte le curseur pendant qu'on le déplace.
        self._seeking = True
        self._app.playback.seek(value)
        self._position_label.set_label(format_duration(value))
        # Rearme un dégel différé : quand les change-value cessent (fin du
        # glisser), _seeking retombe et _on_state reprend la synchro.
        if self._release_id is not None:
            GLib.source_remove(self._release_id)
        self._release_id = GLib.timeout_add(250, self._end_seek)
        return False  # laisse GTK bouger le curseur à `value`

    def _end_seek(self):
        self._seeking = False
        self._release_id = None
        return False  # one-shot

    def _on_favorite_toggled(self, button):
        # Le bascule vient de PlaybackSession (source de vérité) ; ce
        # gestionnaire ne réagit qu'à un clic utilisateur, pas au set_active
        # programmatique de _on_state (protégé par _updating_favorite).
        if getattr(self, '_updating_favorite', False):
            return
        self._app.playback.toggle_favorite()

    def _on_volume_changed(self, scale):
        if getattr(self, '_updating_volume', False):
            return
        value = scale.get_value()
        if value > 0:
            self._volume = value
        self._app.playback.set_volume(value)

    def _on_volume_toggle(self, _button):
        # Muet ↔ dernier volume non nul (parité avec le bouton mute du web).
        current = self._volume_scale.get_value()
        self._volume_scale.set_value(0.0 if current > 0 else (self._volume or 1.0))

    # ── État ─────────────────────────────────────────────────────────────────

    def suppress(self):
        """Appelée par la fenêtre à l'ouverture du grand lecteur : masque le
        mini-lecteur et l'y maintient malgré les _on_state qui continuent
        d'arriver (avance de la position)."""
        self._suppressed = True
        self.set_visible(False)

    def restore_visibility(self):
        """Rappelée par la fenêtre à la fermeture du grand lecteur, qui avait
        masqué le mini-lecteur : il ne réapparaît que si une piste est chargée."""
        self._suppressed = False
        self.set_visible(self._has_item)

    def _on_state(self, state):
        self._has_item = state.has_item
        # Tant que le grand lecteur est ouvert, le mini-lecteur reste masqué :
        # on met quand même à jour le contenu ci-dessous (il sera à jour au
        # retour), mais jamais la visibilité.
        self.set_visible(state.has_item and not self._suppressed)
        if not state.has_item:
            return

        self._title_label.set_label(state.title or '')
        self._artist_label.set_label(state.artist or '')
        self._play_pause_button.set_icon_name(
            'media-playback-pause-symbolic' if state.is_playing
            else 'media-playback-start-symbolic')
        self._previous_button.set_sensitive(state.has_previous)
        self._next_button.set_sensitive(state.has_next)

        self._shuffle_button.set_active(state.shuffle)
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
            'starred-symbolic' if state.is_favorite else 'non-starred-symbolic')
        self._favorite_button.set_css_classes(
            ['flat', 'error'] if state.is_favorite else ['flat'])
        self._updating_favorite = False

        self._updating_volume = True
        if abs(self._volume_scale.get_value() - state.volume) > 0.001:
            self._volume_scale.set_value(state.volume)
        self._volume_button.set_icon_name(
            'audio-volume-muted-symbolic' if state.volume <= 0
            else 'audio-volume-high-symbolic')
        self._volume_button.set_tooltip_text(
            _('Rétablir le son') if state.volume <= 0 else _('Couper le son'))
        self._updating_volume = False

        if state.cover_url != self._cover_url:
            self._cover_url = state.cover_url
            self._cover.set_paintable(None)
            if state.cover_url:
                task = self._load_cover(state.cover_url)
                asyncio.get_event_loop_policy().get_event_loop().create_task(task)

        if not self._seeking:
            self._seek_scale.set_range(0, max(state.duration_seconds, 1))
            self._seek_scale.set_value(state.position_seconds)
        self._position_label.set_label(format_duration(state.position_seconds))
        self._duration_label.set_label(format_duration(state.duration_seconds))

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
