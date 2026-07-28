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
<style>
.viz-root {
  /* IBM Carbon (Gray 10 theme) — palette validated for CVD + contrast */
  color-scheme: light;
  --page:      #f4f4f4;
  --surface-1: #ffffff;
  --ink-1:     #161616;
  --ink-2:     #525252;
  --muted:     #6f6f6f;
  --grid:      #e0e0e0;
  --baseline:  #c6c6c6;
  --border:    #e0e0e0;
  --brand:     #0f62fe;  /* IBM Blue 60 */
  --s1: #0f62fe;  /* IBM Blue 60 — bob */
  --s2: #ba4e00;  /* Carbon orange 60 — claude */
  --s3: #198038;  /* Carbon green 60 — cursor */
  --sgray: #8d8d8d;  /* baseline reference (neutral, always direct-labeled) */
  --good: #24a148;
  --good-text: #0e6027;
  --critical: #da1e28;  /* Carbon red 60 */
}
@media (prefers-color-scheme: dark) {
  :root:where(:not([data-theme="light"])) .viz-root {
    /* Carbon Gray 100 theme; same validated hue trio holds on #262626 */
    color-scheme: dark;
    --page: #161616; --surface-1: #262626;
    --ink-1: #f4f4f4; --ink-2: #c6c6c6; --muted: #8d8d8d;
    --grid: #393939; --baseline: #494949; --border: #393939;
    --brand: #4589ff;
    --s1: #0f62fe; --s2: #ba4e00; --s3: #198038;
    --good: #42be65; --good-text: #42be65; --critical: #fa4d56;
  }
}
:root[data-theme="dark"] .viz-root {
  color-scheme: dark;
  --page: #161616; --surface-1: #262626;
  --ink-1: #f4f4f4; --ink-2: #c6c6c6; --muted: #8d8d8d;
  --grid: #393939; --baseline: #494949; --border: #393939;
  --brand: #4589ff;
  --s1: #0f62fe; --s2: #ba4e00; --s3: #198038;
  --good: #42be65; --good-text: #42be65; --critical: #fa4d56;
}
.viz-root {
  font-family: "IBM Plex Sans", system-ui, -apple-system, "Segoe UI", sans-serif;
  background: var(--page); color: var(--ink-1);
  margin: 0; padding: 28px 20px 48px; min-height: 100vh; box-sizing: border-box;
}
.wrap { max-width: 1060px; margin: 0 auto; }
h1 { font-size: 26px; font-weight: 400; margin: 0 0 6px; letter-spacing: 0; }
.titlerow { display: flex; align-items: flex-start; justify-content: space-between;
            gap: 14px; padding-top: 10px; border-top: 3px solid var(--brand); }
#runbtn { font: inherit; font-size: 14px; font-weight: 400; padding: 10px 18px;
          border-radius: 0; border: 1px solid transparent; cursor: pointer;
          background: #0f62fe; color: #fff; white-space: nowrap; }
#runbtn:hover:not(:disabled) { background: #0353e9; }
#runbtn:disabled { background: var(--surface-1); color: var(--muted);
                   cursor: default; }
#cancelbtn { font: inherit; font-size: 13px; font-weight: 600; padding: 8px 14px;
             border-radius: 8px; border: 1px solid var(--critical);
             cursor: pointer; background: transparent; color: var(--critical);
             white-space: nowrap; }
#cancelbtn:hover:not(:disabled) { background: var(--critical); color: #fff; }
#cancelbtn:disabled { border-color: var(--border); color: var(--muted);
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
#tip { position: fixed; pointer-events: none; background: rgba(18, 22, 27, .96);
       border: 1px solid rgba(255,255,255,.14); border-radius: 8px; padding: 8px 11px;
       font-size: 12px; box-shadow: 0 6px 18px rgba(0,0,0,.4); display: none;
       z-index: 10; max-width: 420px; }
#tip .tv { font-weight: 650; font-size: 13px; color: #fff; }
#tip .tl { color: #b8c2cc; margin-top: 1px; overflow-wrap: anywhere;
           white-space: pre-wrap; }

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
.fe.think .etx { color: var(--muted); font-style: italic; opacity: .85; }
.feedcols.hidethink .fe.think { display: none; }
.thinktoggle { font-size: 11.5px; color: var(--ink-2); display: inline-flex;
               gap: 5px; align-items: center; cursor: pointer; user-select: none;
               margin-left: 12px; }
.chip.xs { cursor: pointer; }
.chip.xs:hover { border-color: var(--ink-2); }
.nowdoing { font-size: 11px; color: var(--muted); font-weight: 400;
            max-width: 46%; overflow: hidden; text-overflow: ellipsis;
            white-space: nowrap; margin-left: 10px; font-family: ui-monospace,
            SFMono-Regular, Menlo, monospace; }
/* tool-mix fingerprint */
.tmrow { display: flex; align-items: center; gap: 10px; margin: 7px 0; }
.tmrow .rlabel { flex: none; width: 76px; font-size: 12.5px; font-weight: 600; }
.tmbar { display: flex; height: 18px; border-radius: 5px; overflow: hidden;
         flex: 1; background: var(--surface-2); }
.tmseg { height: 100%; min-width: 1px; }
.tmnum { flex: none; font-size: 11.5px; color: var(--ink-2); width: 60px;
         text-align: right; }
.tmlegend { display: flex; gap: 14px; flex-wrap: wrap; font-size: 11.5px;
            color: var(--ink-2); margin-top: 8px; }
.tmlegend .sw { width: 10px; height: 10px; border-radius: 3px; display:
                inline-block; margin-right: 5px; vertical-align: -1px; }
/* job timeline */
.tlrow { display: flex; align-items: center; gap: 10px; margin: 5px 0; }
.tlrow .rlabel { flex: none; width: 150px; font-size: 11.5px; font-weight: 600;
                 overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tltrack { position: relative; flex: 1; height: 16px; background:
           var(--surface-2); border-radius: 4px; }
.tlev { position: absolute; top: 2px; width: 6px; height: 12px;
        border-radius: 2px; }
.tlev:hover { transform: scaleX(1.6); z-index: 2; outline: 1px solid var(--ink-1); }
.tlrow .rlabel.tlfail { color: var(--critical) !important; }
.tlspan { position: absolute; top: 6px; height: 4px; border-radius: 2px;
          opacity: .35; }
.tlaxis { display: flex; justify-content: space-between; font-size: 10.5px;
          color: var(--muted); margin: 3px 0 0 160px; }
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

/* ---- IBM Carbon overrides: sharp containers, IBM Plex Mono for data ---- */
.tile, .card, .btrack, .planbox, .sqlbox, #pm-panel, .tmbar, .tltrack,
.tlspan, .feed, .legend .sw, .sw, #historySel, #exportBtn, #cancelbtn,
#dbglog, #dbgPause, #dbgClear, #dbgSnap { border-radius: 0 !important; }
#tip { border-radius: 2px; }
.tile { border-left: 3px solid var(--grid); }
.tile:first-child { border-left-color: var(--brand); }
.fe .et, .sqlbox, #pm-body, #dbglog, .nowdoing, .tile .val {
  font-family: "IBM Plex Mono", ui-monospace, SFMono-Regular, Menlo, monospace; }
.tile .val { font-weight: 400; font-size: 24px; }
h2 { font-weight: 400; }
button { letter-spacing: .16px; }

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
    <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
      <select id="historySel" style="display:none;font:inherit;font-size:12px;padding:5px 8px;border-radius:8px;border:1px solid var(--border);background:var(--surface-1);color:var(--ink-1);cursor:pointer"></select>
      <button id="exportBtn" style="font:inherit;font-size:13px;font-weight:600;padding:8px 14px;border-radius:8px;border:1px solid var(--border);cursor:pointer;background:var(--surface-1);color:var(--ink-1);display:none">⬇ Export</button>
      <button id="cancelbtn" style="display:none">⏹ Cancel</button>
      <button id="runbtn" style="display:none">▶ Run bakeoff</button>
    </div>
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
    <p class="note" id="livenote">tool calls and operations per contestant, streamed as they happen<label class="thinktoggle"><input type="checkbox" id="showthink" checked> 🧠 show thinking</label><span class="note" style="margin-left:12px">· click a db chip for the full transcript 📜</span></p>
    <div class="feedcols" id="feedcols"></div>
  </div>
  <div class="card" id="timelinecard" style="margin-bottom:22px; display:none">
    <h2>Job timeline — how each agent spent its time</h2>
    <p class="note">every event per (contestant × database) placed on a shared clock — 🧠 thinking, reads, writes, shell, output. Long gaps = the model is generating; dense read clusters = exploration; writes at the end = single-shot strategy.</p>
    <div id="timeline"></div>
  </div>
  <div class="card" id="toolmixcard" style="margin-bottom:22px; display:none">
    <h2>Tool-mix fingerprint — strategy at a glance</h2>
    <p class="note">how each agent divides its actions: explore (read/search) vs produce (write/edit) vs execute (shell) — e.g. heavy reading before writing = careful; instant writes = pattern-matching</p>
    <div id="toolmix"></div>
  </div>
  <div class="card" id="debugcard" style="margin-bottom:22px; display:none">
    <h2>🐞 Debug console</h2>
    <p class="note">live viewership diagnostics — each poll's HTTP status &amp; payload size, when the DOM actually re-renders vs skips (fingerprint unchanged), and any JS error thrown while rendering. Enable “verbose” to log every 300&nbsp;ms poll.</p>
    <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:10px;font-size:12px">
      <button id="dbgPause" style="font:inherit;padding:4px 10px;border-radius:6px;border:1px solid var(--border);background:var(--surface-1);color:var(--ink-1);cursor:pointer">pause</button>
      <button id="dbgClear" style="font:inherit;padding:4px 10px;border-radius:6px;border:1px solid var(--border);background:var(--surface-1);color:var(--ink-1);cursor:pointer">clear</button>
      <button id="dbgSnap" style="font:inherit;padding:4px 10px;border-radius:6px;border:1px solid var(--border);background:var(--surface-1);color:var(--ink-1);cursor:pointer">dump snapshot</button>
      <label style="display:flex;gap:5px;align-items:center;cursor:pointer;user-select:none"><input type="checkbox" id="dbgVerbose"> verbose (every poll)</label>
    </div>
    <div id="dbglog" style="font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:11.5px;line-height:1.6;background:#0d1117;color:#c9d1d9;border-radius:8px;padding:10px 12px;max-height:280px;overflow:auto;white-space:pre-wrap;word-break:break-word"></div>
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
  // live: in-flight jobs contribute their elapsed clock so the tile ticks
  const doneKeys = new Set(DATA.results.map(r => r.contestant + "|" + r.db));
  const liveTime = (DATA.jobs || [])
    .filter(j => j.status === "running" && j.started_t != null &&
                 !doneKeys.has(j.contestant + "|" + j.db))
    .reduce((a, j) => a + Math.max(0, (DATA.elapsed_s || 0) - j.started_t), 0);
  const agentTime = DATA.results.reduce((a, r) => a + (r.agent.wall_time_s || 0), 0)
    + planTime + liveTime;
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

  // rows migrated — live from job counters, authoritative once validation lands
  const jrows = DATA.jobs || [];
  const rowsMoved = jrows.reduce((a, j) => a + (j.rows_moved || 0), 0);
  const rowsExp = jrows.reduce((a, j) => a + (j.rows_expected || 0), 0);
  if (rowsExp)
    tile("Rows migrated", fmtInt(rowsMoved) + " / " + fmtInt(rowsExp),
      Math.round(100 * rowsMoved / rowsExp) + "% of source rows in the target");

  // SQL statement volume — shadow execution + validation counters
  const stmts = jrows.reduce((a, j) => a + (j.sql_ok || 0) + (j.sql_err || 0), 0);
  const stmtErrs = jrows.reduce((a, j) => a + (j.sql_err || 0), 0);
  if (stmts)
    tile("SQL statements", fmtInt(stmts),
      stmtErrs ? stmtErrs + " failed — see SQL + QA errors" : "all executed clean");

  tile("Total agent time", agentTime >= 120 ? (agentTime / 60).toFixed(1) + " min" : Math.round(agentTime) + "s", "wall clock across both phases");

  // run clock — freeze at the last event once the run is done
  if (DATA.live) {
    const lastT = (DATA.events || []).length
      ? DATA.events[DATA.events.length - 1].t : DATA.elapsed_s || 0;
    const clock = DATA.done ? lastT : (DATA.elapsed_s || 0);
    tile("Run clock", clock >= 120 ? (clock / 60).toFixed(1) + " min" : Math.round(clock) + "s",
      DATA.done ? "total run duration" : "elapsed — parallelism hides agent time");
  }
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

  // fastest agent — lowest average migration wall clock among fully successful
  // jobs (baseline excluded: a rule-based script wins on speed by definition)
  const fastRows = order.filter(c => c !== "baseline").map(c => {
    const winsR = DATA.results.filter(r => r.contestant === c && r.validation.success);
    if (!winsR.length) return null;
    const avg = winsR.reduce((a, r) => a + (r.agent.wall_time_s || 0), 0) / winsR.length;
    return { label: c, avg };
  }).filter(Boolean);
  if (fastRows.length) {
    const fbest = fastRows.reduce((a, r) => (r.avg < a.avg ? r : a), fastRows[0]);
    tile("Fastest agent", fbest.label,
      fastRows.map(r => r.label + " " + fmtSec(r.avg)).join(" · ") +
      " — avg per successful migration (baseline excluded)");
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
    // "now doing" ticker: the agent's most recent action, live
    if (DATA.live && !DATA.done) {
      const acts = (DATA.events || []).filter(e =>
        e.contestant === c && (e.kind === "tool" || e.kind === "think"));
      if (acts.length) {
        const last = acts[acts.length - 1];
        head.append(el("span", "nowdoing",
          (last.kind === "think" ? "🧠 " : "→ ") + last.text));
      }
    }
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
    const ticon = { postgres: "🐘", duckdb: "🦆", snowflake: "❄" }[targetName] || "🗄";
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
  // preserve each column's scroll position across re-renders: keep the user's
  // place when they scrolled up; stick to bottom only if they were at bottom
  const scrollPrev = {};
  for (const f of cols.querySelectorAll(".feed")) {
    scrollPrev[f.dataset.c] = {
      top: f.scrollTop,
      atBottom: f.scrollHeight - f.scrollTop - f.clientHeight < 30,
    };
  }
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
                  : j.status === "running" ? "running"
                  : j.status === "cancelled" ? "fail" : "";
        const mark = j.status === "done" ? (j.success ? " ✓" : " ✗")
                   : j.status === "running" ? " …"
                   : j.status === "cancelled" ? " ⊘" : "";
        const chip = el("span", "chip xs " + cls, j.db + mark + " 📜");
        chip.title = "view full transcript";
        chip.addEventListener("click", () => openTranscript(c, j.db));
        chips.append(chip);
      }
      col.append(chips);
    }
    const feed = el("div", "feed");
    feed.dataset.c = c;
    const mine = events.filter(e => e.contestant === c).slice(-FEED_CAP);
    for (const e of mine) {
      const row = el("div", "fe " + (e.kind || "text"));
      row.append(el("span", "et", e.t.toFixed(1) + "s"),
                 el("span", "edb", e.db),
                 el("span", "etx", (e.kind === "think" ? "🧠 " : "") + e.text));
      feed.append(row);
    }
    if (!mine.length) feed.append(el("div", "fe", "— no activity yet —"));
    col.append(feed);
    cols.append(col);
    const prev = scrollPrev[c];
    requestAnimationFrame(() => {
      if (!prev || prev.atBottom) feed.scrollTop = feed.scrollHeight;
      else feed.scrollTop = prev.top;      // user scrolled up — stay put
    });
  }
}

// ---- transcript viewer (reuses the plan modal) ----
async function openTranscript(contestant, db) {
  openPlanModal("📜 " + contestant + " × " + db + " — full agent transcript", "loading…");
  try {
    const r = await fetch("/log/" + contestant + "__" + db, { cache: "no-store" });
    byId("pm-body").textContent = await r.text();
  } catch (e) {
    byId("pm-body").textContent = "transcript unavailable: " + e;
  }
}

// ---- thinking toggle ----
(function initThinkToggle() {
  const cb = byId("showthink");
  if (!cb) return;
  const apply = () => byId("feedcols").classList.toggle("hidethink", !cb.checked);
  cb.checked = localStorage.getItem("bakeoff.showthink") !== "0";
  cb.addEventListener("change", () => {
    localStorage.setItem("bakeoff.showthink", cb.checked ? "1" : "0");
    apply();
  });
  apply();
})();

// ---- tool-mix fingerprint ----
const TOOL_CATS = ["explore", "write", "shell", "other"];
// Carbon steps, CVD-validated as a trio; gray/think/issue are labeled slots
const TOOL_COLORS = { explore: "#1192e8", write: "#8a3ffc", shell: "#d02670",
                      other: "#8d8d8d", think: "#007d79", issue: "#da1e28" };
function toolCategory(text) {
  const t = (text || "").toLowerCase();
  if (/^(read|cat|head|tail|grep|glob|search|ls\b|list|find|fetch|codebase)/.test(t)) return "explore";
  if (/^(writ|edit|creat|apply|str_replace|multiedit|todo|delete)/.test(t)) return "write";
  if (/^(bash|shell|run|exec|command|terminal)/.test(t)) return "shell";
  return "other";
}
function renderToolMix(DATA, order) {
  const card = byId("toolmixcard"), box = byId("toolmix");
  const tools = (DATA.events || []).filter(e => e.kind === "tool");
  if (!tools.length) { card.style.display = "none"; return; }
  card.style.display = "";
  box.replaceChildren();
  for (const c of order) {
    const mine = tools.filter(e => e.contestant === c);
    if (!mine.length) continue;
    const counts = {};
    for (const e of mine) counts[toolCategory(e.text)] = (counts[toolCategory(e.text)] || 0) + 1;
    const row = el("div", "tmrow");
    const lbl = el("span", "rlabel", c);
    lbl.style.color = css(colorVar(c));
    const bar = el("div", "tmbar");
    for (const cat of TOOL_CATS) {
      if (!counts[cat]) continue;
      const seg = el("div", "tmseg");
      seg.style.width = (100 * counts[cat] / mine.length) + "%";
      seg.style.background = TOOL_COLORS[cat];
      hoverable(seg, counts[cat] + " " + cat, c + " — " +
        Math.round(100 * counts[cat] / mine.length) + "% of " + mine.length + " tool calls");
      bar.append(seg);
    }
    row.append(lbl, bar, el("span", "tmnum", mine.length + " calls"));
    box.append(row);
  }
  const lg = el("div", "tmlegend");
  for (const cat of TOOL_CATS) {
    const item = el("span");
    const sw = el("span", "sw"); sw.style.background = TOOL_COLORS[cat];
    item.append(sw, document.createTextNode(
      { explore: "explore (read/search)", write: "produce (write/edit)",
        shell: "execute (shell)", other: "other" }[cat]));
    lg.append(item);
  }
  box.append(lg);
}

// ---- job timeline (Gantt of events on a shared clock) ----
function renderTimeline(DATA, order) {
  const card = byId("timelinecard"), box = byId("timeline");
  const events = DATA.events || [], jobs = DATA.jobs || [];
  if (!events.length || !jobs.length) { card.style.display = "none"; return; }
  card.style.display = "";
  box.replaceChildren();
  // shared clock: grow with elapsed time while running, but FREEZE at the
  // last event once every job is done (elapsed_s keeps ticking while the
  // server idles, which would compress the dots forever)
  const lastT = events.length ? Math.max(...events.map(e => e.t)) : 0;
  const running = DATA.live && !DATA.done;
  const tmax = Math.max(running ? (DATA.elapsed_s || 0) : 0, lastT, 1);
  const sorted = [...jobs].sort((a, b) =>
    a.db === b.db ? order.indexOf(a.contestant) - order.indexOf(b.contestant)
                  : a.db.localeCompare(b.db));
  for (const j of sorted) {
    const evs = events.filter(e => e.contestant === j.contestant && e.db === j.db)
                      .slice(0, 400);
    if (!evs.length) continue;
    const failed = j.success === false || j.status === "cancelled";
    const row = el("div", "tlrow");
    const lbl = el("span", "rlabel" + (failed ? " tlfail" : ""),
      (failed ? (j.status === "cancelled" ? "⊘ " : "✗ ") : "") +
      j.contestant + " × " + j.db);
    if (!failed) lbl.style.color = css(colorVar(j.contestant));
    const track = el("div", "tltrack");
    const t0 = evs[0].t, t1 = evs[evs.length - 1].t;
    const span = el("div", "tlspan");
    span.style.left = (100 * t0 / tmax) + "%";
    span.style.width = Math.max(0.5, 100 * (t1 - t0) / tmax) + "%";
    span.style.background = failed ? css("--critical") : css(colorVar(j.contestant));
    if (failed) span.style.opacity = ".55";
    track.append(span);
    for (const e of evs) {
      const d = el("div", "tlev");
      d.style.left = "min(99%, " + (100 * e.t / tmax) + "%)";
      const cat = e.kind === "tool" ? toolCategory(e.text) : null;
      d.style.background = e.kind === "think" ? TOOL_COLORS.think
        : e.kind === "issue" ? TOOL_COLORS.issue
        : cat ? TOOL_COLORS[cat]
        : "#8b98a5";
      hoverable(d,
        e.t.toFixed(1) + "s · " + (cat || (e.kind === "think" ? "🧠 thinking" : e.kind)),
        (e.text || "").slice(0, 200));
      track.append(d);
    }
    row.append(lbl, track);
    box.append(row);
  }
  const axis = el("div", "tlaxis");
  axis.append(el("span", "", "0s"),
              el("span", "", Math.round(tmax / 2) + "s"),
              el("span", "", Math.round(tmax) + "s"));
  box.append(axis);
}

// ---- bar chart cards ----
function barCard(title, note, rows, fmt, higherBetter) {
  const card = el("div", "card");
  card.append(el("h2", "", title));
  card.append(el("p", "note", note));
  const max = Math.max(...rows.map(r => r.value ?? 0), 1e-9);
  // best → worst, top → bottom (ascending normally, descending when higher is
  // better); nulls after real values; baseline always last
  rows = [...rows].sort((a, b) => {
    const ab = a.label === "baseline", bb = b.label === "baseline";
    if (ab !== bb) return ab ? 1 : -1;
    const nil = higherBetter ? -Infinity : Infinity;
    const av = a.value == null ? nil : a.value;
    const bv = b.value == null ? nil : b.value;
    return higherBetter ? bv - av : av - bv;
  });
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
      track.append(bar, el("span", "rval", r.display || fmt(r.value)));
      hoverable(row, r.display || fmt(r.value),
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
  const order = contestantOrder(DATA);   // includes still-running jobs
  if (!order.length) return;
  const jobs = DATA.jobs || [];
  const anyLive = jobs.some(j => j.status === "running");
  const L = anyLive ? " (live)" : "";
  // Per-contestant merge: finished results are authoritative; jobs still
  // running contribute their live values (shadow-tailed files, elapsed clock).
  // fn(finishedResults, runningJobs) -> {value, extra?, na?}
  const merged = fn => order.map(c => {
    const rs = DATA.results.filter(r => r.contestant === c);
    const doneDbs = new Set(rs.map(r => r.db));
    const running = jobs.filter(j => j.contestant === c &&
      j.status === "running" && !doneDbs.has(j.db));
    return { label: c, ...fn(rs, running, c) };
  });

  const s = c => DATA.summary[c] || {};
  if (order.some(c => s(c).plan_lines != null))
    barCard("Phase 1 — planning", "time spent producing the architectural migration plan (hover for size & cost)",
      order.map(c => ({ label: c, value: s(c).plan_wall_time_s,
                        extra: fmtInt(s(c).plan_lines) + " plan lines, " +
                               (fmtUsd(s(c).plan_cost_usd) || "$0") })),
      fmtSec);

  barCard("Wall time (phase 2)" + L, "migration wall clock across all databases — running jobs show elapsed time so far (lower is better)",
    merged((rs, running) => ({
      value: rs.reduce((a, r) => a + (r.agent.wall_time_s || 0), 0) +
             running.reduce((a, j) => a + (j.started_t != null
               ? Math.max(0, (DATA.elapsed_s || 0) - j.started_t) : 0), 0)
    })), fmtSec);

  barCard("Cost" + L, "spend in USD — reported by each CLI when its job finishes (running jobs not yet counted); Bob converts Bobcoins at $0.50/coin, Cursor is estimated from tokens (lower is better)",
    merged(rs => {
      const cs = rs.map(r => r.agent.cost_usd).filter(v => v != null);
      return { value: cs.length ? cs.reduce((a, v) => a + v, 0) : null,
               na: rs.length ? "not reported" : "running…" };
    }), v => "$" + v.toFixed(2));

  barCard("Lines of SQL produced" + L, "non-blank lines across migrated schema + load scripts — counts grow live as agents write files",
    merged((rs, running) => ({
      value: rs.reduce((a, r) => a + (r.loc || 0), 0) +
             running.reduce((a, j) => a + (j.loc || 0), 0)
    })), fmtInt);

  barCard("Output tokens", "tokens generated by the agent, reported at job end (input tokens are not comparable across CLIs — see table)",
    merged(rs => {
      const ts = rs.map(r => r.agent.tokens_out).filter(v => v != null);
      return { value: ts.length ? ts.reduce((a, v) => a + v, 0) : null,
               na: rs.length ? "n/a" : "running…" };
    }), fmtTok);

  // live shadow-execution failures while running (includes transient errors
  // the agent later fixed — rewrites reset + replay the schema); replaced by
  // the authoritative schema+load validation count once each job finishes
  barCard("SQL + QA errors" + L, "failed statements PLUS testing-layer error findings — Oracle-isms in output (e.g. CREATE SEQUENCE), agent failures (max-turns, credits), dropped constraints. Hover for the exec/QA split; warnings shown but not counted in the bar",
    merged((rs, running, c) => {
      const execErrs = rs.reduce((n, r) => n + (r.validation.schema_errors || []).length +
                                              (r.validation.load_errors || []).length, 0) +
                       running.reduce((n, j) => n + (j.sql_err || 0), 0);
      const qaErrs = rs.reduce((n, r) => n + ((r.quality || {}).errors || 0), 0);
      const qaWarns = rs.reduce((n, r) => n + ((r.quality || {}).warnings || 0), 0);
      // live QA: the watcher emits one deduped "issue" event per finding while
      // the agent works; count those for jobs whose result hasn't landed yet
      const runningDbs = new Set(running.map(j => j.db));
      const liveQA = (DATA.events || []).filter(e =>
        e.kind === "issue" && e.contestant === c && runningDbs.has(e.db)).length;
      // statement volume from the shadow-execution counters (persist on jobs
      // after completion; includes replays after agent rewrites)
      const stmts = jobs.filter(j => j.contestant === c)
                        .reduce((n, j) => n + (j.sql_ok || 0) + (j.sql_err || 0), 0);
      const errs = execErrs + qaErrs + liveQA;
      return { value: errs,
               display: stmts ? errs + " err / " + stmts + " stmts" : String(errs),
               extra: execErrs + " failed statement(s) + " + (qaErrs + liveQA) +
                      " QA error(s)" + (qaWarns ? " (+" + qaWarns +
                      " warning(s), not counted)" : "") +
                      (stmts ? " — " + stmts + " statements executed" : "") };
    }), fmtInt);

  barCard("Constraint preservation" + L, "% of source PK / FK / CHECK / UNIQUE / NOT NULL constraints carried into the migrated DDL vs ground truth — updates live as agents write schema files (higher is better; heuristic quality findings live in the Testing layer)",
    merged((rs, running) => {
      let exp = 0, kept = 0;
      for (const r of rs) {
        const c = r.validation.constraints || {};
        exp += c.expected || 0; kept += c.preserved || 0;
      }
      for (const j of running) {
        const c = j.constraints || {};
        exp += c.expected || 0; kept += c.preserved || 0;
      }
      return { value: exp ? 100 * kept / exp : null,
               na: anyLive ? "writing…" : "n/a" };
    }), v => v.toFixed(0) + "%", true);
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
  renderTimeline(DATA, order);
  renderToolMix(DATA, order);
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
// ---- cancel button ----
const cancelbtn = byId("cancelbtn");
cancelbtn.addEventListener("click", async () => {
  cancelbtn.disabled = true;
  cancelbtn.textContent = "⏹ Cancelling…";
  try {
    const r = await fetch("/cancel", { method: "POST" });
    if (!r.ok && r.status !== 409) throw new Error(r.status);
  } catch (e) {
    cancelbtn.textContent = "⏹ Cancel";
    cancelbtn.disabled = false;
  }
});

function updateRunBtn(state) {
  const running = !state.idle && !state.done;
  // cancel is visible only while a run is actually in flight
  if (state.can_cancel && running) {
    cancelbtn.style.display = "";
  } else {
    cancelbtn.style.display = "none";
    cancelbtn.textContent = "⏹ Cancel";
    cancelbtn.disabled = false;
  }
  if (!state.can_run) { runbtn.style.display = "none"; return; }
  runbtn.style.display = "";
  if (running) {
    runbtn.disabled = true;
    runbtn.textContent = "⏳ Running…";
    runRequested = false;
  } else if (!runRequested) {
    runbtn.disabled = false;
    runbtn.textContent = state.idle ? "▶ Run bakeoff" : "↻ Run again";
  }
}

// ---- debug console (live viewership diagnostics) ----
const DBG_CAP = 500;
let dbgPaused = false, dbgVerbose = false, renderCount = 0;
function dbgEl() { return document.getElementById("dbglog"); }
function dbg(kind, msg) {
  const box = dbgEl();
  if (!box || dbgPaused) return;
  const now = new Date();
  const ts = now.toTimeString().slice(0, 8) + "." +
    String(now.getMilliseconds()).padStart(3, "0");
  const color = { error: "#ff6b6b", warn: "#e6a23c", render: "#3fb950",
                  poll: "#8b98a5", info: "#58a6ff" }[kind] || "#8b98a5";
  const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 24;
  const line = el("div");
  line.append(el2("span", "#6b7480", ts + " "),
              el2("span", color, kind + " ", true));
  line.append(document.createTextNode(msg));   // message as text — no injection
  box.append(line);
  while (box.childNodes.length > DBG_CAP) box.removeChild(box.firstChild);
  if (atBottom) box.scrollTop = box.scrollHeight;
}
function el2(tag, color, text, bold) {
  const s = document.createElement(tag);
  s.style.color = color;
  if (bold) s.style.fontWeight = "600";
  s.textContent = text;
  return s;
}
// surface JS errors (incl. those thrown inside render) straight into the panel
window.addEventListener("error", e =>
  dbg("error", "JS: " + (e.message || e.error) +
    (e.filename ? " @ " + e.filename.split("/").pop() + ":" + e.lineno : "")));
window.addEventListener("unhandledrejection", e =>
  dbg("error", "promise: " + ((e.reason && e.reason.message) || e.reason)));

(function initDebug() {
  if (!LIVE) return;                       // debug console is a live-mode tool
  const card = document.getElementById("debugcard");
  if (card) card.style.display = "";
  const bind = (id, fn) => { const b = document.getElementById(id); if (b) b.addEventListener("click", fn); };
  bind("dbgClear", () => { const b = dbgEl(); if (b) b.replaceChildren(); });
  bind("dbgPause", () => {
    dbgPaused = !dbgPaused;
    const b = document.getElementById("dbgPause");
    if (b) b.textContent = dbgPaused ? "resume" : "pause";
    if (!dbgPaused) { const box = dbgEl(); if (box) box.scrollTop = box.scrollHeight; }
  });
  const vb = document.getElementById("dbgVerbose");
  if (vb) vb.addEventListener("change", () => {
    dbgVerbose = vb.checked;
    dbg("info", dbgVerbose ? "verbose on — logging every poll" : "verbose off");
  });
  bind("dbgSnap", () => {
    if (!lastData) { dbg("info", "no snapshot received yet"); return; }
    const j = JSON.stringify(lastData);
    dbg("info", "snapshot " + j.length + "B · events " +
      (lastData.events || []).length + " · jobs " + (lastData.jobs || []).length +
      " · results " + (lastData.results || []).length +
      " (full object also logged to browser console)");
    console.log("[bakeoff snapshot]", lastData);
  });
})();

if (LIVE) {
  dbg("info", "live mode — polling /live.json every 300ms");
  let stopped = false, failures = 0, lastFp = null, polls = 0;
  async function tick() {
    try {
      const t0 = performance.now();
      const r = await fetch("/live.json", { cache: "no-store" });
      const txt = await r.text();
      const dt = Math.round(performance.now() - t0);
      polls++;
      let state;
      try {
        state = JSON.parse(txt);
      } catch (pe) {
        dbg("error", "bad JSON from /live.json (" + txt.length + "B): " + pe.message);
        throw pe;
      }
      failures = 0;
      // only rebuild the DOM when the state actually changed — keeps scroll
      // position stable in expanded plan docs and feeds while idle
      const fp = JSON.stringify([state.done, state.idle, state.previous_run,
        state.elapsed_s, (state.events || []).length,
        (state.results || []).length,
        (state.jobs || []).map(j => j.status + (j.phase || "") +
          (j.rows_moved || 0) + "." + (j.sql_ok || 0) + "." + (j.sql_err || 0)).join(",")]);
      if (dbgVerbose)
        dbg("poll", "#" + polls + " " + r.status + " " + txt.length + "B " + dt + "ms " +
          (fp === lastFp ? "· no change" : "· changed"));
      if (fp !== lastFp) {
        lastFp = fp;
        const jobs = state.jobs || [];
        const running = jobs.filter(j => j.status === "running").length;
        const doneN = jobs.filter(j => j.status === "done").length;
        const rt0 = performance.now();
        try {
          render(state);
          renderCount++;
          dbg("render", "#" + renderCount + " " + Math.round(performance.now() - rt0) +
            "ms · events " + (state.events || []).length + " · jobs " + jobs.length +
            " (" + running + " run, " + doneN + " done) · results " +
            (state.results || []).length +
            (state.done ? " · DONE" : state.idle ? " · idle" : ""));
        } catch (re) {
          // don't let a render bug kill the live connection — log and keep polling
          dbg("error", "render() threw: " + ((re && re.message) || re) +
            (re && re.stack ? " | " + re.stack.split("\\n")[1] : ""));
        }
      }
      updateRunBtn(state);
      // one-shot mode stops on done; --serve mode keeps polling so the
      // button can start the next run
      if (state.done && !state.can_run) {
        stopped = true;
        dbg("info", "run finished (done, one-shot) — polling stopped");
      }
    } catch (e) {
      if (++failures > 5) {
        byId("livenote").textContent = "live server stopped — reopen results/<run>/dashboard.html for the final report";
        byId("livedot").classList.add("done");
        stopped = true;
        dbg("error", "live server unreachable after 5 tries — polling stopped");
      } else {
        dbg("warn", "poll failed (" + failures + "/5): " + ((e && e.message) || e));
      }
    }
    if (!stopped) setTimeout(tick, 300);
  }
  tick();
} else if (EMBED) {
  render(EMBED);
}

// ---- export button ----
const exportBtn = byId("exportBtn");
if (exportBtn) {
  exportBtn.style.display = "";
  exportBtn.addEventListener("click", () => {
    const data = lastData;
    if (!data) return;
    const ts = (data.generated_at || new Date().toISOString()).replace(/[: ]/g, "-").slice(0, 19);
    // export results.json
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "bakeoff-results-" + ts + ".json";
    a.click();
    URL.revokeObjectURL(a.href);
  });
}

// ---- history dropdown (live mode only) ----
if (LIVE) {
  const sel = byId("historySel");
  async function loadHistory() {
    try {
      const r = await fetch("/history.json", { cache: "no-store" });
      const entries = await r.json();
      if (!entries.length) return;
      sel.style.display = "";
      sel.innerHTML = "<option value=''>⏱ Past runs…</option>" +
        entries.map(e => `<option value="${e.ts}">${e.label}</option>`).join("");
      sel.addEventListener("change", async () => {
        if (!sel.value) return;
        try {
          const r2 = await fetch("/history/" + sel.value, { cache: "no-store" });
          const data = await r2.json();
          render({ ...data, live: false });
          exportBtn.style.display = "";
        } catch (e) {}
      });
    } catch (e) {}
  }
  loadHistory();
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
