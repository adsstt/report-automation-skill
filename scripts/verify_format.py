#!/usr/bin/env python3
"""目标表头核对器（表头版本化）

客户会改表头。本工具负责三件事：
  1. 核对：profile 定义的列，目标工作簿里是否都有（支持 aliases 别名匹配）
  2. 报差异：缺了哪些列、多了哪些列、顺序是否变了
  3. 起草：从目标工作簿反向生成一份 sheets 列定义草稿（--emit-yaml），供 onboarding 用

用法：
    python scripts/verify_format.py --profile runtime/profile.x.yaml --target 客户工作簿.xlsx
    python scripts/verify_format.py --target 客户工作簿.xlsx --emit-yaml

退出码：0=全部匹配；1=有列缺失；2=用法/读取错误
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

from cli_compat import configure_console

configure_console()

try:
    import yaml
except ImportError:  # pragma: no cover
    print("缺少依赖 pyyaml", file=sys.stderr)
    sys.exit(2)

# 表头 → 指标/派生指标的建议映射（起草用，先派生再指标）
DERIVED_HINTS: list[tuple[str, str]] = [
    (r"安装成本|cpi", "cpa_install"),
    (r"注册成本|cpr", "cpa_reg"),
    (r"kyc.*成本", "cpa_kyc"),
    (r"ftd.*成本", "cpa_ftd"),
    (r"ftt.*成本", "cpa_ftt"),
    (r"注册转化率|注册率", "cvr_install_reg"),
    (r"kyc转化率", "cvr_reg_kyc"),
    (r"ftd转化率|入金率", "cvr_install_ftd"),
    (r"ftt转化率", "cvr_ftd_ftt"),
    (r"^roi$", "roi"),
]
METRIC_HINTS: list[tuple[str, str]] = [
    (r"花费|消耗|spend", "spend"),
    (r"展示|曝光|impression", "impressions"),
    (r"点击(?!率)|click", "clicks"),
    (r"安装次数|激活|install", "install"),
    (r"注册数|注册(?!成本|转化)", "注册"),
    (r"^kyc\b|kyc\(|kyc$", "KYC"),
    (r"ftd\s*actual|ftd（actual）", "FTD_Actual"),
    (r"ftd\s*ltv|ftd（ltv）", "FTD_LTV"),
    (r"^ftt|ftt\(|trade", "FTT"),
    (r"收入|revenue", "收入"),
    (r"日期", "$report_date"),
    (r"国家", "group.country"),
]


def norm(text: str) -> str:
    return re.sub(r"\s+", "", str(text)).strip().lower()


def load_book(path: Path) -> dict[str, list[str]]:
    from openpyxl import load_workbook

    wb = load_workbook(filename=path, data_only=True, read_only=True)
    out: dict[str, list[str]] = {}
    for ws in wb.worksheets:
        header: list[str] = []
        for row in ws.iter_rows(min_row=1, max_row=6, values_only=True):
            cells = [str(c).strip() for c in row if c not in (None, "")]
            if len(cells) < 2:
                continue
            chinese = sum(1 for c in cells if any("\u4e00" <= ch <= "\u9fff" for ch in c))
            numeric = sum(1 for c in cells if re.fullmatch(r"-?[\d.]+", c))
            if chinese >= 2 and numeric == 0 and len(cells) > len(header):
                header = cells
        if header:
            out[ws.title] = header
    wb.close()
    return out


def suggest_source(header: str) -> str:
    key = norm(header)
    for pattern, name in DERIVED_HINTS:
        if re.search(pattern, key):
            return f"derived.{name}"
    for pattern, name in METRIC_HINTS:
        if re.search(pattern, key):
            return name if name.startswith(("$", "group.")) else f"metrics.{name}"
    return "TODO"


def emit_yaml(book: dict[str, list[str]]) -> str:
    sheets = []
    for name, headers in book.items():
        cols = [{"header": h, "source": suggest_source(h), "format": "number:2"} for h in headers]
        sheets.append(
            {
                "id": re.sub(r"[^a-z0-9_]", "", name.lower())[:24] or "sheet",
                "match": "daily",
                "target_sheet": name,
                "role": "comparison" if re.search(r"环比|对比", name) else "history",
                "row_key": {"field": headers[0] if headers else "日期", "format": "text"},
                "placement": {"mode": "append_bottom", "same_key": "replace_in_place"},
                "column_match": "by_header",
                "group_labeling": "sparse" if re.search(r"环比|对比", name) else "repeat",
                "columns": cols,
            }
        )
    return yaml.safe_dump({"sheets": sheets}, allow_unicode=True, sort_keys=False, width=200)


def match_column(col: dict[str, Any], headers: list[str]) -> tuple[str | None, str | None]:
    """按 header 与 aliases 匹配实际表头，返回 (实际表头, 命中的别名写法)。"""
    target = norm(col["header"])
    for h in headers:
        if norm(h) == target:
            return h, None
    for alias in col.get("aliases") or []:
        for h in headers:
            if norm(h) == norm(alias):
                return h, alias
    # 退化匹配：去掉括号内容与单位后比较
    def strip_paren(text: str) -> str:
        return re.sub(r"[（(].*?[)）]", "", norm(text))

    for h in headers:
        if strip_paren(h) == strip_paren(col["header"]):
            return h, "fuzzy_strip_paren"
    return None, None


def verify(profile: dict[str, Any], book: dict[str, list[str]]) -> list[str]:
    problems: list[str] = []
    artifacts = [a for a in (profile.get("output") or {}).get("artifacts", []) if a.get("kind") == "xlsx_workbook"]
    if not artifacts:
        print("配方单里没有 xlsx_workbook 产物，无需核对表头")
        return problems

    for art in artifacts:
        for sheet in art.get("sheets", []):
            target_name = sheet.get("target_sheet")
            if not target_name:
                problems.append(f"sheet {sheet.get('id')} 未声明 target_sheet，无法核对")
                continue
            headers = book.get(target_name)
            if headers is None:
                problems.append(f"目标工作簿里找不到 sheet「{target_name}」")
                continue
            print(f"\n=== {target_name}（目标 {len(headers)} 列）")
            matched_positions: list[int] = []
            missing: list[str] = []
            for col in sheet.get("columns", []):
                actual, alias = match_column(col, headers)
                if actual is None:
                    missing.append(col["header"])
                    print(f"  ✗ 缺列：{col['header']}")
                else:
                    pos = headers.index(actual)
                    matched_positions.append(pos)
                    note = f"（别名命中 {alias}）" if alias and alias != "fuzzy_strip_paren" else (
                        "（模糊命中）" if alias == "fuzzy_strip_paren" else "")
                    print(f"  ✓ {col['header']} → {actual}{note}")
            extra = [h for i, h in enumerate(headers) if i not in matched_positions]
            if extra:
                print(f"  目标多出未定义的列 {len(extra)} 个：{', '.join(extra)}")
            if missing:
                problems.append(f"{target_name} 缺 {len(missing)} 列：{', '.join(missing)}")
            if matched_positions != sorted(matched_positions):
                problems.append(f"{target_name} 列顺序与配方单不一致（不影响按列名写入，但会影响肉眼核对）")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description="核对 profile 的列定义与目标工作簿表头是否一致")
    ap.add_argument("--profile", type=Path)
    ap.add_argument("--target", required=True, type=Path)
    ap.add_argument("--emit-yaml", action="store_true", help="从目标工作簿反向生成 sheets 列定义草稿")
    args = ap.parse_args()

    if not args.target.exists():
        print(f"找不到目标文件：{args.target}", file=sys.stderr)
        return 2
    book = load_book(args.target)

    if args.emit_yaml:
        sys.stdout.write(emit_yaml(book))
        return 0

    if not args.profile:
        print("需要 --profile，或使用 --emit-yaml", file=sys.stderr)
        return 2
    profile = yaml.safe_load(args.profile.read_text(encoding="utf-8"))
    problems = verify(profile, book)

    print()
    if problems:
        print(f"核对未通过（{len(problems)} 项）：")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("核对通过：配方单定义的列在目标工作簿中全部命中")
    return 0


if __name__ == "__main__":
    sys.exit(main())
