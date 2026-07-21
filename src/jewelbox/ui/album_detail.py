"""Fiche album : pochette, métadonnées et liste des pistes.

Parité avec l'écran Album du client Android (AlbumDetailScreen) : en-tête
avec pochette carrée, titre, artiste, méta (année · genre · label · durée
totale) puis notes ; sous l'en-tête, la liste des pistes — numéro (ou
indicateur « en lecture »), titre, durée, bouton favori. Les pistes sans
fichier audio sont estompées et non cliquables (classe .track-unavailable,
déjà utilisée ailleurs dans le style).

Code frontière (exclu de la couverture) : cette page ne fait qu'afficher un
Album chargé par api.client et déléguer les actions à PlaybackSession /
JewelBoxClient, tous deux déjà testés séparément.
"""

import asyncio
from gettext import gettext as _

from gi.repository import Adw, GLib, Gtk, Pango

from jewelbox.api.client import ApiError
from jewelbox.core.formats import format_duration


class AlbumDetailPage(Gtk.Stack):
    """États : chargement, erreur (Réessayer), contenu. Une instance par
    navigation — la fenêtre en recrée une à chaque album ouvert."""

    def __init__(self, application, album_id: int):
        super().__init__(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self._app = application
        self._album_id = album_id
        self._album = None
        self._track_rows = {}   # track_id → (row, play_button, position_label)
        self._current_track_id = None  # piste en cours d'après PlaybackSession
        self._is_playing = False       # cette piste joue-t-elle (vs pause) ?
        # Appelé avec le titre de l'album une fois chargé, pour que la
        # NavigationPage englobante affiche le vrai titre plutôt qu'un
        # texte générique le temps du chargement.
        self.on_title_known = None

        loading = Adw.StatusPage(title=_('Chargement de l’album…'))
        loading.set_child(Adw.Spinner(
            width_request=48, height_request=48, halign=Gtk.Align.CENTER))
        self.add_named(loading, 'loading')

        self._error = Adw.StatusPage(
            icon_name='network-error-symbolic', title=_('Album introuvable'))
        retry = Gtk.Button(label=_('Réessayer'), halign=Gtk.Align.CENTER,
                           css_classes=['pill', 'suggested-action'])
        retry.connect('clicked', lambda *_a: self.reload())
        self._error.set_child(retry)
        self.add_named(self._error, 'error')

        self._content = self._build_content()
        self.add_named(self._content, 'content')

        self._playback = application.playback
        if self._playback is not None:
            self._playback.add_listener(self._on_playback_state)
        self.connect('destroy', self._on_destroy)

        self.reload()

    def _on_destroy(self, *_args):
        if self._playback is not None:
            self._playback.remove_listener(self._on_playback_state)

    def _build_content(self):
        # Pochette carrée stricte : une Gtk.Picture avec COVER demande une
        # taille selon le ratio de l'image (paysage → rectangle). Un
        # AspectFrame ratio=1 (obey_child=False) impose un cadre carré ;
        # overflow=HIDDEN rogne le débordement. Le cadre est enfermé dans un
        # conteneur de largeur fixe 280 (halign=CENTER) : sans ce plafond,
        # sur une fenêtre large l'AspectFrame s'étirerait au-delà de 280 en
        # largeur tout en gardant 280 en hauteur, cassant le ratio 1:1.
        self._cover = Gtk.Picture(content_fit=Gtk.ContentFit.COVER)
        aspect = Gtk.AspectFrame(
            ratio=1.0, obey_child=False,
            overflow=Gtk.Overflow.HIDDEN, child=self._cover)
        cover_frame = Gtk.Box(
            width_request=280, height_request=280,
            hexpand=False, vexpand=False,
            halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        cover_frame.append(aspect)
        aspect.set_hexpand(True)
        aspect.set_vexpand(True)
        self._title_label = Gtk.Label(
            wrap=True, justify=Gtk.Justification.CENTER,
            css_classes=['title-1'])
        self._artist_label = Gtk.Label(css_classes=['title-3', 'accent'])
        self._meta_label = Gtk.Label(css_classes=['dim-label'])
        self._notes_label = Gtk.Label(
            wrap=True, justify=Gtk.Justification.CENTER, visible=False,
            margin_top=8)

        header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4,
                         margin_top=12, margin_bottom=12,
                         margin_start=24, margin_end=24)
        header.append(cover_frame)
        header.append(Gtk.Box(height_request=12))
        header.append(self._title_label)
        header.append(self._artist_label)
        header.append(self._meta_label)
        header.append(self._notes_label)

        self._tracks_box = Gtk.ListBox(
            selection_mode=Gtk.SelectionMode.NONE,
            css_classes=['boxed-list'],
            margin_start=24, margin_end=24, margin_bottom=24)
        # « row-activated » est le signal émis par le clic (ou Entrée) sur une
        # ligne activatable — d'où le comportement « clic n'importe où ».
        self._tracks_box.connect('row-activated', self._on_row_activated)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        outer.append(header)
        outer.append(self._tracks_box)

        # Pas de barre d'en-tête propre à la fiche : la barre du shell externe
        # (sélecteur d'onglets + menu + bouton retour révélé à l'empilement)
        # reste visible au-dessus du NavigationView sur toutes les pages. Une
        # Adw.HeaderBar interne ferait doublon sous elle et resterait vide.
        return Gtk.ScrolledWindow(
            child=outer, hscrollbar_policy=Gtk.PolicyType.NEVER)

    # ── Chargement ───────────────────────────────────────────────────────────

    def reload(self):
        client = self._app.get_client()
        if client is None:
            self.set_visible_child_name('error')
            return
        self.set_visible_child_name('loading')
        task = self._load(client)
        asyncio.get_event_loop_policy().get_event_loop().create_task(task)

    async def _load(self, client):
        try:
            album = await client.album(self._album_id)
        except ApiError:
            self.set_visible_child_name('error')
            return
        self._album = album
        self._populate(client, album)
        self.set_visible_child_name('content')
        if self.on_title_known is not None:
            self.on_title_known(album.title)

    def _populate(self, client, album):
        cover_url = client.resolve_cover(album.cover_url)
        if cover_url:
            task = self._load_cover(cover_url)
            asyncio.get_event_loop_policy().get_event_loop().create_task(task)

        self._title_label.set_label(album.title)
        self._artist_label.set_label(album.artist.name)

        meta = [str(part) for part in (
            album.year, album.genre,
            album.label.name if album.label else None,
            album.total_duration,
        ) if part]
        self._meta_label.set_label(' · '.join(meta))
        self._meta_label.set_visible(bool(meta))

        self._notes_label.set_label(album.notes or '')
        self._notes_label.set_visible(bool(album.notes))

        while (row := self._tracks_box.get_row_at_index(0)) is not None:
            self._tracks_box.remove(row)
        self._track_rows = {}
        for track in album.tracks:
            self._tracks_box.append(self._build_track_row(client, album, track))

    async def _load_cover(self, url):
        client = self._app.get_client()
        if client is None:
            return
        try:
            data = await client.fetch_bytes(url)
        except ApiError:
            return
        from gi.repository import Gdk
        try:
            texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(data))
        except GLib.Error:
            return
        self._cover.set_paintable(texture)

    # ── Pistes ───────────────────────────────────────────────────────────────

    def _build_track_row(self, client, album, track):
        position_label = Gtk.Label(
            label=str(track.position), width_chars=2,
            css_classes=['dim-label'])

        # Bouton de lecture toujours visible sur une piste jouable (parité
        # avec le bouton play des cartes Android) — pas seulement une icône
        # d'état qui n'apparaîtrait qu'une fois la lecture commencée. Sur la
        # piste en cours il devient un bouton pause/reprise (voir
        # _on_playback_state et _on_track_button_clicked).
        play_button = Gtk.Button(
            icon_name='media-playback-start-symbolic', css_classes=['flat', 'circular'],
            valign=Gtk.Align.CENTER, tooltip_text=_('Lire'),
            visible=track.has_file)
        if track.has_file:
            play_button.connect(
                'clicked', self._on_track_button_clicked, album, track.id)
        leading = Gtk.Box(width_request=44)
        leading.append(position_label)
        leading.append(play_button)

        title_label = Gtk.Label(
            label=track.title, xalign=0, hexpand=True,
            ellipsize=Pango.EllipsizeMode.END)

        duration_label = Gtk.Label(
            label=track.duration or format_duration(None),
            css_classes=['dim-label'])

        favorite_button = Gtk.ToggleButton(
            active=track.is_favorite, valign=Gtk.Align.CENTER,
            css_classes=['flat'], tooltip_text=_('Favori'),
            icon_name=('starred-symbolic' if track.is_favorite
                      else 'non-starred-symbolic'))
        favorite_button.connect(
            'toggled', self._on_favorite_toggled, client, track.id)

        row_box = Gtk.Box(spacing=12, margin_top=6, margin_bottom=6,
                          margin_start=12, margin_end=12)
        row_box.append(leading)
        row_box.append(title_label)
        row_box.append(duration_label)
        row_box.append(favorite_button)

        row = Gtk.ListBoxRow(activatable=track.has_file, child=row_box)
        if not track.has_file:
            row.add_css_class('track-unavailable')
        else:
            # L'album et l'id de piste sont portés par la row pour que le
            # gestionnaire « row-activated » de la ListBox (voir _build_content)
            # les retrouve au clic. Le signal « activate » d'une ListBoxRow ne
            # se déclenche PAS au clic souris (seulement au clavier / par
            # programme) : c'est « row-activated » sur la ListBox parente qui
            # répond au clic — c'est ce qui empêchait le clic sur la ligne
            # d'agir alors que le bouton, lui, marchait.
            row._album = album
            row._track_id = track.id

        self._track_rows[track.id] = (row, play_button, position_label)
        return row

    def _on_row_activated(self, _listbox, row):
        # album/track_id posés sur la row à sa construction (voir
        # _build_track_row) ; absents sur une piste sans fichier (non
        # activatable, donc ce signal ne s'y déclenche pas de toute façon).
        album = getattr(row, '_album', None)
        if album is not None:
            self._toggle_or_play(album, row._track_id)

    def _on_track_button_clicked(self, _button, album, track_id):
        self._toggle_or_play(album, track_id)

    def _toggle_or_play(self, album, track_id):
        # Sur la piste déjà en cours, pause/reprise ; sur une autre, on la
        # lance. Partagé par le bouton lecture et le clic sur la ligne, pour
        # qu'un clic n'importe où sur la piste courante bascule pause/reprise
        # au lieu de la relancer depuis le début.
        playback = self._app.playback
        if playback is None:
            return
        if track_id == self._current_track_id:
            playback.toggle_play_pause()
        else:
            playback.play_album(album, track_id)

    def _on_favorite_toggled(self, button, client, track_id):
        is_favorite = button.get_active()
        button.set_icon_name(
            'starred-symbolic' if is_favorite else 'non-starred-symbolic')
        task = self._set_favorite(client, track_id, is_favorite)
        asyncio.get_event_loop_policy().get_event_loop().create_task(task)

    async def _set_favorite(self, client, track_id, is_favorite):
        try:
            await client.set_favorite(track_id, is_favorite)
        except ApiError:
            pass  # best-effort, comme le reste de l'app

    # ── Surbrillance de la piste en cours ────────────────────────────────────

    def _on_playback_state(self, state):
        self._current_track_id = state.current_track_id
        self._is_playing = state.is_playing
        for track_id, (row, play_button, position_label) in self._track_rows.items():
            is_current = state.current_track_id == track_id
            # Piste en cours : le numéro reste affiché ; seul le bouton change
            # (pause pendant la lecture, reprise en pause). Les autres gardent
            # leur bouton lecture.
            if is_current:
                play_button.set_icon_name(
                    'media-playback-pause-symbolic' if state.is_playing
                    else 'media-playback-start-symbolic')
                play_button.set_tooltip_text(
                    _('Pause') if state.is_playing else _('Reprendre'))
            else:
                play_button.set_icon_name('media-playback-start-symbolic')
                play_button.set_tooltip_text(_('Lire'))
            if not row.get_activatable():
                continue
            if is_current:
                row.add_css_class('accent')
            else:
                row.remove_css_class('accent')
