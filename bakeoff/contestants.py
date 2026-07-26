"""Contestant adapters.

Each contestant gets an identical isolated workspace (source DDL + CSVs + the
same MIGRATION_TASK.md) and must produce migrated/schema.sql, migrated/load.sql
and migrated/notes.md. Adapters return a uniform metrics dict.

  baseline -- deterministic rule-based converter (free; pipeline smoke test and
              the benchmark floor an agent must beat)
  claude   -- Claude Code headless: claude -p ... --output-format json
  bob      -- IBM Bob Shell one-shot: bob "..." --chat-mode code -o json
"""
import json
import os
import re
import subprocess
import threading
import time
from pathlib import Path

AGENT_PROMPT = ("Read MIGRATION_TASK.md in the current directory and complete "
                "the migration task exactly as specified.")

PLAN_PROMPT = ("Read PLANNING_TASK.md in the current directory and complete "
               "the planning task exactly as specified.")

PLANNING_TEMPLATE = """# Phase 1: Architectural migration plan — Oracle → {target_title}

Before any migration code is written, your team must produce an architectural
migration plan for this database.

This directory holds an export of ONE Oracle 19c database:

- `source/schema.sql` — Oracle DDL: tables, constraints, sequences, triggers, PL/SQL
- `source/data/*.csv` — one CSV per table (header row = column names)

## Deliverable (write exactly one file)

`plan/MIGRATION_PLAN.md` — the plan phase 2 will execute. It must cover:

1. **Inventory** — every table, constraint, sequence, trigger, and PL/SQL
   object in the source, with row counts from the CSVs
2. **Type mapping** — each Oracle type in use → its {dialect} equivalent,
   with rationale
3. **Schema strategy** — constraint handling and creation order (respect FK
   dependencies)
4. **Sequence / trigger / PL/SQL strategy** — native {dialect} equivalent
   where one exists, or a documented drop with recommended replacement
5. **Data load strategy** — how each CSV is loaded, date format and
   empty-string/NULL handling
6. **Validation strategy** — how you would verify tables, row counts, and
   numeric integrity after migration
7. **Risks & assumptions**

## Rules

- Do NOT write anything into `migrated/` in this phase — planning only.
- Do not modify anything under `source/`.
"""


def write_planning_task(workspace, target):
    (Path(workspace) / "PLANNING_TASK.md").write_text(PLANNING_TEMPLATE.format(
        target_title=target.name.title(), dialect=target.dialect))

TASK_TEMPLATE = """# Task: migrate this Oracle database to {target_title}

This directory holds an export of ONE Oracle 19c database:

- `source/schema.sql` — Oracle DDL: tables, constraints, sequences, triggers, PL/SQL
- `source/data/*.csv` — one CSV per table (header row = column names; empty string = NULL{date_note})

## Deliverables (write into `migrated/`)

1. `migrated/schema.sql` — equivalent DDL in **{dialect}**. Convert Oracle data
   types appropriately. Preserve table and column names and any constraints the
   target supports. Do NOT include PL/SQL, triggers, or Oracle-only syntax.
2. `migrated/load.sql` — **{dialect}** statements that load every CSV into its
   table. {load_note} Convert date formats and treat empty strings as NULL.
3. `migrated/notes.md` — anything that could not be translated directly
   (sequences, triggers, procedures): what it did and the recommended
   target-side equivalent. Note any assumptions.

## Rules

- Do not modify anything under `source/`.
- Both SQL files are executed non-interactively by {executor}.
  Statements are separated by semicolons; the files must run without errors.
- Migration is graded on: every table created with the right columns, every row
  loaded, and numeric column sums matching the source data exactly.
"""


PLAN_SECTION = """
## Your phase-1 plan

Your team already produced an architectural migration plan in phase 1:
`plan/MIGRATION_PLAN.md`. Read it FIRST and execute the migration according to
that plan. If you must deviate from it, record each deviation and the reason
in `migrated/notes.md`.
"""


def write_task_file(workspace, target, with_plan=False):
    date_note = ("; dates use Oracle's DD-MON-YYYY format"
                 if _workspace_uses_oracle_dates(workspace) else "")
    if target.name == "postgres":
        load_note = ("Load with COPY, referencing CSVs by relative path, e.g. "
                     "`COPY employees FROM 'source/data/employees.csv' "
                     "WITH (FORMAT csv, HEADER true, NULL '');`.")
    else:
        load_note = ("CSVs are pre-uploaded to stage `@bakeoff_stage`; load with "
                     "`COPY INTO <table> FROM @bakeoff_stage/<table>.csv ...`.")
    text = TASK_TEMPLATE.format(
        target_title=target.name.title(),
        dialect=target.dialect,
        executor=target.executor_desc,
        load_note=load_note,
        date_note=date_note,
    )
    if with_plan:
        head, sep, tail = text.partition("## Deliverables")
        text = head + PLAN_SECTION + "\n" + sep + tail
    (Path(workspace) / "MIGRATION_TASK.md").write_text(text)


def _workspace_uses_oracle_dates(workspace):
    for csv_file in (Path(workspace) / "source" / "data").glob("*.csv"):
        text = csv_file.read_text()
        if re.search(r"\d{2}-[A-Z]{3}-\d{4}", text):
            return True
    return False


def _base_metrics(name):
    return {"contestant": name, "exit_code": None, "wall_time_s": None,
            "cost_usd": None, "tokens_in": None, "tokens_out": None,
            "turns": None, "error": None}


def _find_key(obj, keys):
    """Recursive best-effort search for the first matching key in nested JSON."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k.lower() in keys and isinstance(v, (int, float)):
                return v
        for v in obj.values():
            found = _find_key(v, keys)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = _find_key(v, keys)
            if found is not None:
                return found
    return None


def _parse_json_tail(out):
    """Parse agent stdout as JSON; bob prefixes the JSON with text blocks."""
    try:
        return json.loads(out)
    except (json.JSONDecodeError, TypeError):
        pass
    idx = out.rfind("\n{")
    if idx != -1:
        try:
            return json.loads(out[idx:])
        except json.JSONDecodeError:
            pass
    return None


def _run_subprocess(cmd, workspace, timeout, raw_path, on_line=None):
    """Run cmd, streaming stdout line-by-line to on_line as it arrives."""
    env = {k: v for k, v in os.environ.items()
           if k not in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")}
    start = time.monotonic()
    out_lines, err_lines = [], []
    proc = subprocess.Popen(
        cmd, cwd=str(workspace), env=env, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def _pump(stream, sink, cb):
        for line in stream:
            sink.append(line)
            if cb:
                try:
                    cb(line.rstrip("\n"))
                except Exception:
                    pass

    readers = [threading.Thread(target=_pump, args=(proc.stdout, out_lines, on_line), daemon=True),
               threading.Thread(target=_pump, args=(proc.stderr, err_lines, None), daemon=True)]
    for t in readers:
        t.start()
    try:
        exit_code = proc.wait(timeout=timeout)
        err_extra = ""
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        exit_code = -1
        err_extra = f"TIMEOUT after {timeout}s"
    for t in readers:
        t.join(timeout=5)
    out, err = "".join(out_lines), err_extra or "".join(err_lines)
    wall = round(time.monotonic() - start, 1)
    if raw_path:
        Path(raw_path).write_text(
            f"$ {' '.join(cmd)}\n\n--- stdout ---\n{out}\n--- stderr ---\n{err}\n")
    return exit_code, out, err, wall


def _summarize_tool_use(name, tool_input):
    """One human-readable line for a tool_use block."""
    if not isinstance(tool_input, dict):
        return name
    for key in ("command", "file_path", "path", "pattern", "description"):
        if tool_input.get(key):
            return f"{name}: {tool_input[key]}"
    return name


def run_claude(workspace, ccfg, timeout, raw_path=None, on_event=None,
               prompt=AGENT_PROMPT):
    """Claude Code headless with stream-json: every assistant message arrives as
    a JSONL line while the agent runs, so tool calls surface in real time."""
    m = _base_metrics("claude")
    cmd = [ccfg.get("cmd", "claude"), "-p", prompt,
           "--output-format", "stream-json", "--verbose",
           "--permission-mode", "acceptEdits",
           "--max-turns", str(ccfg.get("max_turns", 50)),
           "--allowedTools",
           "Bash(ls:*),Bash(cat:*),Bash(head:*),Bash(tail:*),Bash(wc:*)"]
    cmd += ["--model", ccfg.get("model") or "opus"]

    final = {}

    def on_line(line):
        line = line.strip()
        if not line.startswith("{"):
            return
        try:
            evt = json.loads(line)
        except json.JSONDecodeError:
            return
        etype = evt.get("type")
        if etype == "assistant":
            for block in (evt.get("message") or {}).get("content", []):
                if block.get("type") == "tool_use":
                    if on_event:
                        on_event("tool", _summarize_tool_use(
                            block.get("name", "?"), block.get("input")))
                elif block.get("type") == "text" and block.get("text", "").strip():
                    if on_event:
                        on_event("text", block["text"].strip())
        elif etype == "result":
            final.update(evt)

    exit_code, out, err, wall = _run_subprocess(cmd, workspace, timeout, raw_path, on_line)
    m["exit_code"], m["wall_time_s"] = exit_code, wall
    if not final:  # e.g. older CLI emitting a single JSON object
        try:
            final = json.loads(out)
        except (json.JSONDecodeError, TypeError):
            final = {}
    if final:
        m["cost_usd"] = final.get("total_cost_usd")
        m["turns"] = final.get("num_turns")
        usage = final.get("usage", {})
        m["tokens_in"] = usage.get("input_tokens")
        m["tokens_out"] = usage.get("output_tokens")
        if final.get("is_error"):
            m["error"] = str(final.get("result"))[:300]
    elif exit_code != 0:
        m["error"] = (err or out)[:300]
    return m


def run_bob(workspace, bcfg, timeout, raw_path=None, on_event=None,
            prompt=AGENT_PROMPT):
    """Bob prints progress text then a stats JSON tail; stream the text lines
    as activity events and parse the tail for metrics as before."""
    m = _base_metrics("bob")
    cmd = [bcfg.get("cmd", "bob"), prompt,
           "--chat-mode", bcfg.get("chat_mode", "code"),
           "--approval-mode", "auto_edit",
           "-o", "json",
           "--accept-license"]
    if bcfg.get("model"):
        cmd += ["--model", bcfg["model"]]
    if bcfg.get("max_coins"):
        cmd += ["--max-coins", str(bcfg["max_coins"])]

    def on_line(line):
        line = line.strip()
        if not line or not on_event:
            return
        if line.startswith(("{", "}", '"')) or line == "---output---":
            return  # stats JSON tail / separators, not activity
        kind = "tool" if re.match(
            r"(?i)^(reading|writing|editing|running|executing|creating|✦|✓|→)", line) else "text"
        on_event(kind, line)

    exit_code, out, err, wall = _run_subprocess(cmd, workspace, timeout, raw_path, on_line)
    m["exit_code"], m["wall_time_s"] = exit_code, wall
    data = _parse_json_tail(out)
    if data is not None:
        m["tokens_in"] = _find_key(data, {"input_tokens", "prompt_tokens", "prompt"})
        m["tokens_out"] = _find_key(data, {"output_tokens", "completion_tokens", "candidates"})
        m["turns"] = _find_key(data, {"num_turns", "turns", "totalrequests"})
        # Bob prices in Bobcoins, never dollars. `sessionCost` is this run's coin
        # spend; `budgetSpend` is the cumulative total against `maxBudget` (it
        # increments by exactly sessionCost per run, which is how we know both
        # are denominated in coins).
        m["coins"] = _find_key(data, {"sessioncost", "coins", "coins_used", "total_coins"})
        m["budget_spend"] = _find_key(data, {"budgetspend"})
        m["max_budget"] = _find_key(data, {"maxbudget"})
        usd_per_coin = bcfg.get("usd_per_coin", 0.50)
        if m["coins"] is not None:
            m["cost_usd"] = round(m["coins"] * usd_per_coin, 4)
            m["cost_source"] = f"{m['coins']:.4f} coins × ${usd_per_coin:.2f}/coin"
        else:
            m["cost_usd"] = _find_key(data, {"total_cost_usd", "cost_usd", "cost"})
    elif exit_code != 0:
        m["error"] = (err or out)[:300]
    return m


# ---------------------------------------------------------------------------
# Baseline: deterministic Oracle -> target conversion, no LLM, zero cost.
# ---------------------------------------------------------------------------
def _convert_type(otype, target_name):
    t = otype.strip().upper()
    mm = re.match(r"VARCHAR2\((\d+)\)", t)
    if mm:
        return f"VARCHAR({mm.group(1)})"
    mm = re.match(r"NUMBER\((\d+),(\d+)\)", t)
    if mm:
        return f"DECIMAL({mm.group(1)},{mm.group(2)})"
    mm = re.match(r"NUMBER\((\d+)\)", t)
    if mm:
        return f"DECIMAL({mm.group(1)},0)"
    if t == "NUMBER":
        return "FLOAT"
    if t == "CLOB":
        return "VARCHAR"
    mm = re.match(r"CHAR\((\d+)\)", t)
    if mm:
        return f"VARCHAR({mm.group(1)})"
    return t  # DATE, TIMESTAMP pass through


def _parse_oracle_tables(schema_text):
    """-> [(table, [(col, oracle_type)], [constraint_lines])] in DDL order."""
    tables = []
    for mm in re.finditer(r"CREATE TABLE (\w+) \(\n(.*?)\n\);", schema_text, re.DOTALL):
        tname, body = mm.group(1), mm.group(2)
        cols, cons = [], []
        for line in body.splitlines():
            line = line.strip().rstrip(",")
            if not line:
                continue
            if line.upper().startswith("CONSTRAINT"):
                cons.append(line)
            else:
                parts = line.split(None, 1)
                cols.append((parts[0], parts[1]))
        tables.append((tname, cols, cons))
    return tables


def run_baseline(workspace, _cfg, _timeout, raw_path=None, target_name="postgres",
                 on_event=None):
    def emit(kind, text):
        if on_event:
            on_event(kind, text)
    m = _base_metrics("baseline")
    m["cost_usd"] = 0.0
    start = time.monotonic()
    workspace = Path(workspace)
    emit("tool", "Read: source/schema.sql")
    schema_text = (workspace / "source" / "schema.sql").read_text()
    tables = _parse_oracle_tables(schema_text)
    emit("text", f"parsed {len(tables)} table(s) from Oracle DDL")
    uses_oracle_dates = _workspace_uses_oracle_dates(workspace)

    ddl_parts, load_parts = [], []
    for tname, cols, cons in tables:
        col_defs = []
        for cname, rest in cols:
            not_null = " NOT NULL" if "NOT NULL" in rest.upper() else ""
            otype = re.sub(r"\s+NOT\s+NULL", "", rest, flags=re.IGNORECASE).strip()
            col_defs.append(f"  {cname} {_convert_type(otype, target_name)}{not_null}")
        col_defs += [f"  {c}" for c in cons]
        ddl_parts.append(f"CREATE TABLE {tname} (\n" + ",\n".join(col_defs) + "\n);")

        if target_name == "postgres":
            # Postgres parses DD-MON-YYYY dates natively; NULL '' maps empties
            load_parts.append(
                f"COPY {tname} FROM 'source/data/{tname}.csv' "
                f"WITH (FORMAT csv, HEADER true, NULL '');")
        else:  # snowflake
            datefmt = ", DATE_FORMAT='DD-MON-YYYY'" if uses_oracle_dates else ""
            load_parts.append(
                f"COPY INTO {tname} FROM @bakeoff_stage/{tname}.csv "
                f"FILE_FORMAT=(TYPE=CSV SKIP_HEADER=1 EMPTY_FIELD_AS_NULL=TRUE "
                f'FIELD_OPTIONALLY_ENCLOSED_BY=\'"\'{datefmt});')

    (workspace / "migrated").mkdir(exist_ok=True)
    emit("tool", "Write: migrated/schema.sql")
    (workspace / "migrated" / "schema.sql").write_text("\n\n".join(ddl_parts) + "\n")
    emit("tool", "Write: migrated/load.sql")
    (workspace / "migrated" / "load.sql").write_text("\n".join(load_parts) + "\n")
    dropped = len(re.findall(r"CREATE SEQUENCE|CREATE OR REPLACE TRIGGER|"
                             r"CREATE OR REPLACE PROCEDURE|CREATE OR REPLACE FUNCTION",
                             schema_text))
    emit("tool", "Write: migrated/notes.md")
    (workspace / "migrated" / "notes.md").write_text(
        "# Baseline (rule-based) migration notes\n\n"
        f"- Mechanical type mapping; {dropped} sequence/trigger/PLSQL objects dropped "
        "without replacement.\n"
        "- COMMENT ON statements not carried over.\n")
    m["exit_code"] = 0
    m["wall_time_s"] = round(time.monotonic() - start, 3)
    if raw_path:
        Path(raw_path).write_text("baseline converter: no agent output\n")
    return m


def run_baseline_plan(workspace, target_name="postgres", on_event=None):
    """Deterministic phase-1 plan: what a rule-based converter can honestly
    promise. Free and instant — the planning floor an agent must beat."""
    m = _base_metrics("baseline")
    m["cost_usd"] = 0.0
    start = time.monotonic()
    workspace = Path(workspace)
    schema_text = (workspace / "source" / "schema.sql").read_text()
    tables = _parse_oracle_tables(schema_text)
    n_seq = len(re.findall(r"CREATE SEQUENCE", schema_text))
    n_trg = len(re.findall(r"CREATE OR REPLACE TRIGGER", schema_text))
    n_pls = len(re.findall(r"CREATE OR REPLACE (?:PROCEDURE|FUNCTION)", schema_text))
    lines = [
        "# Migration plan (rule-based baseline)", "",
        "## 1. Inventory",
        f"- {len(tables)} tables: " + ", ".join(t for t, _c, _k in tables),
        f"- {n_seq} sequences, {n_trg} triggers, {n_pls} PL/SQL objects", "",
        "## 2. Type mapping",
        "- VARCHAR2(n)/CHAR(n) → VARCHAR(n); NUMBER(p,s) → DECIMAL(p,s); "
        "NUMBER → double; CLOB → TEXT; DATE/TIMESTAMP pass through", "",
        "## 3. Schema strategy",
        "- Recreate tables in source DDL order; keep NOT NULL and inline "
        "constraints the target supports", "",
        "## 4. Sequences / triggers / PL/SQL",
        "- Dropped without replacement (mechanical converter); documented in notes.md", "",
        "## 5. Data load",
        "- Bulk load each CSV per table; empty string → NULL; Oracle "
        "DD-MON-YYYY dates parsed on load", "",
        "## 6. Validation",
        "- Row counts per table vs CSV line counts; numeric column sums vs source", "",
        "## 7. Risks",
        "- No PK auto-increment behavior (sequences dropped); business logic in "
        "PL/SQL is lost",
    ]
    (workspace / "plan").mkdir(exist_ok=True)
    (workspace / "plan" / "MIGRATION_PLAN.md").write_text("\n".join(lines) + "\n")
    if on_event:
        on_event("tool", "Write: plan/MIGRATION_PLAN.md")
    m["exit_code"] = 0
    m["wall_time_s"] = round(time.monotonic() - start, 3)
    return m


def run_contestant(name, workspace, cfg, timeout, raw_path=None, target_name="postgres",
                   on_event=None, phase="migrate"):
    prompt = PLAN_PROMPT if phase == "plan" else AGENT_PROMPT
    if name == "baseline":
        if phase == "plan":
            return run_baseline_plan(workspace, target_name, on_event)
        return run_baseline(workspace, cfg, timeout, raw_path, target_name, on_event)
    if name == "claude":
        return run_claude(workspace, cfg.get("contestants", {}).get("claude", {}),
                          timeout, raw_path, on_event, prompt)
    if name == "bob":
        return run_bob(workspace, cfg.get("contestants", {}).get("bob", {}),
                       timeout, raw_path, on_event, prompt)
    raise ValueError(f"unknown contestant {name}")
