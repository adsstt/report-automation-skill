#!/usr/bin/env python3
"""影子对账（shadow diff）

把「用反推规则跑出来的影子报表」和「用户给的目标报表样本」逐格比对，
把差异精确定位到单元格。差异是疑点的主要来源——对不上的格子比空想更能暴露真实规则。

支持 CSV 与 XLSX；按行键对齐而不是按行号，避免行顺序差异造成假报警。

用法：
    python scripts/shadow_diff.py --shadow 影子.csv --expected 目标.csv --out runtime/shadow_diff.md
    python scripts/shadow_diff.py --shadow 影子.xlsx --expected 目标.xlsx --sheet "18点时报环比"

退出码：0=完全一致；1=存在差异；2=用法或读取错误
"""

from __future__ import annotations

import argparse
import csv
import io
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from cli_compat import configure_console

configure_console()


def load_table(path: Path, sheet: str | None = None) -> tuple[list[str], list[dict[str, Any]]]:
    """读入为 (header, rows)。CSV 自动尝试 UTF-8/GBK；XLSX 取指定或首个 sheet。"""
    suffix = path.suffix.lower()
    if suffix in {".xlsx", ".xlsm"}:
        try:
            from openpyxl import load_workbook
        except ImportError:  # pragma: no cover
            raise RuntimeError("缺少 openpyxl，无法读 xlsx")
        wb = load_workbook(filename=path, data_only=True, read_only=True)
        ws = wb[sheet] if sheet and sheet in wb.sheetnames else wb.worksheets[0]
        rows_raw = [["" if c is None else c for c in r] for r in ws.iter_rows(values_only=True)]
        wb.close()
    else:
        text = None
        for enc in ("utf-8-sig", "utf-8", "gb18030"):
            try:
                text = path.read_text(encoding=enc)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            raise RuntimeError("无法解码文件")
        delim = "," if text.count(",") >= text.count("\t") else "\t"
        rows_raw = list(csv.reader(io.StringIO(text), delimiter=delim))

    if not rows_raw:
        return [], []
    header = [str(c).strip() for c in rows_raw[0]]
    rows: list[dict[str, Any]] = []
    for r in rows_raw[1:]:
        if all(str(c).strip() == "" for c in r):
            continue
        rows.append({header[i]: (r[i] if i < len(r) else "") for i in range(len(header))})
    return header, rows


def to_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    s = str(value).strip()
    if s in ("", "-", "--", "/", "N/A", "n/a", "None"):
        return None
    s = s.replace(",", "").replace("$", "").replace("%", "")
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def auto_keys(header: list[str], rows: list[dict[str, Any]], max_keys: int = 3) -> list[str]:
    """自动挑行键：优先文本型、区分度高的列。"""
    candidates = []
    total = max(len(rows), 1)
    for col in header:
        vals = [str(r.get(col, "")).strip() for r in rows]
        non_empty = [v for v in vals if v]
        if not non_empty:
            continue
        numeric = sum(1 for v in non_empty if to_decimal(v) is not None)
        text_ratio = 1 - numeric / len(non_empty)
        distinct = len(set(non_empty)) / max(len(non_empty), 1)
        if text_ratio > 0.8 and distinct > 0.2:
            candidates.append((distinct, col))
    candidates.sort(reverse=True)
    return [c for _, c in candidates[:max_keys]]


def compare(
    shadow_header: list[str],
    shadow_rows: list[dict[str, Any]],
    expected_header: list[str],
    expected_rows: list[dict[str, Any]],
    keys: list[str] | None,
    tolerance: Decimal,
) -> dict[str, Any]:
    keys = keys or auto_keys(expected_header, expected_rows)
    # 只比较两边都有的列
    common_cols = [c for c in expected_header if c in shadow_header and c not in keys]
    extra_shadow = [c for c in shadow_header if c not in expected_header]
    missing_cols = [c for c in expected_header if c not in shadow_header]

    def row_key(row: dict[str, Any]) -> str:
        return " | ".join(str(row.get(k, "")).strip() for k in keys)

    s_map = {row_key(r): r for r in shadow_rows}
    e_map = {row_key(r): r for r in expected_rows}

    missing_in_shadow = [k for k in e_map if k not in s_map]
    extra_in_shadow = [k for k in s_map if k not in e_map]

    cell_issues: list[dict[str, Any]] = []
    col_stats: dict[str, int] = {}
    compared = 0
    for key in e_map:
        if key not in s_map:
            continue
        for col in common_cols:
            ev, sv = e_map[key].get(col), s_map[key].get(col)
            compared += 1
            ed, sd = to_decimal(ev), to_decimal(sv)
            if ed is not None and sd is not None:
                diff = abs(ed - sd)
                scale = max(abs(ed), Decimal(1))
                if diff <= tolerance or diff / scale <= tolerance:
                    continue
            else:
                if str(ev).strip() == str(sv).strip():
                    continue
            col_stats[col] = col_stats.get(col, 0) + 1
            cell_issues.append({"row": key, "column": col, "expected": ev, "shadow": sv})

    return {
        "keys": keys,
        "common_columns": len(common_cols),
        "compared_cells": compared,
        "mismatched_cells": len(cell_issues),
        "columns_compared": common_cols,
        "columns_only_in_shadow": extra_shadow,
        "columns_missing_in_shadow": missing_cols,
        "rows_missing_in_shadow": missing_in_shadow,
        "rows_extra_in_shadow": extra_in_shadow,
        "mismatch_by_column": dict(sorted(col_stats.items(), key=lambda kv: -kv[1])),
        "samples": cell_issues[:200],
    }


def format_report(result: dict[str, Any], shadow: Path, expected: Path) -> str:
    lines = [
        "# 影子对账报告",
        "",
        f"- 影子报表：`{shadow}`",
        f"- 目标样本：`{expected}`",
        f"- 行键：{ '、'.join(result['keys']) or '（无，按整行比对）' }",
        f"- 比对列数：{result['common_columns']}，比对单元格：{result['compared_cells']}",
        f"- 差异单元格：**{result['mismatched_cells']}**",
        "",
    ]

    if result["columns_missing_in_shadow"]:
        lines.append("## 影子报表缺少的列")
        lines.append("")
        lines.extend(f"- `{c}`" for c in result["columns_missing_in_shadow"])
        lines.append("")

    if result["columns_only_in_shadow"]:
        lines.append("## 影子报表多出的列（目标格式里没有）")
        lines.append("")
        lines.extend(f"- `{c}`" for c in result["columns_only_in_shadow"])
        lines.append("")

    if result["rows_missing_in_shadow"]:
        lines.append("## 目标样本有、影子报表缺的行")
        lines.append("")
        lines.extend(f"- `{k}`" for k in result["rows_missing_in_shadow"][:50])
        if len(result["rows_missing_in_shadow"]) > 50:
            lines.append(f"- …（共 {len(result['rows_missing_in_shadow'])} 行）")
        lines.append("")

    if result["rows_extra_in_shadow"]:
        lines.append("## 影子报表多出的行")
        lines.append("")
        lines.extend(f"- `{k}`" for k in result["rows_extra_in_shadow"][:50])
        if len(result["rows_extra_in_shadow"]) > 50:
            lines.append(f"- …（共 {len(result['rows_extra_in_shadow'])} 行）")
        lines.append("")

    if result["mismatch_by_column"]:
        lines.append("## 按列的差异分布")
        lines.append("")
        lines.append("| 列 | 差异格数 |")
        lines.append("|---|---|")
        for col, cnt in result["mismatch_by_column"].items():
            lines.append(f"| `{col}` | {cnt} |")
        lines.append("")

        lines.append("## 差异样例（前 30 条）")
        lines.append("")
        lines.append("| 行键 | 列 | 目标值 | 影子值 |")
        lines.append("|---|---|---|---|")
        for item in result["samples"][:30]:
            lines.append(
                f"| `{item['row']}` | `{item['column']}` | {item['expected']} | {item['shadow']} |"
            )
        lines.append("")
        lines.append("> 差异不是「错误」，是**还不知道的规则**。逐条归类后写回 profile 的 rules / metrics。")
    elif result["mismatched_cells"] == 0:
        lines.append("## 结果")
        lines.append("")
        lines.append("数值层完全一致 ✅")
        lines.append("")

    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="影子报表 vs 目标样本 逐格对账")
    ap.add_argument("--shadow", required=True, type=Path)
    ap.add_argument("--expected", required=True, type=Path)
    ap.add_argument("--sheet", help="xlsx 时指定 sheet 名")
    ap.add_argument("--keys", help="行键列名，逗号分隔；缺省自动识别")
    ap.add_argument("--tolerance", default="0.000001", help="数值容差（绝对值与相对值同时适用）")
    ap.add_argument("--out", type=Path, help="把 markdown 报告写到文件")
    ap.add_argument("--max-print", type=int, default=20)
    args = ap.parse_args()

    for p in (args.shadow, args.expected):
        if not p.exists():
            print(f"找不到文件：{p}", file=sys.stderr)
            return 2

    try:
        s_header, s_rows = load_table(args.shadow, args.sheet)
        e_header, e_rows = load_table(args.expected, args.sheet)
    except Exception as exc:  # noqa: BLE001
        print(f"读取失败：{exc}", file=sys.stderr)
        return 2

    if not s_rows or not e_rows:
        print("有一侧没有任何数据行，无法对账", file=sys.stderr)
        return 2

    keys = [k.strip() for k in args.keys.split(",")] if args.keys else None
    result = compare(s_header, s_rows, e_header, e_rows, keys, Decimal(args.tolerance))
    report = format_report(result, args.shadow, args.expected)

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(report, encoding="utf-8")
        print(f"报告已写入 {args.out}")

    print(
        f"行键={result['keys']} 比对={result['compared_cells']} 格 "
        f"差异={result['mismatched_cells']} 格"
    )
    for col, cnt in list(result["mismatch_by_column"].items())[: args.max_print]:
        print(f"  {col}: {cnt}")
    if result["rows_missing_in_shadow"]:
        print(f"  影子缺行：{len(result['rows_missing_in_shadow'])}")
    if result["rows_extra_in_shadow"]:
        print(f"  影子多行：{len(result['rows_extra_in_shadow'])}")

    return 0 if result["mismatched_cells"] == 0 and not result["rows_missing_in_shadow"] else 1


if __name__ == "__main__":
    sys.exit(main())
