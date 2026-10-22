# GSI2Campus3D

**GSI data preparation for Blender / Campus3D Workflow [A]**

GSI2Campus3D is a QGIS plugin that helps convert Japanese geospatial data provided by the Geospatial Information Authority of Japan (GSI) into lightweight 3D models for use in Blender.

The plugin was developed for architectural, urban planning, GIS, and disaster-prevention education, with the goal of reducing the technical effort required to create 3D regional models and allowing more time for observation, analysis, and discussion.

**Est. 2026 | Powered by IINO Lab.**

---

## Overview / 概要

GSI2Campus3D は、国土地理院の基盤地図情報・数値標高モデル（DEM）などを利用して、QGIS上で地域の3Dモデルを作成し、Blenderへ受け渡すためのQGISプラグインです。

主な目的は、GISや3Dモデリングそのものの複雑な操作をできるだけ減らし、

- 地形
- 建物
- 道路
- 航空写真
- 都市計画情報
- 防災情報

などを組み合わせて、地域を立体的に観察・分析できる環境を簡単に構築することです。

Campus3D Workflow は次の2つのツールで構成されています。

```text
[A] QGIS
GSI2Campus3D
      ↓
Campus3D Blender Package
      ↓
[B] Blender
Campus3D Tools
      ↓
GLB
```

---

## Main Features / 主な機能

### GSI data processing

- 国土地理院「基盤地図情報」XMLの読み込み
- 複数XMLファイルの自動検索
- DEM5A / DEM5B / DEM5C / DEM10A / DEM10B などへの対応
- 古いShift_JIS形式DEM XMLへの対応
- 平面直角座標系 第1系～第19系の選択
- 都道府県・地域名を併記した座標系選択

### 3D model generation

- DEMによるTerrain生成
- 建物ポリゴンの3D化
- 道路データの3D化
- 建物を以下の4種類に分類

```text
Building_Normal_Small
Building_Normal_Large
Building_Solid_Small
Building_Solid_Large
```

### Terrain texture

- 国土地理院「全国最新写真（シームレス）」をTerrainテクスチャとして利用
- Blender用 `Terrain_Texture.png` を自動生成
- TerrainとテクスチャのUV対応

### Blender export

以下のようなBlender連携パッケージを作成します。

```text
Campus3D_Blender/
├── campus3d_manifest.json
├── Terrain.obj
├── Roads.obj
├── Building_Normal_Small.obj
├── Building_Normal_Large.obj
├── Building_Solid_Small.obj
├── Building_Solid_Large.obj
└── Terrain_Texture.png
```

### Vertical exaggeration

QGIS 3Dビューの鉛直倍率をBlender出力へ反映できます。

地形の高低差のみを強調し、建物自身の高さは実尺度を維持します。

For example:

```text
Terrain elevation : ×3.0
Building height   : ×1.0
```

これにより、広域地形の起伏を読みやすくしながら、建築物のスケール感を保持できます。

### Usability

- QGIS 3Dビューの鉛直倍率との自動連携
- Blender出力時の進捗表示
- 縦スクロール対応UI
- マウスホイールによる数値・選択項目の誤変更防止
- 設定値を初期状態へ戻すリセット機能
- 入力・出力フォルダはリセット時も保持

---

## Requirements / 必要環境

### QGIS

Development and testing have mainly been performed with:

```text
QGIS 3.44 LTR
Python 3.12
```

### Required QGIS plugin

GSI2Campus3D uses:

**QuickDEM4JP**

for conversion of GSI DEM XML data.

QuickDEM4JP must be installed separately in QGIS.

### Operating systems

Main development and testing:

- Windows 11

Limited testing has also been performed on Linux / Ubuntu environments.

Compatibility with other QGIS versions and operating systems is not guaranteed.

---

## Installation / インストール

### Current development version

Download or clone this repository.

The QGIS plugin folder is:

```text
gsi2campus3d
```

Copy this folder into your QGIS plugin directory, or create a ZIP file containing the folder and install it using:

```text
QGIS
→ Plugins
→ Manage and Install Plugins
→ Install from ZIP
```

After installation, enable **GSI2Campus3D** from the QGIS plugin manager.

A toolbar button for:

**Campus3D Workflow [A] — GSI2Campus3D**

will appear.

A packaged release version will also be provided through GitHub Releases.

---

## Basic Workflow / 基本的な使い方

### 1. Download GSI data

Download the required data from the Geospatial Information Authority of Japan.

Typical datasets include:

- 基盤地図情報 基本項目
- 数値標高モデル DEM

Place the downloaded and extracted folders in a working directory.

Example:

```text
Kashiwazaki/
├── FG-GML-xxxx-ALL-xxxx/
├── FG-GML-xxxx-DEM5A-xxxx/
└── FG-GML-xxxx-DEM5B-xxxx/
```

GSI2Campus3D can recursively search the parent directory.

---

### 2. Select the coordinate system

Choose the appropriate Japanese Plane Rectangular Coordinate System.

Example:

```text
Niigata Prefecture
Plane Rectangular CS VIII
EPSG:6676
```

Always select the correct coordinate system for the target area.

---

### 3. Generate the QGIS model

Specify:

- source folder
- DEM folder
- output folder

and run the processing workflow.

The plugin generates:

- DEM
- buildings
- roads

for 2D and 3D visualization in QGIS.

---

### 4. Open QGIS 3D View

Open:

```text
View
→ 3D Map Views
→ New 3D Map View
```

Adjust the vertical scale if necessary.

For relatively wide regional models, a vertical exaggeration of approximately:

```text
2.0 – 3.0
```

may make terrain features easier to perceive.

The optimal value depends on the terrain and visualization scale.

---

### 5. Export the Blender package

Use the Blender package export function in GSI2Campus3D.

The Terrain resolution and texture resolution can be adjusted.

Typical defaults:

```text
Terrain long side : 256 cells
Texture           : 2048 px
Horizontal scale  : 1:1000
```

---

### 6. Import into Blender

Use the companion Blender add-on:

**Campus3D Tools**

Campus3D Tools automatically imports the exported package and organizes:

- Terrain
- Roads
- Buildings
- Terrain texture

into the Blender scene.

---

## Additional GIS Layers / 追加GIS情報

GSI2Campus3D is designed to work together with standard QGIS functionality.

Other GIS datasets can be overlaid in QGIS, for example:

- 用途地域
- 洪水浸水想定区域
- 津波浸水想定
- 土砂災害関連情報
- 河川
- 土地利用
- その他の国土数値情報

These layers can be used for urban planning, architectural, geographic, and disaster-prevention analysis.

The plugin intentionally does not attempt to replace QGIS's standard GIS functions.

---

## Educational Use / 教育利用

Campus3D Workflow was originally developed for university education in architecture, urban planning, and GIS.

Example learning activities include:

- comparing 2D maps with 3D terrain
- understanding relationships between terrain and urban areas
- observing relationships between roads, buildings, and topography
- comparing urban planning zones with actual building distributions
- examining flood and tsunami hazard areas in relation to terrain
- selecting and analyzing arbitrary regions
- exporting models to GLB
- comparing models created by other students

The project aims to shift classroom effort from:

> **creating the 3D model**

toward:

> **using the 3D model to observe, analyze, and think**

---

## Known Limitations / 現在の制限

- GSI source data formats may change.
- DEM coverage and resolution vary by region.
- Areas without DEM data may appear as NoData in QGIS 3D views.
- Very large areas may require additional processing time and memory.
- Terrain vertical exaggeration is intended mainly for visualization and should not be interpreted as true geometric scale in the vertical direction.
- Some functions depend on QuickDEM4JP.
- macOS compatibility has not yet been sufficiently tested.

---

## Project Status

Current QGIS plugin version:

```text
v0.4.18
```

Status:

**Public beta / educational research version**

The software is under active development.

Bug reports, suggestions, and reports of successful use in other regions or environments are welcome.

---

## Companion Project

### Campus3D Tools for Blender

A companion Blender add-on is used to import and manage the Blender packages generated by GSI2Campus3D.

The Blender project will be published separately.

```text
[A] GSI2Campus3D
       ↓
[B] Campus3D Tools
       ↓
GLB
```

---

## Author

**Akinaru Iino**  
IINO Lab.  
Niigata Institute of Technology, Japan

Campus3D Workflow  
**Est. 2026 | Powered by IINO Lab.**

---

## License

The open-source license for this project will be specified before the first official release.

---

## Acknowledgements

This project makes use of geospatial data and services provided by the Geospatial Information Authority of Japan (GSI).

GSI2Campus3D also works in combination with QuickDEM4JP for processing GSI DEM data.

Please follow the terms of use and licensing requirements of each source dataset and external software when using or redistributing derived materials.
