#!/usr/bin/env python3
"""Resolve a persisted product name or alias to its report profile."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from cli_compat import configure_console

configure_console()

try:
    import yaml
except ImportError:  # pragma: no cover
    print("缺少依赖 pyyaml，请先执行 pip install pyyaml", file=sys.stderr)
    raise SystemExit(2)


def load_registry(path: Path) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"无法读取产品目录：{exc}") from exc
    if value.get("registry_version") != 1 or not isinstance(value.get("products"), dict):
        raise ValueError("产品目录格式无效")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="按产品名或别名查找已保存的报表 profile")
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--product")
    parser.add_argument("--list", action="store_true", help="列出已登记产品")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    managed = args.workspace.resolve() / ".report-automation"
    registry_path = managed / "product-registry.yaml"
    if not registry_path.exists():
        print(f"尚未建立产品目录：{registry_path}", file=sys.stderr)
        return 1
    try:
        registry = load_registry(registry_path)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2

    if args.list:
        items = [
            {"id": product_id, "name": entry.get("name"), "aliases": entry.get("aliases") or []}
            for product_id, entry in registry["products"].items()
        ]
        print(json.dumps(items, ensure_ascii=False, indent=2) if args.json else "\n".join(
            f"{item['id']}\t{item['name']}\t{', '.join(item['aliases'])}" for item in items
        ))
        return 0
    if not args.product:
        parser.error("需要 --product，或使用 --list")

    query = args.product.strip().casefold()
    matches: list[tuple[str, dict[str, Any], str]] = []
    for product_id, entry in registry["products"].items():
        if query == str(entry.get("name") or "").casefold():
            matches.append((product_id, entry, "name"))
            continue
        if query == product_id.casefold():
            matches.append((product_id, entry, "id"))
            continue
        if any(query == str(alias).casefold() for alias in (entry.get("aliases") or [])):
            matches.append((product_id, entry, "alias"))
    if not matches:
        print(f"产品目录中找不到：{args.product}", file=sys.stderr)
        return 1
    if len(matches) > 1:
        print(f"产品名或别名不唯一：{args.product}", file=sys.stderr)
        return 1
    product_id, entry, matched_by = matches[0]
    profile_path = (managed / str(entry.get("profile") or "")).resolve()
    try:
        profile_path.relative_to(managed.resolve())
    except ValueError:
        print("产品目录中的 profile 路径越出 .report-automation，已拒绝", file=sys.stderr)
        return 2
    if not profile_path.exists():
        print(f"产品 profile 不存在：{profile_path}", file=sys.stderr)
        return 1
    result = {
        "id": product_id,
        "product": entry.get("name"),
        "profile": str(profile_path),
        "matched_by": matched_by,
        "updated_at": entry.get("updated_at"),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else profile_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
