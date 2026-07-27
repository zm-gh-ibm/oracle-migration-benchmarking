"""Real-time run state + local HTTP server for the live dashboard.

LiveRun holds one run's state: job statuses, per-agent events (tool calls,
output lines, data movement, QA issues), and completed results.

DashboardServer serves the dashboard page at /, the current state at
/live.json, and — when constructed with an on_run callback — accepts
POST /run so the "Run bakeoff" button in the page can start a fresh run
without touching the terminal. The page polls once a second.
"""
import json
import threading
import time
import webbrowser
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MAX_EVENTS = 4000          # ring-buffer cap so huge runs don't bloat the page
EVENT_TEXT_CAP = 220


class LiveRun:
    def __init__(self, cfg):
        self.cfg = cfg
        self.started = time.monotonic()
        self.lock = threading.Lock()
        self.jobs = {}          # (contestant, db) -> {"status": ..., "success": ...}
        self.events = []
        self.results = []
        self.done = False
        self._seq = 0

    # ---- mutation (called from worker threads) ----
    def add_job(self, contestant, db, rows_expected=None, tables=None):
        """tables: [{"name", "total", "fields"}] — known upfront from the
        manifest so the dashboard can show every table as pending, then fill
        each one in as its rows stream across."""
        with self.lock:
            self.jobs[(contestant, db)] = {
                "contestant": contestant, "db": db,
                "status": "pending", "phase": None, "success": None,
                "rows_expected": rows_expected, "rows_moved": 0,
                "tables": [{"name": t["name"], "total": t["total"],
                            "fields": list(t.get("fields", [])),
                            "rows": 0, "cols": None, "status": "pending"}
                           for t in (tables or [])],
                "sql": [], "sql_ok": 0, "sql_err": 0}

    def job_status(self, contestant, db, status):
        with self.lock:
            self.jobs[(contestant, db)]["status"] = status

    def job_phase(self, contestant, db, phase):
        with self.lock:
            self.jobs[(contestant, db)]["phase"] = phase

    def job_plan(self, contestant, db, lines, excerpt, text=None):
        """Attach the phase-1 plan document (count, heading excerpt, and full
        text) so the dashboard can show — and expand — what phase 2 executes."""
        with self.lock:
            self.jobs[(contestant, db)]["plan_doc"] = {
                "lines": lines, "excerpt": [str(x)[:120] for x in excerpt[:12]],
                "text": text}

    def job_rows(self, contestant, db, rows_moved):
        with self.lock:
            self.jobs[(contestant, db)]["rows_moved"] = rows_moved

    def job_table(self, contestant, db, table, rows, status, cols=None):
        """Update one table's transfer progress:
        status pending|created|streaming|loaded."""
        with self.lock:
            for t in self.jobs[(contestant, db)]["tables"]:
                if t["name"].lower() == table.lower():
                    t["rows"] = rows
                    t["status"] = status
                    if cols is not None:
                        t["cols"] = cols
                    break

    def job_sql(self, contestant, db, kind, ok, text, error=None):
        """Append one executed statement to the job's live SQL console."""
        with self.lock:
            job = self.jobs[(contestant, db)]
            job["sql"].append({"kind": kind, "ok": ok, "text": str(text)[:160],
                               "error": (str(error)[:160] if error else None)})
            del job["sql"][:-30]
            job["sql_ok" if ok else "sql_err"] += 1

    def job_done(self, result):
        with self.lock:
            job = self.jobs[(result["contestant"], result["db"])]
            job["status"] = "done"
            job["success"] = result["validation"]["success"]
            job["rows_moved"] = result["validation"].get("rows_loaded") or 0
            self.results.append(result)

    def event(self, contestant, db, kind, text):
        with self.lock:
            self._seq += 1
            self.events.append({
                "n": self._seq,
                "t": round(time.monotonic() - self.started, 1),
                "contestant": contestant, "db": db, "kind": kind,
                "text": str(text)[:EVENT_TEXT_CAP]})
            if len(self.events) > MAX_EVENTS:
                del self.events[:len(self.events) - MAX_EVENTS]

    def finish(self):
        with self.lock:
            self.done = True

    # ---- snapshot for /live.json ----
    def snapshot(self):
        from .report import summarize
        with self.lock:
            results = list(self.results)
            payload = {
                "live": True,
                "done": self.done,
                "elapsed_s": round(time.monotonic() - self.started, 1),
                "run": self.cfg["run"],
                "input": self.cfg["input"],
                "quality_exempt": self.cfg["output"].get("quality_exempt", {}),
                "jobs": list(self.jobs.values()),
                "events": list(self.events),
                "results": results,
            }
        payload["summary"] = summarize(results) if results else {}
        return payload


class DashboardServer:
    """Serves the dashboard and, optionally, starts runs on POST /run."""

    def __init__(self, cfg, page_html, on_run=None, initial=None):
        self.cfg = cfg
        self.page_html = page_html
        self.on_run = on_run      # () -> bool: True if a run was started
        self.current = None       # the active/most recent LiveRun
        self.initial = initial    # last run's results.json payload, shown while idle
        self._server = None

    def attach(self, live_run):
        self.current = live_run

    def snapshot(self):
        if self.current is not None:
            payload = self.current.snapshot()
            payload["idle"] = False
        elif self.initial is not None:
            # no run yet this session — show the previous run's results
            payload = {**self.initial, "live": True, "idle": False,
                       "done": True, "elapsed_s": 0, "previous_run": True}
        else:
            payload = {"live": True, "idle": True, "done": False, "elapsed_s": 0,
                       "run": self.cfg["run"], "input": self.cfg["input"],
                       "jobs": [], "events": [], "results": [], "summary": {}}
        payload["can_run"] = self.on_run is not None
        return payload

    def metrics(self):
        """Prometheus exposition format — scrape /metrics with any collector."""
        snap = self.snapshot()
        L = ["# HELP bakeoff_rows_moved Rows loaded into the target per job",
             "# TYPE bakeoff_rows_moved gauge"]

        def lab(j):
            return f'contestant="{j["contestant"]}",db="{j["db"]}"'

        for j in snap["jobs"]:
            L.append(f'bakeoff_rows_moved{{{lab(j)}}} {j.get("rows_moved") or 0}')
        L += ["# TYPE bakeoff_rows_expected gauge"]
        for j in snap["jobs"]:
            L.append(f'bakeoff_rows_expected{{{lab(j)}}} {j.get("rows_expected") or 0}')
        L += ["# HELP bakeoff_sql_statements_total Statements shadow-executed",
              "# TYPE bakeoff_sql_statements_total counter"]
        for j in snap["jobs"]:
            L.append(f'bakeoff_sql_statements_total{{{lab(j)},result="ok"}} '
                     f'{j.get("sql_ok", 0)}')
            L.append(f'bakeoff_sql_statements_total{{{lab(j)},result="error"}} '
                     f'{j.get("sql_err", 0)}')
        L += ["# TYPE bakeoff_jobs gauge"]
        for status in ("pending", "running", "done"):
            n = sum(1 for j in snap["jobs"] if j["status"] == status)
            L.append(f'bakeoff_jobs{{status="{status}"}} {n}')
        L += ["# TYPE bakeoff_cost_usd gauge"]
        for cname, s in (snap.get("summary") or {}).items():
            if s.get("cost_usd") is not None:
                L.append(f'bakeoff_cost_usd{{contestant="{cname}"}} {s["cost_usd"]}')
        L += ["# TYPE bakeoff_run_done gauge",
              f'bakeoff_run_done {1 if snap.get("done") else 0}',
              "# TYPE bakeoff_elapsed_seconds gauge",
              f'bakeoff_elapsed_seconds {snap.get("elapsed_s", 0)}']
        return "\n".join(L) + "\n"

    def _history_entries(self):
        """Return past runs from runs/<name>/history/, newest first."""
        run_name = self.cfg["run"]["name"]
        hist_root = Path(__file__).resolve().parent.parent / "runs" / run_name / "history"
        entries = []
        if not hist_root.exists():
            return entries
        for ts_dir in sorted(hist_root.iterdir(), reverse=True):
            rj = ts_dir / "results.json"
            if not rj.exists():
                continue
            try:
                data = json.loads(rj.read_text())
                summary = data.get("summary", {})
                contestants = [c for c in summary if c != "baseline"]
                label = ts_dir.name  # e.g. 20260725-163059
                stats = " · ".join(
                    f"{c} {summary[c].get('fully_successful',0)}/{summary[c].get('databases',0)}"
                    for c in contestants if c in summary
                )
                entries.append({"ts": label, "label": f"{label}  {stats}",
                                 "path": str(rj)})
            except Exception:
                continue
        return entries

    def serve(self, port, open_browser=True):
        srv = self

        class Handler(BaseHTTPRequestHandler):
            def _send(self, code, body, ctype):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                route = self.path.split("?")[0]
                if route == "/live.json":
                    self._send(200, json.dumps(srv.snapshot(), default=str).encode(),
                               "application/json")
                elif route == "/metrics":
                    self._send(200, srv.metrics().encode(),
                               "text/plain; version=0.0.4; charset=utf-8")
                elif route == "/history.json":
                    self._send(200, json.dumps(srv._history_entries()).encode(),
                               "application/json")
                elif route.startswith("/history/"):
                    # serve a specific past results.json by timestamp
                    ts = route.split("/history/")[1].split("?")[0]
                    run_name = srv.cfg["run"]["name"]
                    rj = (Path(__file__).resolve().parent.parent /
                          "runs" / run_name / "history" / ts / "results.json")
                    if rj.exists():
                        self._send(200, rj.read_bytes(), "application/json")
                    else:
                        self._send(404, b'{"error":"not found"}', "application/json")
                else:
                    self._send(200, srv.page_html.encode(), "text/html; charset=utf-8")

            def do_POST(self):
                if self.path.split("?")[0] != "/run":
                    self._send(404, b'{"error": "not found"}', "application/json")
                elif srv.on_run is None:
                    self._send(405, b'{"error": "server not in --serve mode"}',
                               "application/json")
                elif srv.on_run():
                    self._send(202, b'{"started": true}', "application/json")
                else:
                    self._send(409, b'{"error": "a run is already in progress"}',
                               "application/json")

            def log_message(self, *args):
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{self._server.server_address[1]}/"
        if open_browser:
            threading.Thread(target=webbrowser.open, args=(url,), daemon=True).start()
        return url

    def shutdown(self):
        if self._server:
            self._server.shutdown()
