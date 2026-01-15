#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
合并同名站点脚本
将同名站点合并，经纬度取平均值（不区分线路和方向）
"""

import csv
import glob
import math
from pathlib import Path
from collections import defaultdict

REPO_ROOT = Path(__file__).resolve().parent
IC_DATA_DIR = REPO_ROOT / "INIT_IC_data"
OUTPUT_DIR = REPO_ROOT / "MID_output"
MID_STATION_DIR = REPO_ROOT / "MID_station"

def haversine_distance(lon1, lat1, lon2, lat2):
    """计算两点间的球面距离（米）"""
    R = 6371000  # 地球半径（米）
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    c = 2 * math.asin(math.sqrt(a))
    return R * c

def split_single_point(lon, lat, distance_meters=10):
    """
    将单个点分裂成两个点，距离约50米（每个点移动25米），平均坐标不变
    
    Args:
        lon: 经度
        lat: 纬度
        distance_meters: 每个点移动的距离（米），默认25米，两个点总距离约50米
    
    Returns:
        ((lon1, lat1), (lon2, lat2)) 分裂后的两个点
    """
    R = 6371000  # 地球半径（米）
    
    # 选择正北方向（0度）进行分裂
    # 计算纬度方向的偏移（向北为正）
    dlat_radians = distance_meters / R
    dlat_degrees = math.degrees(dlat_radians)
    
    # 第一个点：向北移动25米
    point1_lat = lat + dlat_degrees
    point1_lon = lon
    
    # 第二个点：向南移动25米
    point2_lat = lat - dlat_degrees
    point2_lon = lon
    
    return (point1_lon, point1_lat), (point2_lon, point2_lat)

def find_farthest_pair(coords_list):
    """
    找到距离最远的两个点
    
    Args:
        coords_list: [(lon, lat), ...] 坐标列表
    
    Returns:
        ((lon1, lat1), (lon2, lat2)) 距离最远的两个点，如果只有一个点则分裂成两个
    """
    if len(coords_list) == 1:
        # 单个点分裂成两个
        return split_single_point(coords_list[0][0], coords_list[0][1])
    
    if len(coords_list) == 2:
        return coords_list[0], coords_list[1]
    
    # 计算所有点对之间的距离，找到最远的
    max_distance = 0
    farthest_pair = (coords_list[0], coords_list[1])
    
    for i in range(len(coords_list)):
        for j in range(i + 1, len(coords_list)):
            lon1, lat1 = coords_list[i]
            lon2, lat2 = coords_list[j]
            distance = haversine_distance(lon1, lat1, lon2, lat2)
            if distance > max_distance:
                max_distance = distance
                farthest_pair = (coords_list[i], coords_list[j])
    
    return farthest_pair

def read_ic_card_boarding_stats():
    """
    读取所有IC卡消费明细CSV文件，统计每个站点的总上车次数（不区分线路和方向）
    返回：{站点名称: 总上车次数}
    """
    print("\n读取IC卡消费明细...")

    csv_files = sorted(glob.glob(str(IC_DATA_DIR / 'IC卡消费明细查询_*.csv')))
    if not csv_files:
        print("  警告: 未找到IC卡消费明细文件")
        return {}
    
    print(f"  找到 {len(csv_files)} 个CSV文件")
    
    # 统计每个站点的总上车次数（不区分线路和方向，精确匹配站点名称）
    station_boarding_count = defaultdict(int)  # {station_name: 总次数}
    
    total_records = 0
    valid_records = 0
    
    for csv_file in csv_files:
        try:
            with open(csv_file, 'r', encoding='gb18030', errors='ignore') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    total_records += 1
                    
                    station_name = row.get('站点名称', '').strip()
                    
                    if not station_name:
                        continue
                    
                    valid_records += 1
                    # 精确匹配站点名称，直接累加上车次数
                    station_boarding_count[station_name] += 1
                    
        except Exception as e:
            print(f"  警告: 处理文件 {csv_file} 时出错: {e}")
            continue
    
    print(f"  总记录数: {total_records}, 有效记录数: {valid_records}")
    print(f"  统计到 {len(station_boarding_count)} 个唯一站点的上车数据")
    
    return station_boarding_count

def merge_stations(input_file, output_file):
    """
    合并同名站点，计算经纬度平均值，并统计上车次数
    
    Args:
        input_file: 输入CSV文件路径
        output_file: 输出CSV文件路径
    """
    print(f"读取文件: {input_file}")
    
    # 读取IC卡数据，统计每个站点的总上车次数
    station_boarding_count = read_ic_card_boarding_stats()
    
    # 存储每个站点的所有经纬度
    station_coords = defaultdict(list)  # {station_name: [(lon, lat), ...]}
    
    # 读取CSV文件
    with open(input_file, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        total_count = 0
        for row in reader:
            total_count += 1
            station_name = row.get('station_name', '').strip()
            if not station_name:
                continue
            
            try:
                lon = float(row.get('station_lon', '').strip())
                lat = float(row.get('station_lat', '').strip())
                station_coords[station_name].append((lon, lat))
            except (ValueError, TypeError):
                print(f"  警告: 跳过无效坐标的站点 '{station_name}'")
                continue
    
    print(f"  总记录数: {total_count}")
    print(f"  唯一站点数: {len(station_coords)}")
    
    # 对于每个站点名称，选择距离最远的两个点，上车次数平分
    merged_stations = []
    station_id = 1
    for station_name, coords_list in sorted(station_coords.items()):
        if not coords_list:
            continue
        
        # 获取该站点的总上车次数（精确匹配站点名称）
        total_boarding_count = station_boarding_count.get(station_name, 0)
        
        # 找到距离最远的两个点（如果只有一个点，会分裂成两个）
        point1, point2 = find_farthest_pair(coords_list)
        
        # 总是输出两个点，上车次数平分
        boarding_count_per_point = total_boarding_count // 2
        remainder = total_boarding_count % 2  # 余数给第一个点
        
        # 第一个点
        merged_stations.append({
            'station_id': station_id,
            'station_name': station_name,
            'station_lon': point1[0],
            'station_lat': point1[1],
            'occurrence_count': len(coords_list),
            'boarding_count': boarding_count_per_point + remainder  # 余数给第一个点
        })
        station_id += 1
        
        # 第二个点
        merged_stations.append({
            'station_id': station_id,
            'station_name': station_name,
            'station_lon': point2[0],
            'station_lat': point2[1],
            'occurrence_count': len(coords_list),
            'boarding_count': boarding_count_per_point
        })
        station_id += 1
    
    # 写入输出文件
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"\n写入文件: {output_file}")
    with open(output_file, 'w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=['station_id', 'station_name', 'station_lon', 'station_lat', 'occurrence_count', 'boarding_count'])
        writer.writeheader()
        for station in merged_stations:
            writer.writerow({
                'station_id': station['station_id'],
                'station_name': station['station_name'],
                'station_lon': f'{station["station_lon"]:.8f}',
                'station_lat': f'{station["station_lat"]:.8f}',
                'occurrence_count': station['occurrence_count'],
                'boarding_count': station['boarding_count']
            })
    
    # 统计唯一站点名称数
    unique_station_names = len(set(s['station_name'] for s in merged_stations))
    stations_with_two_points = sum(1 for name in set(s['station_name'] for s in merged_stations) 
                                   if sum(1 for s2 in merged_stations if s2['station_name'] == name) == 2)
    
    print(f"  唯一站点名称数: {unique_station_names}")
    print(f"  输出站点记录数: {len(merged_stations)}")
    print(f"  有2个点的站点数: {stations_with_two_points}")
    print(f"  输出文件: {output_file}")
    
    # 显示一些统计信息
    print("\n统计信息:")
    print(f"  出现次数最多的站点（在站点文件中）:")
    # 按站点名称分组统计
    station_occurrence = defaultdict(int)
    for station in merged_stations:
        station_occurrence[station['station_name']] = max(station_occurrence[station['station_name']], 
                                                           station['occurrence_count'])
    sorted_by_occurrence = sorted(station_occurrence.items(), key=lambda x: x[1], reverse=True)
    for i, (name, count) in enumerate(sorted_by_occurrence[:10], 1):
        print(f"    {i}. {name}: {count} 次")
    
    print(f"\n  上车次数最多的站点（IC卡数据，按站点名称汇总）:")
    # 按站点名称汇总上车次数
    station_boarding = defaultdict(int)
    for station in merged_stations:
        station_boarding[station['station_name']] += station['boarding_count']
    sorted_by_boarding = sorted(station_boarding.items(), key=lambda x: x[1], reverse=True)
    for i, (name, count) in enumerate(sorted_by_boarding[:10], 1):
        print(f"    {i}. {name}: {count:,} 次")
    
    # 统计有上车数据的站点数
    stations_with_boarding = sum(1 for s in merged_stations if s['boarding_count'] > 0)
    total_boarding = sum(s['boarding_count'] for s in merged_stations)
    print(f"\n  有上车数据的记录数: {stations_with_boarding} / {len(merged_stations)}")
    print(f"  总上车次数: {total_boarding:,}")
    
    return merged_stations

if __name__ == '__main__':
    input_file = OUTPUT_DIR / 'huangshan.csv'
    output_file = OUTPUT_DIR / 'huangshan_merged_stations.csv'
    
    try:
        merge_stations(input_file, output_file)
        print("\n处理完成！")
    except FileNotFoundError:
        print(f"错误: 找不到文件 {input_file}")
    except Exception as e:
        print(f"错误: {e}")
        import traceback
        traceback.print_exc()

