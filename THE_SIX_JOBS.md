# The 6 Jobs, Simply Explained

A full bakeoff run is **3 contestants × 2 databases = 6 migration jobs**. Every
job is the same challenge given to a different worker: *"Here is an export of
one Oracle database. Migrate it to DuckDB."*

## The two databases (the work to be done)

Both are synthetic Oracle 19c exports, generated fresh each run from seed 42 so
every contestant gets identical inputs.

**`hr_0001` — an HR system.** 5 tables, 315 rows total:
`departments`, `jobs`, `employees`, `job_history`, `performance_reviews`.

**`orders_0002` — an order-processing system.** 5 tables, 454 rows total:
`customers`, `products`, `orders`, `order_items`, `shipments`.

Each database comes as one `schema.sql` (Oracle DDL) plus one CSV of data per
table — and it's deliberately full of Oracle-isms that don't exist in DuckDB:
sequences with triggers, PL/SQL procedures, `VARCHAR2`/`NUMBER` types,
`DD-MON-YYYY` dates, and `COMMENT ON` statements. Translating those is the
hard part.

## The three contestants (the workers)

**`baseline`** — a ~100-line rule-based Python converter. No AI, free, instant.
It mechanically maps types and drops anything it can't translate. It exists as
the *floor*: if an AI agent can't beat this, the AI adds no value.

**`claude`** — Claude Code running headless. Reads the task, explores the
source files with tool calls, writes the migration SQL, costs real API dollars.

**`bob`** — IBM's Bob CLI doing the same thing, one-shot in code mode.

## The 6 jobs

| # | Job | What it means |
|---|-----|---------------|
| 1 | `baseline` × `hr_0001` | Rule-based script converts the HR database |
| 2 | `baseline` × `orders_0002` | Rule-based script converts the orders database |
| 3 | `claude` × `hr_0001` | Claude Code migrates the HR database |
| 4 | `claude` × `orders_0002` | Claude Code migrates the orders database |
| 5 | `bob` × `hr_0001` | Bob migrates the HR database |
| 6 | `bob` × `orders_0002` | Bob migrates the orders database |

Each job runs in its own isolated workspace (a private copy of the source
files), so contestants can't see each other's work or the answer key.

## What each job must produce

Three files in `migrated/`:

1. **`schema.sql`** — the tables recreated in DuckDB syntax
2. **`load.sql`** — statements that load every CSV into those tables
3. **`notes.md`** — what couldn't be translated (sequences, triggers, PL/SQL) and what to do instead

## How a job passes

The harness executes the produced SQL against a real DuckDB and checks it
against ground truth the contestants never saw. A job **passes** only if all
of these hold:

- every table exists with the right columns
- every row loaded (315 for `hr_0001`, 454 for `orders_0002`)
- numeric column checksums match the source data exactly
- zero SQL errors

Along the way each job also records wall time, cost, tokens, and lines of SQL
produced — that's what the dashboard compares across contestants.
