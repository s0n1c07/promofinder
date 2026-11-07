"""Compare two run folders: how far two judges agree, and where a person should look.

Used for model agreement (judge A vs judge B), stability (one model run twice) and
ablations (rules only vs rules + model). Promos are matched across runs by time: same
episode and at least 50% overlap of the shorter window.
"""

from __future__ import annotations

import json
from pathlib import Path

# (model score name, report name, Judgement field used when a run has no model scores)
DIMENSIONS = (("hook", "hook", "hook_score"), ("cliff", "cliffhanger", "cliff_score"),
              ("standalone", "standalone", "standalone_score"))


def load_run(folder: str | Path) -> dict:
    folder = Path(folder)
    read = lambda name: json.loads((folder / name).read_text(encoding="utf-8"))  # noqa: E731
    return {"folder": str(folder), "meta": read("run_meta.json"), "promos": read("promos.json"),
            "candidates": {c["candidate_id"]: c for c in read("candidates.json")}}


def _overlap(a: dict, b: dict) -> float:
    if a["episode"] != b["episode"]:
        return 0.0
    shared = min(a["end_s"], b["end_s"]) - max(a["start_s"], b["start_s"])
    return max(0.0, shared) / max(1e-6, min(a["duration_s"], b["duration_s"]))


def same_window(p: dict, q: dict, min_overlap: float = 0.5) -> bool:
    return len(p["segments"]) == len(q["segments"]) and all(
        _overlap(a, b) >= min_overlap for a, b in zip(p["segments"], q["segments"]))


def _ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return ranks


def spearman(x: list[float], y: list[float]) -> float | None:
    if len(x) < 3:
        return None
    rx, ry = _ranks(x), _ranks(y)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    vx, vy = sum((a - mx) ** 2 for a in rx), sum((b - my) ** 2 for b in ry)
    return round(cov / (vx * vy) ** 0.5, 3) if vx and vy else None


def _label(p: dict) -> str:
    seg = " + ".join(f"Ep {s['episode']} {s['start']}–{s['end']}" for s in p["segments"])
    return f"#{p['rank']} {seg}"


def compare_runs(a: str | Path, b: str | Path, top_n: int = 10) -> dict:
    A, B = load_run(a), load_run(b)
    pa, pb = A["promos"], B["promos"]

    def matched(p: dict, pool: list[dict]) -> dict | None:
        return next((q for q in pool if same_window(p, q)), None)

    top_shared = [p for p in pa[:top_n] if matched(p, pb[:top_n])]
    pairs = [(p, q) for p in pa if (q := matched(p, pb))]
    result: dict = {
        "a": {"folder": A["folder"], "judge": A["meta"]["judge"], "promos": len(pa)},
        "b": {"folder": B["folder"], "judge": B["meta"]["judge"], "promos": len(pb)},
        "top_n": top_n,
        "top_n_shared": len(top_shared),
        "top_n_only_a": [_label(p) for p in pa[:top_n] if not matched(p, pb[:top_n])],
        "top_n_only_b": [_label(q) for q in pb[:top_n] if not matched(q, pa[:top_n])],
        "promos_matched": len(pairs),
        "rank_spearman": spearman([p["rank"] for p, _ in pairs], [q["rank"] for _, q in pairs]),
    }

    # Candidate-level agreement, on the same clip only: rule-built candidates keep their IDs across
    # runs, but a model proposal's ID (E010-L01, M01-02) names whatever that run's model proposed.
    shared_ids = sorted(cid for cid in set(A["candidates"]) & set(B["candidates"])
                        if A["candidates"][cid]["segments"] == B["candidates"][cid]["segments"])
    dims: dict = {}
    for short, long, field in DIMENSIONS:
        xa, xb, exact, near, model_pairs = [], [], 0, 0, 0
        for cid in shared_ids:
            ja, jb = A["candidates"][cid]["judgement"], B["candidates"][cid]["judgement"]
            if not ja or not jb:
                continue
            sa = ja["detail"].get("model_scores", {}).get(short) if ja.get("detail") else None
            sb = jb["detail"].get("model_scores", {}).get(short) if jb.get("detail") else None
            if sa is not None and sb is not None:
                model_pairs += 1
                exact += sa == sb
                near += abs(sa - sb) <= 1
            xa.append(sa if sa is not None else ja[field])
            xb.append(sb if sb is not None else jb[field])
        dims[long] = {
            "candidates": len(xa),
            "spearman": spearman(xa, xb),
            "model_pairs": model_pairs,
            "exact_agreement": round(exact / model_pairs, 3) if model_pairs else None,
            "within_one_point": round(near / model_pairs, 3) if model_pairs else None,
        }
    result["scores"] = dims

    review = []
    for cid in shared_ids:
        ja, jb = A["candidates"][cid]["judgement"], B["candidates"][cid]["judgement"]
        if not (ja and jb and ja.get("detail") and jb.get("detail")):
            continue
        ma, mb = ja["detail"]["model_scores"], jb["detail"]["model_scores"]
        gap = max(abs(ma["hook"] - mb["hook"]), abs(ma["cliff"] - mb["cliff"]))
        if gap >= 2:
            review.append({"candidate_id": cid, "gap": gap, "a": ma, "b": mb,
                           "a_why": ja["why_it_works"], "b_why": jb["why_it_works"]})
    result["review_queue"] = sorted(review, key=lambda r: (-r["gap"], r["candidate_id"]))
    return result


def compare_markdown(r: dict) -> str:
    a, b = r["a"], r["b"]
    lines = [
        f"# Run comparison: {a['judge']} vs {b['judge']}", "",
        f"- A: `{a['folder']}` ({a['promos']} promos)",
        f"- B: `{b['folder']}` ({b['promos']} promos)", "",
        f"**Top {r['top_n']} shared:** {r['top_n_shared']} of {r['top_n']} "
        f"(same episode, windows overlapping at least 50%).", "",
        f"**Promos found by both:** {r['promos_matched']}; rank correlation (Spearman) on those: "
        f"{r['rank_spearman']}.", "",
        "| Score | Candidates | Spearman | Model pairs | Exact | Within 1 point |",
        "|---|---|---|---|---|---|",
    ]
    for name, d in r["scores"].items():
        lines.append(f"| {name} | {d['candidates']} | {d['spearman']} | {d['model_pairs']} | "
                     f"{d['exact_agreement']} | {d['within_one_point']} |")
    lines += ["", f"## Only in A's top {r['top_n']}", ""] + [f"- {x}" for x in r["top_n_only_a"] or ["None."]]
    lines += ["", f"## Only in B's top {r['top_n']}", ""] + [f"- {x}" for x in r["top_n_only_b"] or ["None."]]
    lines += ["", "## Review queue: the two models differ by 2+ points on hook or cliffhanger", ""]
    if r["review_queue"]:
        lines += ["| Candidate | Gap | A (hook/cliff) | B (hook/cliff) | A says | B says |", "|---|---|---|---|---|---|"]
        lines += [f"| {x['candidate_id']} | {x['gap']} | {x['a']['hook']}/{x['a']['cliff']} | "
                  f"{x['b']['hook']}/{x['b']['cliff']} | {x['a_why']} | {x['b_why']} |" for x in r["review_queue"]]
    else:
        lines.append("None.")
    return "\n".join(lines) + "\n"
