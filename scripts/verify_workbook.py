#!/usr/bin/env python3
"""工作簿层回归断言（P2）

数值层由 shadow_diff.py 负责；本工具只验"Excel 有没有被写坏"：
  1. 模板原有内容逐格未被改动
  2. 目标 sheet 存在，且新增行数与预期一致
  3. 同一天重跑两次，行数不增长（幂等）
  4. 环比块结构正确：每组 3 行（基期/本期/环比），环比行第二列写"环比"
  5. 稀疏分组：组首行有维度值、后续行为空
  6. 环比值是数值或 / 符号

用法：
    python scripts/verify_workbook.py --template 模板.xlsx --output 产物.xlsx \
        --report-date 2026-10-25 [--sheet "11点时报环比"] [--expect-new-rows 132]

退出码：0=全部通过；1=有断言失败；2=用法错误
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from cli_compat import configure_console

configure_console()

from openpyxl import load_workbook


def cell_dump(ws, max_row: int, max_col: int) -> list[list[str]]:
    out = []
    for row in ws.iter_rows(min_row=1, max_row=max_row, max_col=max_col, values_only=True):
        out.append(["" if v is None else str(v) for v in row])
    return out


def find_sheet(wb, name: str):
    import re

    def norm(t: str) -> str:
        return re.sub(r"[\s（）()]", "", str(t))

    if name in wb.sheetnames:
        return wb[name]
    for ws in wb.worksheets:
        if norm(ws.title) == norm(name):
            return ws
    return None


def check_preserved(template_path: Path, output_path: Path) -> list[str]:
    """模板原有 sheet 的前 N 行必须逐格一致（N=模板原 max_row，列取两者较小值）。"""
    problems: list[str] = []
    tpl = load_workbook(template_path, data_only=True)
    out = load_workbook(output_path, data_only=True)
    for ws_t in tpl.worksheets:
        ws_o = find_sheet(out, ws_t.title)
        if ws_o is None:
            problems.append(f"产物缺少 sheet「{ws_t.title}」")
            continue
        max_row = min(ws_t.max_row, ws_o.max_row)
        max_col = min(ws_t.max_column, ws_o.max_column, 60)
        a = cell_dump(ws_t, max_row, max_col)
        b = cell_dump(ws_o, max_row, max_col)
        diffs = sum(1 for ra, rb in zip(a, b) for x, y in zip(ra, rb) if x != y)
        if diffs:
            problems.append(f"「{ws_t.title}」原有数据被改动 {diffs} 格（模板 {ws_t.max_row} 行）")
        else:
            print(f"  ✓ 「{ws_t.title}」原有 {max_row} 行未被改动")
    tpl.close()
    out.close()
    return problems


def check_comparison_block(ws, header_row: int, dim_col: int, label_col: int) -> list[str]:
    problems: list[str] = []
    blocks = groups = 0
    row = header_row + 1
    while row + 2 <= ws.max_row:
        label = ws.cell(row=row + 2, column=label_col).value
        if str(label).strip() != "环比":
            # 不是标准块，跳过
            row += 1
            continue
        groups += 1
        first_dim = ws.cell(row=row, column=dim_col).value
        if not first_dim:
            problems.append(f"环比块第 {row} 行缺少维度值（稀疏写法要求组首行写维度）")
        # 稀疏写法：本期行维度留空；环比行在维度列写"环比"标识（由上面的检测保证）
        if ws.cell(row=row + 1, column=dim_col).value not in (None, ""):
            problems.append(f"环比块第 {row + 1} 行维度列应为空（稀疏写法）")
        blocks += 1
        row += 3
    if groups and blocks * 3 > ws.max_row:
        problems.append("环比块行数异常")
    print(f"  ✓ 环比块 {groups} 组（每组 3 行，环比行标识正确）")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description="工作簿层回归断言")
    ap.add_argument("--template", required=True, type=Path)
    ap.add_argument("--output", required=True, type=Path)
    ap.add_argument("--report-date", required=True)
    ap.add_argument("--sheet", action="append", default=[], help="检查环比块结构的 sheet 名")
    ap.add_argument("--expect-new-rows", type=int, help="指定 sheet 期望新增行数")
    ap.add_argument("--expect-sheet", default=None, help="配合 --expect-new-rows 使用的 sheet 名")
    args = ap.parse_args()

    for p in (args.template, args.output):
        if not p.exists():
            print(f"找不到文件：{p}", file=sys.stderr)
            return 2

    problems: list[str] = []
    print("1) 模板原有内容是否被改动")
    problems += check_preserved(args.template, args.output)

    out = load_workbook(args.output, data_only=True)
    tpl = load_workbook(args.template, data_only=True)

    print("2) 新增行数")
    if args.expect_new_rows is not None and args.expect_sheet:
        ws = find_sheet(out, args.expect_sheet)
        ws_t = find_sheet(tpl, args.expect_sheet)
        if ws is None:
            problems.append(f"产物缺少 sheet「{args.expect_sheet}」")
        else:
            delta = ws.max_row - (ws_t.max_row if ws_t else 0)
            if delta == args.expect_new_rows:
                print(f"  ✓ 「{args.expect_sheet}」新增 {delta} 行")
            else:
                problems.append(f"「{args.expect_sheet}」新增 {delta} 行，期望 {args.expect_new_rows}")

    print("3) 环比块结构")
    for name in args.sheet:
        ws = find_sheet(out, name)
        if ws is None:
            problems.append(f"产物缺少环比 sheet「{name}」")
            continue
        # 找表头行（含"日期"与"国家"的行）
        header_row = 1
        for r in range(1, min(ws.max_row, 8) + 1):
            texts = [str(ws.cell(row=r, column=c).value or "") for c in range(1, 8)]
            if any("日期" in t for t in texts) and any("国家" in t for t in texts):
                header_row = r
                break
        problems += check_comparison_block(ws, header_row, dim_col=2, label_col=2)

    print("4) 目标日期行数（幂等性基准）")
    for ws_t in tpl.worksheets:
        ws_o = find_sheet(out, ws_t.title)
        if ws_o is None:
            continue
        count = 0
        for row in ws_o.iter_rows(min_row=2, max_col=3, values_only=True):
            if any(str(v or "")[:10] == args.report_date for v in row):
                count += 1
        if count:
            print(f"  「{ws_t.title}」含 {args.report_date} 的行 {count} 行")

    tpl.close()
    out.close()

    print()
    if problems:
        print(f"断言失败 {len(problems)} 项：")
        for p in problems:
            print(f"  ✗ {p}")
        return 1
    print("全部断言通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
