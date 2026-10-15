"""Readable run and data-quality report (Markdown), rendered from the JSON run report."""

from __future__ import annotations

from typing import Any

STATUS_TEXT = {
    "success": "SUCCESS - every row was accepted, every check passed, the exports were published",
    "success_with_rejections": "SUCCESS WITH REJECTIONS - some source rows were rejected (within the thresholds); "
    "every check passed and the exports were published",
    "failed": "FAILED - nothing was committed or exported; the previous model and exports are unchanged",
}


def _table(header: list[str], rows: list[list[Any]]) -> list[str]:
    def cell(value: Any) -> str:
        return "" if value is None else str(value).replace("|", "\\|").replace("\n", " ")

    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join("---" for _ in header) + " |"]
    lines += ["| " + " | ".join(cell(v) for v in row) + " |" for row in rows]
    return lines


def render_quality_report(report: dict[str, Any]) -> str:
    lines = [f"# Pipeline run report: {report['project']}", ""]
    lines += _table(
        ["Item", "Value"],
        [
            ["Status", STATUS_TEXT.get(report["status"], report["status"])],
            ["Exit code", report["exit_code"]],
            ["Run id", report["run_id"]],
            ["Started (UTC)", report["started_at"]],
            ["Finished (UTC)", report["finished_at"]],
            ["Duration (s)", report["duration_s"]],
            ["Warehouse engine", report["engine"]],
        ],
    )
    if report["errors"]:
        lines += ["", "## Errors", ""] + [f"- {e}" for e in report["errors"]]

    lines += ["", "## Sources", ""]
    rejected = report["rejections"]["by_table"]
    lines += _table(
        [
            "Source",
            "File",
            "Rows read",
            "Accepted by CSV checks",
            "Rejected (all reasons)",
            "Values normalized",
            "SHA-256",
        ],
        [
            [
                t,
                s["file"],
                s["rows_read"],
                s["accepted_by_csv_checks"],
                rejected.get(t, 0),
                s["normalized_values"],
                s["sha256"][:16],
            ]
            for t, s in report["sources"].items()
        ],
    )
    lines += ["", "## Rejected rows by reason", ""]
    if report["rejections"]["by_reason"]:
        lines += _table(
            ["Source", "Reason", "Rows"],
            [[r["table"], r["reason"], r["rows"]] for r in report["rejections"]["by_reason"]],
        )
        lines += ["", "Every rejected row is listed with its line number and values in `rejected_rows.csv`."]
    else:
        lines += ["None."]

    lines += ["", "## Quality checks", ""]
    if report["checks"]:
        lines += _table(
            ["Check", "Type", "Result", "Expected", "Actual", "Detail"],
            [
                [c["name"], c["category"], "pass" if c["passed"] else "FAIL", c["expected"], c["actual"], c["detail"]]
                for c in report["checks"]
            ],
        )
    else:
        lines += ["Not run (the run stopped before the checks)."]

    if report["model_rows"]:
        lines += ["", "## Model", ""]
        lines += _table(["Table", "Rows"], [[t, n] for t, n in report["model_rows"].items()])
    if report["kpis"]:
        lines += ["", "## KPIs", ""]
        lines += _table(
            ["KPI", "Value", "Unit", "Period"],
            [
                [k["label"], k["value"] if k["value"] is not None else "n/a", k["unit"], k["period"]]
                for k in report["kpis"]
            ],
        )
    if report["exports"]:
        lines += ["", "## Power BI exports", ""]
        lines += _table(
            ["File", "Rows", "SHA-256"], [[e["file"], e["rows"], e["sha256"][:16]] for e in report["exports"]]
        )
    lines += ["", "## Steps", ""]
    lines += _table(
        ["Step", "Status", "Duration (s)"], [[s["step"], s["status"], s["duration_s"]] for s in report["steps"]]
    )
    if report["warnings"]:
        lines += ["", "## Warnings", ""] + [f"- {w}" for w in report["warnings"]]
    lines += ["", "Full details: `run_report.json`; event log: `pipeline.log.jsonl`.", ""]
    return "\n".join(lines)
