"""Local extent export extension for Campus3D GSI2Campus3D v0.4.29.

Purpose: crop DEM + all geometry to an interactively selected rectangle while
preserving the existing OBJ/texture exporter and the official plugin source.
Requires QGIS 3.44 (PyQGIS, Processing, GDAL).
"""
from __future__ import annotations

import gc
import json
import math
import os
import shutil
import tempfile
from uuid import uuid4

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (
    QMessageBox, QCheckBox, QFormLayout, QGroupBox, QHBoxLayout,
    QLabel, QPushButton, QWidget,
)
from qgis.core import (
    QgsCoordinateTransform, QgsFeature, QgsGeometry,
    Qgis, QgsProject, QgsRasterLayer, QgsRectangle,
    QgsVectorLayer,
)
from qgis.gui import QgsMapToolExtent, QgsRubberBand
import processing
from .campus3d_package_naming import next_local_folder

EXTENSION_BUILD = 'v0.4.29-integrated.1'
_MAX_VERTICES = 1_000_000  # conservative limit for detail-region exports


def _area_dimensions(rect, spacing_m):
    """Pure geometric grid estimate, including both boundary vertices."""
    w, h, spacing = rect.width(), rect.height(), float(spacing_m)
    if (not all(map(math.isfinite, (w, h, spacing)))
            or min(w, h, spacing) <= 0):
        raise ValueError('範囲寸法と格子間隔には正の有限値が必要です。')
    nx = max(2, math.ceil(w / spacing) + 1)
    ny = max(2, math.ceil(h / spacing) + 1)
    return nx, ny, nx * ny


def _clip_dem(dem, rect, directory, terrain_spacing_m):
    """GDAL Warp to an EXACT projected rectangle, at source-native pixel density.

    A high-resolution terrain grid is then sampled independently by the
    official exporter. This does not invent extra source DEM information.
    """
    from osgeo import gdal

    uri = dem.source().split('|', 1)[0]
    source = gdal.Open(uri, gdal.GA_ReadOnly)
    if source is None:
        raise RuntimeError('DEMをGDALから開けません。ラスターデータの形式を確認してください。')
    band = source.GetRasterBand(1)
    if band is None:
        raise RuntimeError('DEMに標高バンドがありません。')
    # dem.rasterUnitsPerPixelX/Y honor the QGIS raster georeference.
    dx = abs(float(dem.rasterUnitsPerPixelX()))
    dy = abs(float(dem.rasterUnitsPerPixelY()))
    if not math.isfinite(dx) or not math.isfinite(dy) or dx <= 0 or dy <= 0:
        raise RuntimeError('DEMのピクセル解像度が取得できません。')
    # The existing exporter samples elevations at terrain grid vertices.
    # Oversampling the DEM at half the requested mesh spacing provides smooth
    # bilinear interpolation at those vertices, not extra measured detail.
    crop_dx = min(dx, max(0.05, float(terrain_spacing_m) / 2.0))
    crop_dy = min(dy, max(0.05, float(terrain_spacing_m) / 2.0))
    nx = max(1, math.ceil(rect.width() / crop_dx))
    ny = max(1, math.ceil(rect.height() / crop_dy))
    if nx * ny > 60_000_000:
        raise RuntimeError('切り出しラスタが6,000万画素を超えます。対象範囲を狭めてください。')
    path = os.path.join(directory, 'Selected_DEM.tif')
    # Warp handles source NoData, reprojection when needed, and exact bounds.
    warped = gdal.Warp(
        path, source,
        format='GTiff',
        outputBounds=(rect.xMinimum(), rect.yMinimum(),
                      rect.xMaximum(), rect.yMaximum()),
        width=nx, height=ny,
        dstSRS=dem.crs().toWkt(),
        resampleAlg='bilinear',
        dstNodata=-999999.0,
        outputType=gdal.GDT_Float32,
        creationOptions=['TILED=YES', 'COMPRESS=LZW'],
    )
    if warped is None:
        raise RuntimeError('指定範囲のDEM切り出しに失敗しました。')
    warped.FlushCache()
    warped = None
    source = None
    clipped = QgsRasterLayer(path, 'Campus3D_Selected_DEM', 'gdal')
    if not clipped.isValid():
        raise RuntimeError('切り出したDEMをQGISで開けませんでした。')
    return clipped, (dx, dy), (rect.width() / nx, rect.height() / ny)


def _clip_vector(layer, rect, dem_crs, overlay):
    """Return geometries clipped to a rectangle, in DEM CRS, in memory.

    Clip, rather than bounding-box filtering, prevents roads/buildings from
    crossing the exported terrain footprint. Source attributes are inherited.
    """
    if layer is None or not layer.isValid():
        return None
    source = layer
    if source.crs() != dem_crs:
        source = processing.run('native:reprojectlayer', {
            'INPUT': source, 'TARGET_CRS': dem_crs, 'OUTPUT': 'memory:'
        })['OUTPUT']
    result = processing.run('native:clip', {
        'INPUT': source, 'OVERLAY': overlay, 'OUTPUT': 'memory:'
    })['OUTPUT']
    if result is None or not result.isValid():
        raise RuntimeError(f'ベクターのクリップに失敗しました: {layer.name()}')
    return result


def _overlay_for_rect(rect, crs):
    layer = QgsVectorLayer('Polygon?crs=' + crs.authid(),
                           'Campus3D_ExportExtent', 'memory')
    if not layer.isValid():
        raise RuntimeError('範囲クリップ用の一時ポリゴンを作成できません。')
    feature = QgsFeature(layer.fields())
    feature.setGeometry(QgsGeometry.fromRect(rect))
    ok, _ = layer.dataProvider().addFeatures([feature])
    if not ok:
        raise RuntimeError('範囲クリップ用ポリゴンを書き込めません。')
    layer.updateExtents()
    return layer


def _save_numbered_package(staged, out_folder, rect, terrain_spacing, manifest, metadata):
    """Move package into a never-before-used directory without touching old exports.

    The source staging directory is on the same volume as the destination.
    A concurrent naming collision is handled by retrying another sequence.
    A directory rename never merges existing package contents on Windows.
    """
    for _ in range(10000):
        final = next_local_folder(out_folder, terrain_spacing,
                                  rect.width(), rect.height())
        name = os.path.basename(final)
        manifest['package_name'] = name
        manifest['local_export'] = {
            'terrain_spacing_m': float(terrain_spacing),
            'folder_name': name,
            'version': '0.4.29',
        }
        metadata['package_name'] = name
        with open(os.path.join(staged, 'campus3d_manifest.json'), 'w', encoding='utf-8') as fp:
            json.dump(manifest, fp, ensure_ascii=False, indent=2)
        with open(os.path.join(staged, 'campus3d_metadata.json'), 'w', encoding='utf-8') as fp:
            json.dump(metadata, fp, ensure_ascii=False, indent=2)
        if os.path.lexists(final):
            continue
        try:
            os.rename(staged, final)
            return final
        except OSError:
            if os.path.lexists(final):
                continue  # Another export may have claimed this number.
            raise
    raise RuntimeError('空きの出力フォルダ名を確保できませんでした。')


def install_local_extent(dialog_type):
    """Inject the feature once. Patches the bundled base class at startup without modifying existing workflow logic."""
    if getattr(dialog_type, '_campus3d_local_extent_installed', False):
        return
    required = ('export_blender_package', '_terrain_grid_dimensions',
                'update_terrain_estimate')
    for name in required:
        if not hasattr(dialog_type, name):
            raise RuntimeError(f'Campus3Dの互換機能がありません: {name}。プラグイン本体と拡張モジュールの整合性を確認してください。')

    original_init = dialog_type.__init__
    original_export = dialog_type.export_blender_package
    original_grid = dialog_type._terrain_grid_dimensions
    original_estimate = dialog_type.update_terrain_estimate
    original_close = dialog_type.closeEvent

    def _active_extent(self, dem=None):
        if not (hasattr(self, 'c3d_local_checkbox')
                and self.c3d_local_checkbox.isChecked()):
            return None
        selected = getattr(self, '_c3d_selected_extent', None)
        src_crs = getattr(self, '_c3d_selected_crs', None)
        if selected is None or src_crs is None:
            raise RuntimeError('任意範囲出力がONです。先に地図上で矩形を選択してください。')
        if dem is None:
            dem = getattr(self, '_last_dem', None)
        if dem is None or not dem.isValid():
            raise RuntimeError('先に3Dモデル用データを作成してください。')
        transform = QgsCoordinateTransform(
            src_crs, dem.crs(), QgsProject.instance().transformContext())
        rect = transform.transformBoundingBox(selected)
        values = (rect.xMinimum(), rect.xMaximum(),
                  rect.yMinimum(), rect.yMaximum())
        if not all(map(math.isfinite, values)) or rect.isEmpty():
            raise RuntimeError('選択領域の座標変換に失敗しました。')
        # Do not silently shrink the user-selected area or export untrusted data.
        full = dem.extent()
        tolerance = max(full.width(), full.height()) * 1e-10
        if (rect.xMinimum() < full.xMinimum() - tolerance
                or rect.yMinimum() < full.yMinimum() - tolerance
                or rect.xMaximum() > full.xMaximum() + tolerance
                or rect.yMaximum() > full.yMaximum() + tolerance):
            raise RuntimeError('選択矩形がDEMの有効範囲外にはみ出しています。DEM内で選び直してください。')
        return rect

    def _update_extent_label(self):
        selected = getattr(self, '_c3d_selected_extent', None)
        if selected is None:
            self.c3d_extent_label.setText('選択範囲：未指定')
        elif not self.c3d_local_checkbox.isChecked():
            self.c3d_extent_label.setText('選択範囲：保存済み（現在は区画全体を出力）')
        else:
            try:
                dem = _preview_dem(self)
                r = self._c3d_active_extent(dem)
                epsg = dem.crs().authid() if dem is not None and dem.isValid() else ''
                self.c3d_extent_label.setText(
                    f'選択範囲：{r.width():.1f} × {r.height():.1f} m '
                    f'（{epsg}）')
            except Exception:
                self.c3d_extent_label.setText('範囲取得済み（DEM未生成、または範囲外）')
        try:
            self.update_terrain_estimate()
        except Exception:
            pass
        preview = getattr(self, 'c3d_path_preview', None)
        if preview is not None:
            if not self.c3d_local_checkbox.isChecked():
                preview.setText('Campus3D_Blender（区画全体：従来どおり）')
            elif selected is None:
                preview.setText('範囲選択後に保存フォルダ名を表示します')
            else:
                try:
                    r = self._c3d_active_extent(_preview_dem(self))
                    target = next_local_folder(self.out_edit.text().strip() or '.',
                                               self.terrain_res.value(),
                                               r.width(), r.height())
                    preview.setText(os.path.basename(target) + '（別保存・上書きなし）')
                except Exception:
                    preview.setText('範囲・DEMが確定するとフォルダ名を表示します')

    def _preview_dem(self):
        """Resolve the same DEM that the existing Blender export would use."""
        dem = getattr(self, '_last_dem', None)
        if dem is not None and dem.isValid():
            return dem
        try:
            epsg = self.zone.currentData()[1]
            layers = QgsProject.instance().mapLayersByName(f'DEM_EPSG{epsg}')
            return next((layer for layer in layers if layer.isValid()), None)
        except Exception:
            return None

    def _recommended_spacing(rect, vertex_limit):
        """Smallest 0.1-m UI increment whose grid count fits the vertex cap."""
        left, right = 0.0, max(rect.width(), rect.height())
        for _ in range(65):
            mid = (left + right) / 2.0
            if mid <= 0:
                break
            if _area_dimensions(rect, mid)[2] <= vertex_limit:
                right = mid
            else:
                left = mid
        spacing = max(1.0, math.ceil((right - 1e-10) * 10.0) / 10.0)
        while _area_dimensions(rect, spacing)[2] > vertex_limit:
            spacing = round(spacing + 0.1, 1)
        return spacing

    def _set_resolution_notice(self, content, severity='normal'):
        notice = getattr(self, 'c3d_resolution_notice', None)
        if notice is None:
            return
        color = {
            'error': '#BF3030',
            'warning': '#986600',
            'normal': '',
        }.get(severity, '')
        notice.setText(content)
        notice.setStyleSheet(
            f'QLabel {{ font-weight: 600; color: {color}; }}' if color else
            'QLabel { font-weight: 500; }'
        )

    def new_estimate(self, *_):
        """Immediately preview mesh size for rectangle or full DEM, no heavy raster IO."""
        if not hasattr(self, 'c3d_resolution_notice'):
            return original_estimate(self)
        dem = _preview_dem(self)
        if dem is None:
            self.terrain_estimate.setText('DEM生成後に格子数を計算します')
            _set_resolution_notice(self, '先に［3Dモデル用データを作成］を実行してください。')
            return
        local = self.c3d_local_checkbox.isChecked()
        if local and getattr(self, '_c3d_selected_extent', None) is None:
            self.terrain_estimate.setText('任意範囲：未指定')
            _set_resolution_notice(self, '地図上で矩形を選択すると、格子数・負荷を自動計算します。')
            return
        try:
            rect = _active_extent(self, dem) if local else dem.extent()
            nx, ny, n = _area_dimensions(rect, self.terrain_res.value())
        except Exception as exc:
            self.terrain_estimate.setText('見積り不可')
            _set_resolution_notice(self, f'⚠ 範囲・解像度を確認してください：{exc}', 'error')
            return
        triangles = 2 * (nx - 1) * (ny - 1)
        self.terrain_estimate.setText(
            f'対象：{rect.width():,.1f} × {rect.height():,.1f} m　／　'
            f'格子：{nx:,} × {ny:,} ≒ {n:,} 頂点\n'
            f'最大 {triangles:,} 三角形（地形のみ・概算）'
        )
        cap = _MAX_VERTICES if local else 12_000_000
        caution = 250_000 if local else 2_000_000
        if n > cap:
            required = _recommended_spacing(rect, cap)
            _set_resolution_notice(
                self, f'⚠ 出力上限超過：{n:,} 頂点（上限 {cap:,}）。'
                f'格子間隔を約 {required:g} m 以上にするか、範囲を狭めてください。',
                'error'
            )
        elif n > caution:
            _set_resolution_notice(
                self, f'⚠ 大規模出力：{n:,} 頂点。'
                '時間やメモリを多く使う可能性があります。', 'warning'
            )
        else:
            _set_resolution_notice(
                self, f'✓ 出力規模は目安の範囲内です（{n:,} 頂点）。'
                '小さい格子間隔ほど処理負荷は増えます。'
            )

    def _return_to_previous_map_tool(self):
        canvas = self.iface.mapCanvas()
        tool = getattr(self, '_c3d_map_tool', None)
        if tool is not None and canvas.mapTool() is tool:
            previous = getattr(self, '_c3d_prev_tool', None)
            if previous is not None and previous is not tool:
                canvas.setMapTool(previous)
            else:
                canvas.unsetMapTool(tool)

    def _extent_chosen(self, extent):
        if extent is None or extent.isEmpty() or extent.width() <= 0 or extent.height() <= 0:
            return
        canvas = self.iface.mapCanvas()
        self._c3d_selected_extent = QgsRectangle(extent)
        self._c3d_selected_crs = canvas.mapSettings().destinationCrs()
        self.c3d_local_checkbox.setChecked(True)
        _return_to_previous_map_tool(self)
        tool = getattr(self, '_c3d_map_tool', None)
        if tool is not None:
            tool.clearRubberBand()
        # Persistent outline after the drawing tool relinquishes the canvas.
        self._c3d_outline.setToGeometry(QgsGeometry.fromRect(extent), None)
        self._c3d_outline.show()
        _update_extent_label(self)

    def _begin_extent_selection(self):
        canvas = self.iface.mapCanvas()
        _return_to_previous_map_tool(self)
        self._c3d_prev_tool = canvas.mapTool()
        if getattr(self, '_c3d_map_tool', None) is None:
            self._c3d_map_tool = QgsMapToolExtent(canvas)
            self._c3d_map_tool.extentChanged.connect(
                lambda e: _extent_chosen(self, e))
        try:
            self._c3d_map_tool.clearRubberBand()
        except Exception:
            pass
        self.c3d_extent_label.setText('地図上でドラッグして矩形を指定してください…')
        canvas.setMapTool(self._c3d_map_tool)

    def _clear_extent(self):
        _return_to_previous_map_tool(self)
        self._c3d_selected_extent = None
        self._c3d_selected_crs = None
        self._c3d_outline.hide()
        self._c3d_outline.reset(Qgis.GeometryType.Polygon)
        self.c3d_local_checkbox.setChecked(False)
        _update_extent_label(self)

    def new_init(self, iface):
        original_init(self, iface)
        self._c3d_selected_extent = None
        self._c3d_selected_crs = None
        self._c3d_map_tool = None
        self._c3d_prev_tool = None
        self._c3d_outline = QgsRubberBand(iface.mapCanvas(), Qgis.GeometryType.Polygon)
        # Show ONLY the selected rectangle's four edges.  The previous
        # setColor(opaque_blue) also filled the polygon at 100% opacity,
        # obscuring the map while selecting a detailed export area.
        self._c3d_outline.setStrokeColor(QColor(38, 105, 240))
        self._c3d_outline.setFillColor(QColor(38, 105, 240, 0))
        self._c3d_outline.setBrushStyle(Qt.NoBrush)
        self._c3d_outline.setWidth(2)
        self._c3d_outline.hide()

        boxes = self.findChildren(QGroupBox)
        box = next((b for b in boxes if '[A-2]' in b.title()), None)
        if box is None or not isinstance(box.layout(), QFormLayout):
            raise RuntimeError('Blender出力の設定画面が見つかりません。')
        form = box.layout()
        self.c3d_local_checkbox = QCheckBox('任意矩形範囲だけをBlenderへ出力')
        self.c3d_local_checkbox.setChecked(False)
        self.c3d_local_checkbox.toggled.connect(lambda _: _update_extent_label(self))
        self.terrain_res.valueChanged.connect(lambda _: _update_extent_label(self))
        insert_at = form.getWidgetPosition(self.export_blender_btn)[0]
        form.insertRow(insert_at, '出力領域', self.c3d_local_checkbox)
        insert_at += 1
        row_widget = QWidget(self)
        row = QHBoxLayout(row_widget)
        row.setContentsMargins(0, 0, 0, 0)
        select_btn = QPushButton('地図上で矩形を選択')
        select_btn.clicked.connect(lambda: _begin_extent_selection(self))
        clear_btn = QPushButton('選択解除')
        clear_btn.clicked.connect(lambda: _clear_extent(self))
        row.addWidget(select_btn)
        row.addWidget(clear_btn)
        form.insertRow(insert_at, '範囲操作', row_widget)
        insert_at += 1
        self.c3d_extent_label = QLabel('選択範囲：未指定')
        self.c3d_extent_label.setWordWrap(True)
        form.insertRow(insert_at, '選択結果', self.c3d_extent_label)
        insert_at += 1

        # Prominent, always-visible resolution settings immediately below the
        # area selector.  Both widgets already exist in the base class but are
        # intentionally not added to its form before this point.
        self.c3d_resolution_box = QGroupBox('地形メッシュ解像度・出力規模（変更すると即時更新）', self)
        resolution_form = QFormLayout(self.c3d_resolution_box)
        resolution_form.addRow('Terrain格子間隔', self.terrain_res)
        resolution_form.addRow('頂点数の見積り', self.terrain_estimate)
        self.c3d_resolution_notice = QLabel('範囲・間隔から自動判定します')
        self.c3d_resolution_notice.setWordWrap(True)
        resolution_form.addRow(self.c3d_resolution_notice)
        help_label = QLabel('※ 格子間隔を小さくすると地形が細かくなりますが、元DEM自体の精度は上がりません。')
        help_label.setWordWrap(True)
        resolution_form.addRow(help_label)
        font = self.terrain_res.font()
        font.setBold(True)
        self.terrain_res.setFont(font)
        form.insertRow(insert_at, self.c3d_resolution_box)
        insert_at += 1

        self.c3d_path_preview = QLabel('局所出力は解像度・範囲寸法・連番で別保存します')
        self.c3d_path_preview.setWordWrap(True)
        form.insertRow(insert_at, '保存フォルダ', self.c3d_path_preview)
        insert_at += 1
        notice = QLabel('選択範囲は一時的なものです。既存のm単位Terrain格子間隔を使い、'
                        'DEM・建物・道路・画像を同じ範囲で出力します。')
        notice.setWordWrap(True)
        form.insertRow(insert_at, notice)
        # Includes loaded settings and DEM layers retained in the QGIS project.
        _update_extent_label(self)

    def new_grid(self, dem, spacing):
        if not (hasattr(self, 'c3d_local_checkbox')
                and self.c3d_local_checkbox.isChecked()):
            return original_grid(self, dem, spacing)
        if getattr(self, '_c3d_selected_extent', None) is None:
            return original_grid(self, dem, spacing)  # until selection is made
        rect = _active_extent(self, dem)
        result = _area_dimensions(rect, spacing)
        return result

    def new_export(self, out_folder, dem, building_items, road_layer, epsg,
                   scale_denominator, terrain_resolution, *args, **kwargs):
        if not (hasattr(self, 'c3d_local_checkbox')
                and self.c3d_local_checkbox.isChecked()):
            return original_export(
                self, out_folder, dem, building_items, road_layer, epsg,
                scale_denominator, terrain_resolution, *args, **kwargs)

        rect = _active_extent(self, dem)
        nx, ny, verts = _area_dimensions(rect, terrain_resolution)
        if verts > _MAX_VERTICES:
            raise RuntimeError(f'局所Terrainが{verts:,}頂点になります（上限{_MAX_VERTICES:,}）。格子間隔を広げてください。')
        if verts > 250_000:
            answer = QMessageBox.warning(
                self, '高精細Terrain出力の確認',
                f'選択範囲は約{verts:,}頂点、最大{2*(nx-1)*(ny-1):,}三角形になります。'
                '処理が長時間に及ぶ可能性があります。続行しますか？',
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer != QMessageBox.Yes:
                raise RuntimeError('高精細Terrain出力はキャンセルされました。')
        os.makedirs(out_folder, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.campus3d_extent_', dir=out_folder) as staging:
            clipped_dem, source_pixels, interpolation_pixels = _clip_dem(dem, rect, staging, terrain_resolution)
            overlay = _overlay_for_rect(rect, clipped_dem.crs())
            clipped_buildings = []
            for name, layer, height, embed in building_items:
                output_layer = _clip_vector(layer, rect, clipped_dem.crs(), overlay)
                clipped_buildings.append((name, output_layer, height, embed))
            clipped_roads = _clip_vector(road_layer, rect, clipped_dem.crs(), overlay)
            package, manifest = original_export(
                self, staging, clipped_dem, clipped_buildings, clipped_roads,
                epsg, scale_denominator, terrain_resolution, *args, **kwargs)
            # Preserve the official Blender manifest shape while enriching it.
            manifest['export_mode'] = 'custom_extent'
            manifest['exporter_extension'] = EXTENSION_BUILD
            manifest['selection_crs'] = self._c3d_selected_crs.authid()
            metadata = {
                'schema_version': '1.0',
                'exporter_version': EXTENSION_BUILD,
                'export_mode': 'custom_extent',
                'source_crs': dem.crs().authid(),
                'selection_crs': self._c3d_selected_crs.authid(),
                'export_crs': clipped_dem.crs().authid(),
                'extent': {
                    'xmin': rect.xMinimum(), 'ymin': rect.yMinimum(),
                    'xmax': rect.xMaximum(), 'ymax': rect.yMaximum(),
                },
                'origin': manifest.get('origin', {}),
                'terrain': {
                    'grid_spacing_m': float(terrain_resolution),
                    'source_pixel_size_m': list(source_pixels),
                    'interpolation_raster_pixel_size_m': list(interpolation_pixels),
                    'estimated_grid_vertices': verts,
                    'estimated_grid_shape': [nx, ny],
                    'interpolation': 'GDAL bilinear crop + QGIS terrain sampling',
                    'is_source_resolution_increased': False,
                },
                'geometry': {
                    'building_export': True,
                    'road_export': clipped_roads is not None,
                    'boundary_clip': True,
                    'artificial_cut_walls_possible': True,
                },
                'simulation_ready': False,
                'notes': 'Bounding walls of clipped buildings are artificial. Not yet suitable for thermal flux calculations without surface classification and boundary handling.'
            }
            # Keep the Blender manifest schema and relative file names unchanged.
            # Metadata remains a separate optional, analysis-oriented record.
            manifest.setdefault('files', {})['Campus3D_Metadata'] = 'campus3d_metadata.json'
            destination = _save_numbered_package(
                package, out_folder, rect, terrain_resolution, manifest, metadata)
            # Close QGIS/GDAL file handles before Windows removes the workspace.
            clipped_buildings.clear()
            clipped_roads = None
            overlay = None
            clipped_dem = None
            gc.collect()
        return destination, manifest

    def new_close(self, event):
        try:
            _return_to_previous_map_tool(self)
            if getattr(self, '_c3d_outline', None) is not None:
                self._c3d_outline.hide()
                self._c3d_outline.reset(Qgis.GeometryType.Polygon)
        finally:
            original_close(self, event)

    dialog_type.__init__ = new_init
    dialog_type._c3d_active_extent = _active_extent
    dialog_type._terrain_grid_dimensions = new_grid
    dialog_type.update_terrain_estimate = new_estimate
    dialog_type.export_blender_package = new_export
    dialog_type.closeEvent = new_close
    dialog_type._campus3d_local_extent_installed = True
