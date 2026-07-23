"""Modèles des réponses du serveur JewelBox (logique pure, sans GTK).

Miroir des DTO du client Android (Dtos.kt) : les noms de champs suivent le
JSON du serveur Fastify (voir server/src/db/queries.js#mapAlbum), en
snake_case. Tout champ optionnel a une valeur par défaut pour qu'un payload
partiel ne casse jamais la construction.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ServerInfo:
    """GET /api/server-info — la carte d'identité du serveur (serveur >= 1.12).

    server_id est un UUID créé au premier démarrage du serveur : il survit aux
    changements d'adresse DHCP, c'est lui qui permet de reconnaître « son »
    serveur. app == « jewelbox » confirme qu'on parle bien à un JewelBox.
    """

    app: str = ''
    name: str = ''
    version: str = ''
    server_id: str = ''
    api: str = ''
    collection: str | None = None

    @property
    def is_jewelbox(self) -> bool:
        return self.app == 'jewelbox'


@dataclass(frozen=True)
class Artist:
    id: int
    name: str


@dataclass(frozen=True)
class Label:
    id: int
    name: str


@dataclass(frozen=True)
class Track:
    id: int
    title: str
    position: int = 0
    duration: str | None = None
    has_file: bool = False
    play_count: int = 0
    is_favorite: bool = False


@dataclass(frozen=True)
class Album:
    id: int
    title: str
    artist: Artist
    year: int | None = None
    genre: str | None = None
    rating: int | None = None
    total_duration: str | None = None
    ean: str | None = None
    notes: str | None = None
    cover_url: str | None = None
    has_audio: bool = False
    label: Label | None = None
    # Présent uniquement sur le détail (GET /api/albums/:id) ; vide en liste.
    tracks: tuple[Track, ...] = ()


@dataclass(frozen=True)
class QueueTrack:
    """Piste au format des endpoints de file (playlists, smart playlists) :
    elle porte son album et son artiste pour qu'une liste hétérogène soit
    jouable telle quelle. entry_id n'existe que dans les playlists
    utilisateur (une piste peut y figurer deux fois)."""

    id: int
    title: str
    entry_id: int | None = None
    position: int = 0
    duration: str | None = None
    has_file: bool = False
    play_count: int = 0
    is_favorite: bool = False
    album_id: int = 0
    album_title: str = ''
    artist_name: str = ''
    cover_url: str | None = None


@dataclass(frozen=True)
class PlaylistSummary:
    """Ligne de GET /api/playlists — compte et durée agrégés côté serveur."""

    id: int
    name: str
    created_at: str | None = None
    updated_at: str | None = None
    track_count: int = 0
    total_duration_seconds: int = 0
    # Empruntée à l'album de la première piste ; seul le flux d'accueil la
    # renseigne, GET /api/playlists omet simplement le champ.
    cover_url: str | None = None


@dataclass(frozen=True)
class Playlist:
    """Playlist complète (GET /api/playlists/:id et réponses de mutation)."""

    id: int
    name: str
    created_at: str | None = None
    updated_at: str | None = None
    tracks: tuple[QueueTrack, ...] = ()
    # Uniquement dans la réponse de POST /playlists/:id/tracks : nb ajoutés.
    added: int = 0


@dataclass(frozen=True)
class SmartPlaylistMeta:
    key: str
    track_count: int = 0


@dataclass(frozen=True)
class SmartPlaylist:
    key: str = ''
    tracks: tuple[QueueTrack, ...] = ()


@dataclass(frozen=True)
class DynamicMixPlayed:
    """Réponse de POST /api/smart-playlists/dynamic_mix/played : la liste
    recomplétée après rotation."""

    removed: bool = False
    tracks: tuple[QueueTrack, ...] = ()


@dataclass(frozen=True)
class SearchResults:
    """GET /api/player/search (serveur >= 1.7) : les deux sections en un
    appel, plafonnées côté serveur (30 albums / 100 pistes)."""

    albums: tuple[Album, ...] = ()
    tracks: tuple[QueueTrack, ...] = ()


@dataclass(frozen=True)
class SmartSummary:
    """Résumé d'une liste intelligente dans les récents : le libellé et l'icône
    sont résolus côté client à partir de la clé (cf. ui/smart_specs)."""

    key: str = ''
    track_count: int = 0


@dataclass(frozen=True)
class HomeRecentItem:
    """Entrée de la section « récents » : exactement un des champs
    album/playlist/smart est renseigné, selon item_type."""

    item_type: str = ''
    played_at: str | None = None
    album: Album | None = None
    playlist: PlaylistSummary | None = None
    smart: SmartSummary | None = None


@dataclass(frozen=True)
class Home:
    """GET /api/player/home (serveur >= 1.9)."""

    recent: tuple[HomeRecentItem, ...] = ()
    suggestions: tuple[Album, ...] = ()


@dataclass(frozen=True)
class Pagination:
    total: int = 0
    page: int = 1
    limit: int = 24
    total_pages: int = 0


@dataclass(frozen=True)
class AlbumsPage:
    data: tuple[Album, ...] = ()
    pagination: Pagination = field(default_factory=Pagination)
