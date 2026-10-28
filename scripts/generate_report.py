#!/usr/bin/env python3
"""按配方单出报表（P1 版：只产出过程表 / 结果表 CSV）

引擎里不允许出现按平台分支的逻辑。平台差异全部来自两处：
  - references/platforms/*.md（知识，供人/AI 阅读）
  - profile.yaml 的 sources / metrics / rules（配置）

本版本范围：
  - 读源文件 → 规范化 → 关联 → 聚合 → 写 CSV → 对账
  - Excel 工作簿渲染在 P2（遇到 xlsx 产物会跳过并提示，不算失败）

用法：
    python scripts/generate_report.py --profile <profile.yaml> --report-type daily --date 2026-10-24 \
        --source-file tt_ad=<path.csv> --source-file af_ad=<path.csv> ... --out-dir runtime/out

退出码：0=成功；1=被门禁拒绝或校验失败；2=用法/读取错误
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from cli_compat import configure_console

configure_console()

try:
    import yaml
except ImportError:  # pragma: no cover
    print("缺少依赖 pyyaml，请先执行: pip install pyyaml", file=sys.stderr)
    sys.exit(2)

sys.path.insert(0, str(Path(__file__).resolve().parent))
import validate_profile as vp  # noqa: E402
from country_resolver import CountryResolver  # noqa: E402


# ---------------------------------------------------------------- 基础工具

def dec(value: Any) -> Decimal:
    if value is None:
        return Decimal(0)
    s = str(value).strip().replace(",", "").replace("$", "")
    if s in ("", "-", "--", "/", "None"):
        return Decimal(0)
    try:
        return Decimal(s)
    except InvalidOperation:
        return Decimal(0)


def fmt(value: Decimal | None) -> str:
    if value is None:
        return ""
    return str(value)


def read_source(path: Path, source: dict[str, Any]) -> list[dict[str, Any]]:
    """Read a configured CSV/XLSX source using the profile layout contract."""
    layout = source.get("layout") or {}
    header_row = int(layout.get("header_row") or 0)
    if path.suffix.lower() in {".xlsx", ".xlsm"}:
        try:
            from openpyxl import load_workbook
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("缺少 openpyxl，无法读取 Excel 源文件") from exc
        workbook = load_workbook(path, data_only=True, read_only=True)
        sheet_ref = layout.get("sheet", 0)
        worksheet = workbook.worksheets[sheet_ref] if isinstance(sheet_ref, int) else workbook[sheet_ref]
        rows = worksheet.iter_rows(min_row=header_row + 1, values_only=True)
        headers = ["" if value is None else str(value).strip() for value in next(rows)]
        result = [
            {headers[index]: value for index, value in enumerate(row) if index < len(headers) and headers[index]}
            for row in rows
            if any(value not in (None, "") for value in row)
        ]
        workbook.close()
        return result

    encoding = layout.get("encoding") or "utf-8-sig"
    delimiter = layout.get("csv_delimiter") or ","
    with path.open(encoding=encoding, newline="") as handle:
        for _ in range(header_row):
            next(handle, None)
        return list(csv.DictReader(handle, delimiter=delimiter))


def num_out(value: Decimal) -> Any:
    """整数输出为 int，其余为 float——与现有产物的写法保持一致。"""
    if value == value.to_integral_value():
        return int(value)
    return float(value)


def safe_spreadsheet_value(value: Any) -> Any:
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def safe_div(a: Decimal, b: Decimal) -> Decimal | None:
    return None if b == 0 else a / b


def report_dates(report_type: dict[str, Any], report_date: str) -> set[str]:
    """Expand a report contract into the source dates that belong to the run."""
    anchor = date.fromisoformat(report_date)
    scope = report_type.get("time_scope") or {}
    mode = scope.get("mode", "single_period")
    period = scope.get("period", "day")
    if mode == "rolling_window":
        days = int(scope.get("rolling_days") or 1)
        return {(anchor - timedelta(days=offset)).isoformat() for offset in range(days)}
    if period == "custom":
        start = date.fromisoformat(scope["start"])
        end = date.fromisoformat(scope["end"])
        days = (end - start).days + 1
        starts = [start]
        if mode == "multi_period" and "previous_custom" in (scope.get("windows") or []):
            starts.insert(0, start - timedelta(days=days))
        return {
            (window_start + timedelta(days=offset)).isoformat()
            for window_start in starts
            for offset in range(days)
        }
    if period == "week":
        current_start = anchor - timedelta(days=anchor.weekday())
        starts = [current_start]
        if mode == "multi_period" and "previous_week" in (scope.get("windows") or []):
            starts.insert(0, current_start - timedelta(days=7))
        return {
            (start + timedelta(days=offset)).isoformat()
            for start in starts
            for offset in range(7)
        }
    if period == "month":
        current_start = anchor.replace(day=1)
        starts = [current_start]
        if mode == "multi_period" and "previous_month" in (scope.get("windows") or []):
            previous_end = current_start - timedelta(days=1)
            starts.insert(0, previous_end.replace(day=1))
        result: set[str] = set()
        for start in starts:
            cursor = start
            while cursor.month == start.month:
                result.add(cursor.isoformat())
                cursor += timedelta(days=1)
        return result
    if mode == "multi_period" and "previous_day" in (scope.get("windows") or []):
        return {report_date, (anchor - timedelta(days=1)).isoformat()}
    return {report_date}


def report_period_label(report_type: dict[str, Any], report_date: str, source_date: str) -> str:
    scope = report_type.get("time_scope") or {}
    mode = scope.get("mode", "single_period")
    if mode == "single_period":
        return "current"
    anchor = date.fromisoformat(report_date)
    observed = date.fromisoformat(source_date[:10])
    period = scope.get("period", "day")
    if mode == "rolling_window":
        if (report_type.get("comparison") or {}).get("mode") == "sequential_trend":
            return observed.isoformat()
        return "current_window"
    if period == "custom":
        start = date.fromisoformat(scope["start"])
        end = date.fromisoformat(scope["end"])
        return "current_custom" if start <= observed <= end else "previous_custom"
    if period == "week":
        current_start = anchor - timedelta(days=anchor.weekday())
        return "current_week" if observed >= current_start else "previous_week"
    if period == "month":
        current_start = anchor.replace(day=1)
        return "current_month" if observed >= current_start else "previous_month"
    return "current_day" if observed == anchor else "previous_day"


# ---------------------------------------------------------------- 源数据规范化

def normalize(rows: list[dict[str, str]], source: dict[str, Any]) -> list[dict[str, Any]]:
    """原始行 → canonical 行，同时保留原始行（launch_config 需要配置字段）。"""
    mapping = {row["raw"]: row["canonical"] for row in source.get("observed_schema") or []}
    numeric_fields = {
        row["raw"] for row in source.get("observed_schema") or []
        if row.get("dtype") in {"number", "percent"}
    }
    out = []
    for row_number, raw in enumerate(rows, start=2 + int((source.get("layout") or {}).get("header_row") or 0)):
        canon: dict[str, Any] = {"_raw": raw}
        for raw_name, canonical in mapping.items():
            if raw_name in raw:
                if raw_name in numeric_fields:
                    value = raw[raw_name]
                    text = str(value).strip().replace(",", "").replace("$", "").replace("%", "")
                    if text not in {"", "-", "--", "/", "None"}:
                        try:
                            Decimal(text)
                        except InvalidOperation as exc:
                            raise ValueError(
                                f"数据源 {source['id']} 第 {row_number} 行字段 {raw_name} 无法解析为数字：{value}"
                            ) from exc
                canon[canonical] = raw[raw_name]
        canon["_source_id"] = source["id"]
        canon["_grain"] = source["grain"]
        out.append(canon)
    return out


def resolve_effective(metric: dict[str, Any], report_date: str) -> dict[str, Any] | None:
    """按报表日期解析口径分段：选择不晚于报表日的最新生效段。"""
    segs = metric.get("effective") or []
    if not segs:
        return None
    active = [seg for seg in segs if seg.get("from") and seg["from"] <= report_date]
    if active:
        return max(active, key=lambda seg: seg["from"])
    fallbacks = [
        seg for seg in segs
        if "from" not in seg and seg.get("before") and report_date < seg["before"]
    ]
    return min(fallbacks, key=lambda seg: seg["before"]) if fallbacks else None


def metric_sources(metric: dict[str, Any], report_date: str = "") -> list[str]:
    """Return every source id contributing to a metric for this reporting date."""
    if metric.get("components"):
        return [component.split(".", 1)[0] for component in metric["components"]]
    seg = resolve_effective(metric, report_date)
    if seg and seg.get("field"):
        return [seg["field"].split(".", 1)[0]]
    if metric.get("from"):
        return [metric["from"].split(".", 1)[0]]
    chosen = (seg or {}).get("candidate_ref") or metric.get("chosen") or metric.get("candidate_ref")
    for cand in metric.get("candidates") or []:
        if cand.get("id") == chosen:
            return [cand.get("source")]
    return []


def metric_source(metric: dict[str, Any], report_date: str = "") -> str | None:
    """Backward-compatible single-source accessor."""
    sources = metric_sources(metric, report_date)
    return sources[0] if sources else None


def metric_value(row: dict[str, Any], metric_name: str, metric: dict[str, Any],
                 source_ids: dict[str, dict[str, Any]], report_date: str = "") -> Decimal:
    """按 metric 定义从一行取数，支持「直接映射 / 候选 / 口径分段」三种形式。"""
    expected_sources = metric_sources(metric, report_date)
    if expected_sources and row.get("_source_id") not in expected_sources:
        return Decimal(0)
    if metric.get("components"):
        source_id = row.get("_source_id")
        component = next(
            (item for item in metric["components"] if item.split(".", 1)[0] == source_id),
            None,
        )
        if component is None:
            return Decimal(0)
        return dec(row.get(component.split(".", 1)[1]))
    seg = resolve_effective(metric, report_date)
    if seg:
        if seg.get("field"):
            _, canonical = seg["field"].split(".", 1)
            return dec(row.get(canonical))
        if seg.get("candidate_ref"):
            for cand in metric.get("candidates") or []:
                if cand["id"] == seg["candidate_ref"]:
                    return dec(row["_raw"].get(cand["field"]))
        return Decimal(0)
    if metric.get("from"):
        _, canonical = metric["from"].split(".", 1)
        return dec(row.get(canonical))
    chosen = metric.get("chosen")
    if not chosen:
        return Decimal(0)
    for cand in metric.get("candidates") or []:
        if cand["id"] == chosen:
            return dec(row["_raw"].get(cand["field"]))
    return Decimal(0)


# ---------------------------------------------------------------- 规则应用

def classify_event(campaign: str, adgroup: str, rules: dict[str, Any]) -> tuple[str, str]:
    cfg = rules.get("event_classification") or {}
    patterns: dict[str, str] = cfg.get("patterns") or {}
    priority = cfg.get("field_priority") or ["campaign_name", "adgroup_name"]

    def hits(value: str) -> list[str]:
        return [name for name, pat in patterns.items() if re.search(pat, value or "", re.IGNORECASE)]

    for field in priority:
        value = campaign if field == "campaign_name" else adgroup
        found = hits(value)
        if len(found) == 1:
            return found[0], field
    return rules.get("unknown_event", "Unknown"), "无法识别"


def derive_country(row: dict[str, Any], resolver: CountryResolver,
                   rules: dict[str, Any]) -> tuple[str, str, list[str]]:
    """两段式国家解析（Campaign 名 → 广告组名），翻译交给三层码表解析器。

    返回 (国家名称, 国家代码, 未识别码列表)。解析不出来时名称/代码为 Unknown，
    未识别码不再静默丢弃，交给调用方汇总成校验清单。
    """
    cfg = rules.get("country_lookup") or {}
    stages = [(cfg.get("from", "campaign_name"), cfg.get("split", "_"))]
    if cfg.get("fallback_from"):
        stages.append((cfg["fallback_from"], cfg.get("fallback_seps", "[-_]")))

    seen_unknown: list[str] = []
    for field, seps in stages:
        for part in re.split(seps, str(row.get(field) or "")):
            token = part.strip()
            if not token:
                continue
            comps = [t.strip().upper() for t in token.split("&") if t.strip()]
            # 允许 2–6 个字母：既要覆盖 ISO 两位码，也要覆盖 LATAM / ME 这类聚合码
            if not comps or not all(re.fullmatch(r"[A-Z]{2,6}", c) for c in comps):
                continue
            res = resolver.resolve_group(token)
            if res["unknowns"]:
                seen_unknown.extend(res["unknowns"])
                continue
            # 同一阶段内「先命中先用」——与产线一致：Campaign 命中聚合码就不再往下找
            return res["name"], res["code"], []
    return "Unknown", "Unknown", seen_unknown


def derive_port(row: dict[str, Any], rules: dict[str, Any]) -> str:
    cfg = rules.get("port_derivation") or {}
    if not cfg:
        return ""
    tokens = {str(x).upper() for x in str(row.get(cfg.get("from", "campaign_name")) or "").split(cfg.get("split", "_"))}
    for token, label in (cfg.get("map") or {}).items():
        if token.upper() in tokens:
            return label
    return cfg.get("default", "")


def launch_config(row: dict[str, Any], rules: dict[str, Any]) -> str:
    """按规则的模板拼装投放配置展示字段；取不到就返回规则里的异常标记。

    注意：这里读的是**规范化后的行**（canonical 字段），不是原始行——
    规则里写的字段名都是 profile 的 canonical 名。
    """
    cfg = rules.get("launch_config")
    if not cfg:
        return ""
    app_type = (cfg.get("app_type_map") or {}).get(str(row.get(cfg.get("app_type_field", ""))), str(row.get(cfg.get("app_type_field", "")) or ""))
    series_budget = dec(row.get(cfg.get("series_budget_field", "")))
    group_raw = str(row.get(cfg.get("group_budget_field", "")) or "").strip()
    cbo_token = cfg.get("cbo_group_budget_token", "")
    group_budget = Decimal(0) if group_raw in {"", "-"} or group_raw == cbo_token else dec(group_raw)

    if series_budget > 0 and group_raw == cbo_token:
        level, amount = "CBO", series_budget
        budget_type = str(row.get(cfg.get("series_budget_type_field", "")) or "")
    elif series_budget == 0 and group_budget > 0:
        level, amount = "ABO", group_budget
        budget_type = str(row.get(cfg.get("group_budget_type_field", "")) or "")
    else:
        return cfg.get("invalid_marker", "配置异常")

    amount_text = str(int(amount)) if amount == amount.to_integral_value() else str(amount).rstrip("0").rstrip(".")
    bid = str(row.get(cfg.get("bid_field", "")) or "").strip()
    bid_text = cfg.get("no_bid_text", "不设出价") if bid in {"", "-"} else f"{cfg.get('bid_prefix', '出价')}{bid}"
    template = cfg.get("template", "{app_type}｜{level}｜${amount}/{budget_type}｜{bid_strategy}｜{bid}")
    return template.format(
        app_type=app_type, level=level, amount=amount_text, budget_type=budget_type,
        bid_strategy=str(row.get(cfg.get("bid_strategy_field", "")) or ""), bid=bid_text,
    )


# ---------------------------------------------------------------- 主流程

def required_source_ids(profile: dict[str, Any], report_date: str) -> set[str]:
    required = {
        source["id"] for source in profile["sources"]
        if source.get("role") == "ad_platform"
    }
    for join in (profile.get("source_plan") or {}).get("joins") or []:
        required.update([join["left"], join["right"]])
    for metric in profile.get("metrics", {}).values():
        required.update(metric_sources(metric, report_date))
    return required


def discover_source(source: dict[str, Any], profile_dir: Path, report_date: str) -> Path | None:
    discover = source.get("discover") or {}
    if (discover.get("pick") or {}).get("strategy") == "explicit":
        return None
    directory = Path(discover.get("dir") or ".")
    if not directory.is_absolute():
        directory = profile_dir / directory
    candidates = sorted(
        (path for path in directory.glob(discover.get("glob") or "*") if path.is_file()),
        key=lambda path: (path.stat().st_mtime_ns, path.name),
    ) if directory.exists() else []
    if not candidates:
        return None
    strategy = (discover.get("pick") or {}).get("strategy")
    if strategy == "latest_batch_by_date":
        tokens = {report_date, report_date.replace("-", "")}
        dated = [path for path in candidates if any(token in path.name for token in tokens)]
        if dated:
            return dated[-1]
    return candidates[-1]


def load_sources(profile: dict[str, Any], overrides: dict[str, Path], profile_path: Path,
                 report_date: str) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    known_ids = {source["id"] for source in profile["sources"]}
    unknown = sorted(set(overrides) - known_ids)
    if unknown:
        raise ValueError(f"--source-file 引用了未知 source：{', '.join(unknown)}")
    required = required_source_ids(profile, report_date)
    for source in profile["sources"]:
        sid = source["id"]
        path = overrides.get(sid) or discover_source(source, profile_path.parent, report_date)
        if path is None:
            if source.get("role") == "screenshot":
                raise SystemExit(f"source {sid}: 截图数据源尚未实现（本版只留占位）")
            if sid in required:
                raise ValueError(f"缺少必需数据源：{sid}（请传 --source-file {sid}=路径，或配置可发现的 discover）")
            continue
        if not path.exists():
            raise ValueError(f"数据源文件不存在：{sid}={path}")
        raw_rows = read_source(path, source)
        raw_headers = set(raw_rows[0]) if raw_rows else set()
        canonical_to_raw = {
            item.get("canonical"): item.get("raw")
            for item in source.get("observed_schema") or []
        }
        missing_columns = [
            canonical for canonical in source.get("required_columns") or []
            if canonical_to_raw.get(canonical) not in raw_headers
        ]
        if missing_columns:
            raise ValueError(f"数据源 {sid} 缺少必需列：{', '.join(missing_columns)}")
        out[sid] = normalize(raw_rows, source)
    return out


def key_value(row: dict[str, Any], key: str, source: dict[str, Any]) -> str:
    """Resolve a configured join/dedupe key without baking platform names into the engine."""
    candidates = [key]
    if key == "date":
        candidates.extend(["af_date", "report_date"])
    if key == "ad_id" or (key == "creative_id" and source.get("role") == "attribution"):
        candidates.extend(["af_ad_id", "creative_id", "ad_id"])
    if key == "adgroup_id":
        candidates.extend(["af_adgroup_id"])
    if key == "campaign_id":
        candidates.extend(["af_campaign_id"])
    for candidate in candidates:
        value = row.get(candidate)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def dedupe_sources(profile: dict[str, Any], data: dict[str, list[dict[str, Any]]]) -> None:
    sources = {source["id"]: source for source in profile["sources"]}
    for rule in (profile.get("source_plan") or {}).get("dedupe") or []:
        sid = rule["source"]
        source = sources[sid]
        rows = data.get(sid, [])
        grouped: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            grouped[tuple(key_value(row, key, source) for key in rule["key"])].append(row)
        kept: list[dict[str, Any]] = []
        for group in grouped.values():
            keep = rule.get("keep")
            if keep == "first":
                kept.append(group[0])
            elif keep == "latest_snapshot":
                snapshot_field = (source.get("snapshot") or {}).get("snapshot_field")
                kept.append(max(group, key=lambda row: str(row.get(snapshot_field) or "")))
            else:
                kept.append(group[-1])
        data[sid] = kept


def snapshot_maturity_failures(profile: dict[str, Any], data: dict[str, list[dict[str, Any]]],
                               report_date: str) -> list[str]:
    failures: list[str] = []
    report_day = date.fromisoformat(report_date)
    for source in profile["sources"]:
        maturity = (source.get("snapshot") or {}).get("maturity") or {}
        if not maturity:
            continue
        sid = source["id"]
        for metric_name, required_days in maturity.items():
            metric = (profile.get("metrics") or {}).get(metric_name)
            if not metric or sid not in metric_sources(metric, report_date):
                continue
            immature_dates: set[str] = set()
            for row in data.get(sid, []):
                raw_date = key_value(row, "date", source)[:10]
                try:
                    age = (report_day - date.fromisoformat(raw_date)).days
                except ValueError:
                    continue
                if age < int(required_days):
                    immature_dates.add(raw_date)
            if immature_dates:
                failures.append(
                    f"快照指标未成熟：{metric_name} 需要 {required_days} 天，"
                    f"当前包含 {', '.join(sorted(immature_dates)[:5])}"
                )
    return failures


def join_metrics(profile: dict[str, Any], data: dict[str, list[dict[str, Any]]],
                 left_source_id: str, report_date: str) -> tuple[dict[int, dict[str, Decimal]], dict[int, bool]]:
    """Execute source_plan joins and return right-side metrics keyed by left row identity."""
    plan = profile.get("source_plan") or {}
    sources = {source["id"]: source for source in profile["sources"]}
    metrics = profile["metrics"]
    mapped: dict[int, dict[str, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    matched: dict[int, bool] = defaultdict(bool)

    for join in plan.get("joins") or []:
        if join.get("left") != left_source_id:
            continue
        right_id = join["right"]
        left_source = sources[left_source_id]
        right_source = sources[right_id]
        index: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
        for right_row in data.get(right_id, []):
            key = tuple(key_value(right_row, name, right_source) for name in join["keys"])
            if all(key):
                index[key].append(right_row)
        for left_row in data.get(left_source_id, []):
            key = tuple(key_value(left_row, name, left_source) for name in join["keys"])
            hits = index.get(key, []) if all(key) else []
            if hits:
                matched[id(left_row)] = True
            for right_row in hits:
                for name, cfg in metrics.items():
                    if right_id in metric_sources(cfg, report_date):
                        mapped[id(left_row)][name] += metric_value(
                            right_row, name, cfg, sources, report_date
                        )
    return mapped, matched


def main() -> int:
    ap = argparse.ArgumentParser(description="按配方单出报表（P1：CSV）")
    ap.add_argument("--profile", required=True, type=Path)
    ap.add_argument("--report-type", required=True)
    ap.add_argument("--date", required=True, help="YYYY-MM-DD")
    ap.add_argument("--source-file", action="append", default=[], help="id=路径，可多次指定")
    ap.add_argument("--out-dir", type=Path, default=Path("runtime/out"))
    args = ap.parse_args()

    if vp.is_bundled_example(args.profile):
        print("examples/ 下的 profile 是只读样板，不能直接生成正式报表；请复制到项目目录并重新 probe、对账和确认。", file=sys.stderr)
        return 1

    profile = vp.load_profile(args.profile)
    issues = vp.schema_issues(profile) + vp.semantic_issues(profile)
    fails = [i for i in issues if i.level == "FAIL"]
    for w in (i for i in issues if i.level == "WARN"):
        print(f"提示：{w}")
    if fails:
        print("\n门禁拒绝：配方单里还有未解决的问题，不能出正式报表。", file=sys.stderr)
        for f in fails:
            print(f"  {f}", file=sys.stderr)
        print("\n如果只想看诊断草稿，请按流程显式请求（本版 P1 未实现草稿输出）。", file=sys.stderr)
        return 1

    report_type = next(
        (item for item in profile.get("report_types") or [] if item.get("id") == args.report_type),
        None,
    )
    if report_type is None:
        print(f"不存在的报表类型：{args.report_type}", file=sys.stderr)
        return 2
    selected_dates = report_dates(report_type, args.date)

    overrides: dict[str, Path] = {}
    for item in args.source_file:
        if "=" not in item:
            print(f"--source-file 需要 id=路径 形式，收到：{item}", file=sys.stderr)
            return 2
        sid, path = item.split("=", 1)
        overrides[sid] = Path(path)

    try:
        data = load_sources(profile, overrides, args.profile, args.date)
    except (OSError, ValueError, KeyError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    sources_by_id = {s["id"]: s for s in profile["sources"]}
    dedupe_sources(profile, data)
    maturity_failures = snapshot_maturity_failures(profile, data, args.date)
    if maturity_failures:
        for failure in maturity_failures:
            print(failure, file=sys.stderr)
        return 1

    rules = profile.get("rules") or {}
    resolver = CountryResolver(rules)
    unknown_codes: set[str] = set()
    ad_sources = [s for s in profile["sources"] if s.get("role") == "ad_platform"]
    if not ad_sources:
        print("配方单里没有投放侧数据源", file=sys.stderr)
        return 2
    ad_batches: list[tuple[str, list[dict[str, Any]], dict[int, dict[str, Decimal]], dict[int, bool]]] = []
    for ad_source in ad_sources:
        ad_id = ad_source["id"]
        ad_rows = [r for r in data.get(ad_id, []) if str(r.get("date") or "")[:10] in selected_dates]
        if not ad_rows:
            continue
        mapped, matched = join_metrics(profile, data, ad_id, args.date)
        joins_for_source = [
            join for join in (profile.get("source_plan") or {}).get("joins") or []
            if join.get("left") == ad_id
        ]
        if joins_for_source:
            matched_rows = sum(1 for row in ad_rows if matched.get(id(row), False))
            coverage = matched_rows / len(ad_rows)
            threshold = Decimal(str((rules.get("join_coverage_threshold", 0.9))))
            if Decimal(str(coverage)) < threshold:
                print(
                    f"数据源 {ad_id} 关联覆盖率 {coverage:.2%} 低于门槛 {float(threshold):.2%}"
                    f"（匹配 {matched_rows}/{len(ad_rows)} 行）",
                    file=sys.stderr,
                )
                return 1
            keep_left = ((profile.get("source_plan") or {}).get("unmatched_policy") or {}).get("keep_left", True)
            if not keep_left or any(join.get("type") == "inner" for join in joins_for_source):
                ad_rows = [row for row in ad_rows if matched.get(id(row), False)]
        ad_batches.append((ad_id, ad_rows, mapped, matched))
    if not ad_batches:
        print(f"所有投放侧源里都没有 {args.date} 所属周期的数据行", file=sys.stderr)
        return 2

    metrics_cfg = profile["metrics"]
    display_only = {n for n, c in metrics_cfg.items() if c.get("display_only")}
    csv_artifacts = [a for a in profile["output"]["artifacts"] if a.get("kind") == "csv"]
    process_artifact = next((a for a in csv_artifacts if a["role"] == "process_table"), None)
    result_artifact = next((a for a in csv_artifacts if a["role"] == "result_table"), None)
    if not process_artifact or not result_artifact:
        print("配方单缺少 process_table / result_table 的 CSV 产物定义", file=sys.stderr)
        return 2

    group_grain = process_artifact.get("grain", "adgroup")
    scope_mode = (report_type.get("time_scope") or {}).get("mode", "single_period")
    segmented_periods = scope_mode == "multi_period" or (
        scope_mode == "rolling_window"
        and (report_type.get("comparison") or {}).get("mode") == "sequential_trend"
    )

    # ---- 过程表：按投放侧粒度聚合 ----
    buckets: dict[str, dict[str, Any]] = {}
    for ad_id, ad_rows, mapped, matched in ad_batches:
        ad_metric_names = {
            name for name, cfg in metrics_cfg.items()
            if ad_id in metric_sources(cfg, args.date)
        }
        for row in ad_rows:
            key_field = {"adgroup": "adgroup_id", "ad": "ad_id", "campaign": "campaign_id", "creative": "creative_id", "account": "account_id"}[group_grain]
            entity_key = str(row.get(key_field) or "")
            source_date = str(row.get("date") or args.date)[:10]
            period = report_period_label(report_type, args.date, source_date)
            local_key = f"{period}:{source_date}:{entity_key}" if segmented_periods else f"{source_date}:{entity_key}"
            key = f"{ad_id}:{local_key}"
            bucket = buckets.setdefault(key, {
                "sample": row,
                "source_id": ad_id,
                "metrics": defaultdict(Decimal),
                "matched": False,
                "period": period,
            })
            for name in ad_metric_names:
                bucket["metrics"][name] += metric_value(
                    row, name, metrics_cfg[name], sources_by_id, args.date
                )
            for name, value in mapped.get(id(row), {}).items():
                bucket["metrics"][name] += value
            bucket["matched"] = bucket["matched"] or matched.get(id(row), False)

    # 投放侧四个基础指标在过程表里用固定中文列名；其余指标（含 display_only）
    # 都要出现在输出里——display_only 只表示「不参与成本公式」，不是「不展示」。
    base_metric_names = {"spend", "impressions", "clicks", "install"}
    display_metric_names = [n for n in metrics_cfg if n not in base_metric_names]

    process_rows = []
    for key, bucket in buckets.items():
        sample = bucket["sample"]
        source_id = bucket["source_id"]
        campaign = str(sample.get("campaign_name") or "")
        adgroup_name = str(sample.get("adgroup_name") or "")
        event, basis = classify_event(campaign, adgroup_name, rules)
        country_name, country_code, unknown = derive_country(sample, resolver, rules)
        unknown_codes.update(unknown)
        values = bucket["metrics"]
        unmatched_label = ((profile.get("source_plan") or {}).get("unmatched_policy") or {}).get("label", "未匹配")
        process_rows.append({
            "记录键": f"{args.report_type}:{args.date}:-:{key}:{event}",
            "日期": f"{str(sample.get('date') or args.date)[:10]} 00:00:00",
            **({"周期": bucket["period"]} if segmented_periods else {}),
            "平台": (rules.get("platform_label") or sources_by_id[source_id].get("platform", "")),
            "端口": derive_port(sample, rules),
            "账户名称": str(sample.get("account_name") or ""),
            "账户ID": str(sample.get("account_id") or ""),
            "Campaign名称": campaign,
            "广告系列ID": str(sample.get("campaign_id") or ""),
            "广告组名称": adgroup_name,
            "广告组ID": str(sample.get("adgroup_id") or ""),
            "广告ID": str(sample.get("ad_id") or ""),
            "广告名称": str(sample.get("ad_name") or ""),
            "国家名称": country_name,
            "国家代码": country_code,
            "事件": event,
            "匹配状态": "已匹配" if bucket["matched"] else unmatched_label,
            "来源批次": f"{args.date}|{rules.get('source_batch_label') or args.report_type}",
            "花费": num_out(values.get("spend", Decimal(0))),
            "展示": num_out(values.get("impressions", Decimal(0))),
            "点击": num_out(values.get("clicks", Decimal(0))),
            "安装": num_out(values.get("install", Decimal(0))),
            **{n: num_out(values.get(n, Decimal(0))) for n in display_metric_names},
            "投放配置": launch_config(sample, rules),
        })

    # ---- 结果表：按 rules 的分组键汇总 ----
    group_by = list(result_artifact.get("group_by") or ["account_name", "country_name", "平台", "端口", "事件"])
    if segmented_periods and "周期" not in group_by:
        group_by.insert(0, "周期")
    # rows_filter 形如 "positive_any(花费,FTD_LTV,FTD_Actual)"：任一字段 > 0 才输出
    rows_filter = result_artifact.get("rows_filter") or ""
    positive_fields: list[str] = []
    m = re.match(r"positive_any\(([^)]*)\)", rows_filter)
    if m:
        positive_fields = [x.strip() for x in m.group(1).split(",") if x.strip()]
    column_rename: dict[str, str] = result_artifact.get("column_rename") or {}

    result_buckets: dict[tuple, dict[str, Decimal]] = {}
    for row in process_rows:
        key = tuple(str(row.get(g) or "") for g in group_by)
        agg = result_buckets.setdefault(key, defaultdict(Decimal))
        for name in ("花费", "展示", "点击", "安装", *display_metric_names):
            agg[name] += dec(row.get(name))
    result_rows = []
    for key, agg in result_buckets.items():
        if positive_fields and not any(agg.get(f, Decimal(0)) > 0 for f in positive_fields):
            continue
        record = {column_rename.get(g, g): key[i] for i, g in enumerate(group_by)}
        record.update({column_rename.get(k, k): num_out(v) for k, v in agg.items()})
        for cname, cvalue in (result_artifact.get("constant_columns") or {}).items():
            record.setdefault(cname, cvalue)
        result_rows.append(record)

    # Resolve official paths now, but do not publish anything until every
    # blocking check has passed.
    date_token = args.date.replace("-", "")
    process_path = args.out_dir / f"过程表_{date_token}.csv"
    result_path = args.out_dir / f"结果表_{date_token}.csv"

    # ---- 对账（blocking）----
    checks = (profile.get("validation") or {}).get("checks") or []
    tolerance = Decimal("0.01")
    failures = []
    if any(c["id"] == "total_reconcile" for c in checks):
        # 结果表按"分组后"的行过滤（如整组无花费且无 FTD 才不输出），
        # 所以对账必须按同一粒度筛选过程行，逐行筛会少算。
        reconcilable = process_rows
        if positive_fields:
            result_group_by = group_by
            included = {
                key for key, agg in result_buckets.items()
                if any(agg.get(f, Decimal(0)) > 0 for f in positive_fields)
            }
            if result_group_by:
                reconcilable = [
                    r for r in process_rows
                    if tuple(str(r.get(g) or "") for g in result_group_by) in included
                ]
            else:
                reconcilable = [
                    r for r in process_rows
                    if any(dec(r.get(f)) > 0 for f in positive_fields)
                ]
        for name in ("花费", "安装"):
            p_total = sum(dec(r.get(name)) for r in reconcilable)
            r_total = sum(dec(r.get(name)) for r in result_rows)
            if abs(p_total - r_total) > tolerance:
                failures.append(
                    f"{name} 过程表合计 {p_total} ≠ 结果表合计 {r_total}"
                    f"（已按结果表分组过滤口径对账，参与对账过程行 {len(reconcilable)}/{len(process_rows)}）"
                )

    if unknown_codes:
        print(f"\n国家码告警（未识别，已按 Unknown 输出）：{', '.join(sorted(unknown_codes))}", file=sys.stderr)
        print("  处理方式：补 references/lookups/iso3166-1.zh.json 或 business-aggregates.yaml，"
              "或在 profile 的 country_lookup.map 里加客户特殊写法", file=sys.stderr)
    if failures:
        print("\n校验失败（默认阻断交付）：", file=sys.stderr)
        for f in failures:
            print(f"  {f}", file=sys.stderr)
        return 1

    # ---- 原子发布 ----
    args.out_dir.mkdir(parents=True, exist_ok=True)
    for path, rows in ((process_path, process_rows), (result_path, result_rows)):
        headers: list[str] = []
        for row in rows:
            for key in row:
                if key not in headers:
                    headers.append(key)
        temporary = path.with_name(f".{path.name}.tmp")
        with temporary.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=headers)
            writer.writeheader()
            writer.writerows([
                {key: safe_spreadsheet_value(value) for key, value in row.items()}
                for row in rows
            ])
        temporary.replace(path)
    print(f"过程表 {len(process_rows)} 行 → {process_path}")
    print(f"结果表 {len(result_rows)} 行 → {result_path}")
    print("对账通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
