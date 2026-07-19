"""Lancement en mode développement, sans installation Meson :

    cd src && python3 -m jewelbox
"""
import sys

from jewelbox.main import main

sys.exit(main('dev'))
