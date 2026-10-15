"""End-to-end demo in one command (SQLite, no server needed).

    python scripts/demo.py

1. checks the configuration and the sources (read-only);
2. runs the pipeline (staging, star schema, marts, KPIs, quality checks, Power BI exports);
3. recomputes the results independently from the raw CSV files (scripts/verify_results.py);
4. prints where the reports and the Power BI files are.

Use ``--postgres`` to run step 2 against PostgreSQL instead (REPORTING_PG_DSN must be set; see
scripts/pg_demo.py for a disposable local server).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def step(title: str, *args: str) -> None:
    print(f"\n=== {title}\n$ {' '.join(args)}", flush=True)
    completed = subprocess.run([sys.executable, *args], cwd=ROOT)
    if completed.returncode != 0:
        raise SystemExit(f"step failed with exit code {completed.returncode}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--postgres", action="store_true", help="use config/pipeline.postgres.example.toml")
    args = parser.parse_args()
    config = "config/pipeline.postgres.example.toml" if args.postgres else "config/pipeline.toml"
    exports = "output/powerbi-postgres" if args.postgres else "output/powerbi"
    step("1. Check the configuration and the sources", "-m", "reporting_pipeline", "check", "--config", config)
    step("2. Run the pipeline", "-m", "reporting_pipeline", "run", "--config", config)
    step("3. Independent recomputation of the results", "scripts/verify_results.py", "data/sample", exports)
    print(f"\nPower BI files: {exports}/  (open powerbi/project/SalesPerformance.pbip, see powerbi/README.md)")
    print("Run reports:    runs/<timestamp>_<run id>/quality_report.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
