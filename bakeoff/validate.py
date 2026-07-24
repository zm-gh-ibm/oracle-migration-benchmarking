"""Validate a contestant's migrated output against the ground-truth manifest."""
from pathlib import Path

from .targets import TARGETS


def validate_workspace(workspace, manifest, target_name, target_cfg=None,
                       on_data=None, pacing_s=0.0):
    """Execute migrated/schema.sql + load.sql against the target and score them.

    on_data(table, rows_done, rows_total, moved, expected, fields, final)
    fires as rows move into the target: batch-by-batch while a table streams
    (final=False, on targets that support load_progress) and once per table
    when its load statement lands (final=True). fields is the table's column
    names. pacing_s throttles streaming batches so the dashboard's data-flow
    animation is visible on small databases.
    """
    workspace = Path(workspace)
    result = {
        "schema_file_exists": False,
        "load_file_exists": False,
        "schema_errors": [],
        "load_errors": [],
        "tables": [],
        "tables_expected": len(manifest["tables"]),
        "tables_ok": 0,
        "rows_expected": sum(t["row_count"] for t in manifest["tables"]),
        "rows_loaded": 0,
        "row_match_ok": 0,
        "checksums_expected": sum(1 for t in manifest["tables"] if t["checksum_col"]),
        "checksums_ok": 0,
        "success": False,
    }
    schema_sql = workspace / "migrated" / "schema.sql"
    load_sql = workspace / "migrated" / "load.sql"
    result["schema_file_exists"] = schema_sql.exists()
    result["load_file_exists"] = load_sql.exists()
    if not schema_sql.exists():
        return result

    target = TARGETS[target_name](workspace, target_cfg)
    try:
        result["schema_errors"] = target.run_script(schema_sql.read_text())
        if load_sql.exists():
            # Report rows moving source -> target: batch-level via the target's
            # load_progress hook, plus an authoritative per-table count after
            # each load statement.
            tinfo = {t["name"].lower(): t for t in manifest["tables"]}
            counts = {}
            streamed, finalized = set(), set()

            def _emit(tname_l, done, final):
                t = tinfo[tname_l]
                counts[tname_l] = done
                on_data(t["name"], done, t["row_count"], sum(counts.values()),
                        result["rows_expected"],
                        [c["name"] for c in t["columns"]], final)

            def _table_count(tname):
                try:
                    return target.scalar(f'SELECT COUNT(*) FROM "{tname}"') or 0
                except Exception:
                    try:
                        return target.scalar(f"SELECT COUNT(*) FROM {tname}") or 0
                    except Exception:
                        return 0

            def _after_stmt(_stmt):
                if on_data is None:
                    return
                for tname_l in tinfo:
                    n = _table_count(tinfo[tname_l]["name"])
                    changed = n != counts.get(tname_l, 0)
                    pending = tname_l in streamed and tname_l not in finalized
                    if n and (changed or pending):
                        finalized.add(tname_l)
                        _emit(tname_l, n, True)

            def _progress(table, done, total):
                tname_l = table.lower()
                if tname_l in tinfo:
                    streamed.add(tname_l)
                    _emit(tname_l, done, False)

            if on_data and hasattr(target, "load_progress"):
                target.load_progress = _progress
                target.load_pacing_s = pacing_s

            result["load_errors"] = target.run_script(
                load_sql.read_text(), on_stmt=_after_stmt if on_data else None)

        present = target.table_names()
        for t in manifest["tables"]:
            tname = t["name"]
            detail = {"name": tname, "created": False, "row_count": None,
                      "rows_ok": False, "columns_ok": False, "checksum_ok": None}
            if tname.lower() in present:
                detail["created"] = True
                try:
                    detail["row_count"] = target.scalar(f'SELECT COUNT(*) FROM "{tname}"')
                except Exception:
                    detail["row_count"] = target.scalar(f"SELECT COUNT(*) FROM {tname}")
                detail["rows_ok"] = detail["row_count"] == t["row_count"]
                detail["columns_ok"] = target.column_count(tname) == len(t["columns"])
                if t["checksum_col"]:
                    try:
                        cast = getattr(target, "numeric_cast", "DOUBLE")
                        got = target.scalar(
                            f'SELECT ROUND(SUM(CAST("{t["checksum_col"]}" AS {cast})), 2) '
                            f'FROM "{tname}"')
                        detail["checksum_ok"] = (
                            got is not None and abs(float(got) - t["checksum"]) < 0.05)
                    except Exception as e:
                        detail["checksum_ok"] = False
                        detail["checksum_error"] = str(e).split("\n")[0][:200]
                    if detail["checksum_ok"]:
                        result["checksums_ok"] += 1
                if detail["row_count"]:
                    result["rows_loaded"] += detail["row_count"]
                if detail["rows_ok"]:
                    result["row_match_ok"] += 1
                if detail["created"] and detail["columns_ok"]:
                    result["tables_ok"] += 1
            result["tables"].append(detail)
    finally:
        target.close()

    result["success"] = (
        result["tables_ok"] == result["tables_expected"]
        and result["row_match_ok"] == result["tables_expected"]
        and result["checksums_ok"] == result["checksums_expected"]
        and not result["schema_errors"]
        and not result["load_errors"]
    )
    return result


def loc_of_migration(workspace):
    """Non-blank lines of SQL the contestant produced."""
    total = 0
    for f in (Path(workspace) / "migrated").glob("*.sql"):
        total += sum(1 for line in f.read_text().splitlines() if line.strip())
    return total
