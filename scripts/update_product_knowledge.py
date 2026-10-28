#!/usr/bin/env python3
"""Safely persist AI-extracted product knowledge into a report profile."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cli_compat import configure_console

configure_console()

try:
    import yaml
    from jsonschema import Draft202012Validator
except ImportError:  # pragma: no cover
    print("缺少依赖 pyyaml/jsonschema，请先执行 pip install -r requirements.txt", file=sys.stderr)
    raise SystemExit(2)

sys.path.insert(0, str(Path(__file__).resolve().parent))
import validate_profile as vp  # noqa: E402

UPDATE_SCHEMA_PATH = Path(__file__).resolve().parents[1] / "references" / "knowledge-update-schema.json"


def load_yaml(path: Path) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"无法读取 YAML {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"YAML 顶层必须是对象：{path}")
    return value


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    if slug:
        return slug
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:10]
    return f"product-{digest}"


PATH_TOKEN = re.compile(r"([^.\[\]]+)|\[(\d+)\]")


def path_tokens(path: str) -> list[str | int]:
    tokens: list[str | int] = []
    for key, index in PATH_TOKEN.findall(path):
        tokens.append(int(index) if index else key)
    if not tokens or "".join(str(item) for item in tokens) == "":
        raise ValueError(f"无效更新路径：{path}")
    return tokens


def apply_change(target: dict[str, Any], change: dict[str, Any]) -> None:
    tokens = path_tokens(str(change.get("path") or ""))
    cursor: Any = target
    for token in tokens[:-1]:
        if isinstance(token, int):
            if not isinstance(cursor, list) or token >= len(cursor):
                raise ValueError(f"更新路径不存在：{change.get('path')}")
            cursor = cursor[token]
        else:
            if not isinstance(cursor, dict):
                raise ValueError(f"更新路径不是对象：{change.get('path')}")
            if token not in cursor:
                cursor[token] = {}
            cursor = cursor[token]
    leaf = tokens[-1]
    operation = change.get("operation", "set")
    if operation not in {"set", "delete"}:
        raise ValueError(f"不支持的 operation：{operation}")
    if isinstance(leaf, int):
        if not isinstance(cursor, list) or leaf >= len(cursor):
            raise ValueError(f"更新路径不存在：{change.get('path')}")
        if operation == "delete":
            cursor.pop(leaf)
        else:
            cursor[leaf] = copy.deepcopy(change.get("value"))
    else:
        if not isinstance(cursor, dict):
            raise ValueError(f"更新路径不是对象：{change.get('path')}")
        if operation == "delete":
            if leaf not in cursor:
                raise ValueError(f"无法删除不存在的路径：{change.get('path')}")
            del cursor[leaf]
        else:
            cursor[leaf] = copy.deepcopy(change.get("value"))


def dump_yaml(value: dict[str, Any]) -> bytes:
    return yaml.safe_dump(value, allow_unicode=True, sort_keys=False, width=200).encode("utf-8")


def atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_name(f".{path.name}.tmp")
    staged.write_bytes(content)
    staged.replace(path)


def validate_update(update: dict[str, Any]) -> list[str]:
    schema = json.loads(UPDATE_SCHEMA_PATH.read_text(encoding="utf-8"))
    failures = [
        f"{'.'.join(str(part) for part in error.absolute_path) or '<root>'}: {error.message}"
        for error in sorted(Draft202012Validator(schema).iter_errors(update), key=lambda item: list(item.absolute_path))
    ]
    changes = update.get("changes") or []
    for index, item in enumerate(changes):
        prefix = f"changes[{index}]"
        if item.get("level") == "P0" and item.get("status") == "confirmed":
            confirmation = item.get("confirmation") or {}
            if not confirmation.get("confirmed_at") or not confirmation.get("user_quote"):
                failures.append(f"{prefix} 的 P0 确认必须包含 confirmed_at 和 user_quote")
    return failures


def registry_entry(workspace: Path, profile_path: Path, update: dict[str, Any], timestamp: str) -> tuple[Path, dict[str, Any]]:
    managed = workspace / ".report-automation"
    registry_path = managed / "product-registry.yaml"
    registry = load_yaml(registry_path) if registry_path.exists() else {"registry_version": 1, "products": {}}
    product_name = str(update["product"])
    slug = slugify(product_name)
    try:
        profile_ref = profile_path.relative_to(managed).as_posix()
    except ValueError as exc:
        raise ValueError("profile 必须位于工作区的 .report-automation 目录内") from exc
    existing = registry.setdefault("products", {}).get(slug) or {}
    existing_aliases = [str(item) for item in (existing.get("aliases") or []) if str(item).strip()]
    aliases = [str(item) for item in (update.get("aliases") or []) if str(item).strip()]
    registry.setdefault("products", {})[slug] = {
        "name": product_name,
        "aliases": list(dict.fromkeys([product_name, *existing_aliases, *aliases])),
        "profile": profile_ref,
        "updated_at": timestamp,
    }
    return registry_path, registry


def main() -> int:
    parser = argparse.ArgumentParser(description="把已确认的产品知识安全写回 profile")
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--profile", required=True, type=Path)
    parser.add_argument("--update", required=True, type=Path)
    parser.add_argument("--apply", action="store_true", help="确认校验后实际写入；缺省只预览")
    args = parser.parse_args()

    try:
        update = load_yaml(args.update)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2
    update_failures = validate_update(update)
    if update_failures:
        for failure in update_failures:
            print(f"知识更新格式错误：{failure}", file=sys.stderr)
        return 2
    pending_p0 = [
        item for item in (update.get("changes") or [])
        if item.get("level") == "P0" and item.get("status") != "confirmed"
    ]
    if pending_p0:
        print("存在未确认 P0，拒绝修改产品知识：", file=sys.stderr)
        for item in pending_p0:
            print(f"  {item.get('path')}: {item.get('evidence') or '等待用户确认'}", file=sys.stderr)
        return 1

    workspace = args.workspace.resolve()
    profile_path = args.profile.resolve()
    if not profile_path.exists():
        print(f"找不到 profile：{profile_path}", file=sys.stderr)
        return 2
    try:
        profile_path.relative_to(workspace)
    except ValueError:
        print("profile 必须位于指定 workspace 内", file=sys.stderr)
        return 2
    try:
        profile = load_yaml(profile_path)
        if str((profile.get("product") or {}).get("name") or "").casefold() != str(update["product"]).casefold():
            raise ValueError("update.product 与 profile.product.name 不一致")
        candidate = copy.deepcopy(profile)
        for change in update["changes"]:
            apply_change(candidate, change)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2

    failures = [
        issue for issue in (vp.schema_issues(candidate) + vp.semantic_issues(candidate))
        if issue.level == "FAIL"
    ]
    if failures:
        print("知识更新会产生无效 profile，已拒绝：", file=sys.stderr)
        for issue in failures:
            print(f"  {issue}", file=sys.stderr)
        return 1

    print("拟更新路径：")
    for change in update["changes"]:
        print(f"  {change.get('operation', 'set')} {change['path']}")
    if not args.apply:
        print("预览完成；未修改任何文件。确认后添加 --apply。")
        return 0

    timestamp = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    token = datetime.now(timezone.utc).astimezone().strftime("%Y%m%dT%H%M%S%f")
    product_dir = profile_path.parent
    backup_path = product_dir / "backups" / f"profile.{token}.yaml"
    history_name = f"{token}-{slugify(str(update['id']))}.yaml"
    history_path = product_dir / "knowledge-history" / history_name
    before = profile_path.read_bytes()
    after = dump_yaml(candidate)
    history = {
        "history_version": 1,
        "update_id": update["id"],
        "product": update["product"],
        "applied_at": timestamp,
        "source": update["source"],
        "changes": update["changes"],
        "before_sha256": hashlib.sha256(before).hexdigest(),
        "after_sha256": hashlib.sha256(after).hexdigest(),
        "profile": profile_path.name,
    }
    try:
        registry_path, registry = registry_entry(workspace, profile_path, update, timestamp)
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(profile_path, backup_path)
        atomic_write(history_path, dump_yaml(history))
        atomic_write(registry_path, dump_yaml(registry))
        atomic_write(profile_path, after)
    except (OSError, ValueError) as exc:
        print(f"写入产品知识失败：{exc}", file=sys.stderr)
        return 2
    print(f"产品知识已写回：{profile_path}")
    print(f"备份：{backup_path}")
    print(f"审计记录：{history_path}")
    print(f"产品目录：{registry_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
