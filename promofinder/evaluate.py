"""Score a run against the gold set and the silver cliffhanger labels.

The matching rules were fixed in eval/FINDINGS.md before any judge was scored:
- Lenient hit: a promo from the same episode covers at least 50% of the gold window.
- Strict hit: a promo opens with the gold hook block in its first 3 s and contains the gold
  cliffhanger block.
- Montage hit: every gold segment is at least 50% covered by a segment of one promo, in order.
- Recall is reported within the top 10, the top 20 and all promos.
- Silver: for each episode whose ending the next episode's continuity note describes, the
  episode's best-ranked promo must end with its cliffhanger beat within 5 s of the described beat.
"""

from __future__ import annotations

import json
import math
import re
import tomllib
from collections import Counter
from pathlib import Path

from .timecode import tc_to_seconds

_EPS = 0.05
_STOP = set("""a an the and or but of to in on at by for with from into onto upon as is are was were be been being
has have had his her their its this that these those who whom which while when where after before over
under than then there here she he they them him it out off up down not no yes all any each every""".split())


def _cover(seg: dict, start: float, end: float) -> float:
    """Fraction of [start, end] that a promo segment covers."""
    shared = min(seg["end_s"], end) - max(seg["start_s"], start)
    return max(0.0, shared) / max(1e-6, end - start)


def _contains(seg: dict, block: dict) -> bool:
    return seg["start_s"] - _EPS <= block["start_s"] < seg["end_s"]


def lenient_hit(promo: dict, gold: dict) -> bool:
    start, end = tc_to_seconds(gold["start"]), tc_to_seconds(gold["end"])
    return any(s["episode"] == gold["episode"] and _cover(s, start, end) >= 0.5 for s in promo["segments"])


def strict_hit(promo: dict, gold: dict, blocks: dict[str, dict]) -> bool:
    hook, cliff = blocks.get(gold["hook_block"]), blocks.get(gold["cliff_block"])
    first, last = promo["segments"][0], promo["segments"][-1]
    if hook is None or cliff is None or first["episode"] != gold["episode"] or last["episode"] != gold["episode"]:
        return False
    return (first["start_s"] - _EPS <= hook["start_s"] <= first["start_s"] + 3.0 + _EPS
            and _contains(last, cliff))


def montage_hit(promo: dict, montage: dict) -> bool:
    segs, i = promo["segments"], 0
    for g in montage["segments"]:
        start, end = tc_to_seconds(g["start"]), tc_to_seconds(g["end"])
        while i < len(segs) and not (segs[i]["episode"] == g["episode"] and _cover(segs[i], start, end) >= 0.5):
            i += 1
        if i == len(segs):
            return False
    return True


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z']+", text.lower().replace("’", "'")) if len(w) > 3 and w not in _STOP}


def labelled_beat(episode: dict, note: str, tail_fraction: float = 1 / 3) -> dict:
    """The block of `episode` that a continuity note describes. The notes describe the episode's
    cliffhanger, so only the final third is searched. Shared content words are weighted by rarity
    in the episode (a name on every line counts little, "pocket watch" a lot); the speaker's name
    counts as part of a spoken line; the later block wins a tie."""
    docs = [(b, _words(f"{b['speaker']['label'] if b['speaker'] else ''} {b['text']}")) for b in episode["blocks"]]
    df = Counter(w for _, words in docs for w in words)
    note_words = _words(note)
    start = episode["duration_s"] * (1 - tail_fraction)

    def score(doc: tuple[dict, set[str]]) -> tuple[float, float]:
        block, words = doc
        return round(sum(math.log(len(docs) / df[w]) for w in note_words & words), 9), block["start_s"]

    return max((d for d in docs if d[0]["start_s"] >= start), key=score)[0]


def evaluate_run(run_dir: str | Path, gold_path: str | Path, tops: tuple[int, ...] = (10, 20)) -> dict:
    run_dir = Path(run_dir)
    promos = json.loads((run_dir / "promos.json").read_text(encoding="utf-8"))
    episodes = {int(f.stem[1:]): json.loads(f.read_text(encoding="utf-8"))
                for f in sorted((run_dir / "parsed").glob("E*.json"))}
    blocks = {b["block_id"]: b for ep in episodes.values() for b in ep["blocks"]}
    gold = tomllib.loads(Path(gold_path).read_text(encoding="utf-8"))
    meta = json.loads((run_dir / "run_meta.json").read_text(encoding="utf-8"))

    def best_rank(test) -> int | None:
        return next((p["rank"] for p in promos if test(p)), None)

    moments = []
    for g in gold.get("moment", []):
        moments.append({
            "id": g["id"], "episode": g["episode"], "priority": g["priority"],
            "lenient_rank": best_rank(lambda p, g=g: lenient_hit(p, g)),
            "strict_rank": best_rank(lambda p, g=g: strict_hit(p, g, blocks)),
        })
    montages = [{"id": m["id"], "rank": best_rank(lambda p, m=m: montage_hit(p, m))} for m in gold.get("montage", [])]

    def recall(rows: list[dict], key: str, k: int | None) -> str:
        hits = sum(r[key] is not None and (k is None or r[key] <= k) for r in rows)
        return f"{hits}/{len(rows)}"

    table = {}
    for name, rows in (("all", moments), ("must", [m for m in moments if m["priority"] == "must"])):
        for key in ("lenient_rank", "strict_rank"):
            table[f"{name}_{key.split('_')[0]}"] = {f"top{k}": recall(rows, key, k) for k in tops} | {
                "all": recall(rows, key, None)}

    silver = []
    for n, ep in sorted(episodes.items()):
        note_ep = episodes.get(n + 1)
        if not note_ep or note_ep["header"]["follows_episode"] != n or not note_ep["header"]["prev_cliffhanger_note"]:
            continue
        beat = labelled_beat(ep, note_ep["header"]["prev_cliffhanger_note"])
        best = next((p for p in promos if p["episodes"] == [n]), None)
        hit = best is not None and abs(blocks[best["cliffhanger"]["block_id"]]["start_s"] - beat["start_s"]) <= 5.0
        silver.append({"episode": n, "note": note_ep["header"]["prev_cliffhanger_note"], "beat": beat["block_id"],
                       "beat_text": beat["text"], "best_promo": best["promo_id"] if best else None,
                       "best_cliff": best["cliffhanger"]["block_id"] if best else None, "hit": hit})
    return {"run": str(run_dir), "judge": meta["judge"], "gold_status": gold.get("status"), "promos": len(promos),
            "recall": table, "moments": moments, "montages": montages,
            "silver": {"hits": sum(s["hit"] for s in silver), "labelled": len(silver), "episodes": silver}}


def evaluation_markdown(r: dict) -> str:
    lines = [f"# Gold and silver check: {r['judge']}", "",
             f"Run `{r['run']}`, {r['promos']} promos. Gold set status: **{r['gold_status']}**. "
             "Matching rules: `eval/FINDINGS.md` (fixed before scoring).", "",
             "| Gold moments | Match | Top 10 | Top 20 | All |", "|---|---|---|---|---|"]
    for key, row in r["recall"].items():
        group, match = key.split("_")
        lines.append(f"| {group} | {match} | {row['top10']} | {row['top20']} | {row['all']} |")
    found = [f"{m['id']} at #{m['rank']}" for m in r["montages"] if m["rank"] is not None]
    lines += ["", f"Gold montages found: {len(found)}/{len(r['montages'])}"
              + (f" ({', '.join(found)})." if found else "."), "",
              "| Gold | Ep | Priority | Best lenient rank | Best strict rank |", "|---|---|---|---|---|"]
    lines += [f"| {m['id']} | {m['episode']} | {m['priority']} | {m['lenient_rank'] or '–'} | {m['strict_rank'] or '–'} |"
              for m in r["moments"]]
    s = r["silver"]
    lines += ["", f"## Silver: the episode's best promo ends within 5 s of the described cliffhanger: "
              f"{s['hits']}/{s['labelled']}", "",
              "| Ep | Described beat (matched by shared words) | Best promo | Its cliffhanger block | Hit |",
              "|---|---|---|---|---|"]
    lines += [f"| {x['episode']} | {x['beat']}: {x['beat_text'][:80]} | {x['best_promo'] or '–'} | "
              f"{x['best_cliff'] or '–'} | {'yes' if x['hit'] else 'no'} |" for x in s["episodes"]]
    return "\n".join(lines) + "\n"
