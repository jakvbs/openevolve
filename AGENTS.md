# AGENTS.md — Guidance for Agents working on OpenEvolve (SQL Query Opt)

This file defines how agents should operate inside this repository, with focus on `examples/sql_query_opt/` (PostgreSQL query optimization, benchmarking, and A/B index testing). These rules apply to the entire repo; items scoped to SQL are explicitly marked.

## Do
- Prefer the existing tooling under `examples/sql_query_opt/` for all SQL benchmarking.
  - Runner (one scenario, paired baseline vs candidate): `./run_pair_bottlenecks.sh --scenario <Sx>`
  - Quick A/B for a single index file: `./ab_index_quick.sh <indexes/idx_*.sql> "S2_isin,S3_accountType"`
  - Heavy A/B (longer substrings, deeper OFFSET) for ILIKE/TRGM: `./heavy_ab_trgm.sh <idx.sql> <globalSearch|postCode> <source> <len>`
  - Full evaluator (all 9 scenarios): `python3 evaluator.py <sql> --out artifacts/metrics_*.json`
  - Duration (SELECT timing, median of 3): `python3 measure_duration.py`

- Use the local PostgreSQL with these defaults unless explicitly overridden:
  - `PGPASSWORD='secret-password'`
  - `psql -h localhost -p 5432 -U shareholder-register -d shareholder-register`
  - The example assumes an issuer ident `'TEST-ISSUER'` and parameterized SQL files (psql `-v` variables).

- Follow the benchmark policy (SQL example scope):
  - Phase 1: optimize the query only (no schema changes). Validate functional equivalence first.
  - Phase 2: test indexes via A/B (NO INDEX → WITH INDEX). Accept an index only if:
    - At least 2× improvement in a primary metric (shared_read_total or total_cost_total) for candidate SQL, and
    - No metric degrades by >10%, and
    - `functional_ok == 1.0` in both variants.
  - Keep `parallel_evaluations=1` (avoid cache/lock interference).

- Functional verification first (critical):
  - Use `evaluator.py` which compares ordered results of candidate vs baseline.
  - Reject any candidate with differences (non‑zero diff) — do not proceed to performance comparison.

- Use EXPLAIN with timing disabled for plan metrics:
  - `EXPLAIN (ANALYZE, BUFFERS, COSTS ON, TIMING OFF, FORMAT JSON)`
  - Primary metrics: `Shared Read Blocks`, root `Total Cost`
  - Secondary: `Rows Removed by Filter`, `Temp Read/Temp Written Blocks`, `scan_types`
  - For wall‑clock measurements, use `measure_duration.py` (not EXPLAIN timing).

- Control timeouts explicitly:
  - Evaluator per‑scenario: `EVAL_TIMEOUT` (seconds); dynamic cap can be disabled via `EVAL_DYNAMIC_DISABLE=1`.
  - Pair runner: `TIMEOUT_MS` (psql `statement_timeout`), default 60000.

- Keep artifacts organized (agents should not remove them):
  - Per run plans: `examples/sql_query_opt/artifacts/runs/<YYYYmmdd_HHMMSS>_<sql_stem>/net_movement_S*_plan.json`
  - Pair reports: `examples/sql_query_opt/artifacts/pairs/<timestamp>_<scenario>/pair_report.md`
  - Quick A/B: `examples/sql_query_opt/artifacts/ab_quick_<idx_name>/`
  - Duration: `examples/sql_query_opt/artifacts/duration_<timestamp>/results.json`

- Prefer parameter substitution via psql `-v` vars over string editing in SQL files. Always keep all query parameters declared at the top of the `.sql` files and pass values via CLI.

- When editing `candidate.sql` (SQL example scope):
  - Maintain projection and row order identical to baseline unless the task explicitly allows a schema/DTO change.
  - Favor SARGable predicates and index‑friendly constructs (e.g., `LATERAL` + `DISTINCT ON` for latest‑row lookups, `JOIN` to unnested parameter lists instead of `ANY()` for better planning).
  - Prefer `ILIKE` + GIN trgm for text search; avoid `UPPER(col) LIKE UPPER(...)` unless required by compatibility.

- For index experiments (SQL example scope):
  - Put each candidate DDL in `examples/sql_query_opt/indexes/<file>.sql` using idempotent `CREATE INDEX IF NOT EXISTS ...`.
  - Drop by name with `DROP INDEX IF EXISTS <name>` and run `ANALYZE` on affected tables before measurements.

- Document results succinctly:
  - For each accepted attempt: produce a short table (per scenario) with baseline/candidate cost, read/temp totals, and ratios; add a link/path to artifacts.

- SQL example scope — net‑movement (`examples/sql_query_opt/candidate.sql`): Keep the three LATERAL aggregates for sums semantically identical to `baseline.sql`.
  - `ss` (sum_start) and `es` (sum_end): apply only date bounds and `:filter_isin` inside the LATERALs; do not include other outer filters.
  - `cv` (capital/votes): apply only date bound for `:holdingDate` and `:filter_isin`, plus baseline rules `acc.account_type <> 'ISSUANCE_ACCOUNT' OR acc.account_type IS NULL` and `i.status IS NULL OR i.status <> 'SUSPENDED'`.
  - Verify with: `EVAL_TIMEOUT=60 EVAL_DYNAMIC_DISABLE=1 python3 examples/sql_query_opt/evaluator.py candidate.sql --out examples/sql_query_opt/artifacts/metrics_candidate.json` and require `.metrics.functional_ok == 1.0`.

 - SQL example scope — workflow gate: Before any pair benchmarks, require `.metrics.functional_ok == 1.0` for `candidate.sql`.
   - Command: `EVAL_TIMEOUT=60 EVAL_DYNAMIC_DISABLE=1 python3 examples/sql_query_opt/evaluator.py candidate.sql --out examples/sql_query_opt/artifacts/metrics_candidate.json` (check with `jq '.metrics.functional_ok'`).

 - SQL example scope — quick pair check: Run a single high‑signal scenario.
   - Command: ``SCENARIO=S7_globalSearch TIMEOUT_MS=60000 ./examples/sql_query_opt/run_pair_bottlenecks.sh``.

 - SQL example scope — batch pair run (S1–S9): Execute all scenarios sequentially.
   - Command: ``for S in S1_minimal S2_isin S3_accountType S4_internalStatus S5_postCode S6_accountNumber S7_globalSearch S8_minStartHolding S9_minNetChange; do ./examples/sql_query_opt/run_pair_bottlenecks.sh --scenario "$S"; done``.

 - SQL example scope — wall‑clock medians: Measure end‑to‑end SELECT times (median of 3) for baseline vs candidate.
   - Command: `python3 examples/sql_query_opt/measure_duration.py` (artifacts under `examples/sql_query_opt/artifacts/duration_*/results.json`).

 - SQL example scope — cache hygiene on data refresh: After DB reload/ETL, clear evaluator’s baseline cache to avoid stale comparisons.
   - Command: `rm -f examples/sql_query_opt/artifacts/cache/baseline_*.tsv examples/sql_query_opt/artifacts/cache/baseline_*.json` (do not remove other artifacts).

 - SQL example scope — Pareto knob: Prefer `--pareto 0.80` for bottleneck summaries; adjust via `PARETO`/`EVAL_PARETO` when iterating quickly.

## Don't
- Don’t modify `~/.codex/AGENTS.md` unless explicitly asked by Jakub.
- Don’t run concurrent evaluators against the same database (avoid `parallel_evaluations > 1`).
- Don’t accept an optimized query without passing the functional equivalence check.
- Don’t treat raw execution time from EXPLAIN (with `TIMING ON`) as primary evidence; prefer BUFFERS + COST and, if needed, wall‑clock via `measure_duration.py`.
- Don’t create or leave test indexes applied by default; always A/B them and drop when not accepted.
- Don’t hard‑code constants in SQL; keep everything parameterized and injected via `-v`.
- Don’t remove or rewrite artifact history in `examples/sql_query_opt/artifacts/` unless asked; it’s used for summaries and prompts.

- SQL example scope — net‑movement: Don’t push `:filter_accountType`, `:filter_accountNumber`, or `:filter_custodian` into the `ss`/`es`/`cv` LATERAL subqueries; keep these filters only in the outer `WHERE`.
  - Anti‑pattern example (avoid inside LATERAL): `AND acc.account_type IN (SELECT account_type FROM ft)`.

 - SQL example scope — consistency: Don’t compare baseline vs candidate with different `TIMEOUT_MS`/`EVAL_TIMEOUT` values within the same run; keep them identical for fairness.
