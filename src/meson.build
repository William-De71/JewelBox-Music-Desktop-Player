moduledir = pkgdatadir / 'jewelbox'

gnome.compile_resources('jewelbox',
  'jewelbox.gresource.xml',
  gresource_bundle: true,
  install: true,
  install_dir: pkgdatadir,
)

conf = configuration_data()
conf.set('PYTHON', python.full_path())
conf.set('VERSION', meson.project_version())
conf.set('localedir', get_option('prefix') / get_option('localedir'))
conf.set('pkgdatadir', pkgdatadir)

configure_file(
  input: 'jewelbox.in',
  output: 'jewelbox',
  configuration: conf,
  install: true,
  install_dir: get_option('bindir'),
  install_mode: 'r-xr-xr-x',
)

install_subdir('jewelbox', install_dir: pkgdatadir)
