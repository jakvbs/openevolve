#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, os, shlex, subprocess, time, statistics, math
from pathlib import Path
from typing import Any, Dict, Tuple
try:
    from openevolve.evaluation_result import EvaluationResult  # type: ignore
except Exception:  # fallback for direct CLI usage
    class EvaluationResult(dict):
        def __init__(self, metrics: Dict[str, float], artifacts: Dict[str, Any] = None):
            super().__init__()
            self.metrics = metrics
            self.artifacts = artifacts or {}
        def to_dict(self):
            return self.metrics

DIR = Path(__file__).parent
ARTIFACTS_ROOT = DIR / "artifacts"

PG = dict(
    host=os.environ.get("PGHOST", "localhost"),
    port=os.environ.get("PGPORT", "5432"),
    user=os.environ.get("PGUSER", "shareholder-register"),
    db=os.environ.get("PGDATABASE", "shareholder-register"),
    password=os.environ.get("PGPASSWORD", "secret-password"),
)


def _psql_explain(rendered_sql: str, timeout_sec: int | None) -> Tuple[dict, str]:
    sql_text = rendered_sql.strip()
    if not sql_text.endswith(";"):
        sql_text += ";"
    env = os.environ.copy()
    env["PGPASSWORD"] = PG["password"]
    stmt = (
        (f"SET statement_timeout = 0; " if timeout_sec is None else f"SET statement_timeout = {int(timeout_sec*1000)}; ")
        + "EXPLAIN (ANALYZE, BUFFERS, COSTS ON, TIMING OFF, FORMAT JSON)\n"
        + sql_text
    )
    cmd = [
        "psql", "-h", PG["host"], "-p", str(PG["port"]), "-U", PG["user"], "-d", PG["db"],
        "-X", "-q", "-t", "-A", "-v", "ON_ERROR_STOP=1", "-c", stmt
    ]
    kwargs = dict(env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
    proc = subprocess.run(cmd, **kwargs, timeout=(None if timeout_sec is None else timeout_sec + 5))
    if proc.returncode != 0:
        raise RuntimeError(f"psql failed ({proc.returncode}):\n{proc.stderr.strip() or proc.stdout.strip()}\nCmd: {shlex.join(cmd)}")
    raw = proc.stdout.strip()
    try:
        doc = json.loads(raw)
        plan = (doc[0] if isinstance(doc, list) else doc)
    except Exception:
        # try to find JSON in mixed output
        start = raw.find("[")
        end = raw.rfind("]")
        plan = json.loads(raw[start:end+1])[0]
    return plan, raw


def _psql_explain_timing(rendered_sql: str, timeout_sec: int | None, io_timing: bool = True) -> Tuple[dict, str]:
    sql_text = rendered_sql.strip()
    if not sql_text.endswith(";"):
        sql_text += ";"
    env = os.environ.copy()
    env["PGPASSWORD"] = PG["password"]
    pre = []
    if io_timing:
        pre.append("SET track_io_timing = on; ")
    pre.append("SET enable_incremental_sort = on; ")
    pre_stmt = "".join(pre)
    stmt = (
        (f"SET statement_timeout = 0; " if timeout_sec is None else f"SET statement_timeout = {int(timeout_sec*1000)}; ")
        + pre_stmt
        + "EXPLAIN (ANALYZE, BUFFERS, COSTS ON, TIMING ON, FORMAT JSON)\n"
        + sql_text
    )
    cmd = [
        "psql", "-h", PG["host"], "-p", str(PG["port"]), "-U", PG["user"], "-d", PG["db"],
        "-X", "-q", "-t", "-A", "-v", "ON_ERROR_STOP=1", "-c", stmt
    ]
    proc = subprocess.run(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False,
                          timeout=(None if timeout_sec is None else timeout_sec + 5))
    if proc.returncode != 0:
        raise RuntimeError(f"psql (TIMING ON) failed ({proc.returncode}):\n{proc.stderr.strip() or proc.stdout.strip()}\nCmd: {shlex.join(cmd)}")
    raw = proc.stdout.strip()
    try:
        doc = json.loads(raw)
        plan = (doc[0] if isinstance(doc, list) else doc)
    except Exception:
        start = raw.find("["); end = raw.rfind("]")
        plan = json.loads(raw[start:end+1])[0]
    return plan, raw


def _walk(n: Dict[str, Any], acc: Dict[str, Any]) -> None:
    for k_src, k_dst in (
        ("Shared Read Blocks", "shared_read_total"),
        ("Shared Hit Blocks", "shared_hit_total"),
        ("Temp Read Blocks", "temp_read_total"),
        ("Temp Written Blocks", "temp_written_total"),
        ("Rows Removed by Filter", "rows_removed_total"),
    ):
        if k_src in n:
            acc[k_dst] = acc.get(k_dst, 0) + int(n[k_src] or 0)
    nt = n.get("Node Type")
    if nt:
        st = acc.setdefault("scan_types", {})
        st[nt] = st.get(nt, 0) + 1
    for ch in n.get("Plans", []) or []:
        _walk(ch, acc)


def _collect_nodes_with_time(n: Dict[str, Any], bag: list[dict]) -> None:
    item = {
        "node_type": n.get("Node Type"),
        "relation": n.get("Relation Name") or n.get("Alias"),
        "actual_total_time": float(n.get("Actual Total Time", 0.0) or 0.0),
        "actual_rows": int(n.get("Actual Rows", 0) or 0),
        "plan_rows": int(n.get("Plan Rows", 0) or 0),
        "shared_read": int(n.get("Shared Read Blocks", 0) or 0),
        "temp_read": int(n.get("Temp Read Blocks", 0) or 0) + int(n.get("Temp Written Blocks", 0) or 0),
    }
    bag.append(item)
    for ch in n.get("Plans", []) or []:
        _collect_nodes_with_time(ch, bag)


def _run_select(sql_text: str, timeout_sec: int) -> float:
    env = os.environ.copy()
    env["PGPASSWORD"] = PG["password"]
    # Ensure trailing semicolon for -f path route; write to temp file
    tmp = Path(os.getenv("TMPDIR", "/tmp")) / f"oe_select_{int(time.time()*1000)}.sql"
    tmp.write_text(sql_text if sql_text.strip().endswith(";") else sql_text.strip() + ";", encoding="utf-8")
    args = [
        "psql", "-h", PG["host"], "-p", str(PG["port"]), "-U", PG["user"], "-d", PG["db"],
        "-X", "-q", "-t", "-A", "-v", "ON_ERROR_STOP=1",
        "-c", f"SET statement_timeout = {int(timeout_sec*1000)};",
        "-f", str(tmp),
    ]
    t0 = time.monotonic()
    proc = subprocess.run(args, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    dt = (time.monotonic() - t0) * 1000.0
    try:
        tmp.unlink(missing_ok=True)
    except Exception:
        pass
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "SELECT failed")
    return dt


def evaluate_internal(sql_path: Path, out_dir: Path, timeout_sec: int | None) -> Dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    sql_text = sql_path.read_text(encoding="utf-8")
    t0 = time.monotonic()
    plan_json, raw = _psql_explain(sql_text, timeout_sec=timeout_sec)
    elapsed_ms = int((time.monotonic() - t0) * 1000)
    # Normalize plan object
    doc = plan_json if isinstance(plan_json, dict) else plan_json[0]
    plan = doc.get("Plan") or doc  # accept raw Plan or wrapped
    (out_dir / "plan.json").write_text(json.dumps({"Plan": plan}, indent=2), encoding="utf-8")
    # Metrics
    acc: Dict[str, Any] = {}
    _walk(plan, acc)
    acc["total_cost_total"] = float(plan.get("Total Cost", 0.0) or 0.0)
    acc["plan_rows_root_sum"] = int(plan.get("Plan Rows", 0) or 0)
    acc["actual_rows_root_sum"] = int(plan.get("Actual Rows", 0) or 0)
    acc["timeouts"] = 0
    # Initial combined score (log-normalized, without wall-clock yet)
    rd = float(acc.get("shared_read_total", 0.0))
    ct = float(acc.get("total_cost_total", 0.0))
    sr = 1.0 / (1.0 + math.log1p(rd))
    sc = 1.0 / (1.0 + math.log1p(ct))
    # Default weights when wall-clock is not yet known
    try:
        w_read, w_cost = [float(x) for x in os.environ.get("EVAL_CS_WEIGHTS_NO_TIME", "0.85,0.15").split(",")]
    except Exception:
        w_read, w_cost = 0.85, 0.15
    acc["combined_score"] = w_read * sr + w_cost * sc
    payload = {
        "metrics": acc,
        "artifacts": {"plan_path": str(out_dir / "plan.json"), "run_dir": str(out_dir), "explain_elapsed_ms": elapsed_ms},
    }
    (out_dir / "metrics.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def _summarize_bottlenecks_from_plan(plan_path: Path, pareto: float | None = 0.90, top: int | None = None) -> Dict[str, Any]:
    doc = json.loads(plan_path.read_text(encoding="utf-8"))
    if isinstance(doc, list):
        doc = doc[0]
    plan = doc.get("Plan") or doc
    def walk_node(node, acc):
        nt = node.get("Node Type") or "?"
        rn = node.get("Relation Name") or node.get("Alias") or "?"
        key = (nt, rn)
        d = acc.setdefault(key, {"shared_read": 0, "rows_removed": 0, "temp": 0, "cost": 0.0, "filters": {}, "count": 0})
        d["shared_read"] += int(node.get("Shared Read Blocks", 0) or 0)
        d["rows_removed"] += int(node.get("Rows Removed by Filter", 0) or 0)
        d["temp"] += int(node.get("Temp Read Blocks", 0) or 0) + int(node.get("Temp Written Blocks", 0) or 0)
        d["cost"] += float(node.get("Total Cost", 0.0) or 0.0)
        d["count"] += 1
        filt = node.get("Filter")
        if filt:
            s = str(filt).replace("\n", " ")
            s = s[:140] + ("…" if len(s) > 140 else "")
            d["filters"][s] = d["filters"].get(s, 0) + 1
        for ch in node.get("Plans", []) or []:
            walk_node(ch, acc)
    local = {}
    walk_node(plan, local)
    ranked = []
    for key, m in local.items():
        sr = float(m["shared_read"]); tm = float(m["temp"]); rm = float(m["rows_removed"]); ct = float(m["cost"]) / 1000.0
        severity = 3.0 * math.log1p(sr) + 2.0 * math.log1p(tm) + 1.5 * math.log1p(rm) + 1.0 * math.log1p(ct)
        ranked.append((key, m, severity))
    ranked.sort(key=lambda x: x[2], reverse=True)
    if pareto and ranked:
        total = sum(sev for _, _, sev in ranked) or 1.0
        keep = []; cum = 0.0
        for item in ranked:
            keep.append(item); cum += item[2]
            if cum / total >= max(0.0, min(1.0, pareto)):
                break
        topk = keep
    else:
        if top is None:
            try:
                top = int(os.environ.get("EVAL_BOTTLENECKS_TOP", "5"))
            except Exception:
                top = 5
        topk = ranked[: top]
    def hint_for(nt: str) -> str:
        if nt == "Seq Scan": return "Add/adjust index; push filters earlier; consider Bitmap/Index Scan"
        if nt in ("Bitmap Heap Scan", "Bitmap Index Scan"): return "Ensure selectivity and correct join keys; consider covering index"
        if nt in ("Sort", "Incremental Sort"): return "Reduce input rows pre-sort; add index matching ORDER BY; tune work_mem"
        if nt in ("Hash Join", "HashAggregate"): return "Reduce build-side size; add index for join; watch work_mem"
        if nt == "Nested Loop": return "Ensure inner side has index on join key; consider join reorder"
        if nt == "Merge Join": return "Avoid global sorts: add indexes to supply order or change join"
        if nt == "WindowAgg": return "Replace with LATERAL/LIMIT 1 or DISTINCT ON; pre-filter partitions"
        return "Pushdown filters; appropriate indexes; reduce intermediate cardinality"
    results = [
        {
            "node_type": k[0], "relation": k[1],
            "severity": round(sev, 3),
            "shared_read": int(m["shared_read"]),
            "temp_io": int(m["temp"]),
            "rows_removed": int(m["rows_removed"]),
            "total_cost_k": round(float(m["cost"]) / 1000.0, 1),
            "hint": hint_for(k[0]),
            "filters_sample": sorted(m["filters"].items(), key=lambda kv: kv[1], reverse=True)[:2],
        } for k, m, sev in topk
    ]
    out_json = {"plan_file": str(plan_path.name), "results": results}
    lines = ["# Bottlenecks Summary\n", f"Plan: {plan_path.name}\n"]
    if pareto:
        lines.append(f"(Pareto cutoff: {max(0.0, min(1.0, pareto)):.2f})\n")
    for i, item in enumerate(results, 1):
        filt = "; ".join([f"{t}×{c}" for t, c in item["filters_sample"]]) if item["filters_sample"] else ""
        lines.append(f"- {i}. {item['node_type']} on {item['relation']} | sev={item['severity']} | read={item['shared_read']} | temp={item['temp_io']} | rm={item['rows_removed']} | cost_k={item['total_cost_k']}\n  hint: {item['hint']}\n  filters: {filt}")
    md_text = "\n".join(lines) + "\n"
    return {"md": md_text, "all": results, "top": results[:5]}


def evaluate(program_path):
    """
    OpenEvolve entrypoint: evaluate(program_path) -> dict/EvaluationResult-compatible.
    Creates a timestamped artifacts dir and uses env EVAL_TIMEOUT (seconds).
    """
    ts = time.strftime("%Y%m%d_%H%M%S")
    out_dir = ARTIFACTS_ROOT / ts
    timeout_env = os.environ.get("EVAL_TIMEOUT")
    timeout_sec = None if (timeout_env and timeout_env == "0") else int(timeout_env) if timeout_env else 60
    payload = evaluate_internal(Path(program_path), out_dir, timeout_sec)
    artifacts = payload.get("artifacts", {}).copy()
    # Optional auto-emission of bottlenecks (env-driven in OpenEvolve flow)
    emit_bn = os.environ.get("EVAL_ATTACH_BOTTLENECKS", "0") in ("1", "true", "True")
    if emit_bn:
        plan_path = Path(artifacts["plan_path"])
        bn = _summarize_bottlenecks_from_plan(
            plan_path,
            pareto=float(os.environ.get("EVAL_BOTTLENECKS_PARETO","0.90")),
            top=int(os.environ["EVAL_BOTTLENECKS_TOP"]) if "EVAL_BOTTLENECKS_TOP" in os.environ else None
        )
        md_text = bn["md"]
        artifacts.update({
            "bottlenecks_md": md_text
        })
    # Optional: SELECT wall-clock runs via env (for OpenEvolve flow)
    try:
        select_runs_env = int(os.environ.get("EVAL_SELECT_RUNS", "0"))
    except Exception:
        select_runs_env = 0
    if select_runs_env > 0:
        sql_text = Path(program_path).read_text(encoding="utf-8")
        timeout_env = os.environ.get("EVAL_TIMEOUT")
        timeout_sel = None if (timeout_env and timeout_env == "0") else int(timeout_env) if timeout_env else 60
        # Warmup
        try:
            _ = _run_select(sql_text, timeout_sel or 60)
        except Exception as e:
            payload["metrics"]["select_error"] = str(e)
        samples = []
        ok = 0
        for _ in range(select_runs_env):
            try:
                dt = _run_select(sql_text, timeout_sel or 60)
                samples.append(dt)
                ok += 1
            except Exception as e:
                samples.append(float("nan"))
                payload["metrics"]["select_error"] = str(e)
        if ok > 0:
            vals = [x for x in samples if x == x]
            med = statistics.median(vals)
            p95 = sorted(vals)[max(0, int(len(vals)*0.95)-1)]
            payload["metrics"]["select_runs"] = select_runs_env
            payload["metrics"]["select_median_ms"] = round(med, 3)
            payload["metrics"]["select_p95_ms"] = round(p95, 3)
            # Recompute combined_score with wall-clock component
            rd = float(payload["metrics"].get("shared_read_total", 0.0))
            ct = float(payload["metrics"].get("total_cost_total", 0.0))
            tm = float(payload["metrics"].get("select_median_ms", 0.0))
            sr = 1.0 / (1.0 + math.log1p(rd))
            sc = 1.0 / (1.0 + math.log1p(ct))
            st = 1.0 / (1.0 + math.log1p(tm))
            try:
                w_read, w_time, w_cost = [float(x) for x in os.environ.get("EVAL_CS_WEIGHTS", "0.5,0.4,0.1").split(",")]
            except Exception:
                w_read, w_time, w_cost = 0.5, 0.4, 0.1
            s = max(w_read + w_time + w_cost, 1e-9)
            w_read, w_time, w_cost = w_read/s, w_time/s, w_cost/s
            payload["metrics"]["combined_score"] = round(w_read*sr + w_time*st + w_cost*sc, 12)
    # Return EvaluationResult to preserve artifacts across iterations
    return EvaluationResult(metrics=payload["metrics"], artifacts=artifacts)


def main():
    ap = argparse.ArgumentParser(description="Evaluate a raw SQL (no scenarios, no placeholders).")
    ap.add_argument("sql_file", help="Path to a SQL file with all params inlined.")
    ap.add_argument("--out-dir", help="Artifacts directory (default: artifacts/<timestamp>)")
    ap.add_argument("--timeout", type=int, default=int(os.environ.get("EVAL_TIMEOUT", "60")), help="Statement timeout in seconds (0=no timeout).")
    ap.add_argument("--select-runs", type=int, default=0, help="If >0, run the SELECT N times (after 1x warmup) and record median/p95 ms.")
    ap.add_argument("--timing", action="store_true", help="Also collect a TIMING ON plan and top per-node times.")
    ap.add_argument("--attach-bottlenecks", action="store_true", help="Attach bottlenecks summary (markdown + top list) to artifacts.")
    ap.add_argument("--bottlenecks-pareto", type=float, default=float(os.environ.get("EVAL_BOTTLENECKS_PARETO","0.90")), help="Pareto cutoff (0..1) used for bottlenecks.")
    ap.add_argument("--bottlenecks-top", type=int, default=int(os.environ.get("EVAL_BOTTLENECKS_TOP","5")), help="Top-K items when pareto=0.")
    args = ap.parse_args()
    sql_path = Path(args.sql_file)
    if not sql_path.exists():
        raise SystemExit(f"SQL file not found: {sql_path}")
    ts = time.strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.out_dir) if args.out_dir else ARTIFACTS_ROOT / ts
    timeout_sec = None if args.timeout == 0 else args.timeout
    res = evaluate_internal(sql_path, out_dir, timeout_sec)
    # Optional: SELECT wall-clock runs
    if args.select_runs and args.select_runs > 0:
        sql_text = sql_path.read_text(encoding="utf-8")
        # Warmup
        try:
            _ = _run_select(sql_text, timeout_sec or 60)
        except Exception as e:
            # Keep going but note failure
            res["metrics"]["select_error"] = str(e)
        samples = []
        ok = 0
        for _ in range(args.select_runs):
            try:
                dt = _run_select(sql_text, timeout_sec or 60)
                samples.append(dt)
                ok += 1
            except Exception as e:
                samples.append(float("nan"))
                res["metrics"]["select_error"] = str(e)
        if ok > 0:
            vals = [x for x in samples if x == x]
            med = statistics.median(vals)
            p95 = sorted(vals)[max(0, int(len(vals)*0.95)-1)]
            res["metrics"]["select_runs"] = args.select_runs
            res["metrics"]["select_median_ms"] = round(med, 3)
            res["metrics"]["select_p95_ms"] = round(p95, 3)
            # Recompute combined_score with wall-clock component
            rd = float(res["metrics"].get("shared_read_total", 0.0))
            ct = float(res["metrics"].get("total_cost_total", 0.0))
            tm = float(res["metrics"].get("select_median_ms", 0.0))
            sr = 1.0 / (1.0 + math.log1p(rd))
            sc = 1.0 / (1.0 + math.log1p(ct))
            st = 1.0 / (1.0 + math.log1p(tm))
            try:
                w_read, w_time, w_cost = [float(x) for x in os.environ.get("EVAL_CS_WEIGHTS", "0.5,0.4,0.1").split(",")]
            except Exception:
                w_read, w_time, w_cost = 0.5, 0.4, 0.1
            # Normalize weights defensively
            s = max(w_read + w_time + w_cost, 1e-9)
            w_read, w_time, w_cost = w_read/s, w_time/s, w_cost/s
            res["metrics"]["combined_score"] = round(w_read*sr + w_time*st + w_cost*sc, 12)
            (out_dir / "metrics.json").write_text(json.dumps(res, indent=2), encoding="utf-8")
    # Optional: TIMING ON plan
    if args.timing:
        sql_text = sql_path.read_text(encoding="utf-8")
        t0 = time.monotonic()
        plan_timing, raw = _psql_explain_timing(sql_text, timeout_sec=timeout_sec)
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        doc = plan_timing if isinstance(plan_timing, dict) else plan_timing[0]
        plan = doc.get("Plan") or doc
        (out_dir / "plan_timing.json").write_text(json.dumps({"Plan": plan}, indent=2), encoding="utf-8")
        nodes: list[dict] = []
        _collect_nodes_with_time(plan, nodes)
        nodes.sort(key=lambda x: x.get("actual_total_time", 0.0), reverse=True)
        top = nodes[:20]
        (out_dir / "per_node_top_time.json").write_text(json.dumps(top, indent=2), encoding="utf-8")
        res["metrics"]["timing_plan_elapsed_ms"] = elapsed_ms
        res["metrics"]["timing_available"] = True
        (out_dir / "metrics.json").write_text(json.dumps(res, indent=2), encoding="utf-8")
    # Optional: bottlenecks generation (CLI flag)
    if args.attach_bottlenecks:
        bn = _summarize_bottlenecks_from_plan(out_dir / "plan.json", pareto=args.bottlenecks_pareto, top=args.bottlenecks_top)
        res["artifacts"]["bottlenecks_md"] = bn["md"]
        (out_dir / "metrics.json").write_text(json.dumps(res, indent=2), encoding="utf-8")
    print(json.dumps({"artifacts_dir": str(out_dir), **res}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
