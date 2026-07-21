"""Spécifications des listes intelligentes — clé, icône, libellé.

Parité avec SmartSpecs.kt côté Android : l'ordre de cette liste est l'ordre
d'affichage, et il suit exactement DEFINITIONS dans le serveur
(server/src/db/smartPlaylists.js). Le serveur reste la source de vérité des
clés existantes ; on n'affiche ici que celles qu'il renvoie réellement (voir
PlaylistsPage), donc une clé future inconnue ici sera simplement ignorée.

Les libellés français sont ceux du client Android (strings.xml, valeurs déjà
traduites), pour une expérience identique d'une plateforme à l'autre. Les
icônes sont leurs équivalents symboliques GNOME les plus proches des icônes
Material choisies sur mobile.
"""

from dataclasses import dataclass
from gettext import gettext as _

DYNAMIC_MIX_KEY = 'dynamic_mix'


@dataclass(frozen=True)
class SmartSpec:
    key: str
    icon: str
    label: str


# Ordre canonique, miroir de SMART_SPECS (SmartSpecs.kt) et de DEFINITIONS
# (smartPlaylists.js). Construit paresseusement pour que gettext capture la
# locale au moment de l'appel plutôt qu'à l'import.
def smart_specs() -> tuple[SmartSpec, ...]:
    return (
        SmartSpec('newest', 'starred-symbolic', _('Ajouts récents')),
        SmartSpec('ever_played', 'document-open-recent-symbolic',
                  _('Déjà écoutées')),
        SmartSpec('never_played', 'audio-x-generic-symbolic',
                  _('Jamais écoutées')),
        SmartSpec('last_played', 'alarm-symbolic', _('Écoutées récemment')),
        SmartSpec('most_played', 'view-list-ordered-symbolic',
                  _('Les plus écoutées')),
        SmartSpec('favourites', 'emblem-favorite-symbolic', _('Favoris')),
        SmartSpec('all_tracks', 'folder-music-symbolic',
                  _('Toutes les pistes')),
        SmartSpec(DYNAMIC_MIX_KEY, 'media-playlist-shuffle-symbolic',
                  _('Mix dynamique')),
    )


def smart_spec(key: str) -> SmartSpec | None:
    return next((spec for spec in smart_specs() if spec.key == key), None)
