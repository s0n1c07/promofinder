"""viewer.html: one self-contained page for an editor (PLAN.md §4).

It shows the ranked promos, the posting plan and the transcript of every part with times, and lets
an editor approve, mark maybe or reject each promo with a note. Decisions stay in the browser and
download as a CSV, the feedback a later version learns from. No server, no install, no network:
double-click the file.
"""

from __future__ import annotations

import json

from .export import _KIND_LABEL, _window_text

_EPS = 1e-6
_KIND = {"CAMERA": "camera", "ACTION": "action", "SOUND_FX": "sound"}


def _mmss(seconds: float) -> str:
    return f"{int(seconds // 60):02d}:{seconds % 60:04.1f}"


def _transcript(promo: dict, episodes: dict[int, dict]) -> list[dict]:
    hook, cliff = promo["hook"]["block_id"], promo["cliffhanger"]["block_id"]
    parts = []
    for seg in promo["segments"]:
        lines = []
        for b in episodes[seg["episode"]]["blocks"]:
            if not seg["start_s"] - _EPS <= b["start_s"] < seg["end_s"] - _EPS:
                continue
            sp = b["speaker"]
            lines.append({
                "t": _mmss(b["start_s"]),
                "who": sp["label"] if sp else _KIND[b["kind"]],
                "how": ", ".join(sp["delivery"]) if sp else "",
                "spoken": bool(sp),
                "text": b["text"],
                "mark": "hook" if b["block_id"] == hook else "cliffhanger" if b["block_id"] == cliff else "",
            })
        parts.append({"episode": seg["episode"], "start": seg["start"], "end": seg["end"], "lines": lines})
    return parts


def viewer_data(promos: list[dict], meta: dict, episodes: dict[int, dict]) -> dict:
    by_id = {p["promo_id"]: p for p in promos}
    return {
        "judge": meta.get("judge_label") or meta["judge"],
        "counts": {"promos": len(promos), "episodes": meta["episodes_with_promos"], "of": meta["episodes"]},
        "plan": [{"tier": t["tier"], "ranks": [by_id[i]["rank"] for i in t["promo_ids"] if i in by_id]}
                 for t in meta.get("posting_plan", [])],
        "promos": [{
            "rank": p["rank"], "id": p["promo_id"], "kind": p["kind"], "kindLabel": _KIND_LABEL[p["kind"]],
            "key": _window_text(p), "duration": p["duration_s"], "episodes": p["episodes"],
            "hook": p["hook"]["text"], "onset": p["hook"]["onset_s"],
            "cliff": p["cliffhanger"]["text"], "question": p["cliffhanger"]["open_question"] or "",
            "why": p["why_it_works"], "reason": p["rank_reason"], "trope": p["trope"], "flags": p["flags"],
            "foundBy": p["found_by"],
            "scores": {k: p["scores"][k] for k in ("hook", "cliffhanger", "standalone", "final")},
            "parts": _transcript(p, episodes),
        } for p in promos],
    }


def viewer_html(promos: list[dict], meta: dict, episodes: dict[int, dict]) -> str:
    data = json.dumps(viewer_data(promos, meta, episodes), ensure_ascii=False).replace("</", "<\\/")
    return _TEMPLATE.replace("__DATA__", data)


_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Promo shortlist</title>
<style>
:root {
  --bg: #f6f5f2; --panel: #ffffff; --ink: #1d1d1f; --muted: #5f6368; --line: #e3e1dc;
  --accent: #b4235a; --accent-ink: #ffffff; --chip: #f0eee9; --hook: #fff1c2; --cliff: #e4ecff;
  --yes: #1e7a46; --maybe: #9a6a00; --no: #b3261e; --focus: #2b59c3;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #141416; --panel: #1d1d20; --ink: #ececef; --muted: #a3a3ab; --line: #2e2e33;
    --accent: #f06292; --accent-ink: #1b0a12; --chip: #2a2a2f; --hook: #4a3b10; --cliff: #1e2c4d;
    --yes: #5cc98a; --maybe: #e0b44a; --no: #f2867e; --focus: #8ab4f8;
  }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--ink);
  font: 15px/1.5 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
header { position: sticky; top: 0; z-index: 2; background: var(--bg); border-bottom: 1px solid var(--line); }
.wrap { max-width: 980px; margin: 0 auto; padding: 0 16px; }
.top { display: flex; flex-wrap: wrap; align-items: baseline; gap: 4px 16px; padding: 14px 0 6px; }
h1 { font-size: 20px; margin: 0; }
.sub { color: var(--muted); font-size: 13px; }
.controls { display: flex; flex-wrap: wrap; gap: 8px; padding: 6px 0 12px; }
input, select, button, textarea { font: inherit; color: inherit; }
input[type=search], select { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 6px 10px; }
input[type=search] { flex: 1 1 220px; }
button { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 6px 12px; cursor: pointer; }
button.primary { background: var(--accent); border-color: var(--accent); color: var(--accent-ink); }
button:focus-visible, input:focus-visible, select:focus-visible, textarea:focus-visible { outline: 2px solid var(--focus); outline-offset: 1px; }
.plan { background: var(--panel); border: 1px solid var(--line); border-radius: 12px; padding: 12px 16px; margin: 16px 0; }
.plan h2 { font-size: 15px; margin: 0 0 6px; }
.plan ol { margin: 0; padding-left: 20px; }
.plan a { color: var(--accent); }
.count { color: var(--muted); font-size: 13px; margin: 8px 0; }
.card { background: var(--panel); border: 1px solid var(--line); border-radius: 12px; padding: 14px 16px; margin: 12px 0; }
.card.d-approve { border-left: 4px solid var(--yes); }
.card.d-maybe { border-left: 4px solid var(--maybe); }
.card.d-reject { border-left: 4px solid var(--no); opacity: .75; }
.head { display: flex; flex-wrap: wrap; align-items: baseline; gap: 4px 12px; }
.rank { font-weight: 700; font-size: 18px; color: var(--accent); min-width: 2.5em; }
.where { font-weight: 600; }
.meta { color: var(--muted); font-size: 13px; }
.row { margin: 6px 0 0; }
.label { font-weight: 600; }
.chips { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
.chip { background: var(--chip); border-radius: 999px; padding: 1px 10px; font-size: 12px; color: var(--muted); }
.chip.flag { color: var(--no); }
.bars { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px; margin-top: 10px; max-width: 520px; }
.bar { font-size: 12px; color: var(--muted); }
.bar i { display: block; height: 6px; border-radius: 3px; background: var(--chip); margin-top: 3px; overflow: hidden; }
.bar i b { display: block; height: 100%; background: var(--accent); }
details { margin-top: 10px; }
summary { cursor: pointer; color: var(--accent); font-size: 14px; }
.part { margin: 8px 0 0; }
.part h3 { font-size: 13px; margin: 8px 0 4px; color: var(--muted); }
.line { display: grid; grid-template-columns: 4.2em 1fr; gap: 8px; padding: 2px 6px; border-radius: 6px; font-size: 14px; }
.line .t { color: var(--muted); font-variant-numeric: tabular-nums; }
.line.hook { background: var(--hook); }
.line.cliffhanger { background: var(--cliff); }
.line .who { font-weight: 600; }
.line .what { color: var(--muted); font-style: italic; }
.cut { color: var(--muted); font-size: 12px; text-align: center; margin: 6px 0; }
.decide { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-top: 12px; }
.decide button[aria-pressed=true].approve { background: var(--yes); border-color: var(--yes); color: var(--panel); }
.decide button[aria-pressed=true].maybe { background: var(--maybe); border-color: var(--maybe); color: var(--panel); }
.decide button[aria-pressed=true].reject { background: var(--no); border-color: var(--no); color: var(--panel); }
textarea { width: 100%; min-height: 2.4em; margin-top: 8px; background: var(--bg); border: 1px solid var(--line); border-radius: 8px; padding: 6px 10px; resize: vertical; }
footer { color: var(--muted); font-size: 13px; padding: 24px 0 40px; }
footer a { color: var(--accent); }
@media (max-width: 560px) {
  header { position: static; }   /* a sticky header would take a third of a phone screen */
  .bars { grid-template-columns: repeat(2, minmax(0, 1fr)); } .rank { min-width: 0; }
}
</style>
</head>
<body>
<header>
  <div class="wrap">
    <div class="top">
      <h1>Promo shortlist</h1>
      <span class="sub" id="sub"></span>
    </div>
    <div class="controls">
      <input type="search" id="q" placeholder="Search hooks, cliffhangers, lines…" aria-label="Search">
      <select id="kind" aria-label="Kind"><option value="">All kinds</option></select>
      <select id="dec" aria-label="Decision">
        <option value="">All decisions</option><option value="none">Not decided</option>
        <option value="approve">Approved</option><option value="maybe">Maybe</option><option value="reject">Rejected</option>
      </select>
      <select id="show" aria-label="How many">
        <option value="10">Top 10</option><option value="30">Top 30</option><option value="0" selected>All</option>
      </select>
      <button class="primary" id="export">Export decisions (CSV)</button>
    </div>
  </div>
</header>
<main class="wrap">
  <section class="plan" id="plan"></section>
  <div class="count" id="count"></div>
  <div id="list"></div>
  <footer>
    Times are relative to each episode, as in the screenplays. Hook lines are marked in yellow and cliffhanger
    lines in blue. Decisions are saved in this browser only; export them to keep them.
    The same promos as text: <a href="promos_ranked.md">promos_ranked.md</a>. Checks and failures:
    <a href="qc_report.md">qc_report.md</a> (after <code>python -m promofinder qc</code>).
  </footer>
</main>
<script type="application/json" id="data">__DATA__</script>
<script>
(function () {
  "use strict";
  var D = JSON.parse(document.getElementById("data").textContent);
  // Local files often share one storage area, so decisions are kept per judge; each promo is keyed
  // by its segments, which survive re-ranking.
  var KEY = "promofinder-decisions-" + D.judge;
  var memory = {};
  function load() { try { return JSON.parse(localStorage.getItem(KEY) || "{}"); } catch (e) { return memory; } }
  function save(v) { memory = v; try { localStorage.setItem(KEY, JSON.stringify(v)); } catch (e) { /* private mode */ } }
  var decisions = load();

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = text;
    return n;
  }

  document.getElementById("sub").textContent = D.counts.promos + " promos from " + D.counts.episodes + " of " +
    D.counts.of + " episodes · judge: " + D.judge;

  var kinds = {};
  D.promos.forEach(function (p) { kinds[p.kind] = p.kindLabel; });
  var kindSel = document.getElementById("kind");
  Object.keys(kinds).forEach(function (k) { var o = el("option", "", kinds[k]); o.value = k; kindSel.appendChild(o); });

  var plan = document.getElementById("plan");
  if (D.plan.length) {
    plan.appendChild(el("h2", "", "Posting plan: who sees what first"));
    var ol = el("ol");
    D.plan.forEach(function (t) {
      var li = el("li", "", t.tier + ": ");
      t.ranks.forEach(function (r, i) {
        var a = el("a", "", "#" + r); a.href = "#p" + r; li.appendChild(a);
        if (i < t.ranks.length - 1) li.appendChild(document.createTextNode(", "));
      });
      if (!t.ranks.length) li.appendChild(document.createTextNode("none"));
      ol.appendChild(li);
    });
    plan.appendChild(ol);
  } else { plan.hidden = true; }

  function bar(name, v) {
    var b = el("div", "bar", name + " " + v.toFixed(2));
    var i = el("i"); var f = el("b"); f.style.width = Math.max(0, Math.min(1, v)) * 100 + "%";
    i.appendChild(f); b.appendChild(i); return b;
  }

  function card(p) {
    var d = decisions[p.key] || {};
    var c = el("article", "card" + (d.decision ? " d-" + d.decision : ""));
    c.id = "p" + p.rank;
    var head = el("div", "head");
    head.appendChild(el("span", "rank", "#" + p.rank));
    head.appendChild(el("span", "where", p.key));
    head.appendChild(el("span", "meta", p.duration.toFixed(1) + " s · " + p.kindLabel +
      (p.foundBy === "llm" ? " · found by the model" : "")));
    c.appendChild(head);
    var r1 = el("div", "row"); r1.appendChild(el("span", "label", "Hook (" + p.onset.toFixed(1) + " s in): "));
    r1.appendChild(document.createTextNode(p.hook)); c.appendChild(r1);
    var r2 = el("div", "row"); r2.appendChild(el("span", "label", "Cliffhanger: "));
    r2.appendChild(document.createTextNode(p.cliff + (p.question ? " — left asking: " + p.question : ""))); c.appendChild(r2);
    var r3 = el("div", "row"); r3.appendChild(el("span", "label", "Why it works: "));
    r3.appendChild(document.createTextNode(p.why)); c.appendChild(r3);
    var r4 = el("div", "row meta", "Why this rank: " + p.reason); c.appendChild(r4);
    var bars = el("div", "bars");
    bars.appendChild(bar("Hook", p.scores.hook)); bars.appendChild(bar("Cliffhanger", p.scores.cliffhanger));
    bars.appendChild(bar("Standalone", p.scores.standalone)); bars.appendChild(bar("Final", p.scores.final));
    c.appendChild(bars);
    var chips = el("div", "chips");
    chips.appendChild(el("span", "chip", p.trope));
    p.flags.forEach(function (f) { chips.appendChild(el("span", "chip" + (f === "graphic" || f === "spoiler" ? " flag" : ""), f)); });
    c.appendChild(chips);

    var det = el("details");
    det.appendChild(el("summary", "", "Transcript (" + p.parts.length + (p.parts.length > 1 ? " parts" : " part") + ")"));
    p.parts.forEach(function (part, i) {
      if (i) det.appendChild(el("div", "cut", "— cut —"));
      var box = el("div", "part");
      box.appendChild(el("h3", "", "Episode " + part.episode + ", " + part.start + "–" + part.end));
      part.lines.forEach(function (l) {
        var row = el("div", "line" + (l.mark ? " " + l.mark : ""));
        row.title = l.mark ? (l.mark === "hook" ? "Hook" : "Cliffhanger") : "";
        row.appendChild(el("span", "t", l.t));
        var body = el("span");
        if (l.spoken) {
          body.appendChild(el("span", "who", l.who + (l.how ? " (" + l.how + ")" : "") + ": "));
          body.appendChild(document.createTextNode(l.text));
        } else {
          body.appendChild(el("span", "what", "[" + l.who + "] "));
          body.appendChild(document.createTextNode(l.text));
        }
        row.appendChild(body); box.appendChild(row);
      });
      det.appendChild(box);
    });
    c.appendChild(det);

    var decide = el("div", "decide");
    var buttons = [];
    [["approve", "Approve"], ["maybe", "Maybe"], ["reject", "Reject"]].forEach(function (pair) {
      var b = el("button", pair[0], pair[1]);
      b.type = "button";
      b.setAttribute("aria-pressed", d.decision === pair[0] ? "true" : "false");
      b.addEventListener("click", function () {
        var cur = decisions[p.key] || {};
        cur.decision = cur.decision === pair[0] ? "" : pair[0];
        decisions[p.key] = cur; save(decisions);
        // update this card in place, so an open transcript and the scroll position stay put
        c.className = "card" + (cur.decision ? " d-" + cur.decision : "");
        buttons.forEach(function (x) { x.setAttribute("aria-pressed", x.className === cur.decision ? "true" : "false"); });
        counts();
      });
      buttons.push(b); decide.appendChild(b);
    });
    c.appendChild(decide);
    var note = el("textarea");
    note.placeholder = "Note for this promo (trim, swap the ending, …)";
    note.setAttribute("aria-label", "Note for promo " + p.rank);
    note.value = d.note || "";
    note.addEventListener("change", function () {
      var cur = decisions[p.key] || {}; cur.note = note.value; decisions[p.key] = cur; save(decisions);
    });
    c.appendChild(note);
    return c;
  }

  function matches(p, q) {
    if (!q) return true;
    var hay = [p.key, p.hook, p.cliff, p.question, p.why, p.trope].concat(p.flags);
    p.parts.forEach(function (part) { part.lines.forEach(function (l) { hay.push(l.who, l.text); }); });
    return hay.join(" ").toLowerCase().indexOf(q) !== -1;
  }

  var shown = 0;
  function counts() {
    var decided = D.promos.filter(function (p) { return (decisions[p.key] || {}).decision; }).length;
    document.getElementById("count").textContent = "Showing " + shown + " of " + D.promos.length +
      " promos · " + decided + " decided";
  }

  function render() {
    var q = document.getElementById("q").value.trim().toLowerCase();
    var kind = kindSel.value, dec = document.getElementById("dec").value;
    var top = parseInt(document.getElementById("show").value, 10);
    var list = document.getElementById("list");
    list.textContent = "";
    shown = 0;
    D.promos.forEach(function (p) {
      var d = (decisions[p.key] || {}).decision || "";
      if (top && p.rank > top) return;
      if (kind && p.kind !== kind) return;
      if (dec === "none" ? d : (dec && d !== dec)) return;
      if (!matches(p, q)) return;
      list.appendChild(card(p)); shown++;
    });
    counts();
  }

  function csvCell(v) { v = v === undefined || v === null ? "" : String(v); return /[",\n]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v; }
  document.getElementById("export").addEventListener("click", function () {
    var rows = [["rank", "promo_id", "segments", "duration_s", "kind", "decision", "note", "hook", "cliffhanger", "final_score"]];
    D.promos.forEach(function (p) {
      var d = decisions[p.key] || {};
      if (!d.decision && !d.note) return;
      rows.push([p.rank, p.id, p.key, p.duration, p.kind, d.decision || "", d.note || "", p.hook, p.cliff, p.scores.final]);
    });
    var blob = new Blob(["﻿" + rows.map(function (r) { return r.map(csvCell).join(","); }).join("\r\n")], { type: "text/csv" });
    var a = el("a"); a.href = URL.createObjectURL(blob); a.download = "promo_decisions.csv";
    document.body.appendChild(a); a.click(); a.remove();
  });

  ["q", "dec", "show"].forEach(function (id) { document.getElementById(id).addEventListener("input", render); });
  kindSel.addEventListener("input", render);
  render();
})();
</script>
</body>
</html>
"""
