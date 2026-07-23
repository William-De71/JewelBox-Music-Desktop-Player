"""Fiche liste intelligente : en-tête, liste des pistes (lecture seule).

Parité avec SmartPlaylistScreen côté Android : un en-tête (nombre de pistes et
bouton « Écouter »), puis la liste des pistes — numéro, pochette, titre/artiste,
durée, bouton favori. Contrairement à une playlist utilisateur, on ne peut pas
réordonner ni renommer : ces listes sont calculées par le serveur.

Lancer une liste intelligente signale l'historique via report_smart_key (sa
clé texte) sur play_queue_tracks : elle apparaît alors dans « Récemment écouté »
de l'accueil, au même titre qu'un album ou une playlist utilisateur.

Le mix dynamique (clé dynamic_mix) est le seul modifiable : la barre du shell
offre « Relancer un mix complet » (tire un tout nouveau tirage) et chaque piste
porte un bouton « Retirer du mix ». Ces deux appels renvoient la liste à jour,
avec laquelle la fiche se rafraîchit.

Code frontière (exclu de la couverture) : cette page ne fait qu'afficher une
SmartPlaylist chargée par api.client et déléguer la lecture / les mutations du
mix à PlaybackSession / JewelBoxClient, tous testés séparément.
"""

import asyncio
from gettext import gettext as _

from gi.repository import Adw, Gdk, GLib, Gtk, Pango

from jewelbox.api.client import ApiError
from jewelbox.core.formats import format_duration
from jewelbox.ui.smart_specs import DYNAMIC_MIX_KEY, smart_spec

_COVER_SIZE = 48


class SmartPlaylistDetailPage(Gtk.Stack):
    """États : chargement, erreur (Réessayer), contenu. Une instance par
    navigation. on_title_known(nom) renseigne le titre de la NavigationPage."""

    def __init__(self, application, key: str):
        super().__init__(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self._app = application
        self._key = key
        self._spec = smart_spec(key)
        self._is_dynamic = key == DYNAMIC_MIX_KEY
        self._tracks = ()
        self._textures = {}
        self._row_track_ids = {}
        self._current_track_id = None
        self.on_title_known = None

        loading = Adw.StatusPage(title=_('Chargement de la liste…'))
        loading.set_child(Adw.Spinner(
            width_request=48, height_request=48, halign=Gtk.Align.CENTER))
        self.add_named(loading, 'loading')

        self._error = Adw.StatusPage(
            icon_name='network-error-symbolic', title=_('Liste introuvable'))
        retry = Gtk.Button(label=_('Réessayer'), halign=Gtk.Align.CENTER,
                           css_classes=['pill', 'suggested-action'])
        retry.connect('clicked', lambda *_a: self.reload())
        self._error.set_child(retry)
        self.add_named(self._error, 'error')

        self._content = self._build_content()
        self.add_named(self._content, 'content')

        # Abonnement lié à l'affichage (map/unmap), pas à destroy : dépiler une
        # page d'un Adw.NavigationView ne la détruit pas, donc destroy ne se
        # déclenche pas au retour et les listeners s'accumuleraient à chaque
        # ouverture. Voir le même motif dans playlist_detail.
        self._playback = application.playback
        self._subscribed = False
        self.connect('map', self._on_map)
        self.connect('unmap', self._on_unmap)

        self.reload()

    def _on_map(self, *_args):
        if self._playback is not None and not self._subscribed:
            self._playback.add_listener(self._on_playback_state)
            self._subscribed = True

    def _on_unmap(self, *_args):
        if self._playback is not None and self._subscribed:
            self._playback.remove_listener(self._on_playback_state)
            self._subscribed = False

    def title(self):
        return self._spec.label if self._spec is not None else self._key

    def _build_content(self):
        # En-tête (CenterBox) : compteur de pistes à gauche, NOM de la liste
        # centré, puis « Écouter » (et « Relancer » pour le mix) à droite.
        self._count_label = Gtk.Label(
            xalign=0, css_classes=['dim-label'], valign=Gtk.Align.CENTER)
        name_label = Gtk.Label(
            label=self.title(), css_classes=['title-4'],
            valign=Gtk.Align.CENTER,
            ellipsize=Pango.EllipsizeMode.END, max_width_chars=40)
        self._play_button = Gtk.Button(
            css_classes=['pill', 'suggested-action'], valign=Gtk.Align.CENTER)
        self._play_button.set_child(Adw.ButtonContent(
            icon_name='media-playback-start-symbolic', label=_('Écouter')))
        self._play_button.connect('clicked', lambda *_a: self._play(0))

        end_box = Gtk.Box(spacing=8, valign=Gtk.Align.CENTER)
        end_box.append(self._play_button)

        # Seul le mix dynamique se relance ; pour les autres listes, ce bouton
        # n'est pas ajouté (rien à recalculer côté client).
        if self._is_dynamic:
            refresh = Gtk.Button(
                icon_name='view-refresh-symbolic', valign=Gtk.Align.CENTER,
                tooltip_text=_('Relancer un mix complet'), css_classes=['flat'])
            refresh.connect('clicked', lambda *_a: self._refresh_mix())
            end_box.append(refresh)

        header = Gtk.CenterBox(
            margin_top=16, margin_bottom=8, margin_start=24, margin_end=24)
        header.set_start_widget(self._count_label)
        header.set_center_widget(name_label)
        header.set_end_widget(end_box)

        self._empty_label = Gtk.Label(
            label=_('Aucune piste jouable dans cette liste'),
            css_classes=['dim-label'], margin_top=24, visible=False)

        self._tracks_box = Gtk.ListBox(
            selection_mode=Gtk.SelectionMode.NONE,
            css_classes=['boxed-list'],
            margin_start=24, margin_end=24, margin_bottom=24)
        self._tracks_box.connect('row-activated', self._on_row_activated)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        outer.append(header)
        outer.append(self._empty_label)
        outer.append(self._tracks_box)

        return Gtk.ScrolledWindow(
            child=outer, hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)

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
            smart = await client.smart_playlist(self._key)
        except ApiError:
            self.set_visible_child_name('error')
            return
        self._apply(client, smart.tracks)
        self.set_visible_child_name('content')
        if self.on_title_known is not None:
            self.on_title_known(self.title())

    def _apply(self, client, tracks):
        self._tracks = tracks
        count = len(tracks)
        self._count_label.set_label(
            _('{count} pistes').format(count=count) if count != 1
            else _('1 piste'))
        playable = [t for t in tracks if t.has_file]
        self._play_button.set_visible(bool(playable))
        self._empty_label.set_visible(not tracks)

        while (row := self._tracks_box.get_row_at_index(0)) is not None:
            self._tracks_box.remove(row)
        self._row_track_ids = {}
        for index, track in enumerate(tracks):
            self._tracks_box.append(self._build_track_row(client, track, index))
        if self._playback is not None:
            self._on_playback_state(self._playback._build_state())

    # ── Lecture ──────────────────────────────────────────────────────────────

    def _play(self, start_index: int):
        playback = self._app.playback
        if playback is None:
            return
        # report_smart_key alimente les récents de l'accueil (une liste
        # intelligente EST une entrée d'historique, repérée par sa clé).
        playback.play_queue_tracks(
            self._tracks, start_index=start_index,
            report_smart_key=self._key, source_name=self.title())

    def _play_or_toggle(self, track_id):
        playback = self._app.playback
        if playback is None:
            return
        if track_id == self._current_track_id:
            playback.toggle_play_pause()
            return
        playable = [t for t in self._tracks if t.has_file]
        start = next((i for i, t in enumerate(playable) if t.id == track_id), 0)
        self._play(start)

    # ── Lignes de piste ──────────────────────────────────────────────────────

    def _build_track_row(self, client, track, index):
        position_label = Gtk.Label(
            label=str(index + 1), width_chars=2, css_classes=['dim-label'],
            valign=Gtk.Align.CENTER)

        cover = Gtk.Picture(
            content_fit=Gtk.ContentFit.COVER,
            width_request=_COVER_SIZE, height_request=_COVER_SIZE,
            overflow=Gtk.Overflow.HIDDEN, valign=Gtk.Align.CENTER)
        cover.add_css_class('jewelbox-cover')
        cover_url = client.resolve_cover(track.cover_url)
        if cover_url:
            cover._wanted_url = cover_url
            cover.set_paintable(self._textures.get(cover_url))
            if cover_url not in self._textures:
                task = self._load_cover(cover, cover_url)
                asyncio.get_event_loop_policy().get_event_loop().create_task(task)

        title_label = Gtk.Label(
            label=track.title, xalign=0, hexpand=True,
            ellipsize=Pango.EllipsizeMode.END, css_classes=['heading'])
        subtitle = ' · '.join(
            part for part in (track.artist_name, track.album_title) if part)
        subtitle_label = Gtk.Label(
            label=subtitle, xalign=0, ellipsize=Pango.EllipsizeMode.END,
            css_classes=['caption', 'dim-label'])
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2,
                       valign=Gtk.Align.CENTER, hexpand=True)
        text.append(title_label)
        if subtitle:
            text.append(subtitle_label)

        duration_label = Gtk.Label(
            label=track.duration or format_duration(None),
            css_classes=['dim-label'], valign=Gtk.Align.CENTER)

        favorite_button = Gtk.ToggleButton(
            active=track.is_favorite, valign=Gtk.Align.CENTER,
            css_classes=['flat'], tooltip_text=_('Favori'),
            icon_name=('starred-symbolic' if track.is_favorite
                      else 'non-starred-symbolic'))
        favorite_button.connect(
            'toggled', self._on_favorite_toggled, client, track.id)

        row_box = Gtk.Box(spacing=12, margin_top=6, margin_bottom=6,
                          margin_start=12, margin_end=8)
        row_box.append(position_label)
        row_box.append(cover)
        row_box.append(text)
        row_box.append(duration_label)
        row_box.append(favorite_button)

        # Bouton « Retirer du mix » : seulement pour le mix dynamique, et sur
        # une piste jouable (une piste sans fichier n'y figure pas de toute
        # façon, mais on reste homogène avec la fiche playlist).
        if self._is_dynamic:
            remove = Gtk.Button(
                icon_name='window-close-symbolic', css_classes=['flat'],
                valign=Gtk.Align.CENTER, tooltip_text=_('Retirer du mix'))
            remove.connect('clicked', self._on_remove_mix_track, track.id)
            row_box.append(remove)

        row = Gtk.ListBoxRow(activatable=track.has_file, child=row_box)
        if not track.has_file:
            row.add_css_class('track-unavailable')
        else:
            row._track_id = track.id
            self._row_track_ids[row] = track.id
        return row

    def _on_row_activated(self, _listbox, row):
        track_id = getattr(row, '_track_id', None)
        if track_id is not None:
            self._play_or_toggle(track_id)

    # ── Mix dynamique : relancer / retirer ───────────────────────────────────

    def _refresh_mix(self):
        task = self._run_mix(lambda client: client.dynamic_mix_refresh())
        asyncio.get_event_loop_policy().get_event_loop().create_task(task)

    def _on_remove_mix_track(self, _button, track_id):
        task = self._run_mix(lambda client: client.dynamic_mix_remove(track_id))
        asyncio.get_event_loop_policy().get_event_loop().create_task(task)

    async def _run_mix(self, call):
        """dynamic_mix_refresh renvoie une SmartPlaylist ; dynamic_mix_remove
        un DynamicMixPlayed. Les deux exposent .tracks : on rafraîchit avec."""
        client = self._app.get_client()
        if client is None:
            return
        try:
            result = await call(client)
        except ApiError:
            return
        self._apply(client, result.tracks)

    # ── Favoris ──────────────────────────────────────────────────────────────

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
            pass

    # ── Surbrillance de la piste en cours ────────────────────────────────────

    def _on_playback_state(self, state):
        self._current_track_id = state.current_track_id
        for row, track_id in self._row_track_ids.items():
            if state.current_track_id == track_id:
                row.add_css_class('accent')
            else:
                row.remove_css_class('accent')

    # ── Pochettes ────────────────────────────────────────────────────────────

    async def _load_cover(self, picture, url):
        client = self._app.get_client()
        if client is None:
            return
        try:
            data = await client.fetch_bytes(url)
            texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(data))
        except (ApiError, GLib.Error):
            return
        self._textures[url] = texture
        if getattr(picture, '_wanted_url', None) == url:
            picture.set_paintable(texture)
