#!/usr/bin/env python3
"""Copy a bundled example profile into a user project as an unverified draft."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from cli_compat import configure_console

configure_console()

try:
    import yaml
except ImportError:  # pragma: no cover
    print("缺少依赖 pyyaml", file=sys.stderr)
    sys.exit(2)

sys.path.insert(0, str(Path(__file__).resolve().parent))
import validate_profile as vp  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="把只读示例复制为待验证的项目 profile")
    parser.add_argument("--example", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    if not args.example.exists() or not vp.is_bundled_example(args.example):
        print("--example 必须指向技能包 examples/ 下的 profile", file=sys.stderr)
        return 2
    if args.out.exists():
        print(f"目标已存在，拒绝覆盖：{args.out}", file=sys.stderr)
        return 1

    profile = vp.load_profile(args.example)
    profile["origin"] = {
        "type": "from_example",
        "verified": [],
        "unverified_items": max(1, int((profile.get("origin") or {}).get("unverified_items") or 0)),
    }
    profile["verification"] = []
    profile.setdefault("ambiguities", []).append({
        "id": "p0_promoted_example_verification",
        "question": "该配置源自示例，是否已用当前产品数据完成 probe 与影子对账？",
        "evidence": f"来源：{args.example}",
        "suggested": "重跑 probe、fit_check、shadow_diff，并在确认后写入 verification",
        "affected": ["origin", "verification"],
        "level": "P0",
        "status": "pending",
    })
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        yaml.safe_dump(profile, allow_unicode=True, sort_keys=False, width=160),
        encoding="utf-8",
    )
    print(f"已生成待验证草稿：{args.out}")
    print("下一步：probe → verify_profile_fit → shadow_diff → 用户确认；未完成前生成器会阻断。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
