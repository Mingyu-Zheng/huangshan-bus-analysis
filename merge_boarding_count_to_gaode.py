import csv
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = REPO_ROOT / "MID_output"
MID_STATION_DIR = REPO_ROOT / "MID_station"

def merge_boarding_count_by_order():
    source_file = OUTPUT_DIR / 'huangshan_merged_stations_filtered_gcj02.csv'
    target_file = MID_STATION_DIR / 'stations_gaode_gcj02_2025-12-21T16-26-10.csv'
    output_file = OUTPUT_DIR / 'stations_gaode_with_boarding.csv'

    if not source_file.exists() or not target_file.exists():
        print("错误: 找不到输入文件。")
        print(f"  源文件: {source_file}")
        print(f"  目标文件: {target_file}")
        return

    # 1. 读取源文件中的 boarding_count 列表（按顺序）
    source_data = []
    print(f"正在读取源文件: {source_file}...")
    with open(source_file, mode='r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            source_data.append({
                'name': row.get('station_name', '').strip(),
                'count': row.get('boarding_count', '')
            })

    # 2. 读取目标文件，按顺序填充数据
    print(f"正在处理目标文件: {target_file}...")
    final_rows = []
    
    with open(target_file, mode='r', encoding='utf-8-sig') as f:
        # 先获取表头
        content = list(csv.reader(f))
        if not content: return
        header = [h.strip() for h in content[0] if h.strip()]
        data_rows = content[1:]
        
        if 'boarding_count' not in header:
            header.append('boarding_count')
            
        # 按顺序对齐
        for i, row in enumerate(data_rows):
            if not row: continue
            # 构建字典
            row_dict = {header[j]: row[j] for j in range(min(len(row), len(header)-1))}
            
            target_name = row_dict.get('station_name', '').strip().replace('"', '')
            
            # 尝试在源数据中找到对应位置的站点
            if i < len(source_data):
                src = source_data[i]
                # 即使 ID 不同，只要顺序一致，名称应该是对应的
                row_dict['boarding_count'] = src['count']
                if target_name != src['name']:
                    print(f"提示: 第 {i+2} 行名称不完全匹配 (目标: {target_name}, 源: {src['name']})，已按顺序填充")
            else:
                row_dict['boarding_count'] = ''
                
            final_rows.append(row_dict)

    # 3. 写入新文件
    print(f"正在保存到: {output_file}...")
    with open(output_file, mode='w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=header)
        writer.writeheader()
        writer.writerows(final_rows)
    print(f"成功！处理了 {len(final_rows)} 个站点。")

if __name__ == "__main__":
    merge_boarding_count_by_order()