"""Safe, descriptive output folder names for local Campus3D exports.

No QGIS dependency so path policy can be tested separately.
"""
import math
import os


def _display_metres(value):
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise ValueError('距離・格子間隔は正の有限値で指定してください。')
    # Up to millimetres in the label; no fake precision in the underlying DEM.
    fmt = ('%.3f' % value).rstrip('0').rstrip('.')
    if fmt == '0':
        raise ValueError('格子間隔が小さすぎます。')
    return fmt.replace('.', 'p')


def _display_extent_metres(value):
    """Round region dimensions to whole metres; exact bounds stay in JSON."""
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise ValueError('範囲寸法は正の有限値で指定してください。')
    return str(int(round(value))) if value >= 1 else _display_metres(value)


def local_folder_prefix(spacing_m, width_m, height_m):
    return ('Campus3D_Blender_Local_'
            + _display_metres(spacing_m) + 'm_'
            + _display_extent_metres(width_m) + 'x'
            + _display_extent_metres(height_m) + 'm')


def next_local_folder(parent, spacing_m, width_m, height_m):
    """Find a new numbered destination; never choose an existing directory."""
    prefix = local_folder_prefix(spacing_m, width_m, height_m)
    for serial in range(1, 100000):
        target = os.path.join(parent, '%s_%03d' % (prefix, serial))
        if not os.path.lexists(target):
            return target
    raise RuntimeError('連番が上限に達しました。出力先を変更してください。')
