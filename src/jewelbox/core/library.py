"""Logique pure de la bibliothèque — testée par tests/test_library.py."""

import unicodedata

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


# ── Filtrage local ────────────────────────────────────────────────────────────

# Toute la collection étant déjà en mémoire (FETCH_LIMIT), filtrer ne coûte
# aucun appel réseau : on compare des chaînes normalisées à chaque frappe.

def normalize(text: str | None) -> str:
    """Forme comparable d'un texte : sans casse, sans accents, sans espaces
    superflus.

    Une recherche « dying » doit trouver « My Dying Bride », et « motorhead »
    doit trouver « Motörhead » — sinon le filtre rate justement les titres
    qu'on tape de mémoire, sans se souvenir des diacritiques.
    """
    if not text:
        return ''
    # NFD sépare les lettres de leurs accents ; on jette ensuite les marques
    # combinantes (catégorie Mn), ce qui ramène « ö » à « o ».
    decomposed = unicodedata.normalize('NFD', text)
    stripped = ''.join(c for c in decomposed
                       if unicodedata.category(c) != 'Mn')
    return ' '.join(stripped.casefold().split())


def matches(album, query: str) -> bool:
    """L'album correspond-il à la recherche ? Vrai si chaque mot de la requête
    se retrouve dans le titre ou le nom d'artiste.

    Mots indépendants et non ordonnés : « bride dying » trouve « My Dying
    Bride » aussi bien que « dying bride ». On cherche une sous-chaîne (pas un
    mot entier) pour que « metal » sorte « Metallica ».
    """
    terms = normalize(query).split()
    if not terms:
        return True
    haystack = f'{normalize(album.title)} {normalize(album.artist.name)}'
    return all(term in haystack for term in terms)


def filter_albums(albums, query: str):
    """Les albums correspondant à la recherche, dans l'ordre reçu."""
    return [album for album in albums if matches(album, query)]


# ── Barre d'initiales ─────────────────────────────────────────────────────────

# Lettre affichée pour tout ce qui ne commence pas par A–Z : chiffres et
# symboles (« 7th Nemesis ») se retrouvent sous un même bouton, en tête.
OTHER_INITIAL = '#'

INITIALS = (OTHER_INITIAL,) + tuple(chr(c) for c in range(ord('A'), ord('Z') + 1))


def initial_of(album) -> str:
    """Initiale de classement d'un album : celle de l'artiste, sans accent
    (« Ötzi » se range sous O, pas dans une 27e case isolée)."""
    name = normalize(album.artist.name)
    if not name:
        return OTHER_INITIAL
    first = name[0].upper()
    return first if 'A' <= first <= 'Z' else OTHER_INITIAL


def available_initials(albums) -> set:
    """Les initiales effectivement présentes — les autres boutons de la barre
    A–Z sont désactivés plutôt que masqués, pour que la rangée garde une
    largeur stable quand le filtre change."""
    return {initial_of(album) for album in albums}


def first_index_for_initial(albums, initial: str) -> int | None:
    """Position du premier album d'une initiale, ou None si absente.

    Suppose la liste triée par artiste — c'est le seul tri où la barre A–Z
    a un sens, et la page ne l'affiche que dans ce cas.
    """
    for index, album in enumerate(albums):
        if initial_of(album) == initial:
            return index
    return None


# ── Vue par artiste ───────────────────────────────────────────────────────────

# Grouper les pochettes en sections dans la grille elle-même a été écarté :
# sur une collection réelle, près de la moitié des artistes n'ont qu'un seul
# album, et chacun occuperait une rangée entière presque vide — le défilement
# double au lieu de diminuer. On propose donc une LISTE d'artistes distincte,
# compacte (une ligne par artiste), depuis laquelle un clic filtre la grille.


def artist_entries(albums):
    """Les artistes présents, chacun avec son nombre d'albums, triés par nom.

    Le tri est fait ici, sur la forme normalisée, pour que la liste reste
    alphabétique quel que soit l'ordre d'arrivée des albums (tri par année,
    par exemple) et pour que les accents ne rejettent pas « Ötzi » en fin de
    liste. À nom normalisé égal, l'ordre reste stable.
    """
    counts = {}
    for album in albums:
        name = album.artist.name or ''
        counts[name] = counts.get(name, 0) + 1
    return sorted(((name, count) for name, count in counts.items()),
                  key=lambda entry: normalize(entry[0]))


def filter_artist_entries(entries, query: str):
    """Les entrées d'artistes dont le nom correspond à la recherche.

    Même normalisation que la grille (casse, accents, mots indépendants), pour
    qu'une frappe donne des résultats cohérents d'un onglet à l'autre.
    """
    terms = normalize(query).split()
    if not terms:
        return list(entries)
    return [entry for entry in entries
            if all(term in normalize(entry[0]) for term in terms)]


def albums_by_artist(albums, artist_name: str):
    """Les albums d'un artiste donné, dans l'ordre reçu.

    Comparaison sur le nom exact tel que fourni par le serveur : c'est lui
    qui fait foi sur l'identité d'un artiste. Deux orthographes distinctes
    restent donc deux artistes, ce que la liste montre telles quelles plutôt
    que de les fusionner en douce.
    """
    return [album for album in albums
            if (album.artist.name or '') == artist_name]
