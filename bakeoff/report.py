"""Aggregate results and render results.json + REPORT.md."""
import json
from datetime import datetime
from pathlib import Path


def _rate(num, den):
    return round(num / den, 3) if den else None


def summarize(results):
    """Per-contestant aggregates over all databases."""
    by_c = {}
    for r in results:
        by_c.setdefault(r["contestant"], []).append(r)
    summary = {}
    for cname, rs in by_c.items():
        v = [r["validation"] for r in rs]
        costs = [r["agent"]["cost_usd"] for r in rs if r["agent"]["cost_usd"] is not None]
        coins = [r["agent"]["coins"] for r in rs if r["agent"].get("coins") is not None]
        plans = [r["plan"] for r in rs if r.get("plan")]
        plan_costs = [p["agent"]["cost_usd"] for p in plans
                      if p["agent"]["cost_usd"] is not None]
        toks_in = [r["agent"]["tokens_in"] for r in rs if r["agent"]["tokens_in"]]
        toks_out = [r["agent"]["tokens_out"] for r in rs if r["agent"]["tokens_out"]]
        turns = [r["agent"]["turns"] for r in rs if r["agent"]["turns"]]
        summary[cname] = {
            "databases": len(rs),
            "fully_successful": sum(1 for x in v if x["success"]),
            "success_rate": _rate(sum(1 for x in v if x["success"]), len(rs)),
            "table_success_rate": _rate(sum(x["tables_ok"] for x in v),
                                        sum(x["tables_expected"] for x in v)),
            "row_match_rate": _rate(sum(x["row_match_ok"] for x in v),
                                    sum(x["tables_expected"] for x in v)),
            "checksum_match_rate": _rate(sum(x["checksums_ok"] for x in v),
                                         sum(x["checksums_expected"] for x in v)),
            "constraint_preservation": _rate(
                sum(x.get("constraints", {}).get("preserved", 0) for x in v),
                sum(x.get("constraints", {}).get("expected", 0) for x in v)),
            "lines_of_code": sum(r["loc"] for r in rs),
            "wall_time_s": round(sum(r["agent"]["wall_time_s"] or 0 for r in rs), 1),
            "cost_usd": round(sum(costs), 4) if costs else None,
            "coins": round(sum(coins), 4) if coins else None,
            "tokens_in": sum(toks_in) if toks_in else None,
            "tokens_out": sum(toks_out) if toks_out else None,
            "avg_turns": _rate(sum(turns), len(turns)) if turns else None,
            "quality_errors": sum(r.get("quality", {}).get("errors", 0) for r in rs),
            "quality_warnings": sum(r.get("quality", {}).get("warnings", 0) for r in rs),
            "plan_wall_time_s": (round(sum(p["agent"]["wall_time_s"] or 0
                                           for p in plans), 1) if plans else None),
            "plan_cost_usd": round(sum(plan_costs), 4) if plan_costs else None,
            "plan_lines": sum(p.get("lines", 0) for p in plans) if plans else None,
        }
    return summary


METRIC_COLUMNS = [
    # (config toggle, column header, summary key, format)
    ("success", "Success", "fully_successful", "frac"),
    ("plan", "Plan (lines / s / $)", None, "plan"),
    ("table_success_rate", "Tables OK", "table_success_rate", "pct"),
    ("row_match_rate", "Rows OK", "row_match_rate", "pct"),
    ("checksum_match_rate", "Checksums OK", "checksum_match_rate", "pct"),
    ("lines_of_code", "LOC", "lines_of_code", "int"),
    ("wall_time_s", "Time (s)", "wall_time_s", "num"),
    ("cost_usd", "Cost (USD)", "cost_usd", "usd"),
    ("tokens", "Tokens in/out", None, "tokens"),
    ("turns", "Avg turns", "avg_turns", "num"),
    ("quality", "QA err/warn", None, "qa"),
]


def _fmt(val, kind, row=None):
    if kind == "tokens":
        ti, to = row.get("tokens_in"), row.get("tokens_out")
        return f"{ti:,} / {to:,}" if ti or to else "—"
    if kind == "qa":
        return f"{row.get('quality_errors', 0)} / {row.get('quality_warnings', 0)}"
    if kind == "plan":
        if row.get("plan_lines") is None:
            return "—"
        cost = (f"${row['plan_cost_usd']:.2f}"
                if row.get("plan_cost_usd") is not None else "$0")
        return f"{row['plan_lines']} / {row.get('plan_wall_time_s')}s / {cost}"
    if val is None:
        return "—"
    if kind == "pct":
        return f"{val * 100:.1f}%"
    if kind == "usd":
        return f"${val:.4f}"
    if kind == "int":
        return f"{val:,}"
    if kind == "frac":
        return str(val)
    return str(val)


def render_markdown(cfg, results, summary):
    m_cfg = cfg["output"]["metrics"]
    cols = [(h, key, kind) for tog, h, key, kind in METRIC_COLUMNS
            if m_cfg.get(tog, True)]
    lines = [
        "# Oracle → Lakehouse Migration Bakeoff",
        "",
        f"- **Run:** `{cfg['run']['name']}`  |  **Target:** `{cfg['run']['target']}`"
        f"  |  **Databases:** {cfg['run']['num_databases']}  |  **Seed:** {cfg['run']['seed']}",
        f"- **Generated:** {datetime.now().isoformat(timespec='seconds')}",
        f"- **Oracle features enabled:** "
        + ", ".join(k for k, on in cfg["input"]["oracle_features"].items() if on),
        "",
        "## Summary",
        "",
        "| Contestant | DBs | " + " | ".join(h for h, _k, _f in cols) + " |",
        "|---" * (len(cols) + 2) + "|",
    ]
    order = sorted(summary, key=lambda c: (-(summary[c]["success_rate"] or 0),
                                           summary[c]["wall_time_s"]))
    for cname in order:
        s = summary[cname]
        cells = []
        for header, key, kind in cols:
            if kind == "frac":
                cells.append(f"{s['fully_successful']}/{s['databases']}")
            else:
                cells.append(_fmt(s.get(key), kind, s))
        lines.append(f"| **{cname}** | {s['databases']} | " + " | ".join(cells) + " |")
    lines.append("")
    lines.append("> Success = all tables created with correct columns, all rows loaded, "
                 "all numeric checksums matching ground truth, zero SQL errors. "
                 "`baseline` is a free rule-based converter — the floor an agent must beat.")

    if cfg["output"].get("per_db_breakdown", True):
        lines += ["", "## Per-database detail", ""]
        lines.append("| Database | Contestant | Success | Tables | Rows loaded | "
                     "Checksums | Time (s) | Cost | Errors |")
        lines.append("|---" * 9 + "|")
        for r in sorted(results, key=lambda r: (r["db"], r["contestant"])):
            v, a = r["validation"], r["agent"]
            n_err = len(v["schema_errors"]) + len(v["load_errors"])
            err_txt = str(n_err)
            if a.get("error"):
                err_txt += " (agent err)"
            lines.append(
                f"| {r['db']} | {r['contestant']} | {'✅' if v['success'] else '❌'} "
                f"| {v['tables_ok']}/{v['tables_expected']} "
                f"| {v['rows_loaded']}/{v['rows_expected']} "
                f"| {v['checksums_ok']}/{v['checksums_expected']} "
                f"| {a['wall_time_s'] if a['wall_time_s'] is not None else '—'} "
                f"| {_fmt(a['cost_usd'], 'usd')} | {err_txt} |")
        lines.append("")
    return "\n".join(lines) + "\n"


def write_reports(out_dir, cfg, results, events=None, jobs=None):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = summarize(results)
    payload = {
        "run": cfg["run"],
        "input": cfg["input"],
        "quality_exempt": cfg["output"].get("quality_exempt", {}),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "summary": summary,
        "results": results,
    }
    if events:
        payload["events"] = events
    if jobs:
        payload["jobs"] = jobs
    (out_dir / "results.json").write_text(json.dumps(payload, indent=2, default=str))
    (out_dir / "REPORT.md").write_text(render_markdown(cfg, results, summary))
    from .visualize import write_dashboard
    write_dashboard(out_dir)
    return summary
