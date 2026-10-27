"""Paroles d'une piste : la logique pure, testée.

Le serveur (>= 1.20, GET /api/player/tracks/:id/lyrics) répond des lignes
synchronisées quand il en a (un .lrc posé à côté du fichier, ou LRCLIB),
sinon un texte brut. Ici : lire cette réponse et trouver la ligne chantée,
avec la même petite avance que la PWA et l'appli Android.
"""

from dataclasses import dataclass

# Avance (secondes) : la mise en avant ne traîne pas derrière la voix.
LEAD_SECONDS = 0.3


@dataclass(frozen=True)
class LyricLine:
    time: float
    text: str


@dataclass(frozen=True)
class Lyrics:
    lines: tuple = ()            # LyricLine, triées ; vide sans synchro
    plain: str | None = None
    instrumental: bool = False
    source: str | None = None    # 'file', 'lrclib' ou None

    @property
    def synced(self) -> bool:
        return bool(self.lines)

    @property
    def empty(self) -> bool:
        return not self.lines and not self.plain and not self.instrumental


def parse_lyrics(payload) -> Lyrics:
    """Réponse du serveur → Lyrics. Une forme inattendue donne des paroles vides."""
    if not isinstance(payload, dict):
        return Lyrics()
    lines = []
    if payload.get('synced'):
        for line in payload.get('lines') or ():
            try:
                lines.append(LyricLine(float(line['time']), str(line.get('text') or '')))
            except (KeyError, TypeError, ValueError):
                continue
    lines.sort(key=lambda line: line.time)
    plain = payload.get('plain')
    return Lyrics(
        lines=tuple(lines),
        plain=plain if isinstance(plain, str) and plain.strip() else None,
        instrumental=bool(payload.get('instrumental')),
        source=payload.get('source') if isinstance(payload.get('source'), str) else None,
    )


def current_line_index(lines, position: float) -> int:
    """La ligne chantée : la dernière commencée à `position`, -1 avant la première."""
    lo, hi, found = 0, len(lines) - 1, -1
    while lo <= hi:
        mid = (lo + hi) // 2
        if lines[mid].time <= position + LEAD_SECONDS:
            found, lo = mid, mid + 1
        else:
            hi = mid - 1
    return found
