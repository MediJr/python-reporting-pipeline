# 0002 — One portable SQL code base; money as integer cents

**Status:** accepted

## Context

The transformations must be real SQL, executed on SQLite and on PostgreSQL. The two engines differ on
types (no `DATE` or `NUMERIC` in SQLite), on numeric results (PostgreSQL returns `NUMERIC` for sums of
`BIGINT`) and on driver details (psycopg treats `%` as a parameter marker).

## Decision

- One set of SQL files, run unchanged on both engines. The only dialect token is `{{DATE}}` (`DATE` on
  PostgreSQL, ISO text on SQLite).
- Amounts are stored as **integer cents** and rates as **integer basis points**; rounding is written
  with integer arithmetic (`(x + 5000) / 10000` for half-up on positive values). Sums are cast to
  `BIGINT`.
- Calendar generation and KPI ratios, which would need dialect-specific functions, are done in Python
  with `Decimal`.
- The SQL files live in the package (`src/reporting_pipeline/sql/`) so that a regular installation can
  find them.
- Values from outside are always query parameters; table names come from a fixed list and are checked
  against a pattern.

## Consequences

- SQLite and PostgreSQL produce byte-identical exports (tested).
- Exports convert cents and basis points back to decimal amounts and ratios.
- The SQL avoids engine-specific features (no `FILTER (WHERE …)`, no `generate_series`).
