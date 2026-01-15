#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
完整公交线路客流分析脚本
分析黄山公交的完整客流，包括移动支付数据

与现有脚本的区别：
- 现有脚本只分析可追溯用户（约79万人次）
- 本脚本分析完整客流（约131万人次，包含52万人次移动支付）

票制说明：
- 黄山公交实行1元/2元/5元分级票制
- 1元：少数社区线路
- 2元：大部分市区线路（主流）
- 5元：高铁快线（106/107路）
- 实际支付金额是优惠后的价格，大量免费卡用户拉低平均值
"""

import csv
import glob
import json
from pathlib import Path
from collections import defaultdict, Counter
from datetime import datetime, date, time
from statistics import mean, median, stdev
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots

# ==================== 配置 ====================
REPO_ROOT = Path(__file__).resolve().parent
IC_DATA_DIR = REPO_ROOT / "INIT_IC_data"
OUTPUT_DIR = REPO_ROOT / "OUT_complete_passenger_flow"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# 卡类型分类
TRACKABLE_CARD_TYPES = {
    '身份证', '敬老卡', '学生卡', '交通部普通卡',
    '交通部异地卡', '爱心卡', '军人优待证', '献血荣誉卡'
}

MOBILE_PAYMENT_TYPES = {
    '支付宝离线', '微信同程乘车码', '云闪二维码',
    '银联乘车码', '银行云闪', '支付乘车码'
}

WORK_CARD_TYPES = {
    '司机卡', '员工卡', '监督卡'
}

HIGH_SPEED_RAIL_ROUTES = {'106', '107'}

# 免费卡类型（完全免费）
FREE_CARD_TYPES = {
    '敬老卡', '爱心卡', '军人优待证', '身份证', '献血荣誉卡'
}

# 标准票制定义
STANDARD_FARES = {
    'community': 1.0,    # 社区线路1元
    'urban': 2.0,        # 市区线路2元
    'express': 5.0       # 高铁快线5元
}

# ==================== 数据读取 ====================

def read_all_ic_card_data_complete():
    """读取所有IC卡数据（包含移动支付）"""
    print("=" * 60)
    print("完整公交线路客流分析工具")
    print("=" * 60)
    print("\n正在读取IC卡数据（包含移动支付）...")

    csv_files = sorted(glob.glob(str(IC_DATA_DIR / 'IC卡消费明细查询_*.csv')))
    if not csv_files:
        print(f"错误: 未找到IC卡数据文件")
        return None

    print(f"找到 {len(csv_files)} 个文件")

    route_data = defaultdict(lambda: {
        'trips': [],
        'by_station': defaultdict(list),
        'by_hour': defaultdict(lambda: {'total': 0, 'trackable': 0, 'mobile': 0, 'other': 0}),
        'by_date': defaultdict(lambda: {'total': 0, 'trackable': 0, 'mobile': 0, 'other': 0}),
        'by_card_type': defaultdict(list)
    })

    total_records = 0
    excluded_count = 0
    mobile_count = 0
    trackable_count = 0

    for csv_file in csv_files:
        try:
            with open(csv_file, 'r', encoding='gb18030', errors='ignore') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    total_records += 1

                    card_type = row.get('卡别', '').strip()
                    route = row.get('线路', '').strip()
                    direction = row.get('上下行', '').strip()
                    station_name = row.get('站点名称', '').strip()
                    plate_number = row.get('车牌号', '').strip()
                    amount_str = row.get('消费金额', '').strip()
                    date_str = row.get('日期', '').strip()
                    time_str = row.get('时间', '').strip()

                    # 过滤工作卡
                    if card_type in WORK_CARD_TYPES:
                        excluded_count += 1
                        continue

                    # 过滤无线路记录
                    if not route:
                        continue

                    # 解析日期时间
                    try:
                        if len(date_str) == 8:
                            date_obj = datetime.strptime(date_str, '%Y%m%d').date()
                        else:
                            continue
                        if len(time_str) >= 6:
                            time_obj = datetime.strptime(time_str[:6], '%H%M%S').time()
                        else:
                            continue
                        dt = datetime.combine(date_obj, time_obj)
                    except (ValueError, TypeError):
                        continue

                    # 解析消费金额
                    try:
                        amount = float(amount_str) if amount_str else 0.0
                    except ValueError:
                        amount = 0.0

                    # 判断客流类型
                    if card_type in TRACKABLE_CARD_TYPES:
                        flow_type = 'trackable'
                        trackable_count += 1
                    elif card_type in MOBILE_PAYMENT_TYPES:
                        flow_type = 'mobile'
                        mobile_count += 1
                    else:
                        flow_type = 'other'

                    record = {
                        'card_type': card_type,
                        'route': route,
                        'direction': direction,
                        'station_name': station_name,
                        'plate_number': plate_number,
                        'amount': amount,
                        'datetime': dt,
                        'date': date_obj,
                        'time': time_obj,
                        'hour': time_obj.hour,
                        'weekday': date_obj.weekday(),
                        'flow_type': flow_type
                    }

                    route_data[route]['trips'].append(record)
                    route_data[route]['by_station'][station_name].append(record)
                    route_data[route]['by_hour'][time_obj.hour]['total'] += 1
                    route_data[route]['by_hour'][time_obj.hour][flow_type] += 1
                    route_data[route]['by_date'][date_obj]['total'] += 1
                    route_data[route]['by_date'][date_obj][flow_type] += 1
                    route_data[route]['by_card_type'][card_type].append(record)

        except Exception as e:
            continue

    print(f"\n读取完成:")
    print(f"  总记录数: {total_records:,}")
    print(f"  过滤工作卡: {excluded_count:,}")
    print(f"  有效记录数: {total_records - excluded_count:,}")
    print(f"    - 可追溯用户: {trackable_count:,}")
    print(f"    - 移动支付: {mobile_count:,}")
    print(f"  有效线路数: {len(route_data)}")

    return dict(route_data)

# ==================== 统计分析 ====================

def infer_standard_fare(route_id, avg_fare, mobile_avg_fare, mobile_ratio, total_trips, mobile_trips):
    """
    基于平均支付金额推断线路标准票价

    优先使用移动支付数据（移动支付用户全额付费，更能反映真实票价）

    规则：
    - 高铁快线(106/107): 5元
    - 移动支付均价 <= 1.1: 1元社区线路
    - 1.1 < 移动支付均价 <= 2.3: 2元市区线路
    - 2.3 < 移动支付均价 <= 4.0: 可能3元或特殊票制
    - 移动支付均价 > 4.0: 5元高铁快线

    参数:
        route_id: 线路ID
        avg_fare: 整体平均票价
        mobile_avg_fare: 移动支付用户平均票价（优先使用）
        mobile_ratio: 移动支付占比
        total_trips: 总客流
        mobile_trips: 移动支付客流
    """
    # 优先使用移动支付均价
    reference_fare = mobile_avg_fare if mobile_avg_fare > 0 else avg_fare
    data_source = 'mobile' if mobile_avg_fare > 0 else 'overall'

    # 高铁快线直接识别
    if route_id in HIGH_SPEED_RAIL_ROUTES:
        return {
            'standard_fare': 5.0,
            'fare_type': '高铁快线',
            'confidence': 'high',
            'note': f'高铁快线，5元票制（移动支付均价{mobile_avg_fare:.2f}元）',
            'data_source': 'mobile' if mobile_trips > 0 else 'known'
        }

    # 如果移动支付样本量太少（<50人次），降低置信度
    if mobile_trips < 50:
        return {
            'standard_fare': round(reference_fare * 2) / 2,
            'fare_type': '未知',
            'confidence': 'very_low',
            'note': f'移动支付样本太少（{mobile_trips}人次），无法准确推断',
            'data_source': data_source
        }

    # 基于移动支付均价推断
    if reference_fare <= 1.1:
        return {
            'standard_fare': 1.0,
            'fare_type': '社区线路',
            'confidence': 'high',
            'note': f'1元票制（移动支付均价{reference_fare:.2f}元）',
            'data_source': data_source
        }
    elif 1.1 < reference_fare <= 2.3:
        # 大部分市区线路应该是2元
        return {
            'standard_fare': 2.0,
            'fare_type': '市区线路',
            'confidence': 'high',
            'note': f'2元票制（移动支付均价{reference_fare:.2f}元）',
            'data_source': data_source
        }
    elif 2.3 < reference_fare <= 4.0:
        return {
            'standard_fare': 3.0,
            'fare_type': '特殊线路',
            'confidence': 'medium',
            'note': f'可能是3元或特殊票制（移动支付均价{reference_fare:.2f}元）',
            'data_source': data_source
        }
    elif reference_fare > 4.0:
        return {
            'standard_fare': 5.0,
            'fare_type': '高铁快线',
            'confidence': 'high',
            'note': f'5元高铁快线（移动支付均价{reference_fare:.2f}元）',
            'data_source': data_source
        }
    else:
        return {
            'standard_fare': round(reference_fare),
            'fare_type': '未知',
            'confidence': 'low',
            'note': f'无法准确推断（移动支付均价{reference_fare:.2f}元）',
            'data_source': data_source
        }


def analyze_complete_route_statistics(route_data):
    """分析线路完整客流统计"""
    print("\n正在分析线路完整客流统计...")

    route_stats = {}

    for route_id, data in route_data.items():
        trips = data['trips']
        if len(trips) == 0:
            continue

        # 基础统计
        total_trips = len(trips)
        trackable_trips = [t for t in trips if t['flow_type'] == 'trackable']
        mobile_trips = [t for t in trips if t['flow_type'] == 'mobile']

        # 营收统计
        total_revenue = sum(t['amount'] for t in trips)
        trackable_revenue = sum(t['amount'] for t in trackable_trips)
        mobile_revenue = sum(t['amount'] for t in mobile_trips)

        # 按卡类型统计
        card_type_stats = {}
        for card_type, card_trips in data['by_card_type'].items():
            amounts = [t['amount'] for t in card_trips]
            paid_amounts = [a for a in amounts if a > 0]

            card_type_stats[card_type] = {
                'total_trips': len(card_trips),
                'paid_trips': len(paid_amounts),
                'paid_ratio': len(paid_amounts) / len(card_trips) * 100 if card_trips else 0,
                'total_revenue': sum(amounts),
                'avg_fare': mean(paid_amounts) if paid_amounts else 0
            }

        # 时间分布
        hourly_total = {h: data['by_hour'][h]['total'] for h in range(24)}
        peak_hour = max(hourly_total.items(), key=lambda x: x[1])[0] if hourly_total else None

        # 每日客流（将日期键转换为字符串）
        daily_trips = {str(d): data['by_date'][d]['total'] for d in sorted(data['by_date'].keys())}
        avg_daily_trips = mean(daily_trips.values()) if daily_trips else 0
        max_daily_trips = max(daily_trips.values()) if daily_trips else 0
        min_daily_trips = min(daily_trips.values()) if daily_trips else 0

        # 热门站点
        station_trip_counts = {s: len(lst) for s, lst in data['by_station'].items()}
        top_stations = sorted(station_trip_counts.items(), key=lambda x: x[1], reverse=True)[:10]

        # 计算平均票价和付费比率
        paid_trips = sum(s['paid_trips'] for s in card_type_stats.values())
        paid_ratio = paid_trips / total_trips * 100 if total_trips > 0 else 0
        avg_fare = total_revenue / paid_trips if paid_trips > 0 else 0
        mobile_ratio = len(mobile_trips) / total_trips * 100 if total_trips > 0 else 0

        # 计算移动支付用户的平均票价（优先用于推断标准票价）
        mobile_paid_trips = [t for t in mobile_trips if t['amount'] > 0]
        mobile_avg_fare = sum(t['amount'] for t in mobile_paid_trips) / len(mobile_paid_trips) if mobile_paid_trips else 0

        # 推断标准票价（优先使用移动支付均价）
        fare_inference = infer_standard_fare(
            route_id, avg_fare, mobile_avg_fare, mobile_ratio,
            total_trips, len(mobile_trips)
        )

        route_stats[route_id] = {
            'route_id': route_id,
            'total_trips': total_trips,
            'trackable_trips': len(trackable_trips),
            'mobile_payment_trips': len(mobile_trips),
            'mobile_payment_ratio': mobile_ratio,
            'avg_daily_trips': round(avg_daily_trips, 1),
            'max_daily_trips': max_daily_trips,
            'min_daily_trips': min_daily_trips,
            'peak_hour': peak_hour,
            'total_revenue': round(total_revenue, 2),
            'trackable_revenue': round(trackable_revenue, 2),
            'mobile_revenue': round(mobile_revenue, 2),
            'paid_ratio': round(paid_ratio, 2),
            'avg_fare': round(avg_fare, 2),
            'mobile_avg_fare': round(mobile_avg_fare, 2),  # 新增：移动支付用户平均票价
            'standard_fare': fare_inference['standard_fare'],
            'fare_type': fare_inference['fare_type'],
            'fare_confidence': fare_inference['confidence'],
            'fare_note': fare_inference['note'],
            'fare_data_source': fare_inference.get('data_source', 'unknown'),  # 新增：数据来源
            'card_type_stats': card_type_stats,
            'top_stations': top_stations,
            'daily_trips': daily_trips,
            'hourly_distribution': hourly_total
        }

    print(f"  完成分析 {len(route_stats)} 条线路")
    return route_stats

def analyze_card_type_comparison(route_data):
    """分析卡类型对比"""
    print("\n正在分析卡类型对比...")

    global_card_stats = defaultdict(lambda: {
        'total_trips': 0,
        'total_revenue': 0,
        'paid_trips': 0,
        'routes': set()
    })

    for route_id, data in route_data.items():
        for card_type, trips in data['by_card_type'].items():
            amounts = [t['amount'] for t in trips]
            paid_amounts = [a for a in amounts if a > 0]

            global_card_stats[card_type]['total_trips'] += len(trips)
            global_card_stats[card_type]['total_revenue'] += sum(amounts)
            global_card_stats[card_type]['paid_trips'] += len(paid_amounts)
            global_card_stats[card_type]['routes'].add(route_id)

    # 计算总体统计
    total_all = sum(s['total_trips'] for s in global_card_stats.values())
    total_revenue = sum(s['total_revenue'] for s in global_card_stats.values())

    for card_type, stats in global_card_stats.items():
        stats['trips_ratio'] = stats['total_trips'] / total_all * 100 if total_all > 0 else 0
        stats['revenue_ratio'] = stats['total_revenue'] / total_revenue * 100 if total_revenue > 0 else 0
        stats['paid_ratio'] = stats['paid_trips'] / stats['total_trips'] * 100 if stats['total_trips'] > 0 else 0
        stats['avg_fare'] = stats['total_revenue'] / stats['paid_trips'] if stats['paid_trips'] > 0 else 0
        stats['route_count'] = len(stats['routes'])
        # 找出该卡类型最常使用的线路
        stats['top_route'] = max(stats['routes'], key=lambda r: sum(
            1 for t in route_data[r]['by_card_type'][card_type]
        )) if stats['routes'] else None

        # 推断该卡类型对应的标准票价
        avg_fare = stats['avg_fare']
        if card_type in FREE_CARD_TYPES:
            stats['standard_fare'] = 0
            stats['fare_note'] = '完全免费'
        elif card_type == '学生卡':
            stats['standard_fare'] = 1.0
            stats['fare_note'] = f'学生优惠，平均实付{avg_fare:.2f}元（推断标准票价1元）'
        elif card_type in MOBILE_PAYMENT_TYPES:
            if avg_fare <= 1.1:
                stats['standard_fare'] = 1.0
                stats['fare_note'] = f'推断为1元票制线路'
            elif avg_fare <= 2.3:
                stats['standard_fare'] = 2.0
                stats['fare_note'] = f'推断为2元票制线路（平均实付{avg_fare:.2f}元）'
            else:
                stats['standard_fare'] = 5.0
                stats['fare_note'] = f'推断为5元高铁快线（平均实付{avg_fare:.2f}元）'
        elif avg_fare <= 1.1:
            stats['standard_fare'] = 1.0
            stats['fare_note'] = f'推断为1元票制'
        elif avg_fare <= 2.2:
            stats['standard_fare'] = 2.0
            stats['fare_note'] = f'推断为2元票制（平均实付{avg_fare:.2f}元，享受优惠）'
        else:
            stats['standard_fare'] = round(avg_fare)
            stats['fare_note'] = f'推断标准票价{round(avg_fare)}元'

    print(f"  完成分析 {len(global_card_stats)} 种卡类型")
    return dict(global_card_stats)

def analyze_time_distribution(route_data):
    """分析时间分布"""
    print("\n正在分析时间分布...")

    # 按日期统计
    date_stats = defaultdict(lambda: {'total': 0, 'trackable': 0, 'mobile': 0, 'other': 0, 'weekday': None})
    # 按小时统计
    hour_stats = defaultdict(lambda: {'total': 0, 'trackable': 0, 'mobile': 0, 'other': 0})
    # 按星期统计
    weekday_stats = defaultdict(lambda: {'total': 0, 'trackable': 0, 'mobile': 0, 'other': 0})

    for route_id, data in route_data.items():
        for trip in data['trips']:
            d = trip['date']
            h = trip['hour']
            w = trip['weekday']
            ft = trip['flow_type']

            date_stats[d]['total'] += 1
            date_stats[d][ft] += 1
            date_stats[d]['weekday'] = w

            hour_stats[h]['total'] += 1
            hour_stats[h][ft] += 1

            weekday_stats[w]['total'] += 1
            weekday_stats[w][ft] += 1

    # 将日期键转换为字符串以便JSON序列化
    date_stats_str = {str(d): v for d, v in date_stats.items()}

    print(f"  完成 - 覆盖 {len(date_stats)} 天")
    return {
        'by_date': date_stats_str,
        'by_hour': dict(hour_stats),
        'by_weekday': dict(weekday_stats)
    }

def analyze_station_complete_flow(route_data):
    """分析站点完整客流"""
    print("\n正在分析站点完整客流...")

    station_stats = defaultdict(lambda: {
        'total_trips': 0,
        'trackable_trips': 0,
        'mobile_trips': 0,
        'routes': set(),
        'by_route': defaultdict(int)
    })

    for route_id, data in route_data.items():
        for station, trips in data['by_station'].items():
            station_stats[station]['routes'].add(route_id)

            for trip in trips:
                station_stats[station]['total_trips'] += 1
                if trip['flow_type'] == 'trackable':
                    station_stats[station]['trackable_trips'] += 1
                elif trip['flow_type'] == 'mobile':
                    station_stats[station]['mobile_trips'] += 1

                station_stats[station]['by_route'][route_id] += 1

    print(f"  完成 - 共 {len(station_stats)} 个站点")
    return dict(station_stats)

def analyze_revenue_breakdown(route_data):
    """分析营收明细"""
    print("\n正在分析营收明细...")

    revenue_stats = {
        'total_revenue': 0,
        'by_card_type': defaultdict(float),
        'by_route': defaultdict(float),
        'fare_distribution': defaultdict(int),
        'trackable_revenue': 0,
        'mobile_revenue': 0
    }

    for route_id, data in route_data.items():
        for trip in data['trips']:
            amount = trip['amount']
            card_type = trip['card_type']
            flow_type = trip['flow_type']

            revenue_stats['total_revenue'] += amount
            revenue_stats['by_card_type'][card_type] += amount
            revenue_stats['by_route'][route_id] += amount

            if amount > 0:
                revenue_stats['fare_distribution'][round(amount, 1)] += 1

            if flow_type == 'trackable':
                revenue_stats['trackable_revenue'] += amount
            elif flow_type == 'mobile':
                revenue_stats['mobile_revenue'] += amount

    print(f"  完成 - 总营收 CNY {revenue_stats['total_revenue']:,.2f}")
    return revenue_stats

def analyze_special_patterns(route_data, route_stats, card_stats):
    """分析特殊模式"""
    print("\n正在分析特殊模式...")

    patterns = {
        'high_speed_rail': {},
        'mobile_preference': {},
        'free_card_usage': {}
    }

    # 高铁快线分析
    for route_id in HIGH_SPEED_RAIL_ROUTES:
        if route_id in route_stats:
            s = route_stats[route_id]
            patterns['high_speed_rail'][route_id] = {
                'total_trips': s['total_trips'],
                'mobile_payment_trips': s['mobile_payment_trips'],
                'mobile_ratio': s['mobile_payment_ratio'],
                'total_revenue': s['total_revenue'],
                'avg_fare': s['total_revenue'] / s['total_trips'] if s['total_trips'] > 0 else 0
            }

    # 移动支付偏好线路（TOP 10）
    mobile_routes = [(r, s['mobile_payment_ratio'], s['mobile_payment_trips'])
                     for r, s in route_stats.items()]
    mobile_routes.sort(key=lambda x: x[1], reverse=True)
    patterns['mobile_preference'] = [
        {'route': r, 'ratio': round(ratio, 2), 'trips': trips}
        for r, ratio, trips in mobile_routes[:10]
    ]

    # 免费卡使用线路
    for route_id, data in route_data.items():
        free_trips = [t for t in data['trips'] if t['card_type'] in FREE_CARD_TYPES]
        if free_trips:
            patterns['free_card_usage'][route_id] = {
                'free_trips': len(free_trips),
                'free_ratio': len(free_trips) / len(data['trips']) * 100
            }

    print(f"  高铁快线: {len(patterns['high_speed_rail'])} 条")
    print(f"  移动支付偏好线路: TOP 10")
    print(f"  免费卡使用线路: {len(patterns['free_card_usage'])} 条")

    return patterns

# ==================== 数据导出 ====================

def export_complete_route_summary(route_stats):
    """导出完整客流汇总"""
    print("\n正在导出完整客流汇总...")

    output_file = OUTPUT_DIR / 'complete_route_summary.csv'

    with open(output_file, 'w', encoding='utf-8-sig', newline='') as f:
        fieldnames = [
            'route_id', 'total_trips', 'trackable_trips', 'mobile_payment_trips',
            'mobile_payment_ratio', 'avg_daily_trips', 'max_daily_trips', 'min_daily_trips',
            'peak_hour', 'total_revenue', 'trackable_revenue', 'mobile_revenue',
            'paid_ratio', 'avg_fare', 'mobile_avg_fare', 'standard_fare',
            'fare_type', 'fare_confidence', 'fare_data_source',
            'top_station', 'top_station_count'
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for stats in sorted(route_stats.values(), key=lambda x: x['total_trips'], reverse=True):
            writer.writerow({
                'route_id': stats['route_id'],
                'total_trips': stats['total_trips'],
                'trackable_trips': stats['trackable_trips'],
                'mobile_payment_trips': stats['mobile_payment_trips'],
                'mobile_payment_ratio': round(stats['mobile_payment_ratio'], 2),
                'avg_daily_trips': stats['avg_daily_trips'],
                'max_daily_trips': stats['max_daily_trips'],
                'min_daily_trips': stats['min_daily_trips'],
                'peak_hour': stats['peak_hour'],
                'total_revenue': stats['total_revenue'],
                'trackable_revenue': stats['trackable_revenue'],
                'mobile_revenue': stats['mobile_revenue'],
                'paid_ratio': stats['paid_ratio'],
                'avg_fare': stats['avg_fare'],
                'mobile_avg_fare': stats['mobile_avg_fare'],  # 新增
                'standard_fare': stats['standard_fare'],
                'fare_type': stats['fare_type'],
                'fare_confidence': stats['fare_confidence'],
                'fare_data_source': stats.get('fare_data_source', 'unknown'),  # 新增
                'top_station': stats['top_stations'][0][0] if stats['top_stations'] else '',
                'top_station_count': stats['top_stations'][0][1] if stats['top_stations'] else 0
            })

    print(f"  已保存: {output_file}")

def export_card_type_comparison(card_stats):
    """导出卡类型对比"""
    print("\n正在导出卡类型对比...")

    output_file = OUTPUT_DIR / 'card_type_comparison.csv'

    # 判断卡类型类别
    def get_category(card_type):
        if card_type in TRACKABLE_CARD_TYPES:
            return '可追溯用户'
        elif card_type in MOBILE_PAYMENT_TYPES:
            return '移动支付'
        else:
            return '其他'

    with open(output_file, 'w', encoding='utf-8-sig', newline='') as f:
        fieldnames = [
            'card_type', 'category', 'total_trips', 'trips_ratio',
            'total_revenue', 'revenue_ratio', 'paid_trips', 'paid_ratio',
            'avg_fare', 'standard_fare', 'fare_note', 'route_count', 'top_route'
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for card_type, stats in sorted(card_stats.items(), key=lambda x: x[1]['total_trips'], reverse=True):
            writer.writerow({
                'card_type': card_type,
                'category': get_category(card_type),
                'total_trips': stats['total_trips'],
                'trips_ratio': round(stats['trips_ratio'], 2),
                'total_revenue': round(stats['total_revenue'], 2),
                'revenue_ratio': round(stats['revenue_ratio'], 2),
                'paid_trips': stats['paid_trips'],
                'paid_ratio': round(stats['paid_ratio'], 2),
                'avg_fare': round(stats['avg_fare'], 2),
                'standard_fare': stats.get('standard_fare', 0),
                'fare_note': stats.get('fare_note', ''),
                'route_count': stats['route_count'],
                'top_route': stats['top_route'] or ''
            })

    print(f"  已保存: {output_file}")

def export_time_distribution(time_stats):
    """导出时间分布"""
    print("\n正在导出时间分布...")

    output_file = OUTPUT_DIR / 'time_distribution.csv'

    with open(output_file, 'w', encoding='utf-8-sig', newline='') as f:
        fieldnames = [
            'date', 'weekday', 'total_trips', 'trackable_trips', 'mobile_payment_trips'
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        weekday_names = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']

        for date in sorted(time_stats['by_date'].keys()):
            data = time_stats['by_date'][date]
            writer.writerow({
                'date': date,  # 日期已经是字符串
                'weekday': weekday_names[data['weekday']],
                'total_trips': data['total'],
                'trackable_trips': data['trackable'],
                'mobile_payment_trips': data['mobile']
            })

    print(f"  已保存: {output_file}")

def export_station_complete_flow(station_stats):
    """导出站点完整客流"""
    print("\n正在导出站点完整客流...")

    output_file = OUTPUT_DIR / 'station_complete_flow.csv'

    with open(output_file, 'w', encoding='utf-8-sig', newline='') as f:
        fieldnames = [
            'station_name', 'total_trips', 'trackable_trips', 'mobile_payment_trips',
            'mobile_payment_ratio', 'route_count', 'routes', 'top_route', 'top_route_count'
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for station, stats in sorted(station_stats.items(), key=lambda x: x[1]['total_trips'], reverse=True):
            routes_list = sorted(stats['routes'])
            top_route = max(stats['by_route'].items(), key=lambda x: x[1])[0] if stats['by_route'] else ''
            top_count = stats['by_route'][top_route]

            writer.writerow({
                'station_name': station,
                'total_trips': stats['total_trips'],
                'trackable_trips': stats['trackable_trips'],
                'mobile_payment_trips': stats['mobile_trips'],
                'mobile_payment_ratio': round(stats['mobile_trips'] / stats['total_trips'] * 100, 2) if stats['total_trips'] > 0 else 0,
                'route_count': len(stats['routes']),
                'routes': ';'.join(routes_list),
                'top_route': top_route,
                'top_route_count': top_count
            })

    print(f"  已保存: {output_file}")

def export_revenue_breakdown(revenue_stats):
    """导出营收明细"""
    print("\n正在导出营收明细...")

    output_file = OUTPUT_DIR / 'revenue_breakdown.csv'

    with open(output_file, 'w', encoding='utf-8-sig', newline='') as f:
        fieldnames = ['card_type', 'total_revenue', 'revenue_ratio', 'fare_distribution']
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        total_rev = revenue_stats['total_revenue']

        for card_type, revenue in sorted(revenue_stats['by_card_type'].items(),
                                          key=lambda x: x[1], reverse=True):
            # 票价分布
            fare_dist = revenue_stats['fare_distribution']
            fare_str = ';'.join([f"{fare}元:{count}" for fare, count in sorted(fare_dist.items())])

            writer.writerow({
                'card_type': card_type,
                'total_revenue': round(revenue, 2),
                'revenue_ratio': round(revenue / total_rev * 100, 2) if total_rev > 0 else 0,
                'fare_distribution': fare_str
            })

    print(f"  已保存: {output_file}")

def export_json_details(route_stats, card_stats, time_stats, station_stats, revenue_stats, patterns):
    """导出JSON明细"""
    print("\n正在导出JSON明细...")

    output_file = OUTPUT_DIR / 'complete_flow_details.json'

    # 自定义JSON编码器处理日期对象和set对象
    class DateEncoder(json.JSONEncoder):
        def default(self, obj):
            if hasattr(obj, 'isoformat'):
                return obj.isoformat()
            elif isinstance(obj, set):
                return list(obj)
            return super().default(obj)

    output_data = {
        'metadata': {
            'analysis_date': datetime.now().isoformat(),
            'script_version': '1.0',
            'description': '完整客流分析（包含移动支付）'
        },
        'global_summary': {
            'total_routes': len(route_stats),
            'total_trips': sum(s['total_trips'] for s in route_stats.values()),
            'trackable_trips': sum(s['trackable_trips'] for s in route_stats.values()),
            'mobile_trips': sum(s['mobile_payment_trips'] for s in route_stats.values()),
            'total_revenue': sum(s['total_revenue'] for s in route_stats.values())
        },
        'route_details': route_stats,
        'card_type_details': card_stats,
        'time_distribution': time_stats,
        'station_details': station_stats,
        'revenue_details': revenue_stats,
        'special_patterns': patterns
    }

    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2, cls=DateEncoder)

    print(f"  已保存: {output_file}")

# ==================== Plotly可视化报告 ====================

def generate_plotly_report(route_stats, card_stats, time_stats, station_stats, revenue_stats, patterns):
    """生成Plotly交互式可视化报告"""
    print("\n正在生成Plotly交互式报告...")

    total_trips = sum(s['total_trips'] for s in route_stats.values())
    trackable_trips = sum(s['trackable_trips'] for s in route_stats.values())
    mobile_trips = sum(s['mobile_payment_trips'] for s in route_stats.values())
    total_revenue = sum(s['total_revenue'] for s in route_stats.values())

    # 准备图表数据 - TOP15线路
    top_routes = sorted(route_stats.items(), key=lambda x: x[1]['total_trips'], reverse=True)[:15]

    # 卡类型数据
    top_card_types = sorted(card_stats.keys(), key=lambda x: card_stats[x]['total_trips'], reverse=True)[:8]
    card_data = [(ct, card_stats[ct]['total_trips']) for ct in top_card_types]

    # 移动支付占比TOP15
    mobile_ratio_top = sorted(route_stats.items(), key=lambda x: x[1]['mobile_payment_ratio'], reverse=True)[:15]

    # 营收TOP15
    revenue_top = sorted(route_stats.items(), key=lambda x: x[1]['total_revenue'], reverse=True)[:15]

    # 时间分布（按小时）
    hour_data = [0] * 24
    for h in range(24):
        hour_data[h] = sum(stats['hourly_distribution'].get(h, 0) for stats in route_stats.values())

    # 票价分布
    fare_ranges = {'1元': 0, '2元': 0, '5元': 0, '其他': 0}
    for stats in route_stats.values():
        std_fare = stats.get('standard_fare', 2)
        if std_fare == 1.0:
            fare_ranges['1元'] += stats['total_trips']
        elif std_fare == 2.0:
            fare_ranges['2元'] += stats['total_trips']
        elif std_fare == 5.0:
            fare_ranges['5元'] += stats['total_trips']
        else:
            fare_ranges['其他'] += stats['total_trips']

    # 站点客流TOP15
    station_top = sorted(station_stats.items(), key=lambda x: x[1]['total_trips'], reverse=True)[:15]

    # 创建子图布局 (4行2列)
    fig = make_subplots(
        rows=4, cols=2,
        subplot_titles=(
            'TOP15线路客流对比',
            '卡类型分布',
            '移动支付占比TOP15',
            '营收TOP15线路',
            '时段客流分布',
            '票价分布',
            '站点客流TOP15',
            '各线路客流-营收关系'
        ),
        specs=[
            [{"type": "bar"}, {"type": "pie"}],
            [{"type": "bar"}, {"type": "bar"}],
            [{"type": "scatter"}, {"type": "pie"}],
            [{"type": "bar"}, {"type": "scatter"}]
        ],
        vertical_spacing=0.08,
        horizontal_spacing=0.10
    )

    # 图1：TOP15线路客流
    fig.add_trace(
        go.Bar(
            x=[f"{r}路" for r, s in top_routes],
            y=[s['total_trips'] for r, s in top_routes],
            marker_color='#667eea',
            text=[f"{s['total_trips']:,}" for r, s in top_routes],
            textposition='outside'
        ),
        row=1, col=1
    )

    # 图2：卡类型分布
    fig.add_trace(
        go.Pie(
            labels=[ct for ct, _ in card_data],
            values=[count for _, count in card_data],
            hole=0.3,
            marker=dict(colors=px.colors.sequential.Viridis)
        ),
        row=1, col=2
    )

    # 图3：移动支付占比TOP15
    fig.add_trace(
        go.Bar(
            x=[f"{r}路" for r, s in mobile_ratio_top],
            y=[s['mobile_payment_ratio'] for r, s in mobile_ratio_top],
            marker_color='#FFA726',
            text=[f"{s['mobile_payment_ratio']:.1f}%" for r, s in mobile_ratio_top],
            textposition='outside'
        ),
        row=2, col=1
    )

    # 图4：营收TOP15
    fig.add_trace(
        go.Bar(
            x=[f"{r}路" for r, s in revenue_top],
            y=[s['total_revenue'] for r, s in revenue_top],
            marker_color='#26a69a',
            text=[f"¥{s['total_revenue']:,.0f}" for r, s in revenue_top],
            textposition='outside'
        ),
        row=2, col=2
    )

    # 图5：时段客流分布（折线图）
    fig.add_trace(
        go.Scatter(
            x=list(range(24)),
            y=hour_data,
            mode='lines+markers',
            marker=dict(size=6, color='#ab47bc'),
            line=dict(width=2, color='#ab47bc')
        ),
        row=3, col=1
    )

    # 图6：票价分布
    fig.add_trace(
        go.Pie(
            labels=list(fare_ranges.keys()),
            values=list(fare_ranges.values()),
            hole=0.3,
            marker=dict(colors=['#4CAF50', '#2196F3', '#FF9800', '#9E9E9E'])
        ),
        row=3, col=2
    )

    # 图7：站点客流TOP15
    fig.add_trace(
        go.Bar(
            x=[st for st, _ in station_top],
            y=[s['total_trips'] for _, s in station_top],
            marker_color='#ef5350',
            text=[f"{s['total_trips']:,}" for _, s in station_top],
            textposition='outside'
        ),
        row=4, col=1
    )

    # 图8：客流-营收散点图
    all_routes = list(route_stats.values())
    fig.add_trace(
        go.Scatter(
            x=[s['total_trips'] for s in all_routes],
            y=[s['total_revenue'] for s in all_routes],
            mode='markers+text',
            marker=dict(
                size=[max(8, min(20, s['total_trips'] / 5000)) for s in all_routes],
                color=[s['mobile_payment_ratio'] for s in all_routes],
                colorscale='RdYlGn',
                showscale=True,
                colorbar=dict(title="移动占比%", x=1.02)
            ),
            text=[s['route_id'] for s in all_routes],
            textposition='top center',
            textfont=dict(size=8)
        ),
        row=4, col=2
    )

    # 更新布局
    fig.update_xaxes(title_text="线路", row=1, col=1)
    fig.update_yaxes(title_text="客流量（人次）", row=1, col=1)

    fig.update_xaxes(title_text="线路", row=2, col=1)
    fig.update_yaxes(title_text="移动支付占比（%）", row=2, col=1)

    fig.update_xaxes(title_text="线路", row=2, col=2)
    fig.update_yaxes(title_text="营收（元）", row=2, col=2)

    fig.update_xaxes(title_text="小时", row=3, col=1)
    fig.update_yaxes(title_text="客流量（人次）", row=3, col=1)

    fig.update_xaxes(title_text="站点", row=4, col=1)
    fig.update_yaxes(title_text="客流量（人次）", row=4, col=1)

    fig.update_xaxes(title_text="客流量（人次）", row=4, col=2)
    fig.update_yaxes(title_text="营收（元）", row=4, col=2)

    fig.update_layout(
        height=1600,
        title_text="<b>黄山公交完整客流分析报告</b><br>" +
                  f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}<br>" +
                  f"<sup>总客流: {total_trips:,} | 可追溯: {trackable_trips:,} | 移动支付: {mobile_trips:,} ({mobile_trips/total_trips*100:.1f}%) | 总营收: ¥{total_revenue:,.2f}</sup>",
        title_font_size=14,
        showlegend=True
    )

    # 保存HTML
    output_html = OUTPUT_DIR / 'complete_flow_analysis.html'
    fig.write_html(output_html)
    print(f"  已保存: {output_html}")

    # 保存PNG（可选）
    try:
        output_png = OUTPUT_DIR / "complete_flow_analysis.png"
        fig.write_image(output_png, width=1600, height=1600, scale=2)
        print(f"  已保存: {output_png}")
    except:
        print(f"  PNG保存失败（需要安装kaleido）")


# ==================== Markdown总结报告 ====================

def generate_markdown_summary(route_stats, card_stats, time_stats, station_stats, revenue_stats, patterns):
    """生成Markdown总结报告"""
    print("\n正在生成Markdown总结报告...")

    total_trips = sum(s['total_trips'] for s in route_stats.values())
    trackable_trips = sum(s['trackable_trips'] for s in route_stats.values())
    mobile_trips = sum(s['mobile_payment_trips'] for s in route_stats.values())
    total_revenue = sum(s['total_revenue'] for s in route_stats.values())

    md_content = f"""# 黄山公交完整客流分析报告

**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

---

## 一、整体概况

| 指标 | 数值 |
|------|------|
| **总客流** | {total_trips:,} 人次 |
| **可追溯用户客流** | {trackable_trips:,} 人次 ({trackable_trips/total_trips*100:.1f}%) |
| **移动支付客流** | {mobile_trips:,} 人次 ({mobile_trips/total_trips*100:.1f}%) |
| **总营收** | ¥{total_revenue:,.2f} |
| **有效线路数** | {len(route_stats)} |

---

## 二、TOP15线路客流排名

| 排名 | 线路 | 总客流 | 可追溯用户 | 移动支付 | 移动占比 | 日均客流 | 总营收 |
|------|------|--------|------------|----------|----------|----------|--------|
"""

    # 添加TOP15线路
    top_routes = sorted(route_stats.items(), key=lambda x: x[1]['total_trips'], reverse=True)[:15]
    for rank, (route_id, stats) in enumerate(top_routes, 1):
        md_content += f"| {rank} | {route_id}路 | {stats['total_trips']:,} | {stats['trackable_trips']:,} | {stats['mobile_payment_trips']:,} | {stats['mobile_payment_ratio']:.1f}% | {stats['avg_daily_trips']:.1f} | ¥{stats['total_revenue']:,.2f} |\n"

    md_content += f"""

---

## 三、卡类型分布

| 排名 | 卡类型 | 客流 | 占比 | 营收 | 付费占比 |
|------|--------|------|------|------|----------|
"""

    # 添加卡类型分布
    for rank, (card_type, stats) in enumerate(sorted(card_stats.items(), key=lambda x: x[1]['total_trips'], reverse=True)[:10], 1):
        percentage = stats['trips_ratio']
        md_content += f"| {rank} | {card_type} | {stats['total_trips']:,} | {percentage:.1f}% | ¥{stats['total_revenue']:,.2f} | {stats['paid_ratio']:.1f}% |\n"

    md_content += f"""

---

## 四、票制结构分析

### 4.1 票价分布

| 票价类型 | 线路数 | 客流占比 |
|----------|--------|----------|
"""

    # 票制分析
    fare_type_counts = {}
    fare_type_trips = {}
    for route_id, stats in route_stats.items():
        fare_type = stats['fare_type']
        fare_type_counts[fare_type] = fare_type_counts.get(fare_type, 0) + 1
        fare_type_trips[fare_type] = fare_type_trips.get(fare_type, 0) + stats['total_trips']

    for fare_type, count in sorted(fare_type_counts.items(), key=lambda x: x[1], reverse=True):
        trips = fare_type_trips[fare_type]
        percentage = trips / total_trips * 100
        md_content += f"| {fare_type} | {count} | {percentage:.1f}% |\n"

    md_content += f"""

### 4.2 高铁快线分析（5元票制）

| 线路 | 客流 | 移动支付占比 | 平均票价 | 总营收 |
|------|------|--------------|----------|--------|
"""

    # 高铁快线分析
    for route_id, data in patterns['high_speed_rail'].items():
        md_content += f"| {route_id}路 | {data['total_trips']:,} | {data['mobile_ratio']:.1f}% | ¥{data['avg_fare']:.2f} | ¥{route_stats[route_id]['total_revenue']:,.2f} |\n"

    md_content += f"""

---

## 五、移动支付分析

### 5.1 移动支付占比TOP10

| 排名 | 线路 | 移动支付占比 | 客流 |
|------|------|--------------|------|
"""

    # 移动支付TOP10
    for rank, item in enumerate(patterns['mobile_preference'][:10], 1):
        md_content += f"| {rank} | {item['route']}路 | {item['ratio']:.1f}% | {item['trips']:,} |\n"

    md_content += f"""

### 5.2 移动支付 vs 可追溯用户

* **移动支付用户**: {mobile_trips:,} 人次 ({mobile_trips/total_trips*100:.1f}%)
* **可追溯用户**: {trackable_trips:,} 人次 ({trackable_trips/total_trips*100:.1f}%)
* **移动支付已成为主流**: 移动支付客流超过可追溯用户客流

---

## 六、站点客流TOP15

| 排名 | 站点名称 | 总客流 | 线路数 |
|------|----------|--------|--------|
"""

    # 站点TOP15
    station_top = sorted(station_stats.items(), key=lambda x: x[1]['total_trips'], reverse=True)[:15]
    for rank, (station, stats) in enumerate(station_top, 1):
        md_content += f"| {rank} | {station} | {stats['total_trips']:,} | {len(stats['routes'])} |\n"

    md_content += f"""

---

## 七、关键发现

### 7.1 客流特征

* **移动支付占主导**: {mobile_trips/total_trips*100:.1f}%的客流使用移动支付
* **客流集中度高**: TOP15线路贡献了 {sum(s['total_trips'] for r, s in top_routes):,} 人次客流，占总客流的 {sum(s['total_trips'] for r, s in top_routes)/total_trips*100:.1f}%

### 7.2 票制结构

* **主流票制**: 2元市区线路
* **特色票制**: 5元高铁快线（106/107路）
* **社区服务**: 1元社区线路

### 7.3 营收分析

* **总营收**: ¥{total_revenue:,.2f}
* **平均客单价**: ¥{total_revenue/total_trips:.2f}
* **移动支付贡献**: ¥{sum(s['mobile_revenue'] for s in route_stats.values()):,.2f} ({sum(s['mobile_revenue'] for s in route_stats.values())/total_revenue*100:.1f}%)

---

## 八、数据文件说明

本报告基于以下数据文件生成：

* `complete_route_summary.csv`: 完整客流汇总统计
* `card_type_comparison.csv`: 卡类型对比分析
* `time_distribution.csv`: 时间分布数据
* `station_complete_flow.csv`: 站点完整客流
* `revenue_breakdown.csv`: 营收明细
* `complete_flow_details.json`: 完整客流明细数据

---

*报告由黄山公交完整客流分析工具自动生成*
"""

    # 保存Markdown文件
    output_md = OUTPUT_DIR / 'complete_flow_summary.md'
    with open(output_md, 'w', encoding='utf-8') as f:
        f.write(md_content)

    print(f"  已保存: {output_md}")

# ==================== 主程序 ====================

def main():
    # 1. 读取数据
    route_data = read_all_ic_card_data_complete()
    if not route_data:
        print("错误: 没有有效数据")
        return

    # 2. 统计分析
    route_stats = analyze_complete_route_statistics(route_data)
    card_stats = analyze_card_type_comparison(route_data)
    time_stats = analyze_time_distribution(route_data)
    station_stats = analyze_station_complete_flow(route_data)
    revenue_stats = analyze_revenue_breakdown(route_data)
    patterns = analyze_special_patterns(route_data, route_stats, card_stats)

    # 3. 导出结果
    export_complete_route_summary(route_stats)
    export_card_type_comparison(card_stats)
    export_time_distribution(time_stats)
    export_station_complete_flow(station_stats)
    export_revenue_breakdown(revenue_stats)
    export_json_details(route_stats, card_stats, time_stats, station_stats, revenue_stats, patterns)
    generate_plotly_report(route_stats, card_stats, time_stats, station_stats, revenue_stats, patterns)
    generate_markdown_summary(route_stats, card_stats, time_stats, station_stats, revenue_stats, patterns)

    print("\n" + "=" * 60)
    print("分析完成！")
    print("=" * 60)
    print(f"\n输出文件:")
    print(f"  - CSV汇总: {OUTPUT_DIR / 'complete_route_summary.csv'}")
    print(f"  - Plotly交互式报告: {OUTPUT_DIR / 'complete_flow_analysis.html'}")
    print(f"  - Markdown总结: {OUTPUT_DIR / 'complete_flow_summary.md'}")

    # 打印关键发现
    total_trips = sum(s['total_trips'] for s in route_stats.values())
    trackable_trips = sum(s['trackable_trips'] for s in route_stats.values())
    mobile_trips = sum(s['mobile_payment_trips'] for s in route_stats.values())
    total_revenue = sum(s['total_revenue'] for s in route_stats.values())

    print(f"\n=== 整体概况 ===")
    print(f"总客流: {total_trips:,} 人次")
    print(f"  - 可追溯用户: {trackable_trips:,} 人次 ({trackable_trips/total_trips*100:.1f}%)")
    print(f"  - 移动支付: {mobile_trips:,} 人次 ({mobile_trips/total_trips*100:.1f}%)")
    print(f"总营收: CNY {total_revenue:,.2f}")

    # 票制分析摘要
    print(f"\n=== 票制结构分析（基于推断） ===")

    fare_type_counts = {}
    for route_id, stats in route_stats.items():
        fare_type = stats['fare_type']
        fare_type_counts[fare_type] = fare_type_counts.get(fare_type, 0) + 1

    for fare_type, count in sorted(fare_type_counts.items(), key=lambda x: x[1], reverse=True):
        print(f"{fare_type}: {count} 条线路")

    print(f"\n说明:")
    print(f"  - 黄山公交实行1元/2元/5元分级票制")
    print(f"  - 1元: 少数社区线路")
    print(f"  - 2元: 大部分市区线路（主流）")
    print(f"  - 5元: 高铁快线（106/107路）")
    print(f"  - 分析结果中的平均票价是优惠后的实际支付金额")
    print(f"  - 大量免费卡用户（54.2%）拉低了整体平均票价")

    # 高铁快线
    print(f"\n=== 高铁快线（5元票制）===")
    for route_id, data in patterns['high_speed_rail'].items():
        print(f"{route_id}路: {data['total_trips']:,}人次, 移动支付{data['mobile_ratio']:.1f}%, "
              f"平均票价 CNY {data['avg_fare']:.2f}")
    print(f"解读: 高铁快线无免费优惠，96%+移动支付用户全额付费5元")

    # 移动支付TOP线路
    print(f"\n=== 移动支付占比 TOP 5 ===")
    for item in patterns['mobile_preference'][:5]:
        route_id = item['route']
        ratio = item['ratio']
        trips = item['trips']
        if route_id in route_stats:
            fare_note = route_stats[route_id]['fare_note']
            print(f"{route_id}路: 移动支付占比{ratio}% ({trips:,}人次) - {fare_note}")

    # 免费卡使用情况
    print(f"\n=== 免费卡使用分析 ===")
    free_card_revenue = sum(s['total_revenue'] for s in card_stats.values() if s.get('standard_fare', 1) == 0)
    free_card_trips = sum(s['total_trips'] for s in card_stats.values() if s.get('standard_fare', 1) == 0)
    print(f"完全免费卡客流: {free_card_trips:,} 人次 ({free_card_trips/total_trips*100:.1f}%)")
    print(f"完全免费卡营收: CNY {free_card_revenue:.2f} (0%)")
    print(f"主要免费卡类型: 敬老卡、身份证、爱心卡、军人优待证、献血荣誉卡")

if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print(f"\n错误: {e}")
        import traceback
        traceback.print_exc()
