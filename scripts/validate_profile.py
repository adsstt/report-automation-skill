#!/usr/bin/env python3
"""配方单校验器（report profile validator）

两层校验：
  1. 结构层：用 references/report-profile-schema.json 做 JSON Schema 校验
  2. 语义层：校验 schema 表达不了的红线，例如
     - 引用了不存在的 source / metric / derived
     - confirm_required 的指标还没选定候选字段（chosen 为空）
     - 跨粒度 join、跨层相加
     - 含快照的归因源没有声明去重策略
     - output 列引用了未定义的指标
     - from_example 却没有 probe + shadow_diff 验证证据

退出码：0 = 通过（可能有 warning）；1 = 有 FAIL；2 = 用法或读取错误

用法：
    python scripts/validate_profile.py <profile.yaml>
    python scripts/validate_profile.py <profile.yaml> --json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover
    print("缺少依赖 pyyaml，请先执行: pip install pyyaml jsonschema", file=sys.stderr)
    sys.exit(2)

try:
    from jsonschema import Draft202012Validator
except ImportError:  # pragma: no cover
    print("缺少依赖 jsonschema，请先执行: pip install pyyaml jsonschema", file=sys.stderr)
    sys.exit(2)


SCHEMA_PATH = Path(__file__).resolve().parents[1] / "references" / "report-profile-schema.json"
EXAMPLES_ROOT = Path(__file__).resolve().parents[1] / "examples"


def is_bundled_example(path: Path) -> bool:
    try:
        path.resolve().relative_to(EXAMPLES_ROOT.resolve())
        return True
    except ValueError:
        return False
GRAINS = {"campaign", "adgroup", "ad", "creative"}
IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


class Issue:
    def __init__(self, level: str, code: str, message: str, where: str = ""):
        self.level = level
        self.code = code
        self.message = message
        self.where = where

    def as_dict(self) -> dict[str, str]:
        return {"level": self.level, "code": self.code, "where": self.where, "message": self.message}

    def __str__(self) -> str:
        loc = f" [{self.where}]" if self.where else ""
        return f"{self.level:5s} {self.code}{loc}: {self.message}"


def load_profile(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError("配方单根节点必须是 mapping")
    return data


def schema_issues(profile: dict[str, Any]) -> list[Issue]:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    issues: list[Issue] = []
    for err in sorted(validator.iter_errors(profile), key=lambda e: list(e.absolute_path)):
        where = "/".join(str(p) for p in err.absolute_path) or "<root>"
        issues.append(Issue("FAIL", "SCHEMA", err.message, where))
    return issues


def _source_ids(profile: dict[str, Any]) -> set[str]:
    return {s.get("id") for s in profile.get("sources", []) if s.get("id")}


def _grain_of(profile: dict[str, Any], source_id: str) -> str | None:
    for s in profile.get("sources", []):
        if s.get("id") == source_id:
            return s.get("grain")
    return None


def _canonicals(profile: dict[str, Any], source_id: str) -> set[str]:
    for s in profile.get("sources", []):
        if s.get("id") == source_id:
            return {row.get("canonical") for row in s.get("observed_schema", []) if row.get("canonical")}
    return set()


def semantic_issues(profile: dict[str, Any]) -> list[Issue]:
    issues: list[Issue] = []

    add = lambda level, code, msg, where="": issues.append(Issue(level, code, msg, where))

    sources = profile.get("sources", [])
    source_ids = _source_ids(profile)
    plan = profile.get("source_plan", {}) or {}
    metrics = profile.get("metrics", {}) or {}
    dimensions = profile.get("dimensions", {}) or {}
    derived = profile.get("derived", {}) or {}

    # ---- 1. 源 id 唯一 / 引用存在 ----
    seen: set[str] = set()
    for s in sources:
        sid = s.get("id")
        if sid in seen:
            add("FAIL", "DUP_SOURCE", f"source id 重复：{sid}", f"sources[{sid}]")
        seen.add(sid)

    for name, m in metrics.items():
        frm = m.get("from")
        if frm:
            src = frm.split(".", 1)[0]
            if src not in source_ids:
                add("FAIL", "UNKNOWN_SOURCE", f"metrics.{name}.from 引用了不存在的 source：{src}", f"metrics.{name}")
        for cand in m.get("candidates", []) or []:
            if cand.get("source") not in source_ids:
                add("FAIL", "UNKNOWN_SOURCE", f"metrics.{name} 候选字段引用了不存在的 source：{cand.get('source')}", f"metrics.{name}")

    for name, dim in dimensions.items():
        frm = dim.get("from")
        if frm:
            src = frm.split(".", 1)[0]
            if src not in source_ids:
                add("FAIL", "UNKNOWN_SOURCE", f"dimensions.{name}.from 引用了不存在的 source：{src}", f"dimensions.{name}")

    for j in plan.get("joins", []) or []:
        for side in ("left", "right"):
            if j.get(side) not in source_ids:
                add("FAIL", "UNKNOWN_SOURCE", f"join {j.get('id')} 的 {side} 引用了不存在的 source：{j.get(side)}", "source_plan.joins")

    for d in plan.get("dedupe", []) or []:
        if d.get("source") not in source_ids:
            add("FAIL", "UNKNOWN_SOURCE", f"dedupe 引用了不存在的 source：{d.get('source')}", "source_plan.dedupe")

    # ---- 2. confirm_required 指标必须已选定候选 ----
    for name, m in metrics.items():
        if m.get("confirm_required") is True:
            cands = m.get("candidates") or []
            if not cands:
                add("FAIL", "MISSING_CANDIDATES", f"metrics.{name} 声明了 confirm_required，但没有候选字段列表", f"metrics.{name}")
            chosen = m.get("chosen")
            if chosen in (None, ""):
                add(
                    "FAIL",
                    "UNRESOLVED_P0",
                    f"metrics.{name} 尚未选定口径（chosen 为空）。这属于 P0 阻断项，必须由用户确认后才能出稿",
                    f"metrics.{name}",
                )
            else:
                cand_ids = {c.get("id") for c in cands}
                if chosen not in cand_ids:
                    add("FAIL", "BAD_CHOSEN", f"metrics.{name}.chosen={chosen} 不在候选 id 中：{sorted(cand_ids)}", f"metrics.{name}")

    # ---- 3. 引用型指标的 candidate_ref ----
    for name, m in metrics.items():
        ref = m.get("candidate_ref")
        if not ref:
            continue
        found = any(ref in {c.get("id") for c in (other.get("candidates") or [])} for other in metrics.values())
        if not found:
            add("FAIL", "BAD_CANDIDATE_REF", f"metrics.{name}.candidate_ref={ref} 在任何指标候选列表里都找不到", f"metrics.{name}")

    # ---- 4. 粒度：analysis_grain 必须在 grains_provided 中；join 必须同粒度 ----
    analysis = plan.get("analysis_grain")
    provided = plan.get("grains_provided") or []
    if analysis and provided and analysis not in provided:
        add("FAIL", "GRAIN_MISMATCH", f"source_plan.analysis_grain={analysis} 不在 grains_provided={provided} 中", "source_plan")

    if len(set(provided)) > 1:
        policy = plan.get("additive_policy") or {}
        if policy.get("forbid_cross_grain_sum") is not True:
            add("FAIL", "CROSS_GRAIN_SUM", "提供了多个粒度，但没有禁止跨层相加（forbid_cross_grain_sum 必须为 true）", "source_plan.additive_policy")

    for j in plan.get("joins", []) or []:
        jid = j.get("id")
        enforce = j.get("enforce_grain")
        if analysis and enforce and enforce != analysis:
            add("FAIL", "JOIN_GRAIN", f"join {jid} 的 enforce_grain={enforce} 与 analysis_grain={analysis} 不一致；只允许在分析粒度上关联", "source_plan.joins")
        lg, rg = _grain_of(profile, j.get("left")), _grain_of(profile, j.get("right"))
        if lg and rg and lg != rg:
            add("FAIL", "JOIN_GRAIN", f"join {jid} 两侧粒度不同（{j.get('left')}={lg}, {j.get('right')}={rg}）；跨粒度关联会导致重复计数", "source_plan.joins")
        if lg and analysis and lg != analysis:
            add("FAIL", "JOIN_GRAIN", f"join {jid} 使用的粒度 {lg} 不是分析粒度 {analysis}", "source_plan.joins")

    # ---- 5. 归因快照源必须有去重策略 ----
    dedupe_sources = {d.get("source") for d in (plan.get("dedupe") or [])}
    for s in sources:
        snap = s.get("snapshot") or {}
        if s.get("role") == "attribution" and snap.get("is_snapshot") is True:
            if s.get("id") not in dedupe_sources:
                add("FAIL", "MISSING_DEDUPE", f"归因源 {s.get('id')} 是快照数据，必须声明 dedupe（去重键 + 保留策略）", f"source_plan.dedupe")
            if not snap.get("snapshot_field"):
                add("FAIL", "MISSING_SNAPSHOT_FIELD", f"归因源 {s.get('id')} 是快照数据，必须声明 snapshot_field", f"sources[{s.get('id')}].snapshot")

    # ---- 6. metric.from 的 canonical 必须在 observed_schema 里 ----
    for name, m in metrics.items():
        frm = m.get("from")
        if not frm or "." not in frm:
            continue
        src, canon = frm.split(".", 1)
        if src in source_ids:
            known = _canonicals(profile, src)
            if known and canon not in known:
                add("WARN", "UNKNOWN_CANONICAL", f"metrics.{name}.from 的字段 {canon} 没有出现在 {src}.observed_schema 中", f"metrics.{name}")

    # ---- 7. derived.expr 引用的标识符必须存在 ----
    for name, d in derived.items():
        expr = d.get("expr") or ""
        for ident in IDENT_RE.findall(expr):
            if ident in {"and", "or", "not"}:
                continue
            if ident not in metrics and ident not in derived:
                add("FAIL", "BAD_EXPR", f"derived.{name}.expr 引用了不存在的指标：{ident}", f"derived.{name}")

    # ---- 8. output 列引用校验 ----
    report_ids = {rt.get("id") for rt in (profile.get("report_types") or [])}
    group_keys = set()
    for rt in profile.get("report_types") or []:
        group_keys |= set((rt.get("aggregation") or {}).get("group_by") or [])
        scope = rt.get("time_scope") or {}
        if scope.get("period") == "custom":
            start, end = scope.get("start"), scope.get("end")
            if not start or not end:
                add("FAIL", "CUSTOM_PERIOD_RANGE", f"report_type {rt.get('id')} 的自定义周期缺少 start/end", f"report_types[{rt.get('id')}].time_scope")
            else:
                try:
                    if date.fromisoformat(start) > date.fromisoformat(end):
                        add("FAIL", "CUSTOM_PERIOD_RANGE", f"report_type {rt.get('id')} 的 start 晚于 end", f"report_types[{rt.get('id')}].time_scope")
                except ValueError:
                    add("FAIL", "CUSTOM_PERIOD_RANGE", f"report_type {rt.get('id')} 的自定义日期格式必须为 YYYY-MM-DD", f"report_types[{rt.get('id')}].time_scope")

    comparison_names = {
        "base", "delta", "delta_pct",
        "base.spend", "delta.spend", "delta_pct.spend",
    }

    for art in (profile.get("output") or {}).get("artifacts", []) or []:
        if art.get("kind") != "xlsx_workbook":
            continue
        for sheet in art.get("sheets", []) or []:
            sid = sheet.get("id")
            allowed_group_keys = group_keys | set(sheet.get("group_by") or [])
            if sheet.get("match") not in report_ids:
                add("FAIL", "BAD_SHEET_MATCH", f"sheet {sid} 的 match={sheet.get('match')} 没有对应的 report_type", f"output.sheets[{sid}]")
            header_names = [c.get("header") for c in sheet.get("columns", []) if c.get("header")]
            if len(header_names) != len(set(header_names)):
                add("FAIL", "DUP_COLUMN", f"sheet {sid} 存在重复列名", f"output.sheets[{sid}]")

            total = sheet.get("total_row") or {}
            if total.get("enabled") and total.get("label_column"):
                if total.get("label_column") not in header_names:
                    add("FAIL", "BAD_TOTAL_LABEL", f"sheet {sid} 的总计行 label_column={total.get('label_column')} 不在列名中", f"output.sheets[{sid}]")

            for col in sheet.get("columns", []) or []:
                src = col.get("source") or ""
                hdr = col.get("header")
                if src.startswith("metrics."):
                    key = src.split(".", 1)[1]
                    if key not in metrics:
                        add("FAIL", "BAD_COLUMN", f"sheet {sid} 列「{hdr}」引用了不存在的指标 {key}", f"output.sheets[{sid}]")
                elif src.startswith("derived."):
                    key = src.split(".", 1)[1]
                    if key not in derived:
                        add("FAIL", "BAD_COLUMN", f"sheet {sid} 列「{hdr}」引用了不存在的派生指标 {key}", f"output.sheets[{sid}]")
                elif src.startswith("group."):
                    key = src.split(".", 1)[1]
                    if key not in allowed_group_keys:
                        add("FAIL", "BAD_COLUMN", f"sheet {sid} 列「{hdr}」引用了未在 group_by 中声明的维度 {key}", f"output.sheets[{sid}]")
                elif src.startswith("dimensions."):
                    key = src.split(".", 1)[1]
                    if key not in dimensions:
                        add("FAIL", "BAD_COLUMN", f"sheet {sid} 列「{hdr}」引用了不存在的展示维度 {key}", f"output.sheets[{sid}]")
                elif src.startswith("comparison."):
                    if src.split(".", 1)[1] not in comparison_names:
                        add("WARN", "UNKNOWN_COMPARISON", f"sheet {sid} 列「{hdr}」引用了未识别的对比字段 {src}", f"output.sheets[{sid}]")

    # ---- 8b. 口径分段（effective）自洽性 ----
    for name, m in metrics.items():
        segs = m.get("effective") or []
        if not segs:
            continue
        has_fallback = any("from" not in s and "before" in s for s in segs)
        if not has_fallback:
            add("FAIL", "EFFECTIVE_NO_FALLBACK",
                f"metrics.{name}.effective 缺少兜底分段（只有 from、没有 before）：早于最早生效日的报表将无字段可用",
                f"metrics.{name}")
        for s in segs:
            fld = s.get("field")
            if fld and fld.split(".", 1)[0] not in source_ids:
                add("FAIL", "UNKNOWN_SOURCE",
                    f"metrics.{name}.effective 引用了不存在的 source：{fld.split('.', 1)[0]}",
                    f"metrics.{name}")
        # 分段区间不得重叠（按起点排序后，前一段的终点必须 ≤ 后一段的起点）
        dated = [s for s in segs if s.get("from")]
        dated.sort(key=lambda s: s["from"])
        for prev, cur in zip(dated, dated[1:]):
            if "before" not in prev or prev["before"] > cur["from"]:
                add("WARN", "EFFECTIVE_OVERLAP",
                    f"metrics.{name}.effective 分段可能重叠：{prev.get('from')} 起与 {cur.get('from')} 起",
                    f"metrics.{name}")

    # ---- 8c. 无目标模板标准报告门禁 ----
    intent = profile.get("report_intent") or {}
    if intent:
        period = intent.get("period") or {}
        if not period.get("confirmed") or period.get("type") not in {"daily", "weekly", "monthly", "custom"}:
            suggestion = (period.get("recommendation") or {}).get("suggested_type") or "daily / weekly / monthly / custom"
            add(
                "FAIL",
                "UNCONFIRMED_PERIOD",
                f"时间周期尚未由用户显式选择；建议 {suggestion}。确认前不生成模板、草稿或正式报告",
                "report_intent.period",
            )
        if period.get("type") == "custom" and period.get("confirmed"):
            start, end = period.get("start"), period.get("end")
            if not start or not end:
                add("FAIL", "CUSTOM_PERIOD_RANGE", "自定义周期必须同时填写 start 与 end", "report_intent.period")
            else:
                try:
                    if date.fromisoformat(start) > date.fromisoformat(end):
                        add("FAIL", "CUSTOM_PERIOD_RANGE", "自定义周期 start 不能晚于 end", "report_intent.period")
                except ValueError:
                    add("FAIL", "CUSTOM_PERIOD_RANGE", "自定义周期必须使用 YYYY-MM-DD", "report_intent.period")
        for name in intent.get("primary_kpis") or []:
            if name not in metrics:
                add("FAIL", "UNKNOWN_PRIMARY_KPI", f"核心 KPI {name} 在 metrics 中不存在", "report_intent.primary_kpis")
        workbook_artifacts = [
            artifact for artifact in (profile.get("output") or {}).get("artifacts", [])
            if artifact.get("kind") == "xlsx_workbook"
        ]
        names = [
            sheet.get("target_sheet")
            for artifact in workbook_artifacts
            for sheet in artifact.get("sheets") or []
        ]
        if names != ["管理摘要", "明细数据", "趋势环比"]:
            add("FAIL", "STANDARD_SHEETS", "无模板标准报告必须且只能包含：管理摘要、明细数据、趋势环比", "output.artifacts")
        if period.get("confirmed") and not intent.get("layout_confirmed"):
            add("FAIL", "UNCONFIRMED_LAYOUT", "标准报告版式尚未确认，只允许生成带标识的 DRAFT 预览", "report_intent.layout_confirmed")

    # ---- 9. origin 与验证证据 ----
    origin = profile.get("origin") or {}
    if origin.get("type") == "from_example":
        passed = {v.get("type") for v in (profile.get("verification") or []) if v.get("result") == "pass"}
        for need in ("probe", "shadow_diff"):
            if need not in passed:
                add(
                    "FAIL",
                    "MISSING_VERIFICATION",
                    f"origin.type=from_example，但缺少 {need} 的 pass 验证证据；示例配置必须重跑验证才能冻结",
                    "verification",
                )

    # ---- 9b. P0 疑点未解决即阻断 ----
    for amb in profile.get("ambiguities") or []:
        if amb.get("level") == "P0" and amb.get("status") == "pending":
            add(
                "FAIL",
                "UNRESOLVED_P0",
                f"P0 疑点未解决：{amb.get('id')} — {amb.get('question')}",
                "ambiguities",
            )

    # ---- 10. 未被使用的 source（提示，不阻断）----
    used: set[str] = set()
    for m in metrics.values():
        if m.get("from"):
            used.add(m["from"].split(".", 1)[0])
        for c in m.get("candidates") or []:
            used.add(c.get("source"))
    for j in plan.get("joins") or []:
        used.add(j.get("left"))
        used.add(j.get("right"))
    for s in sources:
        if s.get("id") not in used:
            add("WARN", "UNUSED_SOURCE", f"source {s.get('id')} 未参与任何取数或关联（可能只用于对账）", f"sources[{s.get('id')}]")

    # ---- 11. required_columns 应能在 observed_schema 或关联键中找到 ----
    for s in sources:
        known = _canonicals(profile, s.get("id"))
        if not known:
            continue
        for need in s.get("required_columns") or []:
            if need not in known:
                add("WARN", "REQUIRED_COLUMN_UNMAPPED", f"source {s.get('id')} 的 required_columns 含 {need}，但 observed_schema 里没有该 canonical", f"sources[{s.get('id')}]")

    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description="校验报表配方单（profile.yaml）")
    parser.add_argument("profile", type=Path, help="配方单文件路径")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    parser.add_argument("--strict", action="store_true", help="把 WARN 也当作失败")
    args = parser.parse_args()

    if not args.profile.exists():
        print(f"找不到配方单：{args.profile}", file=sys.stderr)
        return 2

    try:
        profile = load_profile(args.profile)
    except Exception as exc:  # noqa: BLE001
        print(f"读取失败：{exc}", file=sys.stderr)
        return 2

    issues = schema_issues(profile) + semantic_issues(profile)
    fails = [i for i in issues if i.level == "FAIL"]
    warns = [i for i in issues if i.level == "WARN"]

    if args.json:
        print(json.dumps(
            {
                "profile": str(args.profile),
                "ok": not fails and not (args.strict and warns),
                "fails": [i.as_dict() for i in fails],
                "warns": [i.as_dict() for i in warns],
            },
            ensure_ascii=False,
            indent=2,
        ))
    else:
        print(f"校验对象：{args.profile}")
        if not issues:
            print("结果：通过（无问题）")
        for i in issues:
            print(str(i))
        print()
        print(f"结果：{len(fails)} 项阻断，{len(warns)} 项提示")
        if not fails:
            print("结论：配方单可用于生成报表" if not (args.strict and warns) else "结论：strict 模式下提示项视为失败")

    if fails:
        return 1
    if args.strict and warns:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
