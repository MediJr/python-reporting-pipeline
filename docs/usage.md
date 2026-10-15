# Usage

## Commands

| Command | What it does |
| --- | --- |
| `reporting-pipeline check --config FILE` | Validates the configuration and reads the sources with every row check; writes nothing |
| `reporting-pipeline run --config FILE` | Runs the whole pipeline (see [architecture](architecture.md#run-lifecycle)) |
| `reporting-pipeline generate-demo-data --output DIR [--seed N] [--overwrite]` | Writes the deterministic synthetic data set; refuses to replace files without `--overwrite` |
| `reporting-pipeline --version` / `--help` | Version, help with examples and exit codes |

`python -m reporting_pipeline` is equivalent. `-v` / `-vv` (before the command) show progress on the
console; the full event log is always written to the run folder.

Exit codes: 0 success, 1 run failed (nothing committed or published), 2 invalid command line,
3 refused before running (invalid configuration, unusable sources, lock held).

## Configuration reference (TOML)

| Key | Default | Meaning |
| --- | --- | --- |
| `[project] name` | required | Name shown in reports |
| `[project] root` | `.` | Project folder, relative to the configuration file; every other path is relative to it and must stay inside it |
| `[sources] connector` | `csv` | Only `csv` in this version |
| `[sources] directory` | required | Folder of the source files |
| `[sources] encoding` | `utf-8` | Any Python codec; a UTF-8 BOM is accepted |
| `[sources] delimiter` | `,` | One character |
| `[sources.files]` | required | `orders`, `order_lines`, `products`: file names inside `directory` (`.csv`) |
| `[warehouse] engine` | required | `sqlite` or `postgres` |
| `[warehouse] sqlite_path` | — | SQLite file (created if missing) |
| `[warehouse] connection_env` | — | PostgreSQL: **name** of the environment variable holding the connection string |
| `[warehouse] schema` | `reporting` | PostgreSQL schema used (created if missing) |
| `[outputs] powerbi_dir` | required | Folder of the Power BI files, replaced as a whole by each successful run |
| `[outputs] runs_dir` | required | One sub-folder per run, never overwritten |
| `[quality] max_rejected_rate` | 0 for each source | Largest share of rejected rows per source, from 0 to 1 |

Unknown sections or keys, absolute paths, paths leaving the project, output folders inside the sources
and a connection string in place of a variable name are all refused with a precise message.

## PostgreSQL mode

The connection string is never stored in a file. It is read from the environment variable named by
`connection_env` (libpq format, e.g. `host=... port=... dbname=... user=...`; a password, if your
server needs one, can also come from the standard `PGPASSWORD` variable or a `.pgpass` file).

For a local demo without touching any existing server, `scripts/pg_demo.py` creates a **separate,
disposable** PostgreSQL cluster in `.pgdemo/` (ignored by git), listening on 127.0.0.1:55432:

```powershell
.venv\Scripts\python.exe scripts\pg_demo.py init
$env:REPORTING_PG_DSN = (.venv\Scripts\python.exe scripts\pg_demo.py dsn)
.venv\Scripts\reporting-pipeline.exe run --config config\pipeline.postgres.example.toml
.venv\Scripts\python.exe scripts\pg_demo.py destroy
```

`init` refuses if `.pgdemo/` exists or the port is taken; `destroy` only deletes a folder that carries
the demo marker. The demo server uses `trust` authentication on 127.0.0.1 only — a demo setting, not
for real servers. Against any server, the pipeline only works in a schema it created (marker table
`rp_metadata`); it refuses to modify a schema that already contains other tables.

## Scheduling

A run is one command with an exit code, so any scheduler can start it (for example the Windows Task
Scheduler with `reporting-pipeline run --config ...`); exit code 1 or 3 means "look at the run report".
A lock file prevents two runs of the same project at once. There is no built-in scheduler.

## Troubleshooting

| Message | What to do |
| --- | --- |
| `Result: REFUSED (invalid configuration ...)` | Fix the key named in the message (`unknown key(s) in [outputs]: colour`, ...) |
| `source file not found: products.csv` | Check `[sources] directory` and `[sources.files]` |
| `... is not valid utf-8 (byte N); set sources.encoding` | Set the encoding of the files (e.g. `cp1252`) |
| `quality gate failed: rejected_rate_order_lines 2.10% (limit <= 1.00% ...)` | Look at `rejected_rows.csv` in the run folder; fix the source or, if acceptable, raise the threshold |
| `another run holds the lock runs/.lock` | Wait for the other run; if none is running (crash), delete the file |
| `... was not created by this pipeline (no rp_metadata marker); refusing to modify it` | Point `sqlite_path` or `schema` to a new target |
| `environment variable REPORTING_PG_DSN is not set` | Set it in the shell that runs the pipeline |
| `cannot connect to PostgreSQL ...` | Check that the server runs and the connection string is right (the password is never printed) |
