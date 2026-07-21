"""Grand lecteur (« now playing ») : grande pochette, titre/artiste/album,
barre de progression et contrôles de transport.

Parité avec l'écran plein du client Android (NowPlayingScreen) : grande
pochette carrée en haut, titre centré avec le cœur favori épinglé à droite
de la même ligne, artiste (en accent), album, puis la barre de recherche
avec ses deux temps et enfin la rangée de contrôles (aléatoire · précédent ·
lecture · suivant · répétition). Ouvert en cliquant sur le mini-lecteur ;
empilé par-dessus les onglets dans le NavigationView.

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
        # Appelé quand la file se vide (plus rien ne joue) : la fenêtre dépile
        # le grand lecteur, sinon il resterait figé sur la dernière piste.
        self.on_closed = None

        content = self._build_content()
        # Centré et plafonné : sur une fenêtre large, la pochette et les
        # contrôles restent groupés au milieu (colonne d'au plus 400px) plutôt
        # que de s'étirer sur toute la largeur — parité avec la colonne à
        # marges du grand lecteur mobile. Adw.Clamp gère le centrage horizontal.
        content.set_valign(Gtk.Align.CENTER)
        content.set_vexpand(True)
        clamp = Adw.Clamp(
            child=content, maximum_size=560, tightening_threshold=480,
            margin_start=24, margin_end=24, margin_top=16, margin_bottom=24)
        scroller = Gtk.ScrolledWindow(
            child=clamp, hscrollbar_policy=Gtk.PolicyType.NEVER,
            vexpand=True)
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
        return box

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
        placeholder = Gtk.Image(
            icon_name='media-optical-symbolic', pixel_size=128,
            css_classes=['dim-label'],
            halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)

        overlay = Gtk.Overlay(css_classes=['jewelbox-cover'], hexpand=True)
        overlay.set_child(aspect)
        overlay.add_overlay(placeholder)

        return Adw.Clamp(
            child=overlay, maximum_size=440, tightening_threshold=440,
            halign=Gtk.Align.CENTER)

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
            icon_name='non-starred-symbolic', css_classes=['flat', 'circular'],
            valign=Gtk.Align.CENTER, halign=Gtk.Align.END,
            tooltip_text=_('Favori'))
        self._favorite_button.connect('toggled', self._on_favorite_toggled)

        title_row = Gtk.Overlay(child=self._title_label)
        title_row.add_overlay(self._favorite_button)

        self._artist_label = Gtk.Label(
            css_classes=['title-4', 'accent'], halign=Gtk.Align.CENTER,
            ellipsize=Pango.EllipsizeMode.END, margin_top=8)
        self._album_label = Gtk.Label(
            css_classes=['dim-label'], halign=Gtk.Align.CENTER, visible=False,
            ellipsize=Pango.EllipsizeMode.END)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        box.append(title_row)
        box.append(self._artist_label)
        box.append(self._album_label)
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
            valign=Gtk.Align.CENTER, tooltip_text=_('Lecture aléatoire'))
        # _updating_shuffle protège le set_active programmatique de _on_state :
        # sinon resynchroniser le bouton réémettrait « toggled » et rappellerait
        # toggle_shuffle → _publish → _on_state en boucle (cf. player_bar).
        self._updating_shuffle = False
        self._shuffle_button.connect('toggled', self._on_shuffle_toggled)

        self._previous_button = Gtk.Button(
            icon_name='media-skip-backward-symbolic', css_classes=['flat'],
            valign=Gtk.Align.CENTER, tooltip_text=_('Précédent'))
        self._previous_button.connect(
            'clicked', lambda *_a: self._app.playback.previous())

        self._play_pause_button = Gtk.Button(
            icon_name='media-playback-start-symbolic',
            css_classes=['flat', 'circular', 'jewelbox-full-play'],
            valign=Gtk.Align.CENTER, tooltip_text=_('Lecture/Pause'))
        self._play_pause_button.connect(
            'clicked', lambda *_a: self._app.playback.toggle_play_pause())

        self._next_button = Gtk.Button(
            icon_name='media-skip-forward-symbolic', css_classes=['flat'],
            valign=Gtk.Align.CENTER, tooltip_text=_('Suivant'))
        self._next_button.connect(
            'clicked', lambda *_a: self._app.playback.next())

        self._repeat_button = Gtk.Button(
            icon_name='media-playlist-repeat-symbolic', css_classes=['flat'],
            valign=Gtk.Align.CENTER, tooltip_text=_('Répétition'))
        self._repeat_button.connect(
            'clicked', lambda *_a: self._app.playback.cycle_repeat())

        controls = Gtk.Box(spacing=12, halign=Gtk.Align.CENTER)
        controls.append(self._shuffle_button)
        controls.append(self._previous_button)
        controls.append(self._play_pause_button)
        controls.append(self._next_button)
        controls.append(self._repeat_button)
        return controls

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

    def _on_favorite_toggled(self, _button):
        # set_active programmatique de _on_state protégé par _updating_favorite.
        if getattr(self, '_updating_favorite', False):
            return
        self._app.playback.toggle_favorite()

    # ── État ─────────────────────────────────────────────────────────────────

    def _on_state(self, state):
        if not state.has_item:
            if self.on_closed is not None:
                self.on_closed()
            return

        self._title_label.set_label(state.title or '')
        self._artist_label.set_label(state.artist or '')
        self._album_label.set_label(state.album or '')
        self._album_label.set_visible(bool(state.album))

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
            'starred-symbolic' if state.is_favorite else 'non-starred-symbolic')
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
