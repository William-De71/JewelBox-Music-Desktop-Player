"""Formatage pur (sans dépendance GTK) — testé par tests/test_formats.py."""


def format_duration(seconds) -> str:
    """Durée lisible : 245 → « 4:05 », 3725 → « 1:02:05 ».

    Les valeurs None, négatives ou non finies donnent « 0:00 ».
    """
    try:
        total = int(seconds)
    except (TypeError, ValueError, OverflowError):
        return '0:00'
    if total < 0:
        return '0:00'

    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f'{hours}:{minutes:02d}:{secs:02d}'
    return f'{minutes}:{secs:02d}'
