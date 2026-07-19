"""Identité de l'appareil (logique pure).

Le device-id est un UUID généré au premier lancement, persisté dans GSettings
et envoyé au serveur dans l'en-tête X-Device-Id (file de lecture par
appareil). Il ne doit JAMAIS être régénéré tant qu'une valeur existe : le
serveur s'en sert pour retrouver la file de cet appareil.
"""

import uuid


def ensure_device_id(current) -> str:
    """Renvoie l'identifiant existant, ou en génère un s'il n'y en a pas.

    Toute valeur non vide (espaces exclus) est conservée telle quelle — même
    « legacy » ou étrange, elle identifie déjà cet appareil côté serveur.
    """
    existing = (current or '').strip()
    if existing:
        return existing
    return str(uuid.uuid4())
