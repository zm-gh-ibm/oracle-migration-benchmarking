# Oracle → Postgres Migration Bakeoff

A harness that benchmarks agentic coding tools — **Claude Code**, **Cursor**,
and **IBM Bob** — on migrating Oracle databases to PostgreSQL, with a
real-time dashboard that shows every tool call, SQL statement, and row moving as
it happens. Designed to scale from a 2-database demo to fleets of hundreds or
thousands of Oracle databases.

Each contestant runs through two phases per database:

1. **Phase 1 — Plan.** Produce an architectural migration plan
   (`plan/MIGRATION_PLAN.md`): inventory, type mapping, schema/sequence/PL-SQL
   strategy, load strategy, validation strategy, risks.
2. **Phase 2 — Migrate.** Execute that plan: emit the target-dialect DDL and
   load scripts, which are validated against ground truth.

A rule-based `baseline` contestant runs both phases deterministically for free —
the floor an agent must beat to justify its cost.

## How it works

```
┌──────────────┐   ┌───────────────────────┐   ┌───────────────┐   ┌──────────┐
│ Oracle fleet │   │ per-contestant        │   │ validation    │   │ report + │
│ generator    │──▶│ workspaces (isolated) │──▶│ (PostgreSQL / │──▶│ live     │
│ (seeded DDL  │   │ claude / cursor /     │   │  DuckDB /     │   │ dashboard│
│  + CSV data) │   │ bob / baseline        │   │  Snowflake)   │   │          │
│              │   │ phase 1 plan →        │   │ tables, rows, │   │ results  │
│              │   │ phase 2 migrate       │   │ checksums     │   │ .json    │
└──────────────┘   └───────────────────────┘   └───────────────┘   └──────────┘
```

1. **Generate** — `bakeoff/oracle_gen.py` emits N synthetic-but-authentic Oracle
   19c exports (VARCHAR2/NUMBER/CLOB/DATE types, sequences, `BEFORE INSERT`
   triggers, PL/SQL procedures, FK/CHECK constraints, `COMMENT ON`, CSV data
   with `DD-MON-YYYY` dates). Deterministic per seed, plus a private
   `manifest.json` ground truth (row counts, numeric checksums) agents never see.
2. **Plan (phase 1)** — each contestant gets `PLANNING_TASK.md` and writes an
   architectural migration plan. Nothing is migrated yet.
3. **Migrate (phase 2)** — each contestant gets `MIGRATION_TASK.md`, which
   references its own phase-1 plan, and produces `migrated/schema.sql`,
   `migrated/load.sql`, `migrated/notes.md`. Agents run fully headless:
   - `claude -p ... --output-format stream-json --permission-mode acceptEdits`
   - `cursor-agent -p ... --output-format stream-json --force --trust`
   - `bob "..." --chat-mode code --approval-mode auto_edit -o json`
4. **Validate** — the harness executes the migrated SQL against the target and
   scores: tables created with correct column counts, rows loaded, numeric
   column checksums vs ground truth, zero SQL errors.
5. **Report** — a live dashboard plus `results/<run>/REPORT.md` + `results.json`.

## Real-time dashboard

Running the harness serves a self-contained dashboard (no external deps,
light/dark aware). It shows, per contestant and updating a few times a second:

- **Phase status** — `phase 1 · planning` → `phase 2 · migrating`, with the
  produced plan viewable in a popup (`view full plan ⧉`).
- **Data flow** — every database's every table, filling as rows stream into the
  target: `pending → created (◻) → streaming (⇢) → loaded (✓)`, with field-name
  chips for the table in flight.
- **Live SQL console** — the exact statements hitting the target, green/red with
  real error text.
- **Agent activity** — each contestant's tool calls and output as they happen.
- **Testing layer** — mistakes caught during the run and in final output.
- **Metrics** — success, plan size/time/cost, wall time, spend, tokens, LOC,
  plus a **Best ROI** tile (successful migrations per dollar, baseline excluded).
- **Past runs** — a history dropdown loads any archived run from
  `runs/<name>/history/` into the dashboard; **⬇ Export** downloads the
  currently displayed `results.json`.

Two mechanisms make the movement real, not a timer animation:

- **Shadow execution** (`bakeoff/shadow.py`) — while an agent works, its
  `migrated/*.sql` is tailed and each complete statement is applied to a live
  `shadow_*` schema in Postgres the moment it's written. DDL and rows therefore
  appear *during* the run, not only at final validation. Agent SQL rewrites
  trigger a schema reset + replay; failed statements are shown with real errors.
- **Batch streaming** — CSV loads stream in small row batches with per-batch
  progress, paced (`output.flow_pacing_ms`) so the flow is watchable even on
  tiny databases.

A Prometheus-compatible `/metrics` endpoint is also served, if you want to
scrape runs into Prometheus/Grafana.

## Running it

```bash
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt

# start Postgres (default target) — macOS Homebrew shown; any local PG works
brew services start postgresql@17     # the harness auto-creates the `bakeoff` db

# authenticate the agent CLIs (one-time)
claude /login                         # claude contestant
cursor-agent login                    # cursor contestant (spends Cursor credits)

# serve the dashboard (default) — opens the browser, runs start from its button
./.venv/bin/python run_bakeoff.py

# cheap dev/test run (small DBs, no plan phase, sonnet — ~$1–2)
./.venv/bin/python run_bakeoff.py --config bakeoff.config.dev.yaml --once

# run once immediately instead of serving
./.venv/bin/python run_bakeoff.py --once

# free end-to-end smoke test (no agents, no cost)
./.venv/bin/python run_bakeoff.py --once --contestants baseline

# scale it up
./.venv/bin/python run_bakeoff.py --once --num-dbs 100 --parallelism 8
```

Other flags: `--no-plan` (skip phase 1), `--generate-only` (just emit Oracle
sources), `--revalidate` (re-score existing workspaces without re-running
agents), `--contestants claude,bob`, `--target duckdb|snowflake`,
`--live-port N`, `--no-browser`.

## Contestants

| Name | CLI | Model | Notes |
|---|---|---|---|
| `baseline` | — | none | Free rule-based converter; the floor agents must beat |
| `claude` | `claude` (Claude Code) | opus (pinned) | Reports cost in USD directly |
| `cursor` | `cursor-agent` (Cursor CLI) | Cursor default | Real Cursor credits; the CLI reports tokens but no dollar cost. Pin a model via `contestants.cursor.model` for reproducibility |
| `bob` | `bob` (IBM Bob) | IBM default | Prices in Bobcoins; USD computed at `usd_per_coin` |

## Cost

Cost is dominated by LLM tokens, not the harness. Two configs are provided:

| File | Purpose | Est. cost (all contestants × 2 dbs) |
|---|---|---|
| `bakeoff.config.yaml` | Full demo — mid-size DBs, plan phase on, opus | ~$10–15 |
| `bakeoff.config.dev.yaml` | Cheap iteration — small DBs, no plan, sonnet | ~$1–2 |

The main levers:

- `planning_phase: false` — skip phase 1, saves ~40% cost and time
- `rows_per_table` — CSV data is the largest context chunk; halving rows cuts ~50% of tokens
- `contestants.claude.model: sonnet` vs `opus` — ~6× cost difference
- `run.parallelism` — doesn't affect token count, only wall-clock time

(Cursor spend is in plan credits and doesn't appear in the USD estimates.)

## Targets

| Target | Cost | Setup |
|---|---|---|
| **PostgreSQL** (default) | free, local | a running Postgres; the `bakeoff` db and a per-workspace schema are auto-created. DSN via `targets.postgres.dsn` or `BAKEOFF_PG_DSN`. |
| **DuckDB** | free, zero-install | `--target duckdb` — one local `.duckdb` file per workspace |
| **Snowflake** | 30-day free trial ($400 credits) | [signup](https://signup.snowflake.com), copy `.env.example` → `.env`, `pip install snowflake-connector-python`, `--target snowflake` |

Loads use client-side streaming (`COPY … FROM STDIN`), so the Postgres server
never needs filesystem access to the workspace — this also sidesteps macOS
privacy restrictions on the launchd Postgres service reading `~/Documents`.

## Testing / QA layer

`bakeoff/quality.py` catches agent mistakes in two places, surfaced live and in
the report as `QA err/warn`:

- **During dev** — a watcher re-checks the workspace every few seconds:
  source-file tampering, crashes/timeouts, Oracle syntax appearing in the SQL.
- **Final output** — missing/empty deliverables, missing phase-1 plan, leftover
  Oracle-isms (`VARCHAR2`, `NUMBER(...)`, `SYSDATE`, `FROM DUAL`, …), silently
  dropped PRIMARY KEY / FOREIGN KEY / CHECK constraints, undocumented skips.

Checks are target-aware (e.g. `CREATE FUNCTION`/dollar-quoted PL/pgSQL is valid
on Postgres, so it isn't flagged there) and support per-contestant exemptions
via `output.quality_exempt`.

## Run archiving

Every run snapshots itself to `runs/<name>/history/<timestamp>/` — raw agent
logs (per phase), each contestant's produced plan and SQL, and the reports —
so runs never overwrite each other and post-mortems stay possible.

## Toggles (`bakeoff.config.yaml`)

Everything is controlled from one file:

- **`run.*`** — fleet size, seed, contestants, target, parallelism, agent
  timeouts, `planning_phase` on/off.
- **`input.*`** — shape of the Oracle sources: tables/rows per DB, schema
  domains (hr, orders, inventory, finance), and per-feature difficulty switches
  (`sequences_triggers`, `plsql`, `foreign_keys`, `check_constraints`,
  `comments`, `oracle_date_format`, `edge_cases`).
- **`output.*`** — metric columns, per-DB breakdown, raw-log retention,
  `flow_pacing_ms`, `shadow_execution`, `archive_runs`, `quality_exempt`.
- **`contestants.*`** — model overrides (Cursor: `null` = its default model),
  Claude `max_turns`, Bob `max_coins` spend cap and `usd_per_coin` (Bob prices
  in Bobcoins; USD is computed at $0.50/coin when the CLI reports coins only).

## Scaling to hundreds/thousands of Oracle DBs

- Generation is seeded and O(rows); 1,000 DBs ≈ seconds.
- Migration jobs are independent `(contestant × db)` subprocesses fanned out by
  a thread pool (`run.parallelism`); shard the DB list across machines for
  larger fleets — workspaces are self-contained directories.
- Validation uses one isolated Postgres schema (or DuckDB file) per workspace,
  so runs never interfere.
- `--revalidate` re-scores without re-spending agent tokens.

## Layout

```
bakeoff.config.yaml     # the control panel (full-demo settings)
bakeoff.config.dev.yaml # cheap dev/test settings (--config to select)
run_bakeoff.py          # orchestrator CLI (serve / --once / --revalidate)
bakeoff/
  oracle_gen.py         # synthetic Oracle fleet generator (+ ground truth)
  contestants.py        # claude / cursor / bob / baseline adapters, phase-1 & phase-2 tasks
  targets.py            # PostgreSQL + DuckDB + Snowflake executors
  shadow.py             # live shadow-execution of agent SQL during the run
  validate.py           # migrated-SQL scoring vs manifest
  quality.py            # testing/QA layer (dev + output findings)
  live.py               # run state + dashboard HTTP server (/live.json, /metrics)
  report.py             # results.json + REPORT.md
  visualize.py          # self-contained real-time dashboard
runs/<name>/            # sources, workspaces, agent logs, history/ (gitignored)
results/<name>/         # REPORT.md + results.json + dashboard.html (gitignored)
```

Generated `runs/` and `results/` are reproducible and excluded from version
control; run the harness to produce them.

## Roadmap

- OpenAI Codex CLI contestant (`codex`) for a four-way comparison.
- Data-quality dimensions beyond checksums (null-rate, distinct counts, FK integrity).
- Plan-adherence grading (does phase-2 SQL honor the phase-1 plan?).
- Full cost-normalized leaderboard (the dashboard's Best ROI tile is the start).
