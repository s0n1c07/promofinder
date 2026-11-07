"""Ranking: which promos to post first.

final = weighted judge scores + a small bonus for early episodes (a new viewer who taps can
keep watching straight away) - penalties for flags such as graphic content or a spoiler.
Near-duplicates are folded, then a greedy pass spreads the list across episodes and tropes
so the top 10 is not ten cuts of the same scene.
"""

from __future__ import annotations

from collections import Counter

from .windows import overlap

_SCORE_NAMES = {"hook": "hook", "cliffhanger": "cliffhanger", "standalone": "standing alone"}


def same_promo(a: list, b: list, limit: float) -> bool:
    """Two promos are near-duplicates when they open on the same moment and end on the same moment
    (first parts overlap and last parts overlap). For single windows this is plain overlap; a
    tightened cut that shares only its opening with a single window is a different promo."""
    return overlap(a[0], b[0]) > limit and overlap(a[-1], b[-1]) > limit


def episodes_of(item: dict) -> list[int]:
    return sorted({s.episode for s in item["candidate"].segments})


def final_scores(items: list[dict], n_episodes: int, cfg: dict) -> None:
    w, r = cfg["score"]["weights"], cfg["rank"]
    penalties = r.get("flag_penalty", {})
    for it in items:
        s = it["scores"]
        first_ep = it["candidate"].segments[0].episode
        s["funnel"] = round(r["funnel_bonus"] * (1 - (first_ep - 1) / max(1, n_episodes - 1)), 3)
        it["penalised_for"] = [f for f in penalties if f in it["judgement"].flags]
        s["penalty"] = round(sum(penalties[f] for f in it["penalised_for"]), 3)
        s["final"] = round(sum(w[k] * s[k] for k in w) + s["funnel"] - s["penalty"], 3)


def rank_promos(items: list[dict], n_episodes: int, cfg: dict) -> tuple[list[dict], list[dict]]:
    """Return (ranked items with 'rank' and 'rank_reason', folded duplicates)."""
    r = cfg["rank"]
    final_scores(items, n_episodes, cfg)
    pool = sorted(items, key=lambda it: (-it["scores"]["final"], it["candidate"].candidate_id))

    kept, duplicates = [], []
    for it in pool:
        twin = next((k for k in kept if same_promo(it["candidate"].segments, k["candidate"].segments,
                                                   r["max_overlap"])), None)
        if twin is None:
            kept.append(it)
        else:
            it["duplicate_of"] = twin["candidate"].candidate_id
            duplicates.append(it)

    order = {id(it): i for i, it in enumerate(kept)}
    per_episode: Counter = Counter()
    per_trope: Counter = Counter()
    ranked: list[dict] = []
    remaining = list(kept)
    while remaining:
        def adjusted(it: dict) -> tuple[float, int]:
            crowd = sum(per_episode[e] for e in episodes_of(it))
            value = (it["scores"]["final"] - r["episode_penalty"] * crowd
                     - r["trope_penalty"] * per_trope[it["judgement"].trope])
            return round(value, 6), -order[id(it)]

        best = max(remaining, key=adjusted)
        remaining.remove(best)
        best["rank"] = len(ranked) + 1
        best["rank_reason"] = _reason(best, per_trope)
        ranked.append(best)
        per_episode.update(episodes_of(best))
        per_trope[best["judgement"].trope] += 1
    return ranked, duplicates


def _reason(it: dict, tropes_above: Counter) -> str:
    s, j = it["scores"], it["judgement"]
    first_ep = it["candidate"].segments[0].episode
    strongest = max(_SCORE_NAMES, key=lambda k: s[k])
    parts = [f"final {s['final']:.2f}, strongest on {_SCORE_NAMES[strongest]} ({s[strongest]:.2f})"]
    if first_ep <= 5:
        parts.append(f"from Episode {first_ep}, so a new viewer can keep watching straight away")
    if tropes_above[j.trope] == 0:
        parts.append(f"first {j.trope} promo in the list")
    if it.get("penalised_for"):
        why = " and ".join(_PENALTY_WHY.get(f, f) for f in it["penalised_for"])
        parts.append(f"{s['penalty']:.2f} taken off for {why}")
    if j.flags:
        parts.append("check: " + ", ".join(j.flags))
    return "; ".join(parts)


_PENALTY_WHY = {"graphic": "graphic content (ad-policy check)", "spoiler": "a spoiler (kept for viewers who started)"}


def posting_plan(promos: list[dict], n_episodes: int, cfg: dict) -> list[tuple[str, list[dict]]]:
    """Three tiers in posting order: premise promos for cold audiences, then escalation, then late
    payoffs and spoilers for viewers who already started (PLAN.md §3 [8]). Each tier lists its best
    promos by rank. Show-agnostic: tiers come from episode position and the spoiler flag only."""
    r = cfg["rank"]
    late_from = n_episodes - round(n_episodes * r["payoff_share"]) + 1
    premise_to = min(r["premise_episodes"], late_from - 1)  # a short show has no room for all three tiers

    def tier(p: dict) -> int:
        first = p["segments"][0]["episode"]
        if "spoiler" in p["flags"] or first >= late_from:
            return 2
        return 0 if first <= premise_to else 1

    premise, middle, late = _span(1, premise_to), _span(premise_to + 1, late_from - 1), _span(late_from, n_episodes)
    names = [f"Cold audiences: the premise ({premise or 'no episodes'}, no spoilers)",
             f"Escalation ({middle}, no spoilers)" if middle else "Escalation (none: the show is too short for a middle stretch)",
             f"Re-engage viewers who started: late payoffs and spoilers ({late + ', or ' if late else ''}a spoiler flag)"]
    return [(name, [p for p in promos if tier(p) == i][: r["plan_per_tier"]]) for i, name in enumerate(names)]


def _span(first: int, last: int) -> str | None:
    if first > last:
        return None
    return f"Episode {first}" if first == last else f"Episodes {first}–{last}"
