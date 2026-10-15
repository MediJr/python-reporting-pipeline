# Testing

```powershell
.venv\Scripts\python.exe -m pytest -q                         # whole suite
.venv\Scripts\python.exe -m ruff check src tests scripts
.venv\Scripts\python.exe -m ruff format --check src tests scripts
.venv\Scripts\python.exe scripts\verify_results.py data/sample output/powerbi   # after a run
```

Tests use temporary folders and copies of the demo data; they never modify the repository. Expected
values are written by hand in the tests (for example `245.00 × 0.85 = 208.25`), not produced by the code
under test.

| File | What it covers |
| --- | --- |
| `test_config.py` | Valid configurations; unknown keys and sections; absolute paths and paths leaving the project; output folders inside the sources; a connection string in place of a variable name (and no leak of it); invalid TOML |
| `test_sources.py` | Every parser (amounts, rates, dates, allowed values) and reason code; normalization; malformed and blank lines; exact duplicates vs conflicting keys; missing, empty, badly encoded or broken files; BOM; extra columns |
| `test_transform.py` | Calendar (leap year, weekends); the net-amount rule including half-up rounding cases; ratios that must stay empty rather than zero; KPI derivation |
| `test_pipeline.py` | The demo run (every planted anomaly found, exact counts, 19 checks); **independent recomputation** of the results; export format; reruns (identical exports, no duplicates, run history); failure paths: quality gate, SQL error in the middle of the build, export verification failure, foreign database, missing file, held lock, unexpected exception — each with rollback or no change |
| `test_warehouse.py` | SQL loading for both dialects; statement splitting; password redaction; SQL injection attempt stored as plain data; ownership marker; **PostgreSQL on a real temporary server**: byte-identical exports vs SQLite, rebuild on rerun, refusal of a foreign schema, rollback, connection errors without secrets |
| `test_cli.py` | Help, version, usage errors; `check` writes nothing; exit codes 0/1/3; deterministic demo data equal to the committed files; the installed command run from another directory |
| `test_powerbi.py` | The committed PBIP equals the generator output; every measure, table, relationship and DataFolder query; model columns equal the exported headers; report fields exist in the model; no personal path; the DAX comparison script detects differences |
| `test_project.py` | Committed examples equal a fresh run; package metadata and license; pinned versions; documentation links and anchors; no secret or local path in the repository files |

## PostgreSQL tests

The `postgres_dsn` fixture creates a throwaway cluster with `initdb` in a temporary folder, starts it on
a random local port and stops it at the end. If the PostgreSQL binaries are not found, these tests are
**skipped** with a message; set `RP_TEST_PG_DSN` to use an existing test database instead. In the
verified environment (PostgreSQL 18.6) they all ran.

## Power BI validation (manual, Windows)

Power BI Desktop cannot run inside pytest. The procedure used for this repository:

1. generate a local copy of the project pointing at the exports
   (`python scripts/build_powerbi_project.py --output output/powerbi-project --data-folder <abs path>`);
2. check the report files against the JSON schemas shipped with Power BI Desktop (38 files, 0 errors);
3. open the project in Power BI Desktop 2.157, refresh it, run `powerbi/validation/query_model.ps1`;
4. compare with `scripts/compare_powerbi_results.py`: **174 values, 0 differences** (three separate
   sessions, one per report page). The DAX results of one session are committed in
   [powerbi/validation/dax_results.json](../powerbi/validation/dax_results.json) and reused by
   `test_powerbi.py`.

## Mutation checks

To make sure the tests really guard the safeguards, each of these was disabled in turn and the suite run;
every time at least one test failed, and the code was restored: rollback on failure, half-up rounding
of line amounts in SQL, rejection of conflicting duplicates, ownership marker of the warehouse,
referential-integrity rejection of unknown products, quality gate, "completed orders only" filter of a
mart, cleanup of the temporary export folder (8 of 8 caught).
