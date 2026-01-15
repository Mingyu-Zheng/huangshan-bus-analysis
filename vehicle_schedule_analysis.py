#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
从IC刷卡记录推断公交车辆数和班次信息
- 车辆数：根据车牌号统计（每个线路方向的不同车牌号）
- 班次数：同一辆车在一天内，如果站点序号发生倒退，则认为是新班次
"""

import csv
import glob
import os
from pathlib import Path
from collections import defaultdict
from datetime import datetime
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib import font_manager

REPO_ROOT = Path(__file__).resolve().parent
IC_DATA_DIR = REPO_ROOT / "INIT_IC_data"
OUTPUT_DIR = REPO_ROOT / "MID_output"
MID_STATION_DIR = REPO_ROOT / "MID_station"
TRIP_CHARTS_DIR = REPO_ROOT / "OUT_trip_charts"
ANALYSIS_DIR = REPO_ROOT / "OUT_analysis"

# 设置中文字体
plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'Arial Unicode MS']
plt.rcParams['axes.unicode_minus'] = False

def read_ic_card_data():
    """读取所有IC卡消费明细CSV文件"""
    print("读取IC卡消费明细...")

    csv_files = sorted(glob.glob(str(IC_DATA_DIR / 'IC卡消费明细查询_*.csv')))
    if not csv_files:
        print("  警告: 未找到IC卡消费明细文件")
        return []
    
    print(f"  找到 {len(csv_files)} 个CSV文件")
    
    records = []
    total_records = 0
    
    for csv_file in csv_files:
        try:
            with open(csv_file, 'r', encoding='gb18030', errors='ignore') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    total_records += 1
                    
                    # 提取关键字段
                    card_type = row.get('卡别', '').strip()
                    route_num = row.get('线路', '').strip()
                    direction = row.get('上下行', '').strip()
                    station_name = row.get('站点名称', '').strip()
                    station_seq_str = row.get('站点序号', '').strip()
                    plate_number = row.get('车牌号', '').strip()
                    date_str = row.get('日期', '').strip()
                    time_str = row.get('时间', '').strip()
                    
                    # 过滤司机卡
                    if card_type and ('司机' in card_type or '司機' in card_type):
                        continue
                    
                    if not route_num or not station_name or not plate_number:
                        continue
                    
                    # 处理上下行：0或空=上行/去程，1=下行/返程
                    if direction == '' or direction == '0':
                        direction_key = 'forward'
                    elif direction == '1':
                        direction_key = 'reverse'
                    else:
                        direction_key = 'forward' if direction in ['', '0', '上行', '去程'] else 'reverse'
                    
                    # 解析站点序号
                    try:
                        station_seq = int(station_seq_str) if station_seq_str else 0
                    except ValueError:
                        station_seq = 0
                    
                    # 解析日期和时间
                    try:
                        if len(date_str) == 8:  # YYYYMMDD格式
                            date_obj = datetime.strptime(date_str, '%Y%m%d').date()
                        else:
                            continue
                        
                        if len(time_str) >= 6:  # HHMMSS格式
                            time_obj = datetime.strptime(time_str[:6], '%H%M%S').time()
                        else:
                            continue
                        
                        # 筛除时间早于5:00和晚于23:00的数据
                        hour = time_obj.hour
                        if hour < 5 or hour >= 23:
                            continue
                        
                        datetime_obj = datetime.combine(date_obj, time_obj)
                    except (ValueError, TypeError):
                        continue
                    
                    records.append({
                        'route': route_num,
                        'direction': direction_key,
                        'station_name': station_name,
                        'station_seq': station_seq,
                        'plate_number': plate_number,
                        'datetime': datetime_obj,
                        'date': date_obj
                    })
                    
        except Exception as e:
            print(f"  警告: 处理文件 {csv_file} 时出错: {e}")
            continue
    
    print(f"  总记录数: {total_records}, 有效记录数: {len(records)}")
    return records

def load_route_stations():
    """加载线路站点信息，用于验证站点序号"""
    print("加载线路站点信息...")

    try:
        # 从 MID_output 读取
        station_file = OUTPUT_DIR / 'huangshan.csv'

        with open(str(station_file), 'r', encoding='utf-8', errors='ignore') as f:
            reader = csv.DictReader(f)
            route_stations = defaultdict(lambda: defaultdict(dict))  # {route: {direction: {station_name: seq}}}
            
            for row in reader:
                route = (row.get('line_id') or '').strip().lstrip('0') or row.get('line_id', '').strip()
                direction_raw = (row.get('direction') or '').strip()
                station_name = (row.get('station_name') or '').strip()
                station_seq_str = (row.get('station_id') or '').strip()
                
                if not route or not station_name:
                    continue
                
                direction_key = 'reverse' if direction_raw in ['B', 'b', '返程', '下行', '1'] else 'forward'
                
                try:
                    station_seq = int(station_seq_str) if station_seq_str else 0
                except ValueError:
                    continue
                
                route_stations[route][direction_key][station_name] = station_seq
            
            print(f"  加载了 {len(route_stations)} 条线路的站点信息")
            return route_stations
    except Exception as e:
        print(f"  警告: 加载站点信息失败: {e}")
        return defaultdict(lambda: defaultdict(dict))

def normalize_route_name(route_name):
    """标准化线路名称"""
    if not route_name:
        return ''
    route_name = str(route_name).strip()
    # 去掉前导0
    route_name = route_name.lstrip('0') or route_name
    return route_name

def analyze_vehicles_and_trips(records, route_stations):
    """
    分析车辆数和班次信息
    只处理 route_stations 中包含的线路
    """
    print("\n分析车辆数和班次信息...")
    
    # 统计过滤前后的记录数
    total_records = len(records)
    filtered_records = 0
    
    # 按线路、方向、车牌号、日期分组
    # {(route, direction, plate, date): [records]}
    grouped = defaultdict(list)
    
    for record in records:
        route = normalize_route_name(record['route'])
        direction = record['direction']
        
        # 只处理 route_stations 中包含的线路和方向
        if route not in route_stations or direction not in route_stations[route]:
            continue
        
        filtered_records += 1
        key = (route, direction, record['plate_number'], record['date'])
        grouped[key].append(record)
    
    print(f"  总记录数: {total_records}, 过滤后记录数: {filtered_records} (只处理 route_stations 中的线路)")
    
    # 统计结果
    vehicle_stats = defaultdict(set)  # {(route, direction): {plate_numbers}}
    trip_stats = defaultdict(int)  # {(route, direction, plate, date): trip_count}
    detailed_trips = []  # 存储每个班次的详细信息
    
    for (route, direction, plate, date), day_records in grouped.items():
        # 按时间排序
        day_records.sort(key=lambda x: x['datetime'])
        
        # 统计车辆
        vehicle_stats[(route, direction)].add(plate)
        
        # 检测班次：根据方向判断序号是否发生倒退，或时间间隔超过20分钟且站点相同
        # 正向线路：序号应该递增，如果变小则是新班次
        # 反向线路：序号应该递减，如果变大则是新班次
        # 如果相邻记录时间间隔超过20分钟且是同一个站点，也认为是新班次
        current_trip = 1
        last_seq = None
        last_datetime = None
        last_station_name = None
        
        trip_start_time = day_records[0]['datetime']
        trip_stations = []
        trip_boarding_count = 0  # 当前班次的上车次数
        
        for i, record in enumerate(day_records):
            current_seq = record['station_seq']
            current_datetime = record['datetime']
            current_station_name = record['station_name']
            
            # 判断是否发生倒退
            is_backward = False
            if last_seq is not None:
                if direction == 'forward':
                    # 正向：序号变小表示倒退
                    is_backward = current_seq < last_seq
                else:
                    # 反向：序号变大表示倒退（因为反向线路序号是递减的）
                    is_backward = current_seq > last_seq
            
            # 判断时间间隔是否超过20分钟且站点相同
            time_gap_too_long = False
            if last_datetime is not None and last_station_name is not None:
                time_diff = (current_datetime - last_datetime).total_seconds()
                # 时间间隔超过20分钟且是同一个站点
                if time_diff > 1800 and current_station_name == last_station_name:  # 20分钟 = 1200秒
                    time_gap_too_long = True
                elif time_diff > 3600 * 2: # 2小时
                    time_gap_too_long = True
            
            # 如果站点序号倒退或（时间间隔超过20分钟且站点相同），开始新班次
            if is_backward or time_gap_too_long:
                # 保存上一个班次
                if trip_stations:
                    detailed_trips.append({
                        'route': route,
                        'direction': direction,
                        'plate': plate,
                        'date': date,
                        'trip': current_trip,
                        'start_time': trip_start_time,
                        'end_time': day_records[i-1]['datetime'],
                        'station_count': len(trip_stations),
                        'boarding_count': trip_boarding_count,
                        'stations': trip_stations.copy()
                    })
                
                # 开始新班次
                current_trip += 1
                trip_start_time = record['datetime']
                trip_stations = [record['station_name']]
                trip_boarding_count = 1  # 新班次的第一条记录
            else:
                # 继续当前班次
                if record['station_name'] not in trip_stations:
                    trip_stations.append(record['station_name'])
                trip_boarding_count += 1  # 增加上车次数
            
            last_seq = current_seq
            last_datetime = current_datetime
            last_station_name = current_station_name
        
        # 保存最后一个班次
        if trip_stations:
            detailed_trips.append({
                'route': route,
                'direction': direction,
                'plate': plate,
                'date': date,
                'trip': current_trip,
                'start_time': trip_start_time,
                'end_time': day_records[-1]['datetime'],
                'station_count': len(trip_stations),
                'boarding_count': trip_boarding_count,
                'stations': trip_stations.copy()
            })
        
        trip_stats[(route, direction, plate, date)] = current_trip
    
    return vehicle_stats, trip_stats, detailed_trips

def identify_peak_hours(detailed_trips, route, direction, selected_date=None):
    """
    识别指定线路方向的高峰时段
    算法：
    1. 按小时统计上车次数，取排名前30%的时段作为初始高峰时段
    2. 检查高峰时段两侧的一小时，如果接近高峰标准（放宽5%），也纳入高峰时段
    
    Args:
        detailed_trips: 班次详情列表
        route: 线路号
        direction: 方向
        selected_date: 指定日期（如果提供，只使用该日期的数据）
    """
    # 按小时统计该线路方向的上车次数
    hourly_boarding = defaultdict(int)  # {hour: total_boarding_count}
    hourly_trips = defaultdict(int)  # {hour: trip_count}
    
    for trip in detailed_trips:
        if trip['route'] == route and trip['direction'] == direction:
            # 如果指定了日期，只使用该日期的数据
            if selected_date is not None and trip['date'] != selected_date:
                continue
            hour = trip['start_time'].hour
            hourly_boarding[hour] += trip.get('boarding_count', 0)
            hourly_trips[hour] += 1
    
    if not hourly_boarding:
        return set()
    
    # 第一步：按上车次数排序，取前30%的时段作为初始高峰时段
    sorted_hours = sorted(hourly_boarding.items(), key=lambda x: -x[1])
    top_count = max(1, int(len(sorted_hours) * 0.25))  # 前30%
    peak_hours = {hour for hour, _ in sorted_hours[:top_count]}
    
    # 计算初始高峰时段的最小上车次数作为阈值
    if not peak_hours:
        return set()
    
    peak_boarding_values = [hourly_boarding[hour] for hour in peak_hours]
    min_peak_boarding = min(peak_boarding_values)
    # 放宽5%的阈值
    relaxed_threshold = min_peak_boarding * 0.95
    
    # 第二步：检查高峰时段两侧的一小时，如果接近高峰标准，也纳入高峰时段
    # 需要迭代检查，因为新加入的时段也可能有相邻时段需要检查
    changed = True
    while changed:
        changed = False
        # 获取所有可能的小时范围（从数据中获取）
        all_hours = set(hourly_boarding.keys())
        
        for hour in list(peak_hours):
            # 检查前一个小时
            prev_hour = hour - 1
            if prev_hour in all_hours and prev_hour not in peak_hours:
                if hourly_boarding[prev_hour] >= relaxed_threshold:
                    peak_hours.add(prev_hour)
                    changed = True
            
            # 检查后一个小时
            next_hour = hour + 1
            if next_hour in all_hours and next_hour not in peak_hours:
                if hourly_boarding[next_hour] >= relaxed_threshold:
                    peak_hours.add(next_hour)
                    changed = True
    
    return peak_hours

def calculate_peak_offpeak_stats(detailed_trips, route, direction, selected_date=None, peak_hours=None):
    """
    计算高峰和平峰时段的平均每班客流
    
    Args:
        detailed_trips: 班次详情列表
        route: 线路号
        direction: 方向
        selected_date: 指定日期（如果提供，只使用该日期的数据）
        peak_hours: 高峰时段集合（如果提供，直接使用，避免重复计算）
    """
    # 如果未提供高峰时段，则计算（如果指定了日期，使用该日期的数据）
    if peak_hours is None:
        peak_hours = identify_peak_hours(detailed_trips, route, direction, selected_date)
    
    # 统计高峰和平峰时段的班次和客流
    peak_trips = []
    offpeak_trips = []
    
    for trip in detailed_trips:
        if trip['route'] == route and trip['direction'] == direction:
            # 如果指定了日期，只使用该日期的数据
            if selected_date is not None and trip['date'] != selected_date:
                continue
            hour = trip['start_time'].hour
            boarding_count = trip.get('boarding_count', 0)
            
            if hour in peak_hours:
                peak_trips.append(boarding_count)
            else:
                offpeak_trips.append(boarding_count)
    
    # 计算平均值
    avg_peak_boarding = sum(peak_trips) / len(peak_trips) if peak_trips else 0
    avg_offpeak_boarding = sum(offpeak_trips) / len(offpeak_trips) if offpeak_trips else 0
    
    return {
        'peak_hours': sorted(peak_hours),
        'peak_trip_count': len(peak_trips),
        'avg_peak_boarding': avg_peak_boarding,
        'offpeak_trip_count': len(offpeak_trips),
        'avg_offpeak_boarding': avg_offpeak_boarding
    }

def print_statistics(vehicle_stats, trip_stats, detailed_trips, peak_stats_cache=None):
    """
    打印统计结果
    
    Args:
        vehicle_stats: 车辆统计
        trip_stats: 班次统计
        detailed_trips: 详细班次列表
        peak_stats_cache: 高峰/平峰统计缓存 {(route, direction): peak_stats}
    """
    print("\n" + "=" * 80)
    print("车辆数和班次统计结果")
    print("=" * 80)
    
    # 按线路和方向排序
    sorted_routes = sorted(vehicle_stats.keys(), key=lambda x: (int(x[0]) if x[0].isdigit() else 999, x[1]))
    
    for route, direction in sorted_routes:
        vehicles = vehicle_stats[(route, direction)]
        vehicle_count = len(vehicles)
        
        dir_label = "正向" if direction == 'forward' else "反向"
        
        # 统计该线路方向的总班次数和天数
        total_trips = 0
        trips_by_vehicle = defaultdict(int)
        days_with_data = set()  # 记录有数据的天数
        vehicles_per_day = defaultdict(set)  # 每天实际出车的车辆
        
        for (r, d, plate, date), trip_count in trip_stats.items():
            if r == route and d == direction:
                total_trips += trip_count
                trips_by_vehicle[plate] += trip_count
                days_with_data.add(date)
                vehicles_per_day[date].add(plate)
        
        days_count = len(days_with_data)
        avg_trips_per_day = total_trips / days_count if days_count > 0 else 0
        
        # 计算每天平均车辆数（每天实际出车的车辆数的平均值）
        total_vehicles_days = sum(len(vehicles) for vehicles in vehicles_per_day.values())
        avg_vehicles_per_day = total_vehicles_days / days_count if days_count > 0 else 0
        avg_trips_per_vehicle_per_day = avg_trips_per_day / avg_vehicles_per_day if avg_vehicles_per_day > 0 else 0
        
        print(f"\n线路 {route} ({dir_label}):")
        print(f"  每天平均车辆数: {avg_vehicles_per_day:.1f}")
        print(f"  数据天数: {days_count}")
        print(f"  总班次数: {total_trips}")
        print(f"  每天平均班次数: {avg_trips_per_day:.1f}")
        if avg_vehicles_per_day > 0:
            print(f"  每车每天平均班次数: {avg_trips_per_vehicle_per_day:.1f}")
        
        # 使用缓存的高峰/平峰统计
        if peak_stats_cache is not None:
            peak_stats = peak_stats_cache.get((route, direction))
        else:
            peak_stats = calculate_peak_offpeak_stats(detailed_trips, route, direction)
        
        if peak_stats and peak_stats['peak_hours']:
            peak_hours_str = ', '.join([f'{h}:00' for h in peak_stats['peak_hours']])
            print(f"  高峰时段: {peak_hours_str}")
            print(f"  高峰平均每班客流: {peak_stats['avg_peak_boarding']:.1f} (共{peak_stats['peak_trip_count']}班次)")
            print(f"  平峰平均每班客流: {peak_stats['avg_offpeak_boarding']:.1f} (共{peak_stats['offpeak_trip_count']}班次)")
        
        # 显示每辆车的总班次数和每天平均班次数
        if trips_by_vehicle:
            print(f"  各车辆班次统计:")
            for plate, trips in sorted(trips_by_vehicle.items(), key=lambda x: -x[1]):
                vehicle_days = len([d for (r, d, p, date) in trip_stats.keys() 
                                   if r == route and d == direction and p == plate])
                avg_per_day = trips / vehicle_days if vehicle_days > 0 else 0
                print(f"    {plate}: 总 {trips} 班次, {vehicle_days} 天, 平均每天 {avg_per_day:.1f} 班次")

def export_statistics_summary(vehicle_stats, trip_stats, detailed_trips, peak_stats_cache=None, selected_date=None):
    """
    导出汇总统计信息到CSV
    只统计选定日期的班次数据，但每天平均车辆数从所有数据计算
    
    Args:
        vehicle_stats: 车辆统计
        trip_stats: 班次统计
        detailed_trips: 详细班次列表
        peak_stats_cache: 高峰/平峰统计缓存 {(route, direction): peak_stats}
        selected_date: 选定日期（如果提供，只统计该日期的班次数据）
    """
    print("\n导出汇总统计信息...")

    # 确保输出目录存在
    ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)

    output_file = ANALYSIS_DIR / 'vehicle_statistics_summary.csv'

    with open(output_file, 'w', encoding='utf-8-sig', newline='') as f:
        writer = csv.writer(f)
        writer.writerow([
            '线路', '方向', '总车辆数', '每天平均车辆数', '选定日期车辆数', '数据天数', '总班次数', 
            '每天平均班次数', '每车每天平均班次数', '高峰时段', 
            '高峰平均每班客流', '高峰班次数', '平峰平均每班客流', '平峰班次数'
        ])
        
        # 按线路和方向排序
        sorted_routes = sorted(vehicle_stats.keys(), key=lambda x: (int(x[0]) if x[0].isdigit() else 999, x[1]))
        
        for route, direction in sorted_routes:
            vehicles = vehicle_stats[(route, direction)]
            vehicle_count = len(vehicles)
            
            dir_label = "正向" if direction == 'forward' else "反向"
            
            # 计算每天平均车辆数（从所有数据计算，不受selected_date影响）
            vehicles_per_day_all = defaultdict(set)  # 每天实际出车的车辆（所有数据）
            for (r, d, plate, date), trip_count in trip_stats.items():
                if r == route and d == direction:
                    vehicles_per_day_all[date].add(plate)
            
            days_count_all = len(vehicles_per_day_all)
            total_vehicles_days = sum(len(vehicles) for vehicles in vehicles_per_day_all.values())
            avg_vehicles_per_day = total_vehicles_days / days_count_all if days_count_all > 0 else 0
            
            # 统计选定日期的班次数据（如果指定了日期）
            if selected_date is not None:
                # 只统计选定日期的班次
                total_trips = 0
                trips_by_vehicle = defaultdict(int)
                vehicles_on_selected_date = set()
                
                for (r, d, plate, date), trip_count in trip_stats.items():
                    if r == route and d == direction and date == selected_date:
                        total_trips += trip_count
                        trips_by_vehicle[plate] += trip_count
                        vehicles_on_selected_date.add(plate)
                
                # 班次数据只统计选定日期，但数据天数显示所有数据的天数（用于计算每天平均车辆数）
                days_count = days_count_all  # 显示所有数据的天数
                avg_trips_per_day = total_trips  # 当天的班次数（只统计选定日期）
                avg_trips_per_vehicle_per_day = avg_trips_per_day / len(vehicles_on_selected_date) if vehicles_on_selected_date else 0
                vehicles_on_selected_date_count = len(vehicles_on_selected_date)
            else:
                # 统计所有日期的班次数据
                total_trips = 0
                trips_by_vehicle = defaultdict(int)
                days_with_data = set()
                
                for (r, d, plate, date), trip_count in trip_stats.items():
                    if r == route and d == direction:
                        total_trips += trip_count
                        trips_by_vehicle[plate] += trip_count
                        days_with_data.add(date)
                
                days_count = len(days_with_data)
                avg_trips_per_day = total_trips / days_count if days_count > 0 else 0
                avg_trips_per_vehicle_per_day = avg_trips_per_day / avg_vehicles_per_day if avg_vehicles_per_day > 0 else 0
                vehicles_on_selected_date_count = ''  # 未指定日期时为空
            
            # 使用缓存的高峰/平峰统计
            if peak_stats_cache is not None:
                peak_stats = peak_stats_cache.get((route, direction))
            else:
                peak_stats = calculate_peak_offpeak_stats(detailed_trips, route, direction)
            
            if peak_stats is None:
                peak_stats = {'peak_hours': [], 'peak_trip_count': 0, 'avg_peak_boarding': 0, 
                             'offpeak_trip_count': 0, 'avg_offpeak_boarding': 0}
            
            peak_hours_str = ', '.join([f'{h}:00' for h in peak_stats['peak_hours']]) if peak_stats['peak_hours'] else ''
            
            writer.writerow([
                route,
                dir_label,
                vehicle_count,  # 总车辆数
                f'{avg_vehicles_per_day:.1f}',  # 每天平均车辆数
                vehicles_on_selected_date_count if selected_date is not None else '',  # 选定日期车辆数
                days_count,
                total_trips,
                f'{avg_trips_per_day:.1f}',
                f'{avg_trips_per_vehicle_per_day:.1f}',
                peak_hours_str,
                f'{peak_stats["avg_peak_boarding"]:.1f}',
                peak_stats['peak_trip_count'],
                f'{peak_stats["avg_offpeak_boarding"]:.1f}',
                peak_stats['offpeak_trip_count']
            ])
    
    # 导出各车辆详细统计
    output_file_vehicles = ANALYSIS_DIR / 'vehicle_statistics_by_vehicle.csv'

    with open(output_file_vehicles, 'w', encoding='utf-8-sig', newline='') as f:
        writer = csv.writer(f)
        writer.writerow([
            '线路', '方向', '车牌号', '总班次数', '数据天数', '平均每天班次数'
        ])
        
        for route, direction in sorted_routes:
            dir_label = "正向" if direction == 'forward' else "反向"
            
            # 统计每辆车的班次
            trips_by_vehicle = defaultdict(int)
            days_by_vehicle = defaultdict(set)
            
            for (r, d, plate, date), trip_count in trip_stats.items():
                if r == route and d == direction:
                    trips_by_vehicle[plate] += trip_count
                    days_by_vehicle[plate].add(date)
            
            # 按总班次数排序
            for plate, trips in sorted(trips_by_vehicle.items(), key=lambda x: -x[1]):
                vehicle_days = len(days_by_vehicle[plate])
                avg_per_day = trips / vehicle_days if vehicle_days > 0 else 0
                
                writer.writerow([
                    route,
                    dir_label,
                    plate,
                    trips,
                    vehicle_days,
                    f'{avg_per_day:.1f}'
                ])
    
    print(f"  汇总统计信息已导出到: {output_file}")
    print(f"  各车辆统计信息已导出到: {output_file_vehicles}")

def plot_trip_boarding_charts(detailed_trips, vehicle_stats, selected_date, peak_stats_cache=None):
    """
    为每个线路方向绘制班次开始时间-班次总上车次数的折线图
    
    Args:
        detailed_trips: 详细班次列表
        vehicle_stats: 车辆统计
        selected_date: 选定的日期（用于绘图和统计）
        peak_stats_cache: 高峰/平峰统计缓存 {(route, direction): peak_stats}
    """
    print("\n绘制班次上车次数折线图...")
    
    if not detailed_trips:
        print("  警告: 没有班次数据")
        return
    
    if selected_date is None:
        print("  警告: 未指定日期")
        return
    
    print(f"  使用日期: {selected_date.strftime('%Y-%m-%d')}")
    
    # 按线路方向分组（使用选定日期）
    route_direction_trips = defaultdict(list)
    for trip in detailed_trips:
        if trip['date'] == selected_date:
            key = (trip['route'], trip['direction'])
            route_direction_trips[key].append(trip)
    
    # 计算每个线路方向的每天平均车辆数（从所有数据计算）
    route_direction_avg_vehicles = {}
    for route, direction in vehicle_stats.keys():
        vehicles_per_day = defaultdict(set)
        for trip in detailed_trips:
            if trip['route'] == route and trip['direction'] == direction:
                vehicles_per_day[trip['date']].add(trip['plate'])
        
        if vehicles_per_day:
            total_vehicles_days = sum(len(vehicles) for vehicles in vehicles_per_day.values())
            days_count = len(vehicles_per_day)
            avg_vehicles = total_vehicles_days / days_count if days_count > 0 else 0
            route_direction_avg_vehicles[(route, direction)] = avg_vehicles
    
    # 创建输出目录
    output_dir = TRIP_CHARTS_DIR
    os.makedirs(output_dir, exist_ok=True)
    
    # 为每个线路方向绘制图表
    for (route, direction), trips in sorted(route_direction_trips.items(), 
                                            key=lambda x: (int(x[0][0]) if x[0][0].isdigit() else 999, x[0][1])):
        # 按开始时间排序
        trips.sort(key=lambda x: x['start_time'])
        
        # 提取时间和上车次数
        times = [trip['start_time'] for trip in trips]
        boarding_counts = [trip.get('boarding_count', 0) for trip in trips]
        
        # 按小时统计总上车次数
        hourly_boarding = defaultdict(int)  # {hour: total_boarding}
        hourly_times = []  # 存储每个小时的时间点（用于绘图）
        hourly_counts = []  # 存储每个小时的总上车次数
        
        for trip in trips:
            hour = trip['start_time'].hour
            hourly_boarding[hour] += trip.get('boarding_count', 0)
        
        # 按小时排序，生成小时折线数据
        from datetime import time
        for hour in sorted(hourly_boarding.keys()):
            # 将点绘制在每个小时段的中间（后移半小时）
            hour_time = datetime.combine(selected_date, time(hour, 30, 0))
            hourly_times.append(hour_time)
            hourly_counts.append(hourly_boarding[hour])
        
        # 统计车辆数（该线路方向当天出现的不同车牌号）
        vehicles_in_day = set()
        trips_by_vehicle = defaultdict(int)
        for trip in trips:
            vehicles_in_day.add(trip['plate'])
            trips_by_vehicle[trip['plate']] += 1
        
        vehicle_count = len(vehicles_in_day)
        
        # 获取该线路方向的每天平均车辆数
        avg_vehicles_per_day = route_direction_avg_vehicles.get((route, direction), 0)
        
        # 从缓存中获取高峰时段和统计（避免重复计算）
        if peak_stats_cache is not None:
            peak_stats = peak_stats_cache.get((route, direction))
            if peak_stats:
                peak_hours = set(peak_stats['peak_hours'])  # 转换为set用于in操作
            else:
                peak_hours = set()
                peak_stats = {'peak_hours': [], 'peak_trip_count': 0, 'avg_peak_boarding': 0, 
                             'offpeak_trip_count': 0, 'avg_offpeak_boarding': 0}
        else:
            # 如果缓存不存在，才计算（不应该发生）
            peak_hours = identify_peak_hours(detailed_trips, route, direction, selected_date)
            peak_stats = calculate_peak_offpeak_stats(detailed_trips, route, direction, selected_date, peak_hours)
        
        # 创建图表
        fig, ax = plt.subplots(figsize=(14, 6))
        
        # 绘制班次折线图（每个班次一个点）
        ax.plot(times, boarding_counts, marker='o', linewidth=2, markersize=4, 
                label='班次上车次数', color='#3498db', alpha=0.7)
        
        # 绘制小时汇总折线图（每个小时一个点）
        if hourly_times:
            ax.plot(hourly_times, hourly_counts, marker='s', linewidth=2.5, markersize=8, 
                    label='每小时总上车次数', color='#e74c3c', alpha=0.9)
        
        # 绘制高峰时段背景（如果有高峰时段）
        if peak_hours and times:
            y_min, y_max = ax.get_ylim()
            peak_label_added = False
            for hour in sorted(peak_hours):
                # 找到该小时的时间范围
                hour_start = datetime.combine(selected_date, time(hour, 0, 0))
                hour_end = datetime.combine(selected_date, time(hour+1, 0, 0))
                # 绘制背景色
                label = '高峰时段' if not peak_label_added else ''
                ax.axvspan(hour_start, hour_end, alpha=0.15, color='red', label=label, zorder=0)
                if not peak_label_added:
                    peak_label_added = True
        
        # 设置标题和标签
        dir_label = "正向" if direction == 'forward' else "反向"
        ax.set_title(f'线路 {route} ({dir_label}) - 班次上车次数趋势\n日期: {selected_date.strftime("%Y-%m-%d")}', 
                    fontsize=14, fontweight='bold')
        ax.set_xlabel('时间', fontsize=12)
        ax.set_ylabel('上车次数', fontsize=12)
        ax.grid(True, alpha=0.3)
        
        # 添加图例
        ax.legend(loc='best', fontsize=10)
        
        # 格式化x轴时间显示
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
        ax.xaxis.set_major_locator(mdates.HourLocator(interval=2))
        plt.xticks(rotation=45)
        
        # 添加统计信息
        total_trips = len(trips)
        total_boarding = sum(boarding_counts)
        avg_boarding = total_boarding / total_trips if total_trips > 0 else 0
        max_boarding = max(boarding_counts) if boarding_counts else 0
        
        # 统计信息文本（包含车辆数和高峰时段信息）
        stats_text = (f'每天平均车辆数: {avg_vehicles_per_day:.1f} | 当天出车: {vehicle_count} | '
                     f'总班次: {total_trips} | 总上车: {total_boarding} | '
                     f'平均: {avg_boarding:.1f} | 最大: {max_boarding}')
        
        # 添加高峰时段信息
        if peak_hours:
            peak_hours_str = ', '.join([f'{h}:00' for h in sorted(peak_hours)])
            stats_text += f'\n高峰时段: {peak_hours_str}'
            stats_text += f' | 高峰平均: {peak_stats["avg_peak_boarding"]:.1f} ({peak_stats["peak_trip_count"]}班次)'
            stats_text += f' | 平峰平均: {peak_stats["avg_offpeak_boarding"]:.1f} ({peak_stats["offpeak_trip_count"]}班次)'
        
        ax.text(0.02, 0.98, stats_text, transform=ax.transAxes, 
                fontsize=10, verticalalignment='top',
                bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.5))
        
        # 在图表右侧添加车辆班次分布信息（如果车辆数不太多）
        if vehicle_count <= 10 and vehicle_count > 0:
            vehicle_info = []
            for plate, trip_count in sorted(trips_by_vehicle.items(), key=lambda x: -x[1]):
                vehicle_info.append(f'{plate}: {trip_count}班次')
            
            vehicle_text = '车辆班次分布:\n' + '\n'.join(vehicle_info)
            ax.text(0.98, 0.98, vehicle_text, transform=ax.transAxes,
                   fontsize=9, verticalalignment='top', horizontalalignment='right',
                   bbox=dict(boxstyle='round', facecolor='lightblue', alpha=0.5))
        
        plt.tight_layout()
        
        # 保存图表
        filename = f'{output_dir}/route_{route}_{direction}_{selected_date.strftime("%Y%m%d")}.png'
        plt.savefig(filename, dpi=150, bbox_inches='tight')
        plt.close()
        
        print(f"  ✓ 线路 {route} ({dir_label}): {total_trips} 个班次, 已保存到 {filename}")
    
    print(f"\n所有图表已保存到目录: {output_dir}/")

def main():
    print("=" * 80)
    print("公交车辆数和班次分析工具")
    print("=" * 80)
    
    # 读取IC卡数据
    records = read_ic_card_data()
    if not records:
        print("错误: 没有有效的IC卡记录")
        return
    
    # 加载线路站点信息
    route_stations = load_route_stations()
    
    # 分析车辆数和班次
    vehicle_stats, trip_stats, detailed_trips = analyze_vehicles_and_trips(records, route_stations)
    
    # 获取所有线路方向的集合
    all_route_directions = set(vehicle_stats.keys())
    total_route_directions = len(all_route_directions)
    print(f"\n共有 {total_route_directions} 个线路方向")
    
    # 统计每天的数据：总上车次数和包含的线路方向
    date_boarding = defaultdict(int)  # {date: total_boarding_count}
    date_route_directions = defaultdict(set)  # {date: set of (route, direction)}
    
    for trip in detailed_trips:
        date = trip['date']
        date_boarding[date] += trip.get('boarding_count', 0)
        date_route_directions[date].add((trip['route'], trip['direction']))
    
    if not date_boarding:
        print("错误: 没有日期数据")
        return
    
    # 找出包含所有线路方向数据的日期
    complete_dates = []
    for date, route_dirs in date_route_directions.items():
        if route_dirs == all_route_directions:
            complete_dates.append(date)
    
    if not complete_dates:
        print(f"警告: 没有包含所有 {total_route_directions} 个线路方向数据的日期")
        # 如果没有完全包含所有线路方向的日期，选择包含最多线路方向的日期
        max_route_dirs = max(len(route_dirs) for route_dirs in date_route_directions.values())
        complete_dates = [date for date, route_dirs in date_route_directions.items() 
                         if len(route_dirs) == max_route_dirs]
        print(f"  选择包含 {max_route_dirs} 个线路方向的日期")
    
    # 在包含所有线路方向的日期中，选择总上车次数最多的一天
    selected_date = max(complete_dates, key=lambda d: date_boarding[d])
    total_boarding = date_boarding[selected_date]
    route_direction_count = len(date_route_directions[selected_date])
    print(f"\n选择日期: {selected_date.strftime('%Y-%m-%d')} (总上车次数: {total_boarding}, 包含 {route_direction_count} 个线路方向)")
    
    # 统一计算所有线路方向的高峰/平峰统计（使用选定的日期）
    print("\n计算高峰/平峰统计...")
    peak_stats_cache = {}
    for route, direction in vehicle_stats.keys():
        peak_stats = calculate_peak_offpeak_stats(detailed_trips, route, direction, selected_date)
        peak_stats_cache[(route, direction)] = peak_stats
    print(f"  已计算 {len(peak_stats_cache)} 个线路方向的高峰/平峰统计")
    
    
    # 导出汇总统计信息（使用缓存和选定日期）
    export_statistics_summary(vehicle_stats, trip_stats, detailed_trips, peak_stats_cache, selected_date)
    
    # 绘制班次上车次数折线图（使用选定日期和缓存）
    plot_trip_boarding_charts(detailed_trips, vehicle_stats, selected_date, peak_stats_cache)
    
    print("\n" + "=" * 80)
    print("分析完成！")
    print("=" * 80)

if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print(f"\n错误: {e}")
        import traceback
        traceback.print_exc()

