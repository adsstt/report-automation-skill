#!/usr/bin/env python3
"""Register a validated report profile in the workspace product library."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from cli_compat import configure_console

configure_console()

sys.path.insert(0, str(Path(__file__).resolve().parent))
import update_product_knowledge as knowledge  # noqa: E402
import validate_profile as vp  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="把已定稿 profile 登记到工作区产品知识库")
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--profile", required=True, type=Path)
    parser.add_argument("--alias", action="append", default=[])
    parser.add_argument("--force", action="store_true", help="覆盖已登记的同名产品 profile")
    args = parser.parse_args()

    workspace = args.workspace.resolve()
    source = args.profile.resolve()
    if not source.exists():
        print(f"找不到 profile：{source}", file=sys.stderr)
        return 2
    try:
        source.relative_to(workspace)
        profile = knowledge.load_yaml(source)
    except ValueError as exc:
        print(f"profile 必须位于 workspace 内且格式有效：{exc}", file=sys.stderr)
        return 2
    failures = [
        issue for issue in (vp.schema_issues(profile) + vp.semantic_issues(profile))
        if issue.level == "FAIL"
    ]
    if failures:
        print("profile 尚未达到可登记状态：", file=sys.stderr)
        for issue in failures:
            print(f"  {issue}", file=sys.stderr)
        return 1
    product_name = str((profile.get("product") or {}).get("name") or "").strip()
    if not product_name:
        print("profile.product.name 不能为空", file=sys.stderr)
        return 2
    slug = knowledge.slugify(product_name)
    destination = workspace / ".report-automation" / "products" / slug / "profile.yaml"
    if destination.exists() and not args.force:
        print(f"产品已登记，未覆盖：{destination}（需要覆盖时使用 --force）", file=sys.stderr)
        return 1
    timestamp = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    update = {"product": product_name, "aliases": args.alias}
    try:
        registry_path, registry = knowledge.registry_entry(
            workspace, destination.resolve(), update, timestamp
        )
        knowledge.atomic_write(destination, source.read_bytes())
        knowledge.atomic_write(registry_path, knowledge.dump_yaml(registry))
    except (OSError, ValueError) as exc:
        print(f"登记产品失败：{exc}", file=sys.stderr)
        return 2
    print(f"产品已登记：{product_name}")
    print(f"profile：{destination.resolve()}")
    print(f"产品目录：{registry_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
