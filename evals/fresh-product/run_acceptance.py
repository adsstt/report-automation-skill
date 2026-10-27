from __future__ import annotations

import csv
import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
PACKAGE = ROOT.parents[1]
SCRIPTS = PACKAGE / "scripts"
RUNTIME = ROOT / "runtime"


def run(name: str, args: list[object], expected: int = 0) -> str:
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    result = subprocess.run(
        [sys.executable, *(str(arg) for arg in args)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    output = (result.stdout + result.stderr).strip()
    print(f"[{name}] exit={result.returncode}")
    if output:
        console_encoding = sys.stdout.encoding or "utf-8"
        print(output.encode(console_encoding, errors="replace").decode(console_encoding))
    if result.returncode != expected:
        raise RuntimeError(f"{name}: expected exit {expected}, got {result.returncode}")
    return output


def main() -> int:
    if RUNTIME.exists():
        if RUNTIME.resolve().parent != ROOT.resolve():
            raise RuntimeError("refusing to clean runtime outside scenario root")
        shutil.rmtree(RUNTIME)
    RUNTIME.mkdir(parents=True)

    run("fixtures", [ROOT / "make_fixtures.py"])
    probe = RUNTIME / "probe.json"
    draft = RUNTIME / "profile.draft.yaml"
    out_dir = RUNTIME / "output"
    workbook = RUNTIME / "NovaFit_daily_20261025.xlsx"

    run("probe", [
        SCRIPTS / "probe_sources.py",
        "--source-dir", ROOT / "sources" / "2026-10-24",
        "--source-dir", ROOT / "sources" / "2026-10-25",
        "--glob", "*",
        "--limit", "10",
        "--target", ROOT / "samples" / "target_template.xlsx",
        "--out", probe,
    ])
    run("infer", [SCRIPTS / "infer_mappings.py", "--probe", probe, "--out", draft, "--product", "NovaFit"])
    run("draft gate", [SCRIPTS / "validate_profile.py", draft], expected=1)
    run("confirmed profile", [SCRIPTS / "validate_profile.py", ROOT / "profile.confirmed.yaml"])
    run("format", [
        SCRIPTS / "verify_format.py", "--profile", ROOT / "profile.confirmed.yaml",
        "--target", ROOT / "samples" / "target_template.xlsx",
    ])
    run("generate", [
        SCRIPTS / "generate_report.py", "--profile", ROOT / "profile.confirmed.yaml",
        "--report-type", "daily", "--date", "2026-10-25", "--out-dir", out_dir,
    ])
    run("numeric verification", [
        SCRIPTS / "verify_output.py", "--profile", ROOT / "profile.confirmed.yaml",
        "--out-dir", out_dir, "--date", "2026-10-25",
    ])
    run("shadow diff", [
        SCRIPTS / "shadow_diff.py",
        "--shadow", out_dir / "结果表_20261025.csv",
        "--expected", ROOT / "expected" / "结果表_20261025.csv",
        "--keys", "账户ID,国家代码,事件",
        "--out", RUNTIME / "shadow_diff.md",
    ])
    run("render", [
        SCRIPTS / "render_workbook.py", "--profile", ROOT / "profile.confirmed.yaml",
        "--report-type", "daily", "--date", "2026-10-25",
        "--source-file", f"meta_ads={ROOT / 'sources' / '2026-10-25' / 'meta_ads_20261025.xlsx'}",
        "--source-file", f"adjust_events={ROOT / 'sources' / '2026-10-25' / 'adjust_attribution_20261025.csv'}",
        "--compare-source-file", f"meta_ads={ROOT / 'sources' / '2026-10-24' / 'meta_ads_20261024.xlsx'}",
        "--compare-source-file", f"adjust_events={ROOT / 'sources' / '2026-10-24' / 'adjust_attribution_20261024.csv'}",
        "--out", workbook,
    ])
    run("workbook verification", [
        SCRIPTS / "verify_workbook.py",
        "--template", ROOT / "samples" / "target_template.xlsx",
        "--output", workbook,
        "--report-date", "2026-10-25",
        "--sheet", "Daily Comparison",
        "--expect-sheet", "Daily History",
        "--expect-new-rows", "2",
    ])

    with (out_dir / "过程表_20261025.csv").open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    totals = {
        "spend": sum(float(row["花费"]) for row in rows),
        "registration": sum(float(row["注册"]) for row in rows),
        "kyc": sum(float(row["KYC"]) for row in rows),
        "ftd": sum(float(row["FTD_Actual"]) for row in rows),
        "revenue": sum(float(row["收入"]) for row in rows),
    }
    expected = {"spend": 300, "registration": 30, "kyc": 18, "ftd": 8, "revenue": 800}
    if totals != expected:
        raise RuntimeError(f"unexpected core totals: {totals}")
    (RUNTIME / "acceptance_result.md").write_text(
        "# NovaFit fresh-product acceptance\n\n"
        "- Result: PASS\n"
        "- Flow: Probe → Infer (blocked draft) → confirmed profile → generate → verify → render → workbook verify\n"
        f"- Core totals: {totals}\n"
        f"- Workbook: `{workbook}`\n",
        encoding="utf-8",
    )
    print(f"ACCEPTANCE PASS: {totals}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
