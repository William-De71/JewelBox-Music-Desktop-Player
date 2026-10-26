"""Dialogue Préférences : configuration du serveur JewelBox (page Serveur)
et réglages de lecture (page Lecture : arrière-plan, notifications, taille
des classements intelligents).

Même logique que l'écran Réglages du client Android (SettingsViewModel) :
« Enregistrer » persiste l'URL normalisée immédiatement — même serveur
éteint, on peut configurer hors ligne — puis tente en best-effort de
récupérer server_id via /api/server-info (les serveurs < 1.12 ne l'ont pas,
l'URL seule suffit). « Tester » interroge /api/health sans rien persister.
"""

import asyncio
from gettext import gettext as _

from gi.repository import Adw, Gio, GLib, Gtk

from jewelbox.api.client import ApiError, JewelBoxClient
from jewelbox.core import server_url


class PreferencesDialog(Adw.PreferencesDialog):

    def __init__(self, settings, device_id: str):
        super().__init__(title=_('Préférences'))
        self._settings = settings
        self._device_id = device_id

        page = Adw.PreferencesPage(
            title=_('Serveur'), icon_name='network-server-symbolic')
        self.add(page)

        group = Adw.PreferencesGroup(
            title=_('Serveur JewelBox'),
            description=_('Adresse du serveur sur le réseau local, '
                          'ex. 192.168.1.10:3001 (http:// est ajouté si absent).'))
        page.add(group)

        self._url_row = Adw.EntryRow(
            title=_('Adresse du serveur'),
            show_apply_button=True,
            input_purpose=Gtk.InputPurpose.URL,
        )
        self._url_row.set_text(settings.get_string('server-url'))
        self._url_row.connect('apply', self._on_apply)
        # Éditer invalide le résultat du test/enregistrement précédent.
        self._url_row.connect('changed', lambda *_a: self._set_status(''))
        group.add(self._url_row)

        self._test_button = Gtk.Button(
            label=_('Tester'), valign=Gtk.Align.CENTER)
        self._test_button.add_css_class('flat')
        self._test_button.connect('clicked', self._on_test)

        self._spinner = Adw.Spinner(visible=False)

        self._status_row = Adw.ActionRow(
            title=_('Connexion'), subtitle=_('Non testée'))
        self._status_row.add_suffix(self._spinner)
        self._status_row.add_suffix(self._test_button)
        group.add(self._status_row)

        # Bouton « Fermer » centré en bas : le × de la barre de titre ferme
        # déjà le dialogue, celui-ci offre une cible plus évidente au bas de
        # la page.
        close_button = Gtk.Button(
            label=_('Fermer'), halign=Gtk.Align.CENTER,
            margin_top=12, css_classes=['pill'])
        close_button.connect('clicked', lambda *_a: self.close())
        close_group = Adw.PreferencesGroup()
        close_group.add(close_button)
        page.add(close_group)

        self._add_playback_page()

    # Tailles proposées pour les classements « top N » des listes
    # intelligentes (le serveur accepte 1 à 1000).
    _RANKING_SIZES = (50, 100, 200, 500, 1000)

    def _add_playback_page(self):
        page = Adw.PreferencesPage(
            title=_('Lecture'), icon_name='audio-x-generic-symbolic')
        self.add(page)
        group = Adw.PreferencesGroup()
        page.add(group)

        background = Adw.SwitchRow(
            title=_('Lecture en arrière-plan'),
            subtitle=_('Fermer la fenêtre pendant une lecture la masque : '
                       'la musique continue, pilotable depuis le menu système.'),
            active=self._settings.get_boolean('background-playback'))
        background.connect('notify::active', self._on_switch_changed,
                           'background-playback')
        group.add(background)

        remote_control = Adw.SwitchRow(
            title=_('Autoriser le contrôle à distance'),
            subtitle=_('Un autre client du serveur, comme Gladys, peut '
                       'piloter ce lecteur.'),
            active=self._settings.get_boolean('remote-control'))
        remote_control.connect('notify::active', self._on_switch_changed,
                               'remote-control')
        group.add(remote_control)

        notify = Adw.SwitchRow(
            title=_('Notifier le changement de piste'),
            subtitle=_('Quand la fenêtre n’est pas au premier plan.'),
            active=self._settings.get_boolean('notify-track-change'))
        notify.connect('notify::active', self._on_switch_changed,
                       'notify-track-change')
        group.add(notify)

        sizes = Gtk.StringList.new([str(n) for n in self._RANKING_SIZES])
        ranking = Adw.ComboRow(
            title=_('Taille des classements'),
            subtitle=_('Pistes des listes Ajouts récents, Écoutées récemment '
                       'et Les plus écoutées.'),
            model=sizes)
        current = self._settings.get_int('smart-ranking-size')
        if current in self._RANKING_SIZES:
            ranking.set_selected(self._RANKING_SIZES.index(current))
        else:
            # Valeur posée hors de la liste (dconf) : on l'affiche telle quelle.
            sizes.append(str(current))
            ranking.set_selected(sizes.get_n_items() - 1)
        ranking.connect('notify::selected', self._on_ranking_changed)
        group.add(ranking)

        replaygain = Adw.ComboRow(
            title=_('Normalisation du volume'),
            subtitle=_('D’après les tags ReplayGain des fichiers (ajoutés '
                       'par JewelBox Ripper). Par album, chaque disque garde '
                       'ses nuances.'),
            model=Gtk.StringList.new([label for _key, label in self._replaygain_choices()]))
        keys = [key for key, _label in self._replaygain_choices()]
        current = self._settings.get_string('replaygain')
        replaygain.set_selected(keys.index(current) if current in keys else 0)
        replaygain.connect('notify::selected', self._on_replaygain_changed)
        group.add(replaygain)

    @staticmethod
    def _replaygain_choices():
        # Construit à l'appel (et non à l'import) : gettext doit voir la
        # locale déjà installée.
        return (('off', _('Désactivée')), ('track', _('Par piste')),
                ('album', _('Par album')))

    def _on_replaygain_changed(self, row, _pspec):
        position = row.get_selected()
        if 0 <= position < len(self._replaygain_choices()):
            self._settings.set_string('replaygain', self._replaygain_choices()[position][0])
            app = Gio.Application.get_default()
            if app is not None and hasattr(app, 'apply_replaygain'):
                app.apply_replaygain()

    def _on_switch_changed(self, row, _pspec, key):
        self._settings.set_boolean(key, row.get_active())

    def _on_ranking_changed(self, row, _pspec):
        item = row.get_selected_item()
        if item is not None:
            self._settings.set_int('smart-ranking-size', int(item.get_string()))

    # ── Actions ──────────────────────────────────────────────────────────────

    def _on_test(self, _button):
        client = self._client_or_error()
        if client is not None:
            self._run_async(self._test(client))

    def _on_apply(self, _row):
        client = self._client_or_error()
        if client is not None:
            # L'URL normalisée est persistée tout de suite : la source de
            # vérité de « où est le serveur », même s'il est injoignable là.
            self._settings.set_string('server-url', client.base_url)
            self._url_row.set_text(client.base_url)
            self._run_async(self._save_identity(client))

    def _client_or_error(self):
        try:
            return JewelBoxClient(self._url_row.get_text(),
                                  device_id=self._device_id)
        except ValueError as error:
            self._set_status(str(error), error=True)
            return None

    # ── Coroutines ───────────────────────────────────────────────────────────

    async def _test(self, client):
        self._set_busy(True)
        try:
            ok = await client.health()
            if ok:
                self._set_status(_('Connecté — le serveur répond.'))
            else:
                self._set_status(_('Le serveur répond, mais pas comme un '
                                   'JewelBox.'), error=True)
        except ApiError as error:
            self._set_status(_('Échec : {error}').format(error=error),
                             error=True)
        finally:
            self._set_busy(False)

    async def _save_identity(self, client):
        """Best-effort : capturer l'identité stable du serveur (server_id),
        pour le reconnaître plus tard même si son adresse IP change."""
        self._set_busy(True)
        try:
            info = await client.server_info()
            if info.is_jewelbox and info.server_id.strip():
                self._settings.set_string('server-id', info.server_id)
                self._set_status(_('Enregistré — {name} (v{version}).').format(
                    name=info.name or 'JewelBox', version=info.version or '?'))
            else:
                self._set_status(_('Adresse enregistrée, mais ce ne semble '
                                   'pas être un serveur JewelBox.'), error=True)
        except ApiError:
            self._set_status(_('Adresse enregistrée. Serveur injoignable '
                               'pour le moment.'))
        finally:
            self._set_busy(False)

    # ── Aides ────────────────────────────────────────────────────────────────

    def _run_async(self, coroutine):
        asyncio.get_event_loop_policy().get_event_loop().create_task(coroutine)

    def _set_busy(self, busy: bool):
        self._spinner.set_visible(busy)
        self._test_button.set_sensitive(not busy)

    def _set_status(self, text: str, error: bool = False):
        self._status_row.set_subtitle(
            GLib.markup_escape_text(text) if text else _('Non testée'))
        if error:
            self._status_row.add_css_class('error')
        else:
            self._status_row.remove_css_class('error')
