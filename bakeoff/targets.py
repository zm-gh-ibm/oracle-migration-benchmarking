"""Migration targets: Postgres (local, default), DuckDB (zero-install), and
Snowflake (trial creds via .env).

A target executes the contestant's migrated/schema.sql + migrated/load.sql and
answers validation queries. All expose the same small interface.
"""
import os
import re
import time
from pathlib import Path


_DOLLAR_TAG = re.compile(r"\$[A-Za-z_][A-Za-z_0-9]*\$|\$\$")


def split_statements(sql_text):
    """Split a SQL script into statements. Strips -- comments; splits on ';'.

    Quote-aware ('…', "…") and dollar-quote-aware ($$…$$, $tag$…$tag$), so
    PL/pgSQL function bodies with internal semicolons survive intact —
    agents legitimately translate Oracle PL/SQL into Postgres functions.
    """
    out, buf, in_str = [], [], None
    text = re.sub(r"^\s*--.*$", "", sql_text, flags=re.MULTILINE)
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if in_str:
            buf.append(ch)
            if ch == in_str:
                in_str = None
            i += 1
        elif ch == "$":
            mm = _DOLLAR_TAG.match(text, i)
            if mm:  # copy the whole $tag$ … $tag$ body verbatim
                tag = mm.group(0)
                end = text.find(tag, mm.end())
                stop = (end + len(tag)) if end != -1 else n
                buf.append(text[i:stop])
                i = stop
            else:
                buf.append(ch)
                i += 1
        elif ch in ("'", '"'):
            in_str = ch
            buf.append(ch)
            i += 1
        elif ch == ";":
            stmt = "".join(buf).strip()
            if stmt:
                out.append(stmt)
            buf = []
            i += 1
        else:
            buf.append(ch)
            i += 1
    tail = "".join(buf).strip()
    if tail:
        out.append(tail)
    return out


class PostgresTarget:
    name = "postgres"
    dialect = "PostgreSQL"
    numeric_cast = "NUMERIC"   # ROUND(x, 2) needs NUMERIC in Postgres
    executor_desc = ("the psycopg client, one statement at a time, in autocommit "
                     "mode. Relative CSV paths like 'source/data/x.csv' inside "
                     "COPY statements are resolved against the migration folder")

    def __init__(self, workspace, cfg=None, schema=None):
        import psycopg
        cfg = cfg or {}
        self.workspace = Path(workspace)
        self._schema_override = schema
        dsn = (cfg.get("dsn") or os.environ.get("BAKEOFF_PG_DSN")
               or "postgresql:///bakeoff")
        try:
            self.conn = psycopg.connect(dsn, autocommit=True)
        except psycopg.OperationalError as e:
            if "does not exist" not in str(e):
                raise
            # first run: create the database, then reconnect
            info = psycopg.conninfo.conninfo_to_dict(dsn)
            dbname = info.get("dbname") or "bakeoff"
            admin = psycopg.connect(
                psycopg.conninfo.make_conninfo(dsn, dbname="postgres"),
                autocommit=True)
            try:
                admin.execute(f'CREATE DATABASE "{dbname}"')
            except psycopg.errors.DuplicateDatabase:
                pass  # a parallel job won the race
            finally:
                admin.close()
            self.conn = psycopg.connect(dsn, autocommit=True)
        # one schema per (contestant, db) workspace so parallel jobs can't collide
        self.schema = self._schema_override or "s_" + re.sub(
            r"\W", "_", f"{self.workspace.parent.name}_{self.workspace.name}").lower()
        self.reset_schema()

    def reset_schema(self):
        cur = self.conn.cursor()
        cur.execute(f'DROP SCHEMA IF EXISTS "{self.schema}" CASCADE')
        cur.execute(f'CREATE SCHEMA "{self.schema}"')
        cur.execute(f'SET search_path TO "{self.schema}"')

    # COPY <table+cols> FROM '<file>' [WITH (...)]
    _COPY_RE = re.compile(r"(?is)^\s*COPY\s+(.+?)\s+FROM\s+'([^']+)'\s*(.*)$")
    BATCH_ROWS = 10            # rows streamed per batch when reporting progress
    load_progress = None       # callable(table, rows_done, rows_total)
    load_pacing_s = 0.0        # delay between batches (demo visibility)

    def _exec(self, cur, stmt):
        """Run one statement; file-based COPY becomes client-side COPY FROM
        STDIN so the Postgres *server* never needs read access to the
        workspace (macOS TCC blocks the launchd service from ~/Documents).
        With load_progress set, the file is streamed in small row batches and
        progress is reported after each one."""
        mm = self._COPY_RE.match(stmt)
        if not mm:
            cur.execute(stmt)
            return
        head, path, rest = mm.groups()
        with open(path, "rb") as f:
            data = f.read()
        if self.load_progress is None:
            with cur.copy(f"COPY {head} FROM STDIN {rest}") as cp:
                cp.write(data)
            return
        table = head.split()[0].strip('"')
        lines = data.splitlines(keepends=True)
        has_header = re.search(r"(?i)\bheader\b", rest) is not None
        total = max(0, len(lines) - (1 if has_header else 0))
        done = 0
        with cur.copy(f"COPY {head} FROM STDIN {rest}") as cp:
            for i in range(0, len(lines), self.BATCH_ROWS):
                batch = lines[i:i + self.BATCH_ROWS]
                cp.write(b"".join(batch))
                done += len(batch) - (1 if has_header and i == 0 else 0)
                try:
                    self.load_progress(table, min(done, total), total)
                except Exception:
                    pass
                if self.load_pacing_s and i + self.BATCH_ROWS < len(lines):
                    time.sleep(self.load_pacing_s)

    def run_script(self, sql_text, on_stmt=None):
        # contestants reference CSVs relative to the workspace; rewrite to
        # absolute (same trick as DuckDB) so we can open them client-side.
        # Tolerate ./ and ../ prefixes — agents writing from migrated/ think
        # of the CSVs as '../source/data/...' (bob does exactly this).
        abs_prefix = self.workspace.as_posix() + "/source/data/"
        sql_text = re.sub(r"(?<![\w/])(?:\.\./|\./)*source/data/", abs_prefix, sql_text)
        errors = []
        cur = self.conn.cursor()
        for stmt in split_statements(sql_text):
            try:
                self._exec(cur, stmt)
                if on_stmt:
                    on_stmt(stmt)
            except Exception as e:
                errors.append({"statement": stmt[:200], "error": str(e).split("\n")[0][:300]})
        return errors

    def scalar(self, sql):
        row = self.conn.cursor().execute(sql).fetchone()
        return row[0] if row else None

    def table_names(self):
        rows = self.conn.cursor().execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = %s", (self.schema,)).fetchall()
        return {r[0].lower() for r in rows}

    def column_count(self, table):
        row = self.conn.cursor().execute(
            "SELECT COUNT(*) FROM information_schema.columns "
            "WHERE table_schema = %s AND lower(table_name) = %s",
            (self.schema, table.lower())).fetchone()
        return row[0] if row else None

    def close(self):
        self.conn.close()


class SnowflakeTarget:
    name = "snowflake"
    dialect = "Snowflake SQL"
    numeric_cast = "DOUBLE"
    executor_desc = ("the Snowflake Python connector, one statement at a time. "
                     "CSVs are pre-uploaded to a stage named @bakeoff_stage with "
                     "the same file names; load with COPY INTO ... FROM "
                     "@bakeoff_stage/<table>.csv")

    def __init__(self, workspace, cfg=None):
        import snowflake.connector  # pip install snowflake-connector-python
        cfg = cfg or {}
        self.workspace = Path(workspace)
        self.conn = snowflake.connector.connect(
            account=os.environ["SNOWFLAKE_ACCOUNT"],
            user=os.environ["SNOWFLAKE_USER"],
            password=os.environ["SNOWFLAKE_PASSWORD"],
            warehouse=cfg.get("warehouse", os.environ.get("SNOWFLAKE_WAREHOUSE", "COMPUTE_WH")),
            database=cfg.get("database", os.environ.get("SNOWFLAKE_DATABASE", "BAKEOFF")),
        )
        cur = self.conn.cursor()
        self.schema = "S_" + re.sub(r"\W", "_", self.workspace.name).upper()
        cur.execute(f"CREATE OR REPLACE SCHEMA {self.schema}")
        cur.execute(f"USE SCHEMA {self.schema}")
        cur.execute("CREATE OR REPLACE STAGE bakeoff_stage")
        for csv_file in sorted((self.workspace / "source" / "data").glob("*.csv")):
            cur.execute(f"PUT file://{csv_file} @bakeoff_stage AUTO_COMPRESS=FALSE")

    def run_script(self, sql_text, on_stmt=None):
        errors = []
        cur = self.conn.cursor()
        for stmt in split_statements(sql_text):
            try:
                cur.execute(stmt)
                if on_stmt:
                    on_stmt(stmt)
            except Exception as e:
                errors.append({"statement": stmt[:200], "error": str(e).split("\n")[0][:300]})
        return errors

    def scalar(self, sql):
        row = self.conn.cursor().execute(sql).fetchone()
        return row[0] if row else None

    def table_names(self):
        rows = self.conn.cursor().execute(
            f"SELECT table_name FROM information_schema.tables "
            f"WHERE table_schema = '{self.schema}'").fetchall()
        return {r[0].lower() for r in rows}

    def column_count(self, table):
        return self.scalar(
            f"SELECT COUNT(*) FROM information_schema.columns "
            f"WHERE table_schema='{self.schema}' AND lower(table_name)='{table.lower()}'")

    def close(self):
        self.conn.close()


TARGETS = {"postgres": PostgresTarget, "snowflake": SnowflakeTarget}
