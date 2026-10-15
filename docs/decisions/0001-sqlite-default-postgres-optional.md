# 0001 — SQLite by default, PostgreSQL optional, no pandas

**Status:** accepted

## Context

The demo must run from a clean clone without installing or administering a server, yet the project
should show work against a real client-server database. PostgreSQL 18 was available on the development
machine (as a running service whose credentials belong to its owner and must not be used).

## Decision

- **SQLite** (Python standard library) is the default warehouse: `pip install -e .` and one command run
  the whole demo, with no third-party dependency.
- **PostgreSQL** is a second warehouse connector (psycopg 3, optional extra `[postgres]`), selected in
  the configuration. The connection string comes from an environment variable named in the
  configuration.
- For the PostgreSQL demo, `scripts/pg_demo.py` creates a **separate, disposable cluster** (`initdb`) on
  a dedicated local port instead of using the existing service.
- **No pandas**: volumes are small, the CSV module plus SQL cover the needs, and exact `Decimal`
  arithmetic matters more than vectorized operations. One less heavy dependency (pandas pulls numpy).

## Consequences

- The same tests run on both engines; the PostgreSQL tests start their own server and are skipped only
  where PostgreSQL binaries are absent.
- Large-volume optimizations (bulk `COPY`, chunked reading) are not implemented.

## Alternatives considered

- **DuckDB**: excellent for analytics, but it would not show a client-server database and adds a binary
  dependency.
- **Docker for PostgreSQL**: Docker was installed but its daemon was not running; a local `initdb`
  cluster is lighter and needs no extra software beyond PostgreSQL itself.
