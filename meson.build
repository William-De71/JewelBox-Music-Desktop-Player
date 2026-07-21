project('jewelbox',
  version: '0.2.1',
  meson_version: '>= 1.0.0',
  license: 'MIT',
)

i18n = import('i18n')
gnome = import('gnome')

app_id = 'io.github.william_de71.JewelBox'
pkgdatadir = get_option('prefix') / get_option('datadir') / meson.project_name()

python = import('python').find_installation('python3')

subdir('data')
subdir('src')
subdir('po')

gnome.post_install(
  glib_compile_schemas: true,
  gtk_update_icon_cache: true,
  update_desktop_database: true,
)

# Tests de la logique pure — seulement si pytest est disponible
# (il ne l'est pas dans le SDK Flatpak, où seuls les autres tests tournent).
pytest_found = run_command(python, '-c', 'import pytest', check: false)
if pytest_found.returncode() == 0
  test('pytest',
    python,
    args: ['-m', 'pytest', '-q', meson.project_source_root() / 'tests'],
    workdir: meson.project_source_root(),
  )
endif
