#!/usr/bin/env python3
"""Oracle -> Postgres migration bakeoff orchestrator.

  python run_bakeoff.py                       # serve the dashboard; runs start
                                              #   from its "Run bakeoff" button
  python run_bakeoff.py --once                # run immediately, then exit
  python run_bakeoff.py --once --contestants baseline   # pipeline smoke test (free)
  python run_bakeoff.py --num-dbs 100 --parallelism 8
  python run_bakeoff.py --generate-only       # just emit the Oracle sources
  python run_bakeoff.py --revalidate          # re-score existing workspaces
"""
import argparse
import json
import shutil
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from bakeoff.config import load_config
from bakeoff.contestants import run_contestant, write_planning_task, write_task_file
from bakeoff.live import DashboardServer, LiveRun
from bakeoff.oracle_gen import generate_fleet
from bakeoff.quality import (LiveQualityWatcher, apply_exemptions, dev_findings,
                             output_findings, snapshot_source, summarize_findings)
from bakeoff.report import write_reports
from bakeoff.shadow import ShadowExecutor
from bakeoff.targets import TARGETS
from bakeoff.validate import loc_of_migration, validate_workspace
from bakeoff.visualize import render_live_page

ROOT = Path(__file__).resolve().parent


def prepare_workspace(src_db_dir, ws_dir, target_cls):
    """Give a contestant a clean copy of one DB export (no manifest = no answers).
    Phase-specific task files are written by run_job as each phase starts."""
    if ws_dir.exists():
        shutil.rmtree(ws_dir)
    ws_dir.mkdir(parents=True)
    shutil.copytree(src_db_dir / "source", ws_dir / "source")
    (ws_dir / "migrated").mkdir()
    (ws_dir / "plan").mkdir()


def run_job(contestant, db_name, ws_dir, manifest, cfg, raw_dir, live=None):
    timeout = cfg["run"]["agent_timeout_s"]
    target_name = cfg["run"]["target"]
    target_cls = TARGETS[target_name]
    planning = cfg["run"].get("planning_phase", True)
    save_raw = cfg["output"].get("save_raw_agent_output", True)
    raw_path = raw_dir / f"{contestant}__{db_name}.log" if save_raw else None
    print(f"  ▶ {contestant} × {db_name} ...", flush=True)
    on_event = None
    if live:
        live.job_status(contestant, db_name, "running")
        live.event(contestant, db_name, "status", "started")
        on_event = lambda kind, text: live.event(contestant, db_name, kind, text)

    # testing layer: snapshot the source, watch for mistakes while the agent works
    qa_exempt = set(cfg["output"].get("quality_exempt", {}).get(contestant) or ())
    before_hashes = snapshot_source(ws_dir)
    watcher = None
    if on_event:
        watcher = LiveQualityWatcher(
            ws_dir, before_hashes,
            lambda sev, msg: on_event("issue", f"[{sev}] {msg}"),
            exempt=qa_exempt, target_name=target_name).start()

    # ---- phase 1: architectural migration plan ----
    plan_info = None
    plan_findings = []
    if planning:
        if live:
            live.job_phase(contestant, db_name, "plan")
            live.event(contestant, db_name, "phase",
                       "▶ PHASE 1 — producing architectural migration plan")
        write_planning_task(ws_dir, target_cls)
        plan_raw = raw_dir / f"{contestant}__{db_name}__plan.log" if save_raw else None
        plan_timeout = cfg["run"].get("plan_timeout_s", 600)
        plan_metrics = run_contestant(contestant, ws_dir, cfg, plan_timeout,
                                      plan_raw, target_name, on_event, phase="plan")
        plan_file = ws_dir / "plan" / "MIGRATION_PLAN.md"
        plan_text = plan_file.read_text() if plan_file.exists() else ""
        plan_lines = sum(1 for ln in plan_text.splitlines() if ln.strip())
        plan_info = {"agent": plan_metrics, "exists": plan_file.exists(),
                     "lines": plan_lines, "text": plan_text[:40000] or None}
        if live and plan_text:
            # surface the plan itself: its section headings, or first lines
            heads = [ln.strip() for ln in plan_text.splitlines()
                     if ln.strip().startswith("#")]
            excerpt = heads if len(heads) >= 3 else \
                [ln.strip() for ln in plan_text.splitlines() if ln.strip()][:10]
            live.job_plan(contestant, db_name, plan_lines, excerpt,
                          plan_text[:40000])
        if not plan_file.exists():
            plan_findings.append({"phase": "plan", "severity": "error",
                                  "check": "missing-plan",
                                  "message": "phase 1 produced no plan/MIGRATION_PLAN.md"})
        elif plan_lines < 15:
            plan_findings.append({"phase": "plan", "severity": "warn",
                                  "check": "thin-plan",
                                  "message": f"migration plan is only {plan_lines} "
                                             f"non-blank lines"})
        if live:
            live.event(contestant, db_name, "phase",
                       f"■ PHASE 1 COMPLETE — plan: {plan_lines} lines, "
                       f"{plan_metrics['wall_time_s']}s"
                       + (f", ${plan_metrics['cost_usd']:.2f}"
                          if plan_metrics.get("cost_usd") else ""))

    # ---- phase 2: live migration, executing the plan ----
    if live:
        live.job_phase(contestant, db_name, "migrate")
        live.event(contestant, db_name, "phase",
                   "▶ PHASE 2 — live migration started"
                   + (" (executing the phase-1 plan)" if planning else ""))
    write_task_file(ws_dir, target_cls, with_plan=planning)

    flow_pacing = cfg["output"].get("flow_pacing_ms", 40) / 1000.0

    # shadow executor: mirror the agent's SQL into a live scratch schema as it
    # is written, so the dashboard shows DDL + rows moving DURING the run
    shadow = None
    if live and target_name == "postgres" and cfg["output"].get("shadow_execution", True):
        sh_counts = {}

        def sh_table(table, rows, total, final):
            sh_counts[table] = rows
            live.job_rows(contestant, db_name, sum(sh_counts.values()))
            live.job_table(contestant, db_name, table, rows,
                           "loaded" if final else "streaming")
            if final:
                live.event(contestant, db_name, "data",
                           f"{table}: {rows}/{total} rows in target (live from agent SQL)")

        def sh_ddl(table, cols):
            live.job_table(contestant, db_name, table, 0, "created", cols=cols)

        def sh_sql(kind, ok, text, error):
            live.job_sql(contestant, db_name, kind, ok, text, error)
            live.event(contestant, db_name, "sql",
                       ("✓ " if ok else "✗ ") + f"[{kind}] {text[:130]}"
                       + (f" — {error}" if error else ""))

        shadow = ShadowExecutor(ws_dir, cfg["targets"].get("postgres"), manifest,
                                on_sql=sh_sql, on_table=sh_table, on_ddl=sh_ddl,
                                pacing_s=flow_pacing)
        shadow.start()

    try:
        agent_metrics = run_contestant(contestant, ws_dir, cfg, timeout, raw_path,
                                       target_name, on_event)
    finally:
        if watcher:
            watcher.stop()
        if shadow:
            shadow.stop()

    if live:
        live.event(contestant, db_name, "status", "agent finished — validating")

    on_data = None
    pacing_s = 0.0
    if live:
        # if the shadow already streamed this job's data live, the final
        # validation replay just snaps to authoritative numbers quickly
        pacing_s = 0.0 if (shadow and shadow.did_stream) else flow_pacing

        def on_data(table, done, total, moved, expected, fields, final):
            # every batch updates the lane state; only table completion goes
            # to the activity feed (batches would flood it)
            live.job_rows(contestant, db_name, moved)
            live.job_table(contestant, db_name, table, done,
                           "loaded" if final else "streaming")
            if final:
                shown = ", ".join(fields[:6]) + ("…" if len(fields) > 6 else "")
                live.event(contestant, db_name, "data",
                           f"{table} [{shown}]: {done} rows → target  "
                           f"({moved}/{expected} moved)")
    validation = validate_workspace(
        ws_dir, manifest, target_name, cfg["targets"].get(target_name),
        on_data, pacing_s)
    loc = loc_of_migration(ws_dir)

    # testing layer: plan + dev + output findings on the finished job
    findings = apply_exemptions(
        plan_findings
        + dev_findings(ws_dir, before_hashes, agent_metrics)
        + output_findings(ws_dir, manifest, target_name), qa_exempt)
    quality = summarize_findings(findings)
    if live:
        already = {msg for _c, msg in watcher.seen} if watcher else set()
        for f in findings:
            if f["message"] not in already:
                live.event(contestant, db_name, "issue",
                           f"[{f['severity']}] {f['message']}")
    status = "✅" if validation["success"] else "❌"
    print(f"  {status} {contestant} × {db_name}  "
          f"tables {validation['tables_ok']}/{validation['tables_expected']}  "
          f"rows {validation['rows_loaded']}/{validation['rows_expected']}  "
          f"{agent_metrics['wall_time_s']}s", flush=True)
    result = {"db": db_name, "domain": manifest["domain"], "contestant": contestant,
              "agent": agent_metrics, "plan": plan_info,
              "validation": validation, "loc": loc, "quality": quality}
    if live:
        live.event(contestant, db_name, "status",
                   f"{'passed' if validation['success'] else 'FAILED'} — "
                   f"tables {validation['tables_ok']}/{validation['tables_expected']}, "
                   f"rows {validation['rows_loaded']}/{validation['rows_expected']}")
        live.job_done(result)
    return result


def archive_run(cfg, raw_dir, ws_root, results_dir):
    """Preserve this run's evidence under runs/<run>/history/<timestamp>/:
    raw agent logs, every contestant's produced SQL, and the reports. Without
    this, each run overwrites the last — post-mortems become impossible."""
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    hist = ROOT / "runs" / cfg["run"]["name"] / "history" / ts
    hist.mkdir(parents=True, exist_ok=True)
    if raw_dir.exists():
        shutil.copytree(raw_dir, hist / "agent_logs", dirs_exist_ok=True)
    for sub in ("migrated", "plan"):
        for ws in sorted(ws_root.glob(f"*/*/{sub}")):
            dest = hist / sub / ws.parent.parent.name / ws.parent.name
            shutil.copytree(ws, dest, dirs_exist_ok=True)
    for fname in ("results.json", "REPORT.md", "dashboard.html"):
        src = results_dir / fname
        if src.exists():
            shutil.copy2(src, hist / fname)
    return hist


def _print_run_summary(cfg):
    """Print a human-readable pre-run overview for demo audiences."""
    r = cfg["run"]
    inp = cfg["input"]
    feats = [k for k, on in inp["oracle_features"].items() if on]
    lo, hi = inp["tables_per_db"]
    rlo, rhi = inp["rows_per_table"]
    contestants = r["contestants"]
    n_jobs = len(contestants) * r["num_databases"]
    print()
    print("=" * 60)
    print("  Oracle → Lakehouse Migration Bakeoff")
    print("=" * 60)
    print(f"  Run name   : {r['name']}")
    print(f"  Target     : {r['target']}")
    print(f"  Seed       : {r['seed']}  (deterministic — same inputs every run)")
    print()
    print(f"  Databases  : {r['num_databases']}  ({lo}–{hi} tables each, "
          f"{rlo}–{rhi} rows/table)")
    print(f"  Domains    : {', '.join(inp['domains'])}")
    print(f"  Oracle     : {', '.join(feats)}")
    print()
    print(f"  Contestants: {', '.join(contestants)}")
    print(f"  Jobs       : {n_jobs}  "
          f"({len(contestants)} contestants × {r['num_databases']} databases)")
    print(f"  Parallelism: {r['parallelism']}  (concurrent agent runs)")
    print(f"  Phases     : {'1 plan + 2 migrate' if r.get('planning_phase', True) else '2 migrate only'}")
    print("=" * 60)
    print()


def execute_run(cfg, live=None):
    """One full bakeoff: generate the fleet, run every job, write reports."""
    _print_run_summary(cfg)
    run_name = cfg["run"]["name"]
    target_cls = TARGETS[cfg["run"]["target"]]
    sources_dir = ROOT / "runs" / run_name / "sources"
    ws_root = ROOT / "runs" / run_name / "workspaces"
    raw_dir = ROOT / "runs" / run_name / "agent_logs"
    results_dir = ROOT / "results" / run_name

    # 1. generate the Oracle fleet
    print(f"Generating {cfg['run']['num_databases']} Oracle database(s) "
          f"(seed {cfg['run']['seed']}) ...")
    if sources_dir.exists():
        shutil.rmtree(sources_dir)
    manifests = generate_fleet(sources_dir, cfg)
    print(f"  {len(manifests)} database(s) ready under {sources_dir.relative_to(ROOT)}")

    # 2. fan out (contestant x db) jobs
    raw_dir.mkdir(parents=True, exist_ok=True)
    jobs = []
    for m in manifests:
        for contestant in cfg["run"]["contestants"]:
            ws_dir = ws_root / contestant / m["db_name"]
            prepare_workspace(sources_dir / m["db_name"], ws_dir, target_cls)
            jobs.append((contestant, m["db_name"], ws_dir, m))
    if live:
        for contestant, db_name, _w, m in jobs:
            live.add_job(contestant, db_name,
                         rows_expected=sum(t["row_count"] for t in m["tables"]),
                         tables=[{"name": t["name"], "total": t["row_count"],
                                  "fields": [c["name"] for c in t["columns"]]}
                                 for t in m["tables"]])

    print(f"Running {len(jobs)} migration job(s), parallelism="
          f"{cfg['run']['parallelism']}, target={cfg['run']['target']} ...")
    results = []
    with ThreadPoolExecutor(max_workers=cfg["run"]["parallelism"]) as pool:
        futs = [pool.submit(run_job, c, d, w, m, cfg, raw_dir, live)
                for c, d, w, m in jobs]
        for fut in as_completed(futs):
            results.append(fut.result())

    # 3. report
    if live:
        live.finish()
    summary = write_reports(results_dir, cfg, results,
                            events=live.events if live else None,
                            jobs=list(live.jobs.values()) if live else None)
    if cfg["output"].get("archive_runs", True):
        hist = archive_run(cfg, raw_dir, ws_root, results_dir)
        print(f"Run archived to {hist.relative_to(ROOT)}/")
    print(f"\nResults written to {results_dir.relative_to(ROOT)}/")
    print(f"{'contestant':<12}{'success':<12}{'tables':<10}{'rows':<10}"
          f"{'time(s)':<10}{'cost':<12}")
    for cname, s in sorted(summary.items(),
                           key=lambda kv: -(kv[1]["success_rate"] or 0)):
        cost = f"${s['cost_usd']:.4f}" if s["cost_usd"] is not None else "—"
        print(f"{cname:<12}{str(s['fully_successful']) + '/' + str(s['databases']):<12}"
              f"{str(s['table_success_rate']):<10}{str(s['row_match_rate']):<10}"
              f"{str(s['wall_time_s']):<10}{cost:<12}")
    return summary


def serve_forever(cfg, args):
    """Dashboard-first mode: the server stays up and every run is started by
    the "Run bakeoff" button in the page (POST /run), not by re-running this
    script. Ctrl-C stops the server."""
    running = threading.Lock()
    last_results = ROOT / "results" / cfg["run"]["name"] / "results.json"
    initial = None
    if last_results.exists():
        try:
            initial = json.loads(last_results.read_text())
        except Exception:
            pass
    server = DashboardServer(cfg, render_live_page(), on_run=None, initial=initial)

    def do_run():
        with running:
            live = LiveRun(cfg)
            server.attach(live)
            try:
                execute_run(cfg, live)
            except Exception as e:
                print(f"run failed: {e}", flush=True)
                live.event("-", "-", "issue", f"[error] run crashed: {e}")
                live.finish()

    def on_run():
        if not running.acquire(blocking=False):
            return False  # a run is already in progress
        running.release()
        threading.Thread(target=do_run, daemon=True).start()
        return True

    server.on_run = on_run
    try:
        url = server.serve(args.live_port, open_browser=not args.no_browser)
    except OSError as e:
        if getattr(e, "errno", None) != 48:  # EADDRINUSE
            raise
        print(f"Port {args.live_port} is already in use — probably a dashboard "
              f"server from an earlier run.\n"
              f"  kill it:        lsof -ti :{args.live_port} | xargs kill\n"
              f"  or use another: ./run_bakeoff.py --live-port 0   (random free port)")
        return 1
    print(f"Dashboard: {url}")
    print("Press the ▶ Run bakeoff button in the page to start a run. Ctrl-C to quit.")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print("\nshutting down")
        server.shutdown()
    return 0


def revalidate(cfg):
    """Re-score existing workspaces without running agents."""
    run_name = cfg["run"]["name"]
    sources_dir = ROOT / "runs" / run_name / "sources"
    ws_root = ROOT / "runs" / run_name / "workspaces"
    results_dir = ROOT / "results" / run_name
    manifests = [json.loads(p.read_text())
                 for p in sorted(sources_dir.glob("*/manifest.json"))]
    results = []
    for m in manifests:
        for contestant in cfg["run"]["contestants"]:
            ws_dir = ws_root / contestant / m["db_name"]
            validation = validate_workspace(
                ws_dir, m, cfg["run"]["target"], cfg["targets"].get(cfg["run"]["target"]))
            results.append({"db": m["db_name"], "domain": m["domain"],
                            "contestant": contestant,
                            "agent": {"contestant": contestant, "exit_code": None,
                                      "wall_time_s": None, "cost_usd": None,
                                      "tokens_in": None, "tokens_out": None,
                                      "turns": None, "error": None},
                            "validation": validation, "loc": loc_of_migration(ws_dir)})
    write_reports(results_dir, cfg, results)
    print(f"Re-validated {len(results)} workspace(s); reports in "
          f"{results_dir.relative_to(ROOT)}/")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=str(ROOT / "bakeoff.config.yaml"))
    ap.add_argument("--contestants", help="comma list override, e.g. baseline,claude")
    ap.add_argument("--num-dbs", type=int, help="override run.num_databases")
    ap.add_argument("--parallelism", type=int, help="override run.parallelism")
    ap.add_argument("--target", choices=["postgres", "snowflake"])
    ap.add_argument("--no-plan", action="store_true",
                    help="skip phase 1 (architectural migration plan)")
    ap.add_argument("--once", action="store_true",
                    help="run immediately and exit (default is to serve the "
                         "dashboard and wait for its Run button)")
    ap.add_argument("--serve", action="store_true",
                    help="(default) keep the dashboard up; start runs from its button")
    ap.add_argument("--generate-only", action="store_true")
    ap.add_argument("--revalidate", action="store_true",
                    help="skip agents; re-validate existing workspaces")
    ap.add_argument("--no-live", action="store_true",
                    help="disable the real-time dashboard server")
    ap.add_argument("--no-browser", action="store_true",
                    help="don't auto-open the live dashboard in a browser")
    ap.add_argument("--live-port", type=int, default=8765,
                    help="port for the live dashboard (0 = random free port)")
    args = ap.parse_args()

    cfg = load_config(args.config)
    if args.contestants:
        cfg["run"]["contestants"] = [c.strip() for c in args.contestants.split(",")]
    if args.num_dbs:
        cfg["run"]["num_databases"] = args.num_dbs
    if args.parallelism:
        cfg["run"]["parallelism"] = args.parallelism
    if args.target:
        cfg["run"]["target"] = args.target
    if args.no_plan:
        cfg["run"]["planning_phase"] = False

    if args.revalidate:
        return revalidate(cfg)
    if args.generate_only:
        sources_dir = ROOT / "runs" / cfg["run"]["name"] / "sources"
        if sources_dir.exists():
            shutil.rmtree(sources_dir)
        manifests = generate_fleet(sources_dir, cfg)
        print(f"{len(manifests)} database(s) ready under {sources_dir.relative_to(ROOT)}")
        return 0
    if not args.once:
        return serve_forever(cfg, args)

    # --once: run immediately, dashboard live for the duration
    server = None
    live = None
    if not args.no_live:
        live = LiveRun(cfg)
        server = DashboardServer(cfg, render_live_page())
        server.attach(live)
        try:
            url = server.serve(args.live_port, open_browser=not args.no_browser)
            print(f"Live dashboard: {url}")
        except OSError as e:
            print(f"  (live dashboard disabled: {e})")
            server, live = None, None
    execute_run(cfg, live)
    if server:
        time.sleep(3)  # let an open dashboard poll the final done-state once
        server.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
