Raw SQL Evaluation (single query, no scenarios)

Overview
- Uproszczony przepływ: rezygnacja ze scenariuszy i parametryzacji.
- Punkt wejścia to plik SQL z już wstawionymi wartościami parametrów.
- Narzędzia:
  - `evaluator.py` — uruchamia EXPLAIN (ANALYZE, BUFFERS, TIMING OFF, JSON) dla jednego zapytania i zapisuje metryki; wspiera `--select-runs`, `--timing`, `--attach-bottlenecks`.
  - `run_refine.sh`, `run_explore.sh` — workflowy OpenEvolve (małe/duże zmiany) dla `query.sql`.

Prerequisites
- Działający PostgreSQL (domyślnie `localhost:5432`, db/user: `shareholder-register`).
- Eksport `PGPASSWORD='secret-password'` lub ustawienia w `PGHOST/PGPORT/PGUSER/PGDATABASE/PGPASSWORD`.

Quick start (jedno uruchomienie)
```bash
# Przygotuj plik SQL z wypełnionymi wartościami, np. query.sql
python3 evaluator.py ./query.sql --out-dir artifacts/$(date +%s) --timeout 60 --attach-bottlenecks
# (opcjonalnie) dodaj pomiar czasu ściennego: --select-runs 3
```
Artefakty trafią do `examples/sql_query_opt/artifacts/<timestamp>/`:
- `plan.json` — plan EXPLAIN JSON
- `metrics.json` — zsumowane metryki planu; w `artifacts.bottlenecks_md` tekstowy raport bottlenecków (gdy dołączony)

OpenEvolve (evolution)
```bash
# Refinement (małe patche)
./run_refine.sh --promote
# Exploration (większe przebudowy)
./run_explore.sh --promote
```
Skrypty domyślnie ustawiają: 
- `EVAL_ATTACH_BOTTLENECKS=1` (raport bottlenecków do artifacts),
- `EVAL_BOTTLENECKS_PARETO=0.90`,
- tylko w refine: `EVAL_SELECT_RUNS=3` (włącza medianę czasu do combined_score).

Scoring (combined_score)
- Bez pomiaru SELECT: log‑normalizowany miks odczytów i kosztu:
  - `combined_score = w_read·1/(1+log1p(reads)) + w_cost·1/(1+log1p(cost))`
  - Domyślne wagi: `EVAL_CS_WEIGHTS_NO_TIME="0.85,0.15"`
- Z pomiarem SELECT (`--select-runs N`): dołączamy medianę czasu:
  - `combined_score = w_read·reads + w_time·time + w_cost·cost` (każdy składnik w formie `1/(1+log1p(x))`)
  - Domyślne wagi: `EVAL_CS_WEIGHTS="0.5,0.4,0.1"`

Bottlenecks (raport w artifacts)
- Dołącz: `--attach-bottlenecks` lub `EVAL_ATTACH_BOTTLENECKS=1`.
- Domyślnie Pareto 0.90 (liczba pozycji zależy od rozkładu severity).
- Przełącz na top‑K: `--bottlenecks-pareto 0` i `--bottlenecks-top K` (domyślnie K=5; `EVAL_BOTTLENECKS_TOP`).
- Raport trafia jako `artifacts.bottlenecks_md` (markdown). Nie zapisujemy osobnych plików bottlenecków.

Parametry TIMING (profilowanie per‑node)
- `--timing` generuje dodatkowo `plan_timing.json` i `per_node_top_time.json` (czasy per węzeł); kosztowne — używaj on‑demand.

Notes
- `schema.sql` i `schema_digest.md` pozostają jako referencja dla lokalnej bazy.
- Dodatkowe strojenie: `EVAL_TIMEOUT` (sekundy), `EVAL_BOTTLENECKS_PARETO`, `EVAL_BOTTLENECKS_TOP`, `EVAL_CS_WEIGHTS`, `EVAL_CS_WEIGHTS_NO_TIME`.

