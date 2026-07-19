# JewelBox Music Desktop Player

Client desktop Linux (GTK4 + libadwaita + Python) du serveur
[JewelBox Music Library](https://github.com/William-De71/jewelbox-music-library).
Client **pur streaming** : aucune bibliothèque locale, tout est lu depuis le
serveur sur le réseau local. Parité fonctionnelle visée avec le
[client Android](https://github.com/William-De71/jewelbox-music-player).

## Stack

- **UI** : GTK4 + libadwaita (PyGObject), fichiers UI en Blueprint (`.blp`)
- **Audio** : GStreamer `playbin3` (seek HTTP via Range requests, gapless)
- **HTTP** : libsoup 3 + asyncio sur la boucle GLib (`gi.events`)
- **Intégrations desktop** : MPRIS (D-Bus), découverte mDNS via Avahi
- **Réglages** : GSettings — **Build** : Meson — **Distribution** : Flatpak
  (`org.gnome.Platform` 50)

Les chaînes source de l'app sont en français, comme sur Android ; l'infra
gettext (`po/`) est en place pour d'éventuelles traductions.

## Développement

Dépendances Fedora :

```bash
sudo dnf install meson ninja-build blueprint-compiler flatpak-builder \
    python3-gobject gtk4-devel libadwaita-devel
flatpak install flathub org.gnome.Sdk//50
```

Lancement rapide sans installation (utilise le GTK du système) :

```bash
cd src && python3 -m jewelbox
```

Build et installation Flatpak :

```bash
flatpak-builder --user --install --force-clean build/ io.github.william_de71.JewelBox.json
flatpak run io.github.william_de71.JewelBox
```

Tests (logique pure uniquement, sans GTK) :

```bash
pytest tests/
```

## Architecture

```
src/jewelbox/
├── main.py, window.py     # Adw.Application / fenêtre à 4 onglets
├── api/                   # client HTTP (libsoup), modèles, parsing (pur)
├── core/                  # logique pure testable : scrobbler, mixsync,
│                          #   file persistée, formats, réglages
├── playback/              # playbin3, file de lecture, MPRIS
├── discovery/             # découverte Avahi (_jewelbox._tcp)
└── ui/                    # pages et widgets (Python + Blueprint)
```

## Feuille de route

Livraison par phases (chaque phase = commit fonctionnel) : squelette →
client API + réglages → bibliothèque → lecture audio → player UI + reprise →
accueil + recherche → playlists → smart playlists + mix dynamique →
scrobbling Last.fm → MPRIS → découverte mDNS → finitions.
