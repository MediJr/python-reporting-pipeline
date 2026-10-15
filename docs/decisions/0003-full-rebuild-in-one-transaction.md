# 0003 — Full rebuild of the model in one transaction

**Status:** accepted

## Context

Re-running the pipeline must not duplicate data, and a failure must never leave a half-built model or
publish partial figures.

## Decision

- Each run drops and recreates the staging, star-schema and mart tables, loads everything and runs all
  checks **inside one transaction** (both engines support transactional DDL). Commit only if every check
  passes; otherwise roll back.
- The run history (`rp_runs`) is the only table that accumulates; it is written after the build, in its
  own transaction.
- Exports are written to a temporary folder, verified, then swapped in; the previous export folder is
  kept until the new one is complete.
- The pipeline only works in a database or schema that carries its marker (`rp_metadata`), so a rebuild
  can never drop someone else's tables.

## Consequences

- Reruns are idempotent: same sources, same model, byte-identical exports.
- Every run re-reads all sources: simple and safe at the demo volume, not designed for very large
  histories (incremental loading is a possible improvement).
- If the export fails after the commit, the model is newer than the exports until the next successful
  run; the run is reported as failed.
