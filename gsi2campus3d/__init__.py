"""GSI2Campus3D — standalone QGIS plugin (v0.4.29)."""


def classFactory(iface):
    from .gsi2campus3d import GSI2Campus3D, MainDialog
    # Bundled v0.4.29 feature: no separate add-on needs installing.
    from .campus3d_local_extent import install_local_extent
    install_local_extent(MainDialog)
    return GSI2Campus3D(iface)
