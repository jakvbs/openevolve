#!/usr/bin/env bash
set -euo pipefail

# Exploration workflow (bigger rewrites) for raw SQL
# Usage: ./run_explore.sh [OUTPUT_DIR] [--promote] [extra OpenEvolve args]

DIR="$(cd "$(dirname "$0")" && pwd)"
OUT_DIR="$DIR/openevolve_output_explore"
PROMOTE=0

ARGS=()
if [ $# -gt 0 ] && [[ ! "$1" =~ ^- ]]; then
  OUT_DIR="$1"; shift
fi
while (( "$#" )); do
  case "$1" in
    --promote) PROMOTE=1; shift ;;
    *) ARGS+=("$1"); shift ;;
  esac
done

mkdir -p "$OUT_DIR"
# Ensure evaluator attaches bottlenecks artifacts during OpenEvolve runs
export EVAL_ATTACH_BOTTLENECKS=1
export EVAL_BOTTLENECKS_PARETO="${EVAL_BOTTLENECKS_PARETO:-0.90}"
LATEST=""
if [ -d "$OUT_DIR/checkpoints" ]; then
  LATEST=$(ls -d "$OUT_DIR"/checkpoints/checkpoint_* 2>/dev/null | sort -V | tail -1 || true)
fi

CMD=(python3 "$DIR/../../openevolve-run.py" "$DIR/query.sql" "$DIR/evaluator.py" --config "$DIR/config.explore.yaml" --output "$OUT_DIR" "${ARGS[@]}")
if [ -n "$LATEST" ]; then
  echo "Resuming exploration from: $LATEST"
  CMD+=(--checkpoint "$LATEST")
else
  echo "Starting exploration in: $OUT_DIR"
fi
echo "Running: ${CMD[*]}"
"${CMD[@]}"

if [ "$PROMOTE" -eq 1 ]; then
  BEST="$OUT_DIR/best/best_program.sql"
  if [ -f "$BEST" ]; then
    cp -f "$BEST" "$DIR/query.sql"
    echo "Promoted $BEST -> $DIR/query.sql"
  else
    echo "[warn] best_program.sql not found in $OUT_DIR/best; nothing to promote" >&2
  fi
fi
