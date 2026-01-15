# 黄山公交IC卡数据分析项目 - 脚本说明

## 项目概述

本项目用于分析黄山公交IC卡数据，包含客流分析、用户出行规律分析、线路用户深度分析等多个维度。

**统一输出格式**：所有分析脚本均采用 **Plotly交互式HTML + Markdown总结报告** 的形式输出。

---

## 核心分析脚本

### 1. 完整客流分析
**脚本**: `analyze_complete_passenger_flow.py`

**用途**：分析完整客流（包含移动支付，约131万人次）
- 区分可溯源用户（79万）和移动支付（52万）
- 票制推断（1元/2元/5元）
- 时间分布、站点客流、营收分析

**输出目录**: `OUT_complete_passenger_flow/`

**输出文件**:
- `complete_route_summary.csv` - 线路完整客流汇总
- `card_type_comparison.csv` - 卡类型对比
- `time_distribution.csv` - 时间分布
- `station_complete_flow.csv` - 站点完整客流
- `revenue_breakdown.csv` - 营收明细
- `complete_flow_details.json` - JSON明细
- `complete_flow_analysis.html` - **Plotly交互式报告**（8个图表）
- `complete_flow_summary.md` - **Markdown总结报告**

**图表列表**:
1. TOP15线路客流对比（柱状图）
2. 卡类型分布（饼图）
3. 移动支付占比TOP15（柱状图）
4. 营收TOP15线路（柱状图）
5. 时段客流分布（折线图）
6. 票价分布（饼图）
7. 站点客流TOP15（柱状图）
8. 客流-营收关系（散点图）

---

### 2. 用户出行规律分析
**脚本**: `analyze_user_travel_patterns.py`

**用途**：用户出行规律深度分析
- 通勤用户识别
- 出行链路分析
- 多日模式识别
- 异常检测

**输出目录**: `OUT_user_analysis/`

**输出文件**:
- `user_travel_summary.csv` - 用户出行汇总
- `user_travel_details.json` - JSON明细
- `user_travel_analysis.html` - **Plotly交互式报告**（6个图表）
- `user_travel_summary.md` - **Markdown总结报告**

**图表列表**:
1. 卡类型分布（饼图）
2. 通勤类型分布（饼图）
3. 出行次数分布（柱状图）
4. 用户活跃度分布（柱状图）
5. 平均每天出行次数（柱状图）
6. 付费用户 vs 免费用户（饼图）

---

### 3. 核心用户分析
**脚本**: `analyze_route_user_report.py`

**用途**：线路核心用户深度分析
- 核心用户贡献度（top10%/20%）
- 用户集中度分析
- 线路分类（核心依赖型/中度集中/客流分散）

**依赖**: 需要先运行 `analyze_complete_passenger_flow.py` 和 `analyze_user_travel_patterns.py`

**输出目录**: `OUT_route_user_analysis/`

**输出文件**:
- `core_users_absolute_analysis.csv` - 核心用户绝对值分析
- `route_classification_summary.csv` - 线路分类汇总
- `route_user_insight_report_plotly.html` - **Plotly交互式报告**（4个图表）
- `route_user_insight_report.png` - 静态图片
- `route_user_summary.md` - **Markdown总结报告**

**图表列表**:
1. TOP15线路：top10%用户绝对客流量（柱状图）
2. TOP15线路：top10%用户贡献占比（双柱状图）
3. 双维度占比散点图（散点图）
4. 线路类型分布（饼图）

---

## 使用流程

### 执行顺序

```bash
# 步骤1：完整客流分析（基础，独立运行）
python analyze_complete_passenger_flow.py

# 步骤2：用户出行规律分析（独立运行）
python analyze_user_travel_patterns.py

# 步骤3：核心用户分析（依赖步骤1和2的输出）
python analyze_route_user_report.py
```

### 查看输出

1. **交互式图表**: 在浏览器中打开对应的 `*.html` 文件
2. **文字总结**: 查看对应的 `*_summary.md` 文件
3. **数据明细**: 使用Excel/文本编辑器打开CSV文件

---

## 输出格式说明

### Plotly交互式HTML
- **特点**: 可缩放、可悬停查看详情、可下载图片
- **兼容性**: 现代浏览器（Chrome/Firefox/Edge）
- **优势**: 在Python代码中直接调试，不依赖浏览器JavaScript

### Markdown总结报告
- **内容**: 整体概况、详细数据表格、关键发现
- **格式**: 标准Markdown，支持GitHub/GitLab预览
- **用途**: 快速查阅分析结论，便于分享

---

## 项目依赖

- Python 3.8+
- Pandas
- Plotly (`pip install plotly`)
- Kaleido (可选，用于PNG导出: `pip install kaleido`)

---

## 核心发现摘要

### 票制结构
| 票价类型 | 线路数 | 客流占比 | 说明 |
|----------|--------|----------|------|
| 2元票制 | 24条 | 主流 | 市区线路 |
| 5元票制 | 3条 | 少数 | 高铁快线（106/107路） |
| 1元票制 | 1条 | 极少 | 社区线路 |
| 其他 | 2条 | - | 特殊线路 |

### 客流特征
- **总客流**: 约131万人次
- **可溯源用户**: 约79万人次（60%）
- **移动支付**: 约52万人次（40%）
- **移动支付已成为主流**: 超过可溯源用户客流

### 高铁快线特征
- **106路**: 21,435人次，移动支付96.1%，平均票价5.0元
- **107路**: 59,791人次，移动支付96.3%，平均票价5.0元
- **特点**: 移动支付占绝对主导，临时乘客为主

---

## 更新日志

### 2026-01-13
- 统一所有分析脚本输出格式为 **Plotly HTML + Markdown**
- 每个脚本输出5-8个丰富的图表
- 代码结构简洁：直接修改源文件，无后缀版本
- 删除冗余的 `_plotly` 后缀文件
- 更新文档说明

---

## 作者

分析脚本由Claude AI协助开发，基于黄山公交IC卡数据进行深度分析。
