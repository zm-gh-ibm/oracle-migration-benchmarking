"""Self-contained HTML dashboard for bakeoff results — static and live.

Static: renders results/<run>/results.json into results/<run>/dashboard.html —
a single file with inlined data, no external requests, light/dark aware.

Live: the same page rendered with live=True polls /live.json (served by
bakeoff.live during a run) once a second, showing job status, an agent
activity feed (tool calls / output per contestant), and metrics as they land.

  python -m bakeoff.visualize results/demo
"""
import json
import sys
from pathlib import Path

# Contestant identity colors are fixed by entity (never by rank/order).
# baseline is the reference floor -> de-emphasis gray, not a categorical slot.
_SLOTS = ["claude", "bob", "cursor"]

_HEAD = """<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Oracle → Lakehouse Bakeoff</title>
<link rel="icon" type="image/png" href="data:image/png;base64,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">
<style>
.viz-root {
  color-scheme: light;
  --page:      #f9f9f7;
  --surface-1: #fcfcfb;
  --ink-1:     #0b0b0b;
  --ink-2:     #52514e;
  --muted:     #898781;
  --grid:      #e1e0d9;
  --baseline:  #c3c2b7;
  --border:    rgba(11,11,11,0.10);
  --s1: #2a78d6;  /* blue  — bob */
  --s2: #eb6834;  /* orange — claude */
  --s3: #1baf7a;  /* green — cursor (v2) */
  --sgray: #898781;  /* baseline reference */
  --good: #0ca30c;
  --good-text: #006300;
  --critical: #d03b3b;
}
@media (prefers-color-scheme: dark) {
  :root:where(:not([data-theme="light"])) .viz-root {
    color-scheme: dark;
    --page: #0d0d0d; --surface-1: #1a1a19;
    --ink-1: #ffffff; --ink-2: #c3c2b7; --muted: #898781;
    --grid: #2c2c2a; --baseline: #383835; --border: rgba(255,255,255,0.10);
    --s1: #3987e5; --s2: #d95926; --s3: #199e70;
    --good: #0ca30c; --good-text: #0ca30c; --critical: #d03b3b;
  }
}
:root[data-theme="dark"] .viz-root {
  color-scheme: dark;
  --page: #0d0d0d; --surface-1: #1a1a19;
  --ink-1: #ffffff; --ink-2: #c3c2b7; --muted: #898781;
  --grid: #2c2c2a; --baseline: #383835; --border: rgba(255,255,255,0.10);
  --s1: #3987e5; --s2: #d95926; --s3: #199e70;
  --good: #0ca30c; --good-text: #0ca30c; --critical: #d03b3b;
}
.viz-root {
  font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
  background: var(--page); color: var(--ink-1);
  margin: 0; padding: 28px 20px 48px; min-height: 100vh; box-sizing: border-box;
}
.wrap { max-width: 1060px; margin: 0 auto; }
h1 { font-size: 22px; font-weight: 650; margin: 0 0 6px; }
.titlerow { display: flex; align-items: flex-start; justify-content: space-between;
            gap: 14px; }
#runbtn { font: inherit; font-size: 13px; font-weight: 600; padding: 8px 18px;
          border-radius: 8px; border: 1px solid var(--border); cursor: pointer;
          background: var(--s1); color: #fff; white-space: nowrap; }
#runbtn:hover:not(:disabled) { filter: brightness(1.1); }
#runbtn:disabled { background: var(--surface-1); color: var(--muted);
                   cursor: default; }
.meta { color: var(--ink-2); font-size: 13px; display: flex; flex-wrap: wrap;
        gap: 6px 14px; margin-bottom: 22px; }
.meta b { color: var(--ink-1); font-weight: 600; }
.kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
        gap: 12px; margin-bottom: 22px; }
.tile { background: var(--surface-1); border: 1px solid var(--border);
        border-radius: 10px; padding: 14px 16px; }
.tile .lab { font-size: 12px; color: var(--ink-2); margin-bottom: 4px; }
.tile .val { font-size: 26px; font-weight: 600; }
.tile .sub { font-size: 11.5px; color: var(--muted); margin-top: 3px; }
.legend { display: flex; gap: 18px; align-items: center; font-size: 13px;
          color: var(--ink-2); margin: 0 2px 12px; flex-wrap: wrap; }
.legend .key { display: inline-flex; align-items: center; gap: 7px; }
.sw { width: 12px; height: 12px; border-radius: 3px; display: inline-block; }
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
         gap: 12px; margin-bottom: 22px; }
.card { background: var(--surface-1); border: 1px solid var(--border);
        border-radius: 10px; padding: 16px 18px 14px; }
.card h2 { font-size: 13.5px; font-weight: 600; margin: 0 0 2px; }
.card .note { font-size: 11.5px; color: var(--muted); margin: 0 0 12px; }
.brow { display: grid; grid-template-columns: 76px 1fr; align-items: center;
        gap: 10px; padding: 5px 0; border-radius: 6px; }
.brow:hover { background: color-mix(in srgb, var(--grid) 45%, transparent); }
.rlabel { font-size: 12.5px; color: var(--ink-2); text-align: right;
          overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.btrack { display: flex; align-items: center; gap: 8px; min-height: 20px;
          border-left: 1px solid var(--baseline); padding-left: 1px; }
.bar { height: 18px; border-radius: 0 4px 4px 0; min-width: 2px;
       transition: filter .1s; }
.brow:hover .bar { filter: brightness(1.12); }
.rval { font-size: 12px; color: var(--ink-1); font-weight: 550; white-space: nowrap; }
.rna  { font-size: 12px; color: var(--muted); font-style: italic; }
.matrix { display: grid; gap: 2px; font-size: 12.5px; }
.mhead { color: var(--ink-2); font-weight: 600; padding: 6px 10px; }
.mrow-label { color: var(--ink-2); padding: 10px; display: flex; align-items: center; }
.cell { border-radius: 6px; padding: 8px 10px; display: flex; gap: 8px;
        align-items: center; background: color-mix(in srgb, var(--grid) 35%, transparent); }
.cell .ic { font-size: 14px; font-weight: 700; }
.cell.ok .ic { color: var(--good-text); }
.cell.bad .ic { color: var(--critical); }
.cell .ct { color: var(--ink-2); font-size: 11.5px; }
.cell:hover { outline: 1px solid var(--baseline); }
.tablecard { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; font-size: 12.5px; }
th { text-align: left; color: var(--ink-2); font-weight: 600; padding: 7px 10px;
     border-bottom: 1px solid var(--grid); white-space: nowrap; }
td { padding: 7px 10px; border-bottom: 1px solid var(--grid); white-space: nowrap;
     font-variant-numeric: tabular-nums; }
tr:last-child td { border-bottom: none; }
.okword { color: var(--good-text); font-weight: 600; }
.badword { color: var(--critical); font-weight: 600; }
.foot { color: var(--muted); font-size: 12px; line-height: 1.55; margin-top: 18px; }
#tip { position: fixed; pointer-events: none; background: var(--surface-1);
       border: 1px solid var(--border); border-radius: 8px; padding: 8px 11px;
       font-size: 12px; box-shadow: 0 4px 14px rgba(0,0,0,.14); display: none;
       z-index: 10; max-width: 260px; }
#tip .tv { font-weight: 650; font-size: 13px; }
#tip .tl { color: var(--ink-2); margin-top: 1px; }

/* ---- live activity feed ---- */
#livedot { width: 9px; height: 9px; border-radius: 50%; display: inline-block;
           background: var(--good); margin-left: 7px; vertical-align: 1px;
           animation: pulse 1.4s ease-in-out infinite; }
#livedot.done { background: var(--muted); animation: none; }
@keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: .25; } }
.feedcols { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
            gap: 12px; }
.feedcol { border: 1px solid var(--grid); border-radius: 8px; padding: 10px 12px;
           min-width: 0; }
.feedcol .fhead { display: flex; align-items: center; gap: 7px; font-size: 13px;
                  font-weight: 600; margin-bottom: 7px; }
.chips { display: flex; flex-wrap: wrap; gap: 5px; margin-bottom: 8px; }
.chip { font-size: 11px; padding: 2px 8px; border-radius: 999px;
        border: 1px solid var(--grid); color: var(--muted);
        font-variant-numeric: tabular-nums; }
.chip.running { color: var(--ink-1); border-color: currentColor;
                animation: pulse 1.4s ease-in-out infinite; }
.chip.pass { color: var(--good-text); border-color: color-mix(in srgb, var(--good) 45%, transparent); }
.chip.fail { color: var(--critical); border-color: color-mix(in srgb, var(--critical) 45%, transparent); }
.feed { max-height: 300px; overflow-y: auto; font-size: 11.5px; line-height: 1.5;
        font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.fe { display: flex; gap: 7px; padding: 1.5px 0; align-items: baseline; }
.fe .et { color: var(--muted); flex: none; width: 44px; text-align: right;
          font-variant-numeric: tabular-nums; }
.fe .edb { color: var(--muted); flex: none; max-width: 72px; overflow: hidden;
           text-overflow: ellipsis; white-space: nowrap; }
.fe .etx { min-width: 0; overflow-wrap: anywhere; color: var(--ink-2); }
.fe.tool .etx { color: var(--ink-1); }
.fe.status .etx { font-style: italic; color: var(--muted); }
.fe.data .etx { color: var(--good-text); }
.fe.issue .etx { color: var(--critical); }

/* ---- data flow lanes (source -> target) ---- */
.lane { margin-bottom: 12px; }
.lane:last-child { margin-bottom: 2px; }
.lanehead { display: flex; align-items: center; gap: 7px; font-size: 12.5px;
            font-weight: 600; margin-bottom: 5px; }
.lanehead .lanerows { margin-left: auto; color: var(--ink-2); font-weight: 500;
                      font-variant-numeric: tabular-nums; font-size: 12px; }
.lanegrid { display: grid; grid-template-columns: 92px 1fr 92px; gap: 8px;
            align-items: center; }
.dbnode { border: 1px solid var(--grid); border-radius: 8px; padding: 5px 8px;
          font-size: 11.5px; text-align: center; color: var(--ink-2);
          background: color-mix(in srgb, var(--grid) 30%, transparent);
          white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.pipe { position: relative; height: 14px; border-radius: 7px; overflow: hidden;
        background: color-mix(in srgb, var(--grid) 45%, transparent); }
.pipe .fill { position: absolute; top: 0; bottom: 0; left: 0; border-radius: 7px;
              opacity: .35; transition: width .8s ease; }
.pipe .dot { position: absolute; top: 3px; width: 8px; height: 8px;
             border-radius: 50%; animation: flowdot 1.5s linear infinite; }
@keyframes flowdot { from { left: -4%; opacity: 0 } 12% { opacity: 1 }
                     88% { opacity: 1 } to { left: 100%; opacity: 0 } }
.fmini { height: 5px; border-radius: 3px; overflow: hidden;
         background: color-mix(in srgb, var(--grid) 55%, transparent);
         display: inline-block; }
.fminifill { display: block; height: 100%; border-radius: 3px;
             transition: width .15s linear; }
.ftag { border: 1px solid var(--grid); border-radius: 4px; padding: 0 6px;
        font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
        font-size: 10.5px; color: var(--ink-2);
        animation: fmove 1.4s ease-in-out infinite; }
@keyframes fmove { 0%, 100% { transform: translateX(0); opacity: .7 }
                   50% { transform: translateX(7px); opacity: 1 } }
/* full expansion: every table of every database, per contestant */
.dbgroups { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 8px 14px; margin: 8px 100px 2px; }
.dbg { border: 1px solid var(--grid); border-radius: 8px; padding: 8px 10px 6px; }
.dbgname { display: flex; justify-content: space-between; gap: 8px;
           font-size: 11.5px; font-weight: 600; color: var(--ink-2);
           margin-bottom: 4px; }
.dbgname .dstat { font-weight: 500; color: var(--muted);
                  font-variant-numeric: tabular-nums; white-space: nowrap; }
.trow { display: grid; grid-template-columns: minmax(88px, 138px) 1fr 76px;
        gap: 8px; align-items: center; padding: 2.5px 0; font-size: 11px; }
.trow .tname { font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
               color: var(--ink-2); overflow: hidden; text-overflow: ellipsis;
               white-space: nowrap; }
.trow .fmini { width: 100%; }
.trow .tcount { text-align: right; color: var(--muted);
                font-variant-numeric: tabular-nums; white-space: nowrap; }
.trow.pending { opacity: .5; }
.trow.created .tname { color: var(--ink-1); }
.trow.created .fmini { outline: 1px solid color-mix(in srgb, var(--baseline) 70%, transparent); }
.trow.streaming .tname { color: var(--ink-1); font-weight: 600; }
.trow.streaming .fminifill { animation: pulse 1.2s ease-in-out infinite; }
.trow.loaded .tcount { color: var(--good-text); }
.trow.short .tcount { color: var(--critical); }
.tfields { display: flex; flex-wrap: wrap; gap: 4px; margin: 1px 0 4px; }
.sqlbox { margin-top: 5px; border-top: 1px dashed var(--grid); padding-top: 4px;
          font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
          font-size: 10.5px; line-height: 1.5; color: var(--muted); }
.sqlline { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.sqlline .sk { font-weight: 700; margin-right: 4px; }
.sqlline.ok .sk { color: var(--good-text); }
.sqlline.err { color: var(--critical); }
.fe.sql .etx { color: var(--ink-2); font-style: italic; }
.fe.phase .etx { color: var(--ink-1); font-weight: 700; }
.fe.phase { border-top: 1px dashed var(--grid); margin-top: 3px; padding-top: 3px; }
.planbox { border: 1px dashed var(--grid); border-radius: 6px; padding: 6px 9px;
           margin: 2px 0 7px; font-size: 11px; color: var(--ink-2); }
.planbox .ph { font-weight: 650; color: var(--ink-1); margin-bottom: 3px; }
.planbox .pl { padding: 0.5px 0; white-space: nowrap; overflow: hidden;
               text-overflow: ellipsis; }
.planbtn { cursor: pointer; font: inherit; font-size: 10.5px; margin-left: 8px;
           border: 1px solid var(--grid); background: none; color: var(--ink-2);
           border-radius: 5px; padding: 1px 8px; }
.planbtn:hover { color: var(--ink-1); border-color: var(--baseline); }
/* plan document popup — lives outside the re-rendered containers, so live
   polling never resets its scroll position */
#planmodal { position: fixed; inset: 0; z-index: 50; display: none;
             align-items: center; justify-content: center; padding: 4vh 4vw;
             background: rgba(0,0,0,.45); }
#planmodal.open { display: flex; }
#pm-panel { background: var(--surface-1); border: 1px solid var(--border);
            border-radius: 12px; max-width: 860px; width: 100%;
            max-height: 88vh; display: flex; flex-direction: column;
            box-shadow: 0 12px 40px rgba(0,0,0,.35); }
#pm-head { display: flex; justify-content: space-between; align-items: center;
           gap: 12px; padding: 12px 16px; border-bottom: 1px solid var(--grid);
           font-weight: 650; font-size: 13.5px; }
#pm-close { cursor: pointer; border: none; background: none; font: inherit;
            color: var(--ink-2); font-size: 16px; padding: 2px 6px; }
#pm-close:hover { color: var(--ink-1); }
#pm-body { overflow: auto; padding: 12px 16px 16px; white-space: pre-wrap;
           font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
           font-size: 11.5px; line-height: 1.55; color: var(--ink-2); }
@media (max-width: 760px) { .dbgroups { margin: 8px 0 2px; } }

/* ---- quality findings ---- */
.qgroup { margin-bottom: 10px; }
.qgroup:last-child { margin-bottom: 0; }
.qhead { display: flex; align-items: center; gap: 7px; font-size: 12.5px;
         font-weight: 600; margin-bottom: 3px; }
.qhead .qcount { margin-left: auto; font-weight: 500; font-size: 11.5px;
                 color: var(--muted); }
.qrow { display: flex; gap: 8px; font-size: 12px; padding: 2.5px 0;
        align-items: baseline; }
.sev { flex: none; font-size: 10.5px; font-weight: 700; border-radius: 4px;
       padding: 1px 6px; text-transform: uppercase; letter-spacing: .03em; }
.sev.error { color: var(--critical);
             background: color-mix(in srgb, var(--critical) 12%, transparent); }
.sev.warn { color: var(--ink-2);
            background: color-mix(in srgb, var(--grid) 60%, transparent); }
.qrow .qphase { flex: none; color: var(--muted); font-size: 11px; width: 46px; }
.qrow .qmsg { color: var(--ink-2); overflow-wrap: anywhere; }
.qclean { color: var(--good-text); font-size: 12.5px; }
</style>"""

_BODY = """<div class="viz-root"><div class="wrap">
  <div class="titlerow">
    <h1>Oracle → Lakehouse Migration Bakeoff</h1>
    <button id="runbtn" style="display:none">▶ Run bakeoff</button>
  </div>
  <div class="meta" id="meta"></div>
  <div class="kpis" id="kpis"></div>
  <div class="legend" id="legend"></div>
  <div class="card" id="flowcard" style="margin-bottom:22px; display:none">
    <h2>Data flow — Oracle → target</h2>
    <p class="note">agent SQL is shadow-executed against the target the moment it is written: tables appear on CREATE (◻), rows stream in live (⇢), and the console below each database shows the exact statements hitting Postgres</p>
    <div id="lanes"></div>
  </div>
  <div class="card" id="livecard" style="margin-bottom:22px; display:none">
    <h2>Agent activity<span id="livedot" style="display:none"></span></h2>
    <p class="note" id="livenote">tool calls and operations per contestant, streamed as they happen</p>
    <div class="feedcols" id="feedcols"></div>
  </div>
  <div class="card" id="qualitycard" style="margin-bottom:22px; display:none">
    <h2>Testing layer — agent mistakes</h2>
    <p class="note">dev-phase checks (source tampering, crashes, Oracle syntax spotted while working) + final-output checks (leftover Oracle-isms, dropped constraints, missing or undocumented pieces)</p>
    <div id="quality"></div>
  </div>
  <div class="cards" id="cards"></div>
  <div class="card" id="matrixcard" style="margin-bottom:22px; display:none">
    <h2>Migration success matrix</h2>
    <p class="note" id="matrixnote"></p>
    <div class="matrix" id="matrix"></div>
  </div>
  <div class="card tablecard" id="jobscard" style="display:none">
    <h2>All jobs — table view</h2>
    <p class="note">every metric, per (contestant × database)</p>
    <table id="jobs"></table>
  </div>
  <p class="foot" id="foot"></p>
</div>
<div id="planmodal">
  <div id="pm-panel">
    <div id="pm-head"><span id="pm-title"></span><button id="pm-close">✕</button></div>
    <div id="pm-body"></div>
  </div>
</div>
</div>
<div id="tip"></div>
<script>
const EMBED = __DATA__;
const LIVE = __LIVE__;
const SLOT_VARS = { claude: "--s2", bob: "--s1", cursor: "--s3" };
const colorVar = c => SLOT_VARS[c] || "--sgray";
const css = v => getComputedStyle(document.querySelector(".viz-root")).getPropertyValue(v).trim();

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}
const byId = id => document.getElementById(id);
const fmtInt = n => n == null ? "—" : n.toLocaleString("en-US");
const fmtSec = s => s == null ? "—" : (s < 0.01 ? "<0.01s" : s < 1 ? s.toFixed(3) + "s" : s >= 100 ? Math.round(s) + "s" : s.toFixed(1) + "s");
const fmtUsd = c => c == null ? null : "$" + c.toFixed(2);
const fmtTok = t => t == null ? "—" : t >= 1000 ? (t / 1000).toFixed(1) + "K" : String(t);

// ---- tooltip ----
const tip = byId("tip");
function showTip(evt, value, label) {
  tip.replaceChildren(el("div", "tv", value), el("div", "tl", label));
  tip.style.display = "block";
  const pad = 14, w = tip.offsetWidth, h = tip.offsetHeight;
  let x = evt.clientX + pad, y = evt.clientY + pad;
  if (x + w > innerWidth - 8) x = evt.clientX - w - pad;
  if (y + h > innerHeight - 8) y = evt.clientY - h - pad;
  tip.style.left = x + "px"; tip.style.top = y + "px";
}
function hideTip() { tip.style.display = "none"; }
function hoverable(node, value, label) {
  node.tabIndex = 0;
  node.addEventListener("pointermove", e => showTip(e, value, label));
  node.addEventListener("pointerleave", hideTip);
  node.addEventListener("focus", () => {
    const r = node.getBoundingClientRect();
    showTip({ clientX: r.right, clientY: r.top }, value, label);
  });
  node.addEventListener("blur", hideTip);
}

function contestantOrder(DATA) {
  const seen = [];
  const add = c => { if (c && !seen.includes(c)) seen.push(c); };
  Object.keys(DATA.summary || {}).forEach(add);
  (DATA.jobs || []).forEach(j => add(j.contestant));
  (DATA.results || []).forEach(r => add(r.contestant));
  seen.sort((a, b) => {
    const rank = c => c === "baseline" ? 99 : (Object.keys(SLOT_VARS).indexOf(c) + 1 || 50);
    return rank(a) - rank(b);
  });
  return seen;
}

function renderMeta(DATA) {
  const m = byId("meta");
  m.replaceChildren();
  const run = DATA.run || {};
  const pairs = [["Run", run.name], ["Target", run.target],
                 ["Phases", run.planning_phase === false ? "migrate only"
                            : "1 plan → 2 migrate"],
                 ["Databases", String(run.num_databases)], ["Seed", String(run.seed)]];
  if (DATA.live) pairs.push(["Status",
    DATA.idle ? "idle — press ▶ Run bakeoff to start"
              : DATA.previous_run ? "showing last run — press ▶ to run again"
              : DATA.done ? "finished"
              : "running — " + Math.round(DATA.elapsed_s) + "s elapsed"]);
  else pairs.push(["Generated", DATA.generated_at || ""]);
  for (const [k, v] of pairs) {
    const s = el("span"); s.append(el("b", "", k + ": "), document.createTextNode(v ?? "—"));
    m.append(s);
  }
  const feats = Object.entries((DATA.input || {}).oracle_features || {})
    .filter(([, on]) => on).map(([k]) => k).join(", ");
  if (feats) {
    const s = el("span"); s.append(el("b", "", "Oracle features: "), document.createTextNode(feats));
    m.append(s);
  }
}

function renderKpis(DATA, order) {
  const k = byId("kpis");
  k.replaceChildren();
  const totalJobs = (DATA.jobs && DATA.jobs.length) || DATA.results.length;
  const done = DATA.results.length;
  const ok = DATA.results.filter(r => r.validation.success).length;
  const planTime = DATA.results.reduce((a, r) => a + ((r.plan || {}).agent?.wall_time_s || 0), 0);
  const agentTime = DATA.results.reduce((a, r) => a + (r.agent.wall_time_s || 0), 0) + planTime;
  const costs = DATA.results.map(r => r.agent.cost_usd).filter(c => c != null);
  const planCosts = DATA.results.map(r => (r.plan || {}).agent?.cost_usd).filter(c => c != null);
  const spend = costs.reduce((a, c) => a + c, 0) + planCosts.reduce((a, c) => a + c, 0);
  const costGap = order.filter(c => (DATA.summary[c] || {}).cost_usd == null && c !== "baseline");
  function tile(lab, val, sub) {
    const t = el("div", "tile");
    t.append(el("div", "lab", lab), el("div", "val", val));
    if (sub) t.append(el("div", "sub", sub));
    k.append(t);
  }
  const dbCount = (DATA.run || {}).num_databases;
  if (DATA.live && !DATA.done)
    tile("Jobs completed", done + "/" + totalJobs, order.length + " contestants × " + dbCount + " databases");
  else
    tile("Migration jobs", String(totalJobs), order.length + " contestants × " + dbCount + " databases");
  tile("Fully successful", ok + "/" + (done || totalJobs), done ? Math.round(100 * ok / done) + "% of finished jobs passed every check" : "waiting for first result");
  tile("Total agent time", agentTime >= 120 ? (agentTime / 60).toFixed(1) + " min" : Math.round(agentTime) + "s", "wall clock across both phases");
  tile("Total spend", fmtUsd(spend) || "—", costGap.length ? "both phases; reported costs only — " + costGap.join(", ") + " reports none" : "planning + migration");

  // cost-normalized ROI: successful migrations per dollar spent, per contestant
  const roiRows = order.filter(c => c !== "baseline").map(c => {
    const s = DATA.summary[c] || {};
    const totalCost = (s.cost_usd || 0) + (s.plan_cost_usd || 0);
    const wins = DATA.results.filter(r => r.contestant === c && r.validation.success).length;
    return { label: c, wins, cost: totalCost };
  }).filter(r => r.cost > 0);
  if (roiRows.length) {
    const best = roiRows.reduce((a, r) => (r.wins / r.cost > a.wins / a.cost ? r : a), roiRows[0]);
    const roiStr = roiRows.map(r =>
      r.label + ": " + r.wins + " win" + (r.wins !== 1 ? "s" : "") + " / $" + r.cost.toFixed(2)
    ).join(" · ");
    tile("Best ROI", best.label,
      roiStr + " — successful migrations per dollar (baseline excluded, free)");
  }

  const withQ = DATA.results.filter(r => r.quality);
  if (withQ.length) {
    const qe = withQ.reduce((a, r) => a + r.quality.errors, 0);
    const qw = withQ.reduce((a, r) => a + r.quality.warnings, 0);
    tile("Agent mistakes", String(qe), qw + " warning(s) — testing layer, see findings below");
  }
}

function renderLegend(order) {
  const lg = byId("legend");
  lg.replaceChildren();
  if (!order.length) return;
  lg.append(el("span", "", "Contestants:"));
  for (const c of order) {
    const kkey = el("span", "key");
    const sw = el("span", "sw"); sw.style.background = css(colorVar(c));
    kkey.append(sw, document.createTextNode(c + (c === "baseline" ? " (rule-based floor)" : "")));
    lg.append(kkey);
  }
}

// ---- data flow lanes ----
function deriveTables(r) {
  return (r.validation.tables || []).map(t => ({
    name: t.name, rows: t.row_count || 0, total: t.row_count || 0,
    fields: [], status: t.created ? "loaded" : "pending" }));
}
function planDocFrom(r) {
  if (!r || !r.plan || !r.plan.exists) return null;
  const text = r.plan.text || null;
  let excerpt = [];
  if (text) {
    const heads = text.split("\\n").map(l => l.trim()).filter(l => l.startsWith("#"));
    excerpt = heads.length >= 3 ? heads.slice(0, 12)
            : text.split("\\n").filter(l => l.trim()).slice(0, 8);
  }
  return { lines: r.plan.lines, excerpt, text };
}
function jobsFor(DATA) {
  const byKey = {};
  for (const r of DATA.results || []) byKey[r.contestant + "|" + r.db] = r;
  if (DATA.jobs && DATA.jobs.length)
    // backfill tables and plan docs from results when jobs predate them
    return DATA.jobs.map(j => {
      const r = byKey[j.contestant + "|" + j.db];
      const out = { ...j };
      if (!out.tables || !out.tables.length) out.tables = r ? deriveTables(r) : [];
      if (!out.plan_doc || !out.plan_doc.text) out.plan_doc = (r && planDocFrom(r)) || out.plan_doc || null;
      return out;
    });
  // older results.json with no jobs at all
  return (DATA.results || []).map(r => ({
    contestant: r.contestant, db: r.db, status: "done",
    success: r.validation.success,
    rows_expected: r.validation.rows_expected,
    rows_moved: r.validation.rows_loaded || 0,
    tables: deriveTables(r), plan_doc: planDocFrom(r) }));
}
let lastData = null;

// ---- plan document popup ----
const planModal = byId("planmodal");
function openPlanModal(title, text) {
  byId("pm-title").textContent = title;
  byId("pm-body").textContent = text;
  byId("pm-body").scrollTop = 0;
  planModal.classList.add("open");
}
function closePlanModal() { planModal.classList.remove("open"); }
byId("pm-close").addEventListener("click", closePlanModal);
planModal.addEventListener("click", e => { if (e.target === planModal) closePlanModal(); });
addEventListener("keydown", e => { if (e.key === "Escape") closePlanModal(); });

function renderFlow(DATA, order) {
  const card = byId("flowcard");
  const jobs = jobsFor(DATA);
  if (!jobs.length) { card.style.display = "none"; return; }
  card.style.display = "";
  const lanes = byId("lanes");
  lanes.replaceChildren();
  const targetName = (DATA.run || {}).target || "target";
  for (const c of order) {
    const mine = jobs.filter(j => j.contestant === c);
    if (!mine.length) continue;
    const moved = mine.reduce((a, j) => a + (j.rows_moved || 0), 0);
    const total = mine.reduce((a, j) => a + (j.rows_expected || 0), 0);
    const active = DATA.live && !DATA.done && mine.some(j => j.status === "running");
    const color = css(colorVar(c));

    const lane = el("div", "lane");
    const head = el("div", "lanehead");
    const sw = el("span", "sw"); sw.style.background = color;
    head.append(sw, document.createTextNode(c));
    head.append(el("span", "lanerows",
      total ? fmtInt(moved) + " / " + fmtInt(total) + " rows moved"
            : fmtInt(moved) + " rows moved"));
    lane.append(head);

    const grid = el("div", "lanegrid");
    grid.append(el("div", "dbnode", "🏛 Oracle"));
    const pipe = el("div", "pipe");
    const fill = el("div", "fill");
    fill.style.background = color;
    fill.style.width = (total ? Math.min(100, 100 * moved / total) : 0) + "%";
    pipe.append(fill);
    if (active) {
      for (let i = 0; i < 3; i++) {
        const dot = el("div", "dot");
        dot.style.background = color;
        dot.style.animationDelay = (i * 0.5) + "s";
        pipe.append(dot);
      }
    }
    grid.append(pipe);
    const ticon = { postgres: "🐘", snowflake: "❄" }[targetName] || "🗄";
    grid.append(el("div", "dbnode", ticon + " " + targetName));
    lane.append(grid);

    // full expansion: every database, every table, individually
    const groups = el("div", "dbgroups");
    for (const j of mine) {
      if (!j.tables || !j.tables.length) continue;
      const g = el("div", "dbg");
      const head = el("div", "dbgname");
      head.append(el("span", "", j.db));
      const st = j.status === "done" ? (j.success ? "✓ complete" : "✗ failed")
               : j.status === "running"
                 ? (j.phase === "plan" ? "phase 1 · planning" : "phase 2 · migrating")
               : "waiting";
      head.append(el("span", "dstat",
        fmtInt(j.rows_moved || 0) + " / " + fmtInt(j.rows_expected || 0) +
        " rows · " + st));
      g.append(head);
      // phase-1 output: the migration plan this contestant produced
      if (j.plan_doc) {
        const pb = el("div", "planbox");
        const ph = el("div", "ph", "📋 Phase-1 plan — " + j.plan_doc.lines +
          " lines" + (j.phase === "migrate" && j.status === "running"
                      ? " · now being executed" : ""));
        if (j.plan_doc.text) {
          const btn = el("button", "planbtn", "view full plan ⧉");
          const title = c + " × " + j.db + " — migration plan (" +
                        j.plan_doc.lines + " lines)";
          const text = j.plan_doc.text;
          btn.addEventListener("click", () => openPlanModal(title, text));
          ph.append(btn);
        }
        pb.append(ph);
        for (const ln of (j.plan_doc.excerpt || []).slice(0, 8))
          pb.append(el("div", "pl", ln));
        g.append(pb);
      } else if (j.status === "running" && j.phase === "plan") {
        const pb = el("div", "planbox");
        pb.append(el("div", "ph", "📋 Phase 1 — plan being written…"));
        g.append(pb);
      }
      for (const t of j.tables) {
        const short = t.status === "loaded" && t.total && t.rows < t.total;
        const row = el("div", "trow " + t.status + (short ? " short" : ""));
        row.append(el("span", "tname",
          t.name + (t.cols != null ? " (" + t.cols + " cols)" : "")));
        const bar = el("span", "fmini");
        const mf = el("span", "fminifill");
        mf.style.background = color;
        mf.style.width = (t.total ? Math.min(100, 100 * t.rows / t.total) : 0) + "%";
        bar.append(mf);
        row.append(bar);
        const mark = t.status === "streaming" ? " ⇢"
                   : t.status === "created" ? " ◻"
                   : t.status === "loaded" ? (short ? " ✗" : " ✓") : "";
        row.append(el("span", "tcount",
          fmtInt(t.rows) + "/" + fmtInt(t.total) + mark));
        hoverable(row, fmtInt(t.rows) + " / " + fmtInt(t.total) + " rows",
          c + " · " + j.db + "." + t.name +
          (t.fields && t.fields.length ? " — " + t.fields.join(", ") : ""));
        g.append(row);
        // the actively-streaming table shows its fields moving
        if (t.status === "streaming" && t.fields && t.fields.length) {
          const f = el("div", "tfields");
          t.fields.forEach((name, i) => {
            const tag = el("span", "ftag", name);
            tag.style.animationDelay = (i * 0.12) + "s";
            f.append(tag);
          });
          g.append(f);
        }
      }
      // live SQL console: the statements actually executed against the target
      if (j.sql && j.sql.length) {
        const box = el("div", "sqlbox");
        for (const s of j.sql.slice(-6)) {
          const line = el("div", "sqlline " + (s.ok ? "ok" : "err"));
          line.append(el("span", "sk", (s.ok ? "✓" : "✗") + " " + s.kind));
          line.append(document.createTextNode(
            s.text + (s.error ? " — " + s.error : "")));
          line.title = s.text + (s.error ? " | " + s.error : "");
          box.append(line);
        }
        g.append(box);
      }
      groups.append(g);
    }
    lane.append(groups);
    hoverable(lane, fmtInt(moved) + (total ? " / " + fmtInt(total) : "") + " rows",
      c + " — rows loaded into " + targetName + " across " + mine.length + " database(s)");
    lanes.append(lane);
  }
}

// ---- quality findings (testing layer) ----
function renderQuality(DATA, order) {
  const card = byId("qualitycard");
  const withQ = (DATA.results || []).filter(r => r.quality);
  if (!withQ.length) { card.style.display = "none"; return; }
  card.style.display = "";
  const box = byId("quality");
  box.replaceChildren();
  const ex = Object.entries(DATA.quality_exempt || {})
    .filter(([, checks]) => checks && checks.length)
    .map(([c, checks]) => c + ": " + checks.join(", "));
  const note = card.querySelector(".note");
  const base = note.textContent.split("  Exempt")[0];
  note.textContent = base + (ex.length ? "  Exemptions — " + ex.join(" · ") : "");
  for (const c of order) {
    const mine = withQ.filter(r => r.contestant === c);
    if (!mine.length) continue;
    const errs = mine.reduce((a, r) => a + r.quality.errors, 0);
    const warns = mine.reduce((a, r) => a + r.quality.warnings, 0);
    const group = el("div", "qgroup");
    const head = el("div", "qhead");
    const sw = el("span", "sw"); sw.style.background = css(colorVar(c));
    head.append(sw, document.createTextNode(c),
      el("span", "qcount", errs + " error(s) · " + warns + " warning(s)"));
    group.append(head);
    let any = false;
    for (const r of mine) {
      for (const f of r.quality.findings) {
        any = true;
        const row = el("div", "qrow");
        row.append(el("span", "sev " + f.severity, f.severity),
                   el("span", "qphase", f.phase),
                   el("span", "qmsg", r.db + " — " + f.message));
        group.append(row);
      }
    }
    if (!any) group.append(el("div", "qclean",
      "✓ no mistakes detected across " + mine.length + " job(s)"));
    box.append(group);
  }
}

// ---- live activity feed ----
const FEED_CAP = 80;
function renderFeed(DATA, order) {
  const card = byId("livecard");
  const events = DATA.events || [];
  const jobs = DATA.jobs || [];
  if (!events.length && !jobs.length) { card.style.display = "none"; return; }
  card.style.display = "";
  const dot = byId("livedot");
  dot.style.display = DATA.live ? "" : "none";
  dot.classList.toggle("done", !!DATA.done);
  if (DATA.live) {
    const running = jobs.filter(j => j.status === "running").length;
    const doneN = jobs.filter(j => j.status === "done").length;
    byId("livenote").textContent = DATA.done
      ? "run finished — " + events.length + " events captured"
      : doneN + "/" + jobs.length + " jobs done · " + running + " running · " +
        events.length + " events · live";
  }
  const cols = byId("feedcols");
  cols.replaceChildren();
  for (const c of order) {
    const col = el("div", "feedcol");
    const head = el("div", "fhead");
    const sw = el("span", "sw"); sw.style.background = css(colorVar(c));
    head.append(sw, document.createTextNode(c));
    col.append(head);
    const myJobs = jobs.filter(j => j.contestant === c);
    if (myJobs.length) {
      const chips = el("div", "chips");
      for (const j of myJobs) {
        const cls = j.status === "done" ? (j.success ? "pass" : "fail")
                  : j.status === "running" ? "running" : "";
        const mark = j.status === "done" ? (j.success ? " ✓" : " ✗")
                   : j.status === "running" ? " …" : "";
        chips.append(el("span", "chip " + cls, j.db + mark));
      }
      col.append(chips);
    }
    const feed = el("div", "feed");
    const mine = events.filter(e => e.contestant === c).slice(-FEED_CAP);
    for (const e of mine) {
      const row = el("div", "fe " + (e.kind || "text"));
      row.append(el("span", "et", e.t.toFixed(1) + "s"),
                 el("span", "edb", e.db),
                 el("span", "etx", e.text));
      feed.append(row);
    }
    if (!mine.length) feed.append(el("div", "fe", "— no activity yet —"));
    col.append(feed);
    cols.append(col);
    requestAnimationFrame(() => { feed.scrollTop = feed.scrollHeight; });
  }
}

// ---- bar chart cards ----
function barCard(title, note, rows, fmt) {
  const card = el("div", "card");
  card.append(el("h2", "", title));
  card.append(el("p", "note", note));
  const max = Math.max(...rows.map(r => r.value ?? 0), 1e-9);
  for (const r of rows) {
    const row = el("div", "brow");
    row.append(el("span", "rlabel", r.label));
    const track = el("div", "btrack");
    if (r.value == null) {
      track.append(el("span", "rna", r.na || "not reported"));
      hoverable(row, r.na || "not reported", r.label + " — " + title);
    } else {
      const bar = el("div", "bar");
      bar.style.background = css(colorVar(r.label));
      bar.style.width = Math.max(0.4, 100 * r.value / max * 0.72) + "%";
      track.append(bar, el("span", "rval", fmt(r.value)));
      hoverable(row, fmt(r.value),
        r.label + " — " + title + (r.extra ? " (" + r.extra + ")" : ""));
    }
    row.append(track);
    card.append(row);
  }
  byId("cards").append(card);
}

function renderCards(DATA) {
  const cards = byId("cards");
  cards.replaceChildren();
  const sOrder = contestantOrder({ summary: DATA.summary });
  if (!sOrder.length) return;
  if (sOrder.some(c => DATA.summary[c].plan_lines != null))
    barCard("Phase 1 — planning", "time spent producing the architectural migration plan (hover for size & cost)",
      sOrder.map(c => ({ label: c, value: DATA.summary[c].plan_wall_time_s,
                         extra: fmtInt(DATA.summary[c].plan_lines) + " plan lines, " +
                                (fmtUsd(DATA.summary[c].plan_cost_usd) || "$0") })),
      fmtSec);
  barCard("Wall time (phase 2)", "migration wall clock across all databases (lower is better)",
    sOrder.map(c => ({ label: c, value: DATA.summary[c].wall_time_s })), fmtSec);
  barCard("Cost", "spend in USD — Bob prices in Bobcoins, converted at $0.50/coin (lower is better)",
    sOrder.map(c => ({ label: c, value: DATA.summary[c].cost_usd,
                       extra: DATA.summary[c].coins != null
                         ? DATA.summary[c].coins.toFixed(2) + " coins × $0.50" : null })),
    v => "$" + v.toFixed(2));
  barCard("Lines of SQL produced", "non-blank lines across migrated schema + load scripts",
    sOrder.map(c => ({ label: c, value: DATA.summary[c].lines_of_code })), fmtInt);
  barCard("Output tokens", "tokens generated by the agent (input tokens are not comparable across CLIs — see table)",
    sOrder.map(c => ({ label: c, value: DATA.summary[c].tokens_out, na: "n/a" })), fmtTok);
}

// ---- success matrix ----
function renderMatrix(DATA, order) {
  const card = byId("matrixcard");
  if (!DATA.results.length) { card.style.display = "none"; return; }
  card.style.display = "";
  const CAP = 50;
  const dbs = [...new Set(DATA.results.map(r => r.db))].sort();
  const shown = dbs.slice(0, CAP);
  byId("matrixnote").textContent = dbs.length > shown.length
    ? "showing first " + CAP + " of " + dbs.length + " databases — full data in the table below and results.json"
    : "tables created · rows loaded · checksums, per database";
  const mx = byId("matrix");
  mx.replaceChildren();
  mx.style.gridTemplateColumns = "minmax(110px,150px) repeat(" + order.length + ", 1fr)";
  mx.append(el("div"));
  for (const c of order) mx.append(el("div", "mhead", c));
  const byKey = {};
  for (const r of DATA.results) byKey[r.db + "|" + r.contestant] = r;
  for (const db of shown) {
    mx.append(el("div", "mrow-label", db));
    for (const c of order) {
      const r = byKey[db + "|" + c];
      if (!r) {
        const pend = el("div", "cell");
        pend.append(el("span", "ct", DATA.live && !DATA.done ? "…" : "—"));
        mx.append(pend); continue;
      }
      const v = r.validation;
      const cell = el("div", "cell " + (v.success ? "ok" : "bad"));
      cell.append(el("span", "ic", v.success ? "✓" : "✗"));
      cell.append(el("span", "ct",
        v.tables_ok + "/" + v.tables_expected + " tables · " + fmtInt(v.rows_loaded) + " rows"));
      const errs = (v.schema_errors || []).length + (v.load_errors || []).length;
      hoverable(cell,
        (v.success ? "Success" : "Failed"),
        c + " × " + db + " — checksums " + v.checksums_ok + "/" + v.checksums_expected +
        ", SQL errors " + errs + ", " + fmtSec(r.agent.wall_time_s) +
        (r.agent.cost_usd != null ? ", " + fmtUsd(r.agent.cost_usd) : ""));
      mx.append(cell);
    }
  }
}

// ---- table view ----
function renderTable(DATA, order) {
  const card = byId("jobscard");
  if (!DATA.results.length) { card.style.display = "none"; return; }
  card.style.display = "";
  const tbl = byId("jobs");
  tbl.replaceChildren();
  const heads = ["Database", "Contestant", "Success", "Plan (L/s/$)", "Tables",
                 "Rows", "Checksums", "LOC", "Time", "Cost", "Tokens in",
                 "Tokens out", "Turns", "SQL errors", "QA err/warn"];
  const trh = el("tr");
  for (const h of heads) { const th = el("th", "", h); trh.append(th); }
  tbl.append(trh);
  const rows = [...DATA.results].sort((a, b) =>
    a.db === b.db ? order.indexOf(a.contestant) - order.indexOf(b.contestant)
                  : a.db.localeCompare(b.db));
  for (const r of rows) {
    const v = r.validation, a = r.agent;
    const tr = el("tr");
    tr.append(el("td", "", r.db), el("td", "", r.contestant));
    tr.append(el("td", v.success ? "okword" : "badword", v.success ? "pass" : "fail"));
    const pl = r.plan;
    tr.append(el("td", pl && !pl.exists ? "badword" : "",
      pl ? (pl.exists ? pl.lines + " / " + fmtSec(pl.agent.wall_time_s) + " / " +
                        (pl.agent.cost_usd != null ? "$" + pl.agent.cost_usd.toFixed(2) : "$0")
                      : "no plan!") : "—"));
    tr.append(el("td", "", v.tables_ok + "/" + v.tables_expected));
    tr.append(el("td", "", fmtInt(v.rows_loaded) + "/" + fmtInt(v.rows_expected)));
    tr.append(el("td", "", v.checksums_ok + "/" + v.checksums_expected));
    tr.append(el("td", "", fmtInt(r.loc)), el("td", "", fmtSec(a.wall_time_s)));
    tr.append(el("td", "", a.cost_usd != null ? "$" + a.cost_usd.toFixed(4) : "—"));
    tr.append(el("td", "", fmtInt(a.tokens_in)), el("td", "", fmtInt(a.tokens_out)));
    tr.append(el("td", "", a.turns ?? "—"));
    tr.append(el("td", "", String((v.schema_errors || []).length + (v.load_errors || []).length)));
    const q = r.quality;
    tr.append(el("td", q && q.errors ? "badword" : "",
                 q ? q.errors + " / " + q.warnings : "—"));
    tbl.append(tr);
  }
}

function render(DATA) {
  lastData = DATA;
  DATA.results = DATA.results || [];
  DATA.summary = DATA.summary || {};
  const order = contestantOrder(DATA);
  renderMeta(DATA);
  renderKpis(DATA, order);
  renderLegend(order);
  renderFlow(DATA, order);
  renderFeed(DATA, order);
  renderQuality(DATA, order);
  renderCards(DATA);
  renderMatrix(DATA, order);
  renderTable(DATA, order);
  byId("foot").textContent =
    "Success = every table created with correct columns, every row loaded, all numeric " +
    "checksums matching ground truth, zero SQL errors. baseline is a free rule-based " +
    "converter — the floor an agent must beat. Token counts are as reported by each " +
    "CLI: Claude's input count excludes cached prompt tokens while Bob's includes " +
    "them, so input tokens are not directly comparable. Bob prices work in Bobcoins; " +
    "its USD cost is computed at $0.50 per coin when the CLI reports coins only. " +
    "QA err/warn comes from the testing layer: dev-phase checks while the agent " +
    "works plus final-output checks on the delivered SQL.";
}

// ---- run button (server must be in --serve mode) ----
const runbtn = byId("runbtn");
let runRequested = false;
runbtn.addEventListener("click", async () => {
  runbtn.disabled = true;
  runbtn.textContent = "Starting…";
  runRequested = true;
  try {
    const r = await fetch("/run", { method: "POST" });
    if (!r.ok && r.status !== 409) throw new Error(r.status);
  } catch (e) {
    runRequested = false;
    runbtn.textContent = "▶ Run bakeoff";
    runbtn.disabled = false;
  }
});
function updateRunBtn(state) {
  if (!state.can_run) { runbtn.style.display = "none"; return; }
  runbtn.style.display = "";
  const running = !state.idle && !state.done;
  if (running) {
    runbtn.disabled = true;
    runbtn.textContent = "⏳ Running…";
    runRequested = false;
  } else if (!runRequested) {
    runbtn.disabled = false;
    runbtn.textContent = state.idle ? "▶ Run bakeoff" : "↻ Run again";
  }
}

if (LIVE) {
  let stopped = false, failures = 0, lastFp = null;
  async function tick() {
    try {
      const r = await fetch("/live.json", { cache: "no-store" });
      const state = await r.json();
      failures = 0;
      // only rebuild the DOM when the state actually changed — keeps scroll
      // position stable in expanded plan docs and feeds while idle
      const fp = JSON.stringify([state.done, state.idle, state.previous_run,
        state.elapsed_s, (state.events || []).length,
        (state.results || []).length,
        (state.jobs || []).map(j => j.status + (j.phase || "") +
          (j.rows_moved || 0) + (j.sql || []).length).join(",")]);
      if (fp !== lastFp) { lastFp = fp; render(state); }
      updateRunBtn(state);
      // one-shot mode stops on done; --serve mode keeps polling so the
      // button can start the next run
      if (state.done && !state.can_run) stopped = true;
    } catch (e) {
      if (++failures > 5) {
        byId("livenote").textContent = "live server stopped — reopen results/<run>/dashboard.html for the final report";
        byId("livedot").classList.add("done");
        stopped = true;
      }
    }
    if (!stopped) setTimeout(tick, 300);
  }
  tick();
} else if (EMBED) {
  render(EMBED);
}
</script>"""


def render_dashboard(payload, complete=True, live=False):
    data = "null" if payload is None else json.dumps(payload, default=str)
    body = _BODY.replace("__DATA__", data).replace("__LIVE__", "true" if live else "false")
    if complete:
        return ("<!doctype html>\n<html lang=\"en\">\n<head>\n" + _HEAD
                + "\n</head>\n<body style=\"margin:0\">\n" + body + "\n</body>\n</html>\n")
    return _HEAD + "\n" + body


def render_live_page():
    """Page served by the live HTTP server; data arrives via /live.json polling."""
    return render_dashboard(None, complete=True, live=True)


def write_dashboard(results_dir, complete=True):
    results_dir = Path(results_dir)
    payload = json.loads((results_dir / "results.json").read_text())
    out = results_dir / "dashboard.html"
    out.write_text(render_dashboard(payload, complete))
    return out


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "results/demo"
    print(f"wrote {write_dashboard(target)}")
