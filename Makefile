# Raccourcis de développement — `make help` pour la liste.
# Le build « officiel » reste Meson/Flatpak ; ceci n'est que du confort local.

# Clone du serveur pour `make server` (surchargable : make server SERVER_DIR=…)
SERVER_DIR ?= ../JewelBox-Music-Library

.PHONY: help test run run-gpu server flatpak

help:
	@echo "Cibles disponibles :"
	@echo "  make test     - tests + gate de couverture (comme la CI, min 95 %)"
	@echo "  make run      - lance l'app en mode dev, rendu logiciel (sans risque GPU)"
	@echo "  make run-gpu  - lance l'app en mode dev, rendu GPU normal"
	@echo "  make server   - démarre le serveur JewelBox local sur :3001"
	@echo "  make flatpak  - build + installation + lancement du Flatpak"

test:
	pytest --cov

# GSK_RENDERER=cairo : rendu logiciel, tant que nouveau (Quadro P520) n'est
# pas neutralisé — un sondage GL peut figer la machine (gel du 19/07/2026).
run:
	cd src && GSK_RENDERER=cairo python3 -m jewelbox

run-gpu:
	cd src && python3 -m jewelbox

server:
	cd $(SERVER_DIR) && npm run start --workspace=server

flatpak:
	flatpak-builder --user --install --force-clean build/ io.github.william_de71.JewelBox.json
	flatpak run io.github.william_de71.JewelBox
