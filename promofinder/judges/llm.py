"""LLM judge: one model call per episode, plus montage calls, all grounded in block IDs.

Episode call: the model sees the episode as a numbered screenplay and the candidate clips, each
with the blocks it may name as its hook (those starting in the clip's first 3 s) and as its
ending (those still on screen in its last 6 s). A clip may join 2-3 parts, and a bridge clip
starts in the previous episode, whose last stretch is then shown first. The JSON Schema sent with
the call allows exactly those IDs, so a model cannot name a block outside the clip or invent one.
It may also propose up to 2 windows the rules missed, by naming two blocks; code builds them.

Montage call (M4): the model gets a short menu of the beats it rated highest across many episodes
and proposes 2-3 beats to join; code builds the parts, and the hard checks decide.

Code then:
- blends each 1-5 model score with the rule score (0.7 model + 0.3 rules by default),
- copies every quote in the output from the script by block ID,
- checks quotes in the model's "why" line against the script and against the clip itself,
- falls back to the rules for an episode whose call fails, and records why.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from jsonschema import Draft202012Validator

from ..config import resolve
from ..llm.base import LLMClient, LLMError, LLMUnavailable
from . import FLAGS, TROPES, EpisodeVerdict, Judgement
from .offline import OfflineJudge, rule_flags

LLM_FLAGS = ("graphic", "spoiler", "confusing", "resolves_at_end", "context_needed")
SCALE = [1, 2, 3, 4, 5]
_KIND = {"CAMERA": "camera", "ACTION": "action", "SOUND_FX": "sound"}
_DOUBLE = re.compile(r'"([^"]{3,})"|“([^”]{3,})”')
# 'single quotes', allowing apostrophes inside words (can't, Nora's) but not as the closing mark
_SINGLE = re.compile(r"(?<![\w'])'((?:[^']|'(?=\w)){3,}?)'(?!\w)")


# --------------------------------------------------------------------------- prompt


def short_id(block_id: str) -> str:
    """'E012-B005' -> 'B005'. The prompt is per episode, so the prefix only costs tokens."""
    return block_id.split("-", 1)[1]


def _episode_of(block_id: str) -> int:
    return int(block_id[1:4])


def prompt_id(block_id: str, n: int) -> str:
    """Short for the episode being judged, full for any other (no clashes)."""
    return short_id(block_id) if _episode_of(block_id) == n else block_id


def _t(seconds: float) -> str:
    return f"{seconds:.2f}".rstrip("0").rstrip(".")


def render_block(b: dict, n: int | None = None) -> str:
    bid = prompt_id(b["block_id"], n) if n is not None else short_id(b["block_id"])
    head = f"{bid} {_t(b['start_s'])}-{_t(b['end_s'])}"
    if b["kind"] == "DIALOGUE":
        sp = b["speaker"]
        voice = f" [{sp['voice']}]" if sp["voice"] != "ON" else ""
        tags = f" ({', '.join(sp['delivery'])})" if sp["delivery"] else ""
        return f"{head} {sp['label']}{voice}{tags}: {b['text']}"
    return f"{head} [{_KIND[b['kind']]}] {b['text']}"


def render_episode(ep: dict) -> str:
    scenes = {s["scene_id"]: s for s in ep["scenes"]}
    lines = [f"EPISODE {ep['episode_number']} ({_t(ep['duration_s'])} s)"]
    shown: set[str] = set()
    for b in ep["blocks"]:
        if b["scene_id"] not in shown:
            shown.add(b["scene_id"])
            scene = scenes[b["scene_id"]]
            lines += ["", f"== Scene {scene['index']}: {scene['heading']} =="]
        lines.append(render_block(b))
    return "\n".join(lines)


def render_earlier(prev: dict, from_s: float, n: int) -> str:
    lines = [f"EARLIER: the end of Episode {prev['episode_number']}, from {_t(from_s)} s "
             f"(block ids carry their episode)"]
    lines += [render_block(b, n) for b in prev["blocks"] if b["start_s"] >= from_s - 1e-6]
    return "\n".join(lines)


def clip_options(eps: dict[int, dict], cand, cfg: dict) -> tuple[list[str], list[str]]:
    """Block IDs a clip may name as its hook (first part's first 3 s) and as its ending (still on
    screen in the last part's last 6 s), by the same rules the hard checks use."""
    e, p, c = cfg["timeline"]["eps_s"], cfg["promo"], cfg["candidates"]
    first, last = cand.segments[0], cand.segments[-1]
    hooks = [b["block_id"] for b in eps[first.episode]["blocks"]
             if first.start_s - e <= b["start_s"] <= first.start_s + p["hook_max_onset_s"] + e]
    ends = [b["block_id"] for b in eps[last.episode]["blocks"]
            if last.start_s - e <= b["start_s"] < last.end_s and b["end_s"] >= last.end_s - c["tail_s"] - e]
    return hooks, ends


def _clip_line(cid: str, cand, n: int, hooks: list[str], ends: list[str]) -> str:
    def span(s) -> str:
        where = "" if s.episode == n else f"Episode {s.episode} "
        return f"{where}{_t(s.start_s)}-{_t(s.end_s)} s"

    if len(cand.segments) == 1:
        head = f"{cid}: {span(cand.segments[0])} ({cand.duration_s:.1f} s)."
    else:
        parts = ", then ".join(span(s) for s in cand.segments)
        head = f"{cid}, {len(cand.segments)} parts: {parts} ({cand.duration_s:.1f} s in all)."
    return f"{head} Hook options: {', '.join(hooks)}. Ending options: {', '.join(ends)}."


def _verdict_schema(hook_ids: list[str], end_ids: list[str]) -> dict:
    fields = {
        "hook_block": {"type": "string", "enum": hook_ids},
        "hook": {"type": "integer", "enum": SCALE},
        "cliff_block": {"type": "string", "enum": end_ids},
        "question": {"type": "string", "maxLength": 120},
        "cliff": {"type": "integer", "enum": SCALE},
        "standalone": {"type": "integer", "enum": SCALE},
        "trope": {"type": "string", "enum": list(TROPES)},
        "flags": {"type": "array", "items": {"type": "string", "enum": list(LLM_FLAGS)},
                  "maxItems": len(LLM_FLAGS)},
        "why": {"type": "string", "maxLength": 220},
    }
    return {"type": "object", "properties": fields, "required": list(fields), "additionalProperties": False}


def response_schema(clips: dict[str, tuple[list[str], list[str]]], all_ids: list[str], max_proposals: int) -> dict:
    """The reply shape for one episode. Each clip gets its own allowed IDs."""
    return {
        "type": "object",
        "properties": {
            "clips": {
                "type": "object",
                "properties": {cid: _verdict_schema(h, e) for cid, (h, e) in clips.items()},
                "required": list(clips),
                "additionalProperties": False,
            },
            "missed": {"type": "array", "maxItems": max_proposals, "items": _verdict_schema(all_ids, all_ids)},
        },
        "required": ["clips", "missed"],
        "additionalProperties": False,
    }


def build_prompt(ep: dict, candidates: list, cfg: dict, prev: dict | None = None) -> tuple[str, dict, dict]:
    """(user prompt, reply schema, {clip label: candidate}) for one episode. `prev` is the previous
    episode, needed when a bridge clip starts in it."""
    n = ep["episode_number"]
    eps = {n: ep} | ({prev["episode_number"]: prev} if prev else {})
    clips, options, lines = {}, {}, ["", "CLIPS"]
    for i, cand in enumerate(candidates, start=1):
        cid = f"C{i}"
        hooks, ends = ([prompt_id(b, n) for b in ids] for ids in clip_options(eps, cand, cfg))
        clips[cid], options[cid] = cand, (hooks, ends)
        lines.append(_clip_line(cid, cand, n, hooks, ends))
    if not candidates:
        lines.append("None: no stretch of this episode passed the rule checks. Return an empty clips object, "
                     "and use missed if the episode holds a promo.")
    earlier = ""
    starts_in_prev = [s.start_s for cand in candidates for s in cand.segments if prev and s.episode != n]
    if starts_in_prev:
        earlier = render_earlier(prev, max(0.0, min(starts_in_prev) - 5.0), n) + "\n\n"
    all_ids = [short_id(b["block_id"]) for b in ep["blocks"]]
    schema = response_schema(options, all_ids, cfg["judge"]["max_proposals"])
    return earlier + render_episode(ep) + "\n" + "\n".join(lines), schema, clips


# --------------------------------------------------------------------------- montage prompt


def beat_menu(judgements: list[Judgement], per_episode: int, min_score: int = 4,
              proposals: list[dict] = ()) -> dict[int, list[dict]]:
    """The beats the episode judge rated highest, per episode: openings it scored >= 4 as a hook
    and endings it scored >= 4 as a cliffhanger. The model's own proposals count too, built or not:
    a beat it rates highly may be unusable alone (Episode 50's discovery comes 6 s before the end)."""
    rated = [(j.hook_block, j.cliff_block, j.detail["model_scores"]) for j in judgements
             if j.detail.get("model_scores")]
    rated += [(p["hook_block"], p["cliff_block"], p["reply"]) for p in proposals]
    best: dict[str, dict] = {}
    for hook, cliff, scores in rated:
        for bid, role, score in ((hook, "opens", scores["hook"]), (cliff, "ends", scores["cliff"])):
            if score >= min_score:
                beat = best.setdefault(bid, {"block_id": bid, "opens": 0, "ends": 0})
                beat[role] = max(beat[role], score)
    menu: dict[int, list[dict]] = {}
    for beat in best.values():
        menu.setdefault(_episode_of(beat["block_id"]), []).append(beat)
    for n, beats in menu.items():
        beats.sort(key=lambda b: (-max(b["opens"], b["ends"]), -min(b["opens"], b["ends"]), b["block_id"]))
        menu[n] = sorted(beats[:per_episode], key=lambda b: b["block_id"])
    return dict(sorted(menu.items()))


def _mmss(seconds: float) -> str:
    return f"{int(seconds // 60)}:{seconds % 60:04.1f}"


def build_montage_prompt(menu: dict[int, list[dict]], episodes: dict[int, dict], max_montages: int) -> tuple[str, dict]:
    blocks = {b["block_id"]: b for n in menu for b in episodes[n]["blocks"]}
    lines, ids = ["BEATS (story order)"], []
    for n, beats in menu.items():
        lines += ["", f"== Episode {n} =="]
        for beat in beats:
            b = blocks[beat["block_id"]]
            rating = ", ".join(f"{role} {beat[role]}/5" for role in ("opens", "ends") if beat[role])
            who = f"{b['speaker']['label']}: " if b["speaker"] else f"[{_KIND[b['kind']]}] "
            lines.append(f"{b['block_id']} at {_mmss(b['start_s'])} ({rating}) {who}{b['text']}")
            ids.append(b["block_id"])
    item = _verdict_schema(ids, ids)
    item["properties"] = {"beats": {"type": "array", "items": {"type": "string", "enum": ids},
                                    "minItems": 2, "maxItems": 3}} | {
        k: v for k, v in item["properties"].items() if k not in ("hook_block", "cliff_block")}
    item["required"] = list(item["properties"])
    schema = {"type": "object", "properties": {"montages": {"type": "array", "maxItems": max_montages, "items": item}},
              "required": ["montages"], "additionalProperties": False}
    return "\n".join(lines), schema


# --------------------------------------------------------------------------- reply checks


def normalise(text: str) -> str:
    text = text.lower().replace("’", "'").replace("‘", "'")
    return " ".join(re.sub(r"[^\w\s']", " ", text).split())


def quoted_phrases(text: str) -> list[str]:
    """Phrases in double or single quotes (straight or curly)."""
    found = [m.group(1) or m.group(2) for m in _DOUBLE.finditer(text)]
    found += [m.group(1) for m in _SINGLE.finditer(text.replace("‘", "'").replace("’", "'"))]
    return found


def unverified_quotes(why: str, episode_text: str) -> list[str]:
    """Quoted phrases in the model's line that do not appear in the screenplay."""
    return [q for q in quoted_phrases(why) if normalise(q) and normalise(q) not in episode_text]


def clean_why(text: str, max_words: int) -> tuple[str, bool]:
    words = text.split()
    if len(words) <= max_words:
        return " ".join(words), False
    return " ".join(words[:max_words]).rstrip(",;:") + "…", True


@dataclass
class _Context:
    prefix: str                # "E012-" for the episode being judged; "" when every ID is full
    blocks: dict[str, dict]    # every block the reply may refer to
    text: str                  # normalised script text of the episodes involved

    def full(self, block_id: str) -> str:
        return block_id if block_id.startswith("E") else self.prefix + block_id


# --------------------------------------------------------------------------- judge


class LLMJudge:
    def __init__(self, client: LLMClient, cfg: dict):
        self.client = client
        self.cfg = cfg
        self.name = client.name
        path = resolve(cfg["judge"]["prompt"])
        self.system = path.read_text(encoding="utf-8").strip()
        self.prompt_name = path.stem
        self.system_sha = hashlib.sha256(self.system.encode("utf-8")).hexdigest()[:12]
        mpath = resolve(cfg["judge"]["montage_prompt"])
        self.montage_system = mpath.read_text(encoding="utf-8").strip()
        self.montage_prompt_name = mpath.stem
        self.fingerprint = client.fingerprint()  # fails here, before any work, if the model is missing
        self.offline = OfflineJudge(cfg["promo"]["hook_target_onset_s"])
        w = cfg["judge"]["llm_weight"]
        self.label = f"{self.name}, prompt {self.prompt_name}, scores {w:.0%} model + {1 - w:.0%} rules"

    @staticmethod
    def _context(episodes: list[dict], prefix: str = "") -> _Context:
        blocks = {b["block_id"]: b for ep in episodes for b in ep["blocks"]}
        return _Context(prefix, blocks, normalise(" | ".join(b["text"] for b in blocks.values())))

    def _call(self, system: str, user: str, schema: dict):
        """(result, error). A down server stops the run; any other failure is reported."""
        result = None
        try:
            result = self.client.complete_json(system, user, schema)
            problems = sorted(e.message for e in Draft202012Validator(schema).iter_errors(result.data))
            if problems:
                raise LLMError("reply breaks the schema: " + "; ".join(problems[:3]))
            return result, None
        except LLMUnavailable:
            raise
        except LLMError as exc:
            return result, str(exc)

    def judge_episode(self, episode: dict, candidates: list, signals: dict, prev: dict | None = None) -> EpisodeVerdict:
        n = episode["episode_number"]
        user, schema, clips = build_prompt(episode, candidates, self.cfg, prev)
        raw: dict = {"episode": n, "judge": self.name, "prompt": self.prompt_name,
                     "system_prompt_sha256": self.system_sha, "user_prompt": user, "schema": schema}
        result, error = self._call(self.system, user, schema)
        if error:
            verdict = self.offline.judge_episode(episode, candidates, signals)
            for j in verdict.judgements:
                j.judge = f"offline (fallback: {self.name} failed)"
            raw |= {"error": error, "response_text": result.text if result else None}
            verdict.raw, verdict.errors = raw, [error]
            verdict.calls = [{"episode": n, "ok": False, "error": error} | (self._usage(result) if result else {})]
            return verdict

        ctx = self._context([episode] + ([prev] if prev else []), prefix=f"E{n:03d}-")
        judgements = [self._to_judgement(cand, result.data["clips"][cid], ctx) for cid, cand in clips.items()]
        proposals = [{"hook_block": ctx.full(m["hook_block"]), "cliff_block": ctx.full(m["cliff_block"]),
                      "reply": m} for m in result.data["missed"]]
        call = {"episode": n, "ok": True} | self._usage(result)
        raw |= {"response_text": result.text, "usage": call}
        return EpisodeVerdict(judgements, proposals, [call], raw)

    def judge_proposal(self, proposal: dict, candidate, episode: dict, signals: dict) -> Judgement:
        ctx = self._context([episode], prefix=f"E{episode['episode_number']:03d}-")
        return self._to_judgement(candidate, proposal["reply"], ctx)

    def propose_montages(self, menu: dict[int, list[dict]], episodes: dict[int, dict], label: str) -> EpisodeVerdict:
        """One montage call over a menu of beats. Proposals carry full block IDs in story order."""
        user, schema = build_montage_prompt(menu, episodes, self.cfg["multiseg"]["montages_per_call"])
        raw: dict = {"montage_call": label, "judge": self.name, "prompt": self.montage_prompt_name,
                     "user_prompt": user, "schema": schema}
        result, error = self._call(self.montage_system, user, schema)
        if error:
            raw |= {"error": error, "response_text": result.text if result else None}
            return EpisodeVerdict([], raw=raw, errors=[error],
                                  calls=[{"montage_call": label, "ok": False, "error": error}
                                         | (self._usage(result) if result else {})])
        proposals = [{"beats": m["beats"], "reply": m | {"hook_block": m["beats"][0], "cliff_block": m["beats"][-1]}}
                     for m in result.data["montages"]]
        call = {"montage_call": label, "ok": True} | self._usage(result)
        raw |= {"response_text": result.text, "usage": call}
        return EpisodeVerdict([], proposals, [call], raw)

    def judge_montage(self, proposal: dict, candidate, episodes: dict[int, dict]) -> Judgement:
        involved = sorted({s.episode for s in candidate.segments})
        return self._to_judgement(candidate, proposal["reply"], self._context([episodes[n] for n in involved]))

    @staticmethod
    def _usage(result) -> dict:
        return {"input_tokens": result.input_tokens, "output_tokens": result.output_tokens,
                "seconds": result.seconds, "compute_seconds": result.compute_seconds,
                "cached": result.cached, "finish": result.finish, "timing": result.timing}

    def _to_judgement(self, cand, reply: dict, ctx: _Context) -> Judgement:
        jc = self.cfg["judge"]
        w = jc["llm_weight"]
        model = {"hook": (reply["hook"] - 1) / 4, "cliffhanger": (reply["cliff"] - 1) / 4,
                 "standalone": (reply["standalone"] - 1) / 4}
        rules = {k: cand.rule_scores[k] for k in model}
        blended = {k: round(w * model[k] + (1 - w) * rules[k], 3) for k in model}
        hook_block, cliff_block = ctx.full(reply["hook_block"]), ctx.full(reply["cliff_block"])

        onset = round(ctx.blocks[hook_block]["start_s"] - cand.segments[0].start_s, 2)
        flags = set(rule_flags(cand.features | {"hook_onset_s": onset}, self.cfg["promo"]["hook_target_onset_s"]))
        flags |= set(reply["flags"])
        disagree = [k for k in ("hook", "cliffhanger") if abs(model[k] - rules[k]) >= jc["disagree_at"]]
        if disagree:
            flags.add("judge_disagrees")

        why, truncated = clean_why(reply["why"], jc["why_max_words"])
        question = " ".join(reply["question"].split())
        # M4 runs: "," and a JSON fragment came back as questions. Every real one so far ends in "?".
        question_ok = question.endswith("?") and len(re.findall(r"[^\W\d_]{2,}", question)) >= 2
        inside = [b for b in ctx.blocks.values() for s in cand.segments
                  if _episode_of(b["block_id"]) == s.episode and s.start_s - 0.05 <= b["start_s"] < s.end_s]
        clip_text = normalise(" | ".join(b["text"] for b in inside))
        not_in_script = unverified_quotes(why, ctx.text)
        outside_clip = [q for q in unverified_quotes(why, clip_text) if q not in not_in_script]
        return Judgement(
            candidate_id=cand.candidate_id,
            judge=self.name,
            hook_block=hook_block,
            hook_score=blended["hook"],
            cliff_block=cliff_block,
            cliff_score=blended["cliffhanger"],
            standalone_score=blended["standalone"],
            why_it_works=why,
            trope=reply["trope"],
            flags=[f for f in FLAGS if f in flags],
            open_question=question if question_ok else None,
            detail={
                "model_scores": {"hook": reply["hook"], "cliff": reply["cliff"], "standalone": reply["standalone"]},
                "rule_scores": rules,
                "disagree": disagree,
                "unverified_quotes": not_in_script,     # quoted words the script never says
                "quotes_outside_clip": outside_clip,    # real lines, but from elsewhere in the episode
                "why_truncated": truncated,
                "question_dropped": not question_ok,    # not a question: left out, never shown
                "found_by": cand.source,
            },
        )
