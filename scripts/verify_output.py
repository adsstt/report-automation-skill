#!/usr/bin/env python3
"""数值层对账（出稿后自检）

检查生成的 CSV 是否"能交"：
  1. 过程表 / 结果表都存在且非空
  2. 按结果表的行过滤口径对账（花费、安装等可加指标）
  3. 事件 Unknown 视为阻断（说明事件分类规则没覆盖到当前命名）
  4. 国家 Unknown 给出告警（不阻断，但要在交付说明里写清）
  5. 关键指标不出现负数

用法：
    python scripts/verify_output.py --profile <profile.yaml> --out-dir runtime/out --date 2026-10-25

退出码：0=通过；1=有问题；2=用法错误
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    print("缺少依赖 pyyaml", file=sys.stderr)
    sys.exit(2)

ADDITIVE = ("花费", "展示", "点击", "安装", "注册", "KYC", "FTT", "FTD_LTV", "FTD_Actual", "收入")


def dec(value) -> Decimal:
    if value is None:
        return Decimal(0)
    try:
        return Decimal(str(value).strip() or "0")
    except InvalidOperation:
        return Decimal(0)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def main() -> int:
    ap = argparse.ArgumentParser(description="数值层对账")
    ap.add_argument("--profile", required=True, type=Path)
    ap.add_argument("--out-dir", required=True, type=Path)
    ap.add_argument("--date", required=True)
    ap.add_argument("--tolerance", default="0.01")
    args = ap.parse_args()

    profile = yaml.safe_load(args.profile.read_text(encoding="utf-8"))
    token = args.date.replace("-", "")
    process_path = args.out_dir / f"过程表_{token}.csv"
    result_path = args.out_dir / f"结果表_{token}.csv"
    for p in (process_path, result_path):
        if not p.exists():
            print(f"缺少产物：{p}", file=sys.stderr)
            return 2

    process = read_csv(process_path)
    result = read_csv(result_path)
    problems: list[str] = []

    print(f"过程表 {len(process)} 行 / 结果表 {len(result)} 行")
    if not process:
        problems.append("过程表为空")
    if not result:
        problems.append("结果表为空")

    # 行过滤按结果表的分组粒度应用（逐行筛会漏掉同组其他行的金额）
    positive_fields: list[str] = []
    result_group_by: list[str] = []
    for art in (profile.get("output") or {}).get("artifacts", []):
        if art.get("kind") == "csv" and art.get("role") == "result_table":
            m = re.match(r"positive_any\(([^)]*)\)", art.get("rows_filter") or "")
            if m:
                positive_fields = [x.strip() for x in m.group(1).split(",") if x.strip()]
            result_group_by = art.get("group_by") or []
    reconcilable = process
    if positive_fields and result_group_by:
        included = set()
        for row in process:
            key = tuple(str(row.get(g) or "") for g in result_group_by)
            if key in included:
                continue
            included.add(key)
        # 计算每个分组是否通过过滤
        group_sums: dict[tuple, dict[str, Decimal]] = {}
        for row in process:
            key = tuple(str(row.get(g) or "") for g in result_group_by)
            sums = group_sums.setdefault(key, {f: Decimal(0) for f in positive_fields})
            for f in positive_fields:
                sums[f] += dec(row.get(f))
        keep = {k for k, s in group_sums.items() if any(v > 0 for v in s.values())}
        reconcilable = [
            row for row in process
            if tuple(str(row.get(g) or "") for g in result_group_by) in keep
        ]
        print(f"结果表带行过滤，参与对账的过程行 {len(reconcilable)}/{len(process)}")

    tol = Decimal(args.tolerance)
    for name in ADDITIVE:
        if name not in (process[0] if process else {}):
            continue
        p_total = sum(dec(r.get(name)) for r in reconcilable)
        r_total = sum(dec(r.get(name)) for r in result)
        if abs(p_total - r_total) > tol:
            problems.append(f"{name}：过程表 {p_total} ≠ 结果表 {r_total}")
        elif p_total != 0:
            print(f"  ✓ {name} 对账一致（{p_total}）")

    # 事件 Unknown
    unknown_events = [r for r in process if str(r.get("事件")) == "Unknown"]
    if unknown_events:
        samples = sorted({str(r.get("Campaign名称")) for r in unknown_events})[:5]
        problems.append(
            f"事件 Unknown {len(unknown_events)} 行（说明事件分类规则没覆盖当前命名）：{samples}"
        )
    else:
        print("  ✓ 无 Unknown 事件")

    # 国家 Unknown（告警）
    unknown_country = [r for r in process if str(r.get("国家名称")) == "Unknown"]
    if unknown_country:
        samples = sorted({str(r.get("Campaign名称")) for r in unknown_country})[:5]
        print(f"  ⚠ 国家 Unknown {len(unknown_country)} 行：{samples}")
        print("    → 补 references/lookups 或在 profile.country_lookup.map 加客户写法")

    # 负数
    for name in ADDITIVE:
        if name not in (process[0] if process else {}):
            continue
        negatives = [r for r in process if dec(r.get(name)) < 0]
        if negatives:
            problems.append(f"{name} 出现负数 {len(negatives)} 行")

    print()
    if problems:
        print(f"未通过 {len(problems)} 项：")
        for p in problems:
            print(f"  ✗ {p}")
        return 1
    print("数值层对账通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
