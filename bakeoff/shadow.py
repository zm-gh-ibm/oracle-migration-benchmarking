"""Shadow execution: run the agent's SQL against Postgres WHILE the agent works.

Without this, data only moves during end-of-run validation — the dashboard
sits at zero for the minutes an agent spends thinking, then every row lands at
once. The ShadowExecutor tails migrated/schema.sql + migrated/load.sql as the
agent writes them and applies each new complete statement to a scratch schema
(shadow_<contestant>_<db>) the moment it appears. The flow visualizer therefore
shows CREATE TABLEs, COPYs, and rows streaming in real time, exactly when the
agent produces the SQL — including its mistakes (failed statements are shown,
and if the agent rewrites earlier SQL the shadow schema resets and replays).

Final scoring is untouched: validate_workspace still does a clean replay into
the s_* schema afterward.
"""
import re
import threading
from pathlib import Path

from .targets import PostgresTarget, split_statements

_DDL_TABLE_RE = re.compile(r'(?is)^\s*CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?"?(\w+)"?')
_LOAD_TABLE_RE = re.compile(r'(?is)^\s*(?:COPY|INSERT\s+INTO)\s+"?(\w+)"?')


def _kind(stmt):
    s = stmt.lstrip().upper()
    if s.startswith(("COPY", "INSERT")):
        return "LOAD"
    if s.startswith(("CREATE", "ALTER", "DROP", "COMMENT")):
        return "DDL"
    return "SQL"


def _summary(stmt, cap=150):
    return re.sub(r"\s+", " ", stmt.strip())[:cap]


class ShadowExecutor(threading.Thread):
    """Tails the agent's SQL files and mirrors them into a live scratch schema.

    Callbacks (all optional, called from this thread):
      on_sql(kind, ok, text, error)           -- every statement applied
      on_table(table, rows, total, final)     -- rows landing per table
      on_ddl(table, cols)                     -- table created (col count)
    """

    def __init__(self, workspace, target_cfg, manifest, on_sql=None,
                 on_table=None, on_ddl=None, poll_s=0.5, pacing_s=0.0):
        super().__init__(daemon=True)
        self.workspace = Path(workspace)
        self.target_cfg = target_cfg
        self.tinfo = {t["name"].lower(): t for t in manifest["tables"]}
        self.on_sql = on_sql or (lambda *a: None)
        self.on_table = on_table or (lambda *a: None)
        self.on_ddl = on_ddl or (lambda *a: None)
        self.poll_s = poll_s
        self.pacing_s = pacing_s
        self.did_stream = False
        self._halt = threading.Event()
        self._applied = {"schema.sql": [], "load.sql": []}
        self.target = None

    # ---- lifecycle ----
    def run(self):
        schema = "shadow_" + re.sub(
            r"\W", "_",
            f"{self.workspace.parent.name}_{self.workspace.name}").lower()
        try:
            self.target = PostgresTarget(self.workspace, self.target_cfg,
                                         schema=schema)
        except Exception as e:
            self.on_sql("SQL", False, "shadow executor unavailable",
                        str(e).split("\n")[0][:150])
            return
        self.target.load_pacing_s = self.pacing_s
        self.target.load_progress = self._progress
        try:
            while not self._halt.wait(self.poll_s):
                self._scan()
            self._scan()  # final catch-up so fast agents still stream
        finally:
            try:
                self.target.close()
            except Exception:
                pass

    def stop(self, timeout=60):
        self._halt.set()
        self.join(timeout=timeout)

    # ---- tail + apply ----
    def _statements(self, fname):
        path = self.workspace / "migrated" / fname
        if not path.exists():
            return []
        try:
            text = path.read_text()
        except Exception:
            return []
        stmts = split_statements(text)
        # the last statement may still be mid-write if the file doesn't end
        # with a terminator yet
        if stmts and not text.rstrip().endswith(";"):
            stmts = stmts[:-1]
        return stmts

    def _scan(self):
        for fname in ("schema.sql", "load.sql"):
            stmts = self._statements(fname)
            prev = self._applied[fname]
            if stmts[:len(prev)] != prev:
                # agent rewrote earlier SQL -> reset the scratch schema, replay
                self.on_sql("SQL", True,
                            "-- agent revised its SQL; shadow schema reset, replaying",
                            None)
                try:
                    self.target.reset_schema()
                except Exception as e:
                    self.on_sql("SQL", False, "shadow schema reset failed",
                                str(e).split("\n")[0][:150])
                    return
                self._applied = {"schema.sql": [], "load.sql": []}
                self._scan()
                return
            for stmt in stmts[len(prev):]:
                self._apply(stmt)
                prev.append(stmt)

    def _apply(self, stmt):
        kind = _kind(stmt)
        errors = self.target.run_script(stmt + ";")
        ok = not errors
        err = errors[0]["error"] if errors else None
        self.on_sql(kind, ok, _summary(stmt), err)
        if not ok:
            return
        if kind == "DDL":
            mm = _DDL_TABLE_RE.match(stmt)
            if mm and mm.group(1).lower() in self.tinfo:
                try:
                    cols = self.target.column_count(mm.group(1))
                except Exception:
                    cols = None
                self.on_ddl(mm.group(1), cols)
        elif kind == "LOAD":
            mm = _LOAD_TABLE_RE.match(stmt)
            if mm and mm.group(1).lower() in self.tinfo:
                t = self.tinfo[mm.group(1).lower()]
                try:
                    n = self.target.scalar(
                        f'SELECT COUNT(*) FROM "{t["name"]}"') or 0
                except Exception:
                    n = 0
                self.did_stream = self.did_stream or n > 0
                self.on_table(t["name"], n, t["row_count"], True)

    def _progress(self, table, done, total):
        """Batch-level progress from the target's streaming COPY."""
        if table.lower() in self.tinfo:
            self.did_stream = True
            t = self.tinfo[table.lower()]
            self.on_table(t["name"], done, t["row_count"], False)
