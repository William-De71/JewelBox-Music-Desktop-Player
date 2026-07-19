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
    python3-gobject gtk4-devel libadwaita-devel python3-pytest python3-pytest-cov
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

Tests (logique pure uniquement, sans GTK) avec la gate de couverture
(min 95 %, la même que la CI applique sur chaque PR) :

```bash
pytest --cov
```

### Raccourcis Make

Un `Makefile` regroupe les commandes du quotidien (`make help` pour la liste) :

| Commande | Action |
|---|---|
| `make test` | tests + gate de couverture 95 % (identique à la CI) |
| `make run` | lance l'app en mode dev en **rendu logiciel** (`GSK_RENDERER=cairo`) |
| `make run-gpu` | lance l'app en mode dev en rendu GPU normal |
| `make server` | démarre le serveur JewelBox local sur `:3001` (clone attendu dans `../JewelBox-Music-Library`, surchargable : `make server SERVER_DIR=…`) |
| `make flatpak` | build + installation + lancement du Flatpak |

`make run` existe parce qu'un pilote graphique instable peut figer la machine
au sondage GL du démarrage (vécu avec `nouveau`) : le rendu logiciel permet de
tester l'app sans toucher au GPU. Avec un pilote sain, `make run-gpu`.

## Contribuer

Pas de commit direct sur `main` : une branche par fonctionnalité/correctif,
PR obligatoire. La CI exécute les tests avec gate de couverture 95 % (rapport
sur la PR) et valide le build Meson. Un tag `v*.*.*` déclenche le build du
bundle Flatpak et sa publication en release GitHub.

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
