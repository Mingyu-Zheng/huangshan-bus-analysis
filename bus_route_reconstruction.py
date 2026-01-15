#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
公交路线还原脚本
根据站点坐标和线路信息，在路网上还原公交路线
"""

import csv
import json
import math
import glob
import os
import re
from collections import defaultdict, deque
from typing import List, Dict, Tuple, Optional, Set
from pathlib import Path
import heapq

REPO_ROOT = Path(__file__).resolve().parent
IC_DATA_DIR = REPO_ROOT / "INIT_IC_data"
OUTPUT_DIR = REPO_ROOT / "MID_output"
MID_STATION_DIR = REPO_ROOT / "MID_station"
OUT_DIR = REPO_ROOT / "OUT_visualization"

def read_csv_gb2312(filename):
    """读取GB2312/GBK编码的CSV文件"""
    data = []
    # 优先尝试UTF-8，其次再尝试常见的国标编码
    encodings = ['utf-8', 'utf-8-sig', 'gb18030', 'gbk', 'gb2312']
    
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

def haversine_distance(lon1, lat1, lon2, lat2):
    """计算两点间的球面距离（米）"""
    R = 6371000  # 地球半径（米）
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    c = 2 * math.asin(math.sqrt(a))
    return R * c

def point_to_line_distance(px, py, x1, y1, x2, y2):
    """计算点到线段的最短距离（米）和最近点"""
    # 向量计算
    dx = x2 - x1
    dy = y2 - y1
    
    if dx == 0 and dy == 0:
        # 线段退化为点
        return haversine_distance(px, py, x1, y1), (x1, y1), 0.0
    
    # 计算投影参数t
    t = ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))  # 限制在[0,1]范围内
    
    # 最近点坐标
    closest_x = x1 + t * dx
    closest_y = y1 + t * dy
    
    # 计算距离
    distance = haversine_distance(px, py, closest_x, closest_y)
    
    return distance, (closest_x, closest_y), t

def cross_product_2d(x1, y1, x2, y2):
    """计算二维向量叉积"""
    return x1 * y2 - x2 * y1

def is_right_side(station_lon, station_lat, link_start, link_end):
    """
    判断站点是否在道路的右侧（靠右行驶）
    使用叉积判断：如果叉积为正，站点在道路右侧
    """
    # 道路方向向量
    road_dx = link_end[0] - link_start[0]
    road_dy = link_end[1] - link_start[1]
    
    # 从起点到站点的向量
    to_station_dx = station_lon - link_start[0]
    to_station_dy = station_lat - link_start[1]
    
    # 计算叉积
    cross = cross_product_2d(road_dx, road_dy, to_station_dx, to_station_dy)
    
    # 叉积为正表示在右侧（从起点看向终点，右侧为正）
    return cross > 0

class RoadNetwork:
    """路网类"""
    def __init__(self, links_data):
        self.links = {}
        self.link_coords = {}
        self.graph = defaultdict(list)  # 邻接表：link_id -> [(next_link_id, distance), ...]
        self.link_to_nodes = {}  # link_id -> (start_node, end_node)
        self.node_to_links = defaultdict(list)  # node_id -> [link_ids]
        
        print("正在构建路网...")
        for link in links_data:
            link_id = link['link_id']
            self.links[link_id] = link
            self.link_coords[link_id] = link['link_coors']
            
            # 构建节点连接关系
            start_node = tuple(link['start_coor'])
            end_node = tuple(link['end_coor'])
            self.link_to_nodes[link_id] = (start_node, end_node)
            self.node_to_links[start_node].append(link_id)
            self.node_to_links[end_node].append(link_id)
        
        # 构建有向图（基于out_top字段）
        print("正在构建有向连接图...")
        for link_id, link in self.links.items():
            # 使用out_top字段构建有向边
            # out_top: 从当前link可以到达的link_id列表
            out_top = link.get('out_top', [])
            if out_top:
                for next_link_id in out_top:
                    if next_link_id in self.links:
                        # 使用下一个link的长度作为边的权重（更合理）
                        # 因为我们要计算的是通过下一个link的成本
                        next_link = self.links[next_link_id]
                        next_link_length = next_link.get('link_length', 0)
                        # 如果下一个link长度为0，使用当前link的长度作为fallback
                        if next_link_length == 0:
                            next_link_length = link.get('link_length', 0)
                        self.graph[link_id].append((next_link_id, next_link_length))
            
            # 如果没有out_top字段，回退到基于节点连接的方法
            # 但只考虑从end_node出发的link（保持方向性）
            elif 'out_top' not in link:
                end_node = self.link_to_nodes[link_id][1]
                for next_link_id in self.node_to_links[end_node]:
                    if next_link_id != link_id and next_link_id in self.links:
                        # 使用下一个link的长度作为权重
                        next_link = self.links[next_link_id]
                        next_link_length = next_link.get('link_length', 0)
                        if next_link_length == 0:
                            next_link_length = link.get('link_length', 0)
                        self.graph[link_id].append((next_link_id, next_link_length))
        
        # 统计有向边数量
        total_edges = sum(len(neighbors) for neighbors in self.graph.values())
        print(f"路网构建完成: {len(self.links)} 个link, {len(self.graph)} 个有向节点, {total_edges} 条有向边")
    
    def find_closest_link(self, lon, lat, prefer_right=True):
        """
        找到距离站点最近的link
        如果有多个候选，优先选择右侧道路（靠右行驶）
        """
        min_distance = float('inf')
        best_link_id = None
        best_point = None
        best_t = None
        
        candidates = []
        
        for link_id, coords in self.link_coords.items():
            if len(coords) < 2:
                continue
            
            # 检查每个线段段
            for i in range(len(coords) - 1):
                x1, y1 = coords[i]
                x2, y2 = coords[i + 1]
                
                distance, closest_point, t = point_to_line_distance(lon, lat, x1, y1, x2, y2)
                
                if distance < min_distance:
                    min_distance = distance
                    best_link_id = link_id
                    best_point = closest_point
                    best_t = t + i  # 调整t值以反映在整条link中的位置
                
                # 收集所有候选（距离在阈值内）
                if distance < 100:  # 100米内
                    candidates.append({
                        'link_id': link_id,
                        'distance': distance,
                        'point': closest_point,
                        't': t + i,
                        'segment_start': (x1, y1),
                        'segment_end': (x2, y2)
                    })
        
        if not best_link_id:
            return None, None, None
        
        # 如果有多个候选且需要选择右侧道路
        if prefer_right and len(candidates) > 1:
            # 筛选距离相近的候选（在最小距离的1.5倍内）
            threshold = min_distance * 1.5
            close_candidates = [c for c in candidates if c['distance'] <= threshold]
            
            if len(close_candidates) > 1:
                # 选择右侧道路
                right_side_candidates = []
                for cand in close_candidates:
                    link_id = cand['link_id']
                    link = self.links[link_id]
                    link_coords = self.link_coords[link_id]
                    
                    if len(link_coords) >= 2:
                        start = link_coords[0]
                        end = link_coords[-1]
                        if is_right_side(lon, lat, start, end):
                            right_side_candidates.append(cand)
                
                if right_side_candidates:
                    # 从右侧候选中选择最近的
                    best_cand = min(right_side_candidates, key=lambda x: x['distance'])
                    return best_cand['link_id'], best_cand['point'], best_cand['t']
        
        return best_link_id, best_point, best_t
    
    def shortest_path(self, start_link_id, end_link_id, start_t=None, end_t=None):
        """
        使用Dijkstra算法计算两个link之间的最短路径
        返回路径上的link_id列表和总距离
        """
        if start_link_id == end_link_id:
            return [start_link_id], 0.0
        
        # 检查link是否存在
        if start_link_id not in self.links or end_link_id not in self.links:
            return None, float('inf')
        
        # 使用Dijkstra算法
        dist = {start_link_id: 0.0}
        prev = {start_link_id: None}  # 初始化start_link_id的前驱为None
        pq = [(0.0, start_link_id)]
        visited = set()
        
        while pq:
            current_dist, current_link = heapq.heappop(pq)
            
            # 跳过已访问的节点（可能有更短的路径已处理过）
            if current_link in visited:
                continue
            
            visited.add(current_link)
            
            # 找到目标，重建路径
            if current_link == end_link_id:
                path = []
                node = end_link_id
                # 从终点回溯到起点
                while node is not None:
                    path.append(node)
                    node = prev.get(node)
                path.reverse()
                return path, current_dist
            
            # 遍历当前link的所有出边（邻居）
            for next_link, edge_weight in self.graph.get(current_link, []):
                if next_link in visited:
                    continue
                
                # 计算从起点到next_link的新距离
                new_dist = current_dist + edge_weight
                
                # 如果找到更短的路径，更新距离和前驱
                if next_link not in dist or new_dist < dist[next_link]:
                    dist[next_link] = new_dist
                    prev[next_link] = current_link
                    heapq.heappush(pq, (new_dist, next_link))
        
        # 如果队列为空仍未找到目标，说明不可达
        return None, float('inf')
    
    def get_link_path_coords(self, link_ids):
        """
        获取路径上所有link的坐标序列（按有向图方向）
        确保坐标按照link的方向顺序连接
        """
        coords = []
        for i, link_id in enumerate(link_ids):
            if link_id not in self.link_coords:
                continue
                
            link_coords = self.link_coords[link_id]
            if not link_coords:
                continue
            
            if i == 0:
                # 第一个link，直接添加所有坐标
                coords.extend(link_coords)
            else:
                # 后续link，检查是否需要反转
                prev_link_id = link_ids[i - 1]
                prev_link = self.links.get(prev_link_id)
                current_link = self.links.get(link_id)
                
                if prev_link and current_link:
                    # 检查前一个link的终点是否与当前link的起点连接
                    prev_end = tuple(prev_link.get('end_coor', []))
                    current_start = tuple(current_link.get('start_coor', []))
                    current_end = tuple(current_link.get('end_coor', []))
                    
                    # 计算距离判断连接点
                    def coord_distance(c1, c2):
                        if not c1 or not c2 or len(c1) < 2 or len(c2) < 2:
                            return float('inf')
                        return haversine_distance(c1[0], c1[1], c2[0], c2[1])
                    
                    dist_to_start = coord_distance(prev_end, current_start)
                    dist_to_end = coord_distance(prev_end, current_end)
                    
                    # 如果前一个终点更接近当前终点，说明需要反转
                    if dist_to_end < dist_to_start and dist_to_end < 10:  # 10米内认为连接
                        # 反转坐标
                        coords.extend(reversed(link_coords))
                    else:
                        # 正常顺序
                        if coords and len(link_coords) > 0:
                            # 检查是否重复（如果前一个终点和当前起点相同，跳过第一个点）
                            last_coord = coords[-1]
                            first_coord = link_coords[0]
                            if (abs(last_coord[0] - first_coord[0]) < 1e-6 and 
                                abs(last_coord[1] - first_coord[1]) < 1e-6):
                                coords.extend(link_coords[1:])
                            else:
                                coords.extend(link_coords)
                        else:
                            coords.extend(link_coords)
                else:
                    # 如果无法判断，尝试智能连接
                    if coords and len(link_coords) > 0:
                        last_coord = coords[-1]
                        first_coord = link_coords[0]
                        last_coord_rev = link_coords[-1]
                        
                        # 判断是正序还是反序连接更近
                        dist_normal = haversine_distance(
                            last_coord[0], last_coord[1],
                            first_coord[0], first_coord[1]
                        )
                        dist_reverse = haversine_distance(
                            last_coord[0], last_coord[1],
                            last_coord_rev[0], last_coord_rev[1]
                        )
                        
                        if dist_reverse < dist_normal and dist_reverse < 50:  # 50米内
                            coords.extend(reversed(link_coords))
                        else:
                            if dist_normal < 50:  # 50米内认为连接
                                coords.extend(link_coords[1:])
                            else:
                                coords.extend(link_coords)
                    else:
                        coords.extend(link_coords)
        
        return coords

class BusRouteReconstructor:
    """公交路线还原器"""
    def __init__(self, network: RoadNetwork, stations_data: List[Dict], routes_data: List[Dict]):
        self.network = network
        self.stations_data = stations_data
        self.routes_data = routes_data
        # 缓存：缩短重复计算
        self.shortest_path_cache = {}  # (start_link_id, end_link_id) -> (path, length)
        self.coord_link_cache = {}      # (lon, lat) -> (link_id, point, t)
        
        # 构建站点索引：站点名 -> [(lon, lat, index), ...]
        self.station_index = defaultdict(list)
        self.station_name_variants = {}  # 存储名称变体映射
        
        for idx, station in enumerate(stations_data):
            station_name = (
                station.get('station_name', '') or
                station.get('站 点', '') or
                station.get('站点', '')
            ).strip()
            if not station_name:
                continue
            try:
                lon = float(
                    str(
                        station.get('station_lon', '') or
                        station.get('站点经度', '') or
                        station.get('经度', '')
                    ).strip()
                )
                lat = float(
                    str(
                        station.get('station_lat', '') or
                        station.get('站点纬度', '') or
                        station.get('纬度', '')
                    ).strip()
                )
                self.station_index[station_name].append((lon, lat, idx))
                
                # 创建名称变体（去除括号内容、空格等）
                name_variants = [
                    station_name,
                    station_name.replace('（', '(').replace('）', ')'),
                    station_name.replace('(', '').replace(')', ''),
                    station_name.replace('（', '').replace('）', ''),
                    station_name.replace(' ', ''),
                ]
                for variant in name_variants:
                    if variant != station_name:
                        if variant not in self.station_name_variants:
                            self.station_name_variants[variant] = []
                        self.station_name_variants[variant].append(station_name)
            except (ValueError, KeyError):
                continue
    
    def find_station_candidates(self, station_name):
        """查找站点候选（包括名称变体）"""
        # 直接匹配
        if station_name in self.station_index:
            return self.station_index[station_name]
        
        # 尝试名称变体
        if station_name in self.station_name_variants:
            candidates = []
            for variant_name in self.station_name_variants[station_name]:
                if variant_name in self.station_index:
                    candidates.extend(self.station_index[variant_name])
            if candidates:
                return candidates
        
        # 尝试模糊匹配（去除括号、空格等）
        name_clean = station_name.replace('（', '').replace('）', '').replace('(', '').replace(')', '').replace(' ', '')
        for key in self.station_index.keys():
            key_clean = key.replace('（', '').replace('）', '').replace('(', '').replace(')', '').replace(' ', '')
            if name_clean == key_clean or name_clean in key_clean or key_clean in name_clean:
                return self.station_index[key]
        
        return []
    
    def match_station_to_link(self, station_name, previous_link_id=None, previous_point=None, lon=None, lat=None):
        """
        将站点匹配到link
        如果有多个同名站点，选择距离前一个站点最短路径的站点
        """
        # 如果提供了坐标，直接基于坐标匹配最近link
        if lon is not None and lat is not None:
            key = (round(float(lon), 6), round(float(lat), 6))
            if key in self.coord_link_cache:
                link_id, point, t = self.coord_link_cache[key]
            else:
                link_id, point, t = self.network.find_closest_link(lon, lat, prefer_right=True)
                self.coord_link_cache[key] = (link_id, point, t)
            return link_id, point, (lon, lat)
        
        candidates = self.find_station_candidates(station_name)
        
        if not candidates:
            return None, None, None
        
        if len(candidates) == 1:
            # 只有一个候选
            lon, lat, _ = candidates[0]
            link_id, point, t = self.network.find_closest_link(lon, lat, prefer_right=True)
            return link_id, point, (lon, lat)
        
        # 多个候选，需要选择最短路径
        if previous_link_id and previous_point:
            best_candidate = None
            min_path_length = float('inf')
            
            for lon, lat, _ in candidates:
                link_id, point, t = self.network.find_closest_link(lon, lat, prefer_right=True)
                if not link_id:
                    continue
                
                # 计算从前一个link到当前link的最短路径
                path, path_length = self.network.shortest_path(previous_link_id, link_id)
                
                if path and path_length < min_path_length:
                    min_path_length = path_length
                    best_candidate = (link_id, point, (lon, lat))
            
            if best_candidate:
                return best_candidate
        
        # 如果没有前一个站点，选择最近的
        best_candidate = None
        min_distance = float('inf')
        for lon, lat, _ in candidates:
            link_id, point, t = self.network.find_closest_link(lon, lat, prefer_right=True)
            if link_id:
                # 使用link长度作为距离估计
                distance = self.network.links[link_id].get('link_length', 0)
                if distance < min_distance:
                    min_distance = distance
                    best_candidate = (link_id, point, (lon, lat))
        
        return best_candidate if best_candidate else (None, None, None)
    
    def reconstruct_route(self, route_info, direction='forward'):
        """
        还原一条公交路线
        
        Args:
            route_info: 线路信息字典
            direction: 'forward' 正向 或 'reverse' 反向
        """
        route_name = (route_info.get('线路名称') or route_info.get('route') or '').strip()
        direction = route_info.get('direction', direction) or 'forward'
        
        stations_list = route_info.get('stations_list')  # 新CSV提供的站点列表（已按顺序）
        stations_str = route_info.get('线路经过的站点', '').strip()
        
        # 解析站点名称序列和坐标序列
        station_entries = None
        if stations_list:
            station_entries = stations_list
            stations = [s.get('name', '').strip() for s in stations_list if s.get('name', '').strip()]
            # 新格式：若方向为 reverse/B，则反转站点序列
            if direction == 'reverse':
                stations = list(reversed(stations))
                station_entries = list(reversed(station_entries))
        else:
            if not stations_str:
                return None
            stations = [s.strip() for s in stations_str.split('—') if s.strip()]
            # 如果是反向，反转站点顺序（仅针对旧格式）
            if direction == 'reverse':
                stations = list(reversed(stations))
        
        if len(stations) < 2:
            return None
        
        direction_label = "反向" if direction == 'reverse' else "正向"
        
        print(f"\n正在还原路线: {route_name} ({direction_label})")
        print(f"  站点数: {len(stations)}")
        
        # 匹配每个站点到link
        matched_stations = []
        previous_link_id = None
        previous_point = None
        
        for i, station_name in enumerate(stations):
            station_lon = station_lat = None
            if station_entries and i < len(station_entries):
                try:
                    station_lon = float(station_entries[i].get('lon'))
                    station_lat = float(station_entries[i].get('lat'))
                except (TypeError, ValueError):
                    station_lon = station_lat = None
            
            link_id, point, original_coord = self.match_station_to_link(
                station_name, previous_link_id, previous_point, lon=station_lon, lat=station_lat
            )
            
            if link_id:
                matched_stations.append({
                    'name': station_name,
                    'link_id': link_id,
                    'point': point,
                    'original_coord': original_coord,
                    'index': i
                })
                previous_link_id = link_id
                previous_point = point
            else:
                print(f"  警告: 无法匹配站点 '{station_name}'")
        
        if len(matched_stations) < 2:
            print(f"  错误: 匹配的站点数不足")
            return None
        
        # 计算路径
        print(f"  正在计算路径...")
        path_links = []
        total_path_coords = []
        path_segments = []  # 存储每个路径段，用于调试
        
        for i in range(len(matched_stations) - 1):
            start_link = matched_stations[i]['link_id']
            end_link = matched_stations[i + 1]['link_id']
            start_station = matched_stations[i]['name']
            end_station = matched_stations[i + 1]['name']
            
            path, path_length = self.network.shortest_path(start_link, end_link)
            # 缓存最短路结果，减少重复计算
            if (start_link, end_link) in self.shortest_path_cache:
                path, path_length = self.shortest_path_cache[(start_link, end_link)]
            else:
                path, path_length = self.network.shortest_path(start_link, end_link)
                self.shortest_path_cache[(start_link, end_link)] = (path, path_length)
            
            if path:
                path_segments.append({
                    'from': start_station,
                    'to': end_station,
                    'path_length': path_length,
                    'link_count': len(path)
                })
                # 添加路径（避免重复添加第一个link）
                if path_links and path_links[-1] == path[0]:
                    path_links.extend(path[1:])
                else:
                    path_links.extend(path)
            else:
                print(f"  警告: 无法找到从站点 {i} ({start_station}) 到站点 {i+1} ({end_station}) 的路径")
                # 如果找不到路径，至少添加起点和终点link（即使不连通）
                if not path_links or path_links[-1] != start_link:
                    path_links.append(start_link)
                if path_links[-1] != end_link:
                    path_links.append(end_link)
        
        # 获取完整路径坐标
        if path_links:
            total_path_coords = self.network.get_link_path_coords(path_links)
        
        # 迭代优化：尝试调整每个站点选择，使整体路径更短
        print(f"  正在优化路径...")
        optimized_stations = self.optimize_route(matched_stations, path_links)
        
        # 重新计算优化后的路径
        optimized_path_links = []
        for i in range(len(optimized_stations) - 1):
            start_link = optimized_stations[i]['link_id']
            end_link = optimized_stations[i + 1]['link_id']
            if (start_link, end_link) in self.shortest_path_cache:
                path, _ = self.shortest_path_cache[(start_link, end_link)]
            else:
                path, _ = self.network.shortest_path(start_link, end_link)
                self.shortest_path_cache[(start_link, end_link)] = (path, _)
            if path:
                if optimized_path_links and optimized_path_links[-1] == path[0]:
                    optimized_path_links.extend(path[1:])
                else:
                    optimized_path_links.extend(path)
        
        if optimized_path_links:
            total_path_coords = self.network.get_link_path_coords(optimized_path_links)
        
        # 确定起点和终点（考虑方向）
        start_station = stations[0]
        end_station = stations[-1]
        
        return {
            'name': route_name,
            'direction': direction,
            'start': start_station,
            'end': end_station,
            'stations': [
                {
                    'name': s['name'],
                    'lon': s['original_coord'][0],
                    'lat': s['original_coord'][1]
                }
                for s in optimized_stations
            ],
            'path_coords': total_path_coords,
            'path_links': optimized_path_links
        }
    
    def optimize_route(self, matched_stations, current_path_links):
        """
        优化路线：针对每个中间站点，尝试切换到对向link（起终点交换的反向link，且落点位置基本一致），
        如果整体路径更短，则使用对向link。
        """
        def min_distance_to_link(lon, lat, link_id):
            coords = self.network.link_coords.get(link_id, [])
            if len(coords) < 2:
                return float('inf')
            best = float('inf')
            for i in range(len(coords) - 1):
                x1, y1 = coords[i]
                x2, y2 = coords[i + 1]
                dist, _, _ = point_to_line_distance(lon, lat, x1, y1, x2, y2)
                best = min(best, dist)
            return best

        optimized = [s.copy() for s in matched_stations]
        current_length = self.calculate_route_length(optimized)

        def path_len(a, b):
            if (a, b) in self.shortest_path_cache:
                _, l = self.shortest_path_cache[(a, b)]
                return l
            _, l = self.network.shortest_path(a, b)
            self.shortest_path_cache[(a, b)] = (_, l)
            return l

        for i in range(0, len(optimized)):  # 不再跳过首末站
            link_id = optimized[i]['link_id']
            orig_coord = optimized[i].get('original_coord')
            if not link_id or not isinstance(orig_coord, tuple):
                continue

            # 先用 paired_id，如果没有再回退节点反向匹配
            opposite_link = self.network.links.get(link_id, {}).get('paired_id')
            if opposite_link and opposite_link not in self.network.links:
                opposite_link = None

            if not opposite_link:
                start_end = self.network.link_to_nodes.get(link_id)
                if not start_end or len(start_end) != 2:
                    continue
                start_node, end_node = start_end

                for cand_id in self.network.node_to_links.get(end_node, []):
                    cand_nodes = self.network.link_to_nodes.get(cand_id)
                    if cand_nodes and cand_nodes[1] == start_node:  # 反向
                        opposite_link = cand_id
                        break

            if not opposite_link or opposite_link == link_id:
                continue

            # 确保映射点在物理位置上接近
            lon, lat = orig_coord
            dist_to_opposite = min_distance_to_link(lon, lat, opposite_link)
            if dist_to_opposite > 50:  # 允许50米内判定为同一位置
                continue

            # 只对相邻两段做增量比较，避免全量重算
            prev_link = optimized[i - 1]['link_id'] if i - 1 >= 0 else None
            next_link = optimized[i + 1]['link_id'] if i + 1 < len(optimized) else None

            old_len = 0.0
            new_len = 0.0
            if prev_link:
                old_len += path_len(prev_link, link_id)
                new_len += path_len(prev_link, opposite_link)
            if next_link:
                old_len += path_len(link_id, next_link)
                new_len += path_len(opposite_link, next_link)

            if new_len < old_len:
                optimized[i]['link_id'] = opposite_link
                # point/original_coord 保持原物理位置
                current_length = current_length - old_len + new_len

        return optimized
    
    def calculate_route_length(self, matched_stations):
        """计算路线的总长度"""
        if len(matched_stations) < 2:
            return 0.0
        
        total_length = 0.0
        for i in range(len(matched_stations) - 1):
            start_link = matched_stations[i]['link_id']
            end_link = matched_stations[i + 1]['link_id']
            if (start_link, end_link) in self.shortest_path_cache:
                _, path_length = self.shortest_path_cache[(start_link, end_link)]
            else:
                _, path_length = self.network.shortest_path(start_link, end_link)
                self.shortest_path_cache[(start_link, end_link)] = (None, path_length)
            total_length += path_length
        
        return total_length

def normalize_route_name(route_name):
    """标准化线路名称"""
    if not route_name:
        return ''
    route_name = str(route_name).strip()
    # 移除"路"字，统一为数字
    route_name = re.sub(r'(\d+)\s*路', r'\1', route_name)
    # 如果只是数字，保持原样
    return route_name

def normalize_station_name(station_name):
    """标准化站点名称，用于匹配"""
    if not station_name:
        return ''
    name = station_name.strip()
    name = re.sub(r'[（(].*?[）)]', '', name)
    name = name.replace(' ', '')
    return name

def read_ic_card_data():
    """读取所有IC卡消费明细CSV文件，统计上车次数和付费用户数（按小时统计）"""
    print("  读取IC卡消费明细...")

    csv_files = sorted(glob.glob(str(IC_DATA_DIR / 'IC卡消费明细查询_*.csv')))
    if not csv_files:
        print("    警告: 未找到IC卡消费明细文件")
        return {}, {}, {}
    
    print(f"    找到 {len(csv_files)} 个CSV文件")
    
    # 统计上车次数：{线路号: {上下行: {站点名称: 次数}}}
    boarding_stats = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    # 统计付费用户数：{线路号: {上下行: {站点名称: 付费用户数}}}
    paid_user_stats = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    # 按小时统计：{线路号: {上下行: {站点名称: {小时: 次数}}}}
    hourly_boarding_stats = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: defaultdict(int))))
    # 按小时统计付费用户：{线路号: {上下行: {站点名称: {小时: 付费用户数}}}}
    hourly_paid_user_stats = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: defaultdict(int))))
    
    total_records = 0
    valid_records = 0
    paid_records = 0
    
    for csv_file in csv_files:
        try:
            with open(csv_file, 'r', encoding='gb18030', errors='ignore') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    total_records += 1
                    
                    route_num = row.get('线路', '').strip()
                    direction = row.get('上下行', '').strip()
                    station_name = row.get('站点名称', '').strip()
                    # 获取消费金额，判断是否为付费用户
                    amount_str = row.get('消费金额', '').strip()
                    # 获取日期和时间
                    date_str = row.get('日期', '').strip()
                    time_str = row.get('时间', '').strip()
                    
                    if not route_num or not station_name:
                        continue
                    
                    # 处理上下行：0或空=上行/去程，1=下行/返程
                    if direction == '' or direction == '0':
                        direction_key = 'forward'
                    elif direction == '1':
                        direction_key = 'reverse'
                    else:
                        direction_key = 'forward' if direction in ['', '0', '上行', '去程'] else 'reverse'

                    # 解析时间，提取小时
                    hour = None
                    if date_str and time_str:
                        try:
                            # 日期格式：YYYYMMDD，时间格式：HHMMSS
                            if len(time_str) >= 6:
                                hour = int(time_str[:2])  # 提取小时
                        except (ValueError, TypeError):
                            pass
                    
                    valid_records += 1
                    boarding_stats[route_num][direction_key][station_name] += 1
                    
                    # 按小时统计
                    if hour is not None:
                        hourly_boarding_stats[route_num][direction_key][station_name][hour] += 1
                    
                    # 判断是否为付费用户（消费金额>0）
                    # 注意：每条记录代表一个用户，所以付费用户数应该直接加1，而不是加消费次数
                    try:
                        amount = float(amount_str) if amount_str else 0.0
                        if amount > 0:
                            paid_user_stats[route_num][direction_key][station_name] += 1
                            paid_records += 1
                            # 按小时统计付费用户
                            if hour is not None:
                                hourly_paid_user_stats[route_num][direction_key][station_name][hour] += 1
                    except (ValueError, TypeError):
                        pass
                    
        except Exception as e:
            print(f"    警告: 处理文件 {csv_file} 时出错: {e}")
            continue
    
    print(f"    总记录数: {total_records}, 有效记录数: {valid_records}, 付费记录数: {paid_records}")
    print(f"    统计到 {len(boarding_stats)} 条线路的上车数据")
    print(f"    统计到 {len(paid_user_stats)} 条线路的付费用户数据")
    
    return boarding_stats, paid_user_stats, hourly_boarding_stats, hourly_paid_user_stats

def match_station_boarding_count(station_name, route_name, direction, boarding_stats, paid_user_stats=None, 
                                  hourly_stats=None, start_hour=None, end_hour=None):
    """
    匹配站点的上车次数或付费用户数
    
    Args:
        station_name: 站点名称
        route_name: 线路名称
        direction: 方向
        boarding_stats: 上车次数统计
        paid_user_stats: 付费用户统计（可选，如果提供则返回付费用户数）
        hourly_stats: 按小时统计的数据（可选）
        start_hour: 开始小时（0-23，可选）
        end_hour: 结束小时（0-23，可选）
    
    Returns:
        上车次数或付费用户数
    """
    # 如果提供了时间段和按小时统计的数据，使用按小时统计
    if hourly_stats is not None and start_hour is not None and end_hour is not None:
        stats_to_use = hourly_stats
        # 计算时间段内的统计
        normalized_route_name = normalize_route_name(route_name)
        normalized_station_name = normalize_station_name(station_name)
        
        for stat_route_num in stats_to_use.keys():
            normalized_stat_route = normalize_route_name(stat_route_num)
            if (normalized_route_name == normalized_stat_route or 
                route_name == stat_route_num or
                str(normalized_route_name) == str(normalized_stat_route)):
                route_stats = stats_to_use[stat_route_num]
                direction_stats = route_stats.get(direction, {})
                
                for stat_station_name, hourly_data in direction_stats.items():
                    normalized_stat = normalize_station_name(stat_station_name)
                    if normalized_stat == normalized_station_name:
                        # 计算时间段内的总数
                        total = 0
                        for hour in range(start_hour, end_hour + 1):
                            total += hourly_data.get(hour, 0)
                        return total
        return 0
    
    # 如果提供了付费用户统计，使用付费用户统计
    stats_to_use = paid_user_stats if paid_user_stats is not None else boarding_stats
    
    normalized_route_name = normalize_route_name(route_name)
    normalized_station_name = normalize_station_name(station_name)
    
    # 调试信息（可选）
    if not stats_to_use:
        return 0
    
    # 查找对应的统计数据
    for stat_route_num in stats_to_use.keys():
        normalized_stat_route = normalize_route_name(stat_route_num)
        # 改进匹配：支持数字匹配（如"21"匹配"21路"）
        if (normalized_route_name == normalized_stat_route or 
            route_name == stat_route_num or
            str(normalized_route_name) == str(normalized_stat_route)):
            route_stats = stats_to_use[stat_route_num]
            direction_stats = route_stats.get(direction, {})
            
           
            for stat_station_name, count in direction_stats.items():
                normalized_stat = normalize_station_name(stat_station_name)
                # 精确匹配
                if normalized_stat == normalized_station_name:
                    return count
            
    
    return 0


def summarize_boarding_stats(boarding_stats):
    """
    汇总IC卡上车次数
    Returns:
        total_count: 全部记录总和
        per_route_dir: {(normalized_route, direction): count}
    """
    total_count = 0
    per_route_dir = {}
    for route, dir_map in boarding_stats.items():
        for direction, stations in dir_map.items():
            cnt = sum(stations.values())
            norm_route = normalize_route_name(route)
            per_route_dir[(norm_route, direction)] = per_route_dir.get((norm_route, direction), 0) + cnt
            total_count += cnt
    return total_count, per_route_dir


def build_routes_from_station_csv(stations_data: List[Dict]) -> List[Dict]:
    """
    根据新的站点CSV（含线路、方向、序号）生成路线信息
    返回结构：[{ '线路名称': xxx, 'direction': 'forward'/'reverse', 'stations_list': [...] }, ...]
    """
    route_groups = defaultdict(list)
    
    for row in stations_data:
        # 路线编号：兼容 line_id / route / 线路
        route_raw = (row.get('line_id') or row.get('route') or row.get('线路') or '').strip()
        if not route_raw:
            continue
        # 去掉多余前导0，避免 "0001" 与 "1" 不一致
        route_name = route_raw.lstrip('0') or route_raw
        
        # 方向：兼容 A/B、正反/上下行、0/1
        direction_raw = (row.get('direction') or row.get('上下行') or '').strip()
        if direction_raw in ['返程', '下行', '1', 'reverse', 'B', 'b']:
            direction_key = 'reverse'
        else:
            direction_key = 'forward'
        
        # 站点序号：兼容 sequence / 序号 / seq / station_id
        seq_str = row.get('sequence') or row.get('序号') or row.get('seq') or row.get('station_id') or ''
        try:
            sequence = int(float(str(seq_str).strip()))
        except ValueError:
            continue
        
        station_name = (
            row.get('station_name') or row.get('station') or
            row.get('站点') or
            row.get('站 点') or
            ''
        ).strip()
        if not station_name:
            continue
        
        try:
            lon = float(str(row.get('station_lon') or row.get('lon') or row.get('经度') or '').strip())
            lat = float(str(row.get('station_lat') or row.get('lat') or row.get('纬度') or '').strip())
        except ValueError:
            continue
        
        route_groups[(route_name, direction_key)].append({
            'name': station_name,
            'lon': lon,
            'lat': lat,
            'sequence': sequence
        })
    
    routes = []
    for (route_name, direction_key), stations in route_groups.items():
        stations_sorted = sorted(stations, key=lambda x: x['sequence'])
        routes.append({
            '线路名称': str(route_name),
            'direction': direction_key,
            'stations_list': [
                {'name': s['name'], 'lon': s['lon'], 'lat': s['lat']}
                for s in stations_sorted
            ]
        })
    
    return routes

def main():
    print("=" * 60)
    print("公交路线还原工具")
    print("=" * 60)

    # 读取数据
    print("\n1. 读取数据文件...")
    # 站点文件从 MID_output 读取
    station_file = OUTPUT_DIR / 'huangshan.csv'
    print(f"  读取新的站点与线路文件 {station_file} ...")
    stations_data = read_csv_gb2312(str(station_file))
    print(f"  读取到 {len(stations_data)} 条站点记录")

    # 由站点文件直接生成线路与顺序
    route_infos = build_routes_from_station_csv(stations_data)
    print(f"  解析得到 {len(route_infos)} 条线路方向记录（route × direction）")

    print("  读取路网数据...")
    link_file = OUTPUT_DIR / 'link_huangshan_gcj02.json'
    if not link_file.exists():
        link_file = OUTPUT_DIR / 'link_huangshan.json'
    with open(str(link_file), 'r', encoding='utf-8') as f:
        links_data = json.load(f)
    print(f"  读取到 {len(links_data)} 条link记录")
    
    # 读取IC卡消费明细（同时获取上车次数和付费用户数，以及按小时统计）
    boarding_stats, paid_user_stats, hourly_boarding_stats, hourly_paid_user_stats = read_ic_card_data()
    total_boarding_all, total_boarding_per_route_dir = summarize_boarding_stats(boarding_stats)
    total_paid_users_all, total_paid_users_per_route_dir = summarize_boarding_stats(paid_user_stats)
    # 按线路方向、站点的剩余可匹配次数（避免多次累计导致>100%）
    remaining_boarding = defaultdict(lambda: defaultdict(int))
    for route_raw, dir_map in boarding_stats.items():
        norm_route = normalize_route_name(route_raw)
        for direction, stations in dir_map.items():
            for stat_station_name, cnt in stations.items():
                norm_station = normalize_station_name(stat_station_name)
                remaining_boarding[(norm_route, direction)][norm_station] += cnt
    
    # 构建路网
    print("\n2. 构建路网...")
    network = RoadNetwork(links_data)
    
    # 创建还原器
    print("\n3. 创建路线还原器...")
    reconstructor = BusRouteReconstructor(network, stations_data, [])
    
    # 还原所有路线（包括正向和反向）
    print("\n4. 还原公交路线...")
    reconstructed_routes = []
    matched_boarding_total = 0
    matched_boarding_per_route_dir = defaultdict(int)
    matched_boarding_per_route_dir_capped = defaultdict(int)  # 用于匹配率计算（封顶）
    
    for route_info in route_infos:
        direction = route_info.get('direction', 'forward')
        route = reconstructor.reconstruct_route(route_info, direction=direction)
        if route:
            # 添加上车次数和付费用户数统计（包括按小时统计）
            total_count = 0
            total_paid_count = 0
            for station in route['stations']:
                # 上车次数
                count = match_station_boarding_count(
                    station['name'], route['name'], direction, boarding_stats
                )
                station['boarding_count'] = count
                total_count += count
                # 付费用户数
                paid_count = match_station_boarding_count(
                    station['name'], route['name'], direction, boarding_stats, paid_user_stats
                )
                station['paid_user_count'] = paid_count
                total_paid_count += paid_count
                
                # 按小时统计上车次数
                station['hourly_boarding'] = {}
                # 按小时统计付费用户数
                station['hourly_paid_users'] = {}
                
                # 从hourly统计数据中提取该站点的数据
                normalized_route_name = normalize_route_name(route['name'])
                normalized_station_name = normalize_station_name(station['name'])
                
                for stat_route_num in hourly_boarding_stats.keys():
                    normalized_stat_route = normalize_route_name(stat_route_num)
                    if (normalized_route_name == normalized_stat_route or 
                        route['name'] == stat_route_num or
                        str(normalized_route_name) == str(normalized_stat_route)):
                        route_stats = hourly_boarding_stats[stat_route_num]
                        direction_stats = route_stats.get(direction, {})
                        for stat_station_name, hourly_data in direction_stats.items():
                            normalized_stat = normalize_station_name(stat_station_name)
                            if normalized_stat == normalized_station_name:
                                station['hourly_boarding'] = dict(hourly_data)
                                break
                
                for stat_route_num in hourly_paid_user_stats.keys():
                    normalized_stat_route = normalize_route_name(stat_route_num)
                    if (normalized_route_name == normalized_stat_route or 
                        route['name'] == stat_route_num or
                        str(normalized_route_name) == str(normalized_stat_route)):
                        route_stats = hourly_paid_user_stats[stat_route_num]
                        direction_stats = route_stats.get(direction, {})
                        for stat_station_name, hourly_data in direction_stats.items():
                            normalized_stat = normalize_station_name(stat_station_name)
                            if normalized_stat == normalized_station_name:
                                station['hourly_paid_users'] = dict(hourly_data)
                                break
                
                # 原始累计（展示用）
                matched_boarding_total += count
                matched_boarding_per_route_dir[(route['name'], direction)] += count
                # 封顶累计（用于匹配率，避免重复站点导致>100%）
                norm_route = normalize_route_name(route['name'])
                norm_station = normalize_station_name(station['name'])
                remain = remaining_boarding[(norm_route, direction)].get(norm_station, 0)
                used = min(count, remain)
                if used > 0:
                    remaining_boarding[(norm_route, direction)][norm_station] -= used
                matched_boarding_per_route_dir_capped[(norm_route, direction)] += used
            # 为前端展示保存线路总上车次数和付费用户数
            route['total_boarding'] = total_count
            route['total_paid_users'] = total_paid_count
            reconstructed_routes.append(route)
            dir_label = "正向" if direction == 'forward' else "反向"
            print(f"  [OK] {route['name']} ({dir_label}): {len(route['stations'])} 个站点, 总上车次数: {total_count}, 付费用户数: {total_paid_count}")
    
    print(f"\n成功还原 {len(reconstructed_routes)} 条路线（按方向展开）")
    
    # 输出上车记录匹配统计
    if total_boarding_all > 0:
        match_ratio = matched_boarding_total / total_boarding_all
        print(f"上车记录匹配汇总: {matched_boarding_total}/{total_boarding_all} ({match_ratio:.1%})")
        # 分线路方向匹配率
        print("各线路方向匹配率：")
        for (norm_rname, d), matched_cnt in sorted(matched_boarding_per_route_dir_capped.items(), key=lambda x: (x[0][0], x[0][1])):
            total_cnt = total_boarding_per_route_dir.get((norm_rname, d), 0)
            dir_label = "正向" if d == 'forward' else "反向"
            if total_cnt > 0:
                ratio = matched_cnt / total_cnt
                print(f"  {norm_rname} {dir_label}: {matched_cnt}/{total_cnt} ({ratio:.1%})")
            else:
                print(f"  {norm_rname} {dir_label}: {matched_cnt}/0 (无IC数据)")
    else:
        print("上车记录匹配汇总: 无IC卡数据")
    
    # 统计每个线路方向中上车次数最多的站点及其占比
    print("\n各线路方向中上车次数最多的站点统计：")
    print("=" * 80)
    for route_num, dir_map in sorted(boarding_stats.items()):
        for direction, stations in dir_map.items():
            if not stations:
                continue
            
            # 计算总上车次数
            total_count = sum(stations.values())
            if total_count == 0:
                continue
            
            # 找到上车次数最多的站点
            max_station_name = max(stations.items(), key=lambda x: x[1])[0]
            max_station_count = stations[max_station_name]
            percentage = (max_station_count / total_count) * 100
            
            # 标准化线路名称和方向标签
            norm_route = normalize_route_name(route_num)
            dir_label = "正向" if direction == 'forward' else "反向"
            
            print(f"{norm_route} {dir_label}:")
            print(f"  最多站点: {max_station_name}")
            print(f"  上车次数: {max_station_count:,} / 总次数: {total_count:,}")
            print(f"  占比: {percentage:.2f}%")
            print()
    print("=" * 80)
    
    # 计算最大上车次数和最大付费用户数（用于热力图）
    max_boarding_count = 0
    max_paid_user_count = 0
    for route in reconstructed_routes:
        for station in route['stations']:
            max_boarding_count = max(max_boarding_count, station.get('boarding_count', 0))
            max_paid_user_count = max(max_paid_user_count, station.get('paid_user_count', 0))
    
    print(f"最大上车次数: {max_boarding_count}")
    print(f"最大付费用户数: {max_paid_user_count}")
    
    # 生成HTML
    print("\n5. 生成HTML可视化文件...")
    generate_html(reconstructed_routes, max_boarding_count, max_paid_user_count, 
                  hourly_boarding_stats, hourly_paid_user_stats)
    
    print("\n" + "=" * 60)
    print("处理完成！")
    print("=" * 60)
    output_file = OUT_DIR / 'bus_routes_reconstructed.html'
    print(f"\n请在浏览器中打开 {output_file} 查看可视化结果")

def generate_html(routes, max_boarding_count=0, max_paid_user_count=0, 
                  hourly_boarding_stats=None, hourly_paid_user_stats=None):
    """生成HTML可视化文件"""
    
    # 生成颜色列表
    # 路线配色：高对比度，用于线路/箭头
    route_colors = [
        '#e53935',  # 红
        '#1e88e5',  # 蓝
        '#43a047',  # 绿
        '#8e24aa',  # 紫
        '#00acc1',  # 青
        '#fb8c00',  # 橙
        '#d81b60',  # 玫红
        '#3949ab',  # 靛蓝
        '#00897b',  # 蓝绿
        '#c62828',  # 深红
        '#5d4037'   # 深棕
    ]
    # 热力图配色：灰→绿→黄→橙→红→深红
    heatmap_colors = [
        '#b0bec5',  # 灰
        '#43a047',  # 绿
        '#fbc02d',  # 黄
        '#fb8c00',  # 橙
        '#e53935',  # 红
        '#b71c1c'   # 深红
    ]
    
    # 计算地图中心
    all_lons = []
    all_lats = []
    for route in routes:
        for station in route['stations']:
            all_lons.append(station['lon'])
            all_lats.append(station['lat'])
    
    center_lon = sum(all_lons) / len(all_lons) if all_lons else 118.3
    center_lat = sum(all_lats) / len(all_lats) if all_lats else 29.72
    
    # 生成HTML
    html_content = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>黄山市公交路线还原可视化</title>
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
    <script src="https://unpkg.com/leaflet-polylinedecorator@1.7.0/dist/leaflet.polylineDecorator.js"></script>
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
        .route-item {{
            padding: 10px;
            margin: 5px 0;
            border: 1px solid #ddd;
            border-radius: 4px;
            cursor: pointer;
            transition: background 0.3s;
        }}
        .route-item:hover {{
            background: #f0f0f0;
        }}
        .route-item.active {{
            background: #e8f4f8;
            border-color: #3498db;
        }}
        .route-name {{
            font-weight: bold;
            font-size: 16px;
            margin-bottom: 5px;
        }}
        .route-info {{
            font-size: 12px;
            color: #666;
            margin: 2px 0;
        }}
        .stats {{
            background: #f8f9fa;
            padding: 10px;
            border-radius: 4px;
            margin-bottom: 15px;
            font-size: 14px;
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
            font-weight: 500;
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
        .heatmap-legend {{
            font-family: 'Microsoft YaHei', Arial, sans-serif;
        }}
        .time-range-container {{
            background: #f8f9fa;
            padding: 15px;
            border-radius: 4px;
            margin-bottom: 15px;
        }}
        .time-range-container h4 {{
            margin: 0 0 10px 0;
            color: #2c3e50;
            font-size: 14px;
        }}
        .time-range-slider {{
            position: relative;
            width: 100%;
            height: 30px;
            margin: 10px 0;
        }}
        .time-range-track {{
            position: absolute;
            width: 100%;
            height: 6px;
            background: #ddd;
            border-radius: 3px;
            top: 12px;
        }}
        .time-range-fill {{
            position: absolute;
            height: 6px;
            background: #3498db;
            border-radius: 3px;
            top: 12px;
            pointer-events: none;
        }}
        .time-range-handle {{
            position: absolute;
            width: 20px;
            height: 20px;
            background: #3498db;
            border: 2px solid white;
            border-radius: 50%;
            cursor: pointer;
            top: 5px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.2);
            z-index: 10;
        }}
        .time-range-handle:hover {{
            background: #2980b9;
        }}
        .time-range-labels {{
            display: flex;
            justify-content: space-between;
            margin-top: 5px;
            font-size: 12px;
            color: #666;
        }}
        .time-display {{
            text-align: center;
            margin-top: 8px;
            font-size: 13px;
            font-weight: bold;
            color: #2c3e50;
        }}
    </style>
</head>
<body>
    <div id="map"></div>
    <div class="info-panel">
        <h3>公交路线</h3>
        <div class="stats">
            <div>总路线数: {len(routes)}</div>
        </div>
        <div class="control-buttons">
            <button class="btn" id="show-all-btn">显示所有线路</button>
            <button class="btn btn-secondary" id="hide-all-btn">关闭所有线路</button>
            <button class="btn" id="toggle-heatmap-btn" style="background: #27ae60; margin-top: 10px;">切换为付费用户热力图</button>
        </div>
        <div class="time-range-container">
            <h4>时间段筛选</h4>
            <div class="time-range-slider" id="time-range-slider">
                <div class="time-range-track"></div>
                <div class="time-range-fill" id="time-range-fill"></div>
                <div class="time-range-handle" id="time-handle-start"></div>
                <div class="time-range-handle" id="time-handle-end"></div>
            </div>
            <div class="time-range-labels">
                <span>0时</span>
                <span>6时</span>
                <span>12时</span>
                <span>18时</span>
                <span>23时</span>
            </div>
            <div class="time-display" id="time-display">全天 (0:00 - 23:59)</div>
        </div>
        <div id="route-list"></div>
    </div>

    <script>
        // 初始化地图
        var map = L.map('map').setView([{center_lat}, {center_lon}], 13);
        // 自定义图层Pane，保证线路层级高于站点圆饼
        map.createPane('routesPane');
        map.getPane('routesPane').style.zIndex = 600;
        map.createPane('stationsPane');
        // 让站点标记层级高于线路，保证可见
        map.getPane('stationsPane').style.zIndex = 650;
        
        // 使用高德底图（GCJ02）
        L.tileLayer('https://webrd0{{s}}.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=7&x={{x}}&y={{y}}&z={{z}}', {{
            subdomains: ['1','2','3','4'],
            maxZoom: 18,
            tileSize: 256,
            zoomOffset: 0,
            attribution: '© 高德地图'
        }}).addTo(map);
        
        // 路线数据
        var routesData = {json.dumps(routes, ensure_ascii=False, indent=2)};
        
        // 颜色列表（线路）与热力图颜色
        var routeColors = {json.dumps(route_colors)};
        var heatmapColors = {json.dumps(heatmap_colors)};
        
        // 最大上车次数和最大付费用户数（用于热力图）
        var maxBoardingCount = {max_boarding_count};
        var maxPaidUserCount = {max_paid_user_count};
        
        // 当前热力图模式：'boarding' 上车次数 或 'paid' 付费用户
        var currentHeatmapMode = 'boarding';
        
        // 时间条相关变量
        var timeStartHour = 0;
        var timeEndHour = 23;
        var isDragging = false;
        var dragHandle = null;
        
        // 初始化时间条
        function initTimeRange() {{
            var slider = document.getElementById('time-range-slider');
            var handleStart = document.getElementById('time-handle-start');
            var handleEnd = document.getElementById('time-handle-end');
            var fill = document.getElementById('time-range-fill');
            var display = document.getElementById('time-display');
            var sliderWidth = slider.offsetWidth;
            
            function updateTimeRange() {{
                var startPercent = (timeStartHour / 23) * 100;
                var endPercent = (timeEndHour / 23) * 100;
                
                handleStart.style.left = startPercent + '%';
                handleEnd.style.left = endPercent + '%';
                fill.style.left = startPercent + '%';
                fill.style.width = (endPercent - startPercent) + '%';
                
                var startTime = timeStartHour.toString().padStart(2, '0') + ':00';
                var endTime = timeEndHour.toString().padStart(2, '0') + ':59';
                if (timeStartHour === 0 && timeEndHour === 23) {{
                    display.textContent = '全天 (0:00 - 23:59)';
                }} else {{
                    display.textContent = startTime + ' - ' + endTime;
                }}
                
                // 更新热力图
                updateHeatmapDisplay();
            }}
            
            function hourFromPosition(x) {{
                var rect = slider.getBoundingClientRect();
                var percent = Math.max(0, Math.min(100, ((x - rect.left) / rect.width) * 100));
                return Math.round((percent / 100) * 23);
            }}
            
            function onMouseDown(e, handle) {{
                e.preventDefault();
                isDragging = true;
                dragHandle = handle;
                document.addEventListener('mousemove', onMouseMove);
                document.addEventListener('mouseup', onMouseUp);
            }}
            
            function onMouseMove(e) {{
                if (!isDragging) return;
                var hour = hourFromPosition(e.clientX);
                
                if (dragHandle === handleStart) {{
                    timeStartHour = Math.min(hour, timeEndHour);
                }} else {{
                    timeEndHour = Math.max(hour, timeStartHour);
                }}
                
                updateTimeRange();
            }}
            
            function onMouseUp() {{
                isDragging = false;
                dragHandle = null;
                document.removeEventListener('mousemove', onMouseMove);
                document.removeEventListener('mouseup', onMouseUp);
            }}
            
            handleStart.addEventListener('mousedown', function(e) {{ onMouseDown(e, handleStart); }});
            handleEnd.addEventListener('mousedown', function(e) {{ onMouseDown(e, handleEnd); }});
            
            // 点击滑块轨道也可以移动
            slider.addEventListener('click', function(e) {{
                if (e.target === slider || e.target.classList.contains('time-range-track') || e.target.classList.contains('time-range-fill')) {{
                    var hour = hourFromPosition(e.clientX);
                    var startDist = Math.abs(hour - timeStartHour);
                    var endDist = Math.abs(hour - timeEndHour);
                    
                    if (startDist < endDist) {{
                        timeStartHour = Math.min(hour, timeEndHour);
                    }} else {{
                        timeEndHour = Math.max(hour, timeStartHour);
                    }}
                    updateTimeRange();
                }}
            }});
            
            updateTimeRange();
        }}
        
        // 坐标转换 WGS84 -> GCJ02（用于高德底图）
        function outOfChina(lon, lat) {{
            return (lon < 72.004 || lon > 137.8347 || lat < 0.8293 || lat > 55.8271);
        }}
        function transformLat(x, y) {{
            var ret = -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * Math.sqrt(Math.abs(x));
            ret += (20.0 * Math.sin(6.0 * x * Math.PI) + 20.0 * Math.sin(2.0 * x * Math.PI)) * 2.0 / 3.0;
            ret += (20.0 * Math.sin(y * Math.PI) + 40.0 * Math.sin(y / 3.0 * Math.PI)) * 2.0 / 3.0;
            ret += (160.0 * Math.sin(y / 12.0 * Math.PI) + 320 * Math.sin(y * Math.PI / 30.0)) * 2.0 / 3.0;
            return ret;
        }}
        function transformLon(x, y) {{
            var ret = 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * Math.sqrt(Math.abs(x));
            ret += (20.0 * Math.sin(6.0 * x * Math.PI) + 20.0 * Math.sin(2.0 * x * Math.PI)) * 2.0 / 3.0;
            ret += (20.0 * Math.sin(x * Math.PI) + 40.0 * Math.sin(x / 3.0 * Math.PI)) * 2.0 / 3.0;
            ret += (150.0 * Math.sin(x / 12.0 * Math.PI) + 300.0 * Math.sin(x / 30.0 * Math.PI)) * 2.0 / 3.0;
            return ret;
        }}
        function wgs2gcj(lon, lat) {{
            if (outOfChina(lon, lat)) return [lon, lat];
            var a = 6378245.0;
            var ee = 0.00669342162296594323;
            var dLat = transformLat(lon - 105.0, lat - 35.0);
            var dLon = transformLon(lon - 105.0, lat - 35.0);
            var radLat = lat / 180.0 * Math.PI;
            var magic = Math.sin(radLat);
            magic = 1 - ee * magic * magic;
            var sqrtMagic = Math.sqrt(magic);
            dLat = (dLat * 180.0) / ((a * (1 - ee)) / (magic * sqrtMagic) * Math.PI);
            dLon = (dLon * 180.0) / (a / sqrtMagic * Math.cos(radLat) * Math.PI);
            var mgLat = lat + dLat;
            var mgLon = lon + dLon;
            return [mgLon, mgLat];
        }}
        // 将路线与站点坐标转换为 GCJ02
        routesData.forEach(function(r) {{
            if (r.path_coords && r.path_coords.length > 0) {{
                r.path_coords = r.path_coords.map(function(c) {{ return wgs2gcj(c[0], c[1]); }});
            }}
            if (r.stations && r.stations.length > 0) {{
                r.stations = r.stations.map(function(s) {{
                    var pt = wgs2gcj(s.lon, s.lat);
                    return Object.assign({{}}, s, {{lon: pt[0], lat: pt[1]}});
                }});
            }}
        }});
        
        // 获取当前模式下的数值和最大值（考虑时间段）
        function getCurrentValue(station) {{
            // 如果选择了时间段，使用按小时统计的数据
            if (timeStartHour !== 0 || timeEndHour !== 23) {{
                var hourlyData = null;
                if (currentHeatmapMode === 'paid') {{
                    hourlyData = station.hourly_paid_users || {{}};
                }} else {{
                    hourlyData = station.hourly_boarding || {{}};
                }}
                
                if (hourlyData && Object.keys(hourlyData).length > 0) {{
                    var total = 0;
                    for (var hour = timeStartHour; hour <= timeEndHour; hour++) {{
                        total += (hourlyData[hour] || 0);
                    }}
                    return total;
                }}
            }}
            
            // 否则使用全天的统计数据
            if (currentHeatmapMode === 'paid') {{
                return station.paid_user_count || 0;
            }} else {{
                return station.boarding_count || 0;
            }}
        }}
        function getCurrentMaxCount() {{
            return currentHeatmapMode === 'paid' ? maxPaidUserCount : maxBoardingCount;
        }}
        
        // 对数分层：每个颜色对应一个数量级；0-99 统一为灰，100-999 对应第一档，以此类推
        function getLogBinIndex(count) {{
            if (count <= 0) return -1; // 特殊: 无数据
            if (count < 100) return 0; // 0-99 归灰
            var idx = Math.floor(Math.log10(count)); // 100-999 ->2, 1000-9999->3
            return idx - 1; // 前移一档：100-999 ->1，1000-9999->2...
        }}
        function getHeatmapColor(count, maxCount) {{
            if (maxCount === 0 || count <= 0) return '#b0bec5';
            var binIdx = getLogBinIndex(count);
            if (binIdx <= 0) return '#b0bec5'; // 0-99
            // binIdx 已经是正确的索引：1->绿色, 2->黄色, 3->橙色...
            return heatmapColors[binIdx % heatmapColors.length];
        }}
        
        // 根据数值计算标记大小（保留平滑变化，避免过度跳跃）
        function getMarkerRadius(count, maxCount) {{
            if (maxCount === 0) return 8;
            var baseRadius = 8;
            var maxRadius = 25;
            var ratio = count / maxCount;
            return baseRadius + (maxRadius - baseRadius) * Math.sqrt(ratio);
        }}
        
        // 更新所有标记的热力图显示
        function updateHeatmapDisplay() {{
            // 先计算当前时间段的最大值
            var tempMax = 0;
            Object.keys(routeLayers).forEach(function(routeKey) {{
                var layer = routeLayers[routeKey];
                layer.markers.forEach(function(marker) {{
                    var station = marker._stationData;
                    if (station) {{
                        var count = getCurrentValue(station);
                        tempMax = Math.max(tempMax, count);
                    }}
                }});
            }});
            
            var currentMax = tempMax > 0 ? tempMax : getCurrentMaxCount();
            var modeLabel = currentHeatmapMode === 'paid' ? '付费用户数' : '上车次数';
            
            // 更新所有标记
            Object.keys(routeLayers).forEach(function(routeKey) {{
                var layer = routeLayers[routeKey];
                layer.markers.forEach(function(marker) {{
                    var station = marker._stationData; // 存储站点数据
                    if (station) {{
                        var count = getCurrentValue(station);
                        var heatmapColor = getHeatmapColor(count, currentMax);
                        var radius = getMarkerRadius(count, currentMax);
                        
                        marker.setStyle({{
                            radius: radius,
                            fillColor: heatmapColor
                        }});
                        
                        // 更新popup（显示时间段内的数据）
                        var popupContent = '<b>' + station.name + '</b><br>';
                        popupContent += '线路: ' + station.routeName + '<br>';
                        popupContent += '方向: ' + (station.isReverse ? '返程' : '去程') + '<br>';
                        popupContent += '序号: ' + (station.index + 1) + '<br>';
                        if (timeStartHour !== 0 || timeEndHour !== 23) {{
                            popupContent += '<span style="color: #666; font-size: 11px;">时间段: ' + 
                                          timeStartHour.toString().padStart(2, '0') + ':00 - ' + 
                                          timeEndHour.toString().padStart(2, '0') + ':59</span><br>';
                        }}
                        popupContent += '<span style="color: ' + heatmapColor + '; font-weight: bold;">';
                        popupContent += '上车次数: ' + count.toLocaleString();
                        if (timeStartHour !== 0 || timeEndHour !== 23) {{
                            popupContent += ' (全天: ' + (station.boarding_count || 0).toLocaleString() + ')';
                        }}
                        popupContent += '</span><br>';
                        var paidCount = currentHeatmapMode === 'paid' ? count : (station.paid_user_count || 0);
                        popupContent += '<span style="color: ' + heatmapColor + '; font-weight: bold;">';
                        popupContent += '付费用户数: ' + paidCount.toLocaleString();
                        if (timeStartHour !== 0 || timeEndHour !== 23 && currentHeatmapMode === 'paid') {{
                            popupContent += ' (全天: ' + (station.paid_user_count || 0).toLocaleString() + ')';
                        }}
                        popupContent += '</span>';
                        marker.setPopupContent(popupContent);
                        
                        // 更新tooltip
                        var tooltipText = '[' + station.routeName + (station.isReverse ? '返程' : '去程') + '] ' + station.name;
                        if (count > 0) {{
                            tooltipText += ' (' + count.toLocaleString() + modeLabel + ')';
                        }}
                        marker.setTooltipContent(tooltipText);
                    }}
                }});
            }});
            
            // 更新图例
            updateLegend(currentMax, modeLabel);
        }}
        
        // 图例显示对数分层（与 getLogBinIndex 和 getHeatmapColor 逻辑一致）
        function buildLogLevels(maxCount) {{
            var levels = [];
            if (maxCount === 0) return levels;
            
            // 按照实际的颜色分级逻辑生成图例
            // 0-99: 灰色 (heatmapColors[0])
            // 100-999: 绿色 (heatmapColors[1])
            // 1000-9999: 黄色 (heatmapColors[2])
            // 10000-99999: 橙色 (heatmapColors[3])
            // 100000-999999: 红色 (heatmapColors[4])
            // 1000000+: 深红色 (heatmapColors[5])
            
            // 添加 0-99 灰色档
            levels.push({{
                start: 0,
                end: 99,
                label: '0 - 99',
                color: heatmapColors[0]
            }});
            
            // 生成 100 以上的对数分层
            var thresholds = [1000, 10000, 100000, 1000000];
            var start = 100;
            for (var i = 0; i < thresholds.length; i++) {{
                var end = thresholds[i];
                if (start > maxCount) break;
                var actualEnd = Math.min(end - 1, maxCount);
                levels.push({{
                    start: start,
                    end: actualEnd,
                    label: start.toLocaleString() + ' - ' + actualEnd.toLocaleString(),
                    color: heatmapColors[(i + 1) % heatmapColors.length]
                }});
                start = end;
            }}
            
            // 如果还有剩余，添加最后一档
            if (start <= maxCount) {{
                var lastThreshold = thresholds[thresholds.length - 1];
                var lastColorIdx = thresholds.length % heatmapColors.length;
                levels.push({{
                    start: start,
                    end: maxCount,
                    label: '≥ ' + start.toLocaleString(),
                    color: heatmapColors[lastColorIdx]
                }});
            }}
            
            return levels;
        }}
        
        // 更新图例
        function updateLegend(maxCount, modeLabel) {{
            var legendDiv = document.querySelector('.heatmap-legend');
            if (!legendDiv) return;
            
            var logLevels = buildLogLevels(maxCount);
            if (logLevels.length === 0) {{
                logLevels = [{{start:0, end:0, label:'无数据', color:'#95a5a6'}}];
            }}
            
            var newContent = '<h4 style="margin: 0 0 10px 0; color: #2c3e50;">' + modeLabel + '热力图</h4>';
            logLevels.forEach(function(level) {{
                var size = getMarkerRadius(level.end || 0, maxCount);
                newContent += '<div style="margin: 8px 0; display: flex; align-items: center;">';
                newContent += '<span style="display: inline-block; width: ' + (size * 2) + 'px; height: ' + (size * 2) + 'px; background: ' + 
                            level.color + '; border-radius: 50%; margin-right: 10px; border: 2px solid #fff; box-shadow: 0 0 3px rgba(0,0,0,0.3);"></span>';
                newContent += '<span>' + level.label + '</span>';
                newContent += '</div>';
            }});
            
            if (maxCount > 0) {{
                newContent += '<div style="margin-top: 12px; padding-top: 12px; border-top: 1px solid #eee; font-size: 12px; color: #666;">最大次数: ' + 
                            maxCount.toLocaleString() + '</div>';
            }}
            
            legendDiv.innerHTML = newContent;
        }}
        
        // 存储图层
        var routeLayers = {{}};
        
        // 为每条路线分配颜色（按线路名称，不区分方向）
        var routeColorMap = {{}};
        var routeNames = [...new Set(routesData.map(function(r) {{ return r.name; }}))];
        routeNames.forEach(function(routeName, index) {{
            routeColorMap[routeName] = routeColors[index % routeColors.length];
        }});
        
        // 绘制路线
        routesData.forEach(function(route) {{
            var color = routeColorMap[route.name];
            var isReverse = route.direction === 'reverse';
            
            // 绘制路径
            var pathCoords = [];
            if (route.path_coords && route.path_coords.length > 0) {{
                pathCoords = route.path_coords.map(function(coord) {{
                    return [coord[1], coord[0]];  // [lat, lon]
                }});
            }} else {{
                // 回退到站点坐标
                pathCoords = route.stations.map(function(s) {{
                    return [s.lat, s.lon];
                }});
            }}
            
            // 根据方向调整样式
            var lineStyle = {{
                color: color,
                weight: isReverse ? 4 : 6,
                opacity: isReverse ? 0.75 : 0.9,
                smoothFactor: 1,
                pane: 'routesPane'
            }};
            
            // 反向线路使用虚线
            if (isReverse) {{
                lineStyle.dashArray = '10, 5';
            }}
            
            var polyline = L.polyline(pathCoords, lineStyle).addTo(map);
            
            // 方向箭头（导航效果）
            var decorator = null;
            if (L.polylineDecorator) {{
                decorator = L.polylineDecorator(polyline, {{
                    patterns: [
                        {{
                            offset: '8%',
                            repeat: '18%',
                            symbol: L.Symbol.arrowHead({{
                                pixelSize: isReverse ? 9 : 12,
                                headAngle: 38,
                                polygon: false,
                                pathOptions: {{
                                    color: color,
                                    weight: isReverse ? 3 : 3.5,
                                    opacity: isReverse ? 0.7 : 1.0,
                                    pane: 'routesPane'
                                }}
                            }})
                        }}
                    ]
                }}, {{pane: 'routesPane'}}).addTo(map);
            }}
            
            // 创建站点标记（带热力图）
            var markers = [];
            route.stations.forEach(function(station, idx) {{
                // 存储站点数据到标记对象中
                var stationData = {{
                    name: station.name,
                    routeName: route.name,
                    isReverse: isReverse,
                    index: idx,
                    boarding_count: station.boarding_count || 0,
                    paid_user_count: station.paid_user_count || 0
                }};
                
                var count = getCurrentValue(stationData);
                var currentMax = getCurrentMaxCount();
                var heatmapColor = getHeatmapColor(count, currentMax);
                var radius = getMarkerRadius(count, currentMax);
                
                var marker = L.circleMarker([station.lat, station.lon], {{
                    radius: radius,
                    fillColor: heatmapColor,
                    color: '#fff',  // 移除白色描边
                    weight: 2,
                    opacity: 1,
                    fillOpacity: 0.9,
                    pane: 'stationsPane'
                }});
                
                // 存储站点数据到标记对象
                marker._stationData = stationData;
                
                // Popup显示详细信息
                var popupContent = '<b>' + station.name + '</b><br>';
                popupContent += '线路: ' + route.name + '<br>';
                popupContent += '方向: ' + (isReverse ? '返程' : '去程') + '<br>';
                popupContent += '序号: ' + (idx + 1) + '<br>';
                popupContent += '<span style="color: ' + heatmapColor + '; font-weight: bold;">';
                popupContent += '上车次数: ' + stationData.boarding_count.toLocaleString() + '</span><br>';
                popupContent += '<span style="color: ' + heatmapColor + '; font-weight: bold;">';
                popupContent += '付费用户数: ' + stationData.paid_user_count.toLocaleString() + '</span>';
                marker.bindPopup(popupContent);
                
                // Tooltip显示线路、方向和当前模式的数值
                var modeLabel = currentHeatmapMode === 'paid' ? '付费用户' : '次';
                var tooltipText = '[' + route.name + (isReverse ? '返程' : '去程') + '] ' + station.name;
                if (count > 0) {{
                    tooltipText += ' (' + count.toLocaleString() + modeLabel + ')';
                }}
                marker.bindTooltip(tooltipText, {{
                    permanent: false,
                    direction: 'top',
                    offset: [0, -10]
                }});
                marker.addTo(map);
                markers.push(marker);
            }});
            
            // 存储图层（使用唯一键区分方向）
            var routeKey = route.name + '_' + route.direction;
            routeLayers[routeKey] = {{
                polyline: polyline,
                decorator: decorator,
                markers: markers,
                visible: true,
                routeName: route.name,
                direction: route.direction
            }};
            
            // 创建路线列表项
            var routeItem = document.createElement('div');
            routeItem.className = 'route-item active';
            var directionLabel = isReverse ? ' (返程)' : ' (去程)';
            var routeDisplayName = route.name + directionLabel;
            var routeTotalBoarding = 0;
            route.stations.forEach(function(s) {{ routeTotalBoarding += (s.boarding_count || 0); }});
            
            routeItem.innerHTML = 
                '<div class="route-name" style="color: ' + color + ';">' + routeDisplayName + 
                '<div class="route-info">' + route.start + ' → ' + route.end + '</div>' +
                '<div class="route-info">站点数: ' + route.stations.length + '</div>' +
                '<div class="route-info">总上车次数: ' + routeTotalBoarding.toLocaleString()  + '  站均上车次数：' + (routeTotalBoarding / route.stations.length).toFixed(1) + '</div>';
            
            // 点击切换显示/隐藏
            routeItem.addEventListener('click', function() {{
                var layer = routeLayers[routeKey];
                if (layer.visible) {{
                    map.removeLayer(layer.polyline);
                    if (layer.decorator) map.removeLayer(layer.decorator);
                    layer.markers.forEach(function(m) {{ map.removeLayer(m); }});
                    layer.visible = false;
                    routeItem.classList.remove('active');
                }} else {{
                    map.addLayer(layer.polyline);
                    if (layer.decorator) map.addLayer(layer.decorator);
                    layer.markers.forEach(function(m) {{ map.addLayer(m); }});
                    layer.visible = true;
                    routeItem.classList.add('active');
                }}
            }});
            
            document.getElementById('route-list').appendChild(routeItem);
        }});
        
        // 显示所有线路
        document.getElementById('show-all-btn').addEventListener('click', function() {{
            Object.keys(routeLayers).forEach(function(routeKey) {{
                var layer = routeLayers[routeKey];
                if (!layer.visible) {{
                    map.addLayer(layer.polyline);
                    if (layer.decorator) map.addLayer(layer.decorator);
                    layer.markers.forEach(function(m) {{ map.addLayer(m); }});
                    layer.visible = true;
                }}
            }});
            // 更新所有列表项状态
            document.querySelectorAll('.route-item').forEach(function(item) {{
                item.classList.add('active');
            }});
        }});
        
        // 关闭所有线路
        document.getElementById('hide-all-btn').addEventListener('click', function() {{
            Object.keys(routeLayers).forEach(function(routeKey) {{
                var layer = routeLayers[routeKey];
                if (layer.visible) {{
                    map.removeLayer(layer.polyline);
                    if (layer.decorator) map.removeLayer(layer.decorator);
                    layer.markers.forEach(function(m) {{ map.removeLayer(m); }});
                    layer.visible = false;
                }}
            }});
            // 更新所有列表项状态
            document.querySelectorAll('.route-item').forEach(function(item) {{
                item.classList.remove('active');
            }});
        }});
        
        // 切换热力图模式
        document.getElementById('toggle-heatmap-btn').addEventListener('click', function() {{
            // 切换模式
            if (currentHeatmapMode === 'boarding') {{
                currentHeatmapMode = 'paid';
                this.textContent = '切换为上车次数热力图';
                this.style.background = '#3498db';
            }} else {{
                currentHeatmapMode = 'boarding';
                this.textContent = '切换为付费用户热力图';
                this.style.background = '#27ae60';
            }}
            // 更新显示
            updateHeatmapDisplay();
        }});
        
        // 添加热力图图例（左下角）
        var legend = L.control({{position: 'bottomleft'}});
        legend.onAdd = function(map) {{
            var div = L.DomUtil.create('div', 'heatmap-legend');
            div.style.backgroundColor = 'white';
            div.style.padding = '15px';
            div.style.borderRadius = '5px';
            div.style.boxShadow = '0 2px 10px rgba(0,0,0,0.3)';
            div.style.fontFamily = 'Microsoft YaHei, Arial, sans-serif';
            div.style.fontSize = '14px';
            div.style.zIndex = 2000;  // 提升图例层级，避免被覆盖
            div.style.position = 'relative';
            div.style.pointerEvents = 'auto';
            div.style.maxWidth = '260px';
            div.style.margin = '0 0 10px 10px';
            div.innerHTML = '<h4 style="margin: 0 0 10px 0; color: #2c3e50;">上车次数热力图</h4>';
            
            // 使用updateLegend函数初始化图例
            var currentMax = getCurrentMaxCount();
            var modeLabel = currentHeatmapMode === 'paid' ? '付费用户数' : '上车次数';
            updateLegend(currentMax, modeLabel);
            
            return div;
        }};
        legend.addTo(map);
        
        // 初始化时间条
        initTimeRange();
    </script>
</body>
</html>"""
    
    # 确保输出目录存在
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    output_file = OUT_DIR / 'bus_routes_reconstructed.html'
    with open(str(output_file), 'w', encoding='utf-8') as f:
        f.write(html_content)

    print(f"HTML文件已生成: {output_file}")

if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print(f"\n错误: {e}")
        import traceback
        traceback.print_exc()

