"""Writers for a run folder: promos.json / .md / .csv and supporting files."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from .timecode import seconds_to_tc
from .validate import RULES

_EPS = 1e-6
_KIND_LABEL = {"single": "one stretch", "tightened": "middle cut out", "bridge": "across an episode break",
               "montage": "montage"}


def write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def quote(block: dict) -> str:
    if block["kind"] == "DIALOGUE":
        return f'{block["speaker"]["label"]}: "{block["text"]}"'
    return f"({block['kind'].replace('_', ' ').lower()}) {block['text']}"


def _segment_record(seg, episodes: dict[int, dict]) -> dict:
    blocks = episodes[seg.episode]["blocks"]
    inside = [b for b in blocks if seg.start_s - _EPS <= b["start_s"] < seg.end_s - _EPS]
    return {
        "episode": seg.episode,
        "start": seconds_to_tc(seg.start_s),
        "end": seconds_to_tc(seg.end_s),
        "start_s": seg.start_s,
        "end_s": seg.end_s,
        "duration_s": seg.duration_s,
        "first_block": inside[0]["block_id"] if inside else None,
        "last_block": inside[-1]["block_id"] if inside else None,
    }


def promo_record(it: dict, index: dict[str, dict], episodes: dict[int, dict]) -> dict:
    c, j = it["candidate"], it["judgement"]
    hook, cliff = index[j.hook_block], index[j.cliff_block]
    first = c.segments[0]
    return {
        "promo_id": f"P{it['rank']:03d}",
        "rank": it["rank"],
        "kind": c.kind,
        "found_by": c.source,
        "episodes": sorted({s.episode for s in c.segments}),
        "segments": [_segment_record(s, episodes) for s in c.segments],
        "duration_s": c.duration_s,
        "hook": {
            "block_id": hook["block_id"],
            "at": seconds_to_tc(hook["start_s"]),
            "onset_s": round(hook["start_s"] - first.start_s, 2),
            "text": quote(hook),
        },
        "cliffhanger": {
            "block_id": cliff["block_id"],
            "at": seconds_to_tc(cliff["start_s"]),
            "text": quote(cliff),
            "open_question": j.open_question,
            "question_by": "model" if j.detail else "script",
        },
        "why_it_works": j.why_it_works,
        "rank_reason": it["rank_reason"],
        "scores": it["scores"],
        "trope": j.trope,
        "flags": j.flags,
        "judge": j.judge,
        "judge_detail": {k: j.detail[k] for k in ("model_scores", "rule_scores", "unverified_quotes",
                                                   "quotes_outside_clip") if k in j.detail},
        "candidate_id": c.candidate_id,
    }


def _window_text(p: dict) -> str:
    return " + ".join(f"Ep {s['episode']} {s['start']}–{s['end']}" for s in p["segments"])


def write_csv(path: Path, promos: list[dict]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        out = csv.writer(fh)
        out.writerow(["rank", "promo_id", "segments", "duration_s", "hook_at_s", "hook", "cliffhanger",
                      "why_it_works", "final_score", "trope", "flags", "found_by", "approve (y/n)", "editor_notes"])
        for p in promos:
            out.writerow([p["rank"], p["promo_id"], _window_text(p), p["duration_s"], p["hook"]["onset_s"],
                          p["hook"]["text"], p["cliffhanger"]["text"], p["why_it_works"], p["scores"]["final"],
                          p["trope"], " ".join(p["flags"]), p["found_by"], "", ""])


def _cell(text: str, limit: int = 90) -> str:
    text = text.replace("|", "/")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def ranked_markdown(promos: list[dict], meta: dict, top_n: int,
                    plan: list[tuple[str, list[dict]]] | None = None) -> str:
    lines = [
        f"# Ranked promos ({meta.get('judge_label') or meta['judge']})",
        "",
        f"{len(promos)} promos from {meta['episodes_with_promos']} of {meta['episodes']} episodes. "
        f"Every promo passed all {len(RULES)} hard checks (`hard_checks.json`). "
        f"Times are relative to each episode, as in the screenplays.",
        "",
    ]
    if "llm" in meta:
        llm, g = meta["llm"], meta["llm"]["grounding"]
        failed = len(llm["episodes_failed"])
        lines += [
            f"How to read this: hook and cliffhanger text is copied from the script by block ID. Scores blend the "
            f"model's 1–5 ratings with the rules. The \"why it works\" lines and the viewer's question are written by "
            f"the model and only partly checked: of its {g['judgements_from_model']} why lines, "
            f"{g['why_lines_with_unverified_quotes']} quote words the script never says and "
            f"{g['why_lines_quoting_outside_their_clip']} quote lines from outside their clip (marked \"Check\" below). "
            + (f"{g['questions_dropped']} of its questions were not questions (e.g. \",\") and are left out. "
               if g.get("questions_dropped") else "")
            + f"{llm['proposals']['built']} promos were proposed by the model and built by "
            f"code. The model failed on {failed} episode{'s' if failed != 1 else ''} (rules used instead).",
            "",
        ]
    lines += [
        f"## Post first (top {min(top_n, len(promos))})",
        "",
    ]
    for p in promos[:top_n]:
        s, cliff = p["scores"], p["cliffhanger"]
        question = cliff["open_question"]
        if not question or (cliff["question_by"] == "script" and question in cliff["text"]):
            left_open = ""
        elif cliff["question_by"] == "script":
            left_open = f" — left open: \"{question}\""
        else:
            left_open = f" — the viewer is left asking: {question}"
        lines += [
            f"### {p['rank']}. {_window_text(p)} ({p['duration_s']:.1f} s)"
            + (f" · {len(p['segments'])} parts, {_KIND_LABEL[p['kind']]}" if len(p["segments"]) > 1 else "")
            + (" · found by the model" if p["found_by"] == "llm" else ""),
            "",
            f"- **Hook** ({p['hook']['onset_s']:.1f} s in): {p['hook']['text']}",
            f"- **Cliffhanger**: {cliff['text']}{left_open}",
            f"- **Why it works**: {p['why_it_works']}",
            f"- **Why post it here**: {p['rank_reason']}",
            f"- Scores: hook {s['hook']:.2f} · cliffhanger {s['cliffhanger']:.2f} · standalone {s['standalone']:.2f} · "
            f"pacing {s['pacing']:.2f} · length {s['length']:.2f} · final **{s['final']:.2f}** · trope {p['trope']}"
            + (f" · flags: {', '.join(p['flags'])}" if p["flags"] else ""),
        ]
        d = p["judge_detail"]
        if "model_scores" in d:
            m, r = d["model_scores"], d["rule_scores"]
            lines.append(f"- Model said (1–5): hook {m['hook']} · cliffhanger {m['cliff']} · standalone {m['standalone']}; "
                         f"rules said (0–1): hook {r['hook']:.2f} · cliffhanger {r['cliffhanger']:.2f} · "
                         f"standalone {r['standalone']:.2f}")
        if d.get("unverified_quotes"):
            lines.append(f"- Check: the why line quotes words not found in the script: {d['unverified_quotes']}")
        if d.get("quotes_outside_clip"):
            lines.append(f"- Check: the why line quotes lines from outside this clip: {d['quotes_outside_clip']}")
        lines.append("")
    if plan:
        lines += ["## Posting plan", "",
                  "The same promos grouped by audience, in the order to post them. Numbers are ranks.", ""]
        for i, (name, tier) in enumerate(plan, start=1):
            lines += [f"**{i}. {name}**", ""]
            lines += [f"- #{p['rank']} · {_window_text(p)} · {_cell(p['hook']['text'], 100)}" for p in tier]
            lines += ["- (none)"] if not tier else []
            lines.append("")
    lines += [
        "## All promos",
        "",
        "| # | Segments | Kind | Length | Hook | Cliffhanger | Final | Flags |",
        "|---|---|---|---|---|---|---|---|",
    ]
    lines += [
        f"| {p['rank']} | {_window_text(p)} | {p['kind']} | {p['duration_s']:.1f} s | {_cell(p['hook']['text'])} | "
        f"{_cell(p['cliffhanger']['text'])} | {p['scores']['final']:.2f} | {', '.join(p['flags'])} |"
        for p in promos
    ]
    return "\n".join(lines) + "\n"
