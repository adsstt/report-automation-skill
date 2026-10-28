#!/usr/bin/env python3
"""按配方单渲染客户 Excel 工作簿（P2）

设计原则：
  1. 永不修改模板原文件——先复制到目标路径，只在副本上写
  2. 按列名/别名定位列，不按位置（客户改过列顺序与列名）
  3. 追加历史行；同一天重跑原位替换，不产生重复行
  4. 环比 sheet 用「三行交错块」：基期行 → 本期行 → 环比行
  5. 稀疏分组：一组只在首行写维度值
  6. 样式与条件格式尽量从模板已有行继承

实现方式：先调用 generate_report.py 产出过程表 CSV，再据此渲染工作簿。
这样数值口径只有一处实现，渲染层只负责排版。

用法：
    python scripts/render_workbook.py \
        --profile runtime/profile.confirmed.yaml \
        --report-type daily --date 2026-10-25 \
        --source-file tt_ad=... --source-file af_ad=... \
        --out runtime/out/客户工作簿_20261025.xlsx \
        [--compare-date 2026-10-24] [--draft]

退出码：0=成功；1=被门禁拒绝或渲染失败；2=用法错误
"""

from __future__ import annotations

import argparse
import copy
import csv
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import date as date_cls
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from cli_compat import configure_console

configure_console()

try:
    import yaml
except ImportError:  # pragma: no cover
    print("缺少依赖 pyyaml", file=sys.stderr)
    sys.exit(2)

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import validate_profile as vp  # noqa: E402

# CSV 里的指标列名（来自 profile 的 metric 键） → 表达式里用的标识符
EXPR_ALIASES = {
    "spend": "花费",
    "impressions": "展示",
    "clicks": "点击",
    "install": "安装",
}
# 指标键 → 过程表 CSV 的实际列名（生成器对投放侧四个基础指标用固定中文列名）
CSV_COL = {"spend": "花费", "impressions": "展示", "clicks": "点击", "install": "安装"}
METRIC_LABELS = {
    "spend": "花费", "impressions": "展示", "clicks": "点击", "install": "安装",
    "registration": "注册", "kyc": "KYC", "first_deposit": "首充", "trade": "交易",
    "revenue": "收入",
}
GROUP_DIMENSIONS = {
    "date": "日期", "period": "周期",
    "platform": "平台",
    "country": "国家名称", "country_code": "国家代码",
    "event": "事件", "account": "账户名称", "account_id": "账户ID",
    "campaign": "Campaign名称", "campaign_id": "广告系列ID",
    "adgroup": "广告组名称", "adgroup_id": "广告组ID",
    "ad": "广告名称", "ad_id": "广告ID",
}


# ------------------------------------------------------------------ 表达式求值

TOKEN_RE = re.compile(r"\s*(?:(\d+(?:\.\d+)?)|([A-Za-z_\u4e00-\u9fff][\w\u4e00-\u9fff]*)|(.))")


def eval_expr(expr: str, values: dict[str, Any]) -> Decimal | None:
    """只支持 + - * / 与括号的极简表达式求值；分母为 0 返回 None。"""
    pyexpr = expr
    for name in sorted(values, key=len, reverse=True):
        pyexpr = pyexpr.replace(name, f"__v_{len(name)}_" + str(abs(hash(name)) % 100000))
    mapping: dict[str, Decimal] = {}
    for name in values:
        token = f"__v_{len(name)}_{abs(hash(name)) % 100000}"
        mapping[token] = decimal_or_zero(values[name])
    try:
        # 先替换成安全名，再用受限 eval
        result = eval(pyexpr, {"__builtins__": {}}, mapping)  # noqa: S307
    except ZeroDivisionError:
        return None
    except Exception:  # noqa: BLE001
        return None
    if result is None:
        return None
    try:
        return Decimal(str(result))
    except InvalidOperation:
        return None


def decimal_or_zero(value: Any) -> Decimal:
    if value is None:
        return Decimal(0)
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    text = str(value).strip().replace(",", "")
    if text in ("", "-", "/", "None"):
        return Decimal(0)
    try:
        return Decimal(text)
    except InvalidOperation:
        return Decimal(0)


def safe_spreadsheet_value(value: Any) -> Any:
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def safe_div(a: Decimal, b: Decimal) -> Decimal | None:
    return None if b == 0 else a / b


# ------------------------------------------------------------------ 数据准备

def run_generate(profile: Path, report_type: str, report_date: str,
                 source_files: list[str], tmpdir: Path) -> Path:
    """调用 generate_report.py 产出过程表 CSV，返回其路径。"""
    cmd = [
        sys.executable, str(HERE / "generate_report.py"),
        "--profile", str(profile),
        "--report-type", report_type,
        "--date", report_date,
        "--out-dir", str(tmpdir),
    ]
    for item in source_files:
        cmd += ["--source-file", item]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if proc.returncode != 0:
        raise RuntimeError(f"生成过程表失败：\n{proc.stdout}\n{proc.stderr}")
    token = report_date.replace("-", "")
    path = tmpdir / f"过程表_{token}.csv"
    if not path.exists():
        raise RuntimeError(f"未找到过程表 {path}")
    return path


def read_process(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def aggregate(rows: list[dict[str, str]], dims: list[str], report_date: str,
              metric_names: list[str]) -> list[dict[str, Any]]:
    """按 dims 分组求和。dim 支持 '日期'（常量）与中文列名（如 国家名称）。"""
    buckets: dict[tuple, dict[str, Decimal]] = {}
    order: list[tuple] = []
    for row in rows:
        key = tuple(
            str(row.get(d) or report_date)[:10] if d == "日期" else str(row.get(d) or "")
            for d in dims
        )
        if key not in buckets:
            buckets[key] = {m: Decimal(0) for m in metric_names}
            order.append(key)
        for m in metric_names:
            buckets[key][m] += decimal_or_zero(row.get(CSV_COL.get(m, m)))
    return [{"__key__": key, **buckets[key]} for key in order]


def add_overall_group(records: list[dict[str, Any]], rows: list[dict[str, str]],
                      dims: list[str], metric_names: list[str],
                      total_config: dict[str, Any]) -> list[dict[str, Any]]:
    """Add a configured overall group without duplicating process-table rows."""
    if not total_config.get("enabled") or not dims or not rows:
        return records
    label_column = str(total_config.get("label_column") or "")
    if label_column not in dims:
        return records
    key = ["" for _ in dims]
    key[dims.index(label_column)] = str(total_config.get("label") or "总计")
    totals = {name: Decimal(0) for name in metric_names}
    for row in rows:
        for name in metric_names:
            totals[name] += decimal_or_zero(row.get(CSV_COL.get(name, name)))
    overall = {"__key__": tuple(key), **totals}
    return ([overall, *records] if total_config.get("position", "bottom") == "top"
            else [*records, overall])


# ------------------------------------------------------------------ 表头定位

def find_sheet(wb, name: str):
    if name in wb.sheetnames:
        return wb[name]
    # 容忍全角/半角括号与空格差异
    def norm(t: str) -> str:
        return re.sub(r"[\s（）()]", "", str(t))

    for ws in wb.worksheets:
        if norm(ws.title) == norm(name):
            return ws
    return None


def header_map(ws, header_row: int, columns: list[dict[str, Any]]) -> dict[str, int]:
    """列名 → 列号（1-based），支持别名与括号剥离的模糊匹配。"""
    actual = {}
    for col in range(1, ws.max_column + 1):
        value = ws.cell(row=header_row, column=col).value
        if value is not None and str(value).strip():
            actual[str(value).strip()] = col

    def norm(t: str) -> str:
        return re.sub(r"[\s（）()]", "", str(t)).lower()

    resolved: dict[str, int] = {}
    for definition in columns:
        want = definition["header"]
        variants = [want, *(definition.get("aliases") or [])]
        for variant in variants:
            if variant in actual:
                resolved[want] = actual[variant]
                break
            for text, col in actual.items():
                if norm(text) == norm(variant):
                    resolved[want] = col
                    break
            if want in resolved:
                break
    return resolved


def detect_header_row(ws, expect: list[str]) -> int:
    best_row, best_hits = 1, -1
    for row in range(1, min(ws.max_row, 8) + 1):
        hits = 0
        for col in range(1, min(ws.max_column, 60) + 1):
            value = ws.cell(row=row, column=col).value
            if value is None:
                continue
            text = str(value).strip()
            if any(text == e or text.replace(" ", "") == e.replace(" ", "") for e in expect):
                hits += 1
        if hits > best_hits:
            best_row, best_hits = row, hits
    return best_row


# ------------------------------------------------------------------ 渲染

def style_from(ws, src_row: int, dst_row: int, max_col: int) -> None:
    for col in range(1, max_col + 1):
        source = ws.cell(row=src_row, column=col)
        target = ws.cell(row=dst_row, column=col)
        if source.has_style:
            target._style = copy.copy(source._style)


def extend_conditional_formatting(ws, sample_row: int, start_row: int, end_row: int, max_col: int) -> int:
    """把模板里已有的条件格式规则复制到新行（只处理 expression/cellIs 两类，其余跳过）。

    注意：openpyxl 的 ws.conditional_formatting.add() 第一个参数必须是范围字符串，
    传 MultiCellRange 会在保存时崩。
    """
    from openpyxl.formatting.rule import Rule
    from openpyxl.utils import get_column_letter

    if end_row < start_row:
        return 0
    rng = f"A{start_row}:{get_column_letter(max_col)}{end_row}"
    copied = 0
    try:
        existing = list(ws.conditional_formatting)
    except Exception:  # noqa: BLE001
        return 0
    for cf in existing:
        for rule in list(cf.rules):
            if rule.type not in {"expression", "cellIs", "containsText"}:
                continue
            try:
                new_rule = Rule(
                    type=rule.type,
                    formula=list(rule.formula or []),
                    dxf=copy.deepcopy(rule.dxf),
                    operator=rule.operator,
                    text=rule.text,
                )
                new_rule.priority = rule.priority
                new_rule.stopIfTrue = rule.stopIfTrue
                ws.conditional_formatting.add(rng, new_rule)
                copied += 1
            except Exception:  # noqa: BLE001
                continue
            if copied >= 60:
                return copied
    return copied


def write_rows(ws, row_index: int, values: dict[str, Any], colmap: dict[str, int],
               headers: list[str]) -> int:
    for header in headers:
        col = colmap.get(header)
        if col is None:
            continue
        ws.cell(row=row_index, column=col, value=safe_spreadsheet_value(values.get(header)))
    return row_index + 1


def clear_same_key(ws, header_row: int, row_key_col: int, key_value: Any, last_row: int) -> int:
    """删除与 key 相同的已有数据行，返回删除行数。"""
    doomed = []
    for row in range(header_row + 1, last_row + 1):
        cell = ws.cell(row=row, column=row_key_col).value
        if cell is None:
            continue
        if isinstance(key_value, datetime) and isinstance(cell, datetime):
            if cell.date() == key_value.date():
                doomed.append(row)
        elif str(cell).strip() == str(key_value).strip():
            doomed.append(row)
    for row in reversed(doomed):
        ws.delete_rows(row, 1)
    return len(doomed)


def clear_comparison_blocks(ws, header_row: int, date_col: int, label_col: int,
                            base_date: str, cur_date: str) -> int:
    """删除已有的同一对比段环比块（基期=base_date、本期=cur_date），返回删除行数。"""
    doomed: list[int] = []
    row = header_row + 1
    while row + 2 <= ws.max_row:
        label = ws.cell(row=row + 2, column=label_col).value
        if str(label).strip() == "环比":
            b = ws.cell(row=row, column=date_col).value
            c = ws.cell(row=row + 1, column=date_col).value
            if str(b)[:10] == base_date and str(c)[:10] == cur_date:
                doomed.extend([row, row + 1, row + 2])
            row += 3
        else:
            row += 1
    for r in reversed(doomed):
        ws.delete_rows(r, 1)
    return len(doomed)


def main() -> int:
    ap = argparse.ArgumentParser(description="按配方单渲染客户 Excel 工作簿")
    ap.add_argument("--profile", required=True, type=Path)
    ap.add_argument("--report-type", required=True)
    ap.add_argument("--date", required=True)
    ap.add_argument("--source-file", action="append", default=[])
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--compare-date", help="环比基期日期，缺省为报表日期前一天")
    ap.add_argument("--compare-source-file", action="append", default=[], help="基期源文件，格式 id=路径")
    ap.add_argument("--draft", action="store_true", help="生成诊断草稿（落在 _drafts，不碰正式产物）")
    args = ap.parse_args()

    if vp.is_bundled_example(args.profile):
        print("examples/ 下的 profile 是只读样板，不能直接生成正式工作簿；请先复制并重新验证。", file=sys.stderr)
        return 1

    profile = vp.load_profile(args.profile)
    issues = vp.schema_issues(profile) + vp.semantic_issues(profile)
    fails = [i for i in issues if i.level == "FAIL"]
    if fails:
        print("门禁拒绝：配方单还有未解决问题，不能出正式工作簿。", file=sys.stderr)
        for f in fails:
            print(f"  {f}", file=sys.stderr)
        return 1

    artifacts = [a for a in profile["output"]["artifacts"] if a.get("kind") == "xlsx_workbook"]
    if not artifacts:
        print("配方单里没有 xlsx_workbook 产物", file=sys.stderr)
        return 2
    report_config = next(
        (item for item in profile.get("report_types") or [] if item.get("id") == args.report_type),
        None,
    )
    if report_config is None:
        print(f"不存在的报表类型：{args.report_type}", file=sys.stderr)
        return 2
    artifact = next(
        (item for item in artifacts if any(sheet.get("match") == args.report_type for sheet in item.get("sheets") or [])),
        None,
    )
    if artifact is None:
        print(f"没有 xlsx_workbook 产物匹配报表类型：{args.report_type}", file=sys.stderr)
        return 2
    sample_path = Path(artifact["target"]["sample"])
    # 模板路径支持相对 profile 所在目录（示例包里通常是 samples/target_sample.xlsx）
    template = sample_path if sample_path.is_absolute() else (args.profile.parent / sample_path)
    if not template.exists():
        print(f"模板不存在：{template}", file=sys.stderr)
        print("提示：示例包的 target.sample 是相对路径，需把目标工作簿放到 profile 同目录的 samples/ 下，"
              "或在 profile 里改成绝对路径。", file=sys.stderr)
        return 2

    out_path = args.out
    if args.draft:
        drafts = (profile.get("output") or {}).get("drafts") or {}
        draft_dir = Path(drafts.get("dir") or "_drafts")
        out_path = draft_dir / f"{out_path.stem}_DRAFT{out_path.suffix}"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    staged_path = out_path.with_name(f".{out_path.stem}.tmp{out_path.suffix}")
    shutil.copyfile(template, staged_path)       # 校验全部通过后才发布正式文件
    compare_date = args.compare_date or (
        date_cls.fromisoformat(args.date) - timedelta(days=1)
    ).isoformat()

    # Keep intermediate CSVs beside the requested output. This also works in
    # sandboxed hosts where the operating-system temp directory is read-only.
    with tempfile.TemporaryDirectory(dir=out_path.parent) as tmp:
        tmpdir = Path(tmp)
        process_csv = run_generate(args.profile, args.report_type, args.date, args.source_file, tmpdir)
        rows = read_process(process_csv)
        base_rows: list[dict[str, str]] = []
        base_display = compare_date
        current_display = args.date
        if (report_config.get("time_scope") or {}).get("mode") == "multi_period":
            comparison = report_config.get("comparison") or {}
            windows = (report_config.get("time_scope") or {}).get("windows") or []
            base_label = comparison.get("base") or (windows[0] if windows else "previous_period")
            target_label = comparison.get("target") or (windows[-1] if windows else "current_period")
            base_rows = [row for row in rows if row.get("周期") == base_label]
            rows = [row for row in rows if row.get("周期") == target_label]
            base_display = base_label
            current_display = target_label
        elif args.compare_source_file:
            base_csv = run_generate(args.profile, args.report_type, compare_date,
                                    args.compare_source_file, tmpdir / "base")
            base_rows = read_process(base_csv)

        from openpyxl import load_workbook

        wb = load_workbook(staged_path)
        metrics_cfg = profile["metrics"]
        derived_cfg = profile.get("derived") or {}
        metric_names = [m for m in metrics_cfg]
        unknown_report: list[str] = []

        render_failures: list[str] = []
        matching_sheets = [sheet for sheet in artifact["sheets"] if sheet.get("match") == args.report_type]
        if not matching_sheets:
            render_failures.append(f"没有匹配报表类型 {args.report_type} 的 sheet 定义")
        for sheet in matching_sheets:
            ws = find_sheet(wb, sheet.get("target_sheet") or "")
            if ws is None:
                render_failures.append(f"模板里找不到 sheet「{sheet.get('target_sheet')}」")
                continue
            headers = [c["header"] for c in sheet["columns"]]
            header_row = detect_header_row(ws, headers)
            if sheet.get("column_match") == "by_position":
                colmap = {
                    column["header"]: int(column.get("column_order", index)) + 1
                    for index, column in enumerate(sheet["columns"])
                }
            else:
                colmap = header_map(ws, header_row, sheet["columns"])
            missing = [h for h in headers if h not in colmap]
            if missing:
                render_failures.append(f"{ws.title}: 模板缺列 {missing}")
                continue

            group_by = sheet.get("group_by") if "group_by" in sheet else ["日期"]
            dims = [GROUP_DIMENSIONS.get(g, "国家名称" if g == "国家" else g) for g in group_by]
            filtered = rows
            flt = sheet.get("filter") or {}
            if flt.get("event"):
                filtered = [r for r in rows if str(r.get("事件")) == flt["event"]]

            agg = aggregate(filtered, dims, args.date, metric_names)
            is_comparison = sheet.get("role") == "comparison"

            if is_comparison:
                base_filtered = base_rows
                if flt.get("event"):
                    base_filtered = [r for r in base_rows if str(r.get("事件")) == flt["event"]]
                base_agg = aggregate(base_filtered, dims, base_display, metric_names)
                total_config = sheet.get("total_row") or {}
                agg = add_overall_group(agg, filtered, dims, metric_names, total_config)
                base_agg = add_overall_group(base_agg, base_filtered, dims, metric_names, total_config)
                base_map = {tuple(r["__key__"]): r for r in base_agg}
                if sheet.get("placement", {}).get("mode") == "replace_sheet" and ws.max_row > header_row:
                    ws.delete_rows(header_row + 1, ws.max_row - header_row)
                date_col = colmap.get(headers[0])
                label_col = colmap.get(headers[1]) if len(headers) > 1 else None
                if sheet.get("placement", {}).get("mode") != "replace_sheet" and date_col and label_col:
                    removed = clear_comparison_blocks(
                        ws, header_row, date_col, label_col,
                        base_display, current_display)
                    if removed:
                        print(f"{ws.title}: 同对比段已有 {removed} 行，已原位替换")
                target = ws.max_row + 1
                sample_row = max(header_row + 1, ws.max_row - 1)
                written = 0
                block = (sheet.get("comparison_layout") or {}).get("block") or {}
                phases = block.get("rows") or ["base", "current", "delta"]
                for rec in agg:
                    key = tuple(rec["__key__"])
                    base_record = base_map.get(key)
                    base = base_record or {m: None for m in metric_names}
                    for phase in phases:
                        values = {}
                        for header in headers:
                            coldef = next(c for c in sheet["columns"] if c["header"] == header)
                            src = coldef["source"]
                            if src == "$report_date":
                                values[header] = (
                                    None if phase in {"delta", "delta_abs", "delta_pct"}
                                    else (base_display if phase == "base" else current_display)
                                )
                            elif src.startswith("$const_"):
                                values[header] = (sheet.get("constant_columns") or {}).get(header, "")
                            elif src.startswith("group."):
                                # 稀疏分组：维度值只写在每组首行（基期行），本期行与环比行留空
                                dim = src.split(".", 1)[1]
                                mapping = {
                                    "date": "日期", "period": "周期",
                                    "country": "国家名称", "country_code": "国家代码",
                                    "platform": "平台",
                                    "event": "事件", "account": "账户名称", "account_id": "账户ID",
                                    "campaign": "Campaign名称", "campaign_id": "广告系列ID",
                                    "adgroup": "广告组名称", "adgroup_id": "广告组ID",
                                    "ad": "广告名称", "ad_id": "广告ID",
                                }
                                target_dim = mapping.get(dim, dim)
                                if phase == "base" and target_dim in dims:
                                    values[header] = rec["__key__"][dims.index(target_dim)]
                                else:
                                    values[header] = None
                            elif src.startswith("metrics."):
                                name = src.split(".", 1)[1]
                                old_value = None if base_record is None else decimal_or_zero(base.get(name))
                                current_value = decimal_or_zero(rec.get(name))
                                if phase == "base":
                                    values[header] = float(old_value) if old_value is not None else None
                                elif phase == "current":
                                    values[header] = float(current_value)
                                elif phase == "delta_abs":
                                    values[header] = float(current_value - old_value) if old_value is not None else block.get("null_symbol", "N/A")
                                else:
                                    if old_value in (None, 0):
                                        values[header] = block.get("null_symbol", "N/A")
                                    else:
                                        values[header] = float(current_value / old_value - 1)
                            elif src.startswith("derived."):
                                name = src.split(".", 1)[1]
                                expr = (derived_cfg.get(name) or {}).get("expr", "")
                                cur = eval_expr(expr, rec)
                                old = eval_expr(expr, base) if base_record is not None else None
                                if phase == "base":
                                    values[header] = float(old) if old is not None else None
                                elif phase == "current":
                                    values[header] = float(cur) if cur is not None else None
                                elif phase == "delta_abs":
                                    values[header] = float(cur - old) if cur is not None and old is not None else block.get("null_symbol", "N/A")
                                else:
                                    delta = safe_div(cur, old) if (cur is not None and old not in (None, 0)) else None
                                    values[header] = float(delta - 1) if delta is not None else block.get("null_symbol", "N/A")
                        # 对比阶段标识写在第二列
                        label_source = sheet["columns"][1]["source"] if len(sheet["columns"]) > 1 else ""
                        if label_source.startswith("$const_") or phase in {"delta", "delta_abs", "delta_pct"}:
                            label_col = colmap.get(headers[1]) if len(headers) > 1 else None
                            if label_col:
                                values[headers[1]] = {
                                    "base": "上期", "current": "本期", "delta_abs": "绝对变化",
                                    "delta": "环比", "delta_pct": "环比",
                                }[phase]
                        style_from(ws, sample_row, target, ws.max_column)
                        written_row = target
                        target = write_rows(ws, target, values, colmap, headers)
                        for coldef in sheet["columns"]:
                            if not coldef["source"].startswith(("metrics.", "derived.")):
                                continue
                            column = colmap.get(coldef["header"])
                            if column:
                                is_percent_value = str(coldef.get("format") or "").startswith("percent")
                                ws.cell(row=written_row, column=column).number_format = (
                                    "0.00%" if phase in {"delta", "delta_pct"} or is_percent_value else "#,##0.00"
                                )
                        written += 1
                extend_conditional_formatting(ws, sample_row, ws.max_row - written + 1, ws.max_row, ws.max_column)
                if sheet.get("target_sheet") == "管理摘要" and agg:
                    intent = profile.get("report_intent") or {}
                    primary = next((name for name in intent.get("primary_kpis") or [] if name in metrics_cfg), None)
                    if primary:
                        current_record = agg[0]
                        key = tuple(current_record["__key__"])
                        previous_record = base_map.get(key)
                        current_value = decimal_or_zero(current_record.get(primary))
                        label = METRIC_LABELS.get(primary, primary)
                        if previous_record is None:
                            conclusion = f"本期{label}为 {current_value:.2f}；缺少上一等长周期数据，环比为 N/A。"
                        else:
                            previous_value = decimal_or_zero(previous_record.get(primary))
                            absolute = current_value - previous_value
                            if previous_value == 0:
                                conclusion = f"本期{label}为 {current_value:.2f}，上期为 0；变化率为 N/A。"
                            else:
                                ratio = current_value / previous_value - 1
                                conclusion = (
                                    f"本期{label}为 {current_value:.2f}，上期为 {previous_value:.2f}，"
                                    f"绝对变化 {absolute:+.2f}，环比 {ratio:+.2%}。"
                                )
                        ws["B5"] = conclusion
                    for chart in ws._charts:
                        chart.anchor = f"A{ws.max_row + 2}"
                print(f"{ws.title}: 写入环比块 {written} 行（{written // max(len(phases), 1)} 组）")
                continue

            # ---- 普通历史 sheet ----
            row_key_field = sheet["row_key"]["field"]
            row_key_col = colmap.get(row_key_field)
            if row_key_col is None:
                render_failures.append(f"{ws.title}: 找不到行键列 {row_key_field}")
                continue
            if sheet.get("placement", {}).get("same_key") == "replace_in_place":
                removed = clear_same_key(ws, header_row, row_key_col, args.date, ws.max_row)
                if removed:
                    print(f"{ws.title}: 同日期已有 {removed} 行，已原位替换")
            if sheet.get("placement", {}).get("mode") == "replace_sheet" and ws.max_row > header_row:
                ws.delete_rows(header_row + 1, ws.max_row - header_row)

            target = ws.max_row + 1
            sample_row = max(header_row + 1, ws.max_row - 1)
            written = 0
            if not agg:
                # 单行 sheet（如 TT日报）：整表汇总成一行
                merged: dict[str, Decimal] = {m: Decimal(0) for m in metric_names}
                for row in filtered:
                    for m in metric_names:
                        merged[m] += decimal_or_zero(row.get(CSV_COL.get(m, m)))
                agg = [{"__key__": (), **merged}]
                dims = []
            for rec in agg:
                values = {}
                for coldef in sheet["columns"]:
                    header, src = coldef["header"], coldef["source"]
                    if src == "$report_date":
                        values[header] = args.date
                    elif src.startswith("$const_"):
                        values[header] = (sheet.get("constant_columns") or {}).get(header, "")
                    elif src.startswith("group."):
                        dim = src.split(".", 1)[1]
                        mapping = {
                            "date": "日期", "period": "周期",
                            "country": "国家名称", "country_code": "国家代码",
                            "platform": "平台",
                            "event": "事件", "account": "账户名称", "account_id": "账户ID",
                            "campaign": "Campaign名称", "campaign_id": "广告系列ID",
                            "adgroup": "广告组名称", "adgroup_id": "广告组ID",
                            "ad": "广告名称", "ad_id": "广告ID",
                        }
                        values[header] = rec["__key__"][dims.index(mapping.get(dim, dim))] if dims and mapping.get(dim, dim) in dims else None
                    elif src.startswith("metrics."):
                        name = src.split(".", 1)[1]
                        values[header] = float(rec.get(name) or 0)
                    elif src.startswith("derived."):
                        name = src.split(".", 1)[1]
                        expr = (derived_cfg.get(name) or {}).get("expr", "")
                        val = eval_expr(expr, rec)
                        values[header] = float(val) if val is not None else None
                style_from(ws, sample_row, target, ws.max_column)
                written_row = target
                target = write_rows(ws, target, values, colmap, headers)
                for coldef in sheet["columns"]:
                    if str(coldef.get("format") or "").startswith("percent"):
                        column = colmap.get(coldef["header"])
                        if column:
                            ws.cell(row=written_row, column=column).number_format = "0.00%"
                written += 1

            total = sheet.get("total_row") or {}
            if total.get("enabled"):
                label_col = colmap.get(total.get("label_column") or row_key_field)
                values = {h: None for h in headers}
                if label_col:
                    for h in headers:
                        if colmap.get(h) == label_col:
                            values[h] = total.get("label") or "总计"
                sums: dict[str, Decimal] = {m: Decimal(0) for m in metric_names}
                for row in filtered:
                    for m in metric_names:
                        sums[m] += decimal_or_zero(row.get(CSV_COL.get(m, m)))
                for coldef in sheet["columns"]:
                    src = coldef["source"]
                    if src.startswith("metrics."):
                        name = src.split(".", 1)[1]
                        values[coldef["header"]] = float(sums.get(name) or 0)
                    elif src.startswith("derived."):
                        name = src.split(".", 1)[1]
                        expr = (derived_cfg.get(name) or {}).get("expr", "")
                        val = eval_expr(expr, sums)
                        values[coldef["header"]] = float(val) if val is not None else None
                style_from(ws, max(header_row + 1, target - 1), target, ws.max_column)
                write_rows(ws, target, values, colmap, headers)
                print(f"{ws.title}: 写入 {written} 行 + 总计行")
            else:
                print(f"{ws.title}: 写入 {written} 行")

        if render_failures:
            wb.close()
            staged_path.unlink(missing_ok=True)
            print("工作簿渲染失败：", file=sys.stderr)
            for failure in render_failures:
                print(f"  {failure}", file=sys.stderr)
            return 1
        wb.save(staged_path)
        wb.close()
        staged_path.replace(out_path)
        print(f"\n工作簿已生成：{out_path}")
        print(f"模板原件未被修改：{template}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
