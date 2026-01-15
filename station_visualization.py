#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
公交站点可视化脚本
读取站点统计表，将所有站点在高德地图底图上可视化
原始数据为OSM坐标系（WGS84），需要转换为GCJ-02坐标系（高德地图使用）
"""

import csv
import json
import math
import glob
import re
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
INIT_STATION_DIR = REPO_ROOT / "INIT_station"
OUTPUT_DIR = REPO_ROOT / "MID_output"
MID_STATION_DIR = REPO_ROOT / "MID_station"
OUT_DIR = REPO_ROOT / "OUT_visualization"

def read_csv_gb2312(filename):
    """读取GB2312/GBK编码的CSV文件"""
    data = []
    encodings = ['gb18030', 'gbk', 'gb2312', 'utf-8']
    
    for encoding in encodings:
        try:
            with open(filename, 'r', encoding=encoding, errors='ignore') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    normalized_row = {}
                    for key, value in row.items():
                        normalized_key = key.strip()
                        if value:
                            value = value.replace('\r\n', ' ').replace('\n', ' ').strip()
                        normalized_row[normalized_key] = value
                    data.append(normalized_row)
            print(f"  使用编码: {encoding}")
            break
        except (UnicodeDecodeError, Exception) as e:
            if encoding == encodings[-1]:
                print(f"  警告: 所有编码尝试失败，最后错误: {e}")
            continue
    
    return data

def wgs84_to_gcj02(lon, lat):
    """
    将WGS84坐标系（OSM/OpenStreetMap使用）转换为GCJ-02坐标（火星坐标系）
    高德地图使用GCJ-02坐标系
    """
    a = 6378245.0  # 长半轴
    ee = 0.00669342162296594323  # 偏心率平方
    
    def transform_lat(x, y):
        ret = -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + \
              0.1 * x * y + 0.2 * math.sqrt(abs(x))
        ret += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 *
                math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
        ret += (20.0 * math.sin(y * math.pi) + 40.0 *
                math.sin(y / 3.0 * math.pi)) * 2.0 / 3.0
        ret += (160.0 * math.sin(y / 12.0 * math.pi) + 320 *
                math.sin(y * math.pi / 30.0)) * 2.0 / 3.0
        return ret
    
    def transform_lon(x, y):
        ret = 300.0 + x + 2.0 * y + 0.1 * x * x + \
              0.1 * x * y + 0.1 * math.sqrt(abs(x))
        ret += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 *
                math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
        ret += (20.0 * math.sin(x * math.pi) + 40.0 *
                math.sin(x / 3.0 * math.pi)) * 2.0 / 3.0
        ret += (150.0 * math.sin(x / 12.0 * math.pi) + 300.0 *
                math.sin(x / 30.0 * math.pi)) * 2.0 / 3.0
        return ret
    
    d_lat = transform_lat(lon - 105.0, lat - 35.0)
    d_lon = transform_lon(lon - 105.0, lat - 35.0)
    rad_lat = lat / 180.0 * math.pi
    magic = 1 - ee * math.sin(rad_lat) * math.sin(rad_lat)
    sqrt_magic = math.sqrt(magic)
    d_lat = (d_lat * 180.0) / ((a * (1 - ee)) / (magic * sqrt_magic) * math.pi)
    d_lon = (d_lon * 180.0) / (a / sqrt_magic * math.cos(rad_lat) * math.pi)
    mg_lat = lat + d_lat
    mg_lon = lon + d_lon
    return mg_lon, mg_lat

def gcj02_to_wgs84(gcj_lon, gcj_lat):
    """
    将GCJ-02坐标（火星坐标系）转换为WGS84坐标系（OSM/OpenStreetMap使用）
    使用迭代方法进行反向转换
    """
    # 初始猜测值
    wgs_lon, wgs_lat = gcj_lon, gcj_lat
    
    # 迭代转换（通常2-3次迭代即可收敛）
    for _ in range(5):
        delta_lon, delta_lat = wgs84_to_gcj02(wgs_lon, wgs_lat)
        wgs_lon = gcj_lon - (delta_lon - wgs_lon)
        wgs_lat = gcj_lat - (delta_lat - wgs_lat)
    
    return wgs_lon, wgs_lat

def read_new_station_format(txt_files):
    """
    读取新的站点坐标格式TXT文件
    格式：A01,29440248118210104,0490270.111,站点名称
    A表示去程，B表示返程，01表示站点序号
    坐标是高精度整数，需要转换为小数
    """
    all_stations = []
    
    for txt_file in txt_files:
        # 从文件名提取线路号：A0001.txt -> 1, A0021.txt -> 21
        filename = os.path.basename(txt_file)
        route_match = re.match(r'A(\d+)\.txt', filename)
        if not route_match:
            continue
        
        route_num = route_match.group(1).lstrip('0') or '0'
        if not route_num:
            route_num = '0'
        
        print(f"  读取线路 {route_num} 的站点数据: {filename}")
        
        try:
            with open(txt_file, 'r', encoding='gb18030', errors='ignore') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('a') or line.startswith('b') or line == 'OK':
                        continue
                    
                    # 解析行：A01,29440248118210104,0490270.111,其他字段...
                    parts = line.split(',')
                    if len(parts) < 3:
                        continue
                    
                    # 第一列：A01或B01
                    direction_marker = parts[0][0] if parts[0] else ''
                    station_seq = parts[0][1:] if len(parts[0]) > 1 else ''
                    
                    # 跳过A00/B00和无效数据
                    if station_seq == '00' or not station_seq:
                        continue
                    
                    # 第二列：经度（高精度整数）
                    lon_str = parts[1].strip()
                    # 第三列：纬度（高精度整数，可能有小数点）
                    lat_str = parts[2].strip()
                    
                    # 跳过无效数据
                    if lon_str == '99999999999999999' or lat_str == '99999999999999999':
                        continue
                    
                    try:
                        # 清理坐标字符串（去除分号等）
                        lon_str_clean = lon_str.split(';')[0].strip()
                        lat_str_clean = lat_str.split(';')[0].strip()
                        
                        # 转换坐标：高精度整数转小数
                        # 根据参考点（花鸟市场）确定的转换公式：
                        # 参考：A01,29440248118210104,0490270.111 -> 118.3501265, 29.73329573
                        lon_val = float(lon_str_clean)
                        lat_val_str = lat_str_clean
                        
                        # 坐标转换：
                        # 第一列（lon_str）除以1e15得到基础纬度，然后加上固定偏移量
                        # 第二列（lat_str）去掉前导0，减去固定偏移量，除以1e6，加上118得到经度
                        LAT_OFFSET = 0.293047611789895  # 根据参考点计算的纬度偏移量
                        LON_OFFSET = 140143.611  # 根据参考点计算的经度偏移量
                        
                        # 纬度转换：第一列除以1e15，然后加上偏移量
                        lat = (lon_val / 1e15) + LAT_OFFSET
                        
                        # 经度转换：第二列去掉前导0，减去偏移量，除以1e6，加上118
                        if lat_val_str.startswith('0'):
                            lat_val_clean = float(lat_val_str.lstrip('0'))
                        else:
                            lat_val_clean = float(lat_val_str)
                        
                        lon = ((lat_val_clean - LON_OFFSET) / 1e6) + 118.0
                        
                        # 验证坐标范围（黄山市大致范围：经度118-119，纬度29-30）
                        if not (118 <= lon <= 119 and 29 <= lat <= 30):
                            continue
                        
                        # 提取站点名称（最后一个字段，去除分号）
                        station_name = ''
                        if len(parts) > 3:
                            # 查找最后一个非空字段作为站点名称（跳过DD、数字等）
                            for i in range(len(parts) - 1, 2, -1):
                                name_candidate = parts[i].strip().rstrip(';').strip()
                                # 跳过空值、DD、纯数字、A11/A12等
                                if (name_candidate and 
                                    name_candidate != 'DD' and 
                                    not name_candidate.isdigit() and
                                    not re.match(r'^A\d+$', name_candidate) and
                                    not re.match(r'^B\d+$', name_candidate) and
                                    len(name_candidate) > 1):
                                    station_name = name_candidate
                                    break
                        
                        if not station_name:
                            station_name = f"站点{station_seq}"
                        
                        # 坐标转换：WGS84 -> GCJ-02（如果需要）
                        # 注意：新格式的坐标已经是WGS84，但这里我们直接使用，因为OSM底图使用WGS84
                        # 如果使用高德地图，需要转换；如果使用OSM，不需要转换
                        gcj_lon, gcj_lat = wgs84_to_gcj02(lon, lat)
                        
                        station_info = {
                            'name': station_name,
                            'lon': gcj_lon,  # 转换为GCJ-02用于高德地图
                            'lat': gcj_lat,
                            'original_lon': lon,  # 保留WGS84原始坐标
                            'original_lat': lat,
                            'route': route_num,
                            'direction': 'forward' if direction_marker == 'A' else 'reverse',
                            'sequence': int(station_seq) if station_seq.isdigit() else 0,
                            'attributes': []  # 新格式没有属性信息
                        }
                        
                        all_stations.append(station_info)
                        
                    except (ValueError, IndexError) as e:
                        continue
                
        except Exception as e:
            print(f"    警告: 读取文件 {txt_file} 时出错: {e}")
            continue
    
    print(f"  总共读取到 {len(all_stations)} 条站点记录")
    
    return all_stations

def read_huangshan1_csv(csv_file='huangshan1.csv'):
    """
    读取huangshan1.csv格式的站点数据
    格式：station_id,station_name,station_lon,station_lat,route,direction,sequence
    坐标已经是十进制度数格式（WGS84）
    由于使用OpenStreetMap底图（WGS84坐标系），直接使用原始坐标，不需要转换
    """
    stations_info = []
    
    if not os.path.exists(csv_file):
        return None
    
    print(f"检测到 {csv_file}，使用该文件...")
    
    try:
        with open(csv_file, 'r', encoding='utf-8', errors='ignore') as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    # 获取基本字段
                    station_id = row.get('station_id', '').strip()
                    station_name = row.get('station_name', '').strip()
                    lon_str = row.get('station_lon', '').strip()
                    lat_str = row.get('station_lat', '').strip()
                    
                    if not station_name or not lon_str or not lat_str:
                        continue
                    
                    lon = float(lon_str)
                    lat = float(lat_str)
                    
                    if not (118 <= lon <= 119 and 29 <= lat <= 30):
                        continue
                    
                    # 构建站点信息，保留 row 中的所有原始字段
                    station_info = {
                        'name': station_name,
                        'lon': lon,
                        'lat': lat,
                        'original_lon': lon,
                        'original_lat': lat,
                        'attributes': [],
                        'index': int(station_id) if station_id.isdigit() else len(stations_info) + 1
                    }
                    
                    # 将 row 中的其他所有字段（如 boarding_count, occurrence_count 等）都存入 station_info
                    for key, value in row.items():
                        key_clean = key.strip()
                        if key_clean not in station_info:
                            station_info[key_clean] = value.strip() if value else ''
                    
                    stations_info.append(station_info)
                    
                except (ValueError, KeyError) as e:
                    continue
        
        print(f"  从 {csv_file} 读取到 {len(stations_info)} 条站点记录")
        return stations_info
        
    except Exception as e:
        print(f"  警告: 读取 {csv_file} 时出错: {e}")
        return None


def process_stations():
    """处理站点数据"""
    # 尝试读取 MID_output/huangshan.csv
    station_file = OUTPUT_DIR / 'huangshan.csv'

    stations_info = read_huangshan1_csv(str(station_file))

    if stations_info:
        # 统计信息
        print(f"\n有效站点数: {len(stations_info)}")

        # 统计唯一站点名
        unique_names = set(s['name'] for s in stations_info)
        print(f"唯一站点名: {len(unique_names)}")

        return stations_info

    # 尝试读取新格式TXT文件
    txt_files = sorted(glob.glob(str(INIT_STATION_DIR / 'A*.txt')))
    
    if txt_files:
        print("检测到新格式站点文件，使用新格式...")
        stations_info = read_new_station_format(txt_files)
        
        # 统计信息
        print(f"\n有效站点数: {len(stations_info)}")
        
        # 统计唯一站点名
        unique_names = set(s['name'] for s in stations_info)
        print(f"唯一站点名: {len(unique_names)}")
        
        return stations_info
    else:
        # 使用旧格式CSV文件
        print("使用旧格式CSV文件...")
        print("正在读取CSV文件...")
        
        # 读取站点统计表
        stations_data = read_csv_gb2312('2024年黄山市公交站点统计表（含经纬度）.csv')
        print(f"读取到 {len(stations_data)} 条站点记录")
        
        # 处理站点数据
        stations_info = []
        station_key = None
        lon_key = None
        lat_key = None
        
        # 查找列名（精确匹配）
        if stations_data:
            for key in stations_data[0].keys():
                key_clean = key.strip()
                # 站点列名：可能是"站 点"、"站点"等
                if ('站点' in key_clean or '站 点' in key_clean) and '经度' not in key_clean and '纬度' not in key_clean:
                    if not station_key:  # 只取第一个匹配的
                        station_key = key
                # 经度列名
                if '经度' in key_clean and not lon_key:
                    lon_key = key
                # 纬度列名
                if '纬度' in key_clean and not lat_key:
                    lat_key = key
        
        print(f"站点列名: {station_key}")
        print(f"经度列名: {lon_key}")
        print(f"纬度列名: {lat_key}")
        
        # 统计信息
        valid_stations = 0
        invalid_stations = 0
        
        for idx, station in enumerate(stations_data):
            station_name = station.get(station_key, '').strip() if station_key else ''
            # 如果站点名称为空，使用序号作为名称
            if not station_name:
                station_name = f"站点{idx + 1}"
            
            try:
                lon_str = station.get(lon_key, '').strip() if lon_key else ''
                lat_str = station.get(lat_key, '').strip() if lat_key else ''
                
                if not lon_str or not lat_str:
                    invalid_stations += 1
                    continue
                
                lon = float(lon_str)
                lat = float(lat_str)
                
                # 检查坐标是否有效（黄山市大致范围）
                if lon < 118 or lon > 119 or lat < 29 or lat > 30:
                    invalid_stations += 1
                    continue
                
                # 坐标转换：WGS84 -> GCJ-02
                gcj_lon, gcj_lat = wgs84_to_gcj02(lon, lat)
                
                # 获取站点属性
                physical_bay = station.get('物理港湾式', '').strip()
                non_physical_bay = station.get('非物理港湾式', '').strip()
                accessible = station.get('无障碍站台的站点', '').strip()
                electronic_board = station.get('电子站牌', '').strip()
                
                # 构建属性列表
                attributes = []
                if physical_bay:
                    attributes.append('物理港湾式')
                if non_physical_bay:
                    attributes.append('非物理港湾式')
                if accessible:
                    attributes.append('无障碍站台')
                if electronic_board:
                    attributes.append('电子站牌')
                
                stations_info.append({
                    'name': station_name,
                    'lon': gcj_lon,
                    'lat': gcj_lat,
                    'original_lon': lon,
                    'original_lat': lat,
                    'attributes': attributes,
                    'index': idx + 1
                })
                valid_stations += 1
                
            except (ValueError, KeyError) as e:
                invalid_stations += 1
                continue
        
        print(f"\n有效站点数: {valid_stations}")
        print(f"无效站点数: {invalid_stations}")
        
        return stations_info

def generate_html(stations_info):
    """生成HTML可视化文件"""
    
    if not stations_info:
        print("错误: 没有有效的站点数据")
        return
    
    # 计算地图中心点（所有站点的平均坐标）
    # 使用原始坐标（WGS84），因为OSM底图使用WGS84坐标系
    all_lons = [s.get('original_lon', s['lon']) for s in stations_info]
    all_lats = [s.get('original_lat', s['lat']) for s in stations_info]
    
    center_lon = sum(all_lons) / len(all_lons) if all_lons else 118.3
    center_lat = sum(all_lats) / len(all_lats) if all_lats else 29.72
    
    # 统计站点类型
    station_types = {}
    for station in stations_info:
        station_name = station['name']
        if station_name not in station_types:
            station_types[station_name] = []
        station_types[station_name].append(station)
    
    # 生成HTML内容
    html_content = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>黄山市公交站点可视化</title>
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
    <style>
        body {{
            margin: 0;
            padding: 0;
            font-family: 'Microsoft YaHei', Arial, sans-serif;
        }}
        #map {{
            width: 100%;
            height: 100vh;
        }}
        .info-panel {{
            position: absolute;
            top: 10px;
            right: 10px;
            background: white;
            padding: 15px;
            border-radius: 5px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.3);
            z-index: 1000;
            max-width: 350px;
            max-height: 85vh;
            overflow-y: auto;
        }}
        .info-panel h3 {{
            margin-top: 0;
            margin-bottom: 15px;
            color: #2c3e50;
        }}
        .stats {{
            background: #f8f9fa;
            padding: 10px;
            border-radius: 4px;
            margin-bottom: 15px;
            font-size: 14px;
        }}
        .stats-item {{
            margin: 5px 0;
            color: #555;
        }}
        .control-buttons {{
            display: flex;
            flex-direction: column;
            gap: 8px;
            margin-bottom: 15px;
        }}
        .btn {{
            padding: 10px 15px;
            background: #3498db;
            color: white;
            border: none;
            border-radius: 4px;
            cursor: pointer;
            font-size: 14px;
            transition: background 0.3s;
        }}
        .btn:hover {{
            background: #2980b9;
        }}
        .btn-secondary {{
            background: #95a5a6;
        }}
        .btn-secondary:hover {{
            background: #7f8c8d;
        }}
        .search-box {{
            width: 100%;
            padding: 8px;
            border: 1px solid #ddd;
            border-radius: 4px;
            font-size: 14px;
            box-sizing: border-box;
            margin-bottom: 10px;
        }}
        .search-box:focus {{
            outline: none;
            border-color: #3498db;
        }}
        .filter-section {{
            margin-top: 15px;
            padding-top: 15px;
            border-top: 1px solid #eee;
        }}
        .filter-label {{
            font-size: 12px;
            color: #666;
            margin-bottom: 8px;
            display: block;
        }}
        .checkbox-group {{
            display: flex;
            flex-direction: column;
            gap: 5px;
        }}
        .checkbox-item {{
            display: flex;
            align-items: center;
            gap: 5px;
        }}
        .checkbox-item input {{
            cursor: pointer;
        }}
        .checkbox-item label {{
            cursor: pointer;
            font-size: 13px;
            color: #555;
        }}
        .legend {{
            margin-top: 15px;
            padding-top: 15px;
            border-top: 1px solid #eee;
        }}
        .legend-item {{
            display: flex;
            align-items: center;
            gap: 8px;
            margin: 5px 0;
            font-size: 12px;
        }}
        .legend-color {{
            width: 16px;
            height: 16px;
            border-radius: 50%;
            border: 2px solid #fff;
            box-shadow: 0 0 2px rgba(0,0,0,0.3);
        }}
        #save-status.success {{
            background: #d4edda;
            color: #155724;
            border: 1px solid #c3e6cb;
        }}
        #save-status.error {{
            background: #f8d7da;
            color: #721c24;
            border: 1px solid #f5c6cb;
        }}
        .draggable-marker {{
            cursor: move;
        }}
        /* 自定义圆点标记样式 */
        .station-dot {{
            background-color: #3498db;
            border: 2px solid #fff;
            border-radius: 50%;
            box-shadow: 0 0 4px rgba(0,0,0,0.4);
        }}
        .station-dot-dragging {{
            background-color: #e74c3c !important;
            transform: scale(1.5);
            transition: transform 0.2s;
            z-index: 1000 !important;
        }}
    </style>
</head>
<body>
    <div id="map"></div>
    <div class="info-panel">
        <h3>黄山市公交站点</h3>
        <div class="stats">
            <div class="stats-item"><strong>总站点数:</strong> {len(stations_info)}</div>
            <div class="stats-item"><strong>唯一站点名:</strong> {len(station_types)}</div>
        </div>
        <div class="control-buttons">
            <button class="btn" id="show-all-btn">显示所有站点</button>
            <button class="btn btn-secondary" id="hide-all-btn">隐藏所有站点</button>
            <button class="btn" id="save-btn" style="background: #27ae60; margin-top: 10px;">保存坐标</button>
        </div>
        <div id="save-status" style="margin-top: 10px; padding: 8px; border-radius: 4px; display: none; font-size: 12px;"></div>
        <div class="filter-section">
            <label class="filter-label">搜索站点:</label>
            <input type="text" class="search-box" id="station-search" placeholder="输入站点名称...">
            <button class="btn btn-secondary" id="clear-search-btn" style="width: 100%; margin-top: 5px;">清除搜索</button>
        </div>
        <div class="legend">
            <div class="legend-item">
                <div class="legend-color" style="background: #3498db;"></div>
                <span>公交站点</span>
            </div>
        </div>
    </div>

    <script>
        // 初始化地图
        var map = L.map('map').setView([{center_lat}, {center_lon}], 13);
        
        // 使用OpenStreetMap底图（支持WGS84坐标系）
        L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
            attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
            subdomains: ['a', 'b', 'c'],
            maxZoom: 19,
            tileSize: 256,
            zoomOffset: 0
        }}).addTo(map);
        
        // 站点数据
        var stationsData = {json.dumps(stations_info, ensure_ascii=False, indent=2)};
        
        // OpenStreetMap底图使用WGS84坐标系
        // 对于huangshan1.csv，坐标已经是WGS84，直接使用
        // 对于其他格式，如果有original_lon/original_lat（WGS84），使用原始坐标
        stationsData.forEach(function(station) {{
            // 如果有原始坐标（WGS84），在OSM地图上使用原始坐标
            if (station.original_lon && station.original_lat) {{
                station.lon = station.original_lon;
                station.lat = station.original_lat;
            }}
            // 否则使用lon/lat（应该已经是WGS84）
        }});
        
        // 存储所有标记
        var markers = [];
        var markerGroups = {{
            'physical': [],
            'non-physical': [],
            'accessible': [],
            'electronic': [],
            'normal': []
        }};
        
        // 所有站点统一使用蓝色
        function getStationColor(attributes) {{
            return '#3498db';  // 蓝色
        }}
        
        // GCJ-02到WGS84的转换函数（JavaScript版本）
        function gcj02ToWgs84(gcjLon, gcjLat) {{
            var a = 6378245.0;
            var ee = 0.00669342162296594323;
            
            function transformLat(x, y) {{
                var ret = -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 
                          0.1 * x * y + 0.2 * Math.sqrt(Math.abs(x));
                ret += (20.0 * Math.sin(6.0 * x * Math.PI) + 20.0 *
                        Math.sin(2.0 * x * Math.PI)) * 2.0 / 3.0;
                ret += (20.0 * Math.sin(y * Math.PI) + 40.0 *
                        Math.sin(y / 3.0 * Math.PI)) * 2.0 / 3.0;
                ret += (160.0 * Math.sin(y / 12.0 * Math.PI) + 320 *
                        Math.sin(y * Math.PI / 30.0)) * 2.0 / 3.0;
                return ret;
            }}
            
            function transformLon(x, y) {{
                var ret = 300.0 + x + 2.0 * y + 0.1 * x * x + 
                          0.1 * x * y + 0.1 * Math.sqrt(Math.abs(x));
                ret += (20.0 * Math.sin(6.0 * x * Math.PI) + 20.0 *
                        Math.sin(2.0 * x * Math.PI)) * 2.0 / 3.0;
                ret += (20.0 * Math.sin(x * Math.PI) + 40.0 *
                        Math.sin(x / 3.0 * Math.PI)) * 2.0 / 3.0;
                ret += (150.0 * Math.sin(x / 12.0 * Math.PI) + 300.0 *
                        Math.sin(x / 30.0 * Math.PI)) * 2.0 / 3.0;
                return ret;
            }}
            
            // 迭代转换
            var wgsLon = gcjLon;
            var wgsLat = gcjLat;
            for (var i = 0; i < 5; i++) {{
                var dLat = transformLat(wgsLon - 105.0, wgsLat - 35.0);
                var dLon = transformLon(wgsLon - 105.0, wgsLat - 35.0);
                var radLat = wgsLat / 180.0 * Math.PI;
                var magic = 1 - ee * Math.sin(radLat) * Math.sin(radLat);
                var sqrtMagic = Math.sqrt(magic);
                dLat = (dLat * 180.0) / ((a * (1 - ee)) / (magic * sqrtMagic) * Math.PI);
                dLon = (dLon * 180.0) / (a / sqrtMagic * Math.cos(radLat) * Math.PI);
                var mgLat = wgsLat + dLat;
                var mgLon = wgsLon + dLon;
                wgsLon = gcjLon - (mgLon - wgsLon);
                wgsLat = gcjLat - (mgLat - wgsLat);
            }}
            return [wgsLon, wgsLat];
        }}
        
        // WGS84到GCJ-02的转换函数（JavaScript版本）
        function wgs84ToGcj02(wgsLon, wgsLat) {{
            var a = 6378245.0;
            var ee = 0.00669342162296594323;
            
            function transformLat(x, y) {{
                var ret = -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 
                          0.1 * x * y + 0.2 * Math.sqrt(Math.abs(x));
                ret += (20.0 * Math.sin(6.0 * x * Math.PI) + 20.0 *
                        Math.sin(2.0 * x * Math.PI)) * 2.0 / 3.0;
                ret += (20.0 * Math.sin(y * Math.PI) + 40.0 *
                        Math.sin(y / 3.0 * Math.PI)) * 2.0 / 3.0;
                ret += (160.0 * Math.sin(y / 12.0 * Math.PI) + 320 *
                        Math.sin(y * Math.PI / 30.0)) * 2.0 / 3.0;
                return ret;
            }}
            
            function transformLon(x, y) {{
                var ret = 300.0 + x + 2.0 * y + 0.1 * x * x + 
                          0.1 * x * y + 0.1 * Math.sqrt(Math.abs(x));
                ret += (20.0 * Math.sin(6.0 * x * Math.PI) + 20.0 *
                        Math.sin(2.0 * x * Math.PI)) * 2.0 / 3.0;
                ret += (20.0 * Math.sin(x * Math.PI) + 40.0 *
                        Math.sin(x / 3.0 * Math.PI)) * 2.0 / 3.0;
                ret += (150.0 * Math.sin(x / 12.0 * Math.PI) + 300.0 *
                        Math.sin(x / 30.0 * Math.PI)) * 2.0 / 3.0;
                return ret;
            }}
            
            var dLat = transformLat(wgsLon - 105.0, wgsLat - 35.0);
            var dLon = transformLon(wgsLon - 105.0, wgsLat - 35.0);
            var radLat = wgsLat / 180.0 * Math.PI;
            var magic = 1 - ee * Math.sin(radLat) * Math.sin(radLat);
            var sqrtMagic = Math.sqrt(magic);
            dLat = (dLat * 180.0) / ((a * (1 - ee)) / (magic * sqrtMagic) * Math.PI);
            dLon = (dLon * 180.0) / (a / sqrtMagic * Math.cos(radLat) * Math.PI);
            var mgLat = wgsLat + dLat;
            var mgLon = wgsLon + dLon;
            return [mgLon, mgLat];
        }}
        
        // 创建站点标记
        stationsData.forEach(function(station) {{
            var color = getStationColor(station.attributes);
            
            // 使用 L.marker 替代 L.circleMarker，因为 L.marker 原生支持拖动
            var marker = L.marker([station.lat, station.lon], {{
                draggable: true,
                icon: L.divIcon({{
                    className: 'station-dot',
                    iconSize: [12, 12],
                    iconAnchor: [6, 6]
                }})
            }});
            
            // 添加拖动样式和禁用地图拖动
            marker.on('dragstart', function(e) {{
                // 禁用地图拖动
                map.dragging.disable();
                
                // 修改图标样式
                var icon = this.getElement();
                if (icon) {{
                    icon.classList.add('station-dot-dragging');
                }}
            }});
            
            // 拖动结束事件，更新坐标
            marker.on('dragend', function(e) {{
                // 重新启用地图拖动
                map.dragging.enable();
                
                var icon = this.getElement();
                if (icon) {{
                    icon.classList.remove('station-dot-dragging');
                }}
                
                var newPos = e.target.getLatLng();
                var newLat = newPos.lat;
                var newLon = newPos.lng;
                
                // 更新站点数据
                station.lat = newLat;
                station.lon = newLon;
                station.original_lat = newLat;
                station.original_lon = newLon;
                
                // 更新popup内容
                updateMarkerPopup(marker, station);
            }});
            
            // 构建popup内容的函数
            function buildPopupContent(station) {{
                var popupContent = '<div style="min-width: 200px;">';
                popupContent += '<b style="font-size: 14px;">' + station.name + '</b><br>';
                popupContent += '<div style="margin-top: 8px; font-size: 12px; color: #666;">';
                popupContent += '记录ID: ' + (station.index || 'N/A') + '<br>';
                
                // 显示线路信息
                if (station.route) {{
                    var directionText = station.direction;
                    if (!directionText) {{
                        directionText = (station.direction === 'forward' ? '去程' : station.direction === 'reverse' ? '返程' : '');
                    }}
                    popupContent += '线路: ' + station.route + '路';
                    if (directionText) {{
                        popupContent += ' (' + directionText + ')';
                    }}
                    if (station.sequence) {{
                        popupContent += ' - 第' + station.sequence + '站';
                    }}
                    popupContent += '<br>';
                }}
                
                if (station.attributes && station.attributes.length > 0) {{
                    popupContent += '属性: ' + station.attributes.join(', ') + '<br>';
                }}
                
                // 显示上车次数等额外信息
                if (station.boarding_count !== undefined && station.boarding_count !== '') {{
                    popupContent += '<strong>上车次数:</strong> ' + station.boarding_count + '<br>';
                }}
                if (station.occurrence_count !== undefined && station.occurrence_count !== '') {{
                    popupContent += '出现次数: ' + station.occurrence_count + '<br>';
                }}
                
                // 显示WGS84坐标（OSM坐标系）
                var wgsLat = station.lat;
                var wgsLon = station.lon;
                popupContent += '<strong>WGS84坐标:</strong> ' + wgsLat.toFixed(6) + ', ' + wgsLon.toFixed(6) + '<br>';
                
                // 计算并显示GCJ-02坐标（高德坐标系）
                var gcjCoords = wgs84ToGcj02(wgsLon, wgsLat);
                popupContent += '<strong>GCJ-02坐标:</strong> ' + gcjCoords[1].toFixed(6) + ', ' + gcjCoords[0].toFixed(6) + '<br>';
                popupContent += '<small style="color: #999;">(可拖动修改位置)</small>';
                popupContent += '</div></div>';
                return popupContent;
            }}
            
            // 更新标记popup的函数
            function updateMarkerPopup(marker, station) {{
                var popupContent = buildPopupContent(station);
                marker.setPopupContent(popupContent);
            }}
            
            var popupContent = buildPopupContent(station);
            
            // 构建tooltip标签，包含线路信息
            var tooltipText = station.name;
            if (station.route) {{
                var directionText = station.direction || '';
                tooltipText += ' [' + station.route + '路';
                if (directionText) {{
                    tooltipText += directionText;
                }}
                if (station.sequence) {{
                    tooltipText += '-' + station.sequence;
                }}
                tooltipText += ']';
            }}
            
            marker.bindPopup(popupContent);
            marker.bindTooltip(tooltipText, {{
                permanent: false,
                direction: 'top',
                offset: [0, -10]
            }});
            
            // 存储站点数据到标记
            marker.options.stationData = station;
            
            // 添加到地图
            marker.addTo(map);
            markers.push(marker);
            
            // 按属性分类
            if (station.attributes.includes('物理港湾式')) {{
                markerGroups['physical'].push(marker);
            }}
            if (station.attributes.includes('非物理港湾式')) {{
                markerGroups['non-physical'].push(marker);
            }}
            if (station.attributes.includes('无障碍站台')) {{
                markerGroups['accessible'].push(marker);
            }}
            if (station.attributes.includes('电子站牌')) {{
                markerGroups['electronic'].push(marker);
            }}
            if (station.attributes.length === 0) {{
                markerGroups['normal'].push(marker);
            }}
        }});
        
        // 显示所有站点
        document.getElementById('show-all-btn').addEventListener('click', function() {{
            markers.forEach(function(marker) {{
                if (!map.hasLayer(marker)) {{
                    map.addLayer(marker);
                }}
            }});
        }});
        
        // 隐藏所有站点
        document.getElementById('hide-all-btn').addEventListener('click', function() {{
            markers.forEach(function(marker) {{
                if (map.hasLayer(marker)) {{
                    map.removeLayer(marker);
                }}
            }});
        }});
        
        // 搜索功能
        var searchInput = document.getElementById('station-search');
        var clearSearchBtn = document.getElementById('clear-search-btn');
        
        function filterBySearch(searchTerm) {{
            var term = searchTerm.toLowerCase().trim();
            markers.forEach(function(marker) {{
                var station = marker.options.stationData;
                if (!station) {{
                    // 从popup中获取站点名称
                    var popup = marker.getPopup();
                    if (popup) {{
                        var content = popup.getContent();
                        var match = content.match(/<b[^>]*>([^<]+)<\\/b>/);
                        if (match) {{
                            var stationName = match[1].toLowerCase();
                            if (term === '' || stationName.indexOf(term) !== -1) {{
                                if (!map.hasLayer(marker)) {{
                                    map.addLayer(marker);
                                }}
                            }} else {{
                                if (map.hasLayer(marker)) {{
                                    map.removeLayer(marker);
                                }}
                            }}
                        }}
                    }}
                }} else {{
                    if (term === '' || station.name.toLowerCase().indexOf(term) !== -1) {{
                        if (!map.hasLayer(marker)) {{
                            map.addLayer(marker);
                        }}
                    }} else {{
                        if (map.hasLayer(marker)) {{
                            map.removeLayer(marker);
                        }}
                    }}
                }}
            }});
        }}
        
        // 确保所有标记都有站点数据（已经在创建时存储，这里作为备份）
        markers.forEach(function(marker, index) {{
            if (!marker.options.stationData) {{
                marker.options.stationData = stationsData[index];
            }}
        }});
        
        searchInput.addEventListener('input', function() {{
            filterBySearch(this.value);
        }});
        
        clearSearchBtn.addEventListener('click', function() {{
            searchInput.value = '';
            filterBySearch('');
        }});
        
        
        // 支持回车键搜索
        searchInput.addEventListener('keypress', function(e) {{
            if (e.key === 'Enter') {{
                filterBySearch(this.value);
            }}
        }});
        
        // 保存功能
        var saveBtn = document.getElementById('save-btn');
        var saveStatus = document.getElementById('save-status');
        
        function downloadCSV(csvContent, filename) {{
            var blob = new Blob([csvContent], {{ type: 'text/csv;charset=utf-8;' }});
            var link = document.createElement('a');
            var url = URL.createObjectURL(blob);
            link.setAttribute('href', url);
            link.setAttribute('download', filename);
            link.style.visibility = 'hidden';
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
        }}
        
        saveBtn.addEventListener('click', function() {{
            try {{
                if (stationsData.length === 0) return;

                // 动态获取所有可能的列名
                var allKeys = new Set();
                // 确保基本列排在前面
                var baseKeys = ['station_id', 'station_name', 'station_lon', 'station_lat'];
                baseKeys.forEach(k => allKeys.add(k));
                
                stationsData.forEach(s => {{
                    Object.keys(s).forEach(k => {{
                        if (k !== 'lon' && k !== 'lat' && k !== 'original_lon' && k !== 'original_lat' && k !== 'attributes' && k !== 'index' && k !== 'name') {{
                            allKeys.add(k);
                        }}
                    }});
                }});
                
                var headerArray = Array.from(allKeys);
                var headerLine = headerArray.map(k => {{
                    if (k === 'station_name') return 'station_name';
                    if (k === 'station_lon') return 'station_lon';
                    if (k === 'station_lat') return 'station_lat';
                    return k;
                }}).join(',');

                var wgs84Data = [headerLine];
                var gcj02Data = [headerLine];
                
                stationsData.forEach(function(station, index) {{
                    var wgsLat = station.lat;
                    var wgsLon = station.lon;
                    var gcjCoords = wgs84ToGcj02(wgsLon, wgsLat);
                    
                    var rowData = headerArray.map(key => {{
                        if (key === 'station_id') return station.index || (index + 1);
                        if (key === 'station_name') return '"' + (station.name || '') + '"';
                        if (key === 'station_lon') return wgsLon.toFixed(8);
                        if (key === 'station_lat') return wgsLat.toFixed(8);
                        return station[key] !== undefined ? station[key] : '';
                    }});
                    wgs84Data.push(rowData.join(','));

                    var rowDataGcj = [...rowData];
                    rowDataGcj[headerArray.indexOf('station_lon')] = gcjCoords[0].toFixed(8);
                    rowDataGcj[headerArray.indexOf('station_lat')] = gcjCoords[1].toFixed(8);
                    gcj02Data.push(rowDataGcj.join(','));
                }});
                
                // 生成CSV内容
                var wgs84CSV = wgs84Data.join('\\n');
                var gcj02CSV = gcj02Data.join('\\n');
                
                // 下载文件
                var timestamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
                downloadCSV(wgs84CSV, 'stations_osm_wgs84_' + timestamp + '.csv');
                setTimeout(function() {{
                    downloadCSV(gcj02CSV, 'stations_gaode_gcj02_' + timestamp + '.csv');
                    
                    // 显示成功消息
                    saveStatus.textContent = '保存成功！已下载两个CSV文件。';
                    saveStatus.className = 'success';
                    saveStatus.style.display = 'block';
                    
                    setTimeout(function() {{
                        saveStatus.style.display = 'none';
                    }}, 3000);
                }}, 500);
                
            }} catch (error) {{
                saveStatus.textContent = '保存失败: ' + error.message;
                saveStatus.className = 'error';
                saveStatus.style.display = 'block';
                
                setTimeout(function() {{
                    saveStatus.style.display = 'none';
                }}, 5000);
            }}
        }});
    </script>
</body>
</html>"""
    
    # 确保输出目录存在
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    output_file = OUT_DIR / 'station_visualization.html'
    with open(str(output_file), 'w', encoding='utf-8') as f:
        f.write(html_content)

    print(f"\nHTML文件已生成: {output_file}")

if __name__ == '__main__':
    print("=" * 50)
    print("黄山市公交站点可视化生成工具")
    print("=" * 50)
    
    try:
        # 处理站点数据
        stations_info = process_stations()
        
        # 生成HTML
        generate_html(stations_info)
        
        print("\n" + "=" * 50)
        print("处理完成！")
        print("=" * 50)
        output_file = OUT_DIR / 'station_visualization.html'
        print(f"\n请在浏览器中打开 {output_file} 查看可视化结果")
        
    except Exception as e:
        print(f"\n错误: {e}")
        import traceback
        traceback.print_exc()

