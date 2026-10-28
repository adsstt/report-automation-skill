#!/usr/bin/env python3
"""示例复用适配度检查

回答一个问题：**能不能把 examples/ 里某份 profile 拿来给新产品用？**

四个维度打分，任一项不达标就拒绝复用：
  1. 平台组合   —— 源平台集合必须完全一致
  2. 分析粒度   —— analysis_grain 必须一致（且探源确实支持）
  3. 目标结构   —— 目标 sheet 名与列必须能对上（用 verify_format 的匹配逻辑）
  4. 字段覆盖   —— profile 用到的源字段必须在探源结果里存在

用法：
    python scripts/verify_profile_fit.py --profile examples/minimal/profile.yaml \
        --probe runtime/probe.json --target <用户的目标报表.xlsx>

退出码：0=可以复用（仍需重跑对账）；1=不能复用；2=用法错误
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from cli_compat import configure_console

configure_console()

try:
    import yaml
except ImportError:  # pragma: no cover
    print("缺少依赖 pyyaml", file=sys.stderr)
    sys.exit(2)

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import verify_format as vf  # noqa: E402

CATALOG_PATH = HERE.parent / "references" / "platforms" / "catalog.yaml"


def platform_catalog() -> dict:
    return (yaml.safe_load(CATALOG_PATH.read_text(encoding="utf-8")) or {}).get("platforms", {})


def score_platforms(profile: dict, probe: dict) -> tuple[float, str]:
    want = {s["platform"] for s in profile.get("sources", [])}
    got = set()
    catalog = platform_catalog()
    for src in probe.get("sources", []):
        name = str(Path(src.get("path", "")).name).lower()
        cols = " ".join(str(c.get("name", "")).lower() for c in src.get("columns", []) or [])
        text = name + " " + cols
        matches = [
            platform for platform, config in catalog.items()
            if any(str(pattern).lower() in text for pattern in config.get("patterns") or [])
        ]
        if len(matches) == 1:
            got.add(matches[0])
    if not got:
        return 0.0, f"无法从探源结果识别平台（profile 要求 {sorted(want)}）"
    if want == got:
        return 1.0, f"平台组合一致：{sorted(got)}"
    return 0.0, f"平台组合不一致：profile 要求 {sorted(want)}，实际探到 {sorted(got)}"


def score_grain(profile: dict, probe: dict) -> tuple[float, str]:
    want = (profile.get("source_plan") or {}).get("analysis_grain")
    grains = set()
    for src in probe.get("sources", []):
        for g in src.get("grain_candidates", []) or []:
            grains.add(g.get("grain"))
    if want in grains:
        return 1.0, f"分析粒度 {want} 在探源结果中可用"
    return 0.0, f"分析粒度 {want} 不在探源结果里（探到 {sorted(x for x in grains if x)}）"


def score_target(profile: dict, target: Path) -> tuple[float, str]:
    artifacts = [a for a in (profile.get("output") or {}).get("artifacts", []) if a.get("kind") == "xlsx_workbook"]
    if not artifacts:
        return 1.0, "profile 不含 Excel 产物，跳过目标结构检查"
    book = vf.load_book(target)
    total = matched = 0
    missing_sheets = []
    for sheet in artifacts[0].get("sheets", []):
        name = sheet.get("target_sheet") or ""
        headers = book.get(name)
        if headers is None:
            missing_sheets.append(name)
            total += len(sheet.get("columns", []))
            continue
        for col in sheet.get("columns", []):
            total += 1
            if vf.match_column(col, headers)[0] is not None:
                matched += 1
    ratio = matched / max(total, 1)
    if ratio >= 0.95 and not missing_sheets:
        return 1.0, f"目标结构匹配 {matched}/{total} 列"
    if ratio >= 0.8:
        return 0.5, f"目标结构部分匹配 {matched}/{total} 列；缺 sheet {missing_sheets[:3]}"
    return 0.0, f"目标结构不匹配（{matched}/{total} 列；缺 sheet {missing_sheets[:3]}）"


def score_fields(profile: dict, probe: dict) -> tuple[float, str]:
    available = set()
    for src in probe.get("sources", []):
        for col in src.get("columns", []) or []:
            for role in col.get("role_candidates") or []:
                available.add(role)
    needed = set()
    for metric in (profile.get("metrics") or {}).values():
        if metric.get("from"):
            needed.add(metric["from"].split(".", 1)[1])
        for cand in metric.get("candidates") or []:
            for role in ("registration", "kyc", "first_deposit", "trade", "revenue"):
                if role in str(cand.get("field", "")).lower() or role in str(cand.get("semantics", {})):
                    needed.add(role)
    if not needed:
        return 1.0, "profile 未引用可识别的源角色字段"
    hit = needed & available
    ratio = len(hit) / len(needed)
    if ratio >= 0.9:
        return 1.0, f"字段覆盖 {len(hit)}/{len(needed)}"
    if ratio >= 0.7:
        return 0.5, f"字段覆盖偏低 {len(hit)}/{len(needed)}"
    return 0.0, f"字段覆盖不足 {len(hit)}/{len(needed)}（缺 {sorted(needed - available)[:5]}）"


def main() -> int:
    ap = argparse.ArgumentParser(description="示例复用适配度检查")
    ap.add_argument("--profile", required=True, type=Path)
    ap.add_argument("--probe", type=Path, help="probe_sources.py 产出的 JSON")
    ap.add_argument("--target", type=Path, help="用户的目标报表")
    args = ap.parse_args()

    profile = yaml.safe_load(args.profile.read_text(encoding="utf-8"))
    probe = json.loads(args.probe.read_text(encoding="utf-8")) if args.probe else {"sources": []}

    scores: dict[str, tuple[float, str]] = {}
    if args.probe:
        scores["platform"] = score_platforms(profile, probe)
        scores["grain"] = score_grain(profile, probe)
        scores["field_coverage"] = score_fields(profile, probe)
    if args.target:
        if not args.target.exists():
            print(f"找不到目标文件：{args.target}", file=sys.stderr)
            return 2
        scores["target_shape"] = score_target(profile, args.target)

    if not scores:
        print("至少给 --probe 或 --target 之一", file=sys.stderr)
        return 2

    print(f"适配度检查：{args.profile}")
    failed = []
    for name, (value, reason) in scores.items():
        mark = "OK" if value >= 1.0 else ("WARN" if value >= 0.5 else "FAIL")
        print(f"  {mark} {name:16s} {value:.2f}  {reason}")
        if value < 0.5:
            failed.append(name)

    print()
    if failed:
        print(f"结论：不能直接复用（{', '.join(failed)} 不达标）")
        print("请按 SKILL.md 走完整 onboarding，或新增/修改对应配置后再试。")
        return 1
    if any(v < 1.0 for v, _ in scores.values()):
        print("结论：可以复用，但必须重跑 probe + 影子对账，并在交付说明标注未验证项。")
    else:
        print("结论：适配度全绿，可以复用；仍需重跑对账并把 origin.type 改成 from_example。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
