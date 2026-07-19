"""URL du serveur : normalisation et construction d'URL (logique pure).

Parité avec le client Android (ApiClient.normalize) : on nettoie l'entrée
utilisateur, on ajoute http:// si aucun schéma n'est donné. Différence de
convention : ici l'URL de base est stockée SANS barre oblique finale, comme le
documente le schéma GSettings (ex. « http://192.168.1.10:3001 »).
"""

from urllib.parse import quote, urlencode, urlsplit


def normalize(raw_url) -> str:
    """Normalise l'adresse saisie par l'utilisateur.

    « 192.168.1.10:3001/ » → « http://192.168.1.10:3001 ». Lève ValueError si
    l'adresse est vide, sans hôte, ou d'un schéma autre que http(s).
    """
    url = (raw_url or '').strip()
    if not url:
        raise ValueError("L'adresse du serveur est vide")
    if '://' not in url:
        url = f'http://{url}'

    parts = urlsplit(url)
    if parts.scheme not in ('http', 'https'):
        raise ValueError(f'Schéma non pris en charge : {parts.scheme}://')
    if not parts.hostname:
        raise ValueError("L'adresse du serveur n'a pas d'hôte")

    return url.rstrip('/')


def api_url(base_url: str, path: str, query: dict | None = None) -> str:
    """URL absolue d'un endpoint : api_url(base, '/api/health').

    Les valeurs None du dictionnaire query sont omises, les autres sont
    encodées (bool → « true »/« false » comme attend le serveur Fastify).
    """
    url = base_url.rstrip('/') + '/' + path.lstrip('/')
    if query:
        params = {
            key: str(value).lower() if isinstance(value, bool) else value
            for key, value in query.items()
            if value is not None
        }
        if params:
            url += '?' + urlencode(params)
    return url


def resolve_url(base_url: str, url) -> str | None:
    """Résout une URL renvoyée par le serveur (ex. cover_url).

    Le serveur renvoie soit une URL absolue (pochette Discogs/CAA), soit un
    chemin relatif comme « /covers/42.jpg » à résoudre contre le serveur.
    None reste None (pas de pochette).
    """
    if not url:
        return None
    if '://' in url:
        return url
    return base_url.rstrip('/') + '/' + url.lstrip('/')


def stream_url(base_url: str, track_id: int) -> str:
    """URL de streaming d'une piste, à donner telle quelle à playbin3."""
    return api_url(base_url, f'/api/player/tracks/{quote(str(track_id))}/stream')
