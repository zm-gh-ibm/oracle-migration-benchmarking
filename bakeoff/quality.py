"""Testing layer: catch agent mistakes during development and in final output.

Two kinds of checks, both attached to each job result as result["quality"]:

  dev     -- mistakes made while the agent worked: tampering with source/,
             crashes, timeouts. A LiveQualityWatcher thread re-checks the
             workspace every few seconds during the run and pushes "issue"
             events to the live dashboard the moment a mistake appears.
  output  -- mistakes in the deliverables: Oracle syntax left in target SQL,
             missing/empty files, dropped constraints, undocumented skips.

A finding: {"phase": "dev"|"output", "severity": "error"|"warn",
            "check": <slug>, "message": <human text>}.
"""
import hashlib
import re
import threading
from pathlib import Path

# Oracle constructs that must not survive a migration to a lakehouse target.
# Each has its own check slug so output.quality_exempt can suppress precisely.
ORACLEISMS = [
    (r"\bVARCHAR2\b", "error", "Oracle type VARCHAR2 left in output",
     "oracle-varchar2"),
    (r"\bNUMBER\s*\(", "error", "Oracle type NUMBER(...) left in output",
     "oracle-number"),
    (r"\bCREATE\s+SEQUENCE\b", "error", "CREATE SEQUENCE (Oracle-only) in output",
     "oracle-sequence"),
    (r"\bCREATE\s+(?:OR\s+REPLACE\s+)?TRIGGER\b", "error", "trigger DDL in output",
     "oracle-trigger"),
    (r"\bCREATE\s+(?:OR\s+REPLACE\s+)?(?:PROCEDURE|FUNCTION|PACKAGE)\b", "error",
     "PL/SQL object in output", "oracle-plsql"),
    (r"\bSYSDATE\b", "error", "SYSDATE (Oracle-only) in output", "oracle-sysdate"),
    (r"\bFROM\s+DUAL\b", "error", "FROM DUAL (Oracle-only) in output",
     "oracle-dual"),
    (r"\bNVL\s*\(", "warn", "NVL() is Oracle-flavored — use COALESCE",
     "oracle-nvl"),
    (r"\bTO_DATE\s*\(", "warn", "TO_DATE() may not exist on the target",
     "oracle-todate"),
]


def _finding(phase, severity, check, message):
    return {"phase": phase, "severity": severity, "check": check,
            "message": str(message)[:300]}


def strip_sql_comments(sql_text):
    """Remove -- line comments and /* */ blocks, preserving string literals.

    Agents legitimately document their type mapping in comments
    ("-- VARCHAR2(n) -> VARCHAR"); scanning those as leftover Oracle syntax
    would be a false positive.
    """
    out, i, n = [], 0, len(sql_text)
    in_str = None
    while i < n:
        ch = sql_text[i]
        if in_str:
            out.append(ch)
            if ch == in_str:
                in_str = None
            i += 1
        elif ch in ("'", '"'):
            in_str = ch
            out.append(ch)
            i += 1
        elif sql_text.startswith("--", i):
            j = sql_text.find("\n", i)
            i = n if j == -1 else j
        elif sql_text.startswith("/*", i):
            j = sql_text.find("*/", i + 2)
            i = n if j == -1 else j + 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def snapshot_source(workspace):
    """Hash every file under source/ before the agent starts."""
    hashes = {}
    for f in sorted(Path(workspace).rglob("source/**/*")):
        if f.is_file():
            rel = str(f.relative_to(workspace))
            hashes[rel] = hashlib.sha256(f.read_bytes()).hexdigest()
    return hashes


def source_tamper_findings(workspace, before, phase="dev"):
    """Detect the cardinal sin: the agent changed the source it was migrating."""
    workspace = Path(workspace)
    after = snapshot_source(workspace)
    out = []
    for rel, h in before.items():
        if rel not in after:
            out.append(_finding(phase, "error", "source-deleted",
                                f"agent deleted {rel}"))
        elif after[rel] != h:
            out.append(_finding(phase, "error", "source-modified",
                                f"agent modified {rel}"))
    for rel in after:
        if rel not in before:
            out.append(_finding(phase, "warn", "source-added",
                                f"agent added unexpected file {rel}"))
    return out


# checks that are false positives on a given target: Postgres natively supports
# CREATE FUNCTION/PROCEDURE with dollar-quoting, so translated PL/SQL is a
# legitimate (good!) migration there, not a leftover Oracle-ism.
TARGET_NATIVE = {"postgres": {"oracle-plsql"}}


def scan_sql_findings(workspace, phase="output", target_name=None):
    """Grep migrated/*.sql for Oracle constructs that should have been translated."""
    skip = TARGET_NATIVE.get(target_name, set())
    out = []
    for f in sorted(Path(workspace).glob("migrated/*.sql")):
        text = strip_sql_comments(f.read_text())
        for pattern, severity, message, slug in ORACLEISMS:
            if slug in skip:
                continue
            hits = len(re.findall(pattern, text, re.IGNORECASE))
            if hits:
                out.append(_finding(phase, severity, slug,
                                    f"{f.name}: {message} ({hits}×)"))
    return out


def apply_exemptions(findings, exempt):
    """Drop findings whose check slug a contestant is exempted from
    (output.quality_exempt in the config)."""
    if not exempt:
        return findings
    return [f for f in findings if f["check"] not in exempt]


def dev_findings(workspace, before_hashes, agent_metrics):
    """Process-level mistakes made while the agent worked."""
    out = source_tamper_findings(workspace, before_hashes)
    if agent_metrics.get("error"):
        out.append(_finding("dev", "error", "agent-error",
                            f"agent reported an error: {agent_metrics['error']}"))
    elif agent_metrics.get("exit_code") == -1:
        out.append(_finding("dev", "error", "timeout", "agent hit the timeout"))
    elif agent_metrics.get("exit_code") not in (0, None):
        out.append(_finding("dev", "error", "exit-code",
                            f"agent exited with code {agent_metrics['exit_code']}"))
    return out


def output_findings(workspace, manifest, target_name=None):
    """Mistakes in the final deliverables (beyond pass/fail validation)."""
    workspace = Path(workspace)
    out = []
    for name in ("schema.sql", "load.sql", "notes.md"):
        f = workspace / "migrated" / name
        if not f.exists():
            out.append(_finding("output", "error", "missing-deliverable",
                                f"migrated/{name} was never written"))
        elif not f.read_text().strip():
            out.append(_finding("output", "error", "empty-deliverable",
                                f"migrated/{name} is empty"))

    out += scan_sql_findings(workspace, target_name=target_name)

    # constraints silently dropped (source had them, migration doesn't)
    src = workspace / "source" / "schema.sql"
    mig = workspace / "migrated" / "schema.sql"
    if src.exists() and mig.exists():
        src_text = strip_sql_comments(src.read_text())
        mig_text = strip_sql_comments(mig.read_text())
        for pattern, label in [(r"\bPRIMARY\s+KEY\b", "PRIMARY KEY"),
                               (r"\b(?:FOREIGN\s+KEY|REFERENCES)\b", "FOREIGN KEY"),
                               (r"\bCHECK\s*\(", "CHECK")]:
            n_src = len(re.findall(pattern, src_text, re.IGNORECASE))
            n_mig = len(re.findall(pattern, mig_text, re.IGNORECASE))
            if n_mig < n_src:
                out.append(_finding("output", "warn", "constraints-dropped",
                                    f"{label} constraints: source has {n_src}, "
                                    f"migration has {n_mig}"))

    # skipped Oracle objects must at least be documented in notes.md
    notes = workspace / "migrated" / "notes.md"
    if src.exists() and notes.exists():
        src_text = src.read_text()
        notes_text = notes.read_text().lower()
        for pattern, word in [(r"\bCREATE\s+SEQUENCE\b", "sequence"),
                              (r"\bCREATE\s+(?:OR\s+REPLACE\s+)?TRIGGER\b", "trigger"),
                              (r"\bCREATE\s+(?:OR\s+REPLACE\s+)?(?:PROCEDURE|FUNCTION)\b",
                               "procedure")]:
            if re.search(pattern, src_text, re.IGNORECASE) and word not in notes_text:
                out.append(_finding("output", "warn", "undocumented-skip",
                                    f"source has {word}s but notes.md never "
                                    f"mentions them"))
    return out


def summarize_findings(findings):
    return {"findings": findings,
            "errors": sum(1 for f in findings if f["severity"] == "error"),
            "warnings": sum(1 for f in findings if f["severity"] == "warn")}


class LiveQualityWatcher:
    """Re-checks the workspace every few seconds while the agent runs and
    reports new mistakes immediately via on_issue(severity, message)."""

    def __init__(self, workspace, before_hashes, on_issue, interval_s=3,
                 exempt=None, target_name=None):
        self.workspace = workspace
        self.before = before_hashes
        self.on_issue = on_issue
        self.interval = interval_s
        self.exempt = set(exempt or ())
        self.target_name = target_name
        self._stop = threading.Event()
        self.seen = set()   # {(check, message)} — run_job dedupes final events
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def start(self):
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=self.interval + 2)

    def _check_once(self):
        findings = apply_exemptions(
            source_tamper_findings(self.workspace, self.before)
            + scan_sql_findings(self.workspace, phase="dev",
                                target_name=self.target_name), self.exempt)
        for f in findings:
            key = (f["check"], f["message"])
            if key not in self.seen:
                self.seen.add(key)
                try:
                    self.on_issue(f["severity"], f["message"])
                except Exception:
                    pass

    def _loop(self):
        while not self._stop.wait(self.interval):
            try:
                self._check_once()
            except Exception:
                pass
