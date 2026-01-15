#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
将 link_huangshan.json 中的坐标从 OSM (WGS84) 转换为高德坐标系 (GCJ-02)
"""

import json
import math
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
MID_LINK_DIR = REPO_ROOT / "MID_link"
OUTPUT_DIR = REPO_ROOT / "MID_output"

def wgs84_to_gcj02(lon, lat, max_offset_meters=200):
    """
    将WGS84坐标系（OSM/OpenStreetMap使用）转换为GCJ-02坐标（火星坐标系）
    高德地图使用GCJ-02坐标系
    
    如果转换后的偏移超过max_offset_meters，则认为原始数据可能已经是GCJ-02，返回原始坐标
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
    
    def calculate_distance(lon1, lat1, lon2, lat2):
        """计算两点之间的距离（米）"""
        R = 6371000  # 地球半径（米）
        lat1_rad = math.radians(lat1)
        lat2_rad = math.radians(lat2)
        delta_lat = math.radians(lat2 - lat1)
        delta_lon = math.radians(lon2 - lon1)
        a = math.sin(delta_lat / 2) ** 2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(delta_lon / 2) ** 2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        return R * c
    
    d_lat = transform_lat(lon - 105.0, lat - 35.0)
    d_lon = transform_lon(lon - 105.0, lat - 35.0)
    rad_lat = lat / 180.0 * math.pi
    magic = 1 - ee * math.sin(rad_lat) * math.sin(rad_lat)
    sqrt_magic = math.sqrt(magic)
    d_lat = (d_lat * 180.0) / ((a * (1 - ee)) / (magic * sqrt_magic) * math.pi)
    d_lon = (d_lon * 180.0) / (a / sqrt_magic * math.cos(rad_lat) * math.pi)
    mg_lat = lat + d_lat
    mg_lon = lon + d_lon
    
    # 计算偏移距离
    offset_distance = calculate_distance(lon, lat, mg_lon, mg_lat)
    
    # 如果偏移超过阈值，认为原始数据可能已经是GCJ-02，返回原始坐标
    if offset_distance > max_offset_meters:
        return lon, lat
    
    return mg_lon, mg_lat

def convert_coordinates(data):
    """
    转换JSON数据中的所有坐标
    """
    converted_count = 0
    total_links = len(data)
    
    print(f"开始转换 {total_links} 个 link 的坐标...")
    
    for idx, link in enumerate(data):
        if (idx + 1) % 1000 == 0:
            print(f"  已处理 {idx + 1}/{total_links} 个 link...")
        
        # 转换 link_coors（线路坐标数组）
        if 'link_coors' in link and link['link_coors']:
            converted_coors = []
            for coord in link['link_coors']:
                if len(coord) >= 2:
                    lon, lat = coord[0], coord[1]
                    gcj_lon, gcj_lat = wgs84_to_gcj02(lon, lat)
                    converted_coors.append([gcj_lon, gcj_lat])
                    converted_count += 1
            link['link_coors'] = converted_coors
        
        # 转换 start_coor（起点坐标）
        if 'start_coor' in link and link['start_coor']:
            if len(link['start_coor']) >= 2:
                lon, lat = link['start_coor'][0], link['start_coor'][1]
                gcj_lon, gcj_lat = wgs84_to_gcj02(lon, lat)
                link['start_coor'] = [gcj_lon, gcj_lat]
                converted_count += 1
        
        # 转换 end_coor（终点坐标）
        if 'end_coor' in link and link['end_coor']:
            if len(link['end_coor']) >= 2:
                lon, lat = link['end_coor'][0], link['end_coor'][1]
                gcj_lon, gcj_lat = wgs84_to_gcj02(lon, lat)
                link['end_coor'] = [gcj_lon, gcj_lat]
                converted_count += 1
    
    print(f"\n转换完成！共转换了 {converted_count} 个坐标点")
    return data

def main():
    input_file = MID_LINK_DIR / 'link_huangshan.json'
    output_file = OUTPUT_DIR / 'link_huangshan_gcj02.json'
    
    print("=" * 60)
    print("Link 坐标转换工具：WGS84 (OSM) -> GCJ-02 (高德)")
    print("=" * 60)
    
    try:
        # 读取JSON文件
        print(f"\n正在读取文件: {input_file}...")
        with open(input_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        print(f"成功读取 {len(data)} 个 link")
        
        # 转换坐标
        converted_data = convert_coordinates(data)
        
        # 保存转换后的数据
        print(f"\n正在保存转换后的数据到: {output_file}...")
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(converted_data, f, ensure_ascii=False, indent=2)
        
        print(f"\n✓ 转换完成！输出文件: {output_file}")
        print("=" * 60)
        
    except FileNotFoundError:
        print(f"错误: 找不到文件 {input_file}")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"错误: JSON 解析失败 - {e}")
        sys.exit(1)
    except Exception as e:
        print(f"错误: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == '__main__':
    main()

