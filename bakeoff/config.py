"""Load and validate bakeoff.config.yaml."""
import copy
from pathlib import Path

import yaml

DEFAULTS = {
    "run": {
        "name": "demo",
        "seed": 42,
        "num_databases": 2,
        "parallelism": 2,
        "agent_timeout_s": 900,
        "planning_phase": True,
        "plan_timeout_s": 600,
        "contestants": ["baseline", "claude", "bob"],
        "target": "postgres",
    },
    "input": {
        "tables_per_db": [4, 6],
        "rows_per_table": [40, 120],
        "domains": ["hr", "orders", "inventory", "finance"],
        "oracle_features": {
            "sequences_triggers": True,
            "plsql": True,
            "foreign_keys": True,
            "check_constraints": True,
            "comments": True,
            "oracle_date_format": True,
            "edge_cases": False,
        },
    },
    "output": {
        "metrics": {},
        "per_db_breakdown": True,
        "save_raw_agent_output": True,
        "flow_pacing_ms": 40,   # delay per streamed batch so data flow is visible
        "shadow_execution": True,  # execute agent SQL live while it works (postgres)
        "archive_runs": True,      # keep per-run logs/SQL/reports in runs/<name>/history/
        "quality_exempt": {},      # {contestant: [check-slug, ...]} QA suppressions
    },
    "contestants": {
        "claude": {"cmd": "claude", "model": "opus", "max_turns": 50},
        "cursor": {"cmd": "cursor-agent", "model": None},  # None = Cursor's default model
        "bob": {"cmd": "bob", "chat_mode": "code", "model": None, "max_coins": None,
                "usd_per_coin": 0.50},
    },
    "targets": {"postgres": {"dsn": "postgresql:///bakeoff"},
                "duckdb": {}, "snowflake": {}},
}


def _merge(base, override):
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(path):
    path = Path(path)
    raw = yaml.safe_load(path.read_text()) if path.exists() else {}
    cfg = _merge(DEFAULTS, raw)
    known = {"baseline", "claude", "bob", "cursor"}
    for c in cfg["run"]["contestants"]:
        if c not in known:
            raise ValueError(f"unknown contestant '{c}' (known: {sorted(known)})")
    if cfg["run"]["target"] not in ("postgres", "duckdb", "snowflake"):
        raise ValueError("run.target must be postgres, duckdb, or snowflake")
    return cfg
