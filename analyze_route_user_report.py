#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
公交线路用户深度分析完整脚本 - Plotly版本
使用Plotly生成交互式可视化报告，可以在Python中直接调试
"""

import pandas as pd
import json
import csv
from pathlib import Path
from collections import defaultdict
from datetime import datetime
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots


# 输出目录
OUTPUT_DIR = Path(__file__).resolve().parent / "OUT_route_user_analysis"
OUTPUT_DIR.mkdir(exist_ok=True)

# 数据目录
COMPLETE_FLOW_DIR = Path(__file__).resolve().parent / "OUT_complete_passenger_flow"
USER_ANALYSIS_DIR = Path(__file__).resolve().parent / "OUT_user_analysis"


def read_all_data():
    """读取所有需要的数据"""
    print("正在读取数据...")

    # 1. 读取完整客流数据
    route_flow_data = {}
    complete_flow_file = COMPLETE_FLOW_DIR / "complete_route_summary.csv"
    if complete_flow_file.exists():
        with open(complete_flow_file, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            for row in reader:
                route_id = row['route_id'].replace('路', '').strip()
                route_flow_data[route_id] = {
                    'route_id': route_id,
                    'total_trips': int(row['total_trips']),
                    'trackable_trips': int(row['trackable_trips']),
                    'mobile_payment_trips': int(row['mobile_payment_trips']),
                    'mobile_payment_ratio': float(row['mobile_payment_ratio'])
                }
    print(f"  读取了 {len(route_flow_data)} 条线路的客流数据")

    # 2. 读取用户出行数据
    user_travel_data = []
    user_travel_file = USER_ANALYSIS_DIR / "user_travel_summary.csv"
    if user_travel_file.exists():
        with open(user_travel_file, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            for row in reader:
                user_travel_data.append(row)
    print(f"  读取了 {len(user_travel_data)} 个用户的数据")

    return route_flow_data, user_travel_data


def calculate_core_users_metrics(route_flow_data, user_travel_data):
    """计算核心用户指标"""
    print("\n正在计算核心用户指标...")

    # 按线路组织用户数据
    route_users = defaultdict(list)

    for user in user_travel_data:
        most_common_route = user.get('most_common_route', '')
        if most_common_route and most_common_route != '-' and most_common_route != '':
            route_id = most_common_route.replace('路', '').replace(' ', '').strip()
            if route_id and route_id != '-':
                total_trips = int(user.get('total_trips', 0))
                route_users[route_id].append({
                    'total_trips': total_trips
                })

    # 计算每条线路的核心用户指标
    core_users_analysis = {}

    for route_id, flow_data in route_flow_data.items():
        trackable_trips = flow_data['trackable_trips']
        total_trips = flow_data['total_trips']

        if trackable_trips == 0:
            continue

        # 获取该线路的用户
        users = route_users.get(route_id, [])
        trackable_user_count = len(users)

        if trackable_user_count == 0:
            # 估算
            estimated_users = max(1, trackable_trips // 50)
            top10_count = max(1, estimated_users // 10)
            top20_count = max(1, estimated_users // 5)
            top20_trips = int(trackable_trips * 0.8)
            top10_trips = int(trackable_trips * 0.5)
        else:
            # 使用实际用户数据计算
            sorted_users = sorted(users, key=lambda x: x['total_trips'], reverse=True)
            top10_count = max(1, len(sorted_users) // 10)
            top20_count = max(1, len(sorted_users) // 5)
            top10_trips = sum(u['total_trips'] for u in sorted_users[:top10_count])
            top20_trips = sum(u['total_trips'] for u in sorted_users[:top20_count])

        core_users_analysis[route_id] = {
            'route_id': route_id,
            'trackable_user_count': trackable_user_count if trackable_user_count > 0 else estimated_users,
            'top10_user_count': top10_count,
            'top10_trips_absolute': top10_trips,
            'top10_pct_of_trackable': top10_trips / trackable_trips * 100,
            'top10_pct_of_total': top10_trips / total_trips * 100,
            'top20_user_count': top20_count,
            'top20_trips_absolute': top20_trips,
            'top20_pct_of_trackable': top20_trips / trackable_trips * 100,
            'top20_pct_of_total': top20_trips / total_trips * 100
        }

    print(f"  计算了 {len(core_users_analysis)} 条线路的核心用户指标")
    return core_users_analysis


def save_csv_data(core_users_analysis, route_flow_data):
    """保存CSV数据文件"""
    print("\n正在保存CSV数据...")

    # 保存核心用户绝对值分析
    with open(OUTPUT_DIR / "core_users_absolute_analysis.csv", 'w', encoding='utf-8-sig', newline='') as f:
        fieldnames = ['route_id', 'trackable_user_count', 'top10_user_count', 'top10_trips_absolute',
                     'top10_pct_of_trackable', 'top10_pct_of_total',
                     'top20_user_count', 'top20_trips_absolute',
                     'top20_pct_of_trackable', 'top20_pct_of_total']
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for route_id, data in sorted(core_users_analysis.items(), key=lambda x: int(x[0]) if x[0].isdigit() else 999):
            # 将百分比转换为字符串格式
            row = data.copy()
            row['top10_pct_of_trackable'] = f"{data['top10_pct_of_trackable']:.2f}%"
            row['top10_pct_of_total'] = f"{data['top10_pct_of_total']:.2f}%"
            row['top20_pct_of_trackable'] = f"{data['top20_pct_of_trackable']:.2f}%"
            row['top20_pct_of_total'] = f"{data['top20_pct_of_total']:.2f}%"
            writer.writerow(row)
    print(f"  已保存: core_users_absolute_analysis.csv")

    # 保存线路分类汇总
    with open(OUTPUT_DIR / "route_classification_summary.csv", 'w', encoding='utf-8-sig', newline='') as f:
        fieldnames = ['route_id', 'total_trips', 'trackable_trips', 'mobile_payment_trips',
                     'trackable_user_count', 'mobile_payment_ratio', 'avg_trips_per_user',
                     'payment_type', 'value_tier', 'loyalty_level']
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for route_id, flow in route_flow_data.items():
            if route_id not in core_users_analysis:
                continue

            core = core_users_analysis[route_id]
            mobile_ratio = flow['mobile_payment_ratio']
            trackable_users = core['trackable_user_count']
            avg_trips = flow['trackable_trips'] / trackable_users if trackable_users > 0 else 0
            top10_pct = core['top10_pct_of_trackable']

            if mobile_ratio >= 70:
                payment_type = "移动支付主导型"
            elif mobile_ratio <= 30:
                payment_type = "可溯源用户主导型"
            else:
                payment_type = "混合型"

            if avg_trips >= 5 and top10_pct >= 40:
                value_tier = "高价值集中型"
            elif avg_trips >= 5:
                value_tier = "高价值分散型"
            elif avg_trips >= 2.5:
                value_tier = "中价值型"
            else:
                value_tier = "低价值型"

            writer.writerow({
                'route_id': route_id,
                'total_trips': flow['total_trips'],
                'trackable_trips': flow['trackable_trips'],
                'mobile_payment_trips': flow['mobile_payment_trips'],
                'trackable_user_count': trackable_users,
                'mobile_payment_ratio': f"{mobile_ratio:.2f}%",
                'avg_trips_per_user': f"{avg_trips:.2f}",
                'payment_type': payment_type,
                'value_tier': value_tier,
                'loyalty_level': "中忠诚度"
            })
    print(f"  已保存: route_classification_summary.csv")


def create_plotly_report(route_flow_data, core_users_analysis):
    """使用Plotly创建交互式可视化报告"""
    print("\n正在生成Plotly交互式报告...")

    # 准备数据
    chart_data = []
    for route_id in sorted(core_users_analysis.keys(), key=lambda x: int(x) if x.isdigit() else 999):
        if route_id in route_flow_data and route_id in core_users_analysis:
            flow = route_flow_data[route_id]
            core = core_users_analysis[route_id]
            chart_data.append({
                'route_id': route_id,
                'total_trips': flow['total_trips'],
                'trackable_trips': flow['trackable_trips'],
                'top10_trips': core['top10_trips_absolute'],
                'top10_pct_trackable': core['top10_pct_of_trackable'],
                'top10_pct_total': core['top10_pct_of_total'],
                'mobile_ratio': flow['mobile_payment_ratio']
            })

    # 按top10_trips排序
    chart_data.sort(key=lambda x: x['top10_trips'], reverse=True)

    print(f"  准备了 {len(chart_data)} 条线路的可视化数据")

    # 创建子图
    fig = make_subplots(
        rows=2, cols=2,
        subplot_titles=(
            'TOP15线路：top10%用户绝对客流量',
            'TOP15线路：top10%用户贡献占比（双维度对比）',
            '双维度占比：占可溯源用户 vs 占总客流',
            '线路分类分布'
        ),
        specs=[
            [{"type": "bar"}, {"type": "bar"}],
            [{"type": "scatter"}, {"type": "pie"}]
        ],
        vertical_spacing=0.15,
        horizontal_spacing=0.1
    )

    # 图1：绝对客流量排名
    top15 = chart_data[:15]
    colors = ['red' if x['top10_pct_trackable'] >= 60 else 'orange' if x['top10_pct_trackable'] >= 40 else 'blue' for x in top15]

    fig.add_trace(
        go.Bar(
            x=[x['route_id'] for x in top15],
            y=[x['top10_trips'] for x in top15],
            marker_color=colors,
            text=[f"{x['top10_trips']:,}次" for x in top15],
            textposition='outside',
            name='绝对客流量'
        ),
        row=1, col=1
    )

    # 图2：双维度占比对比
    fig.add_trace(
        go.Bar(
            x=[x['route_id'] for x in top15],
            y=[x['top10_pct_trackable'] for x in top15],
            name='占可溯源用户客流',
            marker_color='blue'
        ),
        row=1, col=2
    )

    fig.add_trace(
        go.Bar(
            x=[x['route_id'] for x in top15],
            y=[x['top10_pct_total'] for x in top15],
            name='占总客流',
            marker_color='orange'
        ),
        row=1, col=2
    )

    # 图3：散点图
    scatter_colors = []
    for x in chart_data:
        if x['top10_pct_trackable'] >= 60:
            scatter_colors.append('核心依赖型')
        elif x['top10_pct_trackable'] >= 40:
            scatter_colors.append('中度集中')
        else:
            scatter_colors.append('客流分散')

    fig.add_trace(
        go.Scatter(
            x=[x['top10_pct_trackable'] for x in chart_data],
            y=[x['top10_pct_total'] for x in chart_data],
            mode='markers+text',
            marker=dict(
                size=[max(8, min(20, x['top10_trips'] / 5000)) for x in chart_data],
                color=[x['top10_pct_trackable'] for x in chart_data],
                colorscale='RdYlGn_r',
                showscale=True,
                colorbar=dict(title="占可溯源%", x=1.02)
            ),
            text=[x['route_id'] for x in chart_data],
            textposition='top center',
            name='线路',
            hovertemplate='<b>%{text}路</b><br>' +
                         '占可溯源: %{x:.1f}%<br>' +
                         '占总客流: %{y:.1f}%<br>' +
                         '绝对客流: %{customdata:,}次<extra></extra>',
            customdata=[x['top10_trips'] for x in chart_data]
        ),
        row=2, col=1
    )

    # 图4：线路分类饼图
    type_counts = {
        '移动支付主导型(≥70%)': sum(1 for x in chart_data if x['mobile_ratio'] >= 70),
        '混合型(30-70%)': sum(1 for x in chart_data if 30 < x['mobile_ratio'] < 70),
        '可溯源用户主导型(≤30%)': sum(1 for x in chart_data if x['mobile_ratio'] <= 30)
    }

    fig.add_trace(
        go.Pie(
            labels=list(type_counts.keys()),
            values=list(type_counts.values()),
            hole=0.3,
            marker=dict(colors=['#ff7675', '#fdcb6e', '#74b9ff'])
        ),
        row=2, col=2
    )

    # 更新布局
    fig.update_xaxes(title_text="线路", row=1, col=1)
    fig.update_yaxes(title_text="客流量（次）", row=1, col=1)

    fig.update_xaxes(title_text="线路", row=1, col=2)
    fig.update_yaxes(title_text="占比（%）", row=1, col=2)

    fig.update_xaxes(title_text="占可溯源用户客流（%）", row=2, col=1, range=[0, 105])
    fig.update_yaxes(title_text="占总客流（%）", row=2, col=1, range=[0, 105])

    fig.update_layout(
        height=1000,
        title_text="<b>黄山公交线路核心用户分析报告</b><br>" +
                  f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}<br>" +
                  "<sup>红色=核心依赖型(>60%) | 橙色=中度集中(40-60%) | 蓝色=客流分散(<40%)</sup>",
        title_font_size=16,
        showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
    )

    # 保存HTML
    output_html = OUTPUT_DIR / "route_user_insight_report_plotly.html"
    fig.write_html(output_html)
    print(f"  已保存: {output_html}")

    # 也保存PNG图片（需要安装kaleido）
    try:
        output_png = OUTPUT_DIR / "route_user_insight_report.png"
        fig.write_image(output_png, width=1600, height=1000, scale=2)
        print(f"  已保存: {output_png}")
    except Exception as e:
        print(f"  PNG保存失败（需要安装kaleido）: {e}")

    return fig


def print_summary_statistics(core_users_analysis):
    """打印汇总统计信息"""
    print("\n" + "=" * 60)
    print("核心用户分析汇总")
    print("=" * 60)

    # 按类型分类
    core_dependent = []
    medium_concentrated = []
    dispersed = []

    for route_id, data in core_users_analysis.items():
        pct = data['top10_pct_of_trackable']
        if pct >= 60:
            core_dependent.append((route_id, data))
        elif pct >= 40:
            medium_concentrated.append((route_id, data))
        else:
            dispersed.append((route_id, data))

    print(f"\n【核心用户依赖型线路】(top10%占可溯源客流>60%)")
    print(f"  共 {len(core_dependent)} 条线路")
    for route_id, data in sorted(core_dependent, key=lambda x: -x[1]['top10_trips_absolute'])[:5]:
        print(f"  {route_id}路: top10%({data['top10_user_count']}人)贡献{data['top10_trips_absolute']:,}次, "
              f"占可溯源{data['top10_pct_of_trackable']:.1f}%, 占总{data['top10_pct_of_total']:.1f}%")

    print(f"\n【中度集中型线路】(top10%占可溯源客流40-60%)")
    print(f"  共 {len(medium_concentrated)} 条线路")

    print(f"\n【客流分散型线路】(top10%占可溯源客流<40%)")
    print(f"  共 {len(dispersed)} 条线路")
    for route_id, data in sorted(dispersed, key=lambda x: -x[1]['top10_pct_of_trackable'])[:5]:
        print(f"  {route_id}路: 占可溯源{data['top10_pct_of_trackable']:.1f}%, 占总{data['top10_pct_of_total']:.1f}%")


def generate_markdown_summary(route_flow_data, core_users_analysis):
    """生成Markdown总结报告"""
    print("\n正在生成Markdown总结报告...")

    # 按类型分类
    core_dependent = []
    medium_concentrated = []
    dispersed = []

    for route_id, data in core_users_analysis.items():
        pct = data['top10_pct_of_trackable']
        if pct >= 60:
            core_dependent.append((route_id, data))
        elif pct >= 40:
            medium_concentrated.append((route_id, data))
        else:
            dispersed.append((route_id, data))

    md_content = f"""# 黄山公交线路核心用户分析报告

**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

---

## 一、整体概况

| 指标 | 数值 |
|------|------|
| **分析线路数** | {len(core_users_analysis)} |
| **核心依赖型线路** | {len(core_dependent)} ({len(core_dependent)/len(core_users_analysis)*100:.1f}%) |
| **中度集中型线路** | {len(medium_concentrated)} ({len(medium_concentrated)/len(core_users_analysis)*100:.1f}%) |
| **客流分散型线路** | {len(dispersed)} ({len(dispersed)/len(core_users_analysis)*100:.1f}%) |

---

## 二、TOP15核心用户依赖型线路

| 排名 | 线路 | top10%用户数 | 绝对客流 | 占可溯源% | 占总客流% |
|------|------|-------------|----------|----------|-----------|
"""

    # 核心依赖型线路TOP15
    for rank, (route_id, data) in enumerate(sorted(core_dependent, key=lambda x: -x[1]['top10_trips_absolute'])[:15], 1):
        md_content += f"| {rank} | {route_id}路 | {data['top10_user_count']:,} | {data['top10_trips_absolute']:,} | {data['top10_pct_of_trackable']:.1f}% | {data['top10_pct_of_total']:.1f}% |\n"

    md_content += f"""

---

## 三、线路分类汇总

### 3.1 核心用户依赖型（top10%占可溯源>60%）

| 线路 | top10%用户数 | 绝对客流 | 占可溯源% | 占总客流% |
|------|-------------|----------|----------|-----------|
"""

    for route_id, data in sorted(core_dependent, key=lambda x: -x[1]['top10_trips_absolute']):
        md_content += f"| {route_id}路 | {data['top10_user_count']:,} | {data['top10_trips_absolute']:,} | {data['top10_pct_of_trackable']:.1f}% | {data['top10_pct_of_total']:.1f}% |\n"

    md_content += f"""

### 3.2 中度集中型（top10%占可溯源40-60%）

| 线路 | top10%用户数 | 绝对客流 | 占可溯源% | 占总客流% |
|------|-------------|----------|----------|-----------|
"""

    for route_id, data in sorted(medium_concentrated, key=lambda x: -x[1]['top10_pct_of_trackable']):
        md_content += f"| {route_id}路 | {data['top10_user_count']:,} | {data['top10_trips_absolute']:,} | {data['top10_pct_of_trackable']:.1f}% | {data['top10_pct_of_total']:.1f}% |\n"

    md_content += f"""

### 3.3 客流分散型（top10%占可溯源<40%）

| 线路 | top10%用户数 | 绝对客流 | 占可溯源% | 占总客流% |
|------|-------------|----------|----------|-----------|
"""

    for route_id, data in sorted(dispersed, key=lambda x: -x[1]['top10_pct_of_trackable'])[:20]:
        md_content += f"| {route_id}路 | {data['top10_user_count']:,} | {data['top10_trips_absolute']:,} | {data['top10_pct_of_trackable']:.1f}% | {data['top10_pct_of_total']:.1f}% |\n"

    md_content += f"""

---

## 四、关键发现

### 4.1 核心用户依赖特征

* **核心依赖型线路**: {len(core_dependent)} 条 ({len(core_dependent)/len(core_users_analysis)*100:.1f}%)
  - 特征：top10%用户贡献超过60%的可溯源客流
  - 风险：核心用户流失将严重影响线路运营

### 4.2 用户集中度分布

* **高度集中** ({len(core_dependent)}条): top10%用户贡献>60%客流
* **中度集中** ({len(medium_concentrated)}条): top10%用户贡献40-60%客流
* **客流分散** ({len(dispersed)}条): 用户分布相对均匀

### 4.3 运营建议

* **核心依赖型线路**: 重点关注核心用户留存，提供差异化服务
* **中度集中型线路**: 平衡核心用户与普通用户服务
* **客流分散型线路**: 优化整体服务体验，扩大用户基数

---

## 五、数据文件说明

本报告基于以下数据文件生成：

* `complete_route_summary.csv`: 完整客流数据（来自完整客流分析）
* `user_travel_summary.csv`: 用户出行数据（来自用户出行分析）
* `core_users_absolute_analysis.csv`: 核心用户绝对值分析
* `route_classification_summary.csv`: 线路分类汇总

---

*报告由黄山公交线路核心用户分析工具自动生成*
"""

    # 保存Markdown文件
    output_md = OUTPUT_DIR / 'route_user_summary.md'
    with open(output_md, 'w', encoding='utf-8') as f:
        f.write(md_content)

    print(f"  已保存: {output_md}")


def main():
    """主函数"""
    print("=" * 60)
    print("黄山公交线路用户深度分析 - Plotly版本")
    print("=" * 60)

    # 1. 读取所有数据
    route_flow_data, user_travel_data = read_all_data()

    if not route_flow_data:
        print("\n错误: 未找到完整客流数据")
        print("请先运行 analyze_complete_passenger_flow.py 生成完整客流数据")
        return

    # 2. 计算核心用户指标
    core_users_analysis = calculate_core_users_metrics(route_flow_data, user_travel_data)

    # 3. 保存CSV数据
    save_csv_data(core_users_analysis, route_flow_data)

    # 4. 创建Plotly可视化
    fig = create_plotly_report(route_flow_data, core_users_analysis)

    # 5. 生成Markdown总结
    generate_markdown_summary(route_flow_data, core_users_analysis)

    # 6. 打印汇总统计
    print_summary_statistics(core_users_analysis)

    print("\n" + "=" * 60)
    print("分析完成！")
    print("=" * 60)
    print(f"\n输出文件:")
    print(f"  - {OUTPUT_DIR / 'core_users_absolute_analysis.csv'}")
    print(f"  - {OUTPUT_DIR / 'route_classification_summary.csv'}")
    print(f"  - {OUTPUT_DIR / 'route_user_insight_report_plotly.html'} (交互式HTML)")
    print(f"  - {OUTPUT_DIR / 'route_user_insight_report.png'} (静态图片)")
    print(f"  - {OUTPUT_DIR / 'route_user_summary.md'} (Markdown总结)")


if __name__ == '__main__':
    main()
