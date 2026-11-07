"""Rules-only judge: turns the rule scores of each candidate into a Judgement.

It needs no key and costs nothing. It is the fallback when no model is configured and the
baseline that LLM judges are measured against. Its "why it works" lines are templates.
"""

from __future__ import annotations

from . import EpisodeVerdict, Judgement

_DANGER_TERMS = {
    "flames", "ablaze", "inferno", "on fire", "sets fire", "explosion", "explodes", "blast", "gun",
    "gunshot", "pistol", "sword", "sabre", "saber", "dagger", "knife", "blade", "noose",
}
_HOOK_PHRASE = {
    "physical": "a physical shock",
    "reveal": "a reveal",
    "humiliation": "public humiliation",
    "threat": "a threat",
    "power": "a power shift",
    "romance": "a romantic beat",
}
_CLIFF_PHRASE = {
    "threat": "a threat left hanging",
    "arrival": "an interruption",
    "revelation": "a realisation",
    "danger": "danger in progress",
    "explicit": "a cliffhanger sting",
    "question": "an open question",
}


def _trope(hook_group: str | None, terms: list[str]) -> str:
    if hook_group == "physical":
        return "danger" if _DANGER_TERMS & set(terms) else "cruelty"
    return {
        "humiliation": "betrayal",
        "reveal": "reveal",
        "threat": "danger",
        "power": "revenge",
        "romance": "romance",
    }.get(hook_group or "", "scheme")


def _cliff_phrase(reasons: list[str]) -> str:
    if "unanswered question" in reasons:
        return "an unanswered question"
    if "cuts before the payoff" in reasons:
        return "the moment before the payoff"
    for reason in reasons:
        if reason in _CLIFF_PHRASE:
            return _CLIFF_PHRASE[reason]
    return "the episode's own cliffhanger" if "episode ending" in reasons else "an unresolved beat"


def rule_flags(f: dict, target_onset: float) -> list[str]:
    """Flags the rules can see from a window's features (timing, structure, word lists)."""
    flags = []
    if f["graphic"]:
        flags.append("graphic")
    if f["hook_onset_s"] > target_onset:
        flags.append("slow_start")
    if f["skipped_hook"] >= 0.6:
        flags.append("late_start")
    if f["released_at_end"] >= 0.5:
        flags.append("resolves_at_end")
    if f["mid_exchange_start"]:
        flags.append("mid_exchange_start")
    if len(f["speakers"]) >= 5:
        flags.append("many_speakers")
    if f["context_debt"] >= 1.0:
        flags.append("context_needed")
    if f["cut_out_mid_action"]:
        flags.append("mid_action_cut")
    return flags


class OfflineJudge:
    name = "offline"

    def __init__(self, target_onset_s: float = 1.5):
        self.target_onset_s = target_onset_s

    def judge_episode(self, episode: dict, candidates: list, signals: dict,
                      prev: dict | None = None) -> EpisodeVerdict:
        return EpisodeVerdict([self._judge(c, signals) for c in candidates])

    def judge_proposal(self, proposal: dict, candidate, episode: dict, signals: dict) -> Judgement:
        return self._judge(candidate, signals)

    def _judge(self, c, signals: dict) -> Judgement:
        f, s = c.features, c.rule_scores
        hook_sig = signals[c.hook_block]
        return Judgement(
            candidate_id=c.candidate_id,
            judge=self.name,
            hook_block=c.hook_block,
            hook_score=s["hook"],
            cliff_block=c.cliff_block,
            cliff_score=s["cliffhanger"],
            standalone_score=s["standalone"],
            why_it_works=(f"Opens on {_HOOK_PHRASE.get(f['hook_group'] or '', 'a charged line')} "
                          f"{f['hook_onset_s']:.1f}s in and cuts on {_cliff_phrase(f['cliff_reasons'])}."),
            trope=_trope(f["hook_group"], hook_sig.terms),
            flags=rule_flags(f, self.target_onset_s),
            open_question=f["open_question"],
        )
