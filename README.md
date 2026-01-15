# 黄山公交数据分析系统

## 项目概述

这是一个用于处理黄山公交IC卡数据和线路信息的Python系统，主要功能包括：
- 站点数据提取与处理
- 车辆运行时刻表分析
- 公交线路重构与可视化
- 乘客流量统计

## 目录结构

```
huangshan/
├── INIT_*                    # 原始输入数据
│   ├── INIT_IC_data/        # IC卡原始数据
│   ├── INIT_station/        # 站点原始数据
│   └── INIT_station_excel/  # 站点Excel文件
│
├── MID_*                    # 中间处理结果
│   ├── MID_output/          # 主要中间输出文件
│   ├── MID_link/            # 线路链接数据
│   ├── MID_boundary/        # 边界数据
│   └── MID_station/         # 站点处理结果
│
├── OUT_*                    # 最终输出
│   ├── OUT_visualization/   # 可视化HTML文件
│   ├── OUT_analysis/        # 分析统计结果
│   └── OUT_trip_charts/     # 车次图表
│
└── *.py                     # Python处理脚本
```

## 核心脚本

### 1. bus_route_reconstruction.py (公交线路重构与可视化)

**功能**: 重构公交线路并生成交互式可视化地图，展示线路走向和乘客流量

**输入文件**:
- `MID_output/huangshan.csv` - 站点数据（GCJ-02坐标系）
- `MID_output/link_huangshan_gcj02.json` - 线路链接数据（GCJ-02坐标系）
- `INIT_IC_data/*.csv` - IC卡刷卡数据

**输出文件**:
- `OUT_visualization/bus_routes_reconstructed.html` - 交互式公交线路地图

**主要处理流程**:
1. 读取站点数据和线路链接数据
2. 匹配IC卡数据到具体线路和站点
3. 重建车辆轨迹和上下客站点
4. 生成包含线路走向和乘客流量的HTML地图

**坐标系统**: GCJ-02 (高德/火星坐标系)

---

### 2. analyze_vehicle_schedule.py (车辆时刻表分析)

**功能**: 分析车辆运行时刻表，统计发车间隔、运营时长等指标

**输入文件**:
- `MID_output/huangshan.csv` - 站点数据
- `INIT_IC_data/*.csv` - IC卡刷卡数据

**输出文件**:
- `OUT_analysis/vehicle_statistics_summary.csv` - 车辆统计摘要
- `OUT_analysis/vehicle_statistics_by_vehicle.csv` - 按车辆分项统计
- `OUT_trip_charts/*.png` - 车次时刻图表

**主要处理流程**:
1. 加载站点数据和IC卡数据
2. 按线路和车辆分组统计
3. 计算发车间隔、运营时长、客流等指标
4. 生成CSV统计报表和PNG图表

---

## 数据处理流程

### 数据流图（按执行顺序）

```
步骤①: 准备原始数据
┌─────────────────────────────────────────────────────────────────┐
│  INIT_station_excel/*.xlsx   - 站点Excel文件                      │
│  INIT_IC_data/*.csv          - IC卡消费明细数据                    │
│  MID_link/link_huangshan.json - 线路链接数据(OSM/WGS84)          │
│  MID_boundary/*              - 边界数据(GCJ-02)                   │
│  MID_station/*               - 高德站点数据                       │
└─────────────────────────────────────────────────────────────────┘

步骤②: station2csv.py - 从Excel生成初始站点CSV
┌─────────────────────────────────────────────────────────────────┐
│  INIT_station_excel/*.xlsx                                       │
│  ↓ 输出: MID_output/huangshan.csv                                │
│  - 包含: 站点名称、线路、方向、经纬度(GCJ-02)                     │
└─────────────────────────────────────────────────────────────────┘
                                │
                                ↓ (被多个脚本使用)

步骤③: station_visualization.py - 生成站点可视化地图
┌─────────────────────────────────────────────────────────────────┐
│  MID_output/huangshan.csv                                        │
│  ↓ 输出: OUT_visualization/station_visualization.html           │
│  - 站点可视化地图，内置CSV导出功能                                │
│  - 用户可在浏览器中导出:                                         │
│    → MID_station/stations_wgs84.csv                             │
│    → MID_station/stations_gcj02.csv                             │
└─────────────────────────────────────────────────────────────────┘

步骤④: convert_link_coordinates.py - 转换线路坐标
┌─────────────────────────────────────────────────────────────────┐
│  MID_link/link_huangshan.json (WGS84)                            │
│  ↓ 输出: MID_output/link_huangshan_gcj02.json                    │
│  - 坐标转换: WGS84 → GCJ-02                                      │
└─────────────────────────────────────────────────────────────────┘

步骤⑤: bus_route_reconstruction.py - 公交线路重构可视化
┌─────────────────────────────────────────────────────────────────┐
│  输入: MID_output/huangshan.csv                                  │
│        MID_output/link_huangshan_gcj02.json                      │
│        MID_station/stations_*.csv (从步骤③的HTML导出)            │
│        INIT_IC_data/*.csv                                        │
│  ↓ 输出: OUT_visualization/bus_routes_reconstructed.html        │
│  - 重构公交线路走向                                              │
│  - 匹配IC卡上下客数据                                            │
│  - 生成交互式地图展示乘客流量                                    │
└─────────────────────────────────────────────────────────────────┘

步骤⑥: merge_stations.py - 合并同名站点并添加IC卡统计
┌─────────────────────────────────────────────────────────────────┐
│  输入: MID_output/huangshan.csv                                  │
│        INIT_IC_data/*.csv                                        │
│  ↓ 输出: MID_output/huangshan_merged_stations.csv                │
│  - 合并同名站点                                                  │
│  - 添加IC卡上下客统计                                            │
└─────────────────────────────────────────────────────────────────┘
                                │
                                ↓

步骤⑦: filter_stations_by_boundary.py - 按边界筛选站点
┌─────────────────────────────────────────────────────────────────┐
│  输入: MID_output/huangshan_merged_stations.csv                  │
│        MID_boundary/boundary_gaode_*.json                        │
│  ↓ 输出: MID_output/huangshan_merged_stations_filtered_*.csv    │
│        ├── _wgs84.csv  (OSM坐标)                                 │
│        └── _gcj02.csv (高德坐标)                                 │
│  - 排除边界外站点                                                │
│  - 转换坐标系 (WGS84/GCJ-02)                                    │
└─────────────────────────────────────────────────────────────────┘
                                │
                                ↓

步骤⑧: merge_boarding_count_to_gaode.py - 合并上下客统计到高德站点
┌─────────────────────────────────────────────────────────────────┐
│  输入: MID_output/huangshan_merged_stations_filtered_gcj02.csv   │
│        MID_station/stations_gaode_*.csv                          │
│  ↓ 输出: MID_output/stations_gaode_with_boarding.csv             │
│  - 将上下客统计合并到高德站点文件                                │
└─────────────────────────────────────────────────────────────────┘

步骤⑨: analyze_vehicle_schedule.py - 分析车辆运行时刻表
┌─────────────────────────────────────────────────────────────────┐
│  输入: MID_output/huangshan.csv                                  │
│        INIT_IC_data/*.csv                                        │
│  ↓ 输出: OUT_analysis/vehicle_statistics_summary.csv             │
│        OUT_analysis/vehicle_statistics_by_vehicle.csv            │
│        OUT_trip_charts/*.png                                     │
│  - 车辆运行统计                                                  │
│  - 发车间隔、运营时长、客流分析                                  │
│  - 生成时刻表图表                                                │
└─────────────────────────────────────────────────────────────────┘
```

### 数据依赖关系图

```
INIT_station_excel/*.xlsx
    ↓
MID_output/huangshan.csv ←─────────┐
    │                             │
    ├─────────────────────────────┤
    │                             │
    ↓                             ↓
station_visualization        merge_stations + INIT_IC_data/*.csv
(OUT_visualization/)              ↓
    ↓                   MID_output/huangshan_merged_stations.csv
    用户导出CSV                          ↓
    ↓                   filter_stations_by_boundary + MID_boundary/*
MID_station/                          ↓
stations_wgs84.csv    MID_output/huangshan_merged_stations_filtered_gcj02.csv
stations_gcj02.csv                      ↓
    │                   merge_boarding_count_to_gaode + MID_station/*
    │                                  ↓
    └─────────────────────→  MID_output/stations_gaode_with_boarding.csv


MID_link/link_huangshan.json
    ↓
convert_link_coordinates
    ↓
MID_output/link_huangshan_gcj02.json ←───────────┐
    │                                            │
    │                    (可选) MID_station/      │
    │                    stations_*.csv          │
    ↓                                            │
bus_route_reconstruction + INIT_IC_data/*.csv    │
    ↓                                            │
OUT_visualization/bus_routes_reconstructed.html


MID_output/huangshan.csv + INIT_IC_data/*.csv
    ↓
analyze_vehicle_schedule
    ↓
├── OUT_analysis/vehicle_statistics_summary.csv
├── OUT_analysis/vehicle_statistics_by_vehicle.csv
└── OUT_trip_charts/*.png
```

---

## 辅助脚本

### station2csv.py
从Excel文件提取站点信息，生成标准化的站点CSV文件

**输入**: `INIT_station_excel/*.xlsx`
**输出**: `MID_output/huangshan.csv`

### merge_stations.py
合并同名站点并关联IC卡上下客数据

**输入**: `MID_output/huangshan.csv`, `INIT_IC_data/*.csv`
**输出**: `MID_output/huangshan_merged_stations.csv`

### filter_stations_by_boundary.py
按边界筛选站点并进行坐标转换

**输入**: `MID_output/huangshan_merged_stations.csv`, `MID_boundary/*`
**输出**:
- `MID_output/huangshan_merged_stations_filtered_wgs84.csv`
- `MID_output/huangshan_merged_stations_filtered_gcj02.csv`

### convert_link_coordinates.py
转换线路链接数据坐标系

**输入**: `MID_link/link_huangshan.json`
**输出**: `MID_output/link_huangshan_gcj02.json`

### merge_boarding_count_to_gaode.py
将上下客统计合并到高德站点文件

**输入**: `MID_output/huangshan_merged_stations_filtered_gcj02.csv`, `MID_station/stations_gaode_*.csv`
**输出**: `MID_output/stations_gaode_with_boarding.csv`

### station_visualization.py
生成交互式站点可视化地图

**输入**: `MID_output/huangshan.csv`
**输出**: `OUT_visualization/station_visualization.html` (内置CSV导出功能)

---

## 坐标系统说明

本项目使用两种坐标系统：

1. **WGS84**: GPS/OSM标准坐标系
2. **GCJ-02**: 中国"火星"坐标系，用于高德地图等国内地图服务

**注意**: 两种坐标系在中国地区有约50-100米的偏移，使用时需注意区分。

---

## 依赖项

- Python 3.x
- openpyxl (Excel文件处理)
- 标准库: csv, json, pathlib, datetime, etc.

---

## 使用说明

按照以下顺序运行脚本，完成完整的数据处理流程：

1. 准备原始数据并放入 `INIT_*` 目录
2. 运行 `station2csv.py` - 从Excel文件生成初始站点CSV
3. 运行 `station_visualization.py` - 生成站点可视化地图
4. 运行 `convert_link_coordinates.py` - 转换线路坐标到GCJ-02
5. 运行 `bus_route_reconstruction.py` - 生成公交线路重构可视化
6. 运行 `merge_stations.py` - 合并同名站点并添加IC卡统计
7. 运行 `filter_stations_by_boundary.py` - 按边界筛选站点
8. 运行 `merge_boarding_count_to_gaode.py` - 合并上下客统计到高德站点
9. 运行 `analyze_vehicle_schedule.py` - 分析车辆运行时刻表

结果文件将保存在对应的 `OUT_*` 目录中。
