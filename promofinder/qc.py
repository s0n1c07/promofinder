"""Quality checks for a run folder (PLAN.md §7), written to qc_report.md.

- Blind-viewer test: a second model sees only what a promo shows and says (no episode, no scores,
  no judge's words) and must say who is against whom, what is left open and what it couldn't follow.
- Naive baseline: a model gets raw screenplays and is asked for promos with timestamps directly;
  code counts how many of its cuts break the brief's rules.
- The report gathers every check from files in the run folder, plus optional other runs, the gold
  set and an editor's review, so it is an unedited output like the promos themselves.
"""

from __future__ import annotations

import json
import random
import re
import tomllib
from collections import Counter
from pathlib import Path
from statistics import mean

from jsonschema import Draft202012Validator

from .compare import compare_runs
from .config import resolve
from .evaluate import _cover, evaluate_run
from .export import _window_text, write_json
from .judges.llm import normalise
from .llm.base import LLMError, LLMUnavailable
from .parse import episode_files, parse_episode
from .timecode import TimecodeError, tc_to_seconds
from .timeline import inside_line, reply_opening

_EPS = 0.05
_KIND = {"CAMERA": "camera", "ACTION": "action", "SOUND_FX": "sound"}
CUT_LINE = "[CUT: the ad jumps to a later moment]"


def load_run(run_dir: str | Path) -> dict:
    d = Path(run_dir)

    def read(name: str):
        return json.loads((d / name).read_text(encoding="utf-8"))

    episodes = {int(f.stem[1:]): json.loads(f.read_text(encoding="utf-8")) for f in sorted((d / "parsed").glob("E*.json"))}
    return {"dir": d, "promos": read("promos.json"), "candidates": read("candidates.json"), "meta": read("run_meta.json"),
            "checks": read("hard_checks.json"), "episodes": episodes}


def _ask(client, system: str, user: str, schema: dict):
    """(result, error). A down server stops the check; any other failure is recorded and counted."""
    try:
        res = client.complete_json(system, user, schema)
    except LLMUnavailable:
        raise
    except LLMError as exc:
        return None, str(exc)
    problems = sorted(e.message for e in Draft202012Validator(schema).iter_errors(res.data))
    return res, ("reply breaks the schema: " + "; ".join(problems[:3])) if problems else None


def _usage(res) -> dict:
    if res is None:
        return {}
    return {"input_tokens": res.input_tokens, "output_tokens": res.output_tokens,
            "compute_seconds": res.compute_seconds, "cached": res.cached}


# --------------------------------------------------------------------------- blind-viewer test

BLIND_SCHEMA = {
    "type": "object",
    "properties": {
        "conflict": {"type": "string", "maxLength": 200},
        "open_question": {"type": "string", "maxLength": 160},
        "confusing": {"type": "array", "items": {"type": "string", "maxLength": 80}, "maxItems": 5},
        "standalone": {"type": "integer", "minimum": 1, "maximum": 5},
        "follow": {"type": "string", "enum": ["yes", "no"]},
        "tap": {"type": "string", "enum": ["yes", "no"]},
    },
    "required": ["conflict", "open_question", "confusing", "standalone", "follow", "tap"],
    "additionalProperties": False,
}


def viewer_line(b: dict) -> str:
    """A block as a viewer meets it: no IDs, no times."""
    if b["kind"] == "DIALOGUE":
        sp = b["speaker"]
        how = ([] if sp["voice"] == "ON" else ["off screen"]) + list(sp["delivery"])
        return f"{sp['label']}{' (' + ', '.join(how) + ')' if how else ''}: {b['text']}"
    return f"[{_KIND[b['kind']]}] {b['text']}"


def clip_transcript(promo: dict, episodes: dict[int, dict]) -> str:
    """Everything a promo shows and says, in order, with a marker where its parts are joined."""
    out: list[str] = []
    for i, seg in enumerate(promo["segments"]):
        if i:
            out.append(CUT_LINE)
        out += [viewer_line(b) for b in episodes[seg["episode"]]["blocks"]
                if seg["start_s"] - 1e-6 <= b["start_s"] < seg["end_s"] - 1e-6]
    return "\n".join(out)


def blind_verdict(reply: dict) -> str:
    """clear: followed it and rates it 4-5 for making sense alone; gaps: followed it, but it leans on
    things it doesn't explain (3 or less); lost: couldn't tell what is at stake. A small model lists
    something under "confusing" almost every time, so the list is evidence, not the verdict."""
    if reply["follow"] != "yes":
        return "lost"
    return "clear" if reply["standalone"] >= 4 else "gaps"


def blind_selection(promos: list[dict], top: int, control: int, seed: int,
                    joined: bool = True) -> list[tuple[dict, str]]:
    """The top promos, every joined promo below them (unless joined is False), and a seeded random
    sample of lower-ranked single windows as a control."""
    group = {p["promo_id"]: "top" for p in promos[:top]}
    if joined:
        group |= {p["promo_id"]: "joined" for p in promos[top:] if p["kind"] != "single"}
    rest = [p for p in promos[top:] if p["kind"] == "single"]
    group |= {p["promo_id"]: "control" for p in random.Random(seed).sample(rest, min(control, len(rest)))}
    return [(p, group[p["promo_id"]]) for p in promos if p["promo_id"] in group]


def run_blind_test(run_dir: str | Path, client, cfg: dict, progress=None) -> dict:
    run, q = load_run(run_dir), cfg["qc"]
    path = resolve(q["blind_prompt"])
    system = path.read_text(encoding="utf-8").strip()
    chosen = blind_selection(run["promos"], q["blind_top"], q["blind_control"], q["seed"], q.get("blind_joined", True))
    results = []
    for i, (p, group) in enumerate(chosen, start=1):
        user = clip_transcript(p, run["episodes"])
        res, error = _ask(client, system, user, BLIND_SCHEMA)
        ok = res is not None and error is None
        results.append({
            "promo_id": p["promo_id"], "rank": p["rank"], "kind": p["kind"], "group": group,
            "segments": _window_text(p), "flags": p["flags"],
            "judge_standalone": (p["judge_detail"].get("model_scores") or {}).get("standalone"),
            "standalone_score": p["scores"]["standalone"],
            "transcript": user, "reply": res.data if ok else None, "response_text": res.text if res else None,
            "verdict": blind_verdict(res.data) if ok else None, "error": error,
        } | _usage(res))
        if progress:
            took = ("cached" if res.cached else f"{res.seconds:.0f} s") if res else ""
            progress(f"[{i:>3}/{len(chosen)}] #{p['rank']} {p['kind']}: {results[-1]['verdict'] or 'FAILED: ' + str(error)}"
                     f" {took}")
    out = {"model": client.fingerprint(), "client": client.name, "prompt": path.stem,
           "selection": {k: q.get(k, True) for k in ("blind_top", "blind_control", "seed", "blind_joined")},
           "results": results}
    (run["dir"] / "qc").mkdir(exist_ok=True)
    write_json(run["dir"] / "qc" / "blind.json", out)
    return out


# --------------------------------------------------------------------------- naive one-prompt baseline

NAIVE_SCHEMA = {
    "type": "object",
    "properties": {"promos": {"type": "array", "maxItems": 6, "items": {
        "type": "object",
        "properties": {
            "segments": {"type": "array", "minItems": 1, "maxItems": 3, "items": {
                "type": "object",
                "properties": {"episode": {"type": "integer"}, "start": {"type": "string", "maxLength": 12},
                               "end": {"type": "string", "maxLength": 12}},
                "required": ["episode", "start", "end"], "additionalProperties": False}},
            "hook_quote": {"type": "string", "maxLength": 200},
            "cliffhanger_quote": {"type": "string", "maxLength": 200},
            "why": {"type": "string", "maxLength": 220},
        },
        "required": ["segments", "hook_quote", "cliffhanger_quote", "why"], "additionalProperties": False}}},
    "required": ["promos"],
    "additionalProperties": False,
}


def _content_words(text: str) -> set[str]:
    return {w for w in normalise(text).split() if len(w) >= 3}


def quote_block(quote: str, blocks: list[dict]) -> dict | None:
    """The block a quote comes from, in list order: the same text (any length, so "Dignity?" counts),
    or one containing the other with at least 3 words on the shorter side, or, for a quote that drops
    an aside, the block holding at least 80% of its words. None: the script never says it."""
    q = normalise(quote)
    if not q:
        return None
    for b in blocks:
        t = normalise(b["text"])
        if t and (t == q or (len((t if len(t) <= len(q) else q).split()) >= 3 and (t in q or q in t))):
            return b
    words = _content_words(quote)
    if len(words) < 3:
        return None
    best = max(blocks, key=lambda b: len(words & _content_words(b["text"])), default=None)
    return best if best is not None and len(words & _content_words(best["text"])) >= 0.8 * len(words) else None


def check_naive(promo: dict, episodes: dict[int, dict], cfg: dict) -> dict:
    """The brief's rules, applied to a promo whose timestamps a model wrote as text."""
    p = cfg["promo"]
    problems: set[str] = set()
    segs, times = [], []
    for s in promo["segments"]:
        ep = episodes.get(s["episode"])
        try:
            a, b = tc_to_seconds(s["start"].strip()), tc_to_seconds(s["end"].strip())
        except TimecodeError:
            problems.add("unreadable time")
            continue
        if ep is None or a < -_EPS or b > ep["duration_s"] + _EPS or b <= a:
            problems.add("outside its episode")
            continue
        times += [(ep, a), (ep, b)]
        if b - a < p["min_segment_s"] - _EPS:
            problems.add("a part under 3 s")
        if inside_line(ep, a, _EPS) or inside_line(ep, b, _EPS):
            problems.add("cuts inside a spoken line")
        segs.append((s["episode"], a, b))
    if segs:
        if not p["min_s"] - _EPS <= sum(b - a for _, a, b in segs) <= p["max_s"] + _EPS:
            problems.add("not 20–60 s")
        if segs != sorted(segs) or any(x[0] == y[0] and y[1] < x[2] - _EPS for x, y in zip(segs, segs[1:])):
            problems.add("parts out of story order")
        inside = [b for e, a, z in segs for b in episodes[e]["blocks"] if a - _EPS <= b["start_s"] < z]
        involved = [b for e in dict.fromkeys(e for e, _, _ in segs) for b in episodes[e]["blocks"]]
        (e0, a0, _), (e1, a1, z1) = segs[0], segs[-1]
        hook = quote_block(promo["hook_quote"], inside + involved)
        if hook is None:
            problems.add("hook not in the script")
        elif not (hook["block_id"].startswith(f"E{e0:03d}")
                  and a0 - _EPS <= hook["start_s"] <= a0 + p["hook_max_onset_s"] + _EPS):
            problems.add("hook not in the first 3 s")
        cliff = quote_block(promo["cliffhanger_quote"], inside[::-1] + involved)
        if cliff is None:
            problems.add("cliffhanger not in the script")
        elif not (cliff["block_id"].startswith(f"E{e1:03d}") and a1 - _EPS <= cliff["start_s"] < z1):
            problems.add("cliffhanger not in the clip")
    off_grid = sum(not any(abs(t - x) <= _EPS for b in ep["blocks"] for x in (b["start_s"], b["end_s"]))
                   for ep, t in times)
    return {"problems": sorted(problems), "times": len(times), "times_matching_no_block_edge": off_grid}


def run_naive_baseline(run_dir: str | Path, input_dir: str | Path, client, cfg: dict, spec: str | None = None) -> dict:
    from .cli import episode_set   # here, not at import time: cli imports this module

    run = load_run(run_dir)
    wanted = episode_set(spec or cfg["qc"]["naive_episodes"])
    texts = []
    for f in episode_files(Path(input_dir)):
        n = parse_episode(f, show_id="qc")["episode_number"]
        if n in wanted:
            texts.append((n, f))
    user = "\n\n".join(f"=== {f.name} ===\n{f.read_text(encoding='utf-8')}" for _, f in sorted(texts))
    path = resolve(cfg["qc"]["naive_prompt"])
    res, error = _ask(client, path.read_text(encoding="utf-8").strip(), user, NAIVE_SCHEMA)
    promos = []
    if res is not None and error is None:
        promos = [pr | check_naive(pr, run["episodes"], cfg) for pr in res.data["promos"]]
    out = {"model": client.fingerprint(), "client": client.name, "prompt": path.stem,
           "episodes": sorted(n for n, _ in texts), "error": error, "promos": promos,
           "response_text": res.text if res else None} | _usage(res)
    (run["dir"] / "qc").mkdir(exist_ok=True)
    write_json(run["dir"] / "qc" / "naive.json", out)
    return out


# --------------------------------------------------------------------------- report


def _pct(k: int, n: int) -> str:
    return f"{k}/{n} ({k / n:.0%})" if n else "0/0"


def _table(head: list[str], rows: list[list]) -> list[str]:
    return ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)] + [
        "| " + " | ".join(str(c).replace("|", "/") for c in r) + " |" for r in rows]


def _label(d: Path) -> str:
    """A run folder's name, with its parent when that tells runs apart (archive/m3/run_x -> m3/run_x)."""
    return d.name if d.parent.name == "outputs" else f"{d.parent.name}/{d.name}"


def _blocks(run: dict) -> dict[str, dict]:
    return {b["block_id"]: b for ep in run["episodes"].values() for b in ep["blocks"]}


def _status(c: dict) -> str:
    if c.get("status") == "ranked":
        return f"ranked #{c['rank']}"
    if c.get("status") == "duplicate":
        return f"folded into {c['duplicate_of']}"
    return "rejected: " + ", ".join(c.get("broken_rules", []))


def _hard_checks(run: dict) -> tuple[list, list[str]]:
    h = run["checks"]
    fail = len(h["final_promos_failing_recheck"])
    lines = ["## 1. Hard checks (100% required)", "",
             f"{h['passed']}/{h['candidates_checked']} candidates pass all {len(h['rules'])} rules; "
             f"{h['final_promos']} promos ranked; re-checked after ranking: {fail} fail.", ""]
    if h["dropped_by_rule"]:
        lines += _table(["Rule", "Candidates dropped"], [[k, v] for k, v in h["dropped_by_rule"].items()]) + [""]
    lines += ["Every cut is built by code at a safe cut point; hook and cliffhanger text is copied from the "
              "script by block ID, so the model can't invent a timestamp or a quote in them.", ""]
    return [["Hard checks", f"{h['passed']}/{h['candidates_checked']} candidates pass; {fail} ranked promos fail"]], lines


def _model_output(run: dict) -> tuple[list, list[str]]:
    llm = run["meta"].get("llm")
    if not llm:
        return [], []
    g, cost = llm["grounding"], run["meta"]["cost"]
    props, mons = llm["proposals"], llm.get("montages", {"proposed": 0, "built": 0, "not_built": {}})
    refused = (props["made"] - props["built"]) + (mons["proposed"] - mons["built"])
    rows = [["Model calls", f"{cost['llm_calls']} ({len(llm['episodes_failed'])} failed)"],
            ["\"Why\" lines quoting words the script never says", _pct(g["why_lines_with_unverified_quotes"],
                                                                     g["judgements_from_model"])],
            ["\"Why\" lines quoting a line from outside their clip", _pct(g["why_lines_quoting_outside_their_clip"],
                                                                        g["judgements_from_model"])],
            ["Questions that were not questions (left out)", g.get("questions_dropped", 0)],
            ["Windows proposed by the model / built", f"{props['made']} / {props['built']}"],
            ["Montages proposed / built", f"{mons['proposed']} / {mons['built']}"]]
    reasons = Counter(props["not_built"]) + Counter(mons["not_built"])
    lines = ["## 2. Model output that code checked", ""] + _table(["Check", "Result"], rows) + [""]
    if reasons:
        lines += ["Why code refused model proposals: " + "; ".join(f"{r} ({n})" for r, n in reasons.most_common()) + ".", ""]
    return [["Model output caught by code", f"{refused} of {props['made'] + mons['proposed']} proposals refused; "
             f"{g['why_lines_with_unverified_quotes']} invented quotes in {g['judgements_from_model']} why lines"]], lines


def _gold(ev: dict | None) -> tuple[list, list[str]]:
    if ev is None:
        return [], []
    rec = ev["recall"]
    rows = [[k.replace("_", ", "), v["top10"], v["top20"], v["all"]] for k, v in rec.items()]
    found = [f"{m['id']} at #{m['rank']}" for m in ev["montages"] if m["rank"] is not None]
    lines = [f"## 3. Gold set (status: {ev['gold_status']})", "",
             "Matching rules fixed before scoring (`eval/FINDINGS.md`). Recall of gold moments:", ""]
    lines += _table(["Moments, match", "Top 10", "Top 20", "All"], rows) + [""]
    lines += [f"Gold montages found: {len(found)}/{len(ev['montages'])}" + (f" ({', '.join(found)})." if found else "."),
              "", "Per moment (best rank; – = not found):", ""]
    lines += _table(["Gold", "Ep", "Priority", "Lenient", "Strict"],
                    [[m["id"], m["episode"], m["priority"], m["lenient_rank"] or "–", m["strict_rank"] or "–"]
                     for m in ev["moments"]]) + [""]
    s = ev["silver"]
    lines += [f"## 4. Silver labels: {s['hits']}/{s['labelled']}", "",
              "The episode's best promo must end within 5 s of the cliffhanger that the next episode's "
              "continuity note describes. A weak signal: a promo tool need not pick an episode's own ending.", ""]
    lines += _table(["Ep", "Described beat", "Best promo", "Its ending", "Hit"],
                    [[x["episode"], f"{x['beat']}: {x['beat_text'][:70]}", x["best_promo"] or "–", x["best_cliff"] or "–",
                      "yes" if x["hit"] else "no"] for x in s["episodes"]]) + [""]
    return [[f"Gold set ({ev['gold_status']})", f"lenient: top 10 {rec['all_lenient']['top10']}, top 20 "
             f"{rec['all_lenient']['top20']}, all {rec['all_lenient']['all']}; \"must\" in top 20 "
             f"{rec['must_lenient']['top20']}"],
            ["Silver labels", f"{s['hits']}/{s['labelled']}"]], lines


def _review(run: dict, path: Path | None) -> tuple[list, list[str]]:
    if not path or not path.exists():
        top = run["promos"][:10]
        return [["Would I post this? (top 10)", "not reviewed yet"]], [
            "## 5. Would I post this? (top 10)", "", "Not reviewed yet. Review these and record verdicts in "
            "`eval/top10_review.toml`:", ""] + [f"- #{p['rank']} {_window_text(p)}" for p in top] + [""]
    review = tomllib.loads(path.read_text(encoding="utf-8"))
    by_window = {r["segments"]: r for r in review["promo"]}
    groups: dict[str, Counter] = {}
    rows = []
    for p in run["promos"]:
        r = by_window.get(_window_text(p))
        if r is None:
            continue
        group = "top 10" if p["rank"] <= 10 else "lower-ranked control"
        groups.setdefault(group, Counter())[r["post"]] += 1
        rows.append([p["rank"], _window_text(p), r["post"], r["reason"]])
    lines = [f"## 5. Would I post this? (status: {review['status']})", "",
             f"Reviewer: {review['reviewer']}. Verdicts were given on transcripts in a shuffled order, without "
             "ranks, scores or the judge's words.", ""]
    lines += _table(["Group", "Yes", "Maybe", "No"],
                    [[g, c["yes"], c["maybe"], c["no"]] for g, c in groups.items()]) + [""]
    lines += _table(["Rank", "Segments", "Post?", "Reason"], sorted(rows)) + [""]
    missing = [p for p in run["promos"][:10] if _window_text(p) not in by_window]
    if missing:
        lines += ["Not reviewed yet (new or changed since the review): "
                  + "; ".join(f"#{p['rank']} {_window_text(p)}" for p in missing) + ".", ""]
    top = groups.get("top 10", Counter())
    note = f" ({10 - len(missing)} of 10 reviewed)" if missing else ""
    return [[f"Would I post this? ({review['status']})", f"top 10: {top['yes']} yes, {top['maybe']} maybe, "
             f"{top['no']} no{note}"]], lines


def _blind(run: dict) -> tuple[list, list[str], dict]:
    path = run["dir"] / "qc" / "blind.json"
    if not path.exists():
        return [["Blind-viewer test", "not run (python -m promofinder qc RUN --blind ollama:<model>)"]], [], {}
    data = json.loads(path.read_text(encoding="utf-8"))
    res = [r for r in data["results"] if r["verdict"]]
    failed = len(data["results"]) - len(res)

    def row(name: str, rs: list[dict]) -> list:
        v = Counter(r["verdict"] for r in rs)
        taps = sum(r["reply"]["tap"] == "yes" for r in rs)
        return [name, len(rs), v["clear"], v["gaps"], v["lost"], _pct(v["clear"], len(rs)),
                f"{mean(r['reply']['standalone'] for r in rs):.1f}", _pct(taps, len(rs))]

    groups = [("Top 10", [r for r in res if r["rank"] <= 10]), ("Top 20", [r for r in res if r["rank"] <= 20]),
              ("Top 50", [r for r in res if r["group"] == "top"])]
    groups += [(f"All {k} promos tested", [r for r in res if r["kind"] == k])
               for k in ("single", "tightened", "bridge", "montage")]
    groups += [("Random lower-ranked single windows (control)", [r for r in res if r["group"] == "control"])]
    lines = ["## 6. Blind-viewer test (the stranger proxy)", "",
             f"`{data['client']}` (prompt `{data['prompt']}`) saw only what each promo shows and says: no episode, "
             "no scores, no judge's words. **clear**: it could say what is at stake and rated the promo 4–5 for "
             "making sense alone; **gaps**: it could say what is at stake but rated it 3 or less; **lost**: it "
             "couldn't say what is at stake."
             + (f" {failed} calls failed." if failed else ""), ""]
    head = ["Tested", "Clear", "Gaps", "Lost", "Clear share", "Blind \"makes sense alone\" (mean, 1–5)", "Would tap"]
    lines += _table(["Promos", *head], [row(n, g) for n, g in groups if g]) + [""]
    by_score = [[s, *row("", [r for r in res if r["judge_standalone"] == s])[1:]]
                for s in sorted({r["judge_standalone"] for r in res if r["judge_standalone"] is not None})]
    if by_score:
        lines += ["The judge saw the whole episode; the blind viewer saw only the promo. Does the judge's "
                  "\"makes sense alone\" score (1–5) predict the blind viewer's?", ""]
        lines += _table(["Judge's score", *head], by_score) + [""]
    ratings = Counter(r["reply"]["standalone"] for r in res)
    common, n_common = ratings.most_common(1)[0]
    taps = sum(r["reply"]["tap"] == "yes" for r in res)
    tops, ctrl = [r for r in res if r["group"] == "top"], [r for r in res if r["group"] == "control"]
    lost = lambda rs: sum(r["verdict"] == "lost" for r in rs)  # noqa: E731
    lines.append(f"Signal check: {n_common / len(res):.0%} of its ratings are {common}, it would tap on "
                 f"{taps / len(res):.0%}, and it was lost on {_pct(lost(tops), len(tops))} of the top {len(tops)}"
                 + (f" against {_pct(lost(ctrl), len(ctrl))} of the random control" if ctrl else "") + ".")
    if n_common / len(res) >= 0.9 or taps / len(res) >= 0.95:
        lines[-1] += (" Its answers barely vary, so they can't rank promos. Read its own account of each promo "
                      "below, and the review in section 5.")
    lines.append("")
    themes = [("how people are related", r"relationship|related|connection|between"),
              ("why something matters", r"significance|importance|meaning|purpose|matter"),
              ("who someone is", r"identity|who (is|are)|role|nature of"),
              ("a past event or motive", r"reason|why|past|history|backstory|motive|happened|cause")]
    gaps = Counter(next((t for t, rx in themes if re.search(rx, c.lower())), "other")
                   for r in res for c in r["reply"]["confusing"] if c.strip())
    if gaps:
        lines += ["What it couldn't place, by theme: " + "; ".join(f"{t} ({n})" for t, n in gaps.most_common()) + ".", ""]
    top10 = sorted((r for r in res if r["rank"] <= 10), key=lambda r: r["rank"])
    if top10:
        lines += ["Its own account of the top 10 (whether it got each one right is for a person to check):", ""]
        lines += _table(["Rank", "Verdict", "Who is against whom", "Left open", "Couldn't place"],
                        [[r["rank"], r["verdict"], r["reply"]["conflict"], r["reply"]["open_question"],
                          "; ".join(r["reply"]["confusing"]) or "–"] for r in top10]) + [""]
    top20 = groups[1][1]
    v = Counter(r["verdict"] for r in top20)
    return [["Blind-viewer test", f"top 20: {v['clear']} clear, {v['gaps']} with gaps, {v['lost']} lost"]], lines, data


def _ablation(run: dict, compare: list[Path], gold: Path | None) -> tuple[list, list[str]]:
    summary, lines = [], ["## 7. Ablations", ""]
    runs = [("this run", run["dir"])] + [(_label(Path(c)), Path(c)) for c in compare]
    rows = []
    for name, d in runs:
        meta = json.loads((d / "run_meta.json").read_text(encoding="utf-8"))
        ev = evaluate_run(d, gold) if gold else None
        g = (f"{ev['recall']['all_lenient']['top10']} / {ev['recall']['all_lenient']['top20']} / "
             f"{ev['recall']['all_lenient']['all']}" if ev else "–")
        shared = "–" if d == run["dir"] else compare_runs(run["dir"], d)["top_n_shared"]
        blind = "–"
        if (d / "qc" / "blind.json").exists():
            top20 = [r for r in json.loads((d / "qc" / "blind.json").read_text(encoding="utf-8"))["results"]
                     if r["verdict"] and r["rank"] <= 20]
            if top20:
                clear = sum(r["verdict"] == "clear" for r in top20)
                blind = f"{_pct(clear, len(top20))}; {mean(r['reply']['standalone'] for r in top20):.1f}"
        rows.append([name, meta.get("judge_label") or meta["judge"], meta["promos"], g,
                     f"{ev['silver']['hits']}/{ev['silver']['labelled']}" if ev else "–", blind, shared])
    if len(rows) > 1:
        lines += _table(["Run", "Judge", "Promos", "Gold lenient: top 10 / 20 / all", "Silver",
                         "Blind viewer, top 20: clear; mean score", "Top 10 shared"], rows) + [""]
    path = run["dir"] / "qc" / "naive.json"
    if path.exists():
        n = json.loads(path.read_text(encoding="utf-8"))
        ps = n["promos"]
        broken = [p for p in ps if p["problems"]]
        times = sum(p["times"] for p in ps)
        off = sum(p["times_matching_no_block_edge"] for p in ps)
        reasons = Counter(x for p in ps for x in p["problems"])
        lines += [f"**Naive one-prompt baseline.** `{n['client']}` (prompt `{n['prompt']}`) got the raw screenplays "
                  f"of Episodes {n['episodes'][0]}–{n['episodes'][-1]} in one prompt ({n.get('input_tokens', 0):,} tokens) "
                  "and the brief's rules, and wrote promos with timestamps as text. Code then applied the rules:", ""]
        if n["error"]:
            lines += [f"The call failed: {n['error']}", ""]
        else:
            lines += _table(["Check", "Result"], [
                ["Promos returned", len(ps)],
                ["Promos breaking at least one rule", _pct(len(broken), len(ps))],
                ["Timestamps matching no block edge in the script", _pct(off, times)],
            ] + [[f"Broken: {r}", k] for r, k in reasons.most_common()]) + [""]
            lines += ["The pipeline can't produce these errors: code builds every cut from block IDs, and the "
                      f"hard checks passed {run['checks']['passed']}/{run['checks']['candidates_checked']} candidates.", ""]
            summary.append(["Naive one-prompt baseline", f"{len(broken)} of {len(ps)} promos break a rule "
                            f"(pipeline: 0 of {run['checks']['final_promos']})"])
    return summary, lines if len(lines) > 2 else []


def _agreement(run: dict, compare: list[Path]) -> tuple[list, list[str]]:
    rows, summary = [], []
    for c in compare:
        r = compare_runs(c, run["dir"])
        sc = r["scores"]
        if not any(v["model_pairs"] for v in sc.values()):
            continue
        cells = [f"{sc[k]['spearman']} / {sc[k]['within_one_point']:.0%}" if sc[k]["within_one_point"] is not None
                 else "–" for k in ("hook", "cliffhanger", "standalone")]
        rows.append([_label(c), r["a"]["judge"], f"{r['top_n_shared']}/{r['top_n']}", r["rank_spearman"], *cells,
                     len(r["review_queue"])])
        summary.append([f"Agreement with {_label(c)}", f"top 10 shared {r['top_n_shared']}/10; hook within 1 point "
                        f"{cells[0].split(' / ')[-1]}"])
    if not rows:
        return [], []
    lines = ["## 8. Agreement and stability", "",
             "Each row compares this run with another run on the same clips: another model (agreement) or the "
             "same model with another prompt or clip list (stability). Scores: Spearman / share within 1 point.", ""]
    lines += _table(["Other run", "Its judge", "Top 10 shared", "Rank correlation", "Hook", "Cliffhanger",
                     "Standalone", "Clips 2+ points apart"], rows) + [""]
    return summary, lines


def reply_to_a_cut_line(promo: dict, episodes: dict[int, dict], gap_s: float = 3.0) -> tuple[dict, dict] | None:
    """(cut line, reply) when a later part of a joined promo opens on an answer to a question the cut
    removed (timeline.reply_opening). The hard checks can't see this: no spoken line is split."""
    for seg in promo["segments"][1:]:
        ep = episodes[seg["episode"]]
        q = reply_opening(ep, seg["start_s"], gap_s)
        if q is not None:
            return q, next(b for b in ep["blocks"] if b["kind"] == "DIALOGUE" and b["start_s"] >= seg["start_s"] - 1e-6)
    return None


def _gallery(run: dict, blind: dict, ev: dict | None) -> list[str]:
    blocks, cands = _blocks(run), run["candidates"]
    lines = ["## 9. Failure gallery: where it picks badly", "",
             "Selected by code from this run's files; `eval/FINDINGS.md` says which hypotheses they confirm.", ""]

    if blind:
        bad = [r for r in blind["results"] if r["verdict"] in ("gaps", "lost") and r["rank"] <= 20]
        if bad:
            lines += ["**Top-20 promos the blind viewer couldn't fully follow**", ""]
            lines += _table(["Rank", "Segments", "Verdict", "Couldn't place", "What it thought was going on"],
                            [[r["rank"], r["segments"], r["verdict"], "; ".join(r["reply"]["confusing"]) or "–",
                              r["reply"]["conflict"]] for r in bad]) + [""]

    judged = [c for c in cands if c.get("judgement") and (c["judgement"].get("detail") or {}).get("model_scores")]
    gaps = []
    for c in judged:
        d = c["judgement"]["detail"]
        model = (d["model_scores"]["hook"] - 1) / 4
        gaps.append((model - d["rule_scores"]["hook"], c))
    for title, pick in (("**Openings the model rates far above the rules** (meaning without keywords, or a calm "
                         "beat over-read)", sorted(gaps, key=lambda x: -x[0])[:6]),
                        ("**Openings the rules rate far above the model** (keywords without drama)",
                         sorted(gaps, key=lambda x: x[0])[:6])):
        rows = [[c["candidate_id"], c["judgement"]["detail"]["model_scores"]["hook"],
                 f"{c['judgement']['detail']['rule_scores']['hook']:.2f}",
                 blocks[c["judgement"]["hook_block"]]["text"][:90], _status(c)] for g, c in pick if abs(g) >= 0.5]
        if rows:
            lines += [title, ""] + _table(["Candidate", "Model hook (1–5)", "Rules hook (0–1)", "Opening beat",
                                           "Outcome"], rows) + [""]

    joined = [p for p in run["promos"] if len(p["segments"]) > 1]
    replies = [(p, hit) for p in joined if (hit := reply_to_a_cut_line(p, run["episodes"]))]
    if joined:
        lines += [f"**Joined promos whose later part opens on a reply to a line the cut removed**: {len(replies)} of "
                  f"{len(joined)}. A stranger hears an answer to a question they never heard.", ""]
        if replies:
            lines += _table(["Rank", "Kind", "Segments", "Cut line", "The part opens on"],
                            [[p["rank"], p["kind"], _window_text(p), f"{q['speaker']['label']}: {q['text']}",
                              f"{a['speaker']['label']}: {a['text']}"] for p, (q, a) in replies]) + [""]

    quotes = [(c, d) for c in judged if (d := c["judgement"]["detail"]).get("unverified_quotes")
              or d.get("quotes_outside_clip")]
    if quotes:
        lines += ["**\"Why\" lines with a quote that doesn't hold**", ""]
        lines += _table(["Candidate", "Problem", "Why line"], [
            [c["candidate_id"], "not in the script: " + ", ".join(d["unverified_quotes"]) if d["unverified_quotes"]
             else "from outside the clip: " + ", ".join(d["quotes_outside_clip"]), c["judgement"]["why_it_works"]]
            for c, d in quotes]) + [""]

    llm = run["meta"].get("llm")
    if llm:
        refused = [[f"Episode {p['episode']}", f"{p['hook_block']} → {p['cliff_block']}", p["reason"]]
                   for p in llm["proposals"]["log"] if p["status"] != "built"]
        refused += [["montage", " → ".join(m["beats"]), m["reason"]]
                    for m in llm.get("montages", {}).get("log", []) if m["status"] != "built"]
        if refused:
            lines += ["**Model proposals code refused**", ""] + _table(["From", "Beats", "Why"], refused) + [""]

    shown = {e for p in run["promos"] for e in p["episodes"]}
    missing = [n for n in sorted(run["episodes"]) if n not in shown]
    if missing:
        lines += ["**Episodes with no promo**", ""]
        for n in missing:
            k = sum(1 for c in cands if any(s["episode"] == n for s in c["segments"]))
            props = [p["reason"] for p in (llm or {}).get("proposals", {}).get("log", []) if p["episode"] == n]
            lines.append(f"- Episode {n}: {k} candidates" + (f"; model proposals refused: {'; '.join(props)}" if props
                                                            else "") + ".")
        lines.append("")

    if ev:
        rows = []
        gold = {g["id"]: g for g in tomllib.loads(Path(ev["gold_path"]).read_text(encoding="utf-8"))["moment"]}
        for m in ev["moments"]:
            if m["lenient_rank"] is not None:
                continue
            g = gold[m["id"]]
            start, end = tc_to_seconds(g["start"]), tc_to_seconds(g["end"])
            best = max(((sum(_cover(s, start, end) for s in c["segments"] if s["episode"] == g["episode"]), c)
                        for c in cands), key=lambda x: (x[0], x[1]["candidate_id"]))
            rows.append([m["id"], g["episode"], g["priority"], f"{best[0]:.0%}", best[1]["candidate_id"],
                         _status(best[1]) if best[0] else "no candidate overlaps it"])
        if rows:
            lines += ["**Gold moments not found** (the candidate covering most of each, and what happened to it)", ""]
            lines += _table(["Gold", "Ep", "Priority", "Best cover", "Candidate", "Outcome"], rows) + [""]
    return lines


def qc_markdown(run_dir: str | Path, cfg: dict, gold_path: str | Path | None = None,
                compare: list[str | Path] = (), review_path: str | Path | None = None) -> str:
    run = load_run(run_dir)
    meta = run["meta"]
    gold = Path(gold_path) if gold_path and Path(gold_path).exists() else None
    ev = evaluate_run(run["dir"], gold) | {"gold_path": str(gold)} if gold else None
    compare = [Path(c) for c in compare]
    sections = [_hard_checks(run), _model_output(run), _gold(ev),
                _review(run, Path(review_path) if review_path else None)]
    s_blind, l_blind, blind = _blind(run)
    sections += [(s_blind, l_blind), _ablation(run, compare, gold), _agreement(run, compare)]
    summary = [row for s, _ in sections for row in s]
    sources = ["files in this folder"] + (["the gold set"] if gold else []) + (["other runs"] if compare else [])
    sources_text = ", ".join(sources[:-1]) + " and " + sources[-1] if len(sources) > 1 else sources[0]
    lines = [f"# Quality report: {meta.get('judge_label') or meta['judge']}", "",
             f"Run `{run['dir'].as_posix()}`: {meta['promos']} promos from {meta['episodes_with_promos']} of "
             f"{meta['episodes']} episodes. Every number is computed from {sources_text}; nothing is hand-edited. "
             "What the numbers mean, failures included: `eval/FINDINGS.md`.", ""]
    lines += _table(["Check", "Result"], summary) + [""]
    for _, body in sections:
        lines += body
    lines += _gallery(run, blind, ev)
    return "\n".join(lines).rstrip() + "\n"
