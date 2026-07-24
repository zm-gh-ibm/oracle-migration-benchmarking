"""Synthetic Oracle database generator.

Emits, per database:
  source/schema.sql   -- authentic-looking Oracle DDL (VARCHAR2, NUMBER, CLOB,
                         sequences, triggers, PL/SQL, constraints, comments)
  source/data/*.csv   -- one CSV per table, dates optionally in DD-MON-YYYY
  manifest.json       -- ground truth used by the validator (never shown to agents)

Deterministic for a given (seed, db index), so a fleet of thousands of DBs is
reproducible across machines.
"""
import csv
import json
import random
from pathlib import Path

# ---------------------------------------------------------------------------
# Domain templates: (table, [(col, oracle_type, semantic, nullable)], pk, fks)
# fks: {col: (parent_table, parent_col)} -- listed in dependency order.
# ---------------------------------------------------------------------------
DOMAINS = {
    "hr": [
        ("departments",
         [("dept_id", "NUMBER(6)", "id", False),
          ("dept_name", "VARCHAR2(60)", "org_name", False),
          ("location", "VARCHAR2(80)", "city", True)],
         "dept_id", {}),
        ("jobs",
         [("job_id", "NUMBER(6)", "id", False),
          ("job_title", "VARCHAR2(80)", "job_title", False),
          ("min_salary", "NUMBER(10,2)", "amount", True),
          ("max_salary", "NUMBER(10,2)", "amount", True)],
         "job_id", {}),
        ("employees",
         [("emp_id", "NUMBER(10)", "id", False),
          ("first_name", "VARCHAR2(50)", "first_name", False),
          ("last_name", "VARCHAR2(50)", "last_name", False),
          ("email", "VARCHAR2(120)", "email", True),
          ("hire_date", "DATE", "date", False),
          ("salary", "NUMBER(10,2)", "amount", True),
          ("dept_id", "NUMBER(6)", "fk", True),
          ("bio", "CLOB", "clob", True)],
         "emp_id", {"dept_id": ("departments", "dept_id")}),
        ("job_history",
         [("history_id", "NUMBER(10)", "id", False),
          ("emp_id", "NUMBER(10)", "fk", False),
          ("job_id", "NUMBER(6)", "fk", False),
          ("start_date", "DATE", "date", False),
          ("end_date", "DATE", "date", True)],
         "history_id", {"emp_id": ("employees", "emp_id"), "job_id": ("jobs", "job_id")}),
        ("performance_reviews",
         [("review_id", "NUMBER(10)", "id", False),
          ("emp_id", "NUMBER(10)", "fk", False),
          ("review_date", "DATE", "date", False),
          ("rating", "NUMBER(2)", "rating", False),
          ("summary", "VARCHAR2(400)", "text", True)],
         "review_id", {"emp_id": ("employees", "emp_id")}),
        ("benefits",
         [("benefit_id", "NUMBER(6)", "id", False),
          ("benefit_name", "VARCHAR2(80)", "product", False),
          ("annual_cost", "NUMBER(10,2)", "amount", True),
          ("active_flag", "CHAR(1)", "flag", False)],
         "benefit_id", {}),
    ],
    "orders": [
        ("customers",
         [("customer_id", "NUMBER(10)", "id", False),
          ("cust_name", "VARCHAR2(100)", "full_name", False),
          ("email", "VARCHAR2(120)", "email", True),
          ("credit_limit", "NUMBER(12,2)", "amount", True),
          ("created_at", "DATE", "date", False)],
         "customer_id", {}),
        ("products",
         [("product_id", "NUMBER(10)", "id", False),
          ("product_name", "VARCHAR2(120)", "product", False),
          ("category", "VARCHAR2(40)", "category", True),
          ("list_price", "NUMBER(10,2)", "amount", False)],
         "product_id", {}),
        ("orders",
         [("order_id", "NUMBER(12)", "id", False),
          ("customer_id", "NUMBER(10)", "fk", False),
          ("order_date", "DATE", "date", False),
          ("status", "VARCHAR2(20)", "status", False),
          ("total_amount", "NUMBER(12,2)", "amount", True)],
         "order_id", {"customer_id": ("customers", "customer_id")}),
        ("order_items",
         [("item_id", "NUMBER(12)", "id", False),
          ("order_id", "NUMBER(12)", "fk", False),
          ("product_id", "NUMBER(10)", "fk", False),
          ("quantity", "NUMBER(6)", "qty", False),
          ("unit_price", "NUMBER(10,2)", "amount", False)],
         "item_id", {"order_id": ("orders", "order_id"), "product_id": ("products", "product_id")}),
        ("shipments",
         [("shipment_id", "NUMBER(12)", "id", False),
          ("order_id", "NUMBER(12)", "fk", False),
          ("shipped_date", "DATE", "date", True),
          ("carrier", "VARCHAR2(60)", "carrier", True),
          ("tracking_no", "VARCHAR2(40)", "code", True)],
         "shipment_id", {"order_id": ("orders", "order_id")}),
    ],
    "inventory": [
        ("warehouses",
         [("warehouse_id", "NUMBER(6)", "id", False),
          ("warehouse_name", "VARCHAR2(80)", "org_name", False),
          ("city", "VARCHAR2(60)", "city", True),
          ("capacity_units", "NUMBER(10)", "qty", True)],
         "warehouse_id", {}),
        ("items",
         [("item_id", "NUMBER(10)", "id", False),
          ("item_name", "VARCHAR2(120)", "product", False),
          ("sku", "VARCHAR2(30)", "code", False),
          ("unit_cost", "NUMBER(10,2)", "amount", False)],
         "item_id", {}),
        ("stock_levels",
         [("stock_id", "NUMBER(12)", "id", False),
          ("warehouse_id", "NUMBER(6)", "fk", False),
          ("item_id", "NUMBER(10)", "fk", False),
          ("on_hand", "NUMBER(10)", "qty", False),
          ("last_counted", "DATE", "date", True)],
         "stock_id", {"warehouse_id": ("warehouses", "warehouse_id"), "item_id": ("items", "item_id")}),
        ("stock_movements",
         [("movement_id", "NUMBER(12)", "id", False),
          ("item_id", "NUMBER(10)", "fk", False),
          ("warehouse_id", "NUMBER(6)", "fk", False),
          ("moved_at", "DATE", "date", False),
          ("qty_change", "NUMBER(10)", "qty_signed", False),
          ("reason", "VARCHAR2(200)", "text", True)],
         "movement_id", {"item_id": ("items", "item_id"), "warehouse_id": ("warehouses", "warehouse_id")}),
        ("suppliers",
         [("supplier_id", "NUMBER(8)", "id", False),
          ("supplier_name", "VARCHAR2(100)", "org_name", False),
          ("contact_email", "VARCHAR2(120)", "email", True),
          ("rating", "NUMBER(2)", "rating", True)],
         "supplier_id", {}),
    ],
    "finance": [
        ("accounts",
         [("account_id", "NUMBER(10)", "id", False),
          ("account_name", "VARCHAR2(100)", "org_name", False),
          ("account_type", "VARCHAR2(30)", "acct_type", False),
          ("opened_date", "DATE", "date", False),
          ("balance", "NUMBER(14,2)", "amount", False)],
         "account_id", {}),
        ("transactions",
         [("txn_id", "NUMBER(14)", "id", False),
          ("account_id", "NUMBER(10)", "fk", False),
          ("txn_date", "DATE", "date", False),
          ("amount", "NUMBER(12,2)", "amount_signed", False),
          ("txn_type", "VARCHAR2(20)", "txn_type", False),
          ("memo", "VARCHAR2(240)", "text", True)],
         "txn_id", {"account_id": ("accounts", "account_id")}),
        ("invoices",
         [("invoice_id", "NUMBER(12)", "id", False),
          ("account_id", "NUMBER(10)", "fk", False),
          ("issue_date", "DATE", "date", False),
          ("due_date", "DATE", "date", True),
          ("amount_due", "NUMBER(12,2)", "amount", False),
          ("status", "VARCHAR2(20)", "status", False)],
         "invoice_id", {"account_id": ("accounts", "account_id")}),
        ("payments",
         [("payment_id", "NUMBER(12)", "id", False),
          ("invoice_id", "NUMBER(12)", "fk", False),
          ("paid_date", "DATE", "date", False),
          ("amount_paid", "NUMBER(12,2)", "amount", False),
          ("method", "VARCHAR2(30)", "pay_method", True)],
         "payment_id", {"invoice_id": ("invoices", "invoice_id")}),
    ],
}

FIRST = ["James", "Maria", "Wei", "Aisha", "Carlos", "Yuki", "Fatima", "Liam",
         "Priya", "Elena", "Kofi", "Hana", "Diego", "Ingrid", "Omar", "Grace"]
LAST = ["Smith", "Garcia", "Chen", "Okafor", "Tanaka", "Muller", "Silva",
        "Patel", "Johnson", "Ivanov", "Nakamura", "Brown", "Haddad", "Kim"]
CITIES = ["Austin", "Toronto", "Berlin", "Singapore", "Sao Paulo", "Denver",
          "Osaka", "Dublin", "Pune", "Nairobi", "Lyon", "Boston"]
ORGS = ["Northwind", "Apex", "Vertex", "Summit", "Pioneer", "Cascade",
        "Meridian", "Atlas", "Beacon", "Harbor"]
ORG_SUFFIX = ["Group", "Holdings", "Division", "Unit", "Team", "Partners"]
PRODUCTS = ["Widget", "Gasket", "Rotor", "Valve", "Sensor", "Bracket",
            "Coupler", "Bearing", "Module", "Panel"]
PRODUCT_ADJ = ["Steel", "Compact", "Heavy-Duty", "Precision", "Standard",
               "Industrial", "Micro", "Sealed"]
CATEGORIES = ["HARDWARE", "ELECTRONICS", "TOOLS", "RAW", "CONSUMABLE"]
STATUSES = ["NEW", "OPEN", "PENDING", "SHIPPED", "CLOSED", "CANCELLED"]
CARRIERS = ["FedEx", "UPS", "DHL", "USPS", "Maersk"]
JOB_TITLES = ["Analyst", "Engineer", "Manager", "Director", "Clerk",
              "Specialist", "Coordinator", "Architect"]
TXN_TYPES = ["DEBIT", "CREDIT", "FEE", "INTEREST", "TRANSFER"]
ACCT_TYPES = ["CHECKING", "SAVINGS", "PAYABLE", "RECEIVABLE", "EQUITY"]
PAY_METHODS = ["ACH", "WIRE", "CHECK", "CARD"]
WORDS = ("quarterly reconciliation approved pending review escalated routine "
         "adjustment verified manual audit standard exception priority "
         "seasonal restock damaged returned transfer").split()
UNICODE_SAMPLES = ["café münchen", "北京 warehouse", "señor lópez", "zürich año"]
MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
          "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]


def _fmt_date(rng, oracle_fmt):
    y = rng.randint(2015, 2025)
    m = rng.randint(1, 12)
    d = rng.randint(1, 28)
    if oracle_fmt:
        return f"{d:02d}-{MONTHS[m - 1]}-{y}"
    return f"{y}-{m:02d}-{d:02d}"


def _value(rng, semantic, row_i, features):
    if semantic == "id":
        return row_i + 1
    if semantic == "first_name":
        return rng.choice(FIRST)
    if semantic == "last_name":
        return rng.choice(LAST)
    if semantic == "full_name":
        name = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
        if features.get("edge_cases") and rng.random() < 0.08:
            name = rng.choice(UNICODE_SAMPLES)
        return name
    if semantic == "email":
        return f"{rng.choice(FIRST).lower()}.{rng.choice(LAST).lower()}{rng.randint(1, 99)}@example.com"
    if semantic == "city":
        return rng.choice(CITIES)
    if semantic == "org_name":
        return f"{rng.choice(ORGS)} {rng.choice(ORG_SUFFIX)}"
    if semantic == "product":
        return f"{rng.choice(PRODUCT_ADJ)} {rng.choice(PRODUCTS)}"
    if semantic == "category":
        return rng.choice(CATEGORIES)
    if semantic == "status":
        return rng.choice(STATUSES)
    if semantic == "carrier":
        return rng.choice(CARRIERS)
    if semantic == "job_title":
        return f"{rng.choice(['Senior', 'Lead', 'Junior', 'Principal'])} {rng.choice(JOB_TITLES)}"
    if semantic == "txn_type":
        return rng.choice(TXN_TYPES)
    if semantic == "acct_type":
        return rng.choice(ACCT_TYPES)
    if semantic == "pay_method":
        return rng.choice(PAY_METHODS)
    if semantic == "date":
        return _fmt_date(rng, features.get("oracle_date_format", True))
    if semantic == "amount":
        return round(rng.uniform(10, 25000), 2)
    if semantic == "amount_signed":
        return round(rng.uniform(-8000, 12000), 2)
    if semantic == "qty":
        return rng.randint(1, 500)
    if semantic == "qty_signed":
        return rng.randint(-200, 400)
    if semantic == "rating":
        return rng.randint(1, 5)
    if semantic == "flag":
        return rng.choice(["Y", "N"])
    if semantic == "code":
        return "".join(rng.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(10))
    if semantic == "text":
        txt = " ".join(rng.choice(WORDS) for _ in range(rng.randint(3, 8)))
        if features.get("edge_cases") and rng.random() < 0.1:
            txt += ', includes "quoted, comma" text'
        return txt
    if semantic == "clob":
        return " ".join(rng.choice(WORDS) for _ in range(rng.randint(15, 40))).capitalize() + "."
    raise ValueError(f"unknown semantic {semantic}")


def _ddl_for_table(tname, cols, pk, fks, features):
    lines = [f"CREATE TABLE {tname} ("]
    width = max(len(c[0]) for c in cols) + 2
    col_lines = []
    for cname, otype, _sem, nullable in cols:
        nn = "" if nullable else " NOT NULL"
        col_lines.append(f"  {cname:<{width}}{otype}{nn}")
    cons = [f"  CONSTRAINT pk_{tname} PRIMARY KEY ({pk})"]
    if features.get("foreign_keys", True):
        for col, (ptable, pcol) in fks.items():
            cons.append(
                f"  CONSTRAINT fk_{tname}_{col} FOREIGN KEY ({col}) "
                f"REFERENCES {ptable} ({pcol})")
    if features.get("check_constraints", True):
        for cname, _otype, sem, _n in cols:
            if sem == "rating":
                cons.append(f"  CONSTRAINT ck_{tname}_{cname} CHECK ({cname} BETWEEN 1 AND 5)")
            elif sem == "flag":
                cons.append(f"  CONSTRAINT ck_{tname}_{cname} CHECK ({cname} IN ('Y','N'))")
    lines.append(",\n".join(col_lines + cons))
    lines.append(");")
    return "\n".join(lines)


def _seq_trigger(tname, pk):
    return f"""CREATE SEQUENCE {tname}_seq START WITH 10001 INCREMENT BY 1 NOCACHE;

CREATE OR REPLACE TRIGGER {tname}_bi
  BEFORE INSERT ON {tname}
  FOR EACH ROW
BEGIN
  IF :NEW.{pk} IS NULL THEN
    SELECT {tname}_seq.NEXTVAL INTO :NEW.{pk} FROM dual;
  END IF;
END;
/"""


def _plsql(tables):
    # a procedure and a function that reference real tables/columns of this DB
    proc_target = None
    for tname, cols, pk, _fks in tables:
        for cname, _t, sem, _n in cols:
            if sem in ("amount", "qty") and cname != pk:
                proc_target = (tname, pk, cname)
                break
        if proc_target:
            break
    blocks = []
    if proc_target:
        tname, pk, col = proc_target
        blocks.append(f"""CREATE OR REPLACE PROCEDURE adjust_{col} (
  p_id  IN NUMBER,
  p_pct IN NUMBER
) AS
BEGIN
  UPDATE {tname}
     SET {col} = ROUND({col} * (1 + p_pct / 100), 2)
   WHERE {pk} = p_id;
  IF SQL%ROWCOUNT = 0 THEN
    RAISE_APPLICATION_ERROR(-20001, 'No row found for id ' || p_id);
  END IF;
  COMMIT;
END adjust_{col};
/""")
    tname0 = tables[0][0]
    blocks.append(f"""CREATE OR REPLACE FUNCTION count_{tname0} RETURN NUMBER IS
  v_count NUMBER;
BEGIN
  SELECT COUNT(*) INTO v_count FROM {tname0};
  RETURN v_count;
END count_{tname0};
/""")
    return "\n\n".join(blocks)


def _comments(tname, cols, domain):
    out = [f"COMMENT ON TABLE {tname} IS 'Source: {domain.upper()} module (Oracle 19c export)';"]
    for cname, _t, sem, _n in cols[:2]:
        out.append(f"COMMENT ON COLUMN {tname}.{cname} IS '{sem} field';")
    return "\n".join(out)


def generate_database(root, db_name, domain, seed, cfg_input):
    """Generate one Oracle database export under root/db_name. Returns manifest."""
    rng = random.Random(seed)
    features = dict(cfg_input["oracle_features"])
    lo, hi = cfg_input["tables_per_db"]
    rlo, rhi = cfg_input["rows_per_table"]
    template = DOMAINS[domain]
    n_tables = min(rng.randint(lo, hi), len(template))
    tables = template[:n_tables]  # prefix keeps FK parents present

    db_dir = Path(root) / db_name
    data_dir = db_dir / "source" / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (db_dir / "migrated").mkdir(exist_ok=True)

    # --- data ---
    pk_values = {}
    manifest_tables = []
    table_rows = {}
    for tname, cols, pk, fks in tables:
        n_rows = rng.randint(rlo, rhi)
        rows = []
        for i in range(n_rows):
            row = {}
            for cname, _otype, sem, nullable in cols:
                if sem == "fk":
                    ptable, _pcol = fks[cname]
                    row[cname] = rng.choice(pk_values[ptable])
                elif cname == pk:
                    row[cname] = i + 1
                elif nullable and rng.random() < 0.10:
                    row[cname] = None
                else:
                    row[cname] = _value(rng, sem, i, features)
            rows.append(row)
        pk_values[tname] = [r[pk] for r in rows]
        table_rows[tname] = rows

        with open(data_dir / f"{tname}.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow([c[0] for c in cols])
            for r in rows:
                w.writerow(["" if r[c[0]] is None else r[c[0]] for c in cols])

        # ground-truth checksum on first non-pk numeric column
        checksum_col, checksum = None, None
        for cname, otype, sem, _n in cols:
            if cname != pk and otype.startswith("NUMBER") and sem != "fk":
                vals = [r[cname] for r in rows if r[cname] is not None]
                checksum_col, checksum = cname, round(sum(vals), 2)
                break
        manifest_tables.append({
            "name": tname,
            "columns": [{"name": c[0], "oracle_type": c[1], "nullable": c[3]} for c in cols],
            "row_count": n_rows,
            "checksum_col": checksum_col,
            "checksum": checksum,
        })

    # --- DDL ---
    parts = ["-- Oracle Database 19c schema export",
             f"-- Database: {db_name}   Module: {domain.upper()}",
             "SET DEFINE OFF;", ""]
    for tname, cols, pk, fks in tables:
        parts.append(_ddl_for_table(tname, cols, pk, fks, features))
        parts.append("")
    if features.get("sequences_triggers", True):
        for tname, _cols, pk, _fks in tables:
            parts.append(_seq_trigger(tname, pk))
            parts.append("")
    if features.get("comments", True):
        for tname, cols, _pk, _fks in tables:
            parts.append(_comments(tname, cols, domain))
            parts.append("")
    if features.get("plsql", True):
        parts.append(_plsql(tables))
        parts.append("")
    (db_dir / "source" / "schema.sql").write_text("\n".join(parts))

    manifest = {
        "db_name": db_name,
        "domain": domain,
        "seed": seed,
        "features": features,
        "tables": manifest_tables,
    }
    (db_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest


def generate_fleet(root, cfg):
    """Generate cfg.run.num_databases databases. Returns list of manifests."""
    rng = random.Random(cfg["run"]["seed"])
    domains = cfg["input"]["domains"]
    manifests = []
    for i in range(cfg["run"]["num_databases"]):
        domain = domains[i % len(domains)]
        db_name = f"{domain}_{i + 1:04d}"
        db_seed = rng.randint(0, 2**31)
        manifests.append(generate_database(root, db_name, domain, db_seed, cfg["input"]))
    return manifests
