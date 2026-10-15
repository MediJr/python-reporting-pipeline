# Example outputs

Real outputs of one run of the pipeline on the committed demo data (`config/pipeline.toml`, SQLite).
Nothing here is edited by hand; regenerate with:

```powershell
.venv\Scripts\python.exe scripts\regenerate_examples.py --overwrite
```

`tests/test_project.py` fails if these files no longer match a fresh run (run ids, times and durations
excepted).

| Folder | Files |
| --- | --- |
| [run/](run/quality_report.md) | `quality_report.md` (readable run and quality report), `run_report.json`, `rejected_rows.csv` (the 23 rejected rows with their original values), `pipeline.log.jsonl` (structured log) |
| [powerbi/](powerbi/kpi_summary.csv) | `kpi_summary.csv`, the four marts, `quality_checks.csv`, `rejected_rows.csv`, `manifest.json` |

`fact_sales.csv`, `dim_date.csv` and `dim_product.csv` are not committed here: the pipeline recreates them
in about a second in `output/powerbi/`.
