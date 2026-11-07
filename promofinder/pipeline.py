"""End-to-end run: parse -> signals -> candidates -> judge (+ model proposals) -> hard checks -> rank -> export.

Every stage writes a file into the run folder, so any stage can be inspected or reused.
"""

from __future__ import annotations

import re
import time
from collections import Counter
from collections.abc import Callable
from pathlib import Path

from . import __version__
from .config import config_hash, load_config, resolve
from .export import promo_record, ranked_markdown, write_csv, write_json
from .judges import get_judge
from .lexicon import Lexicon
from .parse import ParseError, episode_files, parse_episode
from .rank import posting_plan, rank_promos
from .schema import validate_episode
from .signals import episode_signals
from .timecode import seconds_to_tc
from .timeline import cut_points
from .validate import RULES, block_index, check_promo
from .multiseg import bridge_candidates, build_parts, is_direct_link, make_candidate, tightened_candidates
from .viewer import viewer_html
from .windows import _Index, build_window, episode_candidates


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "show"


# --------------------------------------------------------------------------- parsing


def parse_folder(in_dir: str | Path, out_dir: str | Path, show_id: str | None = None) -> tuple[dict[int, dict], dict]:
    """Parse every episode file, write E###.json + parse_report.{json,md}; return (episodes, report)."""
    in_dir, out_dir = Path(in_dir), Path(out_dir)
    files = episode_files(in_dir)
    show_id = show_id or slug(in_dir.resolve().name)
    out_dir.mkdir(parents=True, exist_ok=True)

    episodes: dict[int, dict] = {}
    rows: list[dict] = []
    errors: list[dict] = []
    warnings: list[dict] = []
    seen: dict[int, str] = {}
    for f in files:
        try:
            doc = parse_episode(f, show_id=show_id)
        except ParseError as exc:
            errors.append({"file": f.name, "error": str(exc)})
            continue
        ep = doc["episode_number"]
        if ep in seen:
            errors.append({"file": f.name, "error": f"episode {ep} was already parsed from {seen[ep]}; skipped"})
            continue
        seen[ep] = f.name
        problems = validate_episode(doc)
        if problems:
            errors.append({"file": f.name, "error": "schema check failed: " + "; ".join(problems[:5])})
        else:
            episodes[ep] = doc
        write_json(out_dir / f"E{ep:03d}.json", doc)
        kinds = Counter(b["kind"] for b in doc["blocks"])
        rows.append({
            "episode": ep, "file": f.name, "output": f"E{ep:03d}.json", "valid": not problems,
            "scenes": len(doc["scenes"]), "blocks": len(doc["blocks"]), "dialogue": kinds["DIALOGUE"],
            "camera": kinds["CAMERA"], "action": kinds["ACTION"], "sound_fx": kinds["SOUND_FX"],
            "duration_s": doc["duration_s"], "warnings": len(doc["warnings"]),
        })
        warnings += [{"episode": ep, **w} for w in doc["warnings"]]

    report = {
        "tool": f"promofinder {__version__}",
        "input": str(in_dir),
        "show_id": show_id,
        "files_found": len(files),
        "episodes_parsed": len(rows),
        "episodes_valid": sum(r["valid"] for r in rows),
        "totals": {k: sum(r[k] for r in rows) for k in ("scenes", "blocks", "dialogue", "camera", "action", "sound_fx")}
        | {"runtime_s": round(sum(r["duration_s"] for r in rows), 2)},
        "warnings_by_code": dict(Counter(w["code"] for w in warnings)),
        "warnings": warnings,
        "errors": errors,
        "episodes": rows,
    }
    write_json(out_dir / "parse_report.json", report)
    (out_dir / "parse_report.md").write_text(parse_report_markdown(report), encoding="utf-8")
    return episodes, report


def parse_headline(r: dict) -> str:
    t = r["totals"]
    return (
        f"{r['episodes_parsed']}/{r['files_found']} files parsed, {r['episodes_valid']} schema-valid | "
        f"{t['scenes']} scenes, {t['blocks']} blocks, {t['runtime_s'] / 60:.1f} min | "
        f"{len(r['errors'])} errors, {len(r['warnings'])} warnings"
    )


def parse_report_markdown(r: dict) -> str:
    t = r["totals"]
    out = [
        "# Parse report", "",
        f"`{r['tool']}` · input `{r['input']}` · show `{r['show_id']}`", "",
        f"**{parse_headline(r)}**", "",
        f"Blocks by kind: {t['camera']} CAMERA · {t['dialogue']} DIALOGUE · {t['action']} ACTION · "
        f"{t['sound_fx']} SOUND FX", "",
        "## Errors", "",
    ]
    out += [f"- {e['error']}" for e in r["errors"]] or ["None."]
    out += ["", "## Warnings", ""]
    if r["warnings"]:
        out += ["| Ep | Line | Code | Message |", "|---|---|---|---|"]
        out += [f"| {w['episode']} | {w['line'] or ''} | {w['code']} | {w['message']} |" for w in r["warnings"]]
    else:
        out.append("None.")
    out += ["", "## Episodes", "",
            "| Ep | Scenes | Blocks | Dialogue | Camera | Action | Sound FX | Duration | Warnings | Valid |",
            "|---|---|---|---|---|---|---|---|---|---|"]
    out += [
        f"| {e['episode']} | {e['scenes']} | {e['blocks']} | {e['dialogue']} | {e['camera']} | {e['action']} | "
        f"{e['sound_fx']} | {seconds_to_tc(e['duration_s'])} | {e['warnings']} | {'yes' if e['valid'] else 'NO'} |"
        for e in r["episodes"]
    ]
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------- full run


def _usage_summary(calls: list[dict], cfg: dict) -> dict:
    ok = [c for c in calls if "input_tokens" in c]
    tokens_in = sum(c["input_tokens"] for c in ok)
    tokens_out = sum(c["output_tokens"] for c in ok)
    compute = round(sum(c["compute_seconds"] for c in ok), 1)
    per_episode = [c["compute_seconds"] for c in ok if "episode" in c]
    prices = cfg.get("cost", {}).get("reference_usd_per_mtok", {})
    return {
        "llm_calls": len(calls),
        "cache_hits": sum(bool(c.get("cached")) for c in ok),
        "input_tokens": tokens_in,
        "output_tokens": tokens_out,
        "usd": 0.0,
        "model_seconds": compute,
        "model_seconds_per_episode": round(sum(per_episode) / len(per_episode), 1) if per_episode else 0.0,
        "api_equivalent_usd": {m: round((tokens_in * p_in + tokens_out * p_out) / 1e6, 4)
                               for m, (p_in, p_out) in prices.items()} if ok else {},
    }


def _progress_line(i: int, total: int, n: int, n_clips: int, verdict, built: int) -> str:
    c = verdict.calls[-1]
    usage = ""
    if "input_tokens" in c:
        took = "cached" if c["cached"] else f"{c['seconds']:.0f} s"
        usage = f" | {c['input_tokens']:,} in / {c['output_tokens']:,} out tokens, {took}"
    status = "ok" if c.get("ok") else f"FAILED, rules used instead: {c.get('error', '')[:100]}"
    return (f"[{i:>2}/{total}] Episode {n}: {n_clips} clips judged, {len(verdict.proposals)} proposed, "
            f"{built} built{usage} | {status}")


def _llm_summary(judge, judgements: list, errors: list, proposal_log: list) -> dict:
    model_js = [j for j in judgements if j.detail]
    return {
        "model": getattr(judge, "fingerprint", {}),
        "prompt": getattr(judge, "prompt_name", None),
        "episodes_failed": errors,
        "proposals": {
            "made": len(proposal_log),
            "built": sum(p["status"] == "built" for p in proposal_log),
            "not_built": dict(Counter(p["reason"].split(":")[0] for p in proposal_log if p["status"] != "built")),
            "log": proposal_log,
        },
        "grounding": {
            "judgements_from_model": len(model_js),
            "why_lines_with_unverified_quotes": sum(bool(j.detail["unverified_quotes"]) for j in model_js),
            "why_lines_quoting_outside_their_clip": sum(bool(j.detail["quotes_outside_clip"]) for j in model_js),
            "why_lines_cut_to_length": sum(j.detail["why_truncated"] for j in model_js),
            "questions_dropped": sum(j.detail["question_dropped"] for j in model_js),
        },
        "judge_disagrees_with_rules": sum(bool(j.detail["disagree"]) for j in model_js),
        "model_score_counts": {k: {str(v): n for v, n in sorted(Counter(j.detail["model_scores"][k]
                                                                         for j in model_js).items())}
                               for k in ("hook", "cliff", "standalone")},
    }


def _montages(judge, episodes: dict[int, dict], cuts: dict[int, list], signals: dict, lex: Lexicon, cfg: dict,
              judgements: dict, proposed: list[dict], candidates: list, calls: list, errors: list, raw_dir: Path,
              progress: Callable[[str], None] | None) -> list[dict]:
    """LLM judges only: offer the model its own best-rated beats, a chunk of episodes at a time,
    and build the montages it proposes. Code chooses every cut; the hard checks decide later.
    proposed: every window the model proposed in its episode calls, built or not."""
    if not hasattr(judge, "propose_montages"):
        return []
    from .judges.llm import beat_menu

    m = cfg["multiseg"]
    menu = beat_menu(list(judgements.values()), m["beats_per_episode"], proposals=proposed)
    order = sorted(episodes)
    size = m["montage_chunk_episodes"]
    chunks = [order[i:i + size] for i in range(0, len(order), size)]
    log: list[dict] = []
    for k, chunk in enumerate(chunks, start=1):
        sub = {n: menu[n] for n in chunk if n in menu}
        if len(sub) < 2:
            continue
        label = f"montages {k}: Episodes {chunk[0]}-{chunk[-1]}"
        verdict = judge.propose_montages(sub, episodes, label)
        calls += verdict.calls
        errors += [{"montage_call": label, "error": e} for e in verdict.errors]
        if verdict.raw is not None:
            raw_dir.mkdir(exist_ok=True)
            write_json(raw_dir / f"montages_{k}.json", verdict.raw)
        built = 0
        for i, prop in enumerate(verdict.proposals, start=1):
            entry = {"call": k, "beats": prop["beats"]}
            if len({b[:4] for b in prop["beats"]}) < 2:
                log.append(entry | {"status": "not built", "reason": "all beats are from one episode"})
                continue
            parts, why_not = build_parts(episodes, cuts, prop["beats"], cfg)
            cand = None
            if parts is not None:
                idxs = {n: _Index(episodes[n], signals, lex) for n in {pt[0] for pt in parts}}
                cand = make_candidate(parts, idxs, cfg, f"M{k:02d}-{i:02d}", "montage", "llm",
                                      hook_block=prop["beats"][0], cliff_block=prop["beats"][-1])
                why_not = why_not or "no rule score"
            if cand is None:
                log.append(entry | {"status": "not built", "reason": why_not})
                continue
            built += 1
            candidates.append(cand)
            judgements[cand.candidate_id] = judge.judge_montage(prop, cand, episodes)
            log.append(entry | {"status": "built", "candidate_id": cand.candidate_id,
                                "segments": [[s.episode, s.start_s, s.end_s] for s in cand.segments]})
        if progress and verdict.calls:
            progress(_montage_progress(k, len(chunks), chunk, verdict, built))
    return log


def _montage_progress(k: int, total: int, chunk: list[int], verdict, built: int) -> str:
    c = verdict.calls[-1]
    usage = ""
    if "input_tokens" in c:
        took = "cached" if c["cached"] else f"{c['seconds']:.0f} s"
        usage = f" | {c['input_tokens']:,} in / {c['output_tokens']:,} out tokens, {took}"
    status = "ok" if c.get("ok") else f"FAILED: {c.get('error', '')[:100]}"
    return (f"[montages {k}/{total}] Episodes {chunk[0]}-{chunk[-1]}: {len(verdict.proposals)} proposed, "
            f"{built} built{usage} | {status}")


def run(input_dir: str | Path, out_dir: str | Path, judge_spec: str = "offline",
        config_path: str | Path | None = None, show_id: str | None = None, judge=None,
        episodes_only: set[int] | None = None, progress: Callable[[str], None] | None = None) -> dict:
    """Find promos for every episode in input_dir and write the run folder. Returns run_meta.

    judge: a ready Judge object (used by tests); otherwise one is built from judge_spec.
    episodes_only: judge only these episode numbers (quick trials); ranking still uses the show length.
    progress: called with one line per judged episode (LLM judges only).
    """
    clock = time.perf_counter()
    timings: dict[str, float] = {}

    def lap(name: str) -> None:
        nonlocal clock
        now = time.perf_counter()
        timings[name] = round(now - clock, 3)
        clock = now

    cfg = load_config(config_path)
    lex = Lexicon.load(resolve(cfg["lexicon"]["path"]))
    judge = judge or get_judge(judge_spec, cfg)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    episodes, parse_report = parse_folder(input_dir, out / "parsed", show_id)
    if not episodes:
        raise RuntimeError(f"no episode in {input_dir} could be parsed; see {out / 'parsed' / 'parse_report.md'}")
    show_length = len(episodes)
    if episodes_only:
        episodes = {n: ep for n, ep in episodes.items() if n in episodes_only}
        if not episodes:
            raise ValueError(f"none of episodes {sorted(episodes_only)} were parsed")
    lap("parse")

    signals: dict[str, object] = {}
    cuts: dict[int, list] = {}
    singles: dict[int, list] = {}
    windows_scored = 0
    for n, ep in sorted(episodes.items()):
        sig = episode_signals(ep, lex)
        signals.update(sig)
        cuts[n] = cut_points(ep, cfg["timeline"]["eps_s"])
        singles[n], scored = episode_candidates(ep, sig, cuts[n], lex, cfg)
        windows_scored += scored
    # Each episode's group: its single windows, its best tightened cut, and a bridge from the
    # previous episode when this one picks up directly where that one stopped (judged together).
    groups: dict[int, list] = {}
    bridge_from: dict[int, dict] = {}
    for n, ep in sorted(episodes.items()):
        groups[n] = singles[n] + tightened_candidates(ep, signals, cuts[n], lex, cfg, singles[n])
        prev = episodes.get(n - 1)
        if prev is not None and is_direct_link(prev, ep, cfg):
            bridges = bridge_candidates(prev, ep, signals, cuts, lex, cfg)
            if bridges:
                groups[n] += bridges
                bridge_from[n] = prev
    candidates = [c for n in sorted(groups) for c in groups[n]]
    without_candidates = [n for n in sorted(groups) if not groups[n]]
    lap("candidates")

    judgements, calls, errors, proposal_log, proposed = {}, [], [], [], []
    raw_dir = out / "llm_raw"
    for i, n in enumerate(sorted(groups), start=1):
        cands = groups[n]
        verdict = judge.judge_episode(episodes[n], cands, signals, bridge_from.get(n))
        for j in verdict.judgements:
            judgements[j.candidate_id] = j
        proposed += verdict.proposals
        built = 0
        for k, prop in enumerate(verdict.proposals, start=1):
            cand, why_not = build_window(episodes[n], signals, cuts[n], lex, cfg, prop["hook_block"],
                                         prop["cliff_block"], f"E{n:03d}-L{k:02d}")
            entry = {"episode": n, "hook_block": prop["hook_block"], "cliff_block": prop["cliff_block"]}
            if cand is None:
                proposal_log.append(entry | {"status": "not built", "reason": why_not})
                continue
            built += 1
            candidates.append(cand)
            judgements[cand.candidate_id] = judge.judge_proposal(prop, cand, episodes[n], signals)
            proposal_log.append(entry | {"status": "built", "candidate_id": cand.candidate_id,
                                         "start_s": cand.segments[0].start_s, "end_s": cand.segments[0].end_s})
        calls += verdict.calls
        errors += [{"episode": n, "error": e} for e in verdict.errors]
        if verdict.raw is not None:
            raw_dir.mkdir(exist_ok=True)
            write_json(raw_dir / f"E{n:03d}.json", verdict.raw)
        if progress and verdict.calls:
            progress(_progress_line(i, len(groups), n, len(cands), verdict, built))
    montage_log = _montages(judge, episodes, cuts, signals, lex, cfg, judgements, proposed, candidates, calls,
                            errors, raw_dir, progress)
    if hasattr(judge, "system"):
        raw_dir.mkdir(exist_ok=True)
        (raw_dir / "system_prompt.md").write_text(judge.system + "\n", encoding="utf-8")
    lap("judge")

    index = block_index(episodes)
    items, rejected = [], []
    for c in candidates:
        j = judgements.get(c.candidate_id)
        broken = ["no_judgement"] if j is None else check_promo(c.segments, j.hook_block, j.cliff_block,
                                                                  episodes, index, cfg)
        if broken:
            rejected.append({"candidate_id": c.candidate_id, "rules": broken})
            continue
        items.append({
            "candidate": c,
            "judgement": j,
            "scores": {"hook": j.hook_score, "cliffhanger": j.cliff_score, "standalone": j.standalone_score,
                       "pacing": c.rule_scores["pacing"], "length": c.rule_scores["length"]},
        })
    ranked, duplicates = rank_promos(items, show_length, cfg)
    promos = [promo_record(it, index, episodes) for it in ranked]
    still_failing = [p["promo_id"] for p, it in zip(promos, ranked)
                     if check_promo(it["candidate"].segments, it["judgement"].hook_block,
                                    it["judgement"].cliff_block, episodes, index, cfg)]
    lap("check_and_rank")

    status = {it["candidate"].candidate_id: {"status": "ranked", "rank": it["rank"]} for it in ranked}
    status |= {it["candidate"].candidate_id: {"status": "duplicate", "duplicate_of": it["duplicate_of"]}
               for it in duplicates}
    status |= {r["candidate_id"]: {"status": "rejected", "broken_rules": r["rules"]} for r in rejected}
    candidate_rows = [
        c.to_dict() | {"judgement": judgements[c.candidate_id].to_dict() if c.candidate_id in judgements else None}
        | status[c.candidate_id]
        for c in candidates
    ]
    hard_checks = {
        "rules": RULES,
        "candidates_checked": len(candidates),
        "passed": len(items),
        "dropped": len(rejected),
        "dropped_by_rule": dict(Counter(r for x in rejected for r in x["rules"])),
        "dropped_candidates": rejected,
        "final_promos": len(promos),
        "final_promos_failing_recheck": still_failing,
    }
    meta = {
        "tool": f"promofinder {__version__}",
        "judge": judge.name,
        "judge_label": getattr(judge, "label", "rules only, no model"),
        "input": str(input_dir),
        "config_sha256": config_hash(cfg),
        "lexicon": cfg["lexicon"]["path"],
        "episodes": len(episodes),
        "episodes_judged": sorted(episodes) if episodes_only else "all",
        "parse_errors": len(parse_report["errors"]),
        "parse_warnings": len(parse_report["warnings"]),
        "windows_scored": windows_scored,
        "candidates": len(candidates),
        "candidates_by_kind": dict(Counter(c.kind for c in candidates)),
        "candidates_passing_hard_checks": len(items),
        "duplicates_folded": len(duplicates),
        "promos": len(promos),
        "promos_by_kind": dict(Counter(p["kind"] for p in promos)),
        "episodes_with_promos": len({e for p in promos for e in p["episodes"]}),
        "episodes_without_candidates": without_candidates,
        "top_n": cfg["rank"]["top_n"],
        "cost": _usage_summary(calls, cfg),
    }
    if calls:
        meta["llm"] = _llm_summary(judge, [judgements[c.candidate_id] for c in candidates
                                           if c.candidate_id in judgements], errors, proposal_log)
        meta["llm"]["montages"] = {
            "proposed": len(montage_log),
            "built": sum(m["status"] == "built" for m in montage_log),
            "not_built": dict(Counter(m["reason"].split(":")[0] for m in montage_log if m["status"] != "built")),
            "log": montage_log,
        }

    write_json(out / "signals.json", {k: v.to_dict() for k, v in signals.items()})
    write_json(out / "candidates.json", candidate_rows)
    write_json(out / "hard_checks.json", hard_checks)
    write_json(out / "promos.json", promos)
    write_csv(out / "promos.csv", promos)
    plan = posting_plan(promos, show_length, cfg)
    meta["posting_plan"] = [{"tier": name, "promo_ids": [p["promo_id"] for p in tier]} for name, tier in plan]
    (out / "promos_ranked.md").write_text(ranked_markdown(promos, meta, cfg["rank"]["top_n"], plan),
                                          encoding="utf-8")
    (out / "viewer.html").write_text(viewer_html(promos, meta, episodes), encoding="utf-8")
    lap("export")
    meta["timings_s"] = timings
    write_json(out / "run_meta.json", meta)
    return meta
