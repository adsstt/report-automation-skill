#!/usr/bin/env python3
"""反推候选（infer）

从 probe 观测报告出发，生成一份 profile 草稿。**只产候选，不落定**：

- 每个推断都写进 `inference.candidates`，带证据（候选数、是否唯一匹配）
- 出现多候选、无法区分、或涉及平台配对属性（关联键）时，写入 `ambiguities` 并标 P0
- 任何 `confirm_required` 指标的 `chosen` 一律留空，等用户确认

因此草稿**必然可能校验不通过**——这是设计意图：草稿的作用是把问题问清楚，
不是假装已经知道答案。

用法：
    python scripts/infer_mappings.py --probe runtime/probe.json --out runtime/profile.draft.yaml
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from cli_compat import configure_console

configure_console()

try:
    import yaml
except ImportError:  # pragma: no cover
    print("缺少依赖 pyyaml，请先执行: pip install pyyaml", file=sys.stderr)
    sys.exit(2)


ID_ROLES = [
    "account_id", "account_name", "campaign_id", "campaign_name",
    "adgroup_id", "adgroup_name", "ad_id", "ad_name",
    "creative_id", "creative_name", "date",
]
MEASURE_ROLES = [
    "spend", "impressions", "clicks", "install",
    "registration", "kyc", "first_deposit", "trade", "revenue",
]
CATALOG_PATH = Path(__file__).resolve().parents[1] / "references" / "platforms" / "catalog.yaml"

# 目标表表头 → 派生指标。**必须放在指标之前判断**：成本/转化率类列名里往往也带指标名
HEADER_TO_DERIVED: list[tuple[str, str]] = [
    (r"cpi|安装成本", "cpa_install"),
    (r"cpr|注册成本", "cpa_reg"),
    (r"kyc.*成本|cpa[-_ ]?kyc", "cpa_kyc"),
    (r"ftd.*成本|cpa[-_ ]?ftd", "cpa_ftd"),
    (r"ftt.*成本|cpa[-_ ]?ftt", "cpa_ftt"),
    (r"注册率|注册转化率|注册/安装", "cvr_install_reg"),
    (r"kyc转化率|kyc/注册|注册.*kyc.*(率|转化)", "cvr_reg_kyc"),
    (r"入金率|ftd转化率", "cvr_install_ftd"),
    (r"kyc.*ftd|kyc.*入金", "cvr_kyc_ftd"),
    (r"ftt转化率|ftd.*ftt", "cvr_ftd_ftt"),
]

HEADER_TO_ROLE: list[tuple[str, str]] = [
    (r"ftd.*actual", "first_deposit"),
    (r"ftd.*ltv", "first_deposit_ltv"),
    (r"^ftd$|^ftd\b", "first_deposit"),
    (r"trade|ftt", "trade"),
    (r"income|revenue|收入", "revenue"),
    (r"^kyc|basic kyc|advanced kyc", "kyc"),
    (r"signup|register|registration|注册(?!成本)", "registration"),
    (r"install|激活|安装(?!成本)", "install"),
    (r"impression|展示|曝光", "impressions"),
    (r"click|点击(?!率)", "clicks"),
    (r"spend|cost|消耗|花费", "spend"),
]

DERIVED_EXPR = {
    "cpa_install": "spend / install",
    "cpa_reg": "spend / registration",
    "cpa_kyc": "spend / kyc",
    "cpa_ftd": "spend / first_deposit",
    "cpa_ftt": "spend / trade",
    "cvr_install_reg": "registration / install",
    "cvr_reg_kyc": "kyc / registration",
    "cvr_install_ftd": "first_deposit / install",
    "cvr_kyc_ftd": "first_deposit / kyc",
    "cvr_ftd_ftt": "trade / first_deposit",
}
DERIVED_IS_PERCENT = {k for k in DERIVED_EXPR if k.startswith("cvr_")}
# 派生指标依赖的指标
DERIVED_DEPS = {
    "cpa_install": ["spend", "install"],
    "cpa_reg": ["spend", "registration"],
    "cpa_kyc": ["spend", "kyc"],
    "cpa_ftd": ["spend", "first_deposit"],
    "cpa_ftt": ["spend", "trade"],
    "cvr_install_reg": ["registration", "install"],
    "cvr_reg_kyc": ["kyc", "registration"],
    "cvr_install_ftd": ["first_deposit", "install"],
    "cvr_kyc_ftd": ["first_deposit", "kyc"],
    "cvr_ftd_ftt": ["trade", "first_deposit"],
}


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", str(text)).strip().lower()


def safe_sheet_id(name: str, used: set[str]) -> str:
    """生成合法且唯一的 sheet id：必须以小写字母开头，只含 a-z0-9_。"""
    sid = re.sub(r"[^a-z0-9_]", "", name.lower())
    sid = re.sub(r"_+", "_", sid).strip("_")
    if not sid:
        sid = "sheet"
    if not sid[0].isalpha():
        sid = "s_" + sid
    sid = sid[:30]
    base, i = sid, 2
    while sid in used:
        sid = f"{base[:26]}_{i}"
        i += 1
    used.add(sid)
    return sid


def load_platform_catalog() -> dict[str, Any]:
    return (yaml.safe_load(CATALOG_PATH.read_text(encoding="utf-8")) or {}).get("platforms", {})


def guess_source_meta(info: dict[str, Any], catalog: dict[str, Any]) -> dict[str, Any]:
    name = norm(Path(info.get("path", "")).name)
    cols = [norm(c.get("name", "")) for c in info.get("columns", []) or []]
    alltext = name + " " + " ".join(cols)

    matches = [
        platform for platform, config in catalog.items()
        if any(norm(pattern) in alltext for pattern in config.get("patterns") or [])
    ]
    platform = matches[0] if len(matches) == 1 else "unknown"
    role = (catalog.get(platform) or {}).get("role", "ad_platform")
    # 粒度：不能直接取候选第一位（probe 会把 campaign 排在前面）。
    # 投放侧优先按 ad 粒度分析（它有更细的 ID 就作为关联键）；
    # 归因侧取自己最细的粒度。
    names = {
        g["grain"]
        for g in (info.get("grain_candidates") or [])
        if g.get("confidence") in {"high", "medium"}
    }
    if not names:
        grain = "ad"
    elif role == "attribution":
        grain = next((g for g in ("ad", "adgroup", "campaign") if g in names), "ad")
    else:
        grain = "ad" if "ad" in names else next((g for g in ("creative", "adgroup", "campaign") if g in names), "ad")
    return {"platform": platform, "role": role, "grain": grain, "platform_matches": matches}


def build_observed_schema(info: dict[str, Any]) -> tuple[list[dict[str, str]], dict[str, list[str]]]:
    schema: list[dict[str, str]] = []
    role_columns: dict[str, list[str]] = {}
    for col in info.get("columns", []) or []:
        name = col.get("name", "")
        roles = col.get("role_candidates") or []
        if not roles:
            continue
        role = roles[0]
        if role not in ID_ROLES + MEASURE_ROLES:
            continue
        if role == "date" or (col.get("date_ratio") or 0) > 0.8:
            dtype = "date"
        elif (col.get("numeric_ratio") or 0) > 0.9 and role in MEASURE_ROLES:
            dtype = "number"
        else:
            dtype = "text"
        schema.append({"raw": name, "canonical": role, "dtype": dtype})
        role_columns.setdefault(role, []).append(name)
    return schema, role_columns


def match_header(header: str) -> tuple[str, str] | None:
    """目标列名 → (namespace, key)。先判派生，再判指标。"""
    key = norm(header)
    for pattern, dname in HEADER_TO_DERIVED:
        if re.search(pattern, key):
            return ("derived", dname)
    for pattern, role in HEADER_TO_ROLE:
        if re.search(pattern, key):
            return ("metrics", role)
    return None


PERIOD_ALIASES = {
    "daily": "daily", "day": "daily", "日报": "daily",
    "weekly": "weekly", "week": "weekly", "周报": "weekly",
    "monthly": "monthly", "month": "monthly", "月报": "monthly",
    "custom": "custom", "自定义": "custom",
}

METRIC_LABELS = {
    "spend": "花费", "impressions": "展示", "clicks": "点击", "install": "安装",
    "registration": "注册", "kyc": "KYC", "first_deposit": "首充", "trade": "交易",
    "revenue": "收入",
}


def recommend_period(raw_sources: list[dict[str, Any]], goal: dict[str, Any]) -> dict[str, Any]:
    """Recommend a period from observed coverage. A recommendation never confirms it."""
    coverages = [source.get("date_coverage") or {} for source in raw_sources]
    distinct_days = max((int(item.get("distinct_days") or 0) for item in coverages), default=0)
    minimum = min((item.get("min") for item in coverages if item.get("min")), default=None)
    maximum = max((item.get("max") for item in coverages if item.get("max")), default=None)
    description = str(goal.get("goal_description") or goal.get("description") or "")
    has_custom_window = bool(
        goal.get("start") and goal.get("end")
        or re.search(r"活动|campaign|阶段|窗口|大促", description, re.IGNORECASE)
    )
    if has_custom_window:
        suggested = "custom"
        reason = "业务目标包含活动或指定日期窗口"
    elif distinct_days >= 60:
        suggested = "monthly"
        reason = f"源数据覆盖 {distinct_days} 个不同日期，适合观察自然月趋势"
    elif distinct_days >= 14:
        suggested = "weekly"
        reason = f"源数据覆盖 {distinct_days} 个不同日期，适合按完整自然周复盘"
    else:
        suggested = "daily"
        reason = f"源数据仅覆盖 {distinct_days} 个不同日期，优先从日报开始"
    evidence = [reason]
    if minimum and maximum:
        evidence.append(f"可观测日期范围：{minimum} 至 {maximum}")
    return {
        "suggested_type": suggested,
        "options": ["daily", "weekly", "monthly", "custom"],
        "evidence": evidence,
    }


def selected_period(goal: dict[str, Any], recommendation: dict[str, Any]) -> dict[str, Any]:
    raw = goal.get("period") or {}
    if isinstance(raw, str):
        raw = {"type": raw, "confirmed": False}
    raw_type = PERIOD_ALIASES.get(str(raw.get("type") or "").strip().lower())
    confirmed = bool(raw.get("confirmed")) and raw_type in {"daily", "weekly", "monthly", "custom"}
    result: dict[str, Any] = {
        "type": raw_type,
        "confirmed": confirmed,
        "recommendation": recommendation,
    }
    for key in ("start", "end"):
        if raw.get(key):
            result[key] = str(raw[key])
    return result


def standard_report_type(period: dict[str, Any], analysis_grain: str) -> dict[str, Any]:
    selected = period.get("type") or period["recommendation"]["suggested_type"]
    if selected == "custom":
        time_scope = {
            "mode": "multi_period", "period": "custom", "anchor": "report_date",
            "windows": ["previous_custom", "current_custom"],
        }
        for key in ("start", "end"):
            if period.get(key):
                time_scope[key] = period[key]
    elif selected == "weekly":
        time_scope = {"mode": "multi_period", "period": "week", "anchor": "monday", "windows": ["previous_week", "current_week"]}
    elif selected == "monthly":
        time_scope = {"mode": "multi_period", "period": "month", "anchor": "month_start", "windows": ["previous_month", "current_month"]}
    else:
        time_scope = {"mode": "multi_period", "period": "day", "anchor": "report_date", "windows": ["previous_day", "current_day"]}
    return {
        "id": "standard",
        "time_scope": time_scope,
        "aggregation": {"grain": analysis_grain, "group_by": ["account_id"], "total_row": True},
        "comparison": {"mode": "previous_period", "base": time_scope["windows"][0], "target": time_scope["windows"][1]},
    }


def standard_sheets(
    metrics: dict[str, Any], prepared: list[dict[str, Any]], primary_metrics: list[str] | None = None
) -> list[dict[str, Any]]:
    primary_metrics = (primary_metrics or list(metrics))[:8]
    available_roles = {
        role
        for item in prepared
        for role in item.get("role_columns", {})
    }
    dimension_specs = [
        ("account_id", "账户ID", "account_id"),
        ("campaign_id", "Campaign ID", "campaign_id"),
        ("adgroup_id", "Ad Group ID", "adgroup_id"),
        ("ad_id", "Ad ID", "ad_id"),
    ]
    detail_dimensions = [item for item in dimension_specs if item[0] in available_roles]
    if not detail_dimensions:
        detail_dimensions = [("account_id", "账户ID", "account_id")]

    metric_columns = [
        {"header": METRIC_LABELS.get(name, name), "source": f"metrics.{name}", "format": "number:2"}
        for name in primary_metrics
    ]
    summary_columns = [
        {"header": "周期", "source": "$report_date", "format": "text"},
        {"header": "对比", "source": "$const_comparison", "format": "text"},
        *metric_columns,
    ]
    detail_columns = [
        {"header": "日期", "source": "$report_date", "format": "date:yyyy-mm-dd"},
        *[
            {"header": label, "source": f"group.{source}", "format": "text"}
            for _, label, source in detail_dimensions
        ],
        *metric_columns,
    ]
    trend_columns = [
        {"header": "周期", "source": "$report_date", "format": "text"},
        {"header": "对比", "source": "$const_comparison", "format": "text"},
        *metric_columns,
    ]
    base = {
        "match": "standard",
        "row_key": {"field": "周期", "format": "text"},
        "placement": {"mode": "replace_sheet", "same_key": "replace_in_place"},
        "column_match": "by_header",
        "style": {"inherit_from_sample": True, "apply": ["header_fill", "number_format", "column_width"]},
    }
    return [
        {**base, "id": "management_summary", "target_sheet": "管理摘要", "role": "comparison", "header": {"row": 6, "auto_detect": False}, "group_by": [], "constant_columns": {"对比": ""}, "columns": summary_columns, "comparison_layout": {"mode": "block_interleaved", "block": {"rows": ["base", "current", "delta_abs", "delta_pct"], "group_by": [], "delta_label": "环比", "delta_value": "percent_number", "null_symbol": "N/A"}}},
        {**base, "id": "detail_data", "target_sheet": "明细数据", "role": "history", "header": {"row": 2, "auto_detect": False}, "row_key": {"field": "日期", "format": "date:yyyy-mm-dd"}, "group_by": [item[2] for item in detail_dimensions], "columns": detail_columns},
        {**base, "id": "trend_comparison", "target_sheet": "趋势环比", "role": "comparison", "header": {"row": 2, "auto_detect": False}, "group_by": [], "constant_columns": {"对比": ""}, "columns": trend_columns, "comparison_layout": {"mode": "block_interleaved", "block": {"rows": ["base", "current", "delta_abs", "delta_pct"], "group_by": [], "delta_label": "环比", "delta_value": "percent_number", "null_symbol": "N/A"}}},
    ]


def main() -> int:
    ap = argparse.ArgumentParser(description="从 probe 报告反推 profile 草稿（只产候选）")
    ap.add_argument("--probe", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--product", default="TODO_PRODUCT_NAME")
    ap.add_argument("--timezone", default="Asia/Shanghai")
    ap.add_argument("--business-goal", type=Path, help="无目标模板模式的业务目标 YAML")
    ap.add_argument("--standard-output", action="store_true", help="生成标准三 Sheet 候选 profile")
    args = ap.parse_args()

    if args.standard_output and not args.business_goal:
        ap.error("--standard-output 必须同时提供 --business-goal")
    if args.business_goal and not args.business_goal.exists():
        ap.error(f"业务目标文件不存在：{args.business_goal}")

    probe = json.loads(args.probe.read_text(encoding="utf-8"))
    raw_sources = [s for s in (probe.get("sources") or []) if "error" not in s]
    if not raw_sources:
        print("probe 报告里没有可用的 sources，无法反推", file=sys.stderr)
        return 2

    inferences: list[dict[str, Any]] = []
    ambiguities: list[dict[str, Any]] = []
    platform_catalog = load_platform_catalog()

    # ---------- 第一遍：建立 source 清单与粒度信息 ----------
    prepared = []
    for info in raw_sources:
        meta = guess_source_meta(info, platform_catalog)
        schema, role_columns = build_observed_schema(info)
        prepared.append({"info": info, "meta": meta, "schema": schema, "role_columns": role_columns})
        if meta["platform"] == "unknown":
            ambiguities.append({
                "id": "p0_source_platform",
                "question": f"无法唯一识别源文件 {Path(info.get('path', '')).name} 的平台与角色，请确认。",
                "evidence": f"知识库匹配结果：{meta.get('platform_matches') or '无'}",
                "suggested": "确认 platform 与 role 后写回 sources；不要默认套用 TikTok 规则",
                "affected": ["sources"],
                "level": "P0",
                "status": "pending",
            })

    used_ids: set[str] = set()
    for item in prepared:
        prefix = (platform_catalog.get(item["meta"]["platform"]) or {}).get("prefix", "src")
        sid = f"{prefix}_{item['meta']['grain']}"
        if sid in used_ids:                      # 同平台同粒度多文件 → 加序号
            n = 2
            while f"{sid}_{n}" in used_ids:
                n += 1
            sid = f"{sid}_{n}"
        used_ids.add(sid)
        item["id"] = sid

    ad_grains = [i["meta"]["grain"] for i in prepared if i["meta"]["role"] == "ad_platform"]
    af_grains = [i["meta"]["grain"] for i in prepared if i["meta"]["role"] == "attribution"]
    common = [g for g in ("ad", "adgroup", "campaign") if g in ad_grains and g in af_grains]
    analysis_grain = common[0] if common else (ad_grains[0] if ad_grains else "ad")

    # ---------- 第二遍：source 定义 + 指标 ----------
    profile_sources: list[dict[str, Any]] = []
    metrics: dict[str, Any] = {}

    for item in prepared:
        info, meta, sid, schema, role_columns = (
            item["info"], item["meta"], item["id"], item["schema"], item["role_columns"]
        )
        date_cols = role_columns.get("date") or []
        profile_sources.append(
            {
                "id": sid,
                "platform": meta["platform"],
                "role": meta["role"],
                "grain": meta["grain"],
                "discover": {
                    "dir": str(Path(info["path"]).parent),
                    "glob": Path(info["path"]).name,
                    "pick": {"strategy": "latest_batch_by_date", "batch_key": "report_date"},
                },
                "layout": {
                    "header_row": info.get("header_row", 0),
                    "sheet": info.get("sheet", 0),
                    "encoding": info.get("encoding", "utf-8-sig"),
                    "csv_delimiter": info.get("delimiter", ","),
                },
                "date_column": {"raw": date_cols[0] if date_cols else "日期", "parse": "%Y-%m-%d"},
                "observed_schema": schema or [{"raw": "TODO", "canonical": "date", "dtype": "date"}],
                "required_columns": [
                    c for c in ("account_id", f"{meta['grain']}_id") if c in role_columns
                ] or ["date"],
                "snapshot": (
                    {"is_snapshot": True, "snapshot_field": "snapshot_date"}
                    if meta["role"] == "attribution"
                    else {"is_snapshot": False}
                ),
            }
        )

        # 归因侧只让「分析粒度」那一份提供转化指标；其余粒度仅用于对账，避免重复指标
        is_metric_source = meta["role"] == "ad_platform" or meta["grain"] == analysis_grain
        if not is_metric_source:
            inferences.append(
                {
                    "subject": f"source {sid}",
                    "method": "kept_for_reconciliation_only",
                    "confidence": "high",
                    "evidence": {
                        "reason": f"粒度 {meta['grain']} 不是分析粒度 {analysis_grain}，不参与取数",
                    },
                    "status": "candidate",
                }
            )
            continue

        for role in MEASURE_ROLES:
            cols = role_columns.get(role) or []
            if not cols:
                continue
            if len(cols) == 1:
                combine_ad_metric = (
                    meta["role"] == "ad_platform"
                    and role in {"spend", "impressions", "clicks", "install"}
                    and role in metrics
                    and (metrics[role].get("from") or metrics[role].get("components"))
                )
                if combine_ad_metric:
                    key = role
                    existing = metrics[role]
                    components = list(existing.get("components") or [existing["from"]])
                    components.append(f"{sid}.{role}")
                    metrics[role] = {"role": role, "components": components, "agg": "sum"}
                else:
                    key = role if role not in metrics else f"{role}_{sid}"
                    metrics[key] = {"role": role, "from": f"{sid}.{role}", "agg": "sum"}
                distinct_dates = int((info.get("date_coverage") or {}).get("distinct_days") or 0)
                distinct_countries = int(info.get("distinct_countries") or 0)
                enough_evidence = distinct_dates >= 2 and distinct_countries >= 2
                inferences.append(
                    {
                        "subject": f"metrics.{key} ← {sid}.{cols[0]}",
                        "method": "role_column_unique_match",
                        "confidence": "high" if enough_evidence else "low",
                        "evidence": {
                            "column": cols[0],
                            "candidates": 1,
                            "unique_match": True,
                            "distinct_dates": distinct_dates,
                            "distinct_countries": distinct_countries,
                        },
                        "status": "candidate",
                    }
                )
                if not enough_evidence:
                    ambiguity_id = f"p0_{key}_evidence"
                    if any(item.get("id") == ambiguity_id for item in ambiguities):
                        ambiguity_id = f"p0_{key}_{sid}_evidence"
                    ambiguities.append({
                        "id": ambiguity_id,
                        "question": f"{role} 的样本证据不足，是否确认使用字段 {cols[0]}？",
                        "evidence": f"不同日期 {distinct_dates} 个、不同国家 {distinct_countries} 个；冻结门槛均为至少 2 个",
                        "suggested": "补充样本，或由用户显式确认该映射",
                        "affected": [f"metrics.{key}"],
                        "level": "P0",
                        "status": "pending",
                    })
            else:
                key = role if role not in metrics else f"{role}_{sid}"
                cands = []
                for i, col in enumerate(cols):
                    low = norm(col)
                    cands.append(
                        {
                            "id": f"c{i}",
                            "source": sid,
                            "field": col,
                            "semantics": {
                                "entity": "unique_user",
                                "window": "ltv" if "ltv" in low else "cumulative",
                                "basis": "activity" if "activity" in low else ("ltv" if "ltv" in low else "unspecified"),
                                "variant": "v1" if "_v1" in low else "none",
                            },
                            "status": "candidate",
                        }
                    )
                metrics[key] = {"role": role, "candidates": cands, "chosen": None, "confirm_required": True}
                inferences.append(
                    {
                        "subject": f"metrics.{key} ← {sid} 的 {len(cols)} 个候选列",
                        "method": "role_column_multi_match",
                        "confidence": "low",
                        "evidence": {"columns": cols, "candidates": len(cols), "unique_match": False},
                        "status": "candidate",
                    }
                )
                ambiguities.append(
                    {
                        "id": f"p0_{key}_candidate",
                        "question": f"{role} 在源数据里有 {len(cols)} 个候选字段，业务口径应选哪一个？",
                        "evidence": "候选列：" + "、".join(cols),
                        "suggested": "需用户确认；参考 references/platforms 的候选组说明",
                        "affected": [f"metrics.{key}"],
                        "level": "P0",
                        "status": "pending",
                    }
                )

    # ---------- 关联 ----------
    joins = []
    left_sources = [s for s in profile_sources if s["role"] == "ad_platform"]
    right = next((s for s in profile_sources if s["role"] == "attribution" and s["grain"] == analysis_grain), None)
    role_cols_by_sid = {item["id"]: item["role_columns"] for item in prepared}
    for left_index, left in enumerate(left_sources):
        if not right:
            break
        # 投放侧若同时暴露 creative_id 与 ad_id，默认用 creative_id 关联（平台配对属性，仍需确认）
        left_ids = role_cols_by_sid.get(left["id"], {})
        if "creative_id" in left_ids:
            join_key = "creative_id"
        elif f"{analysis_grain}_id" in left_ids:
            join_key = f"{analysis_grain}_id"
        else:
            join_key = f"{analysis_grain}_id"
        joins.append(
            {
                "id": f"{left['id']}_x_{right['id']}",
                "left": left["id"],
                "right": right["id"],
                "type": "left",
                "keys": ["date", "account_id", join_key],
                "enforce_grain": analysis_grain,
            }
        )
        ambiguities.append(
            {
                "id": "p0_join_keys" if left_index == 0 else f"p0_join_keys_{left['id']}",
                "question": (
                    f"{left['platform']} × {right['platform']} 在 {analysis_grain} 粒度的关联键是什么？"
                    f"（草稿默认用 {join_key}，但投放侧可能同时有 ad_id 与 creative_id）"
                ),
                "evidence": "关联键是平台配对的属性，不能沿用其他平台的结论；TikTok×AppsFlyer 实测为 创意素材 ID ↔ Ad ID，其他组合需重新确认",
                "suggested": f"先按 date + account_id + {join_key} 试，再用影子对账验证覆盖率",
                "affected": [f"source_plan.joins[{left['id']}_x_{right['id']}]"],
                "level": "P0",
                "status": "pending",
            }
        )

    business_goal = (
        yaml.safe_load(args.business_goal.read_text(encoding="utf-8")) or {}
        if args.business_goal else {}
    )
    if not isinstance(business_goal, dict):
        print("业务目标 YAML 顶层必须是对象", file=sys.stderr)
        return 2

    # ---------- 目标格式 ----------
    target = probe.get("target") or {}
    report_types = [
        {
            "id": "daily",
            "time_scope": {"mode": "single_period", "period": "day", "anchor": "report_date"},
            "aggregation": {
                "grain": analysis_grain,
                "group_by": ["account", "event", "country", "country_code"],
                "total_row": True,
            },
            "comparison": {"mode": "none"},
        }
    ]

    used_sids: set[str] = set()
    sheets_out: list[dict[str, Any]] = []
    derived_needed: set[str] = set()

    for sheet in target.get("sheets", []) or []:
        # probe 对 xlsx 目标输出 header_guess，对 csv 目标输出 columns；两种都要兼容
        header = [str(h) for h in (sheet.get("header_guess") or sheet.get("header") or []) if str(h).strip()]
        if not header and sheet.get("columns"):
            header = [str(c.get("name", "")) for c in sheet["columns"] if str(c.get("name", "")).strip()]
        if not header:
            continue
        has_cf = (sheet.get("conditional_formatting_rules") or 0) > 0
        is_cmp = bool(re.search(r"环比|对比|变化|comparison", sheet.get("name", ""))) or has_cf

        cols: list[dict[str, Any]] = []
        unmatched: list[str] = []
        for h in header:
            hit = match_header(h)
            if not hit:
                unmatched.append(h)
                continue
            namespace, key = hit
            if namespace == "metrics" and key not in metrics:
                # 目标列需要的指标在源里还没确定（例如 FTD(LTV) 需要单独的 LTV 口径指标），
                # 转成 P1 疑点，不要产出指向不存在指标的列
                unmatched.append(h)
                continue
            if namespace == "derived":
                if not all(dep in metrics for dep in DERIVED_DEPS.get(key, [])):
                    unmatched.append(h)
                    continue
                derived_needed.add(key)
            cols.append({"header": h, "source": f"{namespace}.{key}", "format": "number:2"})

        if not cols:
            continue

        sheets_out.append(
            {
                "id": safe_sheet_id(sheet.get("name", "sheet"), used_sids),
                "match": "daily",
                "role": "comparison" if is_cmp else "history",
                "row_key": {"field": header[0], "format": "text"},
                "placement": {"mode": "append_bottom", "same_key": "replace_in_place"},
                "column_match": "by_header",
                "group_labeling": "sparse" if is_cmp else "repeat",
                "columns": cols,
            }
        )
        inferences.append(
            {
                "subject": f"output.sheets[{sheet.get('name')}]",
                "method": "target_header_match",
                "confidence": "medium",
                "evidence": {
                    "mapped_columns": len(cols),
                    "header_total": len(header),
                    "conditional_formatting_rules": sheet.get("conditional_formatting_rules", 0),
                },
                "status": "candidate",
            }
        )
        if unmatched:
            ambiguities.append(
                {
                    "id": f"p1_unmapped_{safe_sheet_id(sheet.get('name', 'sheet'), set())}",
                    "question": f"目标 sheet「{sheet.get('name')}」中有列在源数据里找不到对应指标，应如何取值？",
                    "evidence": "未匹配列：" + "、".join(unmatched),
                    "suggested": "确认这些列是派生公式、人工填写，还是源字段改名",
                    "affected": [f"output.sheets[{sheet.get('name')}]"],
                    "level": "P1",
                    "status": "pending",
                }
            )

    derived = {
        name: {
            "expr": DERIVED_EXPR[name],
            "null_policy": {"on_zero_denominator": "blank", "on_missing_numerator": "blank"},
            "display": {"percent": True, "decimals": 2} if name in DERIVED_IS_PERCENT else {"decimals": 2},
        }
        for name in sorted(derived_needed)
        if name in DERIVED_EXPR
    }

    profile: dict[str, Any] = {
        "profile_version": 1,
        "product": {"name": args.product, "timezone": args.timezone},
        "accounts": {"include": [], "exclude": []},
        "report_types": report_types,
        "sources": profile_sources,
        "source_plan": {
            "analysis_grain": analysis_grain,
            "grains_provided": sorted({i["meta"]["grain"] for i in prepared}),
            "joins": joins,
            "additive_policy": {
                "forbid_cross_grain_sum": True,
                "additive_measures": [r for r in MEASURE_ROLES if r in metrics],
            },
            "dedupe": [
                {"source": s["id"], "key": ["date", f"{analysis_grain}_id"], "keep": "latest_snapshot"}
                for s in profile_sources
                if s["role"] == "attribution"
            ],
            "unmatched_policy": {"keep_left": True, "report": True, "label": "未匹配"},
        },
        "metrics": metrics,
        "derived": derived,
        "comparison_display": {
            "missing_previous": "zero",
            "new_when_previous_zero": "新增",
            "drop_to_zero": "-100.00%",
            "invalid_derived": "blank",
            "positive_color": "green",
            "negative_color": "red",
            "zero_color": "gray",
        },
        "output": {
            "artifacts": [
                {
                    "kind": "xlsx_workbook",
                    "write_mode": "append_to_sample",
                    "target": {"mode": "append_to_sample", "sample": "TODO_填入目标报表样本路径"},
                    "sheets": sheets_out or [
                        {
                            "id": "history_daily",
                            "match": "daily",
                            "role": "history",
                            "row_key": {"field": "日期", "format": "yyyy-mm-dd"},
                            "placement": {"mode": "append_bottom", "same_key": "replace_in_place"},
                            "columns": [{"header": "日期", "source": "$report_date", "format": "date:yyyy-mm-dd"}],
                        }
                    ],
                },
                {"kind": "csv", "role": "process_table", "path": "output/过程表_{report_type}_{date}.csv"},
                {"kind": "csv", "role": "result_table", "path": "output/结果表_{report_type}_{date}.csv"},
            ],
            "drafts": {
                "dir": "_drafts",
                "naming": "{artifact}_{report_type}_{date}_DRAFT",
                "banner": "诊断草稿-非正式交付",
            },
        },
        "validation": {
            "default_on_failure": "block",
            "checks": [
                {"id": "total_reconcile", "rule": "报告各指标合计 == 源侧过滤后合计", "tolerance": 0.01, "level": "blocking"},
                {"id": "row_count", "rule": "结果行数 == 预期分组数", "level": "blocking"},
            ],
            "draft_channel": {
                "enabled": True,
                "requires_explicit_user_request": True,
                "location": "_drafts",
                "must_not_mix_with_official": True,
            },
        },
        "inference": {
            "generated_at": probe.get("generated_at", ""),
            "candidates": inferences,
            "freeze_requirements": {
                "unique_match_or_user_confirmation": True,
                "min_distinct_dates": 2,
                "min_distinct_countries": 2,
                "no_unresolved_p0": True,
                "shadow_diff_clean": True,
            },
        },
        "ambiguities": ambiguities,
        "origin": {"type": "inferred", "verified": [], "unverified_items": len(inferences)},
    }

    if args.standard_output:
        recommendation = recommend_period(raw_sources, business_goal)
        period = selected_period(business_goal, recommendation)
        report_type = standard_report_type(period, analysis_grain)
        requested_kpis = list(business_goal.get("primary_kpis") or [])
        goal_type = str(business_goal.get("primary_goal") or "custom")
        goal_rankings = {
            "acquisition": ["install", "registration", "kyc", "first_deposit", "spend", "clicks", "impressions"],
            "efficiency": ["spend", "impressions", "clicks", "install", "registration", "first_deposit"],
            "revenue": ["revenue", "first_deposit", "trade", "spend", "install"],
        }
        ranked = [name for name in goal_rankings.get(goal_type, []) if name in metrics]
        primary_kpis = [name for name in requested_kpis if name in metrics] or ranked or list(metrics)[:8]
        sheets = standard_sheets(metrics, prepared, primary_kpis)
        missing_requested = [name for name in requested_kpis if name not in metrics]
        if missing_requested:
            ambiguities.append({
                "id": "p1_missing_requested_kpis",
                "question": "业务目标要求的部分 KPI 在源数据中没有对应字段，是否补充数据源？",
                "evidence": "缺失 KPI：" + "、".join(missing_requested),
                "suggested": "当前标准报告已移除这些 KPI；不要用 0 代替缺失字段",
                "affected": ["report_intent.primary_kpis"],
                "level": "P1",
                "status": "pending",
            })
        goal_description = str(business_goal.get("goal_description") or business_goal.get("description") or "").strip()
        if not business_goal.get("primary_goal") and len(goal_description) < 4:
            ambiguities.append({
                "id": "p0_business_goal",
                "question": "本次报告主要服务什么业务目标？",
                "evidence": "尚未提供足以选择核心 KPI 的业务目标",
                "suggested": "请选择 acquisition（拉新增长）、efficiency（投放效率）、revenue（收入增长），或填写自定义目标",
                "affected": ["report_intent.primary_goal", "report_intent.primary_kpis"],
                "level": "P0",
                "status": "pending",
            })
        available_dimensions = [
            role for role in ("date", "account_id", "country_code", "campaign_id", "adgroup_id", "ad_id")
            if any(role in item.get("role_columns", {}) for item in prepared)
        ]
        profile["report_intent"] = {
            "primary_goal": goal_type,
            "goal_description": goal_description,
            "period": period,
            "comparison": {"mode": "previous_equal_period"},
            "primary_kpis": primary_kpis,
            "dimensions": available_dimensions,
            "layout_confirmed": bool(business_goal.get("layout_confirmed", False)),
        }
        profile["report_types"] = [report_type]
        profile["comparison_display"]["missing_previous"] = "blank"
        workbook = next(a for a in profile["output"]["artifacts"] if a.get("kind") == "xlsx_workbook")
        workbook["write_mode"] = "new_file_per_period"
        workbook["target"] = {"mode": "create_copy", "sample": "standard-report-template.xlsx"}
        workbook["sheets"] = sheets
        inferences.append({
            "subject": "report_intent.period",
            "method": "date_coverage_recommendation",
            "confidence": "medium",
            "evidence": recommendation,
            "status": "candidate",
        })

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        yaml.safe_dump(profile, allow_unicode=True, sort_keys=False, width=200),
        encoding="utf-8",
    )

    p0 = sum(1 for a in ambiguities if a["level"] == "P0")
    workbook_artifact = next(
        (artifact for artifact in profile["output"]["artifacts"] if artifact.get("kind") == "xlsx_workbook"),
        {},
    )
    sheet_count = len(workbook_artifact.get("sheets") or [])
    print(f"草稿已写入 {args.out}")
    print(f"  源 {len(profile_sources)} 个，指标 {len(metrics)} 个，派生 {len(derived)} 个，sheet {sheet_count} 个")
    print(f"  推断候选 {len(inferences)} 条，疑点 {len(ambiguities)} 条（P0 {p0} 条）")
    print()
    print("下一步：validate_profile.py 看还缺什么 → 逐条问 P0 → 补完再跑影子对账")
    return 0


if __name__ == "__main__":
    sys.exit(main())
