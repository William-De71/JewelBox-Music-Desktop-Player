# Raccourcis de développement — `make help` pour la liste.
# Le build « officiel » reste Meson/Flatpak ; ceci n'est que du confort local.

# Clone du serveur pour `make server` (surchargable : make server SERVER_DIR=…)
SERVER_DIR ?= ../JewelBox-Music-Library

APP_ID = io.github.william_de71.JewelBox

.PHONY: help test run run-gpu server flatpak flatpak-reset

help:
	@echo "Cibles disponibles :"
	@echo "  make test     - tests + gate de couverture (comme la CI, min 95 %)"
	@echo "  make run      - lance l'app en mode dev, rendu logiciel (sans risque GPU)"
	@echo "  make run-gpu  - lance l'app en mode dev, rendu GPU normal"
	@echo "  make server   - démarre le serveur JewelBox local sur :3001"
	@echo "  make flatpak  - build + installation + lancement du Flatpak (build LOCAL)"
	@echo "  make flatpak-reset - réinstalle depuis le dépôt publié (rétablit flatpak update)"

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

# --install rattache l'app installée au cache de flatpak-builder (origin
# « jewelbox-origin ») au lieu du dépôt publié : `flatpak update` interroge
# alors ce cache local et ne voit plus jamais les releases. `make flatpak-reset`
# rebascule sur le dépôt « jewelbox » une fois la session de dev terminée.
flatpak:
	flatpak-builder --user --install --force-clean build/ $(APP_ID).json
	flatpak run $(APP_ID)
	@echo
	@echo "⚠️  Build LOCAL installé : « flatpak update » ne suivra plus les releases."
	@echo "   Pour revenir au dépôt publié : make flatpak-reset"

flatpak-reset:
	flatpak uninstall --user -y $(APP_ID) || true
	flatpak install --user -y jewelbox $(APP_ID)
	@echo "✅ Réinstallé depuis le dépôt publié — « flatpak update » suit de nouveau les releases."
