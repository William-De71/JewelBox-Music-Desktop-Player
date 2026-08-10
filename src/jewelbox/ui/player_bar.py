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

from gi.repository import Adw, Gdk, GLib, GObject, Gtk, Pango

from jewelbox.api.client import ApiError
from jewelbox.core.formats import format_duration


def _strv(values):
    """Liste de chaînes en GValue GStrv, seul type que Adw.Breakpoint.add_setter
    accepte pour une propriété « css-classes »."""
    value = GObject.Value(GObject.TYPE_STRV)
    value.set_boxed(values)
    return value

# Largeur (px) de la zone texte titre/artiste en fenêtre large. Bornée pour
# qu'un titre long ne rogne jamais la zone centrale (contrôles + progression).
_INFO_TEXT_WIDTH = 260
# Variante étroite : la zone texte cède la place aux contrôles de transport,
# qui priment (on peut lire le titre dans le grand lecteur, pas mettre en pause).
_INFO_TEXT_WIDTH_NARROW = 96
_COVER_SIZE = 96
_COVER_SIZE_NARROW = 48
_VOLUME_WIDTH = 130

# Paliers de repli, sur la largeur allouée au mini-lecteur lui-même. Les
# valeurs viennent de la somme des zones à chaque étape (voir _build_*) : sous
# chaque seuil, la barre ne tient plus sans rogner, donc on retire l'élément le
# moins essentiel. En dessous du dernier palier ne restent que pochette, titre
# et les trois boutons de transport indispensables.
_BREAK_COMPACT = 900    # curseur de volume masqué, pochette et texte réduits
_BREAK_TIGHT = 640      # aléatoire / répétition / favori masqués
_BREAK_MINIMAL = 460    # barre de progression et minutages masqués

# Largeur demandée par la barre entièrement repliée. Mesurée sur la CenterBox
# au dernier palier (156 info + 122 transport + 76 muet/fermer + 32 marges) :
# c'est le plancher que le mini-lecteur impose désormais à la fenêtre, contre
# ~860 px avant le repli.
_MIN_BAR_WIDTH = 386


class _ScrollingLabel(Gtk.ScrolledWindow):
    """Label de largeur fixe dont le texte défile horizontalement en boucle
    quand il dépasse la largeur disponible ; sinon il s'affiche normalement,
    calé à gauche. GTK4 n'a pas de marquee natif : on anime l'ajustement
    horizontal d'un ScrolledWindow (barres masquées) via un timer.

    Le défilement fait un aller-retour avec une pause à chaque extrémité, ce
    qui reste lisible sans jamais tronquer ni élargir la zone (donc le centre
    de la CenterBox n'est jamais rogné)."""

    _STEP_PX = 1            # pixels par tick
    _TICK_MS = 20           # période du timer (~50 fps)
    _EDGE_PAUSE_MS = 1500   # pause aux extrémités

    def __init__(self, width, css_classes=None):
        super().__init__(
            hscrollbar_policy=Gtk.PolicyType.EXTERNAL,
            vscrollbar_policy=Gtk.PolicyType.NEVER,
            width_request=width, halign=Gtk.Align.START,
            valign=Gtk.Align.CENTER)
        self._label = Gtk.Label(
            xalign=0, halign=Gtk.Align.START,
            css_classes=css_classes or [])
        self.set_child(self._label)
        self._tick_id = None
        self._pause_id = None
        self._direction = 1     # 1 = vers la gauche (avance), -1 = retour
        # Relancer l'évaluation quand le texte change.
        self._label.connect('notify::label', lambda *_: self._restart())
        # Ne pas animer hors écran (mini-lecteur masqué par le grand lecteur) :
        # on stoppe à l'unmap et on réévalue au map.
        self.connect('map', lambda *_: self._restart())
        self.connect('unmap', lambda *_: self._stop())

    def set_label(self, text):
        # Le changement de texte déclenche notify::label → _restart() ; si le
        # texte est identique, aucun signal, donc rien à réévaluer.
        self._label.set_label(text or '')

    def _restart(self):
        self._stop()
        # Différer : à l'instant du set_label la largeur naturelle du label
        # n'est pas encore recalculée. On mesure au prochain cycle idle.
        GLib.idle_add(self._maybe_start, priority=GLib.PRIORITY_LOW)

    def _overflow(self):
        # Largeur naturelle du texte moins la largeur visible de la fenêtre.
        min_w, nat_w = self._label.get_preferred_size()
        return nat_w.width - self.get_width()

    def _maybe_start(self):
        # Retenter tant que le widget n'est pas mesuré, mais seulement s'il est
        # à l'écran : hors écran, on abandonne (le map relancera _restart()).
        if self.get_width() <= 0:
            return self.get_mapped()
        adj = self.get_hadjustment()
        adj.set_value(0)
        self._direction = 1
        if self._overflow() > 0 and self._tick_id is None and self._pause_id is None:
            # Petite pause avant de démarrer, puis on lance le défilement.
            self._pause_id = GLib.timeout_add(
                self._EDGE_PAUSE_MS, self._begin_scroll)
        return False

    def _begin_scroll(self):
        self._pause_id = None
        self._tick_id = GLib.timeout_add(self._TICK_MS, self._tick)
        return False

    def _tick(self):
        adj = self.get_hadjustment()
        max_value = max(0, self._overflow())
        value = adj.get_value() + self._direction * self._STEP_PX
        if value >= max_value:
            adj.set_value(max_value)
            self._pause_then_reverse(-1)
            return False
        if value <= 0:
            adj.set_value(0)
            self._pause_then_reverse(1)
            return False
        adj.set_value(value)
        return True

    def _pause_then_reverse(self, new_direction):
        self._tick_id = None
        def resume():
            self._pause_id = None
            self._direction = new_direction
            self._tick_id = GLib.timeout_add(self._TICK_MS, self._tick)
            return False
        self._pause_id = GLib.timeout_add(self._EDGE_PAUSE_MS, resume)

    def _stop(self):
        if self._tick_id is not None:
            GLib.source_remove(self._tick_id)
            self._tick_id = None
        if self._pause_id is not None:
            GLib.source_remove(self._pause_id)
            self._pause_id = None


class PlayerBar(Adw.BreakpointBin):
    """Barre de lecture repliable.

    Adw.BreakpointBin plutôt que Gtk.CenterBox directement : la barre porte
    beaucoup d'éléments de largeur fixe (pochette, zone texte, curseur de
    volume) dont la somme — ~860 px — s'imposait comme largeur minimale à
    TOUTE la fenêtre, puisqu'elle vit dans une barre du ToolbarView. Réduire
    la fenêtre en dessous était impossible et le contenu des onglets se
    retrouvait coupé à droite. Les breakpoints retirent progressivement le
    superflu (volume, aléatoire/répétition/fermer, progression) pour que la
    barre descende jusqu'à ~300 px sans jamais rogner le transport.

    Le width_request déclaré est la largeur que la barre demande une fois
    entièrement repliée (pochette 48 + titre 96 + trois boutons de transport
    + muet + fermer + marges), mesurée et non estimée : la déclarer plus basse
    ferait déborder la barre au lieu de la replier davantage."""

    def __init__(self, application):
        super().__init__(
            visible=False, width_request=_MIN_BAR_WIDTH, height_request=64)
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

        # -wide porte la hauteur plancher de la barre pleine ; le premier
        # breakpoint la retire pour que la barre repliée suive son contenu.
        self._bar = Gtk.CenterBox(
            css_classes=['jewelbox-player-bar', 'jewelbox-player-bar-wide'],
            margin_start=16, margin_end=16, margin_top=8, margin_bottom=8)
        self._bar.set_start_widget(self._build_info())
        self._bar.set_center_widget(self._build_center())
        self._bar.set_end_widget(self._build_right())
        self.set_child(self._bar)
        self._add_breakpoints()

        if application.playback is not None:
            application.playback.add_listener(self._on_state)

    def _add_breakpoints(self):
        """Repli progressif, du moins au plus essentiel. Chaque palier hérite
        des masquages du précédent : Adw.Breakpoint n'empile pas les setters,
        donc chaque condition réapplique aussi ceux des paliers plus larges."""
        compact = Adw.Breakpoint.new(Adw.BreakpointCondition.parse(
            f'max-width: {_BREAK_COMPACT}px'))
        for widget, prop, value in self._compact_setters():
            compact.add_setter(widget, prop, value)
        self.add_breakpoint(compact)

        tight = Adw.Breakpoint.new(Adw.BreakpointCondition.parse(
            f'max-width: {_BREAK_TIGHT}px'))
        for widget, prop, value in (
                *self._compact_setters(), *self._tight_setters()):
            tight.add_setter(widget, prop, value)
        self.add_breakpoint(tight)

        minimal = Adw.Breakpoint.new(Adw.BreakpointCondition.parse(
            f'max-width: {_BREAK_MINIMAL}px'))
        for widget, prop, value in (
                *self._compact_setters(), *self._tight_setters(),
                *self._minimal_setters()):
            minimal.add_setter(widget, prop, value)
        self.add_breakpoint(minimal)

    def _compact_setters(self):
        """Premier repli : le curseur de volume disparaît (le bouton muet
        reste), la pochette et la zone texte rétrécissent."""
        return (
            # css-classes plutôt qu'add/remove_css_class : un setter de
            # breakpoint ne sait poser que des propriétés GObject, et
            # libadwaita restaure seul la liste d'origine au-dessus du seuil.
            # La valeur doit être un GValue GStrv explicite : add_setter ne
            # convertit pas une liste Python (« from value of type PyObject »).
            (self._bar, 'css-classes', _strv(['jewelbox-player-bar'])),
            (self._volume_scale, 'visible', False),
            (self._cover_frame, 'width-request', _COVER_SIZE_NARROW),
            (self._cover_frame, 'height-request', _COVER_SIZE_NARROW),
            (self._cover, 'width-request', _COVER_SIZE_NARROW),
            (self._cover, 'height-request', _COVER_SIZE_NARROW),
            (self._title_label, 'width-request', _INFO_TEXT_WIDTH_NARROW),
            (self._artist_label, 'width-request', _INFO_TEXT_WIDTH_NARROW),
            (self._source_label, 'width-request', _INFO_TEXT_WIDTH_NARROW),
        )

    def _tight_setters(self):
        """Deuxième repli : ne restent que précédent / lecture / suivant. Le
        mode aléatoire, la répétition et le favori restent accessibles dans le
        grand lecteur, qu'un clic sur la pochette ouvre.

        Le bouton « fermer » est conservé, lui : il n'existe nulle part
        ailleurs, et le masquer rendrait impossible d'arrêter la lecture en
        fenêtre étroite."""
        return (
            (self._shuffle_button, 'visible', False),
            (self._repeat_button, 'visible', False),
            (self._favorite_button, 'visible', False),
        )

    def _minimal_setters(self):
        """Dernier repli : plus de barre de progression ni de minutages — la
        largeur restante suffit tout juste à la pochette, au titre et aux
        boutons de transport.

        Le bouton muet survit : le grand lecteur n'offre aucun réglage de
        volume, c'est donc le seul accès au son dans l'application."""
        return (
            (self._progress_box, 'visible', False),
        )

    # ── Zone gauche : pochette + infos ───────────────────────────────────────

    def _build_info(self):
        # Pochette carrée stricte : overflow=HIDDEN recadre la Picture au
        # cadre 96×96, halign/valign=CENTER empêchent la Box parente de
        # l'étirer — sinon COVER produit un rectangle au lieu d'un carré.
        self._cover = Gtk.Picture(
            content_fit=Gtk.ContentFit.COVER,
            width_request=_COVER_SIZE, height_request=_COVER_SIZE,
            overflow=Gtk.Overflow.HIDDEN,
            halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        # Icône disque en attendant/à défaut de pochette (parité avec le
        # placeholder <Disc> du mini-lecteur web), sous la Picture.
        placeholder = Gtk.Image(
            icon_name='media-optical-symbolic', pixel_size=40,
            css_classes=['dim-label'])
        # Attribut : les breakpoints rétrécissent le cadre en fenêtre étroite.
        self._cover_frame = Gtk.Overlay(
            width_request=_COVER_SIZE, height_request=_COVER_SIZE,
            halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER,
            css_classes=['jewelbox-cover'])
        self._cover_frame.set_child(placeholder)
        self._cover_frame.add_overlay(self._cover)

        # Largeur bornée pour la zone titre/artiste : dans la CenterBox, une
        # zone start qui grandit vole l'espace du centre (contrôles + curseur).
        # Le titre défile horizontalement quand il dépasse cette largeur (voir
        # _ScrollingLabel), l'artiste (généralement court) est simplement tronqué.
        self._title_label = _ScrollingLabel(
            width=_INFO_TEXT_WIDTH, css_classes=['heading'])
        self._artist_label = Gtk.Label(
            xalign=0, css_classes=['caption', 'dim-label'],
            ellipsize=Pango.EllipsizeMode.END, max_width_chars=1,
            width_request=_INFO_TEXT_WIDTH, halign=Gtk.Align.START)
        # 3e ligne discrète : nom de la playlist / liste intelligente en cours ;
        # masquée pour un album ou une piste seule.
        self._source_label = Gtk.Label(
            xalign=0, css_classes=['caption', 'dim-label'], visible=False,
            ellipsize=Pango.EllipsizeMode.END, max_width_chars=1,
            width_request=_INFO_TEXT_WIDTH, halign=Gtk.Align.START)
        labels = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER)
        labels.append(self._title_label)
        labels.append(self._artist_label)
        labels.append(self._source_label)

        info = Gtk.Box(spacing=12, valign=Gtk.Align.CENTER)
        info.append(self._cover_frame)
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
        # _updating_shuffle protège le set_active programmatique de _on_state :
        # sans lui, resynchroniser le bouton réémettrait « toggled », donc
        # rappellerait toggle_shuffle → _publish → _on_state… en boucle (même
        # précaution que le favori et le volume plus bas).
        self._updating_shuffle = False
        self._shuffle_button.connect('toggled', self._on_shuffle_toggled)

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

        # Attribut : les breakpoints masquent toute la ligne de progression au
        # palier le plus étroit.
        self._progress_box = Gtk.Box(spacing=8, hexpand=True)
        self._progress_box.append(self._position_label)
        self._progress_box.append(self._seek_scale)
        self._progress_box.append(self._duration_label)

        # Colonne centrale (contrôles au-dessus, progression dessous). Pas de
        # width_request rigide : la CenterBox donne d'abord aux zones start
        # (infos, jamais tronquées) et end (volume) leur taille naturelle,
        # le centre prend le reste — le curseur de progression (hexpand)
        # s'étire ou se réduit sans jamais rogner le titre.
        center = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=2,
            halign=Gtk.Align.FILL, valign=Gtk.Align.CENTER, hexpand=True)
        center.append(controls)
        center.append(self._progress_box)
        return center

    # ── Zone droite : favori · volume · fermer ───────────────────────────────

    def _build_right(self):
        self._favorite_button = Gtk.ToggleButton(
            icon_name='jewelbox-not-favorite-symbolic', css_classes=['flat'],
            valign=Gtk.Align.CENTER, tooltip_text=_('Favori'))
        self._favorite_button.connect('toggled', self._on_favorite_toggled)

        self._volume_button = Gtk.Button(
            icon_name='audio-volume-high-symbolic', css_classes=['flat'],
            valign=Gtk.Align.CENTER, tooltip_text=_('Couper le son'))
        self._volume_button.connect('clicked', self._on_volume_toggle)

        self._volume_scale = Gtk.Scale(
            orientation=Gtk.Orientation.HORIZONTAL,
            width_request=_VOLUME_WIDTH,
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

    def _on_shuffle_toggled(self, _button):
        # Comme le favori : n'agit qu'au clic utilisateur, jamais au set_active
        # programmatique de _on_state (protégé par _updating_shuffle), sinon la
        # resynchro du bouton relancerait toggle_shuffle en boucle.
        if self._updating_shuffle:
            return
        self._app.playback.toggle_shuffle()

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
        self._source_label.set_label(state.source_name or '')
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
