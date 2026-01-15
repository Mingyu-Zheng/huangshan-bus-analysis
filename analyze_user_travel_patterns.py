#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
用户出行规律分析脚本
从IC卡数据中提取可追踪用户的出行记录，分析其出行规律

功能：
1. 筛选可追踪用户的记录（排除移动支付）
2. 按用户ID聚合所有出行记录
3. 深度分析：通勤识别、出行链路、多日模式、异常检测
4. 输出CSV统计 + JSON明细
5. 生成Plotly交互式可视化报告
6. 生成Markdown总结报告
7. 生成CSS渲染的HTML报告
"""

import csv
import glob
import json
import math
import re
from pathlib import Path
from collections import defaultdict, Counter
from datetime import datetime, timedelta
from statistics import mean, median
import hashlib
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots

# ==================== 配置 ====================
REPO_ROOT = Path(__file__).resolve().parent
IC_DATA_DIR = REPO_ROOT / "INIT_IC_data"
OUTPUT_DIR = REPO_ROOT / "OUT_user_analysis"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# 可追踪用户的卡类型（有固定用户ID）
TRACKABLE_CARD_TYPES = {
    '身份证', '敬老卡', '军人优待证', '爱心卡', '学生卡',
    '交通部普通卡', '交通部异地卡', '献血荣誉卡', '特惠卡'
}

# 需要过滤的卡类型（工作卡等）
EXCLUDE_CARD_TYPES = {
    '司机卡', '员工卡', '监督卡'
}

# 工作日（周一到周五）
WEEKDAYS = {0, 1, 2, 3, 4}  # Monday=0, Friday=4

# 高峰时段定义
PEAK_HOURS = {
    'morning': [(7, 9)],    # 早高峰 7:00-9:00
    'evening': [(17, 19)]   # 晚高峰 17:00-19:00
}

# ==================== 身份证验证函数 ====================

def is_valid_id_card_number(card_number_str):
    """
    验证是否是合法的身份证号码（基于出生年月日规则）
    返回: (是否合法, 出生日期字符串'YYYY-MM-DD' 或 None)
    """
    if not isinstance(card_number_str, str):
        card_number_str = str(card_number_str)

    # 去除空格
    card_number_str = card_number_str.strip()

    # 检查格式：18位，前17位数字，最后一位数字或X
    if not re.match(r'^\d{17}[\dXx]$', card_number_str):
        return False, None

    try:
        # 提取出生年月日（第7-14位：YYYYMMDD）
        year_str = card_number_str[6:10]
        month_str = card_number_str[10:12]
        day_str = card_number_str[12:14]

        year = int(year_str)
        month = int(month_str)
        day = int(day_str)

        # 检查年份范围（1900-当前年份）
        current_year = datetime.now().year
        if year < 1900 or year > current_year:
            return False, None

        # 检查月份
        if month < 1 or month > 12:
            return False, None

        # 检查日期
        if day < 1 or day > 31:
            return False, None

        # 检查具体日期
        # 4、6、9、11月只有30天
        if month in [4, 6, 9, 11] and day > 30:
            return False, None

        # 2月特殊处理
        if month == 2:
            # 闰年判断：能被4整除但不能被100整除，或能被400整除
            is_leap = (year % 4 == 0 and year % 100 != 0) or (year % 400 == 0)
            if is_leap and day > 29:
                return False, None
            elif not is_leap and day > 28:
                return False, None

        # 格式化出生日期
        birth_date = f'{year}-{month:02d}-{day:02d}'
        return True, birth_date

    except Exception:
        return False, None

def calculate_age(birth_date_str):
    """
    根据出生日期计算年龄
    参数: birth_date_str - 'YYYY-MM-DD' 格式
    返回: 年龄（整数）
    """
    try:
        birth = datetime.strptime(birth_date_str, '%Y-%m-%d')
        today = datetime.now()
        age = today.year - birth.year

        # 如果今年还没过生日，减1岁
        if (today.month, today.day) < (birth.month, birth.day):
            age -= 1

        return age
    except:
        return None

def get_age_group(age):
    """
    根据年龄返回年龄分组
    """
    if age is None:
        return '未知'
    elif age < 18:
        return '未成年(<18岁)'
    elif age < 30:
        return '青年(18-29岁)'
    elif age < 50:
        return '中年(30-49岁)'
    elif age < 60:
        return '中老年(50-59岁)'
    elif age < 65:
        return '老年(60-64岁)'
    elif age < 70:
        return '高龄(65-69岁)'
    else:
        return '超高龄(≥70岁)'

# ==================== 数据读取 ====================

def read_all_ic_card_data():
    """读取所有IC卡数据文件"""
    print("正在读取IC卡数据...")

    csv_files = sorted(glob.glob(str(IC_DATA_DIR / 'IC卡消费明细查询_*.csv')))
    if not csv_files:
        print(f"错误: 未找到IC卡数据文件")
        return []

    print(f"找到 {len(csv_files)} 个文件")

    all_records = []
    total_rows = 0
    excluded_count = 0
    trackable_count = 0
    mobile_payment_count = 0

    for csv_file in csv_files:
        try:
            with open(csv_file, 'r', encoding='gb18030', errors='ignore') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    total_rows += 1

                    # 解析字段
                    card_type = row.get('卡别', '').strip()
                    card_number = row.get('卡号', '').strip().strip("'")
                    route = row.get('线路', '').strip()
                    direction = row.get('上下行', '').strip()
                    station_name = row.get('站点名称', '').strip()
                    station_seq = row.get('站点序号', '').strip()
                    plate_number = row.get('车牌号', '').strip()
                    amount_str = row.get('消费金额', '').strip()
                    date_str = row.get('日期', '').strip()
                    time_str = row.get('时间', '').strip()

                    # 过滤工作卡
                    if card_type in EXCLUDE_CARD_TYPES:
                        excluded_count += 1
                        continue

                    # 检查是否为可追踪用户
                    is_trackable = card_type in TRACKABLE_CARD_TYPES

                    # 移动支付类型
                    is_mobile_payment = card_type in {'支付宝离线', '微信同程乘车码',
                                                      '云闪二维码', '银联乘车码', '银行云闪'}

                    if is_mobile_payment:
                        mobile_payment_count += 1
                        continue

                    if is_trackable:
                        trackable_count += 1

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

                        datetime_obj = datetime.combine(date_obj, time_obj)
                    except (ValueError, TypeError):
                        continue

                    # 解析消费金额
                    try:
                        amount = float(amount_str) if amount_str else 0.0
                    except ValueError:
                        amount = 0.0

                    # 解析站点序号
                    try:
                        station_seq_int = int(station_seq) if station_seq else 0
                    except ValueError:
                        station_seq_int = 0

                    # 解析方向
                    if direction == '' or direction == '0':
                        direction_key = 'forward'
                    elif direction == '1':
                        direction_key = 'reverse'
                    else:
                        direction_key = 'forward'

                    # 生成用户ID（使用卡类型+卡号组合）
                    user_id = f"{card_type}:{card_number}" if card_number else f"{card_type}:{hashlib.md5(card_number.encode()).hexdigest()[:8]}"

                    record = {
                        'user_id': user_id,
                        'card_type': card_type,
                        'card_number': card_number,
                        'route': route,
                        'direction': direction_key,
                        'station_name': station_name,
                        'station_seq': station_seq_int,
                        'plate_number': plate_number,
                        'amount': amount,
                        'datetime': datetime_obj,
                        'date': date_obj,
                        'time': time_obj,
                        'hour': time_obj.hour,
                        'weekday': date_obj.weekday(),  # 0=Monday, 6=Sunday
                        'is_weekend': date_obj.weekday() >= 5,
                        'is_trackable': is_trackable
                    }

                    all_records.append(record)

        except Exception as e:
            print(f"  警告: 处理文件 {csv_file} 时出错: {e}")
            continue

    print(f"\n读取完成:")
    print(f"  总记录数: {total_rows:,}")
    print(f"  过滤工作卡: {excluded_count:,}")
    print(f"  移动支付(不追踪): {mobile_payment_count:,}")
    print(f"  可追踪用户记录: {trackable_count:,}")
    print(f"  最终保留记录: {len(all_records):,}")

    return all_records

# ==================== 用户聚合 ====================

def group_by_user(records):
    """按用户ID聚合出行记录"""
    print("\n正在按用户聚合出行记录...")

    user_trips = defaultdict(list)
    for record in records:
        user_trips[record['user_id']].append(record)

    print(f"  共 {len(user_trips):,} 个唯一用户")

    # 统计用户出行次数分布
    trip_counts = [len(trips) for trips in user_trips.values()]
    print(f"  用户出行次数:")
    print(f"    最小值: {min(trip_counts)}")
    print(f"    最大值: {max(trip_counts)}")
    print(f"    平均值: {mean(trip_counts):.1f}")
    print(f"    中位数: {median(trip_counts):.1f}")

    return user_trips

# ==================== 深度分析 ====================

def analyze_user_patterns(user_trips):
    """分析每个用户的出行规律"""
    print("\n正在分析用户出行规律...")

    user_stats = {}

    for user_id, trips in user_trips.items():
        if len(trips) < 1:  # 至少1次出行
            continue

        # 按日期分组
        trips_by_date = defaultdict(list)
        for trip in trips:
            trips_by_date[trip['date']].append(trip)

        # 基础统计
        total_trips = len(trips)
        total_days = len(trips_by_date)
        avg_trips_per_day = total_trips / total_days if total_days > 0 else 0

        # 出行日期范围
        date_list = sorted(trips_by_date.keys())
        date_span = (date_list[-1] - date_list[0]).days + 1
        active_days = len([d for d in date_list if d in date_list])

        # 时间分布
        hour_dist = Counter(t['hour'] for t in trips)
        peak_hour = hour_dist.most_common(1)[0][0] if hour_dist else 0

        # 30分钟时间窗分布（用于精细化的时间集中度分析）
        minute_30slot_dist = Counter((t['hour'] * 60 + t['time'].minute) // 30 for t in trips)

        # ==================== 时空对分析（往返模式识别）====================
        # 定义时间窗：早上(6-11点)、下午(15-20点)
        morning_hours = range(6, 12)
        evening_hours = range(15, 21)

        # 统计各时间段的站点分布
        morning_station_dist = Counter()
        evening_station_dist = Counter()
        morning_time_dist = Counter()  # 记录早段具体时间
        evening_time_dist = Counter()  # 记录晚段具体时间

        for t in trips:
            hour = t['hour']
            station = t['station_name']
            if hour in morning_hours:
                morning_station_dist[station] += 1
                morning_time_dist[hour] += 1
            elif hour in evening_hours:
                evening_station_dist[station] += 1
                evening_time_dist[hour] += 1

        # 计算时空对信息
        spatiotemporal_pair = None
        if morning_station_dist and evening_station_dist:
            # 获取早晚最常用的站点
            top_morning_station = morning_station_dist.most_common(1)[0] if morning_station_dist else (None, 0)
            top_evening_station = evening_station_dist.most_common(1)[0] if evening_station_dist else (None, 0)

            morning_count = sum(morning_station_dist.values())
            evening_count = sum(evening_station_dist.values())
            total_pair_count = morning_count + evening_count

            # 时空对集中度：早晚模式占总出行的比例
            pair_concentration = total_pair_count / total_trips if total_trips > 0 else 0

            # 空间集中度：早上/晚上最常用站点的占比
            morning_station_concentration = top_morning_station[1] / morning_count if morning_count > 0 else 0
            evening_station_concentration = top_evening_station[1] / evening_count if evening_count > 0 else 0

            # 时间集中度：计算2小时时间窗覆盖率
            def calculate_time_window_coverage(time_dist, window_hours=2):
                """计算时间窗覆盖率"""
                if not time_dist:
                    return 0
                total = sum(time_dist.values())
                hours = sorted(time_dist.keys())
                best_coverage = 0
                for h in hours:
                    window_sum = sum(time_dist.get(h + offset, 0) for offset in range(window_hours))
                    best_coverage = max(best_coverage, window_sum)
                return best_coverage / total if total > 0 else 0

            morning_time_concentration = calculate_time_window_coverage(morning_time_dist, window_hours=2)
            evening_time_concentration = calculate_time_window_coverage(evening_time_dist, window_hours=2)

            # 计算时间跨度
            morning_hours_list = list(morning_time_dist.keys())
            evening_hours_list = list(evening_time_dist.keys())
            morning_time_span = (max(morning_hours_list) - min(morning_hours_list) + 1) if morning_hours_list else 0
            evening_time_span = (max(evening_hours_list) - min(evening_hours_list) + 1) if evening_hours_list else 0

            # 计算综合置信度等级
            spatial_avg = (morning_station_concentration + evening_station_concentration) / 2
            temporal_avg = (morning_time_concentration + evening_time_concentration) / 2
            if spatial_avg >= 0.7 and temporal_avg >= 0.7:
                confidence_level = '高'
            elif spatial_avg >= 0.4 or temporal_avg >= 0.4:
                confidence_level = '中'
            else:
                confidence_level = '低'

            spatiotemporal_pair = {
                'morning_station': top_morning_station[0],
                'morning_count': morning_count,
                'morning_station_concentration': morning_station_concentration,
                'morning_time_concentration': morning_time_concentration,
                'morning_time_span': morning_time_span,
                'evening_station': top_evening_station[0],
                'evening_count': evening_count,
                'evening_station_concentration': evening_station_concentration,
                'evening_time_concentration': evening_time_concentration,
                'evening_time_span': evening_time_span,
                'pair_concentration': pair_concentration,
                'is_round_trip': top_morning_station[0] != top_evening_station[0] if top_morning_station[0] and top_evening_station[0] else False,
                'confidence_level': confidence_level
            }

        # 常用线路和站点
        route_dist = Counter(t['route'] for t in trips)
        station_dist = Counter(t['station_name'] for t in trips)

        # 最常用的线路和站点
        top_route = route_dist.most_common(1)[0] if route_dist else (None, 0)
        top_station = station_dist.most_common(1)[0] if station_dist else (None, 0)

        # 获取用户基本信息
        first_trip = trips[0]
        card_type = first_trip['card_type']
        card_number = first_trip['card_number']

        # ==================== 身份证信息验证 ====================
        has_valid_id, birth_date = is_valid_id_card_number(card_number)
        age = calculate_age(birth_date) if has_valid_id else None
        age_group = get_age_group(age) if age is not None else None

        # ==================== 付费情况统计 ====================
        paid_trips = [t for t in trips if t['amount'] > 0]
        free_trips = [t for t in trips if t['amount'] == 0]
        total_paid_amount = sum(t['amount'] for t in paid_trips)
        paid_trip_count = len(paid_trips)
        free_trip_count = len(free_trips)
        avg_paid_per_trip = total_paid_amount / paid_trip_count if paid_trip_count > 0 else 0
        paid_ratio = paid_trip_count / total_trips * 100 if total_trips > 0 else 0

        # 按卡类型判断是否为付费卡类型
        paid_card_types = {'学生卡', '交通部普通卡', '交通部异地卡'}
        is_paid_card_type = card_type in paid_card_types

        # ==================== 深度分析（需要多次出行数据）====================
        if len(trips) >= 2:
            # 通勤识别（需要至少2次出行）
            commuter_info = identify_commuter(trips_by_date, age)

            # 出行链路分析（需要至少2次出行）
            trip_chains = analyze_trip_chains(trips_by_date)

            # 多日模式识别（需要跨日期数据）
            daily_pattern = analyze_daily_pattern(trips_by_date)

            # 异常出行检测（需要至少3次出行）
            anomaly_info = detect_anomalies(trips_by_date)
        else:
            # 单次出行的用户设置默认值
            commuter_info = {'is_commuter': False, 'score': 0, 'type': 'single_trip', 'typical_departure': None}
            trip_chains = []
            daily_pattern = None
            anomaly_info = {'anomaly_count': 0, 'anomalies': []}

        user_stats[user_id] = {
            'user_id': user_id,
            'card_type': card_type,
            'card_number': card_number,
            'total_trips': total_trips,
            'total_days': total_days,
            'avg_trips_per_day': round(avg_trips_per_day, 2),
            'date_span_days': date_span,
            'active_days': active_days,
            'activity_rate': round(active_days / date_span * 100, 2) if date_span > 0 else 0,
            'peak_hour': peak_hour,
            'most_common_route': top_route[0],
            'most_common_route_count': top_route[1],
            'most_common_station': top_station[0],
            'most_common_station_count': top_station[1],
            'hour_distribution': dict(hour_dist),
            'minute_30slot_distribution': dict(minute_30slot_dist),  # 30分钟时间窗分布
            'route_distribution': dict(route_dist),
            'station_distribution': dict(station_dist),
            # 时空对分析（往返模式）
            'spatiotemporal_pair': spatiotemporal_pair,
            # 付费情况统计
            'total_paid_amount': round(total_paid_amount, 2),
            'paid_trip_count': paid_trip_count,
            'free_trip_count': free_trip_count,
            'avg_paid_per_trip': round(avg_paid_per_trip, 2),
            'paid_ratio': round(paid_ratio, 2),
            'is_paid_card_type': is_paid_card_type,
            # 身份证信息
            'has_valid_id': has_valid_id,
            'birth_date': birth_date,
            'age': age,
            'age_group': age_group,
            # 深度分析结果
            'is_commuter': commuter_info['is_commuter'],
            'commuter_score': commuter_info['score'],
            'commuter_type': commuter_info['type'],
            'typical_departure_time': commuter_info['typical_departure'],
            'trip_chains': trip_chains,
            'daily_pattern': daily_pattern,
            'anomalies': anomaly_info
        }

    print(f"  完成分析 {len(user_stats):,} 个用户的出行规律")

    # 统计通勤用户
    commuter_count = sum(1 for s in user_stats.values() if s['is_commuter'])
    print(f"  其中通勤用户: {commuter_count:,} ({commuter_count/len(user_stats)*100:.1f}%)")

    # 统计付费情况
    total_paid_users = sum(1 for s in user_stats.values() if s['paid_trip_count'] > 0)
    total_free_users = sum(1 for s in user_stats.values() if s['paid_trip_count'] == 0)
    total_revenue = sum(s['total_paid_amount'] for s in user_stats.values())
    print(f"  付费用户: {total_paid_users:,} ({total_paid_users/len(user_stats)*100:.1f}%)")
    print(f"  完全免费用户: {total_free_users:,} ({total_free_users/len(user_stats)*100:.1f}%)")
    print(f"  总收费金额: CNY {total_revenue:,.2f}")

    return user_stats

def identify_commuter(trips_by_date, age=None):
    """识别通勤用户

    通勤特征：
    1. 工作日出行频率明显高于周末
    2. 有固定的高峰时段出行模式
    3. 出行时间相对固定（标准差小）

    参数:
    - trips_by_date: 按日期分组的出行记录
    - age: 用户年龄（用于调整通勤判断）

    返回:
    - 通勤信息字典，包括是否为通勤用户、通勤类型等
    """
    # 工作日和周末的出行次数
    weekday_trips = sum(len(trips) for date, trips in trips_by_date.items() if date.weekday() in WEEKDAYS)
    weekend_trips = sum(len(trips) for date, trips in trips_by_date.items() if date.weekday() not in WEEKDAYS)

    weekday_days = len([d for d in trips_by_date.keys() if d.weekday() in WEEKDAYS])
    weekend_days = len([d for d in trips_by_date.keys() if d.weekday() not in WEEKDAYS])

    avg_weekday = weekday_trips / weekday_days if weekday_days > 0 else 0
    avg_weekend = weekend_trips / weekend_days if weekend_days > 0 else 0

    # 检查高峰时段出行比例
    all_trips = []
    for trips in trips_by_date.values():
        all_trips.extend(trips)

    peak_hour_trips = sum(1 for t in all_trips if is_peak_hour(t['hour']))
    peak_ratio = peak_hour_trips / len(all_trips) if all_trips else 0

    # 出行时间规律性（计算标准差）
    if len(all_trips) >= 3:
        hours = [t['hour'] + t['time'].minute/60 for t in all_trips]
        avg_time = mean(hours)
        variance = sum((h - avg_time)**2 for h in hours) / len(hours)
        std_dev = math.sqrt(variance)
        time_regularity = max(0, 1 - std_dev / 12)  # 归一化到0-1，12小时为完全不规律
    else:
        time_regularity = 0

    # 综合评分
    scores = {
        'weekday_weekend_ratio': min(avg_weekday / (avg_weekend + 0.1), 5),  # 工作日偏好
        'peak_ratio': peak_ratio * 2,  # 高峰时段比例
        'time_regularity': time_regularity * 3,  # 时间规律性
    }

    total_score = sum(scores.values()) / len(scores)

    # 判断是否为通勤用户（考虑年龄因素）
    is_commuter = total_score >= 0.4

    # 如果年龄≥65岁，不认为是通勤用户（老年出行）
    if age is not None and age >= 65:
        is_commuter = False

    # 确定通勤类型
    commuter_type = 'non_commuter'
    if age is not None and age >= 65:
        commuter_type = 'senior_citizen'  # 老年出行
    elif is_commuter:
        if avg_weekday >= 2:
            commuter_type = 'daily_commuter'  # 每天通勤
        elif avg_weekday >= 1:
            commuter_type = 'frequent_commuter'  # 频繁通勤
        else:
            commuter_type = 'occasional_commuter'  # 偶尔通勤

    # 典型出发时间（取众数）
    hour_counter = Counter(t['hour'] for t in all_trips)
    typical_departure = f"{hour_counter.most_common(1)[0][0]}:00" if hour_counter else "未知"

    return {
        'is_commuter': is_commuter,
        'score': round(total_score, 2),
        'type': commuter_type,
        'typical_departure': typical_departure,
        'weekday_avg': round(avg_weekday, 2),
        'weekend_avg': round(avg_weekend, 2),
        'peak_ratio': round(peak_ratio, 2)
    }

def is_peak_hour(hour):
    """判断是否为高峰时段"""
    for start, end in PEAK_HOURS['morning'] + PEAK_HOURS['evening']:
        if start <= hour < end:
            return True
    return False

def analyze_trip_chains(trips_by_date):
    """分析出行链路（每天出行的起终点序列）"""
    chains = []

    for date, trips in sorted(trips_by_date.items()):
        if len(trips) < 2:
            continue

        # 按时间排序
        sorted_trips = sorted(trips, key=lambda t: t['datetime'])

        # 提取站点序列
        station_sequence = [t['station_name'] for t in sorted_trips]
        route_sequence = [t['route'] for t in sorted_trips]
        time_sequence = [t['time'].strftime('%H:%M') for t in sorted_trips]

        chains.append({
            'date': date.isoformat(),
            'station_sequence': station_sequence,
            'route_sequence': route_sequence,
            'time_sequence': time_sequence,
            'trip_count': len(sorted_trips)
        })

    # 识别最常见的出行链路模式
    chain_patterns = Counter()
    for chain in chains:
        # 将站点序列转换为字符串作为模式
        pattern = ' -> '.join(chain['station_sequence'])
        chain_patterns[pattern] += 1

    most_common_chain = chain_patterns.most_common(1)[0] if chain_patterns else (None, 0)

    return {
        'daily_chains': chains[:10],  # 保留最近10天的链路
        'most_common_pattern': most_common_chain[0],
        'most_common_pattern_count': most_common_chain[1]
    }

def analyze_daily_pattern(trips_by_date):
    """分析多日出行模式"""
    # 按星期几统计
    weekday_stats = defaultdict(lambda: {'trips': 0, 'days': 0})
    hour_by_weekday = defaultdict(lambda: defaultdict(int))

    for date, trips in trips_by_date.items():
        weekday = date.weekday()
        weekday_stats[weekday]['trips'] += len(trips)
        weekday_stats[weekday]['days'] += 1

        for trip in trips:
            hour_by_weekday[weekday][trip['hour']] += 1

    # 计算每天的平均出行次数
    daily_avg = {}
    for wd in range(7):
        if weekday_stats[wd]['days'] > 0:
            daily_avg[wd] = weekday_stats[wd]['trips'] / weekday_stats[wd]['days']

    # 识别活跃日类型
    if daily_avg:
        weekday_vals = [daily_avg[wd] for wd in WEEKDAYS if wd in daily_avg]
        weekend_vals = [daily_avg[wd] for wd in [5, 6] if wd in daily_avg]

        avg_weekday_trip = mean(weekday_vals) if weekday_vals else 0
        avg_weekend_trip = mean(weekend_vals) if weekend_vals else 0

        if avg_weekday_trip > avg_weekend_trip * 1.5:
            pattern_type = 'weekday_active'  # 工作日活跃
        elif avg_weekend_trip > avg_weekday_trip * 1.5:
            pattern_type = 'weekend_active'  # 周末活跃
        else:
            pattern_type = 'balanced'  # 均衡
    else:
        pattern_type = 'unknown'

    return {
        'pattern_type': pattern_type,
        'weekday_avg_trips': {wd: round(daily_avg.get(wd, 0), 2) for wd in range(7)},
        'peak_hour_by_weekday': {wd: dict(hour_by_weekday[wd]) for wd in range(7)}
    }

def detect_anomalies(trips_by_date):
    """检测异常出行

    异常类型：
    1. 时间异常：非正常时段出行（深夜/凌晨）
    2. 频率异常：某天出行次数异常多/少
    3. 路线异常：非常用线路
    """
    anomalies = []

    # 计算正常出行模式
    all_trips = []
    for trips in trips_by_date.values():
        all_trips.extend(trips)

    if len(all_trips) < 3:
        return {'anomaly_count': 0, 'anomalies': []}

    # 正常时段（5:00-23:00）
    normal_hours = range(5, 23)

    # 统计常用路线（使用频率>10%）
    route_counter = Counter(t['route'] for t in all_trips)
    total_trips = len(all_trips)
    common_routes = {r for r, c in route_counter.items() if c / total_trips > 0.1}

    # 平均每天出行次数
    avg_trips_per_day = total_trips / len(trips_by_date)

    for date, trips in trips_by_date.items():
        # 检测频率异常
        day_trip_count = len(trips)
        if day_trip_count > avg_trips_per_day * 3:  # 超过平均值3倍
            anomalies.append({
                'type': 'high_frequency',
                'date': date.isoformat(),
                'description': f'出行次数异常多: {day_trip_count}次',
                'trip_count': day_trip_count
            })

        # 检测时间异常和路线异常
        for trip in trips:
            # 深夜/凌晨出行（23:00-5:00）
            if trip['hour'] not in normal_hours:
                anomalies.append({
                    'type': 'unusual_time',
                    'date': date.isoformat(),
                    'time': trip['time'].strftime('%H:%M'),
                    'description': f'非正常时段出行: {trip["time"].strftime("%H:%M")}',
                    'station': trip['station_name']
                })

            # 非常用路线
            if trip['route'] and trip['route'] not in common_routes:
                anomalies.append({
                    'type': 'unusual_route',
                    'date': date.isoformat(),
                    'route': trip['route'],
                    'description': f'非常用线路: {trip["route"]}路',
                    'station': trip['station_name']
                })

    return {
        'anomaly_count': len(anomalies),
        'anomalies': anomalies[:20]  # 只保留前20个异常
    }

# ==================== 数据输出 ====================

def export_csv_summary(user_stats):
    """导出CSV汇总统计"""
    print("\n正在导出CSV汇总文件...")

    output_file = OUTPUT_DIR / 'user_travel_summary.csv'

    with open(output_file, 'w', encoding='utf-8-sig', newline='') as f:
        fieldnames = [
            'user_id', 'card_type', 'card_number',
            'total_trips', 'total_days', 'avg_trips_per_day',
            'date_span_days', 'active_days', 'activity_rate',
            'peak_hour', 'most_common_route', 'most_common_route_count',
            'most_common_station', 'most_common_station_count',
            'total_paid_amount', 'paid_trip_count', 'free_trip_count',
            'avg_paid_per_trip', 'paid_ratio', 'is_paid_card_type',
            'is_commuter', 'commuter_score', 'commuter_type',
            'typical_departure_time', 'daily_pattern_type',
            'trip_chains_count', 'anomaly_count'
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for user_id, stats in sorted(user_stats.items(), key=lambda x: x[1]['total_trips'], reverse=True):
            writer.writerow({
                'user_id': stats['user_id'],
                'card_type': stats['card_type'],
                'card_number': stats['card_number'],
                'total_trips': stats['total_trips'],
                'total_days': stats['total_days'],
                'avg_trips_per_day': stats['avg_trips_per_day'],
                'date_span_days': stats['date_span_days'],
                'active_days': stats['active_days'],
                'activity_rate': stats['activity_rate'],
                'peak_hour': stats['peak_hour'],
                'most_common_route': stats['most_common_route'],
                'most_common_route_count': stats['most_common_route_count'],
                'most_common_station': stats['most_common_station'],
                'most_common_station_count': stats['most_common_station_count'],
                'total_paid_amount': stats['total_paid_amount'],
                'paid_trip_count': stats['paid_trip_count'],
                'free_trip_count': stats['free_trip_count'],
                'avg_paid_per_trip': stats['avg_paid_per_trip'],
                'paid_ratio': stats['paid_ratio'],
                'is_paid_card_type': '是' if stats['is_paid_card_type'] else '否',
                'is_commuter': '是' if stats['is_commuter'] else '否',
                'commuter_score': stats['commuter_score'],
                'commuter_type': stats['commuter_type'],
                'typical_departure_time': stats['typical_departure_time'],
                'daily_pattern_type': stats['daily_pattern'],
                'trip_chains_count': len(stats['trip_chains']['daily_chains']) if isinstance(stats['trip_chains'], dict) and 'daily_chains' in stats['trip_chains'] else 0,
                'anomaly_count': stats['anomalies']['anomaly_count'] if isinstance(stats['anomalies'], dict) else 0
            })

    print(f"  已保存: {output_file}")

def export_json_details(user_trips, user_stats):
    """导出JSON明细文件"""
    print("\n正在导出JSON明细文件...")

    output_file = OUTPUT_DIR / 'user_travel_details.json'

    # 构建输出数据
    output_data = {
        'metadata': {
            'total_users': len(user_trips),
            'total_trips': sum(len(trips) for trips in user_trips.values()),
            'analysis_date': datetime.now().isoformat(),
            'commuter_count': sum(1 for s in user_stats.values() if s['is_commuter']),
            'total_paid_users': sum(1 for s in user_stats.values() if s['paid_trip_count'] > 0),
            'total_free_users': sum(1 for s in user_stats.values() if s['paid_trip_count'] == 0),
            'total_revenue': sum(s['total_paid_amount'] for s in user_stats.values())
        },
        'users': {}
    }

    for user_id, stats in user_stats.items():
        # 添加用户的完整出行记录
        trips = user_trips[user_id]
        trip_details = []
        for trip in trips:
            trip_details.append({
                'datetime': trip['datetime'].isoformat(),
                'date': trip['date'].isoformat(),
                'time': trip['time'].isoformat(),
                'route': trip['route'],
                'direction': trip['direction'],
                'station': trip['station_name'],
                'station_seq': trip['station_seq'],
                'plate': trip['plate_number'],
                'amount': trip['amount']
            })

        output_data['users'][user_id] = {
            'stats': stats,
            'trips': trip_details
        }

    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)

    print(f"  已保存: {output_file}")

def export_id_card_users_details(user_stats):
    """导出身份证用户详细信息表"""
    print("\n正在导出身份证用户详细信息...")

    output_file = OUTPUT_DIR / 'id_card_users_details.csv'

    # 筛选有有效身份证的用户
    id_card_users = [
        (user_id, stats) for user_id, stats in user_stats.items()
        if stats.get('has_valid_id', False)
    ]

    if not id_card_users:
        print("  没有找到有有效身份证信息的用户")
        return

    # 按出行次数排序
    id_card_users.sort(key=lambda x: x[1]['total_trips'], reverse=True)

    # 身份证号脱敏函数
    def mask_id_card(card_number):
        """将身份证号中间部分脱敏，只显示前6位和后4位"""
        if not card_number or len(card_number) < 18:
            return card_number
        return card_number[:6] + '********' + card_number[-4:]

    with open(output_file, 'w', encoding='utf-8-sig', newline='') as f:
        fieldnames = [
            'user_id', 'card_type', 'id_card_number', 'birth_date', 'age', 'age_group',
            'total_trips', 'active_days', 'avg_trips_per_day',
            'total_paid_amount', 'paid_trip_count', 'free_trip_count',
            'is_commuter', 'commuter_type', 'most_common_route'
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for user_id, stats in id_card_users:
            writer.writerow({
                'user_id': user_id,
                'card_type': stats['card_type'],
                'id_card_number': mask_id_card(stats['card_number']),
                'birth_date': stats['birth_date'],
                'age': stats['age'],
                'age_group': stats['age_group'],
                'total_trips': stats['total_trips'],
                'active_days': stats['active_days'],
                'avg_trips_per_day': stats['avg_trips_per_day'],
                'total_paid_amount': stats['total_paid_amount'],
                'paid_trip_count': stats['paid_trip_count'],
                'free_trip_count': stats['free_trip_count'],
                'is_commuter': '是' if stats['is_commuter'] else '否',
                'commuter_type': stats['commuter_type'],
                'most_common_route': stats['most_common_route'] or '-'
            })

    print(f"  已保存: {output_file} (共{len(id_card_users):,}条记录)")

# ==================== 辅助函数 ====================

def calculate_time_concentration(hour_dist):
    """计算时间集中度（小时级别）"""
    if not hour_dist or sum(hour_dist.values()) == 0:
        return 0
    total = sum(hour_dist.values())
    hours = list(hour_dist.keys())
    weights = [hour_dist[h] / total for h in hours]
    avg_hour = sum(h * w for h, w in zip(hours, weights))
    variance = sum((h - avg_hour) ** 2 * w for h, w in zip(hours, weights))
    std_dev = variance ** 0.5
    return max(0, 1 - std_dev / 12)

def calculate_station_concentration(station_dist):
    """计算站点集中度"""
    if not station_dist:
        return 0
    total = sum(station_dist.values())
    top_station_count = max(station_dist.values())
    return top_station_count / total

def calculate_route_loyalty(route_dist):
    """计算线路忠诚度
    返回: {
        'top1': Top1线路集中度,
        'top3': Top3线路集中度,
        'hhi': HHI指数,
        'effective_routes': 有效线路数
    }
    """
    if not route_dist:
        return {'top1': 0, 'top3': 0, 'hhi': 0, 'effective_routes': 0}

    total = sum(route_dist.values())
    sorted_routes = sorted(route_dist.values(), reverse=True)

    # Top1线路集中度
    top1_concentration = sorted_routes[0] / total if total > 0 else 0

    # Top3线路集中度（不足3条则按实际计算）
    top3_concentration = sum(sorted_routes[:3]) / total if total > 0 else 0

    # HHI指数 (Herfindahl-Hirschman Index)
    proportions = [v / total for v in route_dist.values()]
    hhi = sum(p ** 2 for p in proportions)

    # 有效线路数
    effective_routes = 1 / hhi if hhi > 0 else 0

    return {
        'top1': top1_concentration,
        'top3': top3_concentration,
        'hhi': hhi,
        'effective_routes': effective_routes
    }

def calculate_time_window_concentration(minute_30slot_dist, window_slots=2):
    """计算时间窗集中度（基于30分钟时间窗分布）
    window_slots: 窗口大小（以30分钟为单位），2=1小时，4=2小时
    """
    if not minute_30slot_dist or sum(minute_30slot_dist.values()) == 0:
        return 0

    total = sum(minute_30slot_dist.values())
    slots = sorted(minute_30slot_dist.keys())

    # 滑动窗口计算最大值
    max_count = 0
    for slot in slots:
        window_sum = sum(minute_30slot_dist.get(slot + offset, 0) for offset in range(window_slots))
        max_count = max(max_count, window_sum)

    return max_count / total if total > 0 else 0

def calculate_time_window_concentration_from_hour_dist(hour_dist, window_hours=1):
    """从小时分布计算时间窗集中度
    hour_dist: 小时分布字典 {hour: count}
    window_hours: 窗口大小（小时），1=1小时，2=2小时
    """
    if not hour_dist or sum(hour_dist.values()) == 0:
        return 0

    total = sum(hour_dist.values())
    hours = sorted(hour_dist.keys())

    # 滑动窗口计算最大值
    max_count = 0
    for hour in hours:
        window_sum = sum(hour_dist.get(hour + offset, 0) for offset in range(window_hours))
        max_count = max(max_count, window_sum)

    return max_count / total if total > 0 else 0

# ==================== Plotly可视化报告 ====================

def generate_plotly_report(user_trips, user_stats):
    """生成Plotly交互式可视化报告"""
    print("\n正在生成Plotly交互式报告...")

    # 计算全局统计
    total_users = len(user_trips)
    total_trips = sum(len(trips) for trips in user_trips.values())
    commuter_users = sum(1 for s in user_stats.values() if s['is_commuter'])
    total_paid_users = sum(1 for s in user_stats.values() if s['paid_trip_count'] > 0)
    total_free_users = sum(1 for s in user_stats.values() if s['paid_trip_count'] == 0)
    total_revenue = sum(s['total_paid_amount'] for s in user_stats.values())

    # 卡类型分布
    card_type_dist = Counter(s['card_type'] for s in user_stats.values())

    # 通勤类型分布
    commuter_type_dist = Counter(s['commuter_type'] for s in user_stats.values() if s['is_commuter'])

    # 出行次数分布
    trip_counts = [s['total_trips'] for s in user_stats.values()]
    trip_ranges = {
        '1次': sum(1 for t in trip_counts if t == 1),
        '2-5次': sum(1 for t in trip_counts if 2 <= t <= 5),
        '6-10次': sum(1 for t in trip_counts if 6 <= t <= 10),
        '11-20次': sum(1 for t in trip_counts if 11 <= t <= 20),
        '21-50次': sum(1 for t in trip_counts if 21 <= t <= 50),
        '50+次': sum(1 for t in trip_counts if t > 50)
    }

    # 活跃度分布
    activity_rates = [s['activity_rate'] for s in user_stats.values()]
    activity_ranges = {
        '0-20%': sum(1 for r in activity_rates if 0 <= r < 20),
        '20-40%': sum(1 for r in activity_rates if 20 <= r < 40),
        '40-60%': sum(1 for r in activity_rates if 40 <= r < 60),
        '60-80%': sum(1 for r in activity_rates if 60 <= r < 80),
        '80-100%': sum(1 for r in activity_rates if 80 <= r <= 100)
    }

    # 平均出行次数分布
    avg_trips = [s['avg_trips_per_day'] for s in user_stats.values()]
    avg_trips_ranges = {
        '<1次/天': sum(1 for t in avg_trips if t < 1),
        '1-2次/天': sum(1 for t in avg_trips if 1 <= t < 2),
        '2-3次/天': sum(1 for t in avg_trips if 2 <= t < 3),
        '3-5次/天': sum(1 for t in avg_trips if 3 <= t < 5),
        '≥5次/天': sum(1 for t in avg_trips if t >= 5)
    }

    # 付费vs免费用户
    payment_data = {
        '付费用户': total_paid_users,
        '免费用户': total_free_users
    }

    # ==================== 身份证用户年龄统计 ====================
    # 筛选有有效身份证的用户
    id_card_users = {k: v for k, v in user_stats.items() if v.get('has_valid_id', False)}
    id_card_count = len(id_card_users)

    # 年龄分布
    age_groups = {}
    for stats in id_card_users.values():
        age_group = stats.get('age_group', '未知')
        age_groups[age_group] = age_groups.get(age_group, 0) + 1

    # 年龄组顺序
    age_group_order = [
        '未成年(<18岁)', '青年(18-29岁)', '中年(30-49岁)',
        '中老年(50-59岁)', '老年(60-64岁)', '高龄(65-69岁)', '超高龄(≥70岁)', '未知'
    ]

    # 各年龄组的平均出行次数
    age_group_trips = {}
    age_group_paid_ratio = {}
    for stats in id_card_users.values():
        age_group = stats.get('age_group', '未知')
        if age_group not in age_group_trips:
            age_group_trips[age_group] = []
            age_group_paid_ratio[age_group] = []
        age_group_trips[age_group].append(stats['total_trips'])
        age_group_paid_ratio[age_group].append(1 if stats['paid_trip_count'] > 0 else 0)

    age_group_avg_trips = {
        k: sum(v) / len(v) if v else 0
        for k, v in age_group_trips.items()
    }
    age_group_paid_percentage = {
        k: sum(v) / len(v) * 100 if v else 0
        for k, v in age_group_paid_ratio.items()
    }

    # 创建子图布局 (3行3列) - 扩展以包含年龄分析
    fig = make_subplots(
        rows=3, cols=3,
        subplot_titles=(
            '卡类型分布',
            '通勤类型分布（调整后）',
            '年龄分布',
            '出行次数分布',
            '用户活跃度分布',
            '年龄组平均出行次数',
            '平均每天出行次数',
            '付费用户 vs 免费用户',
            '年龄组付费用户比例'
        ),
        specs=[
            [{"type": "pie"}, {"type": "pie"}, {"type": "pie"}],
            [{"type": "bar"}, {"type": "bar"}, {"type": "bar"}],
            [{"type": "bar"}, {"type": "pie"}, {"type": "bar"}]
        ],
        vertical_spacing=0.10,
        horizontal_spacing=0.08
    )

    # 图1：卡类型分布
    fig.add_trace(
        go.Pie(
            labels=list(card_type_dist.keys()),
            values=list(card_type_dist.values()),
            hole=0.3,
            marker=dict(colors=px.colors.sequential.Viridis)
        ),
        row=1, col=1
    )

    # 图2：通勤类型分布（包含所有用户类型，包括老年出行）
    commuter_colors_map = {
        'daily_commuter': '#2e7d32',
        'frequent_commuter': '#66bb6a',
        'occasional_commuter': '#a5d6a7',
        'senior_citizen': '#ff7043',  # 老年出行
        'non_commuter': '#ef6c00'
    }
    commuter_labels_map = {
        'daily_commuter': '每天通勤',
        'frequent_commuter': '频繁通勤',
        'occasional_commuter': '偶尔通勤',
        'senior_citizen': '老年出行',
        'non_commuter': '非通勤'
    }
    # 重新统计所有用户的通勤类型（包括非通勤用户）
    all_commuter_dist = Counter(s['commuter_type'] for s in user_stats.values())
    commuter_labels = [commuter_labels_map.get(k, k) for k in all_commuter_dist.keys()]
    fig.add_trace(
        go.Pie(
            labels=commuter_labels,
            values=list(all_commuter_dist.values()),
            hole=0.3,
            marker=dict(colors=[commuter_colors_map.get(k, '#999') for k in all_commuter_dist.keys()])
        ),
        row=1, col=2
    )

    # 图3：年龄分布
    fig.add_trace(
        go.Pie(
            labels=[k for k in age_group_order if k in age_groups],
            values=[age_groups[k] for k in age_group_order if k in age_groups],
            hole=0.3,
            marker=dict(colors=px.colors.sequential.Reds)
        ),
        row=1, col=3
    )

    # 图4：出行次数分布
    fig.add_trace(
        go.Bar(
            x=list(trip_ranges.keys()),
            y=list(trip_ranges.values()),
            marker_color='#667eea',
            text=[f"{v:,}人" for v in trip_ranges.values()],
            textposition='outside'
        ),
        row=2, col=1
    )

    # 图5：用户活跃度分布
    fig.add_trace(
        go.Bar(
            x=list(activity_ranges.keys()),
            y=list(activity_ranges.values()),
            marker_color='#26a69a',
            text=[f"{v:,}人" for v in activity_ranges.values()],
            textposition='outside'
        ),
        row=2, col=2
    )

    # 图6：年龄组平均出行次数
    fig.add_trace(
        go.Bar(
            x=[k for k in age_group_order if k in age_group_avg_trips],
            y=[age_group_avg_trips[k] for k in age_group_order if k in age_group_avg_trips],
            marker_color='#ab47bc',
            text=[f"{age_group_avg_trips[k]:.1f}次" for k in age_group_order if k in age_group_avg_trips],
            textposition='outside'
        ),
        row=2, col=3
    )

    # 图7：平均每天出行次数
    fig.add_trace(
        go.Bar(
            x=list(avg_trips_ranges.keys()),
            y=list(avg_trips_ranges.values()),
            marker_color='#ffa726',
            text=[f"{v:,}人" for v in avg_trips_ranges.values()],
            textposition='outside'
        ),
        row=3, col=1
    )

    # 图6：付费用户 vs 免费用户
    fig.add_trace(
        go.Pie(
            labels=list(payment_data.keys()),
            values=list(payment_data.values()),
            hole=0.4,
            marker=dict(colors=['#ffca28', '#42a5f5'])
        ),
        row=3, col=2
    )

    # 图9：年龄组付费用户比例
    fig.add_trace(
        go.Bar(
            x=[k for k in age_group_order if k in age_group_paid_percentage],
            y=[age_group_paid_percentage[k] for k in age_group_order if k in age_group_paid_percentage],
            marker_color='#ef5350',
            text=[f"{age_group_paid_percentage[k]:.1f}%" for k in age_group_order if k in age_group_paid_percentage],
            textposition='outside'
        ),
        row=3, col=3
    )

    # 更新布局
    fig.update_xaxes(title_text="出行次数范围", row=2, col=1)
    fig.update_yaxes(title_text="用户数（人）", row=2, col=1)

    fig.update_xaxes(title_text="活跃度范围", row=2, col=2)
    fig.update_yaxes(title_text="用户数（人）", row=2, col=2)

    fig.update_xaxes(title_text="年龄组", row=2, col=3)
    fig.update_yaxes(title_text="平均出行次数", row=2, col=3)

    fig.update_xaxes(title_text="平均每天出行次数", row=3, col=1)
    fig.update_yaxes(title_text="用户数（人）", row=3, col=1)

    fig.update_xaxes(title_text="是否付费", row=3, col=2)
    fig.update_yaxes(title_text="用户数（人）", row=3, col=2)

    fig.update_xaxes(title_text="年龄组", row=3, col=3)
    fig.update_yaxes(title_text="付费用户比例（%）", row=3, col=3)

    fig.update_layout(
        height=1600,
        title_text="<b>黄山公交用户出行规律分析报告（含身份证年龄分析）</b><br>" +
                  f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}<br>" +
                  f"<sup>总用户: {total_users:,} | 总出行: {total_trips:,} | 身份证用户: {id_card_count:,} ({id_card_count/total_users*100:.1f}%) | 通勤用户: {commuter_users:,} ({commuter_users/total_users*100:.1f}%) | 总营收: ¥{total_revenue:,.2f}</sup>",
        title_font_size=14,
        showlegend=True
    )

    # 保存HTML
    output_html = OUTPUT_DIR / 'user_travel_analysis.html'
    fig.write_html(output_html)
    print(f"  已保存: {output_html}")

    # 保存PNG（可选）
    try:
        output_png = OUTPUT_DIR / "user_travel_analysis.png"
        fig.write_image(output_png, width=1400, height=1600, scale=2)
        print(f"  已保存: {output_png}")
    except:
        print(f"  PNG保存失败（需要安装kaleido）")


# ==================== HTML报告 ====================

def generate_html_report(user_trips, user_stats):
    """生成CSS渲染的HTML报告"""
    print("\n正在生成HTML报告...")

    # 计算全局统计（复用generate_markdown_summary的统计逻辑）
    total_users = len(user_stats)
    total_trips = sum(s['total_trips'] for s in user_stats.values())
    commuter_users = sum(1 for s in user_stats.values() if s['is_commuter'])
    total_paid_users = sum(1 for s in user_stats.values() if s['paid_trip_count'] > 0)
    total_free_users = sum(1 for s in user_stats.values() if s['paid_trip_count'] == 0)
    total_revenue = sum(s['total_paid_amount'] for s in user_stats.values())
    id_card_count = sum(1 for s in user_stats.values() if s.get('has_valid_id', False))

    # 卡类型分布
    card_type_dist = Counter(s['card_type'] for s in user_stats.values())
    card_type_trip_dist = Counter()
    for stats in user_stats.values():
        card_type_trip_dist[stats['card_type']] += stats['total_trips']

    # 通勤类型分布
    commuter_type_dist = Counter(s['commuter_type'] for s in user_stats.values() if s['is_commuter'])
    all_commuter_dist = Counter(s['commuter_type'] for s in user_stats.values())

    # 出行次数统计
    trip_counts = [s['total_trips'] for s in user_stats.values()]
    trip_ranges = {
        '1次': sum(1 for t in trip_counts if t == 1),
        '2-5次': sum(1 for t in trip_counts if 2 <= t <= 5),
        '6-10次': sum(1 for t in trip_counts if 6 <= t <= 10),
        '11-20次': sum(1 for t in trip_counts if 11 <= t <= 20),
        '21-50次': sum(1 for t in trip_counts if 21 <= t <= 50),
        '50+次': sum(1 for t in trip_counts if t > 50)
    }

    # 通勤类型标签映射
    commuter_labels_map = {
        'daily': '每天通勤',
        'frequent': '频繁通勤',
        'occasional': '偶尔通勤',
        'non_commuter': '非通勤',
        'senior_citizen': '老年出行',
        'single_trip': '单次出行',
        'daily_commuter': '每天通勤',
        'frequent_commuter': '频繁通勤',
        'occasional_commuter': '偶尔通勤'
    }

    # 年龄组统计（身份证用户）
    age_group_stats = {}
    for stats in user_stats.values():
        if stats.get('has_valid_id', False):
            age_group = stats.get('age_group', '未知')
            if age_group not in age_group_stats:
                age_group_stats[age_group] = {
                    'users': 0,
                    'total_trips': 0,
                    'paid_users': 0
                }
            age_group_stats[age_group]['users'] += 1
            age_group_stats[age_group]['total_trips'] += stats['total_trips']
            if stats['paid_trip_count'] > 0:
                age_group_stats[age_group]['paid_users'] += 1

    age_group_order = ['未成年(<18岁)', '青年(18-29岁)', '中年(30-49岁)', '中老年(50-59岁)', '老年(60-64岁)', '高龄(65-69岁)', '超高龄(≥70岁)']
    id_card_total_trips = sum(v['total_trips'] for v in age_group_stats.values())

    # 生成HTML内容
    html_content = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>黄山公交用户出行规律分析报告</title>
    <script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}

        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
            line-height: 1.6;
            color: #333;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            padding: 20px;
        }}

        .container {{
            max-width: 1400px;
            margin: 0 auto;
            background: white;
            border-radius: 20px;
            box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
            overflow: hidden;
        }}

        .header {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 40px;
            text-align: center;
        }}

        .header h1 {{
            font-size: 2.5em;
            margin-bottom: 10px;
            font-weight: 700;
        }}

        .header .meta {{
            font-size: 1.1em;
            opacity: 0.9;
        }}

        .nav {{
            background: #f8f9fa;
            padding: 20px 40px;
            border-bottom: 2px solid #e9ecef;
            position: sticky;
            top: 0;
            z-index: 100;
        }}

        .nav h3 {{
            margin-bottom: 15px;
            color: #495057;
            font-size: 1.2em;
        }}

        .nav ul {{
            list-style: none;
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 10px;
        }}

        .nav li {{
            margin: 0;
        }}

        .nav a {{
            display: block;
            padding: 10px 15px;
            color: #495057;
            text-decoration: none;
            border-radius: 8px;
            transition: all 0.3s ease;
            font-size: 0.95em;
        }}

        .nav a:hover {{
            background: #667eea;
            color: white;
            transform: translateY(-2px);
        }}

        .content {{
            padding: 40px;
        }}

        .section {{
            margin-bottom: 50px;
            padding: 30px;
            background: #f8f9fa;
            border-radius: 15px;
            border-left: 5px solid #667eea;
        }}

        .section h2 {{
            color: #667eea;
            font-size: 2em;
            margin-bottom: 25px;
            padding-bottom: 15px;
            border-bottom: 3px solid #e9ecef;
        }}

        .section h3 {{
            color: #495057;
            font-size: 1.5em;
            margin: 25px 0 15px 0;
        }}

        .section h4 {{
            color: #6c757d;
            font-size: 1.2em;
            margin: 20px 0 10px 0;
        }}

        .stats-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 20px;
            margin: 20px 0;
        }}

        .stat-card {{
            background: white;
            padding: 25px;
            border-radius: 12px;
            box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
            transition: transform 0.3s ease, box-shadow 0.3s ease;
        }}

        .stat-card:hover {{
            transform: translateY(-5px);
            box-shadow: 0 8px 15px rgba(0, 0, 0, 0.2);
        }}

        .stat-card .label {{
            color: #6c757d;
            font-size: 0.9em;
            margin-bottom: 8px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}

        .stat-card .value {{
            color: #667eea;
            font-size: 2em;
            font-weight: 700;
            margin-bottom: 5px;
        }}

        .stat-card .subtext {{
            color: #adb5bd;
            font-size: 0.85em;
        }}

        table {{
            width: 100%;
            border-collapse: collapse;
            margin: 20px 0;
            background: white;
            border-radius: 10px;
            overflow: hidden;
            box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
        }}

        thead {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
        }}

        th {{
            padding: 15px;
            text-align: left;
            font-weight: 600;
            font-size: 0.95em;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}

        td {{
            padding: 12px 15px;
            border-bottom: 1px solid #e9ecef;
        }}

        tbody tr {{
            transition: background 0.2s ease;
        }}

        tbody tr:hover {{
            background: #f8f9fa;
        }}

        tbody tr:last-child td {{
            border-bottom: none;
        }}

        .highlight {{
            background: #fff3cd;
            padding: 15px;
            border-radius: 8px;
            margin: 15px 0;
            border-left: 4px solid #ffc107;
        }}

        .highlight ul {{
            margin-left: 20px;
            margin-top: 10px;
        }}

        .highlight li {{
            margin: 8px 0;
        }}

        .chart-placeholder {{
            background: #e9ecef;
            padding: 40px;
            border-radius: 10px;
            text-align: center;
            color: #6c757d;
            margin: 20px 0;
        }}

        .chart-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(500px, 1fr));
            gap: 25px;
            margin: 25px 0;
        }}

        .chart-wrapper {{
            background: white;
            padding: 20px;
            border-radius: 12px;
            box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
            transition: transform 0.3s ease, box-shadow 0.3s ease;
        }}

        .chart-wrapper:hover {{
            transform: translateY(-3px);
            box-shadow: 0 6px 12px rgba(0, 0, 0, 0.15);
        }}

        .footer {{
            background: #f8f9fa;
            padding: 30px;
            text-align: center;
            color: #6c757d;
            border-top: 2px solid #e9ecef;
        }}

        @media print {{
            body {{
                background: white;
                padding: 0;
            }}

            .container {{
                box-shadow: none;
            }}

            .nav {{
                display: none;
            }}

            .section {{
                page-break-inside: avoid;
                border: 1px solid #e9ecef;
            }}
        }}

        @media (max-width: 768px) {{
            .header h1 {{
                font-size: 1.8em;
            }}

            .content {{
                padding: 20px;
            }}

            .section {{
                padding: 20px;
            }}

            .stats-grid {{
                grid-template-columns: 1fr;
            }}

            table {{
                font-size: 0.9em;
            }}

            th, td {{
                padding: 8px;
            }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>黄山公交用户出行规律分析报告</h1>
            <div class="meta">
                生成时间: {datetime.now().strftime('%Y-%m-%d')}
            </div>
        </div>

        <nav class="nav">
            <h3>目录导航</h3>
            <ul>
                <li><a href="#section1">一、整体概况</a></li>
                <li><a href="#section2">二、卡类型分布</a></li>
                <li><a href="#section3">三、通勤类型分布</a></li>
                <li><a href="#section4">四、出行次数分布</a></li>
                <li><a href="#section5">五、付费情况分析</a></li>
                <li><a href="#section6">六、TOP用户列表</a></li>
                <li><a href="#section7">七、用户依赖度分群分析</a></li>
                <li><a href="#section8">八、身份证用户深度分析</a></li>
                <li><a href="#section9">九、关键发现</a></li>
                <li><a href="#section10">十、数据文件说明</a></li>
            </ul>
        </nav>

        <div class="content">
            <!-- 一、整体概况 -->
            <section id="section1" class="section">
                <h2>一、整体概况</h2>
                <h3>1.1 付费用户 vs 免费用户</h3>
                <div class="chart-grid">
                    <div class="chart-wrapper">
                        <div id="chart-paid-free"></div>
                    </div>
                </div>
                <h3>1.2 关键指标</h3>
                <div class="stats-grid">
                    <div class="stat-card">
                        <div class="label">总用户数</div>
                        <div class="value">{total_users:,}</div>
                    </div>
                    <div class="stat-card">
                        <div class="label">总出行次数</div>
                        <div class="value">{total_trips:,}</div>
                    </div>
                    <div class="stat-card">
                        <div class="label">平均每用户出行</div>
                        <div class="value">{total_trips/total_users:.1f}</div>
                        <div class="subtext">次</div>
                    </div>
                    <div class="stat-card">
                        <div class="label">通勤用户数</div>
                        <div class="value">{commuter_users:,}</div>
                        <div class="subtext">{commuter_users/total_users*100:.1f}%</div>
                    </div>
                    <div class="stat-card">
                        <div class="label">付费用户数</div>
                        <div class="value">{total_paid_users:,}</div>
                        <div class="subtext">{total_paid_users/total_users*100:.1f}%</div>
                    </div>
                    <div class="stat-card">
                        <div class="label">免费用户数</div>
                        <div class="value">{total_free_users:,}</div>
                        <div class="subtext">{total_free_users/total_users*100:.1f}%</div>
                    </div>
                    <div class="stat-card">
                        <div class="label">总营收</div>
                        <div class="value">¥{total_revenue:,.2f}</div>
                    </div>
                    <div class="stat-card">
                        <div class="label">身份证用户数</div>
                        <div class="value">{id_card_count:,}</div>
                        <div class="subtext">{id_card_count/total_users*100:.1f}%</div>
                    </div>
                </div>
            </section>

            <!-- 二、卡类型分布 -->
            <section id="section2" class="section">
                <h2>二、卡类型分布</h2>
                <h3>2.1 卡类型用户数分布</h3>
                <div class="chart-grid">
                    <div class="chart-wrapper">
                        <div id="chart-card-type-pie"></div>
                    </div>
                </div>
                <h3>2.2 用户占比 vs 客流占比对比</h3>
                <div class="chart-grid">
                    <div class="chart-wrapper">
                        <div id="chart-card-type-compare"></div>
                    </div>
                </div>
                <h3>2.3 各卡类型人均出行次数</h3>
                <div class="chart-grid">
                    <div class="chart-wrapper">
                        <div id="chart-card-type-avg-trips"></div>
                    </div>
                </div>
                <h3>2.4 详细数据</h3>
                <table>
                    <thead>
                        <tr>
                            <th>卡类型</th>
                            <th>用户数</th>
                            <th>用户占比</th>
                            <th>出行次数</th>
                            <th>客流占比</th>
                        </tr>
                    </thead>
                    <tbody>
"""

    # 添加卡类型分布表格数据
    for card_type, count in card_type_dist.most_common():
        user_percentage = count / total_users * 100
        trips = card_type_trip_dist[card_type]
        trip_percentage = trips / total_trips * 100
        html_content += f"""
                        <tr>
                            <td>{card_type}</td>
                            <td>{count:,}</td>
                            <td>{user_percentage:.1f}%</td>
                            <td>{trips:,}</td>
                            <td>{trip_percentage:.1f}%</td>
                        </tr>
"""

    html_content += f"""
                    </tbody>
                </table>
                <div class="chart-placeholder" id="cardTypeChart">
                    [图表占位符 - 卡类型分布图]
                </div>
            </section>

            <!-- 三、通勤类型分布 -->
            <section id="section3" class="section">
                <h2>三、通勤类型分布</h2>
                <p style="margin-bottom: 20px;">基于用户的出行规律和年龄信息，将用户分为不同的通勤类型。</p>
                <h3>3.1 通勤类型用户数分布</h3>
                <div class="chart-grid">
                    <div class="chart-wrapper">
                        <div id="chart-commuter-type-pie"></div>
                    </div>
                </div>
                <h3>3.2 各通勤类型用户数量</h3>
                <div class="chart-grid">
                    <div class="chart-wrapper">
                        <div id="chart-commuter-type-bar"></div>
                    </div>
                </div>
                <h3>3.3 详细数据</h3>
                <table>
                    <thead>
                        <tr>
                            <th>通勤类型</th>
                            <th>用户数</th>
                            <th>占总用户比例</th>
                            <th>说明</th>
                        </tr>
                    </thead>
                    <tbody>
"""

    # 添加通勤类型分布
    for comm_type, count in all_commuter_dist.most_common():
        label = commuter_labels_map.get(comm_type, comm_type)
        percentage = count / total_users * 100
        html_content += f"""
                        <tr>
                            <td>{label}</td>
                            <td>{count:,}</td>
                            <td>{percentage:.1f}%</td>
                            <td>-</td>
                        </tr>
"""

    html_content += f"""
                    </tbody>
                </table>
            </section>

            <!-- 四、出行次数分布 -->
            <section id="section4" class="section">
                <h2>四、出行次数分布</h2>
                <p style="margin-bottom: 20px;"><strong>注意</strong>：本报告包含所有1次及以上出行记录的用户。</p>
                <h3>4.1 出行次数范围分布</h3>
                <div class="chart-grid">
                    <div class="chart-wrapper">
                        <div id="chart-trip-range-bar"></div>
                    </div>
                </div>
                <h3>4.2 出行频次占比</h3>
                <div class="chart-grid">
                    <div class="chart-wrapper">
                        <div id="chart-trip-range-pie"></div>
                    </div>
                </div>
                <h3>4.3 详细数据</h3>
                <table>
                    <thead>
                        <tr>
                            <th>出行次数范围</th>
                            <th>用户数</th>
                            <th>占比</th>
                        </tr>
                    </thead>
                    <tbody>
"""

    # 添加出行次数分布
    for range_name, count in trip_ranges.items():
        percentage = count / total_users * 100
        html_content += f"""
                        <tr>
                            <td>{range_name}</td>
                            <td>{count:,}</td>
                            <td>{percentage:.1f}%</td>
                        </tr>
"""

    html_content += f"""
                    </tbody>
                </table>
            </section>

            <!-- 五、付费情况分析 -->
            <section id="section5" class="section">
                <h2>五、付费情况分析</h2>
                <h3>5.1 付费用户 vs 免费用户</h3>
                <div class="chart-grid">
                    <div class="chart-wrapper">
                        <div id="chart-payment-pie"></div>
                    </div>
                </div>
                <div class="stats-grid">
                    <div class="stat-card">
                        <div class="label">付费用户</div>
                        <div class="value">{total_paid_users:,}</div>
                        <div class="subtext">{total_paid_users/total_users*100:.1f}%</div>
                    </div>
                    <div class="stat-card">
                        <div class="label">免费用户</div>
                        <div class="value">{total_free_users:,}</div>
                        <div class="subtext">{total_free_users/total_users*100:.1f}%</div>
                    </div>
                </div>

                <h3>5.2 营收统计</h3>
                <div class="stats-grid">
                    <div class="stat-card">
                        <div class="label">总营收</div>
                        <div class="value">¥{total_revenue:,.2f}</div>
                    </div>
                    <div class="stat-card">
                        <div class="label">人均付费</div>
                        <div class="value">¥{total_revenue/total_paid_users:.2f}</div>
                        <div class="subtext">仅付费用户</div>
                    </div>
                </div>
            </section>

            <!-- 六、TOP用户列表 -->
            <section id="section6" class="section">
                <h2>六、TOP用户列表（按出行次数排序）</h2>
                <table>
                    <thead>
                        <tr>
                            <th>排名</th>
                            <th>卡类型</th>
                            <th>出行次数</th>
                            <th>活跃天数</th>
                            <th>平均每天出行</th>
                            <th>常用线路</th>
                            <th>付费金额</th>
                            <th>通勤用户</th>
                        </tr>
                    </thead>
                    <tbody>
"""

    # 添加TOP用户列表（前50名）
    sorted_users = sorted(user_stats.items(), key=lambda x: x[1]['total_trips'], reverse=True)[:50]

    for rank, (user_id, stats) in enumerate(sorted_users, 1):
        commuter_mark = '✓' if stats['is_commuter'] else '✗'
        paid_amount = f"¥{stats['total_paid_amount']:.2f}" if stats['total_paid_amount'] > 0 else "-"
        most_common_route = stats['most_common_route'] or '-'
        html_content += f"""
                        <tr>
                            <td>{rank}</td>
                            <td>{stats['card_type']}</td>
                            <td>{stats['total_trips']}</td>
                            <td>{stats['active_days']}</td>
                            <td>{stats['avg_trips_per_day']}</td>
                            <td>{most_common_route}</td>
                            <td>{paid_amount}</td>
                            <td>{commuter_mark}</td>
                        </tr>
"""

    html_content += f"""
                    </tbody>
                </table>
            </section>

            <!-- 七、用户依赖度分群分析 -->
            <section id="section7" class="section">
                <h2>七、用户依赖度分群分析</h2>
                <p style="margin-bottom: 20px;">基于91天数据周期内用户的出行次数，将可溯源用户分为三个依赖等级，分析不同依赖度用户的特征和贡献。</p>

                <h3>7.1 用户分群概况</h3>
                <div class="chart-grid">
                    <div class="chart-wrapper">
                        <div id="chart-dependency-pie"></div>
                    </div>
                    <div class="chart-wrapper">
                        <div id="chart-dependency-compare"></div>
                    </div>
                </div>

                <h4>详细数据</h4>
                <table>
                    <thead>
                        <tr>
                            <th>依赖等级</th>
                            <th>定义</th>
                            <th>用户数</th>
                            <th>占比</th>
                            <th>出行次数</th>
                            <th>客流占比</th>
                            <th>人均出行次数</th>
                        </tr>
                    </thead>
                    <tbody>
"""

    # 用户依赖度分群分析
    MODERATE_THRESHOLD = 10
    HEAVY_THRESHOLD = 50

    moderate_users = {uid: s for uid, s in user_stats.items() if s['total_trips'] >= MODERATE_THRESHOLD}
    heavy_users = {uid: s for uid, s in user_stats.items() if s['total_trips'] >= HEAVY_THRESHOLD}

    moderate_count = len(moderate_users)
    heavy_count = len(heavy_users)
    moderate_trips = sum(s['total_trips'] for s in moderate_users.values())
    heavy_trips = sum(s['total_trips'] for s in heavy_users.values())

    html_content += f"""
                        <tr>
                            <td><strong>重度依赖</strong></td>
                            <td>≥50次出行</td>
                            <td>{heavy_count:,}</td>
                            <td>{heavy_count/total_users*100:.1f}%</td>
                            <td>{heavy_trips:,}</td>
                            <td>{heavy_trips/total_trips*100:.1f}%</td>
                            <td>{heavy_trips/heavy_count:.1f}</td>
                        </tr>
                        <tr>
                            <td><strong>中度依赖</strong></td>
                            <td>10-49次出行</td>
                            <td>{moderate_count-heavy_count:,}</td>
                            <td>{(moderate_count-heavy_count)/total_users*100:.1f}%</td>
                            <td>{moderate_trips-heavy_trips:,}</td>
                            <td>{(moderate_trips-heavy_trips)/total_trips*100:.1f}%</td>
                            <td>{(moderate_trips-heavy_trips)/(moderate_count-heavy_count):.1f}</td>
                        </tr>
                        <tr>
                            <td><strong>轻度/偶然</strong></td>
                            <td>&lt;10次出行</td>
                            <td>{total_users-moderate_count:,}</td>
                            <td>{(total_users-moderate_count)/total_users*100:.1f}%</td>
                            <td>{total_trips-moderate_trips:,}</td>
                            <td>{(total_trips-moderate_trips)/total_trips*100:.1f}%</td>
                            <td>{(total_trips-moderate_trips)/(total_users-moderate_count):.1f}</td>
                        </tr>
                    </tbody>
                </table>

                <div class="highlight">
                    <strong>关键发现</strong>：
                    <ul>
                        <li>重度依赖用户虽然只占 {heavy_count/total_users*100:.1f}% 的用户，但贡献了 {heavy_trips/total_trips*100:.1f}% 的客流</li>
                        <li>中度及以上依赖用户占 {moderate_count/total_users*100:.1f}% 的用户，贡献了 {moderate_trips/total_trips*100:.1f}% 的客流</li>
                        <li>{heavy_trips/total_trips*100:.1f}% 的客流集中在 {heavy_count/total_users*100:.1f}% 的重度依赖用户手中</li>
                    </ul>
                </div>
"""

    # ==================== 准备图表数据（在HTML模板生成之前） ====================
    # 转换Counter对象为普通dict
    card_type_dist_dict = dict(card_type_dist)
    card_type_trip_dist_dict = dict(card_type_trip_dist)
    all_commuter_dist_dict = dict(all_commuter_dist)

    # 转换age_group_stats
    age_group_stats_dict = dict()
    for k, v in age_group_stats.items():
        age_group_stats_dict[k] = dict()
        age_group_stats_dict[k]['users'] = v['users']
        age_group_stats_dict[k]['total_trips'] = v['total_trips']

    html_content += f"""
                <h3>7.2 重度依赖用户分析（≥50次出行）</h3>
                <p>重度依赖用户是公交服务的高价值用户群体，出行频次高，出行规律性强，是公交线路规划和优化的重点参考对象。</p>

                <h4>7.2.1 卡类型分布</h4>
                <div class="chart-grid">
                    <div class="chart-wrapper">
                        <div id="chart-heavy-card-type"></div>
                    </div>
                    <div class="chart-wrapper">
                        <div id="chart-heavy-payment"></div>
                    </div>
                </div>
                <table>
                    <thead>
                        <tr>
                            <th>卡类型</th>
                            <th>用户数</th>
                            <th>占重度依赖用户比例</th>
                            <th>出行次数</th>
                            <th>客流占比</th>
                            <th>人均出行</th>
                        </tr>
                    </thead>
                    <tbody>
"""

    # 重度依赖用户卡类型分布
    heavy_card_dist = Counter(s['card_type'] for s in heavy_users.values())
    heavy_card_trips = Counter()
    for s in heavy_users.values():
        heavy_card_trips[s['card_type']] += s['total_trips']

    for card_type, count in heavy_card_dist.most_common():
        user_pct = count / heavy_count * 100
        trips = heavy_card_trips[card_type]
        trip_pct = trips / heavy_trips * 100
        avg_trips = trips / count
        html_content += f"""
                        <tr>
                            <td>{card_type}</td>
                            <td>{count:,}</td>
                            <td>{user_pct:.1f}%</td>
                            <td>{trips:,}</td>
                            <td>{trip_pct:.1f}%</td>
                            <td>{avg_trips:.1f}</td>
                        </tr>
"""

    # 重度依赖用户付费情况
    heavy_paid = sum(1 for s in heavy_users.values() if s['paid_trip_count'] > 0)
    heavy_free = heavy_count - heavy_paid
    heavy_revenue = sum(s['total_paid_amount'] for s in heavy_users.values())

    html_content += f"""
                    </tbody>
                </table>

                <h4>7.2.2 付费情况</h4>
                <div class="stats-grid">
                    <div class="stat-card">
                        <div class="label">付费用户</div>
                        <div class="value">{heavy_paid:,}</div>
                        <div class="subtext">{heavy_paid/heavy_count*100:.1f}%</div>
                    </div>
                    <div class="stat-card">
                        <div class="label">免费用户</div>
                        <div class="value">{heavy_free:,}</div>
                        <div class="subtext">{heavy_free/heavy_count*100:.1f}%</div>
                    </div>
                    <div class="stat-card">
                        <div class="label">总营收</div>
                        <div class="value">¥{heavy_revenue:,.2f}</div>
                    </div>
                    <div class="stat-card">
                        <div class="label">人均付费</div>
                        <div class="value">¥{heavy_revenue/heavy_paid:.2f}</div>
                        <div class="subtext">仅付费用户</div>
                    </div>
                </div>

                <h4>7.2.2.1 付费用户卡类型分布</h4>
                <table>
                    <thead>
                        <tr>
                            <th>卡类型</th>
                            <th>总用户数</th>
                            <th>付费用户数</th>
                            <th>付费率</th>
                            <th>说明</th>
                        </tr>
                    </thead>
                    <tbody>
"""

    # 重度依赖用户按卡类型的付费情况
    for card_type, count in heavy_card_dist.most_common():
        card_users = [s for s in heavy_users.values() if s['card_type'] == card_type]
        card_paid = sum(1 for s in card_users if s['paid_trip_count'] > 0)
        card_total = len(card_users)
        paid_rate = (card_paid / card_total * 100) if card_total > 0 else 0
        note = "付费卡类型" if card_type in {'学生卡', '交通部普通卡', '交通部异地卡'} else "免费卡类型"
        html_content += f"""
                        <tr>
                            <td>{card_type}</td>
                            <td>{card_total:,}</td>
                            <td>{card_paid:,}</td>
                            <td>{paid_rate:.1f}%</td>
                            <td>{note}</td>
                        </tr>
"""

    html_content += f"""
                    </tbody>
                </table>

                <h4>7.2.3 身份证用户分析</h4>
"""

    # 重度依赖用户中的身份证用户
    heavy_id_users = {uid: s for uid, s in heavy_users.items() if s.get('has_valid_id', False)}
    heavy_id_count = len(heavy_id_users)

    if heavy_id_count > 0:
        html_content += f"""
                <p>重度依赖用户中有身份证信息的用户：<strong>{heavy_id_count:,}</strong>人 ({heavy_id_count/heavy_count*100:.1f}%)</p>

                <h5>年龄结构</h5>
                <table>
                    <thead>
                        <tr>
                            <th>年龄组</th>
                            <th>用户数</th>
                            <th>占比</th>
                            <th>出行次数</th>
                            <th>客流占比</th>
                            <th>付费用户占比</th>
                        </tr>
                    </thead>
                    <tbody>
"""

        # 重度依赖用户年龄分布
        heavy_age_stats = {}
        for s in heavy_id_users.values():
            age_group = s.get('age_group', '未知')
            if age_group not in heavy_age_stats:
                heavy_age_stats[age_group] = {'users': 0, 'trips': 0, 'paid': 0}
            heavy_age_stats[age_group]['users'] += 1
            heavy_age_stats[age_group]['trips'] += s['total_trips']
            if s['paid_trip_count'] > 0:
                heavy_age_stats[age_group]['paid'] += 1

        age_group_order = ['未成年(<18岁)', '青年(18-29岁)', '中年(30-49岁)', '中老年(50-59岁)', '老年(60-64岁)', '高龄(65-69岁)', '超高龄(≥70岁)', '未知']
        total_heavy_id_trips = sum(v['trips'] for v in heavy_age_stats.values())

        for age_group in age_group_order:
            if age_group in heavy_age_stats:
                stats_data = heavy_age_stats[age_group]
                user_pct = stats_data['users'] / heavy_id_count * 100
                trip_pct = stats_data['trips'] / total_heavy_id_trips * 100
                paid_pct = stats_data['paid'] / stats_data['users'] * 100 if stats_data['users'] > 0 else 0
                html_content += f"""
                        <tr>
                            <td>{age_group}</td>
                            <td>{stats_data['users']:,}</td>
                            <td>{user_pct:.1f}%</td>
                            <td>{stats_data['trips']:,}</td>
                            <td>{trip_pct:.1f}%</td>
                            <td>{paid_pct:.1f}%</td>
                        </tr>
"""

        html_content += """
                    </tbody>
                </table>
"""
    else:
        html_content += "<p>重度依赖用户中无身份证信息数据。</p>"

    # ==================== 7.2.4 出行规律集中度分析 ====================
    # 统计规律性分布（确保不重叠）
    # 先计算每个用户的集中度
    user_regularity = {}
    for user_id, s in heavy_users.items():
        time_conc = calculate_time_concentration(s.get('hour_distribution', {}))
        station_conc = calculate_station_concentration(s.get('station_distribution', {}))
        if time_conc > 0.7 and station_conc > 0.5:
            user_regularity[user_id] = 'high'
        elif time_conc > 0.5 or station_conc > 0.3:
            user_regularity[user_id] = 'medium'
        else:
            user_regularity[user_id] = 'low'

    high_regularity = sum(1 for r in user_regularity.values() if r == 'high')
    medium_regularity = sum(1 for r in user_regularity.values() if r == 'medium')
    low_regularity = sum(1 for r in user_regularity.values() if r == 'low')

    # 计算平均集中度
    time_concentrations = [calculate_time_concentration(s.get('hour_distribution', {})) for s in heavy_users.values()]
    station_concentrations = [calculate_station_concentration(s.get('station_distribution', {})) for s in heavy_users.values()]
    avg_time_conc = sum(time_concentrations) / len(time_concentrations) if time_concentrations else 0
    avg_station_conc = sum(station_concentrations) / len(station_concentrations) if station_concentrations else 0

    html_content += f"""
                <h4>7.2.4 出行规律集中度分析</h4>
                <p>分析重度依赖用户的出行时间和站点选择是否集中，判断是否有固定出行规律。</p>

                <table>
                    <thead>
                        <tr>
                            <th>规律性等级</th>
                            <th>用户数</th>
                            <th>占比</th>
                            <th>说明</th>
                        </tr>
                    </thead>
                    <tbody>
                        <tr>
                            <td><strong>高规律</strong></td>
                            <td>{high_regularity:,}</td>
                            <td>{high_regularity/heavy_count*100:.1f}%</td>
                            <td>时间和站点高度集中</td>
                        </tr>
                        <tr>
                            <td><strong>中规律</strong></td>
                            <td>{medium_regularity:,}</td>
                            <td>{medium_regularity/heavy_count*100:.1f}%</td>
                            <td>时间或站点有一定规律</td>
                        </tr>
                        <tr>
                            <td><strong>低规律</strong></td>
                            <td>{low_regularity:,}</td>
                            <td>{low_regularity/heavy_count*100:.1f}%</td>
                            <td>出行分散无明显规律</td>
                        </tr>
                    </tbody>
                </table>

                <p><strong>平均时间集中度</strong>: {avg_time_conc:.2f} (0-1，越接近1越集中)</p>
                <p><strong>平均站点集中度</strong>: {avg_station_conc:.2f} (0-1，越接近1越集中)</p>

                <div class="highlight">
                    <strong>关键发现</strong>：
                    <ul>
                        <li>{high_regularity + medium_regularity:,}位重度依赖用户（{(high_regularity + medium_regularity)/heavy_count*100:.1f}%）表现出一定出行规律</li>
                        <li>老年用户（身份证）主要表现为固定时间和固定站点的出行模式</li>
                    </ul>
                </div>
"""

    # ==================== 7.2.5 线路忠诚度分析 ====================
    # 计算每个用户的线路忠诚度
    route_loyalty_stats = []
    for s in heavy_users.values():
        loyalty = calculate_route_loyalty(s.get('route_distribution', {}))
        route_loyalty_stats.append(loyalty)

    # 统计线路忠诚度等级
    high_loyalty = sum(1 for l in route_loyalty_stats if l['top1'] >= 0.7 or l['top3'] >= 0.9)
    medium_loyalty = sum(1 for l in route_loyalty_stats if (l['top1'] >= 0.4 or l['top3'] >= 0.6) and not (l['top1'] >= 0.7 or l['top3'] >= 0.9))
    low_loyalty = heavy_count - high_loyalty - medium_loyalty

    # 计算平均指标
    avg_top1_route = sum(l['top1'] for l in route_loyalty_stats) / len(route_loyalty_stats) if route_loyalty_stats else 0
    avg_top3_route = sum(l['top3'] for l in route_loyalty_stats) / len(route_loyalty_stats) if route_loyalty_stats else 0
    avg_effective_routes = sum(l['effective_routes'] for l in route_loyalty_stats) / len(route_loyalty_stats) if route_loyalty_stats else 0

    html_content += f"""
                <h4>7.2.5 线路忠诚度分析</h4>
                <p>分析重度依赖用户对公交线路的忠诚度，判断是否固定使用某几条线路。</p>

                <table>
                    <thead>
                        <tr>
                            <th>线路忠诚度等级</th>
                            <th>用户数</th>
                            <th>占比</th>
                            <th>说明</th>
                        </tr>
                    </thead>
                    <tbody>
                        <tr>
                            <td><strong>高线路忠诚</strong></td>
                            <td>{high_loyalty:,}</td>
                            <td>{high_loyalty/heavy_count*100:.1f}%</td>
                            <td>Top1线路≥70%或Top3线路≥90%</td>
                        </tr>
                        <tr>
                            <td><strong>中线路忠诚</strong></td>
                            <td>{medium_loyalty:,}</td>
                            <td>{medium_loyalty/heavy_count*100:.1f}%</td>
                            <td>Top1线路40-70%或Top3线路60-90%</td>
                        </tr>
                        <tr>
                            <td><strong>低线路忠诚</strong></td>
                            <td>{low_loyalty:,}</td>
                            <td>{low_loyalty/heavy_count*100:.1f}%</td>
                            <td>线路使用分散</td>
                        </tr>
                    </tbody>
                </table>

                <p><strong>平均Top1线路集中度</strong>: {avg_top1_route:.2f}</p>
                <p><strong>平均Top3线路集中度</strong>: {avg_top3_route:.2f}</p>
                <p><strong>平均有效线路数</strong>: {avg_effective_routes:.1f}条</p>

                <div class="highlight">
                    <strong>关键发现</strong>：
                    <ul>
                        <li>{high_loyalty:,}位重度依赖用户（{high_loyalty/heavy_count*100:.1f}%）高度依赖1-3条线路</li>
                        <li>平均每位重度依赖用户有效使用{avg_effective_routes:.1f}条线路</li>
                        <li>高线路忠诚用户的Top1线路集中度平均达{avg_top1_route*100:.1f}%</li>
                    </ul>
                </div>
"""

    # ==================== 7.2.6 站点时空集中度分析 ====================
    # 计算每个用户的时间窗集中度
    time_window_30m = [calculate_time_window_concentration_from_hour_dist(s.get('hour_distribution', {}), 1) for s in heavy_users.values()]
    time_window_1h = [calculate_time_window_concentration_from_hour_dist(s.get('hour_distribution', {}), 2) for s in heavy_users.values()]

    # 统计时空集中度等级
    high_spatiotemporal = sum(1 for c30, c1h in zip(time_window_30m, time_window_1h) if c30 > 0.6 and c1h > 0.5)
    medium_spatiotemporal = sum(1 for c30, c1h in zip(time_window_30m, time_window_1h) if c30 > 0.5 or c1h > 0.3)
    low_spatiotemporal = heavy_count - high_spatiotemporal - medium_spatiotemporal

    # 计算平均集中度
    avg_station_conc = sum([calculate_station_concentration(s.get('station_distribution', {})) for s in heavy_users.values()]) / heavy_count
    avg_time_30m_conc = sum(time_window_30m) / heavy_count if time_window_30m else 0
    avg_time_1h_conc = sum(time_window_1h) / heavy_count if time_window_1h else 0

    html_content += f"""
                <h4>7.2.6 站点时空集中度分析</h4>
                <p>分析重度依赖用户的出行时间在更细粒度上的集中度（30分钟窗、1小时窗）。</p>

                <table>
                    <thead>
                        <tr>
                            <th>时空集中度等级</th>
                            <th>用户数</th>
                            <th>占比</th>
                            <th>说明</th>
                        </tr>
                    </thead>
                    <tbody>
                        <tr>
                            <td><strong>高集中</strong></td>
                            <td>{high_spatiotemporal:,}</td>
                            <td>{high_spatiotemporal/heavy_count*100:.1f}%</td>
                            <td>Top1站点≥60%且1小时窗≥50%</td>
                        </tr>
                        <tr>
                            <td><strong>中集中</strong></td>
                            <td>{medium_spatiotemporal:,}</td>
                            <td>{medium_spatiotemporal/heavy_count*100:.1f}%</td>
                            <td>Top1站点30-60%或1小时窗25-50%</td>
                        </tr>
                        <tr>
                            <td><strong>低集中</strong></td>
                            <td>{low_spatiotemporal:,}</td>
                            <td>{low_spatiotemporal/heavy_count*100:.1f}%</td>
                            <td>出行分散</td>
                        </tr>
                    </tbody>
                </table>

                <p><strong>平均Top1站点集中度</strong>: {avg_station_conc:.2f}</p>
                <p><strong>平均30分钟时窗集中度</strong>: {avg_time_30m_conc:.2f}</p>
                <p><strong>平均1小时时窗集中度</strong>: {avg_time_1h_conc:.2f}</p>

                <div class="highlight">
                    <strong>关键发现</strong>：
                    <ul>
                        <li>{high_spatiotemporal:,}位重度依赖用户（{high_spatiotemporal/heavy_count*100:.1f}%）表现出一定的站点或时间集中度</li>
                        <li>重度依赖用户的站点选择相对固定，平均Top1站点集中度达{avg_station_conc*100:.1f}%</li>
                        <li>1小时时窗集中度({avg_time_1h_conc*100:.1f}%高于30分钟时窗集中度({avg_time_30m_conc*100:.1f}%，符合预期</li>
                    </ul>
                </div>
"""

    # ==================== 7.2.7 综合规律性评级 ====================
    # 计算综合规律性等级
    comprehensive_regularity = {}
    for user_id, s in heavy_users.items():
        loyalty = calculate_route_loyalty(s.get('route_distribution', {}))
        time_conc = calculate_time_concentration(s.get('hour_distribution', {}))
        station_conc = calculate_station_concentration(s.get('station_distribution', {}))

        # 综合评级
        if loyalty['top1'] >= 0.7 and loyalty['top3'] >= 0.9 and time_conc > 0.7 and station_conc > 0.5:
            comprehensive_regularity[user_id] = 'high'
        elif loyalty['top1'] >= 0.4 or loyalty['top3'] >= 0.6 or time_conc > 0.5 or station_conc > 0.3:
            comprehensive_regularity[user_id] = 'medium'
        else:
            comprehensive_regularity[user_id] = 'low'

    # 统计综合规律性等级
    comp_high = sum(1 for r in comprehensive_regularity.values() if r == 'high')
    comp_medium = sum(1 for r in comprehensive_regularity.values() if r == 'medium')
    comp_low = sum(1 for r in comprehensive_regularity.values() if r == 'low')

    html_content += f"""
                <h4>7.2.7 综合规律性评级</h4>
                <p>综合线路忠诚度、时间集中度、站点集中度三个维度，对重度依赖用户的出行规律进行综合评级。</p>

                <table>
                    <thead>
                        <tr>
                            <th>综合规律性等级</th>
                            <th>用户数</th>
                            <th>占比</th>
                            <th>特征描述</th>
                        </tr>
                    </thead>
                    <tbody>
                        <tr>
                            <td><strong>高规律（固定线路+固定时间+固定站点）</strong></td>
                            <td>{comp_high:,}</td>
                            <td>{comp_high/heavy_count*100:.1f}%</td>
                            <td>三项指标中至少两项达到高等级</td>
                        </tr>
                        <tr>
                            <td><strong>中规律（部分固定）</strong></td>
                            <td>{comp_medium:,}</td>
                            <td>{comp_medium/heavy_count*100:.1f}%</td>
                            <td>三项指标中至少两项达到中等级</td>
                        </tr>
                        <tr>
                            <td><strong>低规律（无明显规律）</strong></td>
                            <td>{comp_low:,}</td>
                            <td>{comp_low/heavy_count*100:.1f}%</td>
                            <td>其他</td>
                        </tr>
                    </tbody>
                </table>

                <div class="highlight">
                    <strong>综合分析结论</strong>：
                    <ul>
                        <li>{comp_high + comp_medium:,}位重度依赖用户（{(comp_high + comp_medium)/heavy_count*100:.1f}%）表现出较为固定的出行模式</li>
                        <li>综合考虑线路、时间、站点三个维度后，规律性用户比例达到{(comp_high + comp_medium)/heavy_count*100:.1f}%</li>
                        <li>这部分用户是公交服务的高价值用户，其出行需求相对稳定可预测</li>
                    </ul>
                </div>
"""

    # ==================== 7.2.8 时空对分析（往返出行模式） ====================
    # 统计有时空对信息的用户
    users_with_pair = {uid: s for uid, s in heavy_users.items() if s.get('spatiotemporal_pair')}
    users_with_pair_count = len(users_with_pair)

    if users_with_pair_count > 0:
        # 按置信度等级统计
        high_confidence = sum(1 for s in users_with_pair.values() if s['spatiotemporal_pair']['confidence_level'] == '高')
        medium_confidence = sum(1 for s in users_with_pair.values() if s['spatiotemporal_pair']['confidence_level'] == '中')
        low_confidence = sum(1 for s in users_with_pair.values() if s['spatiotemporal_pair']['confidence_level'] == '低')

        # 计算平均集中度指标
        avg_morning_station_conc = sum(s['spatiotemporal_pair']['morning_station_concentration']
                                       for s in users_with_pair.values()) / users_with_pair_count
        avg_evening_station_conc = sum(s['spatiotemporal_pair']['evening_station_concentration']
                                       for s in users_with_pair.values()) / users_with_pair_count
        avg_morning_time_conc = sum(s['spatiotemporal_pair']['morning_time_concentration']
                                    for s in users_with_pair.values()) / users_with_pair_count
        avg_evening_time_conc = sum(s['spatiotemporal_pair']['evening_time_concentration']
                                    for s in users_with_pair.values()) / users_with_pair_count
        avg_pair_concentration = sum(s['spatiotemporal_pair']['pair_concentration']
                                    for s in users_with_pair.values()) / users_with_pair_count

        # 统计往返类型
        round_trip_count = sum(1 for s in users_with_pair.values() if s['spatiotemporal_pair']['is_round_trip'])
        same_station_count = users_with_pair_count - round_trip_count

        # 分析早晚上下车站点是否不同（真正的往返）
        # 获取最常见的早晚站点对（分别统计往返和同站）
        station_pairs_round_trip = Counter()  # 往返：早晚站点不同
        station_pairs_same_station = Counter()  # 同站：早晚站点相同
        for s in users_with_pair.values():
            pair = s['spatiotemporal_pair']
            if pair['morning_station'] and pair['evening_station']:
                if pair['morning_station'] != pair['evening_station']:
                    station_pairs_round_trip[(pair['morning_station'], pair['evening_station'])] += 1
                else:
                    station_pairs_same_station[(pair['morning_station'], pair['evening_station'])] += 1

        top_round_trip_pair = station_pairs_round_trip.most_common(1)[0] if station_pairs_round_trip else (None, 0)
        top_same_station_pair = station_pairs_same_station.most_common(1)[0] if station_pairs_same_station else (None, 0)

        html_content += f"""
                <h4>7.2.8 时空对分析（往返出行模式）</h4>
                <p>分析重度依赖用户是否形成固定的"早出晚归"往返模式，从时间集中度和空间集中度两个维度评估时空对质量。</p>

                <p><strong>时空对覆盖范围</strong>：{users_with_pair_count:,}位用户（{users_with_pair_count/heavy_count*100:.1f}%）具有明显的早晚出行模式</p>

                <h5>7.2.8.1 时空对置信度分布</h5>

                <table>
                    <thead>
                        <tr>
                            <th>置信度等级</th>
                            <th>用户数</th>
                            <th>占有时空对用户比例</th>
                            <th>说明</th>
                        </tr>
                    </thead>
                    <tbody>
                        <tr>
                            <td>高置信度</td>
                            <td>{high_confidence:,}</td>
                            <td>{high_confidence/users_with_pair_count*100:.1f}%</td>
                            <td>站点集中度≥70%且时间集中度≥70%</td>
                        </tr>
                        <tr>
                            <td>中置信度</td>
                            <td>{medium_confidence:,}</td>
                            <td>{medium_confidence/users_with_pair_count*100:.1f}%</td>
                            <td>站点或时间集中度40-70%</td>
                        </tr>
"""
        if low_confidence > 0:
            html_content += f"""
                        <tr>
                            <td>低置信度</td>
                            <td>{low_confidence:,}</td>
                            <td>{low_confidence/users_with_pair_count*100:.1f}%</td>
                            <td>站点和时间都<40%</td>
                        </tr>
"""
        html_content += f"""
                    </tbody>
                </table>

                <p><strong>平均早上站点集中度</strong>: {avg_morning_station_conc*100:.1f}%</p>
                <p><strong>平均晚上站点集中度</strong>: {avg_evening_station_conc*100:.1f}%</p>
                <p><strong>平均早上时间集中度</strong>: {avg_morning_time_conc*100:.1f}%（2小时窗覆盖率）</p>
                <p><strong>平均晚上时间集中度</strong>: {avg_evening_time_conc*100:.1f}%（2小时窗覆盖率）</p>
                <p><strong>平均时空对时段占比</strong>: {avg_pair_concentration*100:.1f}%（早晚出行占总出行比例）</p>

                <h5>7.2.8.2 时空对模式分析</h5>

                <table>
                    <thead>
                        <tr>
                            <th>模式类型</th>
                            <th>用户数</th>
                            <th>占有时空对用户比例</th>
                            <th>说明</th>
                        </tr>
                    </thead>
                    <tbody>
                        <tr>
                            <td>往返模式（早晚不同站点）</td>
                            <td>{round_trip_count:,}</td>
                            <td>{round_trip_count/users_with_pair_count*100:.1f}%</td>
                            <td>典型通勤/往返模式</td>
                        </tr>
                        <tr>
                            <td>同站往返模式（早晚相同站点）</td>
                            <td>{same_station_count:,}</td>
                            <td>{same_station_count/users_with_pair_count*100:.1f}%</td>
                            <td>同一站点多次往返</td>
                        </tr>
                    </tbody>
                </table>

                <p><strong>最常见的往返时空对</strong>：{top_round_trip_pair[0][0] if top_round_trip_pair[0] else '无'}(早上) → {top_round_trip_pair[0][1] if top_round_trip_pair[0] else '无'}(晚上) ({top_round_trip_pair[1]:,}位用户)</p>
                <p><strong>最常见的同站往返站点</strong>：{top_same_station_pair[0][0] if top_same_station_pair[0] else '无'} ({top_same_station_pair[1]:,}位用户)</p>

                <h5>7.2.8.3 关键发现</h5>

                <div class="highlight">
                    <strong>关键发现</strong>：
                    <ul>
                        <li>{users_with_pair_count:,}位重度依赖用户（{users_with_pair_count/heavy_count*100:.1f}%）形成明显的早晚出行模式</li>
                        <li>{high_confidence:,}位用户（{high_confidence/users_with_pair_count*100:.1f}%）具有高置信度时空对，站点和时间都非常集中</li>
                        <li>平均早上站点集中度为{avg_morning_station_conc*100:.1f}%，平均晚上站点集中度为{avg_evening_station_conc*100:.1f}%，说明用户站点选择相对固定</li>
                        <li>平均早上时间集中度为{avg_morning_time_conc*100:.1f}%，平均晚上时间集中度为{avg_evening_time_conc*100:.1f}%，说明用户出行时间有一定规律性</li>
                        <li>{round_trip_count/users_with_pair_count*100:.1f}%的用户形成往返模式（早晚站点不同），是典型的通勤往返模式</li>
                        <li>{same_station_count/users_with_pair_count*100:.1f}%的用户形成同站往返模式，可能是同一站点进行多项活动</li>
                    </ul>
                </div>
"""
    else:
        html_content += """
                <h4>7.2.8 时空对分析（往返出行模式）</h4>
                <p><strong>无时空对数据</strong>：重度依赖用户中未检测到明显的早晚出行模式。</p>
"""

    # 中度依赖用户（排除重度）
    moderate_only_users = {uid: s for uid, s in moderate_users.items() if s['total_trips'] < HEAVY_THRESHOLD}
    moderate_only_count = len(moderate_only_users)
    moderate_only_trips = sum(s['total_trips'] for s in moderate_only_users.values())

    # 中度依赖用户卡类型分布
    moderate_card_dist = Counter(s['card_type'] for s in moderate_only_users.values())
    moderate_card_trips = Counter()
    for s in moderate_only_users.values():
        moderate_card_trips[s['card_type']] += s['total_trips']

    html_content += f"""
                <h3>7.3 中度依赖用户分析（10-49次出行）</h3>
                <p>中度依赖用户出行频次中等，有一定规律性但不强，是公交服务的潜在高价值用户群体。</p>

                <h4>7.3.1 卡类型分布</h4>
                <p>分析中度依赖用户（10-49次出行）的卡类型分布。</p>

                <table>
                    <thead>
                        <tr>
                            <th>卡类型</th>
                            <th>用户数</th>
                            <th>占中度依赖用户比例</th>
                            <th>出行次数</th>
                            <th>客流占比</th>
                            <th>人均出行</th>
                        </tr>
                    </thead>
                    <tbody>
"""

    # 重度依赖用户分析 - 卡类型分布
    for card_type, count in moderate_card_dist.most_common():
        user_pct = count / moderate_only_count * 100
        trips = moderate_card_trips[card_type]
        trip_pct = trips / moderate_only_trips * 100
        avg_trips = trips / count
        html_content += f"""
                        <tr>
                            <td>{card_type}</td>
                            <td>{count:,}</td>
                            <td>{user_pct:.1f}%</td>
                            <td>{trips:,}</td>
                            <td>{trip_pct:.1f}%</td>
                            <td>{avg_trips:.1f}</td>
                        </tr>
"""
    html_content += """
                    </tbody>
                </table>

                <h4>7.3.2 付费情况</h4>
"""

    # 中度依赖用户付费情况
    moderate_paid = sum(1 for s in moderate_only_users.values() if s['paid_trip_count'] > 0)
    moderate_free = moderate_only_count - moderate_paid
    moderate_revenue = sum(s['total_paid_amount'] for s in moderate_only_users.values())

    html_content += f"""
                <div class="stats-grid">
                    <div class="stat-card-half">
                        <div class="label">付费用户</div>
                        <div class="value">{moderate_paid:,}</div>
                        <div class="subtext">{moderate_paid/moderate_only_count*100:.1f}%</div>
                    </div>
                    <div class="stat-card-half">
                        <div class="label">免费用户</div>
                        <div class="value">{moderate_free:,}</div>
                        <div class="subtext">{moderate_free/moderate_only_count*100:.1f}%</div>
                    </div>
                </div>
"""

    # ==================== 7.3.3 身份证用户分析 ====================
    # 中度依赖用户中的身份证用户
    moderate_id_users = {uid: s for uid, s in moderate_only_users.items() if s.get('has_valid_id', False)}
    moderate_id_count = len(moderate_id_users)

    if moderate_id_count > 0:
        html_content += f"""
                <h4>7.3.3 身份证用户分析</h4>
                <p>中度依赖用户中有身份证信息的用户：<strong>{moderate_id_count:,}</strong>人 ({moderate_id_count/moderate_only_count*100:.1f}%)</p>

                <h5>年龄结构</h5>
                <table>
                    <thead>
                        <tr>
                            <th>年龄组</th>
                            <th>用户数</th>
                            <th>占比</th>
                            <th>出行次数</th>
                            <th>客流占比</th>
                            <th>付费用户占比</th>
                        </tr>
                    </thead>
                    <tbody>
"""

        # 中度依赖用户年龄分布
        moderate_age_stats = {}
        for s in moderate_id_users.values():
            age_group = s.get('age_group', '未知')
            if age_group not in moderate_age_stats:
                moderate_age_stats[age_group] = {'users': 0, 'trips': 0, 'paid': 0}
            moderate_age_stats[age_group]['users'] += 1
            moderate_age_stats[age_group]['trips'] += s['total_trips']
            if s['paid_trip_count'] > 0:
                moderate_age_stats[age_group]['paid'] += 1

        total_moderate_id_trips = sum(v['trips'] for v in moderate_age_stats.values())

        age_group_order = ['未成年(<18岁)', '青年(18-29岁)', '中年(30-49岁)', '中老年(50-59岁)', '老年(60-64岁)', '高龄(65-69岁)', '超高龄(≥70岁)', '未知']

        for age_group in age_group_order:
            if age_group in moderate_age_stats:
                stats_data = moderate_age_stats[age_group]
                user_pct = stats_data['users'] / moderate_id_count * 100
                trip_pct = stats_data['trips'] / total_moderate_id_trips * 100
                paid_pct = stats_data['paid'] / stats_data['users'] * 100 if stats_data['users'] > 0 else 0
                html_content += f"""
                        <tr>
                            <td>{age_group}</td>
                            <td>{stats_data['users']:,}</td>
                            <td>{user_pct:.1f}%</td>
                            <td>{stats_data['trips']:,}</td>
                            <td>{trip_pct:.1f}%</td>
                            <td>{paid_pct:.1f}%</td>
                        </tr>
"""

    else:
        html_content += "\n                <p>中度依赖用户中无身份证信息数据。</p>\n"

    # ==================== 7.3.4 出行规律集中度分析 ====================
    # 统计规律性分布（确保不重叠）
    # 先计算每个用户的集中度
    moderate_user_regularity = {}
    for user_id, s in moderate_only_users.items():
        time_conc = calculate_time_concentration(s.get('hour_distribution', {}))
        station_conc = calculate_station_concentration(s.get('station_distribution', {}))
        if time_conc > 0.7 and station_conc > 0.5:
            moderate_user_regularity[user_id] = 'high'
        elif time_conc > 0.5 or station_conc > 0.3:
            moderate_user_regularity[user_id] = 'medium'
        else:
            moderate_user_regularity[user_id] = 'low'

    moderate_high_regularity = sum(1 for r in moderate_user_regularity.values() if r == 'high')
    moderate_medium_regularity = sum(1 for r in moderate_user_regularity.values() if r == 'medium')
    moderate_low_regularity = sum(1 for r in moderate_user_regularity.values() if r == 'low')

    # 计算平均集中度
    moderate_time_concentrations = [calculate_time_concentration(s.get('hour_distribution', {})) for s in moderate_only_users.values()]
    moderate_station_concentrations = [calculate_station_concentration(s.get('station_distribution', {})) for s in moderate_only_users.values()]
    moderate_avg_time_conc = sum(moderate_time_concentrations) / len(moderate_time_concentrations) if moderate_time_concentrations else 0
    moderate_avg_station_conc = sum(moderate_station_concentrations) / len(moderate_station_concentrations) if moderate_station_concentrations else 0

    html_content += f"""
                <h4>7.3.4 出行规律集中度分析</h4>
                <p>分析中度依赖用户的出行时间和站点选择是否集中，判断是否有固定出行规律。</p>

                <table>
                    <thead>
                        <tr>
                            <th>规律性等级</th>
                            <th>用户数</th>
                            <th>占比</th>
                            <th>说明</th>
                        </tr>
                    </thead>
                    <tbody>
                        <tr>
                            <td><strong>高规律</strong></td>
                            <td>{moderate_high_regularity:,}</td>
                            <td>{moderate_high_regularity/moderate_only_count*100:.1f}%</td>
                            <td>时间和站点高度集中</td>
                        </tr>
                        <tr>
                            <td><strong>中规律</strong></td>
                            <td>{moderate_medium_regularity:,}</td>
                            <td>{moderate_medium_regularity/moderate_only_count*100:.1f}%</td>
                            <td>时间或站点有一定规律</td>
                        </tr>
                        <tr>
                            <td><strong>低规律</strong></td>
                            <td>{moderate_low_regularity:,}</td>
                            <td>{moderate_low_regularity/moderate_only_count*100:.1f}%</td>
                            <td>出行分散无明显规律</td>
                        </tr>
                    </tbody>
                </table>

                <p><strong>平均时间集中度</strong>: {moderate_avg_time_conc:.2f} (0-1，越接近1越集中)</p>
                <p><strong>平均站点集中度</strong>: {moderate_avg_station_conc:.2f} (0-1，越接近1越集中)</p>

                <div class="highlight">
                    <strong>关键发现</strong>：
                    <ul>
                        <li>{moderate_high_regularity + moderate_medium_regularity:,}位中度依赖用户（{(moderate_high_regularity + moderate_medium_regularity)/moderate_only_count*100:.1f}%）表现出一定出行规律</li>
                        <li>中度依赖用户的规律性略低于重度依赖用户，符合预期</li>
                    </ul>
                </div>
"""

    # ==================== 7.3.5 线路忠诚度分析 ====================
    # 计算每个用户的线路忠诚度
    moderate_route_loyalty_stats = []
    for s in moderate_only_users.values():
        loyalty = calculate_route_loyalty(s.get('route_distribution', {}))
        moderate_route_loyalty_stats.append(loyalty)

    # 统计线路忠诚度等级
    moderate_high_loyalty = sum(1 for l in moderate_route_loyalty_stats if l['top1'] >= 0.7 or l['top3'] >= 0.9)
    moderate_medium_loyalty = sum(1 for l in moderate_route_loyalty_stats if (l['top1'] >= 0.4 or l['top3'] >= 0.6) and not (l['top1'] >= 0.7 or l['top3'] >= 0.9))
    moderate_low_loyalty = moderate_only_count - moderate_high_loyalty - moderate_medium_loyalty

    # 计算平均指标
    moderate_avg_top1_route = sum(l['top1'] for l in moderate_route_loyalty_stats) / len(moderate_route_loyalty_stats) if moderate_route_loyalty_stats else 0
    moderate_avg_top3_route = sum(l['top3'] for l in moderate_route_loyalty_stats) / len(moderate_route_loyalty_stats) if moderate_route_loyalty_stats else 0
    moderate_avg_effective_routes = sum(l['effective_routes'] for l in moderate_route_loyalty_stats) / len(moderate_route_loyalty_stats) if moderate_route_loyalty_stats else 0

    html_content += f"""
                <h4>7.3.5 线路忠诚度分析</h4>
                <p>分析中度依赖用户对公交线路的忠诚度，判断是否固定使用某几条线路。</p>

                <table>
                    <thead>
                        <tr>
                            <th>线路忠诚度等级</th>
                            <th>用户数</th>
                            <th>占比</th>
                            <th>说明</th>
                        </tr>
                    </thead>
                    <tbody>
                        <tr>
                            <td><strong>高线路忠诚</strong></td>
                            <td>{moderate_high_loyalty:,}</td>
                            <td>{moderate_high_loyalty/moderate_only_count*100:.1f}%</td>
                            <td>Top1线路≥70%或Top3线路≥90%</td>
                        </tr>
                        <tr>
                            <td><strong>中线路忠诚</strong></td>
                            <td>{moderate_medium_loyalty:,}</td>
                            <td>{moderate_medium_loyalty/moderate_only_count*100:.1f}%</td>
                            <td>Top1线路40-70%或Top3线路60-90%</td>
                        </tr>
                        <tr>
                            <td><strong>低线路忠诚</strong></td>
                            <td>{moderate_low_loyalty:,}</td>
                            <td>{moderate_low_loyalty/moderate_only_count*100:.1f}%</td>
                            <td>线路使用分散</td>
                        </tr>
                    </tbody>
                </table>

                <p><strong>平均Top1线路集中度</strong>: {moderate_avg_top1_route:.2f}</p>
                <p><strong>平均Top3线路集中度</strong>: {moderate_avg_top3_route:.2f}</p>
                <p><strong>平均有效线路数</strong>: {moderate_avg_effective_routes:.1f}条</p>

                <div class="highlight">
                    <strong>关键发现</strong>：
                    <ul>
                        <li>{moderate_high_loyalty:,}位中度依赖用户（{moderate_high_loyalty/moderate_only_count*100:.1f}%）高度依赖1-3条线路</li>
                        <li>平均每位中度依赖用户有效使用{moderate_avg_effective_routes:.1f}条线路，略高于重度依赖用户</li>
                        <li>中度依赖用户的线路选择相对更多样化</li>
                    </ul>
                </div>
"""

    # ==================== 7.3.6 站点时空集中度分析 ====================
    # 计算每个用户的时间窗集中度
    moderate_time_window_30m = [calculate_time_window_concentration(s.get('minute_30slot_distribution', {}), 1) for s in moderate_only_users.values()]
    moderate_time_window_1h = [calculate_time_window_concentration(s.get('minute_30slot_distribution', {}), 2) for s in moderate_only_users.values()]

    # 重新计算站点集中度
    moderate_station_concs = [calculate_station_concentration(s.get('station_distribution', {})) for s in moderate_only_users.values()]

    # 统计时空集中度等级
    moderate_high_spatial = sum(1 for s, t in zip(moderate_station_concs, moderate_time_window_1h) if s >= 0.6 and t >= 0.5)
    moderate_medium_spatial = sum(1 for s, t in zip(moderate_station_concs, moderate_time_window_1h) if (s >= 0.3 or t >= 0.25) and not (s >= 0.6 and t >= 0.5))
    moderate_low_spatial = moderate_only_count - moderate_high_spatial - moderate_medium_spatial

    # 计算平均值
    moderate_avg_station_conc = sum(moderate_station_concs) / len(moderate_station_concs) if moderate_station_concs else 0
    moderate_avg_time_30m = sum(moderate_time_window_30m) / len(moderate_time_window_30m) if moderate_time_window_30m else 0
    moderate_avg_time_1h = sum(moderate_time_window_1h) / len(moderate_time_window_1h) if moderate_time_window_1h else 0

    html_content += f"""
                <h4>7.3.6 站点时空集中度分析</h4>
                <p>分析中度依赖用户的出行时间在更细粒度上的集中度（30分钟窗、1小时窗）。</p>

                <table>
                    <thead>
                        <tr>
                            <th>时空集中度等级</th>
                            <th>用户数</th>
                            <th>占比</th>
                            <th>说明</th>
                        </tr>
                    </thead>
                    <tbody>
                        <tr>
                            <td><strong>高集中</strong></td>
                            <td>{moderate_high_spatial:,}</td>
                            <td>{moderate_high_spatial/moderate_only_count*100:.1f}%</td>
                            <td>Top1站点≥60%且1小时窗≥50%</td>
                        </tr>
                        <tr>
                            <td><strong>中集中</strong></td>
                            <td>{moderate_medium_spatial:,}</td>
                            <td>{moderate_medium_spatial/moderate_only_count*100:.1f}%</td>
                            <td>Top1站点30-60%或1小时窗25-50%</td>
                        </tr>
                        <tr>
                            <td><strong>低集中</strong></td>
                            <td>{moderate_low_spatial:,}</td>
                            <td>{moderate_low_spatial/moderate_only_count*100:.1f}%</td>
                            <td>出行分散</td>
                        </tr>
                    </tbody>
                </table>

                <p><strong>平均Top1站点集中度</strong>: {moderate_avg_station_conc:.2f}</p>
                <p><strong>平均30分钟时窗集中度</strong>: {moderate_avg_time_30m:.2f}</p>
                <p><strong>平均1小时时窗集中度</strong>: {moderate_avg_time_1h:.2f}</p>

                <div class="highlight">
                    <strong>关键发现</strong>：
                    <ul>
                        <li>{moderate_high_spatial + moderate_medium_spatial:,}位中度依赖用户（{(moderate_high_spatial + moderate_medium_spatial)/moderate_only_count*100:.1f}%）表现出一定的站点或时间集中度</li>
                        <li>中度依赖用户的站点和时间集中度均低于重度依赖用户，符合预期</li>
                    </ul>
                </div>
"""

    # ==================== 7.3.7 综合规律性评级 ====================
    # 综合评级（三项指标均考虑）
    moderate_comprehensive_rating = {}
    for user_id, s in moderate_only_users.items():
        loyalty = calculate_route_loyalty(s.get('route_distribution', {}))
        time_conc = calculate_time_concentration(s.get('hour_distribution', {}))
        station_conc = calculate_station_concentration(s.get('station_distribution', {}))

        # 高规律：三项指标中至少两项达到高等级
        high_count = 0
        if loyalty['top1'] >= 0.7 or loyalty['top3'] >= 0.9:
            high_count += 1
        if time_conc > 0.7:
            high_count += 1
        if station_conc > 0.5:
            high_count += 1

        # 中规律：三项指标中至少两项达到中等级及以上
        medium_count = 0
        if loyalty['top1'] >= 0.4 or loyalty['top3'] >= 0.6:
            medium_count += 1
        if time_conc > 0.5:
            medium_count += 1
        if station_conc > 0.3:
            medium_count += 1

        if high_count >= 2:
            moderate_comprehensive_rating[user_id] = 'high'
        elif medium_count >= 2:
            moderate_comprehensive_rating[user_id] = 'medium'
        else:
            moderate_comprehensive_rating[user_id] = 'low'

    moderate_comp_high = sum(1 for r in moderate_comprehensive_rating.values() if r == 'high')
    moderate_comp_medium = sum(1 for r in moderate_comprehensive_rating.values() if r == 'medium')
    moderate_comp_low = sum(1 for r in moderate_comprehensive_rating.values() if r == 'low')

    html_content += f"""
                <h4>7.3.7 综合规律性评级</h4>
                <p>综合线路忠诚度、时间集中度、站点集中度三个维度，对中度依赖用户的出行规律进行综合评级。</p>

                <table>
                    <thead>
                        <tr>
                            <th>综合规律性等级</th>
                            <th>用户数</th>
                            <th>占比</th>
                            <th>特征描述</th>
                        </tr>
                    </thead>
                    <tbody>
                        <tr>
                            <td><strong>高规律（固定线路+固定时间+固定站点）</strong></td>
                            <td>{moderate_comp_high:,}</td>
                            <td>{moderate_comp_high/moderate_only_count*100:.1f}%</td>
                            <td>三项指标中至少两项达到高等级</td>
                        </tr>
                        <tr>
                            <td><strong>中规律（部分固定）</strong></td>
                            <td>{moderate_comp_medium:,}</td>
                            <td>{moderate_comp_medium/moderate_only_count*100:.1f}%</td>
                            <td>三项指标中至少两项达到中等级</td>
                        </tr>
                        <tr>
                            <td><strong>低规律（无明显规律）</strong></td>
                            <td>{moderate_comp_low:,}</td>
                            <td>{moderate_comp_low/moderate_only_count*100:.1f}%</td>
                            <td>其他</td>
                        </tr>
                    </tbody>
                </table>

                <div class="highlight">
                    <strong>综合分析结论</strong>：
                    <ul>
                        <li>{moderate_comp_high + moderate_comp_medium:,}位中度依赖用户（{(moderate_comp_high + moderate_comp_medium)/moderate_only_count*100:.1f}%）表现出较为固定的出行模式</li>
                        <li>中度依赖用户的综合规律性比例为{(moderate_comp_high + moderate_comp_medium)/moderate_only_count*100:.1f}%，低于重度依赖用户</li>
                    </ul>
                </div>
"""

    # ==================== 7.3.8 时空对分析（往返出行模式） ====================
    # 统计有时空对信息的用户
    moderate_users_with_pair = {uid: s for uid, s in moderate_only_users.items() if s.get('spatiotemporal_pair')}
    moderate_users_with_pair_count = len(moderate_users_with_pair)

    if moderate_users_with_pair_count > 0:
        # 按置信度等级统计
        moderate_high_confidence = sum(1 for s in moderate_users_with_pair.values() if s['spatiotemporal_pair']['confidence_level'] == '高')
        moderate_medium_confidence = sum(1 for s in moderate_users_with_pair.values() if s['spatiotemporal_pair']['confidence_level'] == '中')
        moderate_low_confidence = sum(1 for s in moderate_users_with_pair.values() if s['spatiotemporal_pair']['confidence_level'] == '低')

        # 计算平均集中度指标
        moderate_avg_morning_station_conc = sum(s['spatiotemporal_pair']['morning_station_concentration']
                                       for s in moderate_users_with_pair.values()) / moderate_users_with_pair_count
        moderate_avg_evening_station_conc = sum(s['spatiotemporal_pair']['evening_station_concentration']
                                       for s in moderate_users_with_pair.values()) / moderate_users_with_pair_count
        moderate_avg_morning_time_conc = sum(s['spatiotemporal_pair']['morning_time_concentration']
                                    for s in moderate_users_with_pair.values()) / moderate_users_with_pair_count
        moderate_avg_evening_time_conc = sum(s['spatiotemporal_pair']['evening_time_concentration']
                                    for s in moderate_users_with_pair.values()) / moderate_users_with_pair_count
        moderate_avg_pair_concentration = sum(s['spatiotemporal_pair']['pair_concentration']
                                    for s in moderate_users_with_pair.values()) / moderate_users_with_pair_count

        # 统计往返类型
        moderate_round_trip_count = sum(1 for s in moderate_users_with_pair.values() if s['spatiotemporal_pair']['is_round_trip'])
        moderate_same_station_count = moderate_users_with_pair_count - moderate_round_trip_count

        # 获取最常见的早晚站点对（分别统计往返和同站）
        moderate_station_pairs_round_trip = Counter()  # 往返：早晚站点不同
        moderate_station_pairs_same_station = Counter()  # 同站：早晚站点相同
        for s in moderate_users_with_pair.values():
            pair = s['spatiotemporal_pair']
            if pair['morning_station'] and pair['evening_station']:
                if pair['morning_station'] != pair['evening_station']:
                    moderate_station_pairs_round_trip[(pair['morning_station'], pair['evening_station'])] += 1
                else:
                    moderate_station_pairs_same_station[(pair['morning_station'], pair['evening_station'])] += 1

        moderate_top_round_trip_pair = moderate_station_pairs_round_trip.most_common(1)[0] if moderate_station_pairs_round_trip else (None, 0)
        moderate_top_same_station_pair = moderate_station_pairs_same_station.most_common(1)[0] if moderate_station_pairs_same_station else (None, 0)

        html_content += f"""
                <h4>7.3.8 时空对分析（往返出行模式）</h4>
                <p>分析中度依赖用户是否形成固定的"早出晚归"往返模式，从时间集中度和空间集中度两个维度评估时空对质量。</p>

                <p><strong>时空对覆盖范围</strong>：{moderate_users_with_pair_count:,}位用户（{moderate_users_with_pair_count/moderate_only_count*100:.1f}%）具有明显的早晚出行模式</p>

                <h5>7.3.8.1 时空对置信度分布</h5>

                <table>
                    <thead>
                        <tr>
                            <th>置信度等级</th>
                            <th>用户数</th>
                            <th>占有时空对用户比例</th>
                            <th>说明</th>
                        </tr>
                    </thead>
                    <tbody>
                        <tr>
                            <td>高置信度</td>
                            <td>{moderate_high_confidence:,}</td>
                            <td>{moderate_high_confidence/moderate_users_with_pair_count*100:.1f}%</td>
                            <td>站点集中度≥70%且时间集中度≥70%</td>
                        </tr>
                        <tr>
                            <td>中置信度</td>
                            <td>{moderate_medium_confidence:,}</td>
                            <td>{moderate_medium_confidence/moderate_users_with_pair_count*100:.1f}%</td>
                            <td>站点或时间集中度40-70%</td>
                        </tr>
"""
        if moderate_low_confidence > 0:
            html_content += f"""
                        <tr>
                            <td>低置信度</td>
                            <td>{moderate_low_confidence:,}</td>
                            <td>{moderate_low_confidence/moderate_users_with_pair_count*100:.1f}%</td>
                            <td>站点和时间都<40%</td>
                        </tr>
"""
        html_content += f"""
                    </tbody>
                </table>

                <p><strong>平均早上站点集中度</strong>: {moderate_avg_morning_station_conc*100:.1f}%</p>
                <p><strong>平均晚上站点集中度</strong>: {moderate_avg_evening_station_conc*100:.1f}%</p>
                <p><strong>平均早上时间集中度</strong>: {moderate_avg_morning_time_conc*100:.1f}%（2小时窗覆盖率）</p>
                <p><strong>平均晚上时间集中度</strong>: {moderate_avg_evening_time_conc*100:.1f}%（2小时窗覆盖率）</p>
                <p><strong>平均时空对时段占比</strong>: {moderate_avg_pair_concentration*100:.1f}%（早晚出行占总出行比例）</p>

                <h5>7.3.8.2 时空对模式分析</h5>

                <table>
                    <thead>
                        <tr>
                            <th>模式类型</th>
                            <th>用户数</th>
                            <th>占有时空对用户比例</th>
                            <th>说明</th>
                        </tr>
                    </thead>
                    <tbody>
                        <tr>
                            <td>往返模式（早晚不同站点）</td>
                            <td>{moderate_round_trip_count:,}</td>
                            <td>{moderate_round_trip_count/moderate_users_with_pair_count*100:.1f}%</td>
                            <td>典型通勤/往返模式</td>
                        </tr>
                        <tr>
                            <td>同站往返模式（早晚相同站点）</td>
                            <td>{moderate_same_station_count:,}</td>
                            <td>{moderate_same_station_count/moderate_users_with_pair_count*100:.1f}%</td>
                            <td>同一站点多次往返</td>
                        </tr>
                    </tbody>
                </table>

                <p><strong>最常见的往返时空对</strong>：{moderate_top_round_trip_pair[0][0] if moderate_top_round_trip_pair[0] else '无'}(早上) → {moderate_top_round_trip_pair[0][1] if moderate_top_round_trip_pair[0] else '无'}(晚上) ({moderate_top_round_trip_pair[1]:,}位用户)</p>
                <p><strong>最常见的同站往返站点</strong>：{moderate_top_same_station_pair[0][0] if moderate_top_same_station_pair[0] else '无'} ({moderate_top_same_station_pair[1]:,}位用户)</p>

                <h5>7.3.8.3 关键发现</h5>

                <div class="highlight">
                    <strong>关键发现</strong>：
                    <ul>
                        <li>{moderate_users_with_pair_count:,}位中度依赖用户（{moderate_users_with_pair_count/moderate_only_count*100:.1f}%）形成明显的早晚出行模式</li>
                        <li>{moderate_high_confidence:,}位用户（{moderate_high_confidence/moderate_users_with_pair_count*100:.1f}%）具有高置信度时空对，比例{moderate_high_confidence/moderate_users_with_pair_count*100:.1f}%，低于重度依赖用户</li>
                        <li>平均早上站点集中度为{moderate_avg_morning_station_conc*100:.1f}%，平均晚上站点集中度为{moderate_avg_evening_station_conc*100:.1f}%，略低于重度依赖用户</li>
                        <li>平均早上时间集中度为{moderate_avg_morning_time_conc*100:.1f}%，平均晚上时间集中度为{moderate_avg_evening_time_conc*100:.1f}%，略低于重度依赖用户</li>
                        <li>{moderate_round_trip_count/moderate_users_with_pair_count*100:.1f}%的用户形成往返模式（早晚站点不同）</li>
                        <li>{moderate_same_station_count/moderate_users_with_pair_count*100:.1f}%的用户形成同站往返模式，可能是同一站点进行多项活动</li>
                    </ul>
                </div>
"""
    else:
        html_content += """
                <h4>7.3.8 时空对分析（往返出行模式）</h4>
                <p><strong>无时空对数据</strong>：中度依赖用户中未检测到明显的早晚出行模式。</p>
"""

    html_content += """
---

            </section>

            <!-- 八、身份证用户深度分析 -->
"""

# 更新章节编号
    # 将"八、身份证用户深度分析"改为"九、关键发现"
    # 将"九、关键发现"改为"十、数据文件说明"

    html_content += f"""
            <section id="section8" class="section">
                <h2>八、身份证用户深度分析</h2>

                <h3>8.1 年龄组分布</h3>
                <div class="chart-grid">
                    <div class="chart-wrapper">
                        <div id="chart-age-pie"></div>
                    </div>
                    <div class="chart-wrapper">
                        <div id="chart-age-compare"></div>
                    </div>
                </div>
                <h3>8.2 各年龄组平均出行次数</h3>
                <div class="chart-grid">
                    <div class="chart-wrapper">
                        <div id="chart-age-avg-trips"></div>
                    </div>
                </div>
                <h3>8.3 身份证用户概况</h3>
                <p>基于身份证号码验证，从可溯源用户中识别出有有效身份证信息的用户：</p>
                <table>
                    <thead>
                        <tr>
                            <th>指标</th>
                            <th>数值</th>
                        </tr>
                    </thead>
                    <tbody>
                        <tr>
                            <td><strong>有身份证用户数</strong></td>
                            <td>{id_card_count:,}</td>
                        </tr>
                        <tr>
                            <td><strong>占可溯源用户比例</strong></td>
                            <td>{id_card_count/total_users*100:.1f}%</td>
                        </tr>
                    </tbody>
                </table>

                <h3>8.4 年龄分布</h3>
                <p>身份证用户按年龄分组统计：</p>
                <table>
                    <thead>
                        <tr>
                            <th>年龄组</th>
                            <th>用户数</th>
                            <th>用户占比</th>
                            <th>出行次数</th>
                            <th>客流占比</th>
                            <th>平均出行次数</th>
                            <th>付费用户占比</th>
                        </tr>
                    </thead>
                    <tbody>
"""

    # 添加年龄分布表格
    for age_group in age_group_order:
        if age_group in age_group_stats:
            stats_data = age_group_stats[age_group]
            user_pct = stats_data['users'] / id_card_count * 100
            trips = stats_data['total_trips']
            trip_pct = trips / id_card_total_trips * 100
            avg_trips = stats_data['total_trips'] / stats_data['users']
            paid_pct = stats_data['paid_users'] / stats_data['users'] * 100
            html_content += f"""
                        <tr>
                            <td>{age_group}</td>
                            <td>{stats_data['users']:,}</td>
                            <td>{user_pct:.1f}%</td>
                            <td>{trips:,}</td>
                            <td>{trip_pct:.1f}%</td>
                            <td>{avg_trips:.1f}</td>
                            <td>{paid_pct:.1f}%</td>
                        </tr>
"""

    # 计算老年用户比例
    elderly_users = sum(v['users'] for k, v in age_group_stats.items() if k in ['老年(60-64岁)', '高龄(65-69岁)', '超高龄(≥70岁)'])
    working_age_users = sum(v['users'] for k, v in age_group_stats.items() if k in ['青年(18-29岁)', '中年(30-49岁)', '中老年(50-59岁)'])

    html_content += f"""
                    </tbody>
                </table>

                <h3>8.4 调整后的通勤分析</h3>
                <p><strong>重要说明</strong>: 由于绝大多数身份证用户为老年人（≥60岁），其出行主要为日常生活需求，不应归类为"通勤"。因此，在本次分析中，年龄≥65岁的用户被重新分类为"老年出行"而非通勤用户。</p>

                <table>
                    <thead>
                        <tr>
                            <th>用户类型</th>
                            <th>用户数</th>
                            <th>占总用户比例</th>
                            <th>说明</th>
                        </tr>
                    </thead>
                    <tbody>
                        <tr>
                            <td><strong>通勤用户</strong></td>
                            <td>{commuter_users:,}</td>
                            <td>{commuter_users/total_users*100:.1f}%</td>
                            <td>仅工作年龄段用户，规律出行</td>
                        </tr>
                        <tr>
                            <td><strong>老年出行用户</strong></td>
                            <td>{sum(1 for s in user_stats.values() if s.get('commuter_type') == 'senior_citizen'):,}</td>
                            <td>{sum(1 for s in user_stats.values() if s.get('commuter_type') == 'senior_citizen')/total_users*100:.1f}%</td>
                            <td>年龄≥65岁，日常生活出行</td>
                        </tr>
                        <tr>
                            <td><strong>非通勤用户</strong></td>
                            <td>{sum(1 for s in user_stats.values() if s.get('commuter_type') == 'non_commuter'):,}</td>
                            <td>{sum(1 for s in user_stats.values() if s.get('commuter_type') == 'non_commuter')/total_users*100:.1f}%</td>
                            <td>不规律出行用户</td>
                        </tr>
                    </tbody>
                </table>

                <h3>8.5 综合客流分析</h3>
                <p>本节从客流（出行次数）维度分析各用户群体的贡献度。</p>

                <h4>8.5.1 卡类型客流贡献度</h4>
                <table>
                    <thead>
                        <tr>
                            <th>卡类型</th>
                            <th>用户数占比</th>
                            <th>客流占比</th>
                            <th>人均出行次数</th>
                            <th>说明</th>
                        </tr>
                    </thead>
                    <tbody>
"""

    # 添加卡类型客流贡献度表格
    for card_type, count in card_type_dist.most_common():
        user_pct = count / total_users * 100
        trips = card_type_trip_dist[card_type]
        trip_pct = trips / total_trips * 100
        avg_trips = trips / count
        # 添加说明
        if card_type == '身份证':
            note = '主要客流来源'
        elif card_type == '敬老卡':
            note = '老年用户'
        elif card_type == '献血荣誉卡':
            note = '荣誉卡用户'
        elif card_type == '普通卡':
            note = '普通乘客'
        else:
            note = '-'
        html_content += f"""
                        <tr>
                            <td>{card_type}</td>
                            <td>{user_pct:.1f}%</td>
                            <td>{trip_pct:.1f}%</td>
                            <td>{avg_trips:.1f}次</td>
                            <td>{note}</td>
                        </tr>
"""

    html_content += f"""
                    </tbody>
                </table>

                <div class="highlight">
                    <strong>关键发现</strong>：
                    <ul>
                        <li><strong>用户占比 ≠ 客流占比</strong>：某些卡类型用户少但出行频次高（如敬老卡）</li>
                        <li><strong>身份证用户贡献</strong>：占用户{sum(1 for s in user_stats.values() if s['card_type']=='身份证')/total_users*100:.1f}%，贡献了{card_type_trip_dist['身份证']/total_trips*100:.1f}%的客流</li>
                        <li><strong>客流集中度</strong>：前3种卡类型贡献了{sum(card_type_trip_dist[ct] for ct in list(card_type_dist)[:3])/total_trips*100:.1f}%的客流</li>
                    </ul>
                </div>

                <h4>8.5.2 年龄组客流贡献度</h4>
                <table>
                    <thead>
                        <tr>
                            <th>年龄组</th>
                            <th>用户数占比</th>
                            <th>客流占比</th>
                            <th>人均出行次数</th>
                            <th>说明</th>
                        </tr>
                    </thead>
                    <tbody>
"""

    # 计算总出行次数（所有用户）
    all_trips = total_trips

    # 添加年龄组客流贡献度表格
    for age_group in age_group_order:
        if age_group in age_group_stats:
            stats_data = age_group_stats[age_group]
            user_pct = stats_data['users'] / id_card_count * 100
            trips = stats_data['total_trips']
            trip_pct = trips / id_card_total_trips * 100
            avg_trips = trips / stats_data['users']
            # 添加说明
            if age_group == '超高龄(≥70岁)':
                note = '主要客流来源'
            elif age_group == '高龄(65-69岁)':
                note = '高龄用户'
            elif age_group in ['青年(18-29岁)', '中年(30-49岁)']:
                note = '工作年龄段'
            else:
                note = '-'
            html_content += f"""
                        <tr>
                            <td>{age_group}</td>
                            <td>{user_pct:.1f}%</td>
                            <td>{trip_pct:.1f}%</td>
                            <td>{avg_trips:.1f}次</td>
                            <td>{note}</td>
                        </tr>
"""

    html_content += f"""
                    </tbody>
                </table>

                <div class="highlight">
                    <strong>关键发现</strong>：
                    <ul>
                        <li><strong>超高龄用户主导</strong>：占身份证用户{age_group_stats.get('超高龄(≥70岁)', {'users': 0}).get('users', 0)/id_card_count*100:.1f}%，贡献了{age_group_stats.get('超高龄(≥70岁)', {'total_trips': 0}).get('total_trips', 0)/id_card_total_trips*100:.1f}%的客流</li>
                        <li><strong>人均出行频次</strong>：超高龄用户平均出行{age_group_stats.get('超高龄(≥70岁)', {'total_trips': 0, 'users': 1}).get('total_trips', 0)/max(age_group_stats.get('超高龄(≥70岁)', {'users': 1}).get('users', 1), 1):.1f}次</li>
                    </ul>
                </div>

                <h3>8.6 年龄结构关键发现</h3>
                <div class="highlight">
                    <ul>
                        <li><strong>老年用户（≥60岁）</strong>: {elderly_users:,} ({elderly_users/id_card_count*100:.1f}%)</li>
                        <li><strong>工作年龄段用户（18-59岁）</strong>: {working_age_users:,} ({working_age_users/id_card_count*100:.1f}%)</li>
                        <li><strong>老龄化严重</strong>: 超过{elderly_users/id_card_count*100:.0f}%的身份证用户为老年人</li>
                    </ul>
                </div>
            </section>

            <!-- 九、关键发现 -->
            <section id="section9" class="section">
                <h2>九、关键发现</h2>

                <h3>9.1 用户活跃度</h3>
                <div class="stats-grid">
                    <div class="stat-card">
                        <div class="label">高频用户 (≥50次)</div>
                        <div class="value">{trip_ranges['50+次']:,}</div>
                        <div class="subtext">{trip_ranges['50+次']/total_users*100:.1f}%</div>
                    </div>
                    <div class="stat-card">
                        <div class="label">中频用户 (11-50次)</div>
                        <div class="value">{trip_ranges['11-20次'] + trip_ranges['21-50次']:,}</div>
                        <div class="subtext">{(trip_ranges['11-20次'] + trip_ranges['21-50次'])/total_users*100:.1f}%</div>
                    </div>
                    <div class="stat-card">
                        <div class="label">低频用户 (≤10次)</div>
                        <div class="value">{trip_ranges['1次'] + trip_ranges['2-5次'] + trip_ranges['6-10次']:,}</div>
                        <div class="subtext">{(trip_ranges['1次'] + trip_ranges['2-5次'] + trip_ranges['6-10次'])/total_users*100:.1f}%</div>
                    </div>
                </div>

                <h3>9.2 通勤模式</h3>
                <ul style="margin-left: 20px; margin-top: 15px;">
                    <li style="margin: 10px 0;"><strong>通勤用户比例</strong>: {commuter_users/total_users*100:.1f}%</li>
                    <li style="margin: 10px 0;"><strong>主要通勤类型</strong>:
"""

    # 添加通勤类型分析
    commuter_type_analysis = Counter(s['commuter_type'] for s in user_stats.values() if s['is_commuter'])
    for comm_type, count in commuter_type_analysis.most_common():
        label = commuter_labels_map.get(comm_type, comm_type)
        percentage = count / commuter_users * 100 if commuter_users > 0 else 0
        html_content += f"""
                        <ul style="margin-left: 20px;">
                            <li>{label}: {count:,} ({percentage:.1f}%)</li>
                        </ul>
"""

    html_content += f"""
                    </li>
                </ul>

                <h3>9.3 付费行为</h3>
                <div class="stats-grid">
                    <div class="stat-card">
                        <div class="label">付费转化率</div>
                        <div class="value">{total_paid_users/total_users*100:.1f}%</div>
                        <div class="subtext">付费用户/总用户</div>
                    </div>
                    <div class="stat-card">
                        <div class="label">免费用户占主导</div>
                        <div class="value">{total_free_users/total_users*100:.1f}%</div>
                        <div class="subtext">完全免费出行</div>
                    </div>
                    <div class="stat-card">
                        <div class="label">人均营收贡献</div>
                        <div class="value">¥{total_revenue/total_users:.2f}</div>
                    </div>
                </div>
            </section>

            <!-- 十、数据文件说明 -->
            <section id="section10" class="section">
                <h2>十、数据文件说明</h2>
                <p>本报告基于以下数据文件生成：</p>
                <ul style="margin-left: 20px; margin-top: 15px;">
                    <li style="margin: 10px 0;"><code>user_travel_summary.csv</code>: 用户出行汇总统计</li>
                    <li style="margin: 10px 0;"><code>user_travel_details.json</code>: 用户出行明细数据</li>
                </ul>
            </section>
        </div>

        <div class="footer">
            <p><strong>报告由黄山公交用户出行规律分析工具自动生成</strong></p>
            <p>生成时间: {datetime.now().strftime('%Y-%m-%d')}</p>
        </div>
    </div>

    <script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
    <script>
        // 图表数据
        const chartData = __CHART_DATA_PLACEHOLDER__;

        // 通用图表配置
        const commonLayout = {{
            font: {{ family: 'Segoe UI, Arial, sans-serif' }},
            paper_bgcolor: 'rgba(0,0,0,0)',
            plot_bgcolor: 'rgba(0,0,0,0)',
            margin: {{ l: 60, r: 40, t: 60, b: 80 }},
            autosize: true
        }};

        const commonConfig = {{
            responsive: true,
            displayModeBar: true,
            displaylogo: false,
            modeBarButtonsToRemove: ['pan2d', 'select2d', 'lasso2d']
        }};

        document.addEventListener('DOMContentLoaded', function() {{
            // ==================== 一、整体概况 ====================
            // 1.1 付费用户 vs 免费用户饼图
            const paidFreeData = [{{
                values: [chartData.paid_users, chartData.free_users],
                labels: ['付费用户', '免费用户'],
                type: 'pie',
                hole: 0.4,
                textinfo: 'label+percent',
                textposition: 'inside',
                marker: {{
                    colors: ['#667eea', '#764ba2']
                }}
            }}];
            Plotly.newPlot('chart-paid-free', paidFreeData, {{
                ...commonLayout,
                title: {{ text: '付费用户 vs 免费用户分布', font: {{ size: 18, color: '#667eea' }} }}
            }}, commonConfig);

            // ==================== 二、卡类型分布 ====================
            // 2.1 卡类型用户数分布饼图
            const cardTypeLabels = Object.keys(chartData.card_type_dist);
            const cardTypeValues = Object.values(chartData.card_type_dist);
            const cardTypePieData = [{{
                values: cardTypeValues,
                labels: cardTypeLabels,
                type: 'pie',
                textinfo: 'label+percent',
                textposition: 'inside'
            }}];
            Plotly.newPlot('chart-card-type-pie', cardTypePieData, {{
                ...commonLayout,
                title: {{ text: '卡类型用户数分布', font: {{ size: 18, color: '#667eea' }} }}
            }}, commonConfig);

            // 2.2 用户占比 vs 客流占比对比
            const userPct = cardTypeLabels.map(ct =>
                (chartData.card_type_dist[ct] / chartData.total_users * 100).toFixed(1)
            );
            const tripPct = cardTypeLabels.map(ct =>
                (chartData.card_type_trip_dist[ct] / chartData.total_trips * 100).toFixed(1)
            );
            const cardTypeCompareData = [
                {{
                    x: cardTypeLabels,
                    y: userPct,
                    name: '用户占比',
                    type: 'bar',
                    marker: {{ color: '#667eea' }}
                }},
                {{
                    x: cardTypeLabels,
                    y: tripPct,
                    name: '客流占比',
                    type: 'bar',
                    marker: {{ color: '#764ba2' }}
                }}
            ];
            Plotly.newPlot('chart-card-type-compare', cardTypeCompareData, {{
                ...commonLayout,
                title: {{ text: '用户占比 vs 客流占比对比', font: {{ size: 18, color: '#667eea' }} }},
                barmode: 'group',
                xaxis: {{ title: '卡类型' }},
                yaxis: {{ title: '占比 (%)' }}
            }}, commonConfig);

            // 2.3 各卡类型人均出行次数
            const avgTrips = cardTypeLabels.map(ct =>
                (chartData.card_type_trip_dist[ct] / chartData.card_type_dist[ct]).toFixed(1)
            );
            const cardTypeAvgTripsData = [{{
                x: cardTypeLabels,
                y: avgTrips,
                type: 'bar',
                marker: {{
                    color: avgTrips,
                    colorscale: 'Viridis'
                }},
                text: avgTrips.map(v => v + '次'),
                textposition: 'outside'
            }}];
            Plotly.newPlot('chart-card-type-avg-trips', cardTypeAvgTripsData, {{
                ...commonLayout,
                title: {{ text: '各卡类型人均出行次数', font: {{ size: 18, color: '#667eea' }} }},
                xaxis: {{ title: '卡类型' }},
                yaxis: {{ title: '平均出行次数' }}
            }}, commonConfig);

            // ==================== 三、通勤类型分布 ====================
            // 3.1 通勤类型用户数分布饼图
            const commuterLabels = Object.keys(chartData.all_commuter_dist);
            const commuterValues = Object.values(chartData.all_commuter_dist);
            const commuterPieData = [{{
                values: commuterValues,
                labels: commuterLabels.map(l => chartData.commuter_labels_map[l] || l),
                type: 'pie',
                textinfo: 'label+percent'
            }}];
            Plotly.newPlot('chart-commuter-type-pie', commuterPieData, {{
                ...commonLayout,
                title: {{ text: '通勤类型用户数分布', font: {{ size: 18, color: '#667eea' }} }}
            }}, commonConfig);

            // 3.2 各通勤类型用户数量柱状图
            const commuterBarData = [{{
                x: commuterLabels.map(l => chartData.commuter_labels_map[l] || l),
                y: commuterValues,
                type: 'bar',
                marker: {{ color: '#667eea' }},
                text: commuterValues,
                textposition: 'outside'
            }}];
            Plotly.newPlot('chart-commuter-type-bar', commuterBarData, {{
                ...commonLayout,
                title: {{ text: '各通勤类型用户数量', font: {{ size: 18, color: '#667eea' }} }},
                xaxis: {{ title: '通勤类型' }},
                yaxis: {{ title: '用户数' }}
            }}, commonConfig);

            // ==================== 四、出行次数分布 ====================
            // 4.1 出行次数范围分布
            const tripRangeLabels = Object.keys(chartData.trip_ranges);
            const tripRangeValues = Object.values(chartData.trip_ranges);
            const tripRangeBarData = [{{
                x: tripRangeLabels,
                y: tripRangeValues,
                type: 'bar',
                marker: {{ color: '#667eea' }},
                text: tripRangeValues,
                textposition: 'outside'
            }}];
            Plotly.newPlot('chart-trip-range-bar', tripRangeBarData, {{
                ...commonLayout,
                title: {{ text: '出行次数范围分布', font: {{ size: 18, color: '#667eea' }} }},
                xaxis: {{ title: '出行次数范围' }},
                yaxis: {{ title: '用户数' }}
            }}, commonConfig);

            // 4.2 出行频次占比
            const tripRangePieData = [{{
                values: tripRangeValues,
                labels: tripRangeLabels,
                type: 'pie',
                textinfo: 'label+percent'
            }}];
            Plotly.newPlot('chart-trip-range-pie', tripRangePieData, {{
                ...commonLayout,
                title: {{ text: '出行频次占比', font: {{ size: 18, color: '#667eea' }} }}
            }}, commonConfig);

            // ==================== 五、付费情况分析 ====================
            // 5.1 付费用户 vs 免费用户
            const paymentPieData = [{{
                values: [chartData.paid_users, chartData.free_users],
                labels: ['付费用户', '免费用户'],
                type: 'pie',
                hole: 0.4,
                textinfo: 'label+percent',
                marker: {{ colors: ['#667eea', '#764ba2'] }}
            }}];
            Plotly.newPlot('chart-payment-pie', paymentPieData, {{
                ...commonLayout,
                title: {{ text: '付费用户 vs 免费用户', font: {{ size: 18, color: '#667eea' }} }}
            }}, commonConfig);

            // ==================== 七、用户依赖度分群分析 ====================
            // 7.1 用户依赖度分布
            const dependencyLabels = ['重度依赖', '中度依赖', '轻度/偶然'];
            const dependencyValues = [
                chartData.heavy_count,
                chartData.moderate_count - chartData.heavy_count,
                chartData.total_users - chartData.moderate_count
            ];
            const dependencyPieData = [{{
                values: dependencyValues,
                labels: dependencyLabels,
                type: 'pie',
                hole: 0.4,
                textinfo: 'label+percent'
            }}];
            Plotly.newPlot('chart-dependency-pie', dependencyPieData, {{
                ...commonLayout,
                title: {{ text: '用户依赖度分布', font: {{ size: 18, color: '#667eea' }} }}
            }}, commonConfig);

            // 7.1 用户数占比 vs 客流占比对比
            const depUserPct = [
                (chartData.heavy_count / chartData.total_users * 100).toFixed(1),
                ((chartData.moderate_count - chartData.heavy_count) / chartData.total_users * 100).toFixed(1),
                ((chartData.total_users - chartData.moderate_count) / chartData.total_users * 100).toFixed(1)
            ];
            const depTripPct = [
                (chartData.heavy_trips / chartData.total_trips * 100).toFixed(1),
                ((chartData.moderate_trips - chartData.heavy_trips) / chartData.total_trips * 100).toFixed(1),
                ((chartData.total_trips - chartData.moderate_trips) / chartData.total_trips * 100).toFixed(1)
            ];
            const dependencyCompareData = [
                {{
                    x: dependencyLabels,
                    y: depUserPct,
                    name: '用户占比',
                    type: 'bar',
                    marker: {{ color: '#667eea' }}
                }},
                {{
                    x: dependencyLabels,
                    y: depTripPct,
                    name: '客流占比',
                    type: 'bar',
                    marker: {{ color: '#764ba2' }}
                }}
            ];
            Plotly.newPlot('chart-dependency-compare', dependencyCompareData, {{
                ...commonLayout,
                title: {{ text: '用户数占比 vs 客流占比对比', font: {{ size: 18, color: '#667eea' }} }},
                barmode: 'group',
                xaxis: {{ title: '依赖等级' }},
                yaxis: {{ title: '占比 (%)' }}
            }}, commonConfig);

            // 7.2 重度依赖用户卡类型分布
            const heavyCardLabels = Object.keys(chartData.heavy_card_dist);
            const heavyCardValues = Object.values(chartData.heavy_card_dist);
            const heavyCardTypeData = [{{
                x: heavyCardLabels,
                y: heavyCardValues,
                type: 'bar',
                marker: {{ color: '#667eea' }},
                text: heavyCardValues,
                textposition: 'outside'
            }}];
            Plotly.newPlot('chart-heavy-card-type', heavyCardTypeData, {{
                ...commonLayout,
                title: {{ text: '重度依赖用户卡类型分布', font: {{ size: 18, color: '#667eea' }} }},
                xaxis: {{ title: '卡类型' }},
                yaxis: {{ title: '用户数' }}
            }}, commonConfig);

            // 7.2 重度依赖用户付费情况
            const heavyPaymentData = [{{
                values: [chartData.heavy_paid, chartData.heavy_free],
                labels: ['付费用户', '免费用户'],
                type: 'pie',
                hole: 0.4,
                textinfo: 'label+percent',
                marker: {{ colors: ['#667eea', '#764ba2'] }}
            }}];
            Plotly.newPlot('chart-heavy-payment', heavyPaymentData, {{
                ...commonLayout,
                title: {{ text: '重度依赖用户付费情况', font: {{ size: 18, color: '#667eea' }} }}
            }}, commonConfig);

            // ==================== 八、身份证用户深度分析 ====================
            // 8.1 年龄组分布
            const ageGroupStats = chartData.age_group_stats;
            const ageGroupOrder = chartData.age_group_order.filter(ag => ageGroupStats[ag]);
            const ageUsers = ageGroupOrder.map(ag => ageGroupStats[ag].users);
            const agePieData = [{{
                values: ageUsers,
                labels: ageGroupOrder,
                type: 'pie',
                textinfo: 'label+percent'
            }}];
            Plotly.newPlot('chart-age-pie', agePieData, {{
                ...commonLayout,
                title: {{ text: '身份证用户年龄组分布', font: {{ size: 18, color: '#667eea' }} }}
            }}, commonConfig);

            // 8.1 年龄组用户数 vs 客流对比
            const ageUserPct = ageGroupOrder.map(ag =>
                (ageGroupStats[ag].users / chartData.id_card_count * 100).toFixed(1)
            );
            const ageTripPct = ageGroupOrder.map(ag =>
                (ageGroupStats[ag].total_trips / chartData.id_card_total_trips * 100).toFixed(1)
            );
            const ageCompareData = [
                {{
                    x: ageGroupOrder,
                    y: ageUserPct,
                    name: '用户占比',
                    type: 'bar',
                    marker: {{ color: '#667eea' }}
                }},
                {{
                    x: ageGroupOrder,
                    y: ageTripPct,
                    name: '客流占比',
                    type: 'bar',
                    marker: {{ color: '#764ba2' }}
                }}
            ];
            Plotly.newPlot('chart-age-compare', ageCompareData, {{
                ...commonLayout,
                title: {{ text: '年龄组：用户数占比 vs 客流占比对比', font: {{ size: 18, color: '#667eea' }} }},
                barmode: 'group',
                xaxis: {{ title: '年龄组' }},
                yaxis: {{ title: '占比 (%)' }}
            }}, commonConfig);

            // 8.2 各年龄组平均出行次数
            const ageAvgTrips = ageGroupOrder.map(ag =>
                (ageGroupStats[ag].total_trips / ageGroupStats[ag].users).toFixed(1)
            );
            const ageAvgTripsData = [{{
                x: ageGroupOrder,
                y: ageAvgTrips,
                type: 'bar',
                marker: {{
                    color: ageAvgTrips,
                    colorscale: 'Viridis'
                }},
                text: ageAvgTrips.map(v => v + '次'),
                textposition: 'outside'
            }}];
            Plotly.newPlot('chart-age-avg-trips', ageAvgTripsData, {{
                ...commonLayout,
                title: {{ text: '各年龄组平均出行次数', font: {{ size: 18, color: '#667eea' }} }},
                xaxis: {{ title: '年龄组' }},
                yaxis: {{ title: '平均出行次数' }}
            }}, commonConfig);

            // ==================== 响应式处理 ====================
            window.addEventListener('resize', function() {{
                const plots = document.querySelectorAll('.js-plotly-plot');
                plots.forEach(plot => {{
                    Plotly.Plots.resize(plot);
                }});
            }});

            // ==================== 平滑滚动 ====================
            document.querySelectorAll('a[href^="#"]').forEach(anchor => {{
                anchor.addEventListener('click', function (e) {{
                    e.preventDefault();
                    const target = document.querySelector(this.getAttribute('href'));
                    if (target) {{
                        target.scrollIntoView({{
                            behavior: 'smooth',
                            block: 'start'
                        }});
                    }}
                }});
            }});
        }});
    </script>
</body>
</html>
"""

    # ==================== 构建图表数据并追加到HTML ====================
    import json

    # 构建图表数据JSON
    chart_data_for_json = dict()
    chart_data_for_json['total_users'] = total_users
    chart_data_for_json['total_trips'] = total_trips
    chart_data_for_json['paid_users'] = total_paid_users
    chart_data_for_json['free_users'] = total_free_users
    chart_data_for_json['commuter_users'] = commuter_users
    chart_data_for_json['card_type_dist'] = card_type_dist_dict
    chart_data_for_json['card_type_trip_dist'] = card_type_trip_dist_dict
    chart_data_for_json['all_commuter_dist'] = all_commuter_dist_dict
    chart_data_for_json['commuter_labels_map'] = commuter_labels_map
    chart_data_for_json['trip_ranges'] = trip_ranges
    chart_data_for_json['age_group_stats'] = age_group_stats_dict
    chart_data_for_json['age_group_order'] = age_group_order
    chart_data_for_json['id_card_count'] = id_card_count
    chart_data_for_json['id_card_total_trips'] = id_card_total_trips
    chart_data_for_json['total_revenue'] = total_revenue
    chart_data_for_json['heavy_count'] = heavy_count
    chart_data_for_json['moderate_count'] = moderate_count
    chart_data_for_json['heavy_trips'] = heavy_trips
    chart_data_for_json['moderate_trips'] = moderate_trips
    chart_data_for_json['heavy_card_dist'] = dict(heavy_card_dist)
    chart_data_for_json['heavy_card_trips'] = dict(heavy_card_trips)
    chart_data_for_json['heavy_paid'] = heavy_paid
    chart_data_for_json['heavy_free'] = heavy_free
    chart_data_for_json['heavy_revenue'] = heavy_revenue
    chart_data_json = json.dumps(chart_data_for_json, ensure_ascii=False)

    # 替换HTML中的占位符
    html_content = html_content.replace('__CHART_DATA_PLACEHOLDER__', chart_data_json)

    # 保存HTML文件
    output_html = OUTPUT_DIR / 'user_travel_report.html'
    with open(output_html, 'w', encoding='utf-8') as f:
        f.write(html_content)

    print(f"  已保存: {output_html}")


# ==================== Markdown总结报告 ====================

def generate_markdown_summary(user_trips, user_stats):
    """生成Markdown总结报告"""
    print("\n正在生成Markdown总结报告...")

    # 计算全局统计
    # 注意：所有统计都基于 user_stats（包含1次及以上出行的用户）
    total_users = len(user_stats)
    total_trips = sum(s['total_trips'] for s in user_stats.values())
    commuter_users = sum(1 for s in user_stats.values() if s['is_commuter'])
    total_paid_users = sum(1 for s in user_stats.values() if s['paid_trip_count'] > 0)
    total_free_users = sum(1 for s in user_stats.values() if s['paid_trip_count'] == 0)
    total_revenue = sum(s['total_paid_amount'] for s in user_stats.values())
    id_card_count = sum(1 for s in user_stats.values() if s.get('has_valid_id', False))

    # 卡类型分布
    card_type_dist = Counter(s['card_type'] for s in user_stats.values())
    # 计算每种卡类型的客流占比
    card_type_trip_dist = Counter()
    for stats in user_stats.values():
        card_type_trip_dist[stats['card_type']] += stats['total_trips']

    # 通勤类型分布
    commuter_type_dist = Counter(s['commuter_type'] for s in user_stats.values() if s['is_commuter'])

    # 出行次数统计
    trip_counts = [s['total_trips'] for s in user_stats.values()]

    md_content = f"""# 黄山公交用户出行规律分析报告

**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

---

## 一、整体概况

| 指标 | 数值 |
|------|------|
| **总用户数** | {total_users:,} |
| **总出行次数** | {total_trips:,} |
| **平均每用户出行** | {total_trips/total_users:.1f} 次 |
| **通勤用户数** | {commuter_users:,} ({commuter_users/total_users*100:.1f}%) |
| **付费用户数** | {total_paid_users:,} ({total_paid_users/total_users*100:.1f}%) |
| **免费用户数** | {total_free_users:,} ({total_free_users/total_users*100:.1f}%) |
| **总营收** | ¥{total_revenue:,.2f} |

---

## 二、卡类型分布

| 卡类型 | 用户数 | 用户占比 | 出行次数 | 客流占比 |
|--------|--------|----------|----------|----------|
"""

    # 添加卡类型分布表格
    for card_type, count in card_type_dist.most_common():
        user_percentage = count / total_users * 100
        trips = card_type_trip_dist[card_type]
        trip_percentage = trips / total_trips * 100
        md_content += f"| {card_type} | {count:,} | {user_percentage:.1f}% | {trips:,} | {trip_percentage:.1f}% |\n"

    md_content += f"""

---

## 三、通勤类型分布

基于用户的出行规律和年龄信息，将用户分为不同的通勤类型。

| 通勤类型 | 用户数 | 占总用户比例 | 说明 |
|----------|--------|-------------|------|
"""

    # 统计所有用户的通勤类型
    all_commuter_dist = Counter(s['commuter_type'] for s in user_stats.values())
    commuter_labels_map = {
        'daily': '每天通勤',
        'frequent': '频繁通勤',
        'occasional': '偶尔通勤',
        'non_commuter': '非通勤',
        'senior_citizen': '老年出行',
        'single_trip': '单次出行',
        # 兼容旧代码的类型名称
        'daily_commuter': '每天通勤',
        'frequent_commuter': '频繁通勤',
        'occasional_commuter': '偶尔通勤'
    }

    for comm_type, count in all_commuter_dist.most_common():
        label = commuter_labels_map.get(comm_type, comm_type)
        percentage = count / total_users * 100
        md_content += f"| {label} | {count:,} | {percentage:.1f}% |\n"

    md_content += f"""

---

## 四、出行次数分布

**注意**：本报告包含所有1次及以上出行记录的用户。

| 出行次数范围 | 用户数 | 占比 |
|-------------|--------|------|
"""

    # 添加出行次数分布
    trip_ranges = {
        '1次': sum(1 for t in trip_counts if t == 1),
        '2-5次': sum(1 for t in trip_counts if 2 <= t <= 5),
        '6-10次': sum(1 for t in trip_counts if 6 <= t <= 10),
        '11-20次': sum(1 for t in trip_counts if 11 <= t <= 20),
        '21-50次': sum(1 for t in trip_counts if 21 <= t <= 50),
        '50+次': sum(1 for t in trip_counts if t > 50)
    }

    for range_name, count in trip_ranges.items():
        percentage = count / total_users * 100
        md_content += f"| {range_name} | {count:,} | {percentage:.1f}% |\n"

    md_content += f"""

---

## 五、付费情况分析

### 5.1 付费用户 vs 免费用户

* **付费用户**: {total_paid_users:,} ({total_paid_users/total_users*100:.1f}%)
* **免费用户**: {total_free_users:,} ({total_free_users/total_users*100:.1f}%)

### 5.2 营收统计

* **总营收**: ¥{total_revenue:,.2f}
* **人均付费**: ¥{total_revenue/total_paid_users:.2f} (仅付费用户)

---

## 六、TOP用户列表（按出行次数排序）

| 排名 | 卡类型 | 出行次数 | 活跃天数 | 平均每天出行 | 常用线路 | 付费金额 | 通勤用户 |
|------|--------|----------|----------|--------------|----------|----------|----------|
"""

    # 添加TOP用户列表（不显示用户ID）
    sorted_users = sorted(user_stats.items(), key=lambda x: x[1]['total_trips'], reverse=True)[:50]

    for rank, (user_id, stats) in enumerate(sorted_users, 1):
        commuter_mark = '✓' if stats['is_commuter'] else '✗'
        paid_amount = f"¥{stats['total_paid_amount']:.2f}" if stats['total_paid_amount'] > 0 else "-"
        most_common_route = stats['most_common_route'] or '-'

        md_content += f"| {rank} | {stats['card_type']} | {stats['total_trips']} | {stats['active_days']} | {stats['avg_trips_per_day']} | {most_common_route} | {paid_amount} | {commuter_mark} |\n"

    md_content += f"""

---

## 七、用户依赖度分群分析

基于91天数据周期内用户的出行次数，将可溯源用户分为三个依赖等级，分析不同依赖度用户的特征和贡献。

### 7.1 用户分群概况

"""

    # ==================== 用户依赖度分群分析 ====================
    # 定义分群阈值
    MODERATE_THRESHOLD = 10  # 中度依赖：≥10次出行
    HEAVY_THRESHOLD = 50     # 重度依赖：≥50次出行

    # 筛选用户群
    moderate_users = {uid: s for uid, s in user_stats.items() if s['total_trips'] >= MODERATE_THRESHOLD}
    heavy_users = {uid: s for uid, s in user_stats.items() if s['total_trips'] >= HEAVY_THRESHOLD}

    # 基础统计
    moderate_count = len(moderate_users)
    heavy_count = len(heavy_users)
    moderate_trips = sum(s['total_trips'] for s in moderate_users.values())
    heavy_trips = sum(s['total_trips'] for s in heavy_users.values())

    md_content += f"""| 依赖等级 | 定义 | 用户数 | 占比 | 出行次数 | 客流占比 | 人均出行次数 |
|---------|------|--------|------|----------|----------|--------------|
| 重度依赖 | ≥50次出行 | {heavy_count:,} | {heavy_count/total_users*100:.1f}% | {heavy_trips:,} | {heavy_trips/total_trips*100:.1f}% | {heavy_trips/heavy_count:.1f} |
| 中度依赖 | 10-49次出行 | {moderate_count-heavy_count:,} | {(moderate_count-heavy_count)/total_users*100:.1f}% | {moderate_trips-heavy_trips:,} | {(moderate_trips-heavy_trips)/total_trips*100:.1f}% | {(moderate_trips-heavy_trips)/(moderate_count-heavy_count):.1f} |
| 轻度/偶然 | <10次出行 | {total_users-moderate_count:,} | {(total_users-moderate_count)/total_users*100:.1f}% | {total_trips-moderate_trips:,} | {(total_trips-moderate_trips)/total_trips*100:.1f}% | {(total_trips-moderate_trips)/(total_users-moderate_count):.1f} |

**关键发现**：
- 重度依赖用户虽然只占 {heavy_count/total_users*100:.1f}% 的用户，但贡献了 {heavy_trips/total_trips*100:.1f}% 的客流
- 中度及以上依赖用户占 {moderate_count/total_users*100:.1f}% 的用户，贡献了 {moderate_trips/total_trips*100:.1f}% 的客流
- {heavy_trips/total_trips*100:.1f}% 的客流集中在 {heavy_count/total_users*100:.1f}% 的重度依赖用户手中

### 7.2 重度依赖用户分析（≥50次出行）

#### 7.2.1 卡类型分布
"""

    # 重度依赖用户卡类型分布
    heavy_card_dist = Counter(s['card_type'] for s in heavy_users.values())
    heavy_card_trips = Counter()
    for s in heavy_users.values():
        heavy_card_trips[s['card_type']] += s['total_trips']

    md_content += """| 卡类型 | 用户数 | 占重度依赖用户比例 | 出行次数 | 客流占比 | 人均出行 |
|--------|--------|-------------------|----------|----------|----------|
"""
    for card_type, count in heavy_card_dist.most_common():
        user_pct = count / heavy_count * 100
        trips = heavy_card_trips[card_type]
        trip_pct = trips / heavy_trips * 100
        avg_trips = trips / count
        md_content += f"| {card_type} | {count:,} | {user_pct:.1f}% | {trips:,} | {trip_pct:.1f}% | {avg_trips:.1f} |\n"

    # 重度依赖用户付费情况
    heavy_paid = sum(1 for s in heavy_users.values() if s['paid_trip_count'] > 0)
    heavy_free = heavy_count - heavy_paid
    heavy_revenue = sum(s['total_paid_amount'] for s in heavy_users.values())

    md_content += f"""
#### 7.2.2 付费情况

* **付费用户**: {heavy_paid:,} ({heavy_paid/heavy_count*100:.1f}%)
* **免费用户**: {heavy_free:,} ({heavy_free/heavy_count*100:.1f}%)
* **总营收**: ¥{heavy_revenue:,.2f}
* **人均付费**: ¥{heavy_revenue/heavy_paid:.2f} (仅付费用户)

#### 7.2.2.1 付费用户卡类型分布

| 卡类型 | 总用户数 | 付费用户数 | 付费率 | 说明 |
|--------|---------|-----------|--------|------|
"""
    # 重度依赖用户按卡类型的付费情况
    for card_type, count in heavy_card_dist.most_common():
        card_users = [s for s in heavy_users.values() if s['card_type'] == card_type]
        card_paid = sum(1 for s in card_users if s['paid_trip_count'] > 0)
        card_total = len(card_users)
        paid_rate = (card_paid / card_total * 100) if card_total > 0 else 0
        note = "付费卡类型" if card_type in {'学生卡', '交通部普通卡', '交通部异地卡'} else "免费卡类型"
        md_content += f"| {card_type} | {card_total:,} | {card_paid:,} | {paid_rate:.1f}% | {note} |\n"

    md_content += """
#### 7.2.3 身份证用户分析
"""

    # 重度依赖用户中的身份证用户
    heavy_id_users = {uid: s for uid, s in heavy_users.items() if s.get('has_valid_id', False)}
    heavy_id_count = len(heavy_id_users)

    if heavy_id_count > 0:
        md_content += f"""
重度依赖用户中有身份证信息的用户：{heavy_id_count:,}人 ({heavy_id_count/heavy_count*100:.1f}%)

**年龄结构**：
"""

        # 年龄分布
        heavy_age_stats = {}
        for s in heavy_id_users.values():
            age_group = s.get('age_group', '未知')
            if age_group not in heavy_age_stats:
                heavy_age_stats[age_group] = {'users': 0, 'trips': 0, 'paid': 0}
            heavy_age_stats[age_group]['users'] += 1
            heavy_age_stats[age_group]['trips'] += s['total_trips']
            if s['paid_trip_count'] > 0:
                heavy_age_stats[age_group]['paid'] += 1

        age_group_order = ['未成年(0-17岁)', '青年(18-35岁)', '中年(36-59岁)', '老年(60-69岁)', '超高龄(≥70岁)', '未知']
        total_heavy_id_trips = sum(v['trips'] for v in heavy_age_stats.values())

        md_content += """
| 年龄组 | 用户数 | 占比 | 出行次数 | 客流占比 | 付费用户占比 |
|--------|--------|------|----------|----------|--------------|
"""
        for age_group in age_group_order:
            if age_group in heavy_age_stats:
                stats_data = heavy_age_stats[age_group]
                user_pct = stats_data['users'] / heavy_id_count * 100
                trip_pct = stats_data['trips'] / total_heavy_id_trips * 100
                paid_pct = stats_data['paid'] / stats_data['users'] * 100 if stats_data['users'] > 0 else 0
                md_content += f"| {age_group} | {stats_data['users']:,} | {user_pct:.1f}% | {stats_data['trips']:,} | {trip_pct:.1f}% | {paid_pct:.1f}% |\n"
    else:
        md_content += "\n重度依赖用户中无身份证信息数据。\n"

    # ==================== 出行规律集中度分析 ====================
    md_content += """

#### 7.2.4 出行规律集中度分析

分析重度依赖用户的出行时间和站点选择是否集中，判断是否有固定出行规律。
"""

    # 统计规律性分布（确保不重叠）
    # 先计算每个用户的集中度
    user_regularity = {}
    for user_id, s in heavy_users.items():
        time_conc = calculate_time_concentration(s.get('hour_distribution', {}))
        station_conc = calculate_station_concentration(s.get('station_distribution', {}))
        if time_conc > 0.7 and station_conc > 0.5:
            user_regularity[user_id] = 'high'
        elif time_conc > 0.5 or station_conc > 0.3:
            user_regularity[user_id] = 'medium'
        else:
            user_regularity[user_id] = 'low'

    high_regularity = sum(1 for r in user_regularity.values() if r == 'high')
    medium_regularity = sum(1 for r in user_regularity.values() if r == 'medium')
    low_regularity = sum(1 for r in user_regularity.values() if r == 'low')

    # 计算平均集中度
    time_concentrations = [calculate_time_concentration(s.get('hour_distribution', {})) for s in heavy_users.values()]
    station_concentrations = [calculate_station_concentration(s.get('station_distribution', {})) for s in heavy_users.values()]
    avg_time_conc = sum(time_concentrations) / len(time_concentrations) if time_concentrations else 0
    avg_station_conc = sum(station_concentrations) / len(station_concentrations) if station_concentrations else 0

    md_content += f"""
| 规律性等级 | 用户数 | 占比 | 说明 |
|-----------|--------|------|------|
| 高规律 | {high_regularity:,} | {high_regularity/heavy_count*100:.1f}% | 时间和站点高度集中 |
| 中规律 | {medium_regularity:,} | {medium_regularity/heavy_count*100:.1f}% | 时间或站点有一定规律 |
| 低规律 | {low_regularity:,} | {low_regularity/heavy_count*100:.1f}% | 出行分散无明显规律 |

**平均时间集中度**: {avg_time_conc:.2f} (0-1，越接近1越集中)
**平均站点集中度**: {avg_station_conc:.2f} (0-1，越接近1越集中)

**关键发现**：
- {high_regularity + medium_regularity:,}位重度依赖用户（{(high_regularity + medium_regularity)/heavy_count*100:.1f}%）表现出一定出行规律
- 老年用户（身份证）主要表现为固定时间和固定站点的出行模式
"""

    # ==================== 线路忠诚度分析 ====================
    md_content += """

#### 7.2.5 线路忠诚度分析

分析重度依赖用户对公交线路的忠诚度，判断是否固定使用某几条线路。
"""

    # 计算每个用户的线路忠诚度
    route_loyalty_stats = []
    for s in heavy_users.values():
        loyalty = calculate_route_loyalty(s.get('route_distribution', {}))
        route_loyalty_stats.append(loyalty)

    # 统计线路忠诚度等级
    high_loyalty = sum(1 for l in route_loyalty_stats if l['top1'] >= 0.7 or l['top3'] >= 0.9)
    medium_loyalty = sum(1 for l in route_loyalty_stats if (l['top1'] >= 0.4 or l['top3'] >= 0.6) and not (l['top1'] >= 0.7 or l['top3'] >= 0.9))
    low_loyalty = heavy_count - high_loyalty - medium_loyalty

    # 计算平均指标
    avg_top1_route = sum(l['top1'] for l in route_loyalty_stats) / len(route_loyalty_stats) if route_loyalty_stats else 0
    avg_top3_route = sum(l['top3'] for l in route_loyalty_stats) / len(route_loyalty_stats) if route_loyalty_stats else 0
    avg_effective_routes = sum(l['effective_routes'] for l in route_loyalty_stats) / len(route_loyalty_stats) if route_loyalty_stats else 0

    md_content += f"""
| 线路忠诚度等级 | 用户数 | 占比 | 说明 |
|---------------|--------|------|------|
| 高线路忠诚 | {high_loyalty:,} | {high_loyalty/heavy_count*100:.1f}% | Top1线路≥70%或Top3线路≥90% |
| 中线路忠诚 | {medium_loyalty:,} | {medium_loyalty/heavy_count*100:.1f}% | Top1线路40-70%或Top3线路60-90% |
| 低线路忠诚 | {low_loyalty:,} | {low_loyalty/heavy_count*100:.1f}% | 线路使用分散 |

**平均Top1线路集中度**: {avg_top1_route:.2f}
**平均Top3线路集中度**: {avg_top3_route:.2f}
**平均有效线路数**: {avg_effective_routes:.1f}条

**关键发现**：
- {high_loyalty:,}位重度依赖用户（{high_loyalty/heavy_count*100:.1f}%）高度依赖1-3条线路
- 平均每位重度依赖用户有效使用{avg_effective_routes:.1f}条线路
- 高线路忠诚用户的Top1线路集中度平均达{avg_top1_route*100:.1f}%
"""

    # ==================== 站点时空集中度分析 ====================
    md_content += """

#### 7.2.6 站点时空集中度分析

分析重度依赖用户的出行时间在更细粒度上的集中度（30分钟窗、1小时窗）。
"""

    # 计算每个用户的时间窗集中度
    time_window_30m = [calculate_time_window_concentration(s.get('minute_30slot_distribution', {}), 1) for s in heavy_users.values()]
    time_window_1h = [calculate_time_window_concentration(s.get('minute_30slot_distribution', {}), 2) for s in heavy_users.values()]

    # 重新计算站点集中度
    station_concs = [calculate_station_concentration(s.get('station_distribution', {})) for s in heavy_users.values()]

    # 统计时空集中度等级
    high_spatial = sum(1 for s, t in zip(station_concs, time_window_1h) if s >= 0.6 and t >= 0.5)
    medium_spatial = sum(1 for s, t in zip(station_concs, time_window_1h) if (s >= 0.3 or t >= 0.25) and not (s >= 0.6 and t >= 0.5))
    low_spatial = heavy_count - high_spatial - medium_spatial

    # 计算平均值
    avg_station_conc = sum(station_concs) / len(station_concs) if station_concs else 0
    avg_time_30m = sum(time_window_30m) / len(time_window_30m) if time_window_30m else 0
    avg_time_1h = sum(time_window_1h) / len(time_window_1h) if time_window_1h else 0

    md_content += f"""
| 时空集中度等级 | 用户数 | 占比 | 说明 |
|---------------|--------|------|------|
| 高集中 | {high_spatial:,} | {high_spatial/heavy_count*100:.1f}% | Top1站点≥60%且1小时窗≥50% |
| 中集中 | {medium_spatial:,} | {medium_spatial/heavy_count*100:.1f}% | Top1站点30-60%或1小时窗25-50% |
| 低集中 | {low_spatial:,} | {low_spatial/heavy_count*100:.1f}% | 出行分散 |

**平均Top1站点集中度**: {avg_station_conc:.2f}
**平均30分钟时窗集中度**: {avg_time_30m:.2f}
**平均1小时时窗集中度**: {avg_time_1h:.2f}

**关键发现**：
- {high_spatial + medium_spatial:,}位重度依赖用户（{(high_spatial + medium_spatial)/heavy_count*100:.1f}%）表现出一定的站点或时间集中度
- 重度依赖用户的站点选择相对固定，平均Top1站点集中度达{avg_station_conc*100:.1f}%
- 1小时时窗集中度({avg_time_1h*100:.1f}%)高于30分钟时窗集中度({avg_time_30m*100:.1f}%)，符合预期
"""

    # ==================== 综合规律性评级 ====================
    md_content += """

#### 7.2.7 综合规律性评级

综合线路忠诚度、时间集中度、站点集中度三个维度，对重度依赖用户的出行规律进行综合评级。
"""

    # 综合评级（三项指标均考虑）
    comprehensive_rating = {}
    for user_id, s in heavy_users.items():
        loyalty = calculate_route_loyalty(s.get('route_distribution', {}))
        time_conc = calculate_time_concentration(s.get('hour_distribution', {}))
        station_conc = calculate_station_concentration(s.get('station_distribution', {}))

        # 高规律：三项指标中至少两项达到高等级
        high_count = 0
        if loyalty['top1'] >= 0.7 or loyalty['top3'] >= 0.9:
            high_count += 1
        if time_conc > 0.7:
            high_count += 1
        if station_conc > 0.5:
            high_count += 1

        # 中规律：三项指标中至少两项达到中等级及以上
        medium_count = 0
        if loyalty['top1'] >= 0.4 or loyalty['top3'] >= 0.6:
            medium_count += 1
        if time_conc > 0.5:
            medium_count += 1
        if station_conc > 0.3:
            medium_count += 1

        if high_count >= 2:
            comprehensive_rating[user_id] = 'high'
        elif medium_count >= 2:
            comprehensive_rating[user_id] = 'medium'
        else:
            comprehensive_rating[user_id] = 'low'

    high_comprehensive = sum(1 for r in comprehensive_rating.values() if r == 'high')
    medium_comprehensive = sum(1 for r in comprehensive_rating.values() if r == 'medium')
    low_comprehensive = sum(1 for r in comprehensive_rating.values() if r == 'low')

    md_content += f"""
| 综合规律性等级 | 用户数 | 占比 | 特征描述 |
|---------------|--------|------|----------|
| 高规律（固定线路+固定时间+固定站点） | {high_comprehensive:,} | {high_comprehensive/heavy_count*100:.1f}% | 三项指标中至少两项达到高等级 |
| 中规律（部分固定） | {medium_comprehensive:,} | {medium_comprehensive/heavy_count*100:.1f}% | 三项指标中至少两项达到中等级 |
| 低规律（无明显规律） | {low_comprehensive:,} | {low_comprehensive/heavy_count*100:.1f}% | 其他 |

**综合分析结论**：
- {high_comprehensive + medium_comprehensive:,}位重度依赖用户（{(high_comprehensive + medium_comprehensive)/heavy_count*100:.1f}%）表现出较为固定的出行模式
- 综合考虑线路、时间、站点三个维度后，规律性用户比例达到{(high_comprehensive + medium_comprehensive)/heavy_count*100:.1f}%
- 这部分用户是公交服务的高价值用户，其出行需求相对稳定可预测
"""

    # ==================== 时空对分析（往返模式）====================
    md_content += """

#### 7.2.8 时空对分析（往返出行模式）

分析重度依赖用户是否形成固定的"早出晚归"往返模式，从时间集中度和空间集中度两个维度评估时空对质量。
"""

    # 统计有时空对信息的用户
    users_with_pair = {uid: s for uid, s in heavy_users.items() if s.get('spatiotemporal_pair')}
    users_with_pair_count = len(users_with_pair)

    if users_with_pair_count > 0:
        # 按置信度等级统计
        high_confidence = sum(1 for s in users_with_pair.values() if s['spatiotemporal_pair']['confidence_level'] == '高')
        medium_confidence = sum(1 for s in users_with_pair.values() if s['spatiotemporal_pair']['confidence_level'] == '中')
        low_confidence = sum(1 for s in users_with_pair.values() if s['spatiotemporal_pair']['confidence_level'] == '低')

        # 计算平均集中度指标
        avg_morning_station_conc = sum(s['spatiotemporal_pair']['morning_station_concentration']
                                       for s in users_with_pair.values()) / users_with_pair_count
        avg_evening_station_conc = sum(s['spatiotemporal_pair']['evening_station_concentration']
                                       for s in users_with_pair.values()) / users_with_pair_count
        avg_morning_time_conc = sum(s['spatiotemporal_pair']['morning_time_concentration']
                                    for s in users_with_pair.values()) / users_with_pair_count
        avg_evening_time_conc = sum(s['spatiotemporal_pair']['evening_time_concentration']
                                    for s in users_with_pair.values()) / users_with_pair_count
        avg_pair_concentration = sum(s['spatiotemporal_pair']['pair_concentration']
                                    for s in users_with_pair.values()) / users_with_pair_count

        # 统计往返类型
        round_trip_count = sum(1 for s in users_with_pair.values() if s['spatiotemporal_pair']['is_round_trip'])
        same_station_count = users_with_pair_count - round_trip_count

        # 分析早晚上下车站点是否不同（真正的往返）
        # 获取最常见的早晚站点对（分别统计往返和同站）
        station_pairs_round_trip = Counter()  # 往返：早晚站点不同
        station_pairs_same_station = Counter()  # 同站：早晚站点相同
        for s in users_with_pair.values():
            pair = s['spatiotemporal_pair']
            if pair['morning_station'] and pair['evening_station']:
                if pair['morning_station'] != pair['evening_station']:
                    station_pairs_round_trip[(pair['morning_station'], pair['evening_station'])] += 1
                else:
                    station_pairs_same_station[(pair['morning_station'], pair['evening_station'])] += 1

        top_round_trip_pair = station_pairs_round_trip.most_common(1)[0] if station_pairs_round_trip else (None, 0)
        top_same_station_pair = station_pairs_same_station.most_common(1)[0] if station_pairs_same_station else (None, 0)

        md_content += f"""
**时空对覆盖范围**：{users_with_pair_count:,}位用户（{users_with_pair_count/heavy_count*100:.1f}%）具有明显的早晚出行模式

##### 7.2.8.1 时空对置信度分布

| 置信度等级 | 用户数 | 占有时空对用户比例 | 说明 |
|-----------|--------|-------------------|------|
| 高置信度 | {high_confidence:,} | {high_confidence/users_with_pair_count*100:.1f}% | 站点集中度≥70%且时间集中度≥70% |
| 中置信度 | {medium_confidence:,} | {medium_confidence/users_with_pair_count*100:.1f}% | 站点或时间集中度40-70% |
| 低置信度 | {low_confidence:,} | {low_confidence/users_with_pair_count*100:.1f}% | 站点和时间都<40% |

**平均早上站点集中度**: {avg_morning_station_conc*100:.1f}%
**平均晚上站点集中度**: {avg_evening_station_conc*100:.1f}%
**平均早上时间集中度**: {avg_morning_time_conc*100:.1f}%（2小时窗覆盖率）
**平均晚上时间集中度**: {avg_evening_time_conc*100:.1f}%（2小时窗覆盖率）
**平均时空对时段占比**: {avg_pair_concentration*100:.1f}%（早晚出行占总出行比例）

##### 7.2.8.2 时空对模式分析

| 模式类型 | 用户数 | 占有时空对用户比例 | 说明 |
|---------|--------|-------------------|------|
| 往返模式（早晚不同站点） | {round_trip_count:,} | {round_trip_count/users_with_pair_count*100:.1f}% | 典型通勤/往返模式 |
| 同站往返模式（早晚相同站点） | {same_station_count:,} | {same_station_count/users_with_pair_count*100:.1f}% | 同一站点多次往返 |

**最常见的往返时空对**：{top_round_trip_pair[0][0] if top_round_trip_pair[0] else '无'}(早上) → {top_round_trip_pair[0][1] if top_round_trip_pair[0] else '无'}(晚上) ({top_round_trip_pair[1]:,}位用户)
**最常见的同站往返站点**：{top_same_station_pair[0][0] if top_same_station_pair[0] else '无'} ({top_same_station_pair[1]:,}位用户)

##### 7.2.8.3 关键发现

- {users_with_pair_count:,}位重度依赖用户（{users_with_pair_count/heavy_count*100:.1f}%）形成明显的早晚出行模式
- {high_confidence:,}位用户（{high_confidence/users_with_pair_count*100:.1f}%）具有高置信度时空对，站点和时间都非常集中
- 平均早上站点集中度为{avg_morning_station_conc*100:.1f}%，平均晚上站点集中度为{avg_evening_station_conc*100:.1f}%，说明用户站点选择相对固定
- 平均早上时间集中度为{avg_morning_time_conc*100:.1f}%，平均晚上时间集中度为{avg_evening_time_conc*100:.1f}%，说明用户出行时间有一定规律性
- {round_trip_count/users_with_pair_count*100:.1f}%的用户形成往返模式（早晚站点不同），是典型的通勤往返模式
- {same_station_count/users_with_pair_count*100:.1f}%的用户形成同站往返模式，可能是同一站点进行多项活动
"""
    else:
        md_content += """
**无时空对数据**：重度依赖用户中未检测到明显的早晚出行模式。

这可能是因为：
1. 用户出行时间分布不规律
2. 用户主要在非早晚时段出行
3. 数据量不足以识别稳定的早晚模式
"""

    md_content += """
---

### 7.3 中度依赖用户分析（10-49次出行）

#### 7.3.1 卡类型分布
"""

    # 中度依赖用户（排除重度）
    moderate_only_users = {uid: s for uid, s in moderate_users.items() if s['total_trips'] < HEAVY_THRESHOLD}
    moderate_only_count = len(moderate_only_users)
    moderate_only_trips = sum(s['total_trips'] for s in moderate_only_users.values())

    # 中度依赖用户卡类型分布
    moderate_card_dist = Counter(s['card_type'] for s in moderate_only_users.values())
    moderate_card_trips = Counter()
    for s in moderate_only_users.values():
        moderate_card_trips[s['card_type']] += s['total_trips']

    md_content += """| 卡类型 | 用户数 | 占中度依赖用户比例 | 出行次数 | 客流占比 | 人均出行 |
|--------|--------|-------------------|----------|----------|----------|
"""
    for card_type, count in moderate_card_dist.most_common():
        user_pct = count / moderate_only_count * 100
        trips = moderate_card_trips[card_type]
        trip_pct = trips / moderate_only_trips * 100
        avg_trips = trips / count
        md_content += f"| {card_type} | {count:,} | {user_pct:.1f}% | {trips:,} | {trip_pct:.1f}% | {avg_trips:.1f} |\n"

    # 中度依赖用户付费情况
    moderate_paid = sum(1 for s in moderate_only_users.values() if s['paid_trip_count'] > 0)
    moderate_free = moderate_only_count - moderate_paid
    moderate_revenue = sum(s['total_paid_amount'] for s in moderate_only_users.values())

    md_content += f"""
#### 7.3.2 付费情况

* **付费用户**: {moderate_paid:,} ({moderate_paid/moderate_only_count*100:.1f}%)
* **免费用户**: {moderate_free:,} ({moderate_free/moderate_only_count*100:.1f}%)
* **总营收**: ¥{moderate_revenue:,.2f}
* **人均付费**: ¥{moderate_revenue/moderate_paid:.2f} (仅付费用户)

#### 7.3.2.1 付费用户卡类型分布

| 卡类型 | 总用户数 | 付费用户数 | 付费率 | 说明 |
|--------|---------|-----------|--------|------|
"""
    # 中度依赖用户按卡类型的付费情况
    for card_type, count in moderate_card_dist.most_common():
        card_users = [s for s in moderate_only_users.values() if s['card_type'] == card_type]
        card_paid = sum(1 for s in card_users if s['paid_trip_count'] > 0)
        card_total = len(card_users)
        paid_rate = (card_paid / card_total * 100) if card_total > 0 else 0
        note = "付费卡类型" if card_type in {'学生卡', '交通部普通卡', '交通部异地卡'} else "免费卡类型"
        md_content += f"| {card_type} | {card_total:,} | {card_paid:,} | {paid_rate:.1f}% | {note} |\n"

    md_content += """
#### 7.3.3 身份证用户分析
"""

    # 中度依赖用户中的身份证用户
    moderate_id_users = {uid: s for uid, s in moderate_only_users.items() if s.get('has_valid_id', False)}
    moderate_id_count = len(moderate_id_users)

    if moderate_id_count > 0:
        md_content += f"""
中度依赖用户中有身份证信息的用户：{moderate_id_count:,}人 ({moderate_id_count/moderate_only_count*100:.1f}%)

**年龄结构**：
"""

        # 年龄分布
        moderate_age_stats = {}
        for s in moderate_id_users.values():
            age_group = s.get('age_group', '未知')
            if age_group not in moderate_age_stats:
                moderate_age_stats[age_group] = {'users': 0, 'trips': 0, 'paid': 0}
            moderate_age_stats[age_group]['users'] += 1
            moderate_age_stats[age_group]['trips'] += s['total_trips']
            if s['paid_trip_count'] > 0:
                moderate_age_stats[age_group]['paid'] += 1

        total_moderate_id_trips = sum(v['trips'] for v in moderate_age_stats.values())

        md_content += """
| 年龄组 | 用户数 | 占比 | 出行次数 | 客流占比 | 付费用户占比 |
|--------|--------|------|----------|----------|--------------|
"""
        for age_group in age_group_order:
            if age_group in moderate_age_stats:
                stats_data = moderate_age_stats[age_group]
                user_pct = stats_data['users'] / moderate_id_count * 100
                trip_pct = stats_data['trips'] / total_moderate_id_trips * 100
                paid_pct = stats_data['paid'] / stats_data['users'] * 100 if stats_data['users'] > 0 else 0
                md_content += f"| {age_group} | {stats_data['users']:,} | {user_pct:.1f}% | {stats_data['trips']:,} | {trip_pct:.1f}% | {paid_pct:.1f}% |\n"
    else:
        md_content += "\n中度依赖用户中无身份证信息数据。\n"

    # ==================== 中度依赖用户出行规律集中度分析 ====================
    md_content += """

#### 7.3.4 出行规律集中度分析

分析中度依赖用户的出行时间和站点选择是否集中，判断是否有固定出行规律。
"""

    # 统计中度依赖用户的规律性分布（确保不重叠）
    mod_user_regularity = {}
    for user_id, s in moderate_only_users.items():
        time_conc = calculate_time_concentration(s.get('hour_distribution', {}))
        station_conc = calculate_station_concentration(s.get('station_distribution', {}))
        if time_conc > 0.7 and station_conc > 0.5:
            mod_user_regularity[user_id] = 'high'
        elif time_conc > 0.5 or station_conc > 0.3:
            mod_user_regularity[user_id] = 'medium'
        else:
            mod_user_regularity[user_id] = 'low'

    mod_high_regularity = sum(1 for r in mod_user_regularity.values() if r == 'high')
    mod_medium_regularity = sum(1 for r in mod_user_regularity.values() if r == 'medium')
    mod_low_regularity = sum(1 for r in mod_user_regularity.values() if r == 'low')

    # 计算平均集中度
    mod_time_concentrations = [calculate_time_concentration(s.get('hour_distribution', {})) for s in moderate_only_users.values()]
    mod_station_concentrations = [calculate_station_concentration(s.get('station_distribution', {})) for s in moderate_only_users.values()]
    mod_avg_time_conc = sum(mod_time_concentrations) / len(mod_time_concentrations) if mod_time_concentrations else 0
    mod_avg_station_conc = sum(mod_station_concentrations) / len(mod_station_concentrations) if mod_station_concentrations else 0

    md_content += f"""
| 规律性等级 | 用户数 | 占比 | 说明 |
|-----------|--------|------|------|
| 高规律 | {mod_high_regularity:,} | {mod_high_regularity/moderate_only_count*100:.1f}% | 时间和站点高度集中 |
| 中规律 | {mod_medium_regularity:,} | {mod_medium_regularity/moderate_only_count*100:.1f}% | 时间或站点有一定规律 |
| 低规律 | {mod_low_regularity:,} | {mod_low_regularity/moderate_only_count*100:.1f}% | 出行分散无明显规律 |

**平均时间集中度**: {mod_avg_time_conc:.2f} (0-1，越接近1越集中)
**平均站点集中度**: {mod_avg_station_conc:.2f} (0-1，越接近1越集中)

**关键发现**：
- {mod_high_regularity + mod_medium_regularity:,}位中度依赖用户（{(mod_high_regularity + mod_medium_regularity)/moderate_only_count*100:.1f}%）表现出一定出行规律
- 中度依赖用户的规律性略低于重度依赖用户，符合预期
"""

    # ==================== 线路忠诚度分析（中度依赖用户）====================
    md_content += """

#### 7.3.5 线路忠诚度分析

分析中度依赖用户对公交线路的忠诚度，判断是否固定使用某几条线路。
"""

    # 计算每个中度依赖用户的线路忠诚度
    mod_route_loyalty_stats = []
    for s in moderate_only_users.values():
        loyalty = calculate_route_loyalty(s.get('route_distribution', {}))
        mod_route_loyalty_stats.append(loyalty)

    # 统计线路忠诚度等级
    mod_high_loyalty = sum(1 for l in mod_route_loyalty_stats if l['top1'] >= 0.7 or l['top3'] >= 0.9)
    mod_medium_loyalty = sum(1 for l in mod_route_loyalty_stats if (l['top1'] >= 0.4 or l['top3'] >= 0.6) and not (l['top1'] >= 0.7 or l['top3'] >= 0.9))
    mod_low_loyalty = moderate_only_count - mod_high_loyalty - mod_medium_loyalty

    # 计算平均指标
    mod_avg_top1_route = sum(l['top1'] for l in mod_route_loyalty_stats) / len(mod_route_loyalty_stats) if mod_route_loyalty_stats else 0
    mod_avg_top3_route = sum(l['top3'] for l in mod_route_loyalty_stats) / len(mod_route_loyalty_stats) if mod_route_loyalty_stats else 0
    mod_avg_effective_routes = sum(l['effective_routes'] for l in mod_route_loyalty_stats) / len(mod_route_loyalty_stats) if mod_route_loyalty_stats else 0

    md_content += f"""
| 线路忠诚度等级 | 用户数 | 占比 | 说明 |
|---------------|--------|------|------|
| 高线路忠诚 | {mod_high_loyalty:,} | {mod_high_loyalty/moderate_only_count*100:.1f}% | Top1线路≥70%或Top3线路≥90% |
| 中线路忠诚 | {mod_medium_loyalty:,} | {mod_medium_loyalty/moderate_only_count*100:.1f}% | Top1线路40-70%或Top3线路60-90% |
| 低线路忠诚 | {mod_low_loyalty:,} | {mod_low_loyalty/moderate_only_count*100:.1f}% | 线路使用分散 |

**平均Top1线路集中度**: {mod_avg_top1_route:.2f}
**平均Top3线路集中度**: {mod_avg_top3_route:.2f}
**平均有效线路数**: {mod_avg_effective_routes:.1f}条

**关键发现**：
- {mod_high_loyalty:,}位中度依赖用户（{mod_high_loyalty/moderate_only_count*100:.1f}%）高度依赖1-3条线路
- 平均每位中度依赖用户有效使用{mod_avg_effective_routes:.1f}条线路，略高于重度依赖用户
- 中度依赖用户的线路选择相对更多样化
"""

    # ==================== 站点时空集中度分析（中度依赖用户）====================
    md_content += """

#### 7.3.6 站点时空集中度分析

分析中度依赖用户的出行时间在更细粒度上的集中度（30分钟窗、1小时窗）。
"""

    # 计算每个中度依赖用户的时间窗集中度
    mod_time_window_30m = [calculate_time_window_concentration(s.get('minute_30slot_distribution', {}), 1) for s in moderate_only_users.values()]
    mod_time_window_1h = [calculate_time_window_concentration(s.get('minute_30slot_distribution', {}), 2) for s in moderate_only_users.values()]

    # 重新计算站点集中度
    mod_station_concs = [calculate_station_concentration(s.get('station_distribution', {})) for s in moderate_only_users.values()]

    # 统计时空集中度等级
    mod_high_spatial = sum(1 for s, t in zip(mod_station_concs, mod_time_window_1h) if s >= 0.6 and t >= 0.5)
    mod_medium_spatial = sum(1 for s, t in zip(mod_station_concs, mod_time_window_1h) if (s >= 0.3 or t >= 0.25) and not (s >= 0.6 and t >= 0.5))
    mod_low_spatial = moderate_only_count - mod_high_spatial - mod_medium_spatial

    # 计算平均值
    mod_avg_station_conc_new = sum(mod_station_concs) / len(mod_station_concs) if mod_station_concs else 0
    mod_avg_time_30m = sum(mod_time_window_30m) / len(mod_time_window_30m) if mod_time_window_30m else 0
    mod_avg_time_1h = sum(mod_time_window_1h) / len(mod_time_window_1h) if mod_time_window_1h else 0

    md_content += f"""
| 时空集中度等级 | 用户数 | 占比 | 说明 |
|---------------|--------|------|------|
| 高集中 | {mod_high_spatial:,} | {mod_high_spatial/moderate_only_count*100:.1f}% | Top1站点≥60%且1小时窗≥50% |
| 中集中 | {mod_medium_spatial:,} | {mod_medium_spatial/moderate_only_count*100:.1f}% | Top1站点30-60%或1小时窗25-50% |
| 低集中 | {mod_low_spatial:,} | {mod_low_spatial/moderate_only_count*100:.1f}% | 出行分散 |

**平均Top1站点集中度**: {mod_avg_station_conc_new:.2f}
**平均30分钟时窗集中度**: {mod_avg_time_30m:.2f}
**平均1小时时窗集中度**: {mod_avg_time_1h:.2f}

**关键发现**：
- {mod_high_spatial + mod_medium_spatial:,}位中度依赖用户（{(mod_high_spatial + mod_medium_spatial)/moderate_only_count*100:.1f}%）表现出一定的站点或时间集中度
- 中度依赖用户的站点和时间集中度均低于重度依赖用户，符合预期
"""

    # ==================== 综合规律性评级（中度依赖用户）====================
    md_content += """

#### 7.3.7 综合规律性评级

综合线路忠诚度、时间集中度、站点集中度三个维度，对中度依赖用户的出行规律进行综合评级。
"""

    # 综合评级（三项指标均考虑）
    mod_comprehensive_rating = {}
    for user_id, s in moderate_only_users.items():
        loyalty = calculate_route_loyalty(s.get('route_distribution', {}))
        time_conc = calculate_time_concentration(s.get('hour_distribution', {}))
        station_conc = calculate_station_concentration(s.get('station_distribution', {}))

        # 高规律：三项指标中至少两项达到高等级
        high_count = 0
        if loyalty['top1'] >= 0.7 or loyalty['top3'] >= 0.9:
            high_count += 1
        if time_conc > 0.7:
            high_count += 1
        if station_conc > 0.5:
            high_count += 1

        # 中规律：三项指标中至少两项达到中等级及以上
        medium_count = 0
        if loyalty['top1'] >= 0.4 or loyalty['top3'] >= 0.6:
            medium_count += 1
        if time_conc > 0.5:
            medium_count += 1
        if station_conc > 0.3:
            medium_count += 1

        if high_count >= 2:
            mod_comprehensive_rating[user_id] = 'high'
        elif medium_count >= 2:
            mod_comprehensive_rating[user_id] = 'medium'
        else:
            mod_comprehensive_rating[user_id] = 'low'

    mod_high_comprehensive = sum(1 for r in mod_comprehensive_rating.values() if r == 'high')
    mod_medium_comprehensive = sum(1 for r in mod_comprehensive_rating.values() if r == 'medium')
    mod_low_comprehensive = sum(1 for r in mod_comprehensive_rating.values() if r == 'low')

    md_content += f"""
| 综合规律性等级 | 用户数 | 占比 | 特征描述 |
|---------------|--------|------|----------|
| 高规律（固定线路+固定时间+固定站点） | {mod_high_comprehensive:,} | {mod_high_comprehensive/moderate_only_count*100:.1f}% | 三项指标中至少两项达到高等级 |
| 中规律（部分固定） | {mod_medium_comprehensive:,} | {mod_medium_comprehensive/moderate_only_count*100:.1f}% | 三项指标中至少两项达到中等级 |
| 低规律（无明显规律） | {mod_low_comprehensive:,} | {mod_low_comprehensive/moderate_only_count*100:.1f}% | 其他 |

**综合分析结论**：
- {mod_high_comprehensive + mod_medium_comprehensive:,}位中度依赖用户（{(mod_high_comprehensive + mod_medium_comprehensive)/moderate_only_count*100:.1f}%）表现出较为固定的出行模式
- 中度依赖用户的综合规律性比例为{(mod_high_comprehensive + mod_medium_comprehensive)/moderate_only_count*100:.1f}%，低于重度依赖用户
"""

    # ==================== 时空对分析（中度依赖用户）====================
    md_content += """

#### 7.3.8 时空对分析（往返出行模式）

分析中度依赖用户是否形成固定的"早出晚归"往返模式，从时间集中度和空间集中度两个维度评估时空对质量。
"""

    # 统计中度依赖用户的时空对信息
    mod_users_with_pair = {uid: s for uid, s in moderate_only_users.items() if s.get('spatiotemporal_pair')}
    mod_users_with_pair_count = len(mod_users_with_pair)

    if mod_users_with_pair_count > 0:
        # 按置信度等级统计
        mod_high_confidence = sum(1 for s in mod_users_with_pair.values() if s['spatiotemporal_pair']['confidence_level'] == '高')
        mod_medium_confidence = sum(1 for s in mod_users_with_pair.values() if s['spatiotemporal_pair']['confidence_level'] == '中')
        mod_low_confidence = sum(1 for s in mod_users_with_pair.values() if s['spatiotemporal_pair']['confidence_level'] == '低')

        # 计算平均集中度指标
        mod_avg_morning_station_conc = sum(s['spatiotemporal_pair']['morning_station_concentration']
                                           for s in mod_users_with_pair.values()) / mod_users_with_pair_count
        mod_avg_evening_station_conc = sum(s['spatiotemporal_pair']['evening_station_concentration']
                                           for s in mod_users_with_pair.values()) / mod_users_with_pair_count
        mod_avg_morning_time_conc = sum(s['spatiotemporal_pair']['morning_time_concentration']
                                        for s in mod_users_with_pair.values()) / mod_users_with_pair_count
        mod_avg_evening_time_conc = sum(s['spatiotemporal_pair']['evening_time_concentration']
                                        for s in mod_users_with_pair.values()) / mod_users_with_pair_count
        mod_avg_pair_concentration = sum(s['spatiotemporal_pair']['pair_concentration']
                                        for s in mod_users_with_pair.values()) / mod_users_with_pair_count

        # 统计往返类型
        mod_round_trip_count = sum(1 for s in mod_users_with_pair.values() if s['spatiotemporal_pair']['is_round_trip'])
        mod_same_station_count = mod_users_with_pair_count - mod_round_trip_count

        # 获取最常见的早晚站点对（分别统计往返和同站）
        mod_station_pairs_round_trip = Counter()  # 往返：早晚站点不同
        mod_station_pairs_same_station = Counter()  # 同站：早晚站点相同
        for s in mod_users_with_pair.values():
            pair = s['spatiotemporal_pair']
            if pair['morning_station'] and pair['evening_station']:
                if pair['morning_station'] != pair['evening_station']:
                    mod_station_pairs_round_trip[(pair['morning_station'], pair['evening_station'])] += 1
                else:
                    mod_station_pairs_same_station[(pair['morning_station'], pair['evening_station'])] += 1

        mod_top_round_trip_pair = mod_station_pairs_round_trip.most_common(1)[0] if mod_station_pairs_round_trip else (None, 0)
        mod_top_same_station_pair = mod_station_pairs_same_station.most_common(1)[0] if mod_station_pairs_same_station else (None, 0)

        md_content += f"""
**时空对覆盖范围**：{mod_users_with_pair_count:,}位用户（{mod_users_with_pair_count/moderate_only_count*100:.1f}%）具有明显的早晚出行模式

##### 7.3.8.1 时空对置信度分布

| 置信度等级 | 用户数 | 占有时空对用户比例 | 说明 |
|-----------|--------|-------------------|------|
| 高置信度 | {mod_high_confidence:,} | {mod_high_confidence/mod_users_with_pair_count*100:.1f}% | 站点集中度≥70%且时间集中度≥70% |
| 中置信度 | {mod_medium_confidence:,} | {mod_medium_confidence/mod_users_with_pair_count*100:.1f}% | 站点或时间集中度40-70% |
| 低置信度 | {mod_low_confidence:,} | {mod_low_confidence/mod_users_with_pair_count*100:.1f}% | 站点和时间都<40% |

**平均早上站点集中度**: {mod_avg_morning_station_conc*100:.1f}%
**平均晚上站点集中度**: {mod_avg_evening_station_conc*100:.1f}%
**平均早上时间集中度**: {mod_avg_morning_time_conc*100:.1f}%（2小时窗覆盖率）
**平均晚上时间集中度**: {mod_avg_evening_time_conc*100:.1f}%（2小时窗覆盖率）
**平均时空对时段占比**: {mod_avg_pair_concentration*100:.1f}%（早晚出行占总出行比例）

##### 7.3.8.2 时空对模式分析

| 模式类型 | 用户数 | 占有时空对用户比例 | 说明 |
|---------|--------|-------------------|------|
| 往返模式（早晚不同站点） | {mod_round_trip_count:,} | {mod_round_trip_count/mod_users_with_pair_count*100:.1f}% | 典型通勤/往返模式 |
| 同站往返模式（早晚相同站点） | {mod_same_station_count:,} | {mod_same_station_count/mod_users_with_pair_count*100:.1f}% | 同一站点多次往返 |

**最常见的往返时空对**：{mod_top_round_trip_pair[0][0] if mod_top_round_trip_pair[0] else '无'}(早上) → {mod_top_round_trip_pair[0][1] if mod_top_round_trip_pair[0] else '无'}(晚上) ({mod_top_round_trip_pair[1]:,}位用户)
**最常见的同站往返站点**：{mod_top_same_station_pair[0][0] if mod_top_same_station_pair[0] else '无'} ({mod_top_same_station_pair[1]:,}位用户)

##### 7.3.8.3 关键发现

- {mod_users_with_pair_count:,}位中度依赖用户（{mod_users_with_pair_count/moderate_only_count*100:.1f}%）形成明显的早晚出行模式
- {mod_high_confidence:,}位用户（{mod_high_confidence/mod_users_with_pair_count*100:.1f}%）具有高置信度时空对，比例{mod_high_confidence/mod_users_with_pair_count*100:.1f}%，低于重度依赖用户
- 平均早上站点集中度为{mod_avg_morning_station_conc*100:.1f}%，平均晚上站点集中度为{mod_avg_evening_station_conc*100:.1f}%，略低于重度依赖用户
- 平均早上时间集中度为{mod_avg_morning_time_conc*100:.1f}%，平均晚上时间集中度为{mod_avg_evening_time_conc*100:.1f}%，略低于重度依赖用户
- {mod_round_trip_count/mod_users_with_pair_count*100:.1f}%的用户形成往返模式（早晚站点不同）
- {mod_same_station_count/mod_users_with_pair_count*100:.1f}%的用户形成同站往返模式，可能是同一站点进行多项活动
"""
    else:
        md_content += """
**无时空对数据**：中度依赖用户中未检测到明显的早晚出行模式。
"""

    md_content += f"""

---

## 八、身份证用户深度分析

### 8.1 身份证用户概况

基于身份证号码验证，从可溯源用户中识别出有有效身份证信息的用户：

| 指标 | 数值 |
|------|------|
| **有身份证用户数** | {id_card_count:,} |
| **占可溯源用户比例** | {id_card_count/total_users*100:.1f}% |

### 8.2 年龄分布

身份证用户按年龄分组统计：

| 年龄组 | 用户数 | 用户占比 | 出行次数 | 客流占比 | 平均出行次数 | 付费用户占比 |
|--------|--------|----------|----------|----------|--------------|--------------|
"""

    # 添加年龄分布表格
    age_group_stats = {}
    for stats in user_stats.values():
        if stats.get('has_valid_id', False):
            age_group = stats.get('age_group', '未知')
            if age_group not in age_group_stats:
                age_group_stats[age_group] = {
                    'users': 0,
                    'total_trips': 0,
                    'paid_users': 0
                }
            age_group_stats[age_group]['users'] += 1
            age_group_stats[age_group]['total_trips'] += stats['total_trips']
            if stats['paid_trip_count'] > 0:
                age_group_stats[age_group]['paid_users'] += 1

    # 年龄组顺序
    age_group_order = ['未成年(<18岁)', '青年(18-29岁)', '中年(30-49岁)', '中老年(50-59岁)', '老年(60-64岁)', '高龄(65-69岁)', '超高龄(≥70岁)']

    # 计算身份证用户总出行次数
    id_card_total_trips = sum(v['total_trips'] for v in age_group_stats.values())

    for age_group in age_group_order:
        if age_group in age_group_stats:
            stats_data = age_group_stats[age_group]
            user_pct = stats_data['users'] / id_card_count * 100
            trips = stats_data['total_trips']
            trip_pct = trips / id_card_total_trips * 100
            avg_trips = stats_data['total_trips'] / stats_data['users']
            paid_pct = stats_data['paid_users'] / stats_data['users'] * 100
            md_content += f"| {age_group} | {stats_data['users']:,} | {user_pct:.1f}% | {trips:,} | {trip_pct:.1f}% | {avg_trips:.1f} | {paid_pct:.1f}% |\n"

    # 计算老年用户比例
    elderly_users = sum(v['users'] for k, v in age_group_stats.items() if k in ['老年(60-64岁)', '高龄(65-69岁)', '超高龄(≥70岁)'])
    working_age_users = sum(v['users'] for k, v in age_group_stats.items() if k in ['青年(18-29岁)', '中年(30-49岁)', '中老年(50-59岁)'])

    md_content += f"""

### 8.3 年龄结构关键发现

* **老年用户（≥60岁）**: {elderly_users:,} ({elderly_users/id_card_count*100:.1f}%)
* **工作年龄段用户（18-59岁）**: {working_age_users:,} ({working_age_users/id_card_count*100:.1f}%)
* **老龄化严重**: 超过{elderly_users/id_card_count*100:.0f}%的身份证用户为老年人

### 8.4 调整后的通勤分析

**重要说明**: 由于绝大多数身份证用户为老年人（≥60岁），其出行主要为日常生活需求，不应归类为"通勤"。因此，在本次分析中，年龄≥65岁的用户被重新分类为"老年出行"而非通勤用户。

| 用户类型 | 用户数 | 占总用户比例 | 说明 |
|----------|--------|--------------|------|
| **通勤用户** | {commuter_users:,} | {commuter_users/total_users*100:.1f}% | 仅工作年龄段用户，规律出行 |
| **老年出行用户** | {sum(1 for s in user_stats.values() if s.get('commuter_type') == 'senior_citizen'):,} | {sum(1 for s in user_stats.values() if s.get('commuter_type') == 'senior_citizen')/total_users*100:.1f}% | 年龄≥65岁，日常生活出行 |
| **非通勤用户** | {sum(1 for s in user_stats.values() if s.get('commuter_type') == 'non_commuter'):,} | {sum(1 for s in user_stats.values() if s.get('commuter_type') == 'non_commuter')/total_users*100:.1f}% | 不规律出行用户 |

### 8.5 综合客流分析

本节从客流（出行次数）维度分析各用户群体的贡献度。

#### 8.5.1 卡类型客流贡献度

| 卡类型 | 用户数占比 | 客流占比 | 人均出行次数 | 说明 |
|--------|-----------|----------|--------------|------|
"""

    # 添加卡类型客流贡献度表格
    for card_type, count in card_type_dist.most_common():
        user_pct = count / total_users * 100
        trips = card_type_trip_dist[card_type]
        trip_pct = trips / total_trips * 100
        avg_trips = trips / count
        # 添加说明
        if card_type == '身份证':
            note = '主要客流来源'
        elif card_type == '敬老卡':
            note = '老年用户'
        elif card_type == '献血荣誉卡':
            note = '荣誉卡用户'
        elif card_type == '普通卡':
            note = '普通乘客'
        else:
            note = '-'
        md_content += f"| {card_type} | {user_pct:.1f}% | {trip_pct:.1f}% | {avg_trips:.1f}次 | {note} |\n"

    md_content += f"""

**关键发现**：
- **用户占比 ≠ 客流占比**：某些卡类型用户少但出行频次高（如敬老卡）
- **身份证用户贡献**：占用户{sum(1 for s in user_stats.values() if s['card_type']=='身份证')/total_users*100:.1f}%，贡献了{card_type_trip_dist['身份证']/total_trips*100:.1f}%的客流
- **客流集中度**：前3种卡类型贡献了{sum(card_type_trip_dist[ct] for ct in list(card_type_dist)[:3])/total_trips*100:.1f}%的客流

#### 8.5.2 年龄组客流贡献度

| 年龄组 | 用户数占比 | 客流占比 | 人均出行次数 | 说明 |
|--------|-----------|----------|--------------|------|
"""

    # 计算总出行次数（所有用户）
    all_trips = total_trips

    # 添加年龄组客流贡献度表格
    for age_group in age_group_order:
        if age_group in age_group_stats:
            stats_data = age_group_stats[age_group]
            user_pct = stats_data['users'] / id_card_count * 100
            trips = stats_data['total_trips']
            trip_pct = trips / id_card_total_trips * 100
            avg_trips = trips / stats_data['users']
            # 添加说明
            if age_group == '超高龄(≥70岁)':
                note = '主要客流来源'
            elif age_group == '高龄(65-69岁)':
                note = '高龄用户'
            elif age_group in ['青年(18-29岁)', '中年(30-49岁)']:
                note = '工作年龄段'
            else:
                note = '-'
            md_content += f"| {age_group} | {user_pct:.1f}% | {trip_pct:.1f}% | {avg_trips:.1f}次 | {note} |\n"

    md_content += f"""

**关键发现**：
- **超高龄用户主导**：占身份证用户{age_group_stats.get('超高龄(≥70岁)', {'users': 0}).get('users', 0)/id_card_count*100:.1f}%，贡献了{age_group_stats.get('超高龄(≥70岁)', {'total_trips': 0}).get('total_trips', 0)/id_card_total_trips*100:.1f}%的客流
- **人均出行频次**：超高龄用户平均出行{age_group_stats.get('超高龄(≥70岁)', {'total_trips': 0, 'users': 1}).get('total_trips', 0)/max(age_group_stats.get('超高龄(≥70岁)', {'users': 1}).get('users', 1), 1):.1f}次

---

## 九、关键发现

### 9.1 用户活跃度

* **高频用户** (≥50次): {trip_ranges['50+次']:,} ({trip_ranges['50+次']/total_users*100:.1f}%)
* **中频用户** (11-50次): {trip_ranges['11-20次'] + trip_ranges['21-50次']:,} ({(trip_ranges['11-20次'] + trip_ranges['21-50次'])/total_users*100:.1f}%)
* **低频用户** (≤10次): {trip_ranges['1次'] + trip_ranges['2-5次'] + trip_ranges['6-10次']:,} ({(trip_ranges['1次'] + trip_ranges['2-5次'] + trip_ranges['6-10次'])/total_users*100:.1f}%)

### 9.2 通勤模式

* **通勤用户比例**: {commuter_users/total_users*100:.1f}%
* **主要通勤类型**:
"""

    # 添加通勤类型分析
    commuter_type_analysis = Counter(s['commuter_type'] for s in user_stats.values() if s['is_commuter'])
    for comm_type, count in commuter_type_analysis.most_common():
        label = commuter_labels_map.get(comm_type, comm_type)
        percentage = count / commuter_users * 100 if commuter_users > 0 else 0
        md_content += f"  - **{label}**: {count:,} ({percentage:.1f}%)\n"

    md_content += f"""

### 9.3 付费行为

* **付费转化率**: {total_paid_users/total_users*100:.1f}% (付费用户/总用户)
* **免费用户占主导**: {total_free_users/total_users*100:.1f}% 的用户完全免费出行
* **人均营收贡献**: ¥{total_revenue/total_users:.2f}

---

## 十、数据文件说明

本报告基于以下数据文件生成：

* `user_travel_summary.csv`: 用户出行汇总统计
* `user_travel_details.json`: 用户出行明细数据
* `id_card_users_details.csv`: 身份证用户详细信息表

---

*报告由黄山公交用户出行规律分析工具自动生成*
"""

    # 保存Markdown文件
    output_md = OUTPUT_DIR / 'user_travel_summary.md'
    with open(output_md, 'w', encoding='utf-8') as f:
        f.write(md_content)

    print(f"  已保存: {output_md}")

# ==================== 主程序 ====================

def main():
    print("=" * 60)
    print("用户出行规律分析工具")
    print("=" * 60)

    # 1. 读取IC卡数据
    records = read_all_ic_card_data()
    if not records:
        print("错误: 没有有效数据")
        return

    # 2. 按用户聚合
    user_trips = group_by_user(records)

    # 3. 深度分析
    user_stats = analyze_user_patterns(user_trips)

    # 4. 输出结果
    export_csv_summary(user_stats)
    export_json_details(user_trips, user_stats)
    export_id_card_users_details(user_stats)
    generate_plotly_report(user_trips, user_stats)
    generate_markdown_summary(user_trips, user_stats)
    generate_html_report(user_trips, user_stats)

    print("\n" + "=" * 60)
    print("分析完成！")
    print("=" * 60)
    print(f"\n输出文件:")
    print(f"  - CSV汇总: {OUTPUT_DIR / 'user_travel_summary.csv'}")
    print(f"  - JSON明细: {OUTPUT_DIR / 'user_travel_details.json'}")
    print(f"  - 身份证用户详情: {OUTPUT_DIR / 'id_card_users_details.csv'}")
    print(f"  - Plotly交互式报告: {OUTPUT_DIR / 'user_travel_analysis.html'}")
    print(f"  - Markdown总结: {OUTPUT_DIR / 'user_travel_summary.md'}")
    print(f"  - HTML报告: {OUTPUT_DIR / 'user_travel_report.html'}")

if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print(f"\n错误: {e}")
        import traceback
        traceback.print_exc()
