"""Logique pure de la bibliothèque — testée par tests/test_library.py."""

# Toute la collection possédée en un seul appel, comme le client Android
# (AlbumListViewModel.FETCH_LIMIT) : pas d'artiste coupé entre deux pages,
# et une bibliothèque personnelle tient largement là-dedans.
FETCH_LIMIT = 10000

# Ordre des choix tel qu'affiché dans le menu déroulant de tri.
SORTS = ('artist', 'year')


def sort_params(sort_order: str) -> dict:
    """Paramètres d'API pour un choix de tri GSettings (clé sort-order).

    « artist » : A → Z, le serveur ajoute l'année en clé secondaire, donc un
    seul appel donne l'ordre artiste → date. « year » : plus récents d'abord.
    Un choix inconnu retombe sur le tri artiste (défaut du schéma).
    """
    if sort_order == 'year':
        return {'sort': 'year', 'order': 'desc'}
    return {'sort': 'artist', 'order': 'asc'}


def sort_position(sort_order: str) -> int:
    """Position du choix dans le menu déroulant (0 si choix inconnu)."""
    try:
        return SORTS.index(sort_order)
    except ValueError:
        return 0


def sort_from_position(position: int) -> str:
    """Choix GSettings depuis la position du menu déroulant."""
    if 0 <= position < len(SORTS):
        return SORTS[position]
    return SORTS[0]
