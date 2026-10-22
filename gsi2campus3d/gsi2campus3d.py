import os, glob, traceback, re, zipfile, math, json, shutil
import tempfile
from pathlib import Path
from qgis.PyQt.QtCore import QSettings, QSize, QTimer
from qgis.PyQt.QtGui import QKeySequence, QIcon
from qgis.PyQt.QtWidgets import (QAction,QDialog,QVBoxLayout,QHBoxLayout,QLabel,QLineEdit,QPushButton,
    QFileDialog,QComboBox,QCheckBox,QDoubleSpinBox,QMessageBox,QGroupBox,QFormLayout,QProgressBar,QApplication,QScrollArea,QWidget)
from qgis.core import (QgsProject,QgsVectorLayer,QgsRasterLayer,QgsCoordinateReferenceSystem,
    QgsRectangle,QgsMessageLog,Qgis,QgsPointXY,QgsWkbTypes,QgsMapSettings,QgsMapRendererParallelJob)
import processing

ZONES=[(i,6668+i) for i in range(1,20)]

# 平面直角座標系の適用区域（国土地理院の告示に基づく簡略表示）。
# XI～XIIIは北海道内の地域区分、XIV/XVIII/XIXは東京都島しょ部、
# XV～XVIIは沖縄県内の地域区分です。
ZONE_AREAS={
    1:'長崎県・鹿児島県の一部（離島）',
    2:'福岡県・佐賀県・熊本県・大分県・宮崎県・鹿児島県',
    3:'山口県・島根県・広島県',
    4:'香川県・愛媛県・徳島県・高知県',
    5:'兵庫県・鳥取県・岡山県',
    6:'京都府・大阪府・福井県・滋賀県・三重県・奈良県・和歌山県',
    7:'石川県・富山県・岐阜県・愛知県',
    8:'新潟県・長野県・山梨県・静岡県',
    9:'東京都・福島県・栃木県・茨城県・埼玉県・千葉県・群馬県・神奈川県',
    10:'青森県・秋田県・山形県・岩手県・宮城県',
    11:'北海道（道南・後志・渡島・檜山など）',
    12:'北海道（XI・XIII系以外の地域）',
    13:'北海道（道東：十勝・釧路・根室・オホーツク東部など）',
    14:'東京都（南方島しょ部の一部）',
    15:'沖縄県（沖縄本島周辺など）',
    16:'沖縄県（西側：八重山方面など）',
    17:'沖縄県（東側：大東諸島方面など）',
    18:'東京都（南方島しょ部・西側の一部）',
    19:'東京都（南方島しょ部・東側の一部）',
}

class GSI2Campus3D:
    def __init__(self,iface):
        self.iface=iface; self.action=None; self.dialog=None
    def initGui(self):
        icon_path=os.path.join(os.path.dirname(__file__),'icon.svg')
        self.action=QAction(QIcon(icon_path),'Campus3D [A] — GSI2Campus3D',self.iface.mainWindow())
        self.action.setToolTip('Campus3D Workflow [A] — GSI2Campus3D')
        self.action.setStatusTip('Campus3D [A]: GSI基盤地図情報 → QGIS 3D → Blender連携パッケージ')
        self.action.triggered.connect(self.run)
        self.iface.addPluginToMenu('&GSI2Campus3D',self.action)
        self.iface.addToolBarIcon(self.action)
    def unload(self):
        if self.dialog is not None:
            try: self.dialog.close()
            except Exception: pass
        if self.action:
            self.iface.removePluginMenu('&GSI2Campus3D',self.action); self.iface.removeToolBarIcon(self.action)
    def run(self):
        # Non-modal: keep this panel open while the user inspects/adjusts the QGIS 3D view.
        if self.dialog is None:
            self.dialog=MainDialog(self.iface)
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()

class NoWheelDoubleSpinBox(QDoubleSpinBox):
    """QDoubleSpinBox that never changes value with the mouse wheel.

    Ignoring the wheel event lets the enclosing QScrollArea handle the scroll
    instead, preventing accidental parameter changes while users scroll through
    the long Campus3D panel.
    """
    def wheelEvent(self,event):
        event.ignore()

class NoWheelComboBox(QComboBox):
    """QComboBox that never changes selection with the mouse wheel."""
    def wheelEvent(self,event):
        event.ignore()

class MainDialog(QDialog):
    def __init__(self,iface):
        self.iface=iface
        super().__init__(iface.mainWindow()); self.setWindowTitle('Campus3D Workflow [A] — GSI2Campus3D v0.4.18'); self.resize(880,740)

        # Outer layout + vertically scrollable content.
        # This keeps the branded full UI usable on 1920x1080 and smaller screens
        # without hiding the lower Blender-export controls.
        outer=QVBoxLayout(self)
        outer.setContentsMargins(0,0,0,0)
        outer.setSpacing(0)

        self.main_scroll=QScrollArea(self)
        self.main_scroll.setWidgetResizable(True)
        self.main_scroll.setStyleSheet('QScrollArea { border: none; background: transparent; }')

        scroll_content=QWidget()
        root=QVBoxLayout(scroll_content)
        root.setContentsMargins(6,6,6,6)
        root.setSpacing(5)

        self.main_scroll.setWidget(scroll_content)
        outer.addWidget(self.main_scroll)

        brand=QLabel(
            '<div style="line-height:1.15;">'
            '<span style="font-size:18pt; font-weight:700; color:#ffffff;">Campus3D Workflow</span>'
            '&nbsp;&nbsp;<span style="font-size:12pt; font-weight:700; color:#d9bd78;">[A]</span>'
            '&nbsp;&nbsp;<span style="font-size:12pt; font-weight:700; color:#ffffff;">GSI2Campus3D</span><br>'
            '<span style="font-size:9pt; color:#d7dde2;">GSI data preparation for Blender</span>'
            '&nbsp;&nbsp;|&nbsp;&nbsp;'
            '<span style="font-size:8.5pt; color:#b8c0c7;">Est. 2026 | Powered by IINO Lab. | Plugin v0.4.18</span><br>'
            '<span style="font-size:8.8pt; font-weight:600; color:#e8ecef;">[A] QGIS  →  [B] Blender  →  GLB  →  CampusMint</span>'
            '</div>'
        )
        brand.setMinimumHeight(84)
        brand.setMaximumHeight(92)
        brand.setStyleSheet(
            'QLabel {'
            'background: qlineargradient(x1:0,y1:0,x2:1,y2:0, '
            'stop:0 #211d1a, stop:0.48 #303236, stop:1 #1c2930);'
            'border: 1px solid #4a4f53; border-radius: 10px;'
            'padding: 10px 14px;'
            '}'
        )
        root.addWidget(brand)

        reset_row=QHBoxLayout()
        reset_row.addStretch(1)
        self.reset_defaults_btn=QPushButton('↺ 設定値をデフォルトに戻す（フォルダは保持）')
        self.reset_defaults_btn.setToolTip('座標系・建物高さ・Blender出力設定などを初期値へ戻します。入力/出力フォルダは変更しません。')
        self.reset_defaults_btn.clicked.connect(self.reset_parameters_to_defaults)
        reset_row.addWidget(self.reset_defaults_btn)
        root.addLayout(reset_row)

        inp=QGroupBox('[A-1] GSI / DEM 入力'); f=QFormLayout(inp)
        self.root_edit=QLineEdit(); b=QPushButton('参照…'); b.clicked.connect(self.pick_root)
        r=QHBoxLayout(); r.addWidget(self.root_edit); r.addWidget(b); f.addRow('GSI解凍フォルダ（親フォルダ可）',r)
        self.dem_dir_edit=QLineEdit(); bd=QPushButton('参照…'); bd.clicked.connect(self.pick_dem_dir)
        r=QHBoxLayout(); r.addWidget(self.dem_dir_edit); r.addWidget(bd); f.addRow('DEM XMLフォルダ（親フォルダ可）',r)
        self.out_edit=QLineEdit(os.path.join(os.path.expanduser('~'),'Documents','GSI2Campus3D_output'))
        bo=QPushButton('参照…'); bo.clicked.connect(self.pick_out); r=QHBoxLayout(); r.addWidget(self.out_edit); r.addWidget(bo); f.addRow('出力フォルダ',r)
        root.addWidget(inp)
        cb=QGroupBox('平面直角座標系'); cf=QFormLayout(cb); self.zone=NoWheelComboBox()
        for z,e in ZONES:
            area=ZONE_AREAS.get(z,'')
            self.zone.addItem(f'第 {z} 系  —  {area}  —  EPSG:{e}',(z,e))
        self.zone.setCurrentIndex(7); cf.addRow('系',self.zone); root.addWidget(cb)
        bb=QGroupBox('建物分類・3D'); bf=QFormLayout(bb)
        self.area=NoWheelDoubleSpinBox(); self.area.setRange(1,1000000); self.area.setValue(500); self.area.setSuffix(' m²'); bf.addRow('大規模判定面積',self.area)
        self.h_ns=self.spin(7); self.h_nl=self.spin(12); self.h_ss=self.spin(12); self.h_sl=self.spin(17)
        bf.addRow('普通建物・小規模',self.h_ns); bf.addRow('普通建物・大規模',self.h_nl); bf.addRow('堅ろう建物・小規模',self.h_ss); bf.addRow('堅ろう建物・大規模',self.h_sl)
        self.embed=NoWheelDoubleSpinBox(); self.embed.setRange(0,100); self.embed.setValue(5); self.embed.setSuffix(' m'); bf.addRow('地形への食い込み量',self.embed)
        self.clip=QCheckBox('DEM範囲で建物・道路を切り抜く'); self.clip.setChecked(True); bf.addRow(self.clip)
        self.cleanup=QCheckBox('前回生成したGSI2Campus3Dレイヤをプロジェクトから削除'); self.cleanup.setChecked(True); bf.addRow(self.cleanup)
        self.hide_sources=QCheckBox('処理後に元のGSIレイヤと入力DEMを非表示'); self.hide_sources.setChecked(True); bf.addRow(self.hide_sources)
        root.addWidget(bb)
        self.go=QPushButton('3Dモデル用データを作成'); self.go.clicked.connect(self.process); root.addWidget(self.go)
        self.progress=QProgressBar(); root.addWidget(self.progress)
        bx=QGroupBox('[A-2] Blender [B] へ渡す連携パッケージ'); bxf=QFormLayout(bx)
        self.blender_scale=NoWheelComboBox();
        for d in (1000,500,100,1): self.blender_scale.addItem(f'1:{d}' if d!=1 else '1:1（実寸）',d)
        self.blender_scale.setCurrentIndex(0); bxf.addRow('出力縮尺',self.blender_scale)
        self.blender_zscale=NoWheelDoubleSpinBox(); self.blender_zscale.setRange(0.1,20.0); self.blender_zscale.setDecimals(2); self.blender_zscale.setSingleStep(0.25); self.blender_zscale.setValue(1.0); self.blender_zscale.setSuffix(' ×'); bxf.addRow('Z方向倍率（地形・建物・道路）',self.blender_zscale)
        self.sync_3d_zscale=QCheckBox('QGIS 3Dビューの縦倍率と連動（推奨）'); self.sync_3d_zscale.setChecked(True)
        self.fetch_3d_zscale_btn=QPushButton('3Dビューから取得'); self.fetch_3d_zscale_btn.clicked.connect(lambda: self.sync_zscale_from_3d(False))
        zr=QHBoxLayout(); zr.addWidget(self.sync_3d_zscale); zr.addWidget(self.fetch_3d_zscale_btn)
        bxf.addRow('3D倍率連動',zr)
        self.terrain_res=NoWheelDoubleSpinBox(); self.terrain_res.setRange(32,1024); self.terrain_res.setDecimals(0); self.terrain_res.setSingleStep(32); self.terrain_res.setValue(256); bxf.addRow('Terrain解像度（長辺セル数）',self.terrain_res)
        self.coast_interp=QCheckBox('海岸線・NoData境界を半セル補間してTerrainを端まで延ばす'); self.coast_interp.setChecked(True); bxf.addRow(self.coast_interp)
        self.fill_sea=QCheckBox('NoDataを海面下で埋めて広いTerrainとして出力'); self.fill_sea.setChecked(False); bxf.addRow(self.fill_sea)
        self.sea_floor=self.spin(-5.0); self.sea_floor.setRange(-200.0,50.0); self.sea_floor.setSingleStep(1.0); bxf.addRow('NoData埋め標高（m）',self.sea_floor)
        self.export_texture=QCheckBox('Terrain画像をパッケージに同梱'); self.export_texture.setChecked(True); bxf.addRow(self.export_texture)
        self.texture_source=NoWheelComboBox()
        self.texture_refresh=QPushButton('更新'); self.texture_refresh.clicked.connect(self.refresh_texture_sources)
        rr=QHBoxLayout(); rr.addWidget(self.texture_source); rr.addWidget(self.texture_refresh); bxf.addRow('Terrain画像ソース',rr)
        self.texture_px=NoWheelDoubleSpinBox(); self.texture_px.setRange(512,8192); self.texture_px.setDecimals(0); self.texture_px.setSingleStep(512); self.texture_px.setValue(2048); self.texture_px.setSuffix(' px'); bxf.addRow('Terrain画像 長辺解像度',self.texture_px)
        self.export_blender_btn=QPushButton('現在の生成レイヤからBlenderパッケージを出力'); self.export_blender_btn.clicked.connect(self.export_current_blender_package); bxf.addRow(self.export_blender_btn)
        self.blender_progress=QProgressBar(); self.blender_progress.setRange(0,100); self.blender_progress.setValue(0); self.blender_progress.setFormat('%p%')
        self.blender_status=QLabel('待機中')
        bxf.addRow('Blender出力進行',self.blender_progress)
        bxf.addRow('処理状況',self.blender_status)
        bxf.addRow(QLabel('QGIS 3Dで縦倍率を調整すると、連動ON時はBlender倍率へ自動反映。'))
        root.addWidget(bx)
        root.addWidget(QLabel('v0.4.18: 数値・座標系などの設定をワンクリックで初期値へ戻すリセット機能を追加しました。'))
        root.addWidget(QLabel('3D確認: 表示 → 3Dマップビュー → 新規3Dマップビュー'))
        self._last_dem=None; self._last_buildings=[]; self._last_road=None; self._last_epsg=None; self._last_out=None
        self._last_3d_zscale_seen=None
        self.load_settings()
        self.refresh_texture_sources()

        # Poll the currently open QGIS 3D view while this non-modal dialog is open.
        # This keeps the Blender Z multiplier synchronized without requiring
        # another manual entry after the user finishes tuning the 3D view.
        self._zsync_timer=QTimer(self)
        self._zsync_timer.setInterval(600)
        self._zsync_timer.timeout.connect(lambda: self.sync_zscale_from_3d(True))
        self._zsync_timer.start()
    def reset_parameters_to_defaults(self):
        """Reset editable parameters to the shipped defaults, preserving paths."""
        reply=QMessageBox.question(
            self,
            'GSI2Campus3D',
            '座標系・数値・チェック項目などを初期値に戻します。\n\n'
            'GSI解凍フォルダ / DEM XMLフォルダ / 出力フォルダはそのまま保持します。\n'
            'よろしいですか？',
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return

        # Coordinate system: Plane Rectangular CS VIII (Niigata etc.)
        self.zone.setCurrentIndex(7)

        # Building classification / 3D
        self.area.setValue(500.0)
        self.h_ns.setValue(7.0)
        self.h_nl.setValue(12.0)
        self.h_ss.setValue(12.0)
        self.h_sl.setValue(17.0)
        self.embed.setValue(5.0)
        self.clip.setChecked(True)
        self.cleanup.setChecked(True)
        self.hide_sources.setChecked(True)

        # Blender package
        self.blender_scale.setCurrentIndex(0)   # 1:1000
        self.blender_zscale.setValue(1.0)
        self.sync_3d_zscale.setChecked(True)
        self.terrain_res.setValue(256.0)
        self.coast_interp.setChecked(True)
        self.fill_sea.setChecked(False)
        self.sea_floor.setValue(-5.0)
        self.export_texture.setChecked(True)
        self.texture_px.setValue(2048.0)

        # Terrain imagery source: GSI nationwide latest seamless photo.
        try:
            idx=self.texture_source.findData('GSI_SEAMLESS')
            if idx >= 0:
                self.texture_source.setCurrentIndex(idx)
        except Exception:
            pass
        self._saved_texture_source='GSI_SEAMLESS'

        # Reset visual progress/status only; generated layers/files are untouched.
        try:
            self.progress.setValue(0)
            self.blender_progress.setFormat('%p%')
            self.blender_progress.setValue(0)
            self.blender_status.setText('待機中')
        except Exception:
            pass

        self._last_3d_zscale_seen=None
        self.save_settings()

        # With live sync ON (the default), an already-open 3D view is authoritative.
        # Reflect it immediately so the UI does not briefly show a misleading 1.00.
        synced=False
        try:
            synced=self.sync_zscale_from_3d(True)
        except Exception:
            synced=False

        note = (
            '\n\n※3D倍率連動がONのため、Z方向倍率は現在開いているQGIS 3Dビューの値に'
            '自動同期されています。'
            if synced else ''
        )
        QMessageBox.information(
            self,
            'GSI2Campus3D',
            '設定値をデフォルトへ戻しました。\n'
            'フォルダ指定は保持しています。'
            + note
        )

    def load_settings(self):
        s=QSettings()
        p='GSI2Campus3D/'
        def val(key,default=None,typ=None):
            try:
                return s.value(p+key,default,type=typ) if typ else s.value(p+key,default)
            except Exception:
                return default
        self.root_edit.setText(val('root','',str) or '')
        self.dem_dir_edit.setText(val('dem_dir','',str) or '')
        self.out_edit.setText(val('out',self.out_edit.text(),str) or self.out_edit.text())
        zi=val('zone_index',7,int)
        if 0 <= zi < self.zone.count(): self.zone.setCurrentIndex(zi)
        self.area.setValue(val('area',500.0,float))
        self.h_ns.setValue(val('h_ns',7.0,float)); self.h_nl.setValue(val('h_nl',12.0,float))
        self.h_ss.setValue(val('h_ss',12.0,float)); self.h_sl.setValue(val('h_sl',17.0,float))
        self.embed.setValue(val('embed',5.0,float))
        self.clip.setChecked(val('clip',True,bool)); self.cleanup.setChecked(val('cleanup',True,bool))
        self.hide_sources.setChecked(val('hide_sources',True,bool));
        si=val('blender_scale_index',0,int)
        if 0 <= si < self.blender_scale.count(): self.blender_scale.setCurrentIndex(si)
        self.blender_zscale.setValue(val('blender_zscale',1.0,float))
        self.sync_3d_zscale.setChecked(val('sync_3d_zscale',True,bool))
        self.terrain_res.setValue(val('terrain_res',256.0,float))
        self.coast_interp.setChecked(val('coast_interp',True,bool))
        self.fill_sea.setChecked(val('fill_sea',False,bool))
        self.sea_floor.setValue(val('sea_floor',-5.0,float))
        self.export_texture.setChecked(val('export_texture',True,bool))
        self.texture_px.setValue(val('texture_px',2048.0,float))
        self._saved_texture_source=val('texture_source','GSI_SEAMLESS',str) or 'GSI_SEAMLESS'
        # v0.3.6以前の自動設定は、安定版の地理院シームレス写真へ移行
        if self._saved_texture_source in ('AUTO3D','AUTO'):
            self._saved_texture_source='GSI_SEAMLESS'

    def save_settings(self):
        s=QSettings(); p='GSI2Campus3D/'
        s.setValue(p+'root',self.root_edit.text().strip())
        s.setValue(p+'dem_dir',self.dem_dir_edit.text().strip())
        s.setValue(p+'out',self.out_edit.text().strip())
        s.setValue(p+'zone_index',self.zone.currentIndex())
        s.setValue(p+'area',self.area.value())
        s.setValue(p+'h_ns',self.h_ns.value()); s.setValue(p+'h_nl',self.h_nl.value())
        s.setValue(p+'h_ss',self.h_ss.value()); s.setValue(p+'h_sl',self.h_sl.value())
        s.setValue(p+'embed',self.embed.value())
        s.setValue(p+'clip',self.clip.isChecked()); s.setValue(p+'cleanup',self.cleanup.isChecked())
        s.setValue(p+'hide_sources',self.hide_sources.isChecked());
        s.setValue(p+'blender_scale_index',self.blender_scale.currentIndex())
        s.setValue(p+'blender_zscale',self.blender_zscale.value())
        s.setValue(p+'sync_3d_zscale',self.sync_3d_zscale.isChecked())
        s.setValue(p+'terrain_res',self.terrain_res.value())
        s.setValue(p+'coast_interp',self.coast_interp.isChecked())
        s.setValue(p+'fill_sea',self.fill_sea.isChecked())
        s.setValue(p+'sea_floor',self.sea_floor.value())
        s.setValue(p+'export_texture',self.export_texture.isChecked())
        s.setValue(p+'texture_px',self.texture_px.value())
        s.setValue(p+'texture_source', self.texture_source.currentData() or 'GSI_SEAMLESS')
        s.sync()

    def closeEvent(self,event):
        try:
            if hasattr(self,'_zsync_timer'):
                self._zsync_timer.stop()
        except Exception:
            pass
        self.save_settings()
        super().closeEvent(event)

    def refresh_texture_sources(self):
        saved=getattr(self,'_saved_texture_source',None)
        if saved is None:
            try: saved=self.texture_source.currentData()
            except Exception: saved='GSI_SEAMLESS'
        self.texture_source.blockSignals(True)
        self.texture_source.clear()
        self.texture_source.addItem('地理院 全国最新写真（シームレス・推奨）','GSI_SEAMLESS')
        self.texture_source.addItem('3DビューのTerrain表示から自動','AUTO3D')
        self.texture_source.addItem('プロジェクト全体から衛星・航空写真 / XYZを自動選択','AUTO')
        self.texture_source.addItem('現在の2Dマップ表示（ベクタも含む）','CANVAS')
        project=QgsProject.instance()
        candidates=[]
        for lyr in project.mapLayers().values():
            try:
                if not isinstance(lyr,QgsRasterLayer) or not lyr.isValid():
                    continue
                nm=lyr.name() or ''
                low=nm.lower()
                if low.startswith('dem_epsg') or 'geotiffの出力' in low or 'quickdem' in low:
                    continue
                provider=''
                try: provider=lyr.providerType()
                except Exception: pass
                candidates.append((nm,lyr.id(),provider))
            except Exception:
                pass
        for nm,lid,provider in sorted(candidates,key=lambda x:x[0].lower()):
            self.texture_source.addItem(f'{nm}  [{provider}]',lid)

        idx=self.texture_source.findData(saved)
        if idx<0:
            idx=self.texture_source.findData('GSI_SEAMLESS')
            if idx<0: idx=0
        self.texture_source.setCurrentIndex(idx)
        self.texture_source.blockSignals(False)

    def _gsi_seamless_photo_layer(self):
        """Create an in-memory XYZ layer for GSI nationwide latest seamless photo.

        Official tile:
        https://cyberjapandata.gsi.go.jp/xyz/seamlessphoto/{z}/{x}/{y}.jpg
        zoom 14-18, EPSG:3857.
        """
        url=('https://cyberjapandata.gsi.go.jp/xyz/seamlessphoto/'
             '%7Bz%7D/%7Bx%7D/%7By%7D.jpg')
        uri='type=xyz&url='+url+'&zmin=14&zmax=18&crs=EPSG3857'
        lyr=QgsRasterLayer(uri,'GSI 全国最新写真（シームレス）','wms')
        if not lyr.isValid():
            self.log('GSI seamless photo XYZ layer is invalid.')
            return None
        return lyr

    def _ordered_project_layers(self, layers):
        wanted={lyr.id():lyr for lyr in layers if lyr is not None}
        ordered=[]
        try:
            for lyr in QgsProject.instance().layerOrder():
                if lyr.id() in wanted:
                    ordered.append(wanted.pop(lyr.id()))
        except Exception:
            pass
        ordered.extend(wanted.values())
        return ordered

    def _raster_candidates(self,layers,dem):
        out=[]
        for lyr in layers:
            try:
                if not isinstance(lyr,QgsRasterLayer) or not lyr.isValid() or lyr.id()==dem.id():
                    continue
                nm=(lyr.name() or '').lower()
                if nm.startswith('dem_epsg') or 'geotiffの出力' in nm or 'quickdem' in nm:
                    continue
                out.append(lyr)
            except Exception:
                pass
        return out

    def _three_d_texture_candidates(self,dem):
        """Return raster candidates most likely used by the currently opened 3D terrain."""
        project=QgsProject.instance()
        theme=''
        layers=[]

        canvas3d=getattr(self,'_canvas3d',None)
        if canvas3d is not None:
            try:
                settings=canvas3d.mapSettings()
                if settings is not None:
                    theme=settings.terrainMapTheme() or ''
            except Exception:
                theme=''

        themes=project.mapThemeCollection()
        if theme and themes.hasMapTheme(theme):
            try:
                layers=self._ordered_project_layers(themes.mapThemeVisibleLayers(theme))
                self.log(f'3D Terrain map theme: {theme}')
            except Exception:
                layers=[]

        # If the 3D scene does not override a map theme, QGIS uses the current
        # project/map-theme state for the terrain texture. Prefer an active 2D
        # canvas theme if there is one.
        if not layers:
            try:
                canvas_theme=self.iface.mapCanvas().theme() or ''
            except Exception:
                canvas_theme=''
            if canvas_theme and themes.hasMapTheme(canvas_theme):
                try:
                    layers=self._ordered_project_layers(themes.mapThemeVisibleLayers(canvas_theme))
                    self.log(f'3D Terrain fallback map theme: {canvas_theme}')
                except Exception:
                    layers=[]

        # Finally inspect all project rasters. This still catches XYZ/WMS layers
        # even when they are hidden from the ordinary 2D canvas.
        if not layers:
            layers=list(project.mapLayers().values())

        return self._raster_candidates(layers,dem)

    def _texture_candidate_score(self,lyr):
        nm=(lyr.name() or '').lower()
        provider=''
        try: provider=(lyr.providerType() or '').lower()
        except Exception: pass
        score=0
        # Strong semantic hints
        for key,pts in (
            ('衛星',100),('航空',100),('写真',90),('オルソ',90),('ortho',90),
            ('satellite',100),('aerial',100),('imagery',90),('photo',70),
            ('シームレス',80),('seamless',80),('gsi',30),('地理院',30),
        ):
            if key in nm: score+=pts
        # XYZ tiles in QGIS are normally exposed through the wms provider.
        if provider in ('wms','xyz'): score+=45
        # Avoid map-style/topographic raster when an imagery layer is available.
        for key,pts in (('標準地図',-40),('淡色',-30),('地形図',-25),('topo',-25),('map',-10)):
            if key in nm: score+=pts
        return score

    def resolve_texture_layers(self,dem):
        mode=self.texture_source.currentData() if hasattr(self,'texture_source') else 'GSI_SEAMLESS'
        project=QgsProject.instance()

        if mode=='GSI_SEAMLESS':
            lyr=self._gsi_seamless_photo_layer()
            if lyr is not None:
                return [lyr],'GSI_SEAMLESS:全国最新写真'
            return [],'GSI_SEAMLESS:invalid'

        if mode=='CANVAS':
            try: src=list(self.iface.mapCanvas().mapSettings().layers())
            except Exception: src=[]
            layers=[]
            for lyr in src:
                try:
                    if lyr and lyr.isValid() and lyr.id()!=dem.id():
                        nm=(lyr.name() or '').lower()
                        if nm.startswith('dem_epsg') or 'geotiffの出力' in nm or 'quickdem' in nm:
                            continue
                        layers.append(lyr)
                except Exception:
                    pass
            return layers,'CANVAS'

        if mode and mode not in ('GSI_SEAMLESS','AUTO','AUTO3D','CANVAS'):
            lyr=project.mapLayer(mode)
            if lyr is not None and lyr.isValid():
                return [lyr],lyr.name()

        if mode=='AUTO3D':
            rasters=self._three_d_texture_candidates(dem)
            if rasters:
                best=max(rasters,key=self._texture_candidate_score)
                return [best],f'3D-AUTO:{best.name()}'
            # fall through to project-wide auto if necessary

        rasters=self._raster_candidates(list(project.mapLayers().values()),dem)
        if not rasters:
            return [],'AUTO:none'
        best=max(rasters,key=self._texture_candidate_score)
        return [best],f'AUTO:{best.name()}'

    def spin(self,v):
        s=NoWheelDoubleSpinBox(); s.setRange(0,10000); s.setDecimals(1); s.setValue(v); s.setSuffix(' m'); return s
    def pick_root(self):
        p=QFileDialog.getExistingDirectory(self,'GSIフォルダを選択');
        if p:
            self.root_edit.setText(p); self.save_settings()
    def pick_dem_dir(self):
        p=QFileDialog.getExistingDirectory(self,'DEM XMLフォルダを選択')
        if p:
            self.dem_dir_edit.setText(p); self.save_settings()

    def collect_dem_xmls(self, dem_dir, output_dir=None):
        """Collect only genuine GSI DEM source XML files recursively.

        Important:
        Output rasters created by QGIS/GDAL can leave helper files such as
        DEM_EPSG6677.tif.aux.xml. Because their names contain "DEM", older
        versions accidentally passed them to QuickDEM4JP when the project root
        was also used as the output folder.

        A source GSI DEM XML must match:
            ...-DEM<kind>-YYYYMMDD.xml
        e.g.
            FG-GML-5239-54-58-DEM5A-20250620.xml
            FG-GML-5239-55-85-DEM5B-20100115.xml
            ...-DEM10A-YYYYMMDD.xml

        If multiple DEM products cover the same tile, the higher-resolution /
        preferred product is selected. Unknown but syntactically valid DEM kinds
        are retained with lower priority.
        """
        candidates=(
            glob.glob(os.path.join(dem_dir,'**','*.xml'),recursive=True)
            + glob.glob(os.path.join(dem_dir,'**','*.XML'),recursive=True)
        )

        # Generic GSI DEM source naming pattern.
        # This deliberately rejects output sidecars such as *.tif.aux.xml.
        pat=re.compile(
            r'^(?P<tile>.+)-DEM(?P<kind>\d+[A-Z]?)-(?P<date>\d{8})\.XML$',
            re.IGNORECASE
        )

        # Lower score = preferred.
        priority={
            '1A':0, '1B':1,
            '5A':10, '5B':11, '5C':12,
            '10A':20, '10B':21,
        }

        parsed=[]
        ignored=[]
        seen=set()

        for p in candidates:
            ap=os.path.normcase(os.path.abspath(p))
            if ap in seen:
                continue
            seen.add(ap)

            # Ignore QuickDEM temporary extraction folders from old runs.
            parts=[x.lower() for x in Path(os.path.abspath(p)).parts]
            if any(part.endswith('_quickdem_input') for part in parts):
                continue

            base=os.path.basename(p)
            m=pat.match(base)
            if not m:
                # Log only DEM-looking files; ordinary ALL-layer XMLs are expected.
                if 'DEM' in base.upper():
                    ignored.append(p)
                continue

            parsed.append((p,m))

        chosen={}
        for p,m in parsed:
            tile=m.group('tile').upper()
            kind=m.group('kind').upper()
            date=m.group('date')

            rank=priority.get(kind,90)
            score=(rank,-int(date))
            old=chosen.get(tile)
            if old is None or score < old[0]:
                chosen[tile]=(score,p,kind,date)

        selected=sorted(v[1] for v in chosen.values())

        if parsed or ignored:
            counts={}
            for _,p,kind,_ in chosen.values():
                counts[kind]=counts.get(kind,0)+1
            summary=', '.join(f'DEM{k}={v}' for k,v in sorted(counts.items()))
            skipped=len(parsed)-len(selected)

            self.log(
                f'DEM auto selection: source={len(parsed)}, selected={len(selected)}, '
                f'skipped lower-priority duplicates={skipped}'
                + (f' ({summary})' if summary else '')
            )

            if ignored:
                self.log(
                    f'Ignored non-source DEM-like XML files: {len(ignored)} '
                    + ', '.join(os.path.basename(p) for p in ignored[:5])
                    + (' ...' if len(ignored)>5 else '')
                )

        return selected

    def xml_bytes_for_quickdem(self, path):
        """Return XML bytes suitable for QuickDEM4JP.

        Older GSI DEM products (for example some DEM5B from 2010) declare
        Shift_JIS. The ElementTree parser used inside QuickDEM4JP/convert_fgd_dem
        can reject these multi-byte encoded XML files and report
        "Incorrect XML file".

        Only the temporary copy written into QuickDEM's ZIP is transcoded.
        The user's original GSI XML is never changed.
        """
        raw=Path(path).read_bytes()

        # The XML declaration itself is ASCII-compatible in the encodings used
        # by old GSI files, so it is safe to inspect the first few hundred bytes.
        m=re.search(
            br'<\?xml[^>]*encoding\s*=\s*["\']\s*([^"\']+)\s*["\']',
            raw[:300],
            re.IGNORECASE
        )
        if not m:
            return raw, None

        declared=m.group(1).decode('ascii','ignore').strip()
        norm=declared.lower().replace('_','-')

        if norm in ('utf-8','utf8','us-ascii','ascii'):
            return raw, None

        if norm in ('shift-jis','shiftjis','sjis','ms-kanji','windows-31j','cp932'):
            candidates=['cp932','shift_jis']
        else:
            candidates=[declared]

        decoded=None
        used=None
        last_error=None
        for enc in candidates:
            try:
                decoded=raw.decode(enc)
                used=enc
                break
            except Exception as e:
                last_error=e

        if decoded is None:
            raise RuntimeError(
                f'XML文字コードをUTF-8へ変換できませんでした: {os.path.basename(path)} '
                f'(declared={declared}, error={last_error})'
            )

        decoded=re.sub(
            r'(<\?xml[^>]*encoding\s*=\s*["\'])\s*[^"\']+\s*(["\'])',
            r'\1UTF-8\2',
            decoded,
            count=1,
            flags=re.IGNORECASE
        )
        return decoded.encode('utf-8'), f'{declared}->{used}->UTF-8'

    def quickdem_batch(self, dem_dir, epsg, output_path):
        """Batch-convert all DEM XMLs in a folder using QuickDEM4JP.

        QuickDEM4JP's Processing INPUT is a file parameter in current releases,
        so passing a directory directly is rejected. To keep the UI folder-based,
        GSI2Campus3D packages all DEM XMLs into a temporary ZIP and passes that ZIP
        to QuickDEM4JP, which officially accepts ZIP files containing XMLs.
        """
        from qgis.core import QgsApplication
        alg_id='quickdemforjp:quickdemforjp'
        alg=QgsApplication.processingRegistry().algorithmById(alg_id)
        if alg is None:
            raise RuntimeError('QuickDEM4JP が見つかりません。QGISのプラグイン管理からQuickDEM4JPを有効化してください。')

        xmls=self.collect_dem_xmls(dem_dir, os.path.dirname(output_path))
        if not xmls:
            raise RuntimeError('DEM XMLフォルダ内にDEM XMLが見つかりません。')

        # Current QuickDEM4JP Processing input is file-based.
        # Create a brand-new system temporary directory for every run.
        #
        # Older versions created *_quickdem_input.zip beside the output file.
        # QuickDEM4JP extracts that ZIP into a sibling folder. On Windows/OneDrive
        # the folder can remain locked after a failed run, and deleting it can raise
        # WinError 5 (Access is denied). A unique temp directory removes any need to
        # reuse/delete the previous run's extraction directory before processing.
        temp_root=tempfile.mkdtemp(prefix='GSI2Campus3D_QuickDEM_')
        zip_path=os.path.join(temp_root,'quickdem_input.zip')
        extract_dir=os.path.splitext(zip_path)[0]

        converted_encodings={}
        with zipfile.ZipFile(zip_path,'w',compression=zipfile.ZIP_DEFLATED) as zf:
            for src in xmls:
                # Keep only the file name; GSI DEM tile names are unique within a download set.
                data,conversion=self.xml_bytes_for_quickdem(src)
                zf.writestr(os.path.basename(src),data)
                if conversion:
                    converted_encodings[conversion]=converted_encodings.get(conversion,0)+1

        if converted_encodings:
            detail=', '.join(f'{k}: {v} files' for k,v in sorted(converted_encodings.items()))
            self.log(f'QuickDEM XML encoding normalization: {detail}')
        self.log(f'QuickDEM4JP ZIP input: {zip_path} ({len(xmls)} XML files)')

        defs=list(alg.parameterDefinitions())
        params={}
        mapped={'input':None,'crs':None,'demout':None}
        debug=[]
        for d in defs:
            name=d.name(); desc=d.description() or ''
            text=(name+' '+desc).lower()
            debug.append(f'{name}: {desc}')
            if mapped['input'] is None and (('input' in text or '入力' in text) and ('dem' in text or 'xml' in text or 'zip' in text or 'data' in text or 'データ' in text)):
                params[name]=zip_path; mapped['input']=name; continue
            if mapped['crs'] is None and ('crs' in text or '座標' in text):
                params[name]=QgsCoordinateReferenceSystem(f'EPSG:{epsg}'); mapped['crs']=name; continue
            if mapped['demout'] is None and ('output' in text or '出力' in text) and ('dem' in text or 'geotiff' in text or 'geo tiff' in text) and not ('rgb' in text or 'terrain' in text):
                params[name]=output_path; mapped['demout']=name; continue

        names={d.name():d for d in defs}
        if mapped['input'] is None:
            for n in ('INPUT','INPUT_FILE','INPUT_DEM','DEM','DEM_INPUT'):
                if n in names: params[n]=zip_path; mapped['input']=n; break
        if mapped['crs'] is None:
            for n in ('CRS','OUTPUT_CRS','TARGET_CRS'):
                if n in names: params[n]=QgsCoordinateReferenceSystem(f'EPSG:{epsg}'); mapped['crs']=n; break
        if mapped['demout'] is None:
            for n in ('OUTPUT','OUTPUT_DEM','DEM_OUTPUT','OUTPUT_GEOTIFF'):
                if n in names: params[n]=output_path; mapped['demout']=n; break

        if not all(mapped.values()):
            raise RuntimeError('QuickDEM4JPのパラメータを自動判定できませんでした。\n'+'\n'.join(debug))

        self.log('QuickDEM4JP batch parameters: '+str(mapped))
        try:
            result=processing.run(alg_id,params)
        except Exception:
            # Best effort only. Even if Windows/OneDrive keeps a handle open,
            # the next run will use a different temp directory, so stale files
            # can never contaminate the next conversion.
            try:
                shutil.rmtree(temp_root, ignore_errors=True)
            except Exception:
                pass
            raise
        candidate=output_path
        if not os.path.exists(candidate):
            rv=result.get(mapped['demout']) if isinstance(result,dict) else None
            if isinstance(rv,str) and os.path.exists(rv): candidate=rv
        if not os.path.exists(candidate):
            raise RuntimeError('QuickDEM4JPの一括変換は完了しましたが、DEM GeoTIFFを確認できませんでした。')
        # Remove the whole per-run temp workspace if possible.
        # Failure to clean is non-fatal because the path is never reused.
        try:
            shutil.rmtree(temp_root, ignore_errors=True)
        except Exception:
            pass
        return candidate

    def unique_path(self,folder,stem,ext):
        p=os.path.join(folder,stem+ext)
        if not os.path.exists(p): return p
        i=1
        while True:
            p=os.path.join(folder,f'{stem}_{i:03d}{ext}')
            if not os.path.exists(p): return p
            i+=1
    def pick_out(self):
        p=QFileDialog.getExistingDirectory(self,'出力フォルダを選択');
        if p:
            self.out_edit.setText(p); self.save_settings()
    def log(self,msg): QgsMessageLog.logMessage(msg,'GSI2Campus3D',Qgis.Info)
    def find_files(self,root,token):
        out=[]
        for ext in ('xml','gml','XML','GML'):out+=glob.glob(os.path.join(root,f'**/*{token}*.{ext}'),recursive=True)
        return sorted(set(out))
    def load_union(self,paths,name):
        ls=[]
        for p in paths:
            x=QgsVectorLayer(p,os.path.basename(p),'ogr')
            if x.isValid():ls.append(x)
        if not ls:return None
        if len(ls)==1:return ls[0]
        x=processing.run('native:mergevectorlayers',{'LAYERS':ls,'CRS':None,'OUTPUT':'TEMPORARY_OUTPUT'})['OUTPUT']; x.setName(name); return x
    def reproj_vector(self,layer,epsg):
        x=processing.run('native:reprojectlayer',{'INPUT':layer,'TARGET_CRS':QgsCoordinateReferenceSystem(f'EPSG:{epsg}'),'OUTPUT':'TEMPORARY_OUTPUT'})['OUTPUT']
        fields=[f.name() for f in x.fields() if f.name().lower()!='fid']
        return processing.run('native:retainfields',{'INPUT':x,'FIELDS':fields,'OUTPUT':'TEMPORARY_OUTPUT'})['OUTPUT']
    def save(self,layer,path,name):
        base,ext=os.path.splitext(path)
        final=path if not os.path.exists(path) else self.unique_path(os.path.dirname(path),os.path.basename(base),ext)
        processing.run('native:savefeatures',{'INPUT':layer,'OUTPUT':final}); return QgsVectorLayer(final,name,'ogr')
    def clip_extent(self,layer,extent):
        return processing.run('native:extractbyextent',{'INPUT':layer,'EXTENT':extent,'CLIP':True,'OUTPUT':'TEMPORARY_OUTPUT'})['OUTPUT']

    def dem_valid_footprint(self, dem_path, epsg, out_folder):
        """Create a polygon footprint from the DEM valid-data mask.

        Clipping by the raster bounding rectangle is not enough when the DEM mosaic
        contains NoData gaps. Features over those gaps can be exported by QGIS 3D
        at a fallback elevation and appear as a second, detached city in Blender.
        """
        try:
            from osgeo import gdal, ogr, osr
            src=gdal.Open(dem_path, gdal.GA_ReadOnly)
            if src is None:
                raise RuntimeError('DEMをGDALで開けません。')
            band=src.GetRasterBand(1)
            mask=band.GetMaskBand()
            fp_path=self.unique_path(out_folder,f'DEM_EPSG{epsg}_valid_footprint','.gpkg')
            drv=ogr.GetDriverByName('GPKG')
            ods=drv.CreateDataSource(fp_path)
            srs=osr.SpatialReference(); srs.ImportFromEPSG(int(epsg))
            ol=ods.CreateLayer('valid_footprint',srs,ogr.wkbPolygon)
            ol.CreateField(ogr.FieldDefn('maskval',ogr.OFTInteger))
            gdal.Polygonize(mask,None,ol,0,[],callback=None)
            ol=None; ods=None; src=None
            raw=QgsVectorLayer(fp_path+'|layername=valid_footprint','DEM_valid_mask','ogr')
            if not raw.isValid():
                raise RuntimeError('DEM有効領域ポリゴンを読み込めません。')
            valid=processing.run('native:extractbyexpression',{
                'INPUT':raw,'EXPRESSION':'"maskval" > 0','OUTPUT':'TEMPORARY_OUTPUT'
            })['OUTPUT']
            # Dissolve all valid tiles into one multipart footprint.
            dissolved=processing.run('native:dissolve',{
                'INPUT':valid,'FIELD':[],'SEPARATE_DISJOINT':False,'OUTPUT':'TEMPORARY_OUTPUT'
            })['OUTPUT']
            self.log('DEM valid-data footprint created: '+fp_path)
            return dissolved
        except Exception as e:
            self.log('DEM valid footprint fallback to extent: '+str(e))
            self.log(traceback.format_exc())
            return None

    def clip_to_dem(self, layer, dem, footprint=None):
        """Clip a vector layer to DEM coverage and reject features on NoData.

        If a valid-data footprint is available we use a true polygon clip. For
        polygon buildings we additionally sample DEM at point-on-surface so a
        feature can never survive solely inside a NoData hole.
        """
        x=layer
        try:
            x=processing.run('native:fixgeometries',{'INPUT':x,'METHOD':1,'OUTPUT':'TEMPORARY_OUTPUT'})['OUTPUT']
        except Exception:
            pass
        if footprint is not None and footprint.isValid():
            x=processing.run('native:clip',{'INPUT':x,'OVERLAY':footprint,'OUTPUT':'TEMPORARY_OUTPUT'})['OUTPUT']
        else:
            x=self.clip_extent(x,dem.extent())
        return x

    def filter_buildings_with_valid_dem(self, layer, dem):
        """Keep only buildings whose interior point has a valid DEM sample."""
        from qgis.core import QgsFeature, QgsPointXY, QgsWkbTypes
        uri=f'{QgsWkbTypes.displayString(layer.wkbType())}?crs={layer.crs().authid()}'
        out=QgsVectorLayer(uri,layer.name()+'_demvalid','memory')
        pr=out.dataProvider(); pr.addAttributes(list(layer.fields())); out.updateFields()
        kept=0; dropped=0
        for f in layer.getFeatures():
            g=f.geometry()
            if g is None or g.isEmpty():
                dropped+=1; continue
            try:
                p=g.pointOnSurface().asPoint()
                value,ok=dem.dataProvider().sample(QgsPointXY(p),1)
                good=bool(ok) and value is not None and math.isfinite(float(value))
            except Exception:
                good=False
            if not good:
                dropped+=1; continue
            nf=QgsFeature(out.fields()); nf.setGeometry(g); nf.setAttributes(f.attributes()); pr.addFeature(nf); kept+=1
        out.updateExtents()
        self.log(f'DEM validity filter: kept={kept}, dropped={dropped}')
        return out
    def set3d(self,layer,height,embed):
        layer.setCustomProperty('gsi2campus3d/height',float(height)); layer.setCustomProperty('gsi2campus3d/embed',float(embed)); layer.setCustomProperty('gsi2campus3d/clamping','terrain')
        try:
            from qgis._3d import QgsVectorLayer3DRenderer,QgsPolygon3DSymbol
            sym=QgsPolygon3DSymbol(); sym.setExtrusionHeight(float(height)+float(embed)); sym.setOffset(-float(embed))
            try:
                sym.setAltitudeClamping(Qgis.AltitudeClamping.Terrain)
                sym.setAltitudeBinding(Qgis.AltitudeBinding.Centroid)
            except Exception: pass
            layer.setRenderer3D(QgsVectorLayer3DRenderer(sym)); layer.triggerRepaint()
        except Exception as e:self.log('3D auto setting skipped: '+str(e))

    def remove_previous_generated_layers(self,project,rootnode,out_folder):
        """Remove layers created by previous GSI2Campus3D runs from the project.

        Files on disk are intentionally kept. This avoids Windows file-lock/overwrite
        problems while preventing duplicate layers such as *_001 in the layer panel.
        """
        ids=set()
        group=rootnode.findGroup('GSI2Campus3D')
        if group:
            for node in group.findLayers():
                if node.layer(): ids.add(node.layerId())
        out_abs=os.path.abspath(out_folder)
        generated_name=re.compile(r'^(Building_(Normal|Solid)_(Small|Large)(?:_\d+)?)$|^(RdEdg_EPSG\d+(?:_\d+)?)$|^(DEM_EPSG\d+(?:_\d+)?)$')
        for lyr in project.mapLayers().values():
            try:
                marked=bool(lyr.customProperty('gsi2campus3d/generated',False))
            except Exception:
                marked=False
            src=(lyr.source() or '').split('|')[0]
            try:
                src_abs=os.path.abspath(src)
                in_output=(src_abs==out_abs or src_abs.startswith(out_abs+os.sep))
            except Exception:
                in_output=False
            if marked or (in_output and generated_name.match(lyr.name())):
                ids.add(lyr.id())
        if ids:
            project.removeMapLayers(list(ids))
        group=rootnode.findGroup('GSI2Campus3D')
        if group:
            rootnode.removeChildNode(group)

    def mark_generated(self,layer):
        try: layer.setCustomProperty('gsi2campus3d/generated',True)
        except Exception: pass

    def hide_source_layers(self,project,rootnode,input_root,dem_input_id):
        root_abs=os.path.abspath(input_root)
        for lyr in project.mapLayers().values():
            hide=False
            if lyr.id()==dem_input_id:
                hide=True
            else:
                src=(lyr.source() or '').split('|')[0]
                if src:
                    try:
                        src_abs=os.path.abspath(src)
                        hide=(src_abs==root_abs or src_abs.startswith(root_abs+os.sep))
                    except Exception:
                        pass
            if hide and not bool(lyr.customProperty('gsi2campus3d/generated',False)):
                node=rootnode.findLayer(lyr.id())
                if node: node.setItemVisibilityChecked(False)


    def _dem_sample(self, dem, x, y):
        try:
            value, ok = dem.dataProvider().sample(QgsPointXY(float(x), float(y)), 1)
            if not ok or value is None or not math.isfinite(float(value)):
                return None
            return float(value)
        except Exception:
            return None

    def _write_terrain_obj(self, dem, path, origin_x, origin_y, scale_denominator, vertical_scale=1.0, long_cells=256, interpolate_boundary=True, fill_nodata=False, sea_floor_z=-5.0):
        """Write Terrain OBJ using the DEM valid/NoData boundary.

        Older versions emitted a quad only when all 4 cell corners had valid DEM
        values. Along a coastline this eroded the mesh inward by roughly one grid
        cell. v0.3.0 clips two triangles per cell against the valid-data mask and,
        when enabled, inserts midpoint vertices on valid/NoData edges. This reaches
        approximately half a sampling cell closer to the true coastline.

        For very high coastline fidelity increase long_cells (e.g. 512).\n\n        If fill_nodata=True, the full DEM rectangle is emitted and NoData\n        samples are assigned sea_floor_z. A Blender water plane can then hide\n        the artificial sea-floor surface, while the land is never cut inward.
        """
        e=dem.extent(); w=e.width(); h=e.height()
        if w<=0 or h<=0: raise RuntimeError('DEMの範囲が不正です。')
        long_cells=max(8,int(long_cells))
        if w>=h:
            nx=long_cells+1; ny=max(2,int(round(long_cells*h/w))+1)
        else:
            ny=long_cells+1; nx=max(2,int(round(long_cells*w/h))+1)

        xs=[e.xMinimum()+w*i/(nx-1) for i in range(nx)]
        ys=[e.yMinimum()+h*j/(ny-1) for j in range(ny)]

        vals=[]; valid=[]; minz=None; original_valid_count=0
        for y in ys:
            row=[]; okrow=[]
            for x in xs:
                z=self._dem_sample(dem,x,y)
                if z is not None:
                    original_valid_count += 1
                    if minz is None or z<minz: minz=z
                    row.append(z); okrow.append(True)
                elif fill_nodata:
                    # Keep the same vertical datum as the real DEM.  The filled
                    # sea floor may therefore appear below model Z=0, which is
                    # intentional and convenient for a separate water plane.
                    row.append(float(sea_floor_z)); okrow.append(True)
                else:
                    row.append(None); okrow.append(False)
            vals.append(row); valid.append(okrow)

        if minz is None:
            raise RuntimeError('Terrain OBJ作成用の有効DEM値を取得できませんでした。')

        factor=1.0/float(scale_denominator)
        vertices=[]; uvs=[]; vmap={}

        def add_vertex(key,x,y,z):
            if key in vmap: return vmap[key]
            idx=len(vertices)+1
            X=(x-origin_x)*factor
            Y=(y-origin_y)*factor
            Z=(float(z)-minz)*factor*float(vertical_scale)
            vertices.append((X,Y,Z))
            uvs.append(((x-e.xMinimum())/w,(y-e.yMinimum())/h))
            vmap[key]=idx
            return idx

        def grid_vertex(i,j):
            if not valid[j][i]: return None
            return add_vertex(('g',i,j),xs[i],ys[j],vals[j][i])

        def midpoint_vertex(a,b):
            # a,b are (i,j). One must be valid, the other invalid.
            i1,j1=a; i2,j2=b
            edge=tuple(sorted((a,b)))
            key=('e',edge)
            if key in vmap: return vmap[key]
            x=(xs[i1]+xs[i2])*0.5
            y=(ys[j1]+ys[j2])*0.5
            if valid[j1][i1] and vals[j1][i1] is not None:
                z=vals[j1][i1]
            elif valid[j2][i2] and vals[j2][i2] is not None:
                z=vals[j2][i2]
            else:
                return None
            return add_vertex(key,x,y,z)

        faces=[]
        # Split each raster cell into two triangles. Clip each triangle against
        # the binary valid-data mask (Sutherland-Hodgman with midpoint crossings).
        tri_patterns=((0,1,2),(0,2,3))
        for j in range(ny-1):
            for i in range(nx-1):
                corners=((i,j),(i+1,j),(i+1,j+1),(i,j+1))
                for pat in tri_patterns:
                    tri=[corners[k] for k in pat]
                    inside=[valid[q[1]][q[0]] for q in tri]
                    ninside=sum(1 for x in inside if x)
                    if ninside==0: continue
                    if not interpolate_boundary and ninside<3: continue

                    poly=[]
                    for k in range(3):
                        a=tri[k]; b=tri[(k+1)%3]
                        ain=inside[k]; bin_=inside[(k+1)%3]
                        if ain:
                            vi=grid_vertex(*a)
                            if vi is not None: poly.append(vi)
                        if ain != bin_:
                            vi=midpoint_vertex(a,b)
                            if vi is not None: poly.append(vi)

                    # Remove adjacent duplicate indices.
                    cleaned=[]
                    for vi in poly:
                        if not cleaned or cleaned[-1]!=vi: cleaned.append(vi)
                    if len(cleaned)>1 and cleaned[0]==cleaned[-1]: cleaned.pop()
                    if len(cleaned)<3: continue

                    # Fan triangulation.
                    for k in range(1,len(cleaned)-1):
                        faces.append((cleaned[0],cleaned[k],cleaned[k+1]))

        with open(path,'w',encoding='utf-8',newline='\n') as f:
            f.write('# Campus3D Terrain OBJ\n')
            f.write('o Terrain\n')
            for X,Y,Z in vertices:
                f.write(f'v {X:.9f} {Y:.9f} {Z:.9f}\n')
            for u,v in uvs:
                f.write(f'vt {u:.9f} {v:.9f}\n')
            for a,b,c in faces:
                f.write(f'f {a}/{a} {b}/{b} {c}/{c}\n')

        cell_m=max(w/(nx-1),h/(ny-1))
        return {
            'min_z':minz,'nx':nx,'ny':ny,'vertices':len(vertices),'faces':len(faces),
            'model_width':w*factor,'model_height':h*factor,
            'sampling_cell_m':cell_m,'boundary_interpolation':bool(interpolate_boundary),
            'fill_nodata':bool(fill_nodata),'sea_floor_z_m':float(sea_floor_z),
            'original_valid_samples':int(original_valid_count),
            'sea_level_model_z':(0.0-minz)*factor*float(vertical_scale)
        }

    def _polygon_parts(self, geom):
        if geom is None or geom.isEmpty(): return []
        try:
            if QgsWkbTypes.isMultiType(geom.wkbType()): return geom.asMultiPolygon()
            p=geom.asPolygon(); return [p] if p else []
        except Exception:
            return []

    def _line_parts(self, geom):
        if geom is None or geom.isEmpty(): return []
        try:
            if QgsWkbTypes.isMultiType(geom.wkbType()): return geom.asMultiPolyline()
            p=geom.asPolyline(); return [p] if p else []
        except Exception:
            return []

    def _write_buildings_obj(self, layer, dem, path, object_name, height, embed, origin_x, origin_y, origin_z, scale_denominator, vertical_scale=1.0):
        factor=1.0/float(scale_denominator); idx=0; polys=0; skipped=0
        with open(path,'w',encoding='utf-8',newline='\n') as f:
            f.write(f'# Campus3D {object_name} OBJ\n')
            f.write(f'o {object_name}\n')
            for feat in layer.getFeatures():
                geom=feat.geometry()
                if not geom or geom.isEmpty(): continue
                cp=geom.centroid().asPoint(); base=self._dem_sample(dem,cp.x(),cp.y())
                if base is None: skipped+=1; continue
                for poly in self._polygon_parts(geom):
                    if not poly or not poly[0]: continue
                    ring=list(poly[0])
                    if len(ring)>1 and ring[0]==ring[-1]: ring=ring[:-1]
                    if len(ring)<3: continue
                    bottom=[]; top=[]
                    for p in ring:
                        X=(p.x()-origin_x)*factor; Y=(p.y()-origin_y)*factor
                        Z=(base-origin_z)*factor*float(vertical_scale) - float(embed)*factor
                        idx+=1; bottom.append(idx); f.write(f'v {X:.9f} {Y:.9f} {Z:.9f}\n')
                    for p in ring:
                        X=(p.x()-origin_x)*factor; Y=(p.y()-origin_y)*factor
                        Z=(base-origin_z)*factor*float(vertical_scale) + float(height)*factor
                        idx+=1; top.append(idx); f.write(f'v {X:.9f} {Y:.9f} {Z:.9f}\n')
                    # bottom/top n-gons and wall quads
                    f.write('f '+' '.join(map(str,reversed(bottom)))+'\n')
                    f.write('f '+' '.join(map(str,top))+'\n')
                    n=len(ring)
                    for k in range(n):
                        k2=(k+1)%n
                        f.write(f'f {bottom[k]} {bottom[k2]} {top[k2]} {top[k]}\n')
                    polys+=1
        return {
            'polygons':polys,'vertices':idx,'skipped':skipped,
            'height_m':float(height),'embed_m':float(embed),
            'building_extrusion_vertical_scale':1.0
        }

    def _write_roads_obj(self, layer, dem, path, origin_x, origin_y, origin_z, scale_denominator, vertical_scale=1.0):
        factor=1.0/float(scale_denominator); idx=0; lines=0
        with open(path,'w',encoding='utf-8',newline='\n') as f:
            f.write('# Campus3D Roads OBJ\n'); f.write('o Roads\n')
            for feat in layer.getFeatures():
                for line in self._line_parts(feat.geometry()):
                    inds=[]
                    for p in line:
                        z=self._dem_sample(dem,p.x(),p.y())
                        if z is None: continue
                        X=(p.x()-origin_x)*factor; Y=(p.y()-origin_y)*factor; Z=(z-origin_z)*factor*float(vertical_scale)+0.00005
                        idx+=1; inds.append(idx); f.write(f'v {X:.9f} {Y:.9f} {Z:.9f}\n')
                    if len(inds)>=2:
                        f.write('l '+' '.join(map(str,inds))+'\n'); lines+=1
        return {'lines':lines,'vertices':idx}

    def export_terrain_texture(self, dem, pkg, long_px=2048):
        """Render the selected Terrain image source over the DEM extent.

        Default AUTO mode selects one raster/XYZ layer from the whole project,
        preferring satellite/aerial/orthophoto-like names and WMS/XYZ providers.
        This avoids the previous behavior where visible GSI vector layers were
        baked instead of the 3D satellite imagery.
        """
        path=os.path.join(pkg,'Terrain_Texture.png')
        # Never leave a stale image from an earlier export. If this run fails,
        # the file must be absent so Blender/Explorer cannot mistake old output
        # for the new package.
        try:
            if os.path.exists(path):
                os.remove(path)
                self.log('Removed stale Terrain_Texture.png before regeneration.')
        except Exception as e:
            self.log('Could not remove stale Terrain_Texture.png: '+str(e))

        layers,source_desc=self.resolve_texture_layers(dem)
        diagnostics=[]
        for lyr in layers:
            try:
                provider=lyr.providerType()
            except Exception:
                provider=''
            diagnostics.append(f'{lyr.name()} [{lyr.__class__.__name__}/{provider}]')

        self.log('Terrain texture source: ' + source_desc)
        self.log('Terrain texture layers: ' + ('; '.join(diagnostics) if diagnostics else '(none)'))

        if not layers:
            self.log('Terrain texture export skipped: no suitable raster/XYZ source found.')
            return None, []

        e=dem.extent(); w=e.width(); h=e.height()
        long_px=max(512,int(long_px))
        if w>=h:
            pxw=long_px; pxh=max(64,int(round(long_px*h/w)))
        else:
            pxh=long_px; pxw=max(64,int(round(long_px*w/h)))

        ms=QgsMapSettings()
        ms.setLayers(layers)
        ms.setDestinationCrs(dem.crs())
        ms.setExtent(e)
        ms.setOutputSize(QSize(pxw,pxh))
        ms.setOutputDpi(96)
        try:
            ms.setTransformContext(QgsProject.instance().transformContext())
        except Exception:
            pass

        # White background is useful only for vector/canvas mode.  A selected
        # imagery layer should cover the extent itself.
        try:
            cms=self.iface.mapCanvas().mapSettings()
            ms.setBackgroundColor(cms.backgroundColor())
            ms.setFlag(QgsMapSettings.Antialiasing, True)
        except Exception:
            pass

        job=QgsMapRendererParallelJob(ms)
        job.start()
        job.waitForFinished()
        img=job.renderedImage()
        if img is None or img.isNull():
            self.log('Terrain texture export failed: rendered image is null.')
            return None,[x.name() for x in layers]

        ok=img.save(path,'PNG')
        if not ok or not os.path.exists(path):
            self.log('Terrain texture export failed: PNG save did not create the file.')
            return None,[x.name() for x in layers]

        self.log(f"Terrain texture exported: {path} ({pxw}x{pxh}) source={source_desc}")
        return path,[x.name() for x in layers]

    def set_blender_progress(self, value, status=''):
        """Update Blender-export progress and keep the QGIS UI responsive."""
        try:
            value=max(0,min(100,int(round(value))))
            if hasattr(self,'blender_progress'):
                self.blender_progress.setFormat('%p%')
                self.blender_progress.setValue(value)
            if status and hasattr(self,'blender_status'):
                self.blender_status.setText(status)
            QApplication.processEvents()
        except Exception:
            pass

    def export_blender_package(self, out_folder, dem, building_items, road_layer, epsg, scale_denominator, terrain_resolution, vertical_scale=1.0, interpolate_boundary=True, fill_nodata=False, sea_floor_z=-5.0, export_texture=True, texture_px=2048):
        # Progress is stage-based rather than elapsed-time based. It is intended
        # to reassure users that export is actively advancing through the package.
        self.set_blender_progress(3,'出力フォルダを準備中…')
        pkg=os.path.join(out_folder,'Campus3D_Blender')
        os.makedirs(pkg,exist_ok=True)
        e=dem.extent(); origin_x=(e.xMinimum()+e.xMaximum())/2.0; origin_y=(e.yMinimum()+e.yMaximum())/2.0

        self.set_blender_progress(8,'Terrain OBJを作成中…')
        terrain_path=os.path.join(pkg,'Terrain.obj')
        tinfo=self._write_terrain_obj(
            dem,terrain_path,origin_x,origin_y,scale_denominator,vertical_scale,
            terrain_resolution,interpolate_boundary,fill_nodata,sea_floor_z
        )
        self.set_blender_progress(28,'Terrain OBJ 完了')
        origin_z=tinfo['min_z']
        files={'Terrain':'Terrain.obj'}; stats={'Terrain':tinfo}

        # Four building categories. Each completed category advances the bar.
        build_start=30
        build_end=72
        count=max(1,len(building_items))
        for i,(name,layer,height,embed) in enumerate(building_items):
            p0=build_start+(build_end-build_start)*i/count
            p1=build_start+(build_end-build_start)*(i+1)/count
            self.set_blender_progress(p0,f'建物OBJを作成中… {i+1}/{len(building_items)}  {name}')
            fn=name+'.obj'; p=os.path.join(pkg,fn)
            stats[name]=self._write_buildings_obj(
                layer,dem,p,name,height,embed,origin_x,origin_y,origin_z,
                scale_denominator,vertical_scale
            )
            files[name]=fn
            self.set_blender_progress(p1,f'建物OBJ 完了 {i+1}/{len(building_items)}')

        if road_layer is not None and road_layer.isValid():
            self.set_blender_progress(75,'道路OBJを作成中…')
            stats['Roads']=self._write_roads_obj(
                road_layer,dem,os.path.join(pkg,'Roads.obj'),
                origin_x,origin_y,origin_z,scale_denominator,vertical_scale
            )
            files['Roads']='Roads.obj'
            self.set_blender_progress(82,'道路OBJ 完了')
        else:
            self.set_blender_progress(82,'道路データなし — スキップ')

        texture_layers=[]
        if export_texture:
            self.set_blender_progress(85,'Terrain画像を生成中…')
            tex_path,texture_layers=self.export_terrain_texture(dem,pkg,texture_px)
            if tex_path:
                files['Terrain_Texture']=os.path.basename(tex_path)
                stats['Terrain_Texture']={'layers':texture_layers,'long_side_px':int(texture_px)}
            self.set_blender_progress(96,'Terrain画像処理 完了')
        else:
            self.set_blender_progress(96,'Terrain画像出力OFF — スキップ')

        self.set_blender_progress(98,'manifestを書き出し中…')
        manifest={
            'format':'Campus3D Blender Package','version':'1.1','crs':f'EPSG:{epsg}',
            'scale_denominator':int(scale_denominator),'vertical_scale':float(vertical_scale),'building_vertical_mode':'terrain_exaggeration_only','origin':{'x':origin_x,'y':origin_y,'z':origin_z},
            'extent':{'xmin':e.xMinimum(),'xmax':e.xMaximum(),'ymin':e.yMinimum(),'ymax':e.yMaximum()},
            'real_size_m':{'x':e.width(),'y':e.height()},
            'model_size':{'x':e.width()/float(scale_denominator),'y':e.height()/float(scale_denominator),'z_multiplier':float(vertical_scale)},
            'files':files,'stats':stats,
            'notes':'OBJ files share one local origin and horizontal scale. vertical_scale exaggerates terrain/elevation only, matching QGIS 3D view behavior; building extrusion height and embed depth remain at true model scale. Terrain contains normalized UV coordinates. A visible QGIS background raster/XYZ texture may be embedded as Terrain_Texture.png. Terrain NoData boundary may be midpoint-interpolated for better coastline fit. Optionally NoData is filled at a user-specified sea-floor elevation to create a broad rectangular terrain for a separate Blender water plane.'
        }
        with open(os.path.join(pkg,'campus3d_manifest.json'),'w',encoding='utf-8') as fh:
            json.dump(manifest,fh,ensure_ascii=False,indent=2)

        self.set_blender_progress(100,'Blenderパッケージ出力完了')
        return pkg,manifest

    def _current_3d_canvas(self):
        """Return the most relevant currently open QGIS 3D canvas."""
        try:
            canvases=list(self.iface.mapCanvases3D())
        except Exception:
            canvases=[]

        if not canvases:
            return None

        # Prefer a visible canvas. If several are open, use the last visible one;
        # this normally corresponds to the most recently created/used 3D view.
        visible=[]
        for c in canvases:
            try:
                if c.isVisible():
                    visible.append(c)
                    continue
            except Exception:
                pass
            try:
                w=c.window()
                if w is not None and w.isVisible():
                    visible.append(c)
            except Exception:
                pass

        return visible[-1] if visible else canvases[-1]

    def read_3d_vertical_scale(self):
        """Read the terrain vertical scale from the currently open QGIS 3D view."""
        canvas=self._current_3d_canvas()
        if canvas is None:
            return None

        try:
            settings=canvas.mapSettings()
            if settings is None:
                return None
            terrain=settings.terrainSettings()
            if terrain is None:
                return None

            value=float(terrain.verticalScale())
            if not math.isfinite(value) or value <= 0:
                return None
            return value
        except Exception:
            return None

    def sync_zscale_from_3d(self, silent=True):
        """Copy QGIS 3D terrain vertical scale into Blender export Z multiplier.

        Returns True when a usable value was obtained. When `silent` is True this
        is called by the timer and produces no dialogs/log spam.
        """
        if not hasattr(self,'sync_3d_zscale'):
            return False

        # A manual "3Dビューから取得" press should work even when live sync is off.
        if silent and not self.sync_3d_zscale.isChecked():
            return False

        value=self.read_3d_vertical_scale()
        if value is None:
            if not silent:
                QMessageBox.information(
                    self,'GSI2Campus3D',
                    '開いているQGIS 3Dビューから縦倍率を取得できませんでした。\n'
                    '3Dビューを開いてからもう一度お試しください。\n\n'
                    '取得できない場合は、現在のZ方向倍率をそのまま手入力で使用できます。'
                )
            return False

        # Clamp only to the current UI range. In normal QGIS use this is 0.1–20.
        value=max(self.blender_zscale.minimum(),min(self.blender_zscale.maximum(),value))
        old=float(self.blender_zscale.value())

        if abs(old-value) > 0.0001:
            self.blender_zscale.setValue(value)

        changed=(self._last_3d_zscale_seen is None or abs(float(self._last_3d_zscale_seen)-value)>0.0001)
        self._last_3d_zscale_seen=value

        if not silent:
            self.log(f'QGIS 3D view vertical scale -> Blender Z scale: {value:.2f}x')
            QMessageBox.information(
                self,'GSI2Campus3D',
                f'QGIS 3Dビューの縦倍率 {value:.2f}× をBlender出力倍率へ反映しました。'
            )
        elif changed:
            # One concise log entry only when the value actually changes.
            self.log(f'3D Z-scale sync: {value:.2f}x')

        return True

    def export_current_blender_package(self):
        current_source=self.texture_source.currentData() if hasattr(self,'texture_source') else 'GSI_SEAMLESS'
        self._saved_texture_source=current_source
        self.refresh_texture_sources()
        idx=self.texture_source.findData(current_source)
        if idx>=0: self.texture_source.setCurrentIndex(idx)
        """Export a Blender handoff package after the user has inspected QGIS 3D.

        This intentionally does NOT run as part of the main data-preparation step.
        The user can first inspect the generated terrain/buildings in QGIS 3D,
        decide on a vertical exaggeration, then export the package.
        """
        self.export_blender_btn.setEnabled(False)
        self.set_blender_progress(0,'Blenderパッケージ出力を開始します…')
        try:
            project=QgsProject.instance()
            out=self.out_edit.text().strip()
            if not out:
                raise RuntimeError('出力フォルダを指定してください。')
            os.makedirs(out,exist_ok=True)
            z,epsg=self.zone.currentData()

            dem=self._last_dem if self._last_dem is not None and self._last_dem.isValid() else None
            if dem is None:
                cands=project.mapLayersByName(f'DEM_EPSG{epsg}')
                dem=cands[0] if cands else None
            if dem is None or not dem.isValid():
                raise RuntimeError('生成済みDEMが見つかりません。先に［3Dモデル用データを作成］を実行してください。')

            specs=[
                ('Building_Normal_Small',self.h_ns.value()),
                ('Building_Normal_Large',self.h_nl.value()),
                ('Building_Solid_Small',self.h_ss.value()),
                ('Building_Solid_Large',self.h_sl.value()),
            ]
            building_items=[]
            for name,h in specs:
                lyr=None
                if self._last_buildings:
                    for item in self._last_buildings:
                        if item[0]==name and item[1] is not None and item[1].isValid():
                            lyr=item[1]; break
                if lyr is None:
                    cands=project.mapLayersByName(name)
                    lyr=cands[0] if cands else None
                if lyr is None or not lyr.isValid():
                    raise RuntimeError(f'{name} が見つかりません。先にデータ生成を実行してください。')
                building_items.append((name,lyr,h,self.embed.value()))

            road=self._last_road if self._last_road is not None and self._last_road.isValid() else None
            if road is None:
                cands=project.mapLayersByName(f'RdEdg_EPSG{epsg}')
                road=cands[0] if cands else None

            scale_den=int(self.blender_scale.currentData())

            # Final authoritative sync at the moment of export. This prevents a
            # forgotten manual re-entry even if the timer has not fired since the
            # last 3D-view adjustment.
            used_3d_scale=False
            if self.sync_3d_zscale.isChecked():
                used_3d_scale=self.sync_zscale_from_3d(True)

            zscale=float(self.blender_zscale.value())
            self.save_settings()
            pkg,manifest=self.export_blender_package(
                out,dem,building_items,road,epsg,scale_den,int(self.terrain_res.value()),zscale,
                self.coast_interp.isChecked(),self.fill_sea.isChecked(),self.sea_floor.value(),
                self.export_texture.isChecked(),int(self.texture_px.value())
            )
            QMessageBox.information(
                self,'GSI2Campus3D',
                f'Blender連携パッケージを出力しました。\n\n{pkg}\n\n'
                f'水平縮尺: 1:{scale_den}\nZ方向倍率: {zscale:.2f}×' + ('（QGIS 3Dビューから取得）' if used_3d_scale else '（手入力値）') + '\n\n'
                'Terrain / Roads / 建物4分類は別OBJです。\n'
                f"Terrainサンプリング間隔: 約{manifest['stats']['Terrain'].get('sampling_cell_m',0):.1f} m\n"
                f"NoData海側拡張: {'ON' if manifest['stats']['Terrain'].get('fill_nodata') else 'OFF'}"
                + (f"（{manifest['stats']['Terrain'].get('sea_floor_z_m',0):.1f} m）\n" if manifest['stats']['Terrain'].get('fill_nodata') else "\n")
                + (
                    f"Terrain画像: {manifest['files'].get('Terrain_Texture')} / ソース: {self.texture_source.currentText()}\n"
                    if self.export_texture.isChecked() and manifest['files'].get('Terrain_Texture')
                    else ("Terrain画像: 今回は作成されませんでした（古いPNGも削除済み。画像ソース選択またはログ「Terrain texture source」を確認）\n"
                          if self.export_texture.isChecked()
                          else "Terrain画像: 出力OFF\n")
                )
            )
        except Exception as e:
            self.log(traceback.format_exc())
            try:
                self.blender_progress.setFormat('エラー')
                self.blender_status.setText('Blenderパッケージ出力に失敗しました')
                QApplication.processEvents()
            except Exception:
                pass
            QMessageBox.critical(self,'GSI2Campus3D','Blenderパッケージ出力に失敗しました。\n'+str(e))
        finally:
            self.export_blender_btn.setEnabled(True)

    def open_configured_3d_view(self, project, dem, vector_layers, epsg):
        """Open 3D using QGIS' own *menu action*, then set DEM terrain.

        createNewMapCanvas3D() does create a canvas, but in this environment it
        consistently produced a blank white window while the user's normal
        View > 3D Map Views > New 3D Map View worked.

        v0.3.9 therefore triggers the exact native QAction (normally
        Ctrl+Alt+M). Once QGIS has completely created its own standard 3D view,
        we only change the terrain source to our generated DEM and frame it.
        """
        try:
            from qgis._3d import QgsDemTerrainSettings
            from qgis.PyQt.QtWidgets import QAction

            main = self.iface.mainWindow()
            actions = main.findChildren(QAction)

            native_action = None

            # Most robust criterion: documented default shortcut for
            # "New 3D Map View".
            for a in actions:
                try:
                    sc = a.shortcut().toString(QKeySequence.PortableText)
                    if sc == 'Ctrl+Alt+M':
                        native_action = a
                        break
                except Exception:
                    pass

            # Fallback for customized shortcuts: match English/Japanese menu text.
            if native_action is None:
                for a in actions:
                    try:
                        t = (a.text() or '').replace('&','').strip().lower()
                        if '3d' not in t:
                            continue
                        if ('new' in t and 'map' in t) or ('新規' in t and ('マップ' in t or '地図' in t)):
                            native_action = a
                            break
                    except Exception:
                        pass

            if native_action is None:
                raise RuntimeError(
                    'QGIS標準の［新規3Dマップビュー］アクションを見つけられませんでした。'
                )

            try:
                before_count = len(self.iface.mapCanvases3D())
            except Exception:
                before_count = 0

            self.log(
                'Triggering native QGIS New 3D Map View action: '
                + (native_action.text() or '(unnamed)')
            )

            # This is intentionally the same QAction the user clicks manually.
            native_action.trigger()

            extent_copy = QgsRectangle(dem.extent())

            def _configure_native_view():
                try:
                    canvases = list(self.iface.mapCanvases3D())
                    if not canvases:
                        raise RuntimeError('標準3Dビュー生成後も3D canvasを取得できません。')

                    # Native QGIS appends the newly created view. If an existing
                    # view list was present, prefer the first newly appended item.
                    idx = min(before_count, len(canvases)-1)
                    canvas = canvases[idx] if len(canvases) > before_count else canvases[-1]
                    settings = canvas.mapSettings()
                    if settings is None:
                        raise RuntimeError('標準3DビューのmapSettingsがまだ初期化されていません。')

                    # Do NOT replace CRS, layer stack, theme, transform context,
                    # lights, skybox or any other native settings here.
                    # Only switch terrain to the generated DEM, mirroring the
                    # user's successful manual operation.
                    terrain = QgsDemTerrainSettings()
                    terrain.setLayer(dem)
                    terrain.setResolution(32)
                    terrain.setVerticalScale(1.0)
                    terrain.setElevationOffset(0.0)
                    try:
                        terrain.setSkirtHeight(10.0)
                    except Exception:
                        pass

                    settings.setTerrainSettings(terrain)
                    settings.setTerrainRenderingEnabled(True)

                    self._canvas3d = canvas

                    # Let the terrain rebuild before moving the camera.
                    def _frame():
                        try:
                            canvas.setViewFrom2DExtent(extent_copy)
                        except Exception:
                            try:
                                canvas.resetView()
                            except Exception:
                                pass

                    QTimer.singleShot(500, _frame)
                    self.log(
                        f'Native 3D view configured: DEM_EPSG{epsg} terrain set; '
                        f'view count {before_count}->{len(canvases)}.'
                    )

                except Exception as e:
                    self.log('Native 3D post-configuration failed: '+str(e))
                    self.log(traceback.format_exc())

            # Important: native action first builds the dock/window and its scene.
            # Wait for that initialization to finish before changing terrain.
            QTimer.singleShot(900, _configure_native_view)

            return True, ''
        except Exception as e:
            self.log('3D view auto setup failed: '+str(e))
            self.log(traceback.format_exc())
            return False, str(e)

    def process(self):
        self.save_settings()
        root=self.root_edit.text().strip(); dem_dir=self.dem_dir_edit.text().strip(); out=self.out_edit.text().strip()
        if not root or not os.path.isdir(root): QMessageBox.warning(self,'GSI2Campus3D','GSI入力フォルダを指定してください。'); return
        if not dem_dir or not os.path.isdir(dem_dir): QMessageBox.warning(self,'GSI2Campus3D','DEM XMLフォルダを指定してください。'); return
        dem_xmls=self.collect_dem_xmls(dem_dir, out)
        if not dem_xmls: QMessageBox.warning(self,'GSI2Campus3D','指定フォルダ内にDEM XMLが見つかりません。'); return
        os.makedirs(out,exist_ok=True); z,epsg=self.zone.currentData(); self.go.setEnabled(False); self.progress.setValue(3)
        try:
            project=QgsProject.instance(); project.setCrs(QgsCoordinateReferenceSystem(f'EPSG:{epsg}'))
            rootnode=project.layerTreeRoot()
            if self.cleanup.isChecked():
                self.remove_previous_generated_layers(project,rootnode,out)
            else:
                old=rootnode.findGroup('GSI2Campus3D')
                if old: rootnode.removeChildNode(old)
            group=rootnode.insertGroup(0,'GSI2Campus3D'); g_b=group.addGroup('Buildings'); g_i=group.addGroup('Infrastructure'); g_t=group.addGroup('Terrain')
            # DEM first: QuickDEM4JP can consume a folder containing multiple XMLs.
            # Ask it to create one GeoTIFF directly in the selected plane rectangular CRS.
            dem_out=self.unique_path(out,f'DEM_EPSG{epsg}','.tif')
            self.progress.setValue(8)
            dem_out=self.quickdem_batch(dem_dir,epsg,dem_out)
            dem=QgsRasterLayer(dem_out,f'DEM_EPSG{epsg}');
            if not dem.isValid(): raise RuntimeError('QuickDEM4JPで生成したDEMを読み込めませんでした。')
            self.mark_generated(dem); project.addMapLayer(dem,False); g_t.addLayer(dem); extent=dem.extent(); self.progress.setValue(22)
            bfiles=self.find_files(root,'BldA'); rfiles=self.find_files(root,'RdEdg')
            if not bfiles: raise RuntimeError('BldA XML/GML が見つかりません。')
            b=self.reproj_vector(self.load_union(bfiles,'BldA_source'),epsg)
            self.log(f'Buildings after reprojection: {b.featureCount()}')
            if self.clip.isChecked():
                # v0.2.6 polygonized DEM masks could become empty/misaligned on some
                # QGIS/GDAL builds. Use the proven DEM extent clip first, then reject
                # buildings whose representative point has no valid DEM sample.
                b=self.clip_extent(b,dem.extent())
                self.log(f'Buildings after DEM extent clip: {b.featureCount()}')
                b=self.filter_buildings_with_valid_dem(b,dem)
            threshold=self.area.value(); embed=self.embed.value()
            generated_vectors=[]
            blender_buildings=[]
            blender_road=None
            specs=[('Building_Normal_Small',f'"type" = \'普通建物\' AND $area < {threshold}',self.h_ns.value()),('Building_Normal_Large',f'"type" = \'普通建物\' AND $area >= {threshold}',self.h_nl.value()),('Building_Solid_Small',f'"type" = \'堅ろう建物\' AND $area < {threshold}',self.h_ss.value()),('Building_Solid_Large',f'"type" = \'堅ろう建物\' AND $area >= {threshold}',self.h_sl.value())]
            for i,(name,expr,h) in enumerate(specs):
                x=processing.run('native:extractbyexpression',{'INPUT':b,'EXPRESSION':expr,'OUTPUT':'TEMPORARY_OUTPUT'})['OUTPUT']; lyr=self.save(x,os.path.join(out,name+'.gpkg'),name)
                self.mark_generated(lyr); project.addMapLayer(lyr,False); g_b.addLayer(lyr); self.set3d(lyr,h,embed); generated_vectors.append(lyr); blender_buildings.append((name,lyr,h,embed)); self.progress.setValue(35+i*10)
            if rfiles:
                r=self.reproj_vector(self.load_union(rfiles,'RdEdg_source'),epsg)
                if self.clip.isChecked(): r=self.clip_extent(r,dem.extent())
                rl=self.save(r,os.path.join(out,f'RdEdg_EPSG{epsg}.gpkg'),f'RdEdg_EPSG{epsg}'); self.mark_generated(rl); project.addMapLayer(rl,False); g_i.addLayer(rl); generated_vectors.append(rl); blender_road=rl
            self._last_dem=dem; self._last_buildings=blender_buildings; self._last_road=blender_road; self._last_epsg=epsg; self._last_out=out
            self.progress.setValue(88)
            if self.hide_sources.isChecked():
                self.hide_source_layers(project,rootnode,root,None)
            # Save a simple handoff file for later Blender add-on work.
            e=extent
            with open(os.path.join(out,'campus3d.txt'),'w',encoding='utf-8') as fh:
                fh.write(f'CRS=EPSG:{epsg}\nZONE={z}\nXMIN={e.xMinimum()}\nXMAX={e.xMaximum()}\nYMIN={e.yMinimum()}\nYMAX={e.yMaximum()}\n')
                fh.write(f'HEIGHT_NORMAL_SMALL={self.h_ns.value()}\nHEIGHT_NORMAL_LARGE={self.h_nl.value()}\nHEIGHT_SOLID_SMALL={self.h_ss.value()}\nHEIGHT_SOLID_LARGE={self.h_sl.value()}\n')
            auto3d_ok=False; auto3d_err=''
            if False:  # auto 3D launch intentionally disabled in v0.4.0
                auto3d_ok,auto3d_err=self.open_configured_3d_view(project,dem,generated_vectors,epsg)
            self.progress.setValue(100)
            msg=f'v0.4.18 処理完了\n\n第{z}系 / EPSG:{epsg}\nDEM XMLフォルダをQuickDEM4JPで一括変換しました。\n建物・道路をDEM範囲で切り抜き、建物はDEM有効値で追加検証しました。\n建物を4分類しました。'
            if False:  # auto 3D launch intentionally disabled in v0.4.0
                if auto3d_ok: msg += f'\n\n3Dビューを開き、DEM_EPSG{epsg} をTerrainに自動設定しました。'
                else: msg += f'\n\n3Dビュー自動設定だけ失敗しました。データ生成は完了しています。\n{auto3d_err}'
            msg += '\n\nQGIS 3Dで確認後、必要ならZ方向倍率を決めて［現在の生成レイヤからBlenderパッケージを出力］を押してください。'
            QMessageBox.information(self,'GSI2Campus3D',msg)
        except Exception as e:
            self.log(traceback.format_exc()); QMessageBox.critical(self,'GSI2Campus3D','処理に失敗しました。\n'+str(e)+'\n\n表示 → パネル → ログメッセージも確認してください。')
        finally:self.go.setEnabled(True)
