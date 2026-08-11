desktop_file = i18n.merge_file(
  input: app_id + '.desktop.in',
  output: app_id + '.desktop',
  type: 'desktop',
  po_dir: '../po',
  install: true,
  install_dir: get_option('datadir') / 'applications',
)

desktop_utils = find_program('desktop-file-validate', required: false)
if desktop_utils.found()
  test('validate-desktop', desktop_utils, args: [desktop_file])
endif

metainfo_file = i18n.merge_file(
  input: app_id + '.metainfo.xml.in',
  output: app_id + '.metainfo.xml',
  po_dir: '../po',
  install: true,
  install_dir: get_option('datadir') / 'metainfo',
)

# Le metainfo porte la version que voient les utilisateurs : elle doit suivre
# celle du projet, sinon la release part avec une étiquette périmée.
test('validate-version-sync',
  python,
  args: [
    meson.current_source_dir() / 'check-version-sync.py',
    meson.current_source_dir() / app_id + '.metainfo.xml.in',
    meson.project_version(),
  ],
)

install_data(app_id + '.gschema.xml',
  install_dir: get_option('datadir') / 'glib-2.0' / 'schemas',
)

compile_schemas = find_program('glib-compile-schemas', required: false)
if compile_schemas.found()
  test('validate-gschema', compile_schemas,
    args: ['--strict', '--dry-run', meson.current_source_dir()],
  )
endif

install_data('icons/hicolor/scalable/apps' / app_id + '.svg',
  install_dir: get_option('datadir') / 'icons/hicolor/scalable/apps',
)
install_data('icons/hicolor/symbolic/apps' / app_id + '-symbolic.svg',
  install_dir: get_option('datadir') / 'icons/hicolor/symbolic/apps',
)

# Cœurs favori/non-favori : Adwaita ne fournit qu'un cœur plein
# (emote-love-symbolic) et aucune variante contour, d'où ces deux icônes
# maison pour rester en parité avec le client Android. Elles vont dans
# symbolic/apps : c'est le seul répertoire symbolique déclaré par l'index.theme
# de hicolor, donc le seul où GTK sait les résoudre.
install_data(
  [
    'icons/hicolor/symbolic/apps/jewelbox-favorite-symbolic.svg',
    'icons/hicolor/symbolic/apps/jewelbox-not-favorite-symbolic.svg',
  ],
  install_dir: get_option('datadir') / 'icons/hicolor/symbolic/apps',
)
