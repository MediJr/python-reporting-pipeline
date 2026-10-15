"""Regenerate ``reports/examples/`` by really running the pipeline on the demo data.

The pipeline runs in a temporary copy of the project (so the repository's own output/ and runs/ are
untouched). The run reports and the small Power BI exports are then copied to reports/examples/;
fact_sales.csv, dim_date.csv and dim_product.csv are left out (the pipeline recreates them in seconds).
Nothing in reports/examples/ is edited by hand; tests/test_examples.py checks it against a fresh run.

    python scripts/regenerate_examples.py --overwrite
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "reports" / "examples"
EXPORTS = [
    "kpi_summary.csv",
    "mart_monthly_sales.csv",
    "mart_category_monthly.csv",
    "mart_product_performance.csv",
    "mart_segment_region.csv",
    "quality_checks.csv",
    "rejected_rows.csv",
    "manifest.json",
]
RUN_FILES = ["run_report.json", "quality_report.md", "rejected_rows.csv", "pipeline.log.jsonl"]


def generate(destination: Path) -> int:
    from reporting_pipeline.config import load_config
    from reporting_pipeline.pipeline import run_pipeline

    with tempfile.TemporaryDirectory() as temp:
        work = Path(temp)
        shutil.copytree(ROOT / "data" / "sample", work / "data" / "sample")
        shutil.copytree(ROOT / "config", work / "config")
        result = run_pipeline(load_config(work / "config" / "pipeline.toml"))
        if result.exit_code != 0:
            print(f"pipeline failed: {result.report['errors']}", file=sys.stderr)
            return result.exit_code
        (destination / "run").mkdir(parents=True)
        (destination / "powerbi").mkdir()
        for name in RUN_FILES:
            shutil.copy2(result.run_dir / name, destination / "run" / name)
        for name in EXPORTS:
            shutil.copy2(work / "output" / "powerbi" / name, destination / "powerbi" / name)
    print(f"run {result.run_id}: {result.status}; examples written to {destination}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--overwrite", action="store_true", help="replace the existing examples")
    args = parser.parse_args()
    for sub in ("run", "powerbi"):
        if (EXAMPLES / sub).exists():
            if not args.overwrite:
                print("reports/examples already exists; use --overwrite", file=sys.stderr)
                return 1
            shutil.rmtree(EXAMPLES / sub)
    return generate(EXAMPLES)


if __name__ == "__main__":
    sys.exit(main())
