from __future__ import annotations

import shutil
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def run_cli(script: str, *args: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPTS / script), *(str(arg) for arg in args)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


class ProductKnowledgeTests(unittest.TestCase):
    def test_register_profile_copies_it_into_managed_product_library(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT / "runtime") as tmp:
            workspace = Path(tmp)
            source_profile = workspace / "runtime" / "profile.confirmed.yaml"
            source_profile.parent.mkdir(parents=True)
            shutil.copyfile(ROOT / "examples" / "minimal" / "profile.yaml", source_profile)

            registered = run_cli(
                "register_product_profile.py",
                "--workspace", workspace,
                "--profile", source_profile,
                "--alias", "演示产品",
            )

            self.assertEqual(registered.returncode, 0, registered.stdout + registered.stderr)
            managed_profile = workspace / ".report-automation" / "products" / "demoapp" / "profile.yaml"
            self.assertTrue(managed_profile.exists())
            self.assertEqual(
                yaml.safe_load(managed_profile.read_text(encoding="utf-8"))["product"]["name"],
                "DemoApp",
            )
            self.assertTrue(source_profile.exists())
            update_path = workspace / "knowledge-update.yaml"
            update_path.write_text(yaml.safe_dump({
                "knowledge_update_version": 1,
                "id": "demoapp-owner-20261028",
                "product": "DemoApp",
                "source": {"type": "natural_language", "ref": "chat:turn-7"},
                "changes": [{
                    "path": "knowledge.facts.owner",
                    "value": "growth-team",
                    "level": "P1",
                    "status": "confirmed",
                    "evidence": "用户说明产品负责人",
                }],
            }, allow_unicode=True, sort_keys=False), encoding="utf-8")
            updated = run_cli(
                "update_product_knowledge.py",
                "--workspace", workspace,
                "--profile", managed_profile,
                "--update", update_path,
                "--apply",
            )
            self.assertEqual(updated.returncode, 0, updated.stdout + updated.stderr)
            resolved = run_cli(
                "resolve_product_profile.py", "--workspace", workspace,
                "--product", "演示产品", "--json",
            )
            self.assertEqual(resolved.returncode, 0, resolved.stdout + resolved.stderr)
            self.assertEqual(Path(json.loads(resolved.stdout)["profile"]), managed_profile.resolve())

    def test_unconfirmed_p0_update_cannot_modify_profile(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT / "runtime") as tmp:
            workspace = Path(tmp)
            profile_path = workspace / ".report-automation" / "products" / "demoapp" / "profile.yaml"
            profile_path.parent.mkdir(parents=True)
            shutil.copyfile(ROOT / "examples" / "minimal" / "profile.yaml", profile_path)
            original = profile_path.read_bytes()
            update_path = workspace / "knowledge-update.yaml"
            update_path.write_text(yaml.safe_dump({
                "knowledge_update_version": 1,
                "id": "demoapp-spend-field-20261028",
                "product": "DemoApp",
                "source": {
                    "type": "natural_language",
                    "ref": "chat:用户说明花费字段实际使用安装列（仅用于门禁测试）",
                },
                "changes": [{
                    "path": "metrics.spend.from",
                    "value": "tt_raw.install",
                    "level": "P0",
                    "status": "pending",
                    "evidence": "用户尚未确认",
                }],
            }, allow_unicode=True, sort_keys=False), encoding="utf-8")

            result = run_cli(
                "update_product_knowledge.py",
                "--workspace", workspace,
                "--profile", profile_path,
                "--update", update_path,
                "--apply",
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("P0", result.stderr)
            self.assertEqual(profile_path.read_bytes(), original)
            self.assertFalse((workspace / ".report-automation" / "product-registry.yaml").exists())

    def test_confirmed_update_writes_profile_backup_history_and_registry(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT / "runtime") as tmp:
            workspace = Path(tmp)
            product_dir = workspace / ".report-automation" / "products" / "demoapp"
            profile_path = product_dir / "profile.yaml"
            profile_path.parent.mkdir(parents=True)
            shutil.copyfile(ROOT / "examples" / "minimal" / "profile.yaml", profile_path)
            original = profile_path.read_bytes()
            update_path = workspace / "knowledge-update.yaml"
            update_path.write_text(yaml.safe_dump({
                "knowledge_update_version": 1,
                "id": "demoapp-spend-field-20261028",
                "product": "DemoApp",
                "aliases": ["Demo App", "演示产品"],
                "source": {
                    "type": "natural_language",
                    "ref": "chat:turn-42",
                    "summary": "用户说明花费改用安装字段（仅用于持久化测试）",
                },
                "changes": [{
                    "path": "metrics.spend.from",
                    "value": "tt_raw.install",
                    "level": "P0",
                    "status": "confirmed",
                    "evidence": "用户明确指定字段关系",
                    "confirmation": {
                        "confirmed_at": "2026-10-28T10:30:00+08:00",
                        "user_quote": "花费字段就用安装这一列",
                    },
                }, {
                    "path": "knowledge.facts.attribution_window",
                    "value": "7-day click / 1-day view",
                    "level": "P1",
                    "status": "confirmed",
                    "evidence": "用户上传的归因平台设置截图",
                }],
            }, allow_unicode=True, sort_keys=False), encoding="utf-8")

            result = run_cli(
                "update_product_knowledge.py",
                "--workspace", workspace,
                "--profile", profile_path,
                "--update", update_path,
                "--apply",
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            updated = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
            self.assertEqual(updated["metrics"]["spend"]["from"], "tt_raw.install")
            self.assertEqual(updated["knowledge"]["facts"]["attribution_window"], "7-day click / 1-day view")
            backups = list((product_dir / "backups").glob("profile.*.yaml"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_bytes(), original)
            histories = list((product_dir / "knowledge-history").glob("*.yaml"))
            self.assertEqual(len(histories), 1)
            history = yaml.safe_load(histories[0].read_text(encoding="utf-8"))
            self.assertEqual(history["update_id"], "demoapp-spend-field-20261028")
            self.assertEqual(history["changes"][0]["path"], "metrics.spend.from")
            self.assertNotEqual(history["before_sha256"], history["after_sha256"])
            registry = yaml.safe_load(
                (workspace / ".report-automation" / "product-registry.yaml").read_text(encoding="utf-8")
            )
            self.assertEqual(registry["products"]["demoapp"]["profile"], "products/demoapp/profile.yaml")
            self.assertIn("演示产品", registry["products"]["demoapp"]["aliases"])

            resolved = run_cli(
                "resolve_product_profile.py",
                "--workspace", workspace,
                "--product", "演示产品",
                "--json",
            )
            self.assertEqual(resolved.returncode, 0, resolved.stdout + resolved.stderr)
            resolution = json.loads(resolved.stdout)
            self.assertEqual(Path(resolution["profile"]), profile_path.resolve())
            self.assertEqual(resolution["product"], "DemoApp")
            self.assertEqual(resolution["matched_by"], "alias")


if __name__ == "__main__":
    unittest.main()
