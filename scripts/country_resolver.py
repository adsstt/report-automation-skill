#!/usr/bin/env python3
"""国家码三层解析器

优先级（高 → 低）：
  1. profile 覆盖层      —— 单个客户的特殊写法
  2. 业务聚合层          —— ME / LATAM 这类区域码、组合特例、客户习惯译名
  3. ISO 基础层          —— 码 → 标准中文名
  4. Unknown + 告警

不认识的码不再静默变成 Unknown：调用方能拿到 `unknowns` 列表，用于产出校验清单。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None


LOOKUP_DIR = Path(__file__).resolve().parents[1] / "references" / "lookups"


class CountryResolver:
    def __init__(self, profile_rules: dict[str, Any] | None = None):
        self.profile = (profile_rules or {}).get("country_lookup") or {}
        self.iso: dict[str, str] = {}
        self.aggregates: dict[str, str] = {}
        self.aliases: dict[str, str] = {}
        self.special_combos: dict[str, dict[str, str]] = {}
        self.display_overrides: dict[str, str] = {}
        self.unknown_policy: dict[str, Any] = {}
        self._load()

    def _load(self) -> None:
        iso_path = LOOKUP_DIR / "iso3166-1.zh.json"
        if iso_path.exists():
            data = json.loads(iso_path.read_text(encoding="utf-8"))
            self.iso = {k.upper(): v for k, v in (data.get("codes") or {}).items()}
        biz_path = LOOKUP_DIR / "business-aggregates.yaml"
        if biz_path.exists() and yaml is not None:
            data = yaml.safe_load(biz_path.read_text(encoding="utf-8")) or {}
            self.aggregates = {k.upper(): v for k, v in (data.get("aggregates") or {}).items()}
            self.aliases = {k.upper(): v.upper() for k, v in (data.get("aliases") or {}).items()}
            self.special_combos = {
                k.upper(): v for k, v in (data.get("special_combos") or {}).items()
            }
            self.display_overrides = {
                k.upper(): v for k, v in (data.get("display_overrides") or {}).items()
            }
            self.unknown_policy = data.get("unknown_policy") or {}

    # ---- 单码 ----
    def resolve_code(self, code: str) -> tuple[str | None, str]:
        """返回 (名称, 来源层)。解析不出来时返回 (None, 'unknown')。"""
        c = code.strip().upper()
        if not c:
            return None, "empty"
        # 1) profile 覆盖层
        pmap = {k.upper(): v for k, v in (self.profile.get("map") or {}).items()}
        if c in pmap:
            return pmap[c], "profile"
        # 2) 客户习惯译名 → 3) 聚合码 → 4) 别名 → 5) ISO
        if c in self.display_overrides:
            return self.display_overrides[c], "business_display"
        if c in self.aggregates:
            return self.aggregates[c], "business_aggregate"
        target = self.aliases.get(c, c)
        if target in self.iso:
            return self.iso[target], "iso"
        if target in self.aggregates:
            return self.aggregates[target], "business_aggregate"
        return None, "unknown"

    # ---- 组合（A&B&C 形式）----
    def resolve_group(self, value: str) -> dict[str, Any]:
        raw = str(value or "").strip()
        if not raw:
            return {"name": "Unknown", "code": "Unknown", "layers": [], "unknowns": []}

        # 整组特例最优先
        if raw.upper() in self.special_combos:
            combo = self.special_combos[raw.upper()]
            return {
                "name": combo.get("name", raw),
                "code": combo.get("code", raw.upper()),
                "layers": ["business_special_combo"],
                "unknowns": [],
            }

        names, codes, layers, unknowns = [], [], [], []
        for part in raw.split("&"):
            token = part.strip().upper()
            if not token:
                continue
            name, layer = self.resolve_code(token)
            if name is None:
                unknowns.append(token)
                names.append(self.unknown_policy.get("placeholder", "Unknown"))
                codes.append(token)
                layers.append("unknown")
            else:
                names.append(name)
                codes.append(token)
                layers.append(layer)

        if unknowns and len(unknowns) == len(codes):
            return {"name": "Unknown", "code": "Unknown", "layers": layers, "unknowns": unknowns}
        return {"name": "&".join(names), "code": "&".join(codes), "layers": layers, "unknowns": unknowns}


if __name__ == "__main__":
    import sys

    r = CountryResolver()
    for sample in ("ARG&MX", "GB&CA", "ME", "LATAM", "PA&DO&CR&KE&BD&LK", "ZZ", "VNM", "AU"):
        print(f"{sample:26s} → {r.resolve_group(sample)}")
    sys.exit(0)
