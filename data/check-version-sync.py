#!/usr/bin/env python3
"""Vérifie que le metainfo annonce bien la version du projet.

La version publiée que voient les utilisateurs (GNOME Logiciels,
`flatpak remote-ls`) vient du premier <release> du metainfo, pas de
meson.build. Les deux ont divergé silencieusement de 0.1.0 à 0.6.0 : ce test
fait échouer la CI plutôt que de laisser repartir une release mal étiquetée.

Usage : check-version-sync.py <metainfo.xml.in> <version attendue>
"""
import sys
import xml.etree.ElementTree as ET

metainfo_path, expected = sys.argv[1], sys.argv[2]

releases = ET.parse(metainfo_path).getroot().find('releases')
if releases is None or len(releases) == 0:
    sys.exit(f"❌ {metainfo_path} : aucun <release> déclaré "
             f"(attendu au moins la version {expected}).")

found = releases[0].get('version')
if found != expected:
    sys.exit(
        f"❌ Version désynchronisée : meson.build dit « {expected} », "
        f"le premier <release> du metainfo dit « {found} ».\n"
        f"   Le metainfo est ce qu'AppStream publie : ajoute une entrée "
        f"<release version=\"{expected}\" …> en tête du bloc <releases> "
        f"de {metainfo_path}."
    )

print(f"✅ Version cohérente entre meson.build et le metainfo : {expected}")
