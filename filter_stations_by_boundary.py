#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
根据边界文件过滤站点
排除边界外的站点，只保留边界内的站点
站点坐标为OSM坐标系（WGS84），边界为高德坐标系（GCJ02）
"""

import csv
import json
import math
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = REPO_ROOT / "MID_output"
BOUNDARY_DIR = REPO_ROOT / "MID_boundary"

def wgs84_to_gcj02(lon, lat):
    """
    将WGS84坐标系转换为GCJ02坐标系（高德地图使用）
    参考 bus_route_reconstruction.py 中的转换函数
    """
    a = 6378245.0  # 长半轴
    ee = 0.00669342162296594323  # 偏心率平方
    
    def transform_lat(x, y):
        ret = -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * math.sqrt(abs(x))
        ret += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
        ret += (20.0 * math.sin(y * math.pi) + 40.0 * math.sin(y / 3.0 * math.pi)) * 2.0 / 3.0
        ret += (160.0 * math.sin(y / 12.0 * math.pi) + 320 * math.sin(y * math.pi / 30.0)) * 2.0 / 3.0
        return ret
    
    def transform_lon(x, y):
        ret = 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * math.sqrt(abs(x))
        ret += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
        ret += (20.0 * math.sin(x * math.pi) + 40.0 * math.sin(x / 3.0 * math.pi)) * 2.0 / 3.0
        ret += (150.0 * math.sin(x / 12.0 * math.pi) + 300.0 * math.sin(x / 30.0 * math.pi)) * 2.0 / 3.0
        return ret
    
    def out_of_china(lon, lat):
        return (lon < 72.004 or lon > 137.8347 or lat < 0.8293 or lat > 55.8271)
    
    if out_of_china(lon, lat):
        return lon, lat
    
    dLat = transform_lat(lon - 105.0, lat - 35.0)
    dLon = transform_lon(lon - 105.0, lat - 35.0)
    radLat = lat / 180.0 * math.pi
    magic = math.sin(radLat)
    magic = 1 - ee * magic * magic
    sqrtMagic = math.sqrt(magic)
    dLat = (dLat * 180.0) / ((a * (1 - ee)) / (magic * sqrtMagic) * math.pi)
    dLon = (dLon * 180.0) / (a / sqrtMagic * math.cos(radLat) * math.pi)
    mgLat = lat + dLat
    mgLon = lon + dLon
    return mgLon, mgLat

def point_in_polygon(point, polygon):
    """
    判断点是否在多边形内（射线法）
    
    Args:
        point: (lon, lat) 点坐标
        polygon: [[lon, lat], ...] 多边形顶点列表
    
    Returns:
        True if point is inside polygon, False otherwise
    """
    x, y = point
    n = len(polygon)
    inside = False
    
    p1x, p1y = polygon[0]
    for i in range(1, n + 1):
        p2x, p2y = polygon[i % n]
        if y > min(p1y, p2y):
            if y <= max(p1y, p2y):
                if x <= max(p1x, p2x):
                    if p1y != p2y:
                        xinters = (y - p1y) * (p2x - p1x) / (p2y - p1y) + p1x
                    if p1x == p2x or x <= xinters:
                        inside = not inside
        p1x, p1y = p2x, p2y
    
    return inside

def load_boundary(boundary_file):
    """
    加载边界文件
    
    Args:
        boundary_file: 边界JSON文件路径
    
    Returns:
        polygon: [[lon, lat], ...] 多边形顶点列表
    """
    print(f"读取边界文件: {boundary_file}")
    with open(boundary_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    coordinates = data.get('coordinates', [])
    if not coordinates:
        raise ValueError("边界文件中没有找到坐标数据")
    
    # 确保多边形是闭合的（首尾相同）
    polygon = []
    for coord in coordinates:
        if len(coord) >= 2:
            polygon.append([float(coord[0]), float(coord[1])])
    
    # 如果首尾不同，添加第一个点使其闭合
    if polygon and (polygon[0][0] != polygon[-1][0] or polygon[0][1] != polygon[-1][1]):
        polygon.append(polygon[0])
    
    print(f"  边界多边形有 {len(polygon)} 个顶点")
    print(f"  坐标系统: {data.get('coordinate_system', 'unknown')}")
    
    return polygon

def filter_stations_by_boundary(stations_file, boundary_file, output_file):
    """
    根据边界过滤站点，输出两份CSV文件（OSM坐标和高德坐标）
    
    Args:
        stations_file: 站点CSV文件路径
        boundary_file: 边界JSON文件路径
        output_file: 输出CSV文件基础名称（会自动添加_wgs84和_gcj02后缀）
    """
    # 加载边界
    boundary_polygon = load_boundary(boundary_file)
    
    # 读取站点数据
    print(f"\n读取站点文件: {stations_file}")
    stations = []
    with open(stations_file, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                station_id = row.get('station_id', '').strip()
                station_name = row.get('station_name', '').strip()
                lon = float(row.get('station_lon', '').strip())
                lat = float(row.get('station_lat', '').strip())
                occurrence_count = row.get('occurrence_count', '').strip()
                boarding_count = row.get('boarding_count', '').strip()
                
                stations.append({
                    'station_id': station_id,
                    'station_name': station_name,
                    'lon': lon,
                    'lat': lat,
                    'occurrence_count': occurrence_count,
                    'boarding_count': boarding_count
                })
            except (ValueError, KeyError) as e:
                print(f"  警告: 跳过无效记录: {e}")
                continue
    
    print(f"  读取到 {len(stations)} 个站点")
    
    # 过滤站点
    print(f"\n过滤站点（判断点是否在边界内）...")
    filtered_stations = []
    excluded_count = 0
    
    for station in stations:
        # 将站点坐标从WGS84转换为GCJ02
        gcj_lon, gcj_lat = wgs84_to_gcj02(station['lon'], station['lat'])
        
        # 判断点是否在多边形内
        if point_in_polygon((gcj_lon, gcj_lat), boundary_polygon):
            # 同时保存WGS84和GCJ02坐标
            station['gcj_lon'] = gcj_lon
            station['gcj_lat'] = gcj_lat
            filtered_stations.append(station)
        else:
            excluded_count += 1
            print(f"  排除: {station['station_name']} ({station['lon']:.6f}, {station['lat']:.6f})")
    
    print(f"  边界内站点数: {len(filtered_stations)}")
    print(f"  边界外站点数: {excluded_count}")

    # 写入OSM坐标文件（WGS84）
    output_file_wgs84 = str(output_file).replace('.csv', '_wgs84.csv')
    print(f"\n写入OSM坐标文件: {output_file_wgs84}")
    with open(output_file_wgs84, 'w', encoding='utf-8-sig', newline='') as f:
        fieldnames = ['station_id', 'station_name', 'station_lon', 'station_lat', 'occurrence_count', 'boarding_count']
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for station in filtered_stations:
            writer.writerow({
                'station_id': station['station_id'],
                'station_name': station['station_name'],
                'station_lon': f'{station["lon"]:.8f}',
                'station_lat': f'{station["lat"]:.8f}',
                'occurrence_count': station['occurrence_count'],
                'boarding_count': station['boarding_count']
            })
    
    print(f"  已保存 {len(filtered_stations)} 个站点到 {output_file_wgs84} (WGS84坐标)")

    # 写入高德坐标文件（GCJ02）
    output_file_gcj02 = str(output_file).replace('.csv', '_gcj02.csv')
    print(f"\n写入高德坐标文件: {output_file_gcj02}")
    with open(output_file_gcj02, 'w', encoding='utf-8-sig', newline='') as f:
        fieldnames = ['station_id', 'station_name', 'station_lon', 'station_lat', 'occurrence_count', 'boarding_count']
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for station in filtered_stations:
            writer.writerow({
                'station_id': station['station_id'],
                'station_name': station['station_name'],
                'station_lon': f'{station["gcj_lon"]:.8f}',
                'station_lat': f'{station["gcj_lat"]:.8f}',
                'occurrence_count': station['occurrence_count'],
                'boarding_count': station['boarding_count']
            })
    
    print(f"  已保存 {len(filtered_stations)} 个站点到 {output_file_gcj02} (GCJ02坐标)")
    
    # 统计信息
    total_boarding = sum(int(s['boarding_count']) if s['boarding_count'] else 0 for s in filtered_stations)
    print(f"\n统计信息:")
    print(f"  边界内站点总数: {len(filtered_stations)}")
    print(f"  边界内站点总上车次数: {total_boarding:,}")
    print(f"\n输出文件:")
    print(f"  OSM坐标 (WGS84): {output_file_wgs84}")
    print(f"  高德坐标 (GCJ02): {output_file_gcj02}")
    
    return filtered_stations

if __name__ == '__main__':
    stations_file = OUTPUT_DIR / 'huangshan_merged_stations.csv'
    boundary_file = BOUNDARY_DIR / 'boundary_gaode_1766313981512.json'
    output_file = OUTPUT_DIR / 'huangshan_merged_stations_filtered.csv'  # 基础文件名，会自动添加_wgs84和_gcj02后缀

    try:
        filter_stations_by_boundary(stations_file, boundary_file, output_file)
        print("\n处理完成！")
    except FileNotFoundError as e:
        print(f"错误: 找不到文件 {e}")
    except Exception as e:
        print(f"错误: {e}")
        import traceback
        traceback.print_exc()

