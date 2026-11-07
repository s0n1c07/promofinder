# Action plan: promo finder for microdrama screenplays

Status: **all six milestones done (M1 parser, M2 rules-only pipeline, M3 local AI judge, M4 multi-segment promos, M5 ranking and QC report, M6 viewer, README, final outputs and write-up).** Start with [`README.md`](README.md) and [`WRITEUP.md`](WRITEUP.md). Evidence log: `eval/FINDINGS.md` (it quotes the confidential pack, so it is in the private submission, not this repo; so are the `outputs/` runs named below). Data survey and schema: [`schema/EPISODE_FORMAT.md`](schema/EPISODE_FORMAT.md), [`schema/episode.schema.json`](schema/episode.schema.json).

## 0. Summary

- **Code decides time, the model decides taste.** A deterministic parser turns every line into a block with a stable ID and exact timestamps. The LLM only ever answers "is this a hook / cliffhanger / understandable?" by naming block IDs. Code converts IDs to timestamps and rejects anything that breaks a rule. The model cannot invent a timestamp or a quote.
- **Small, fixed context.** Each LLM call sees one episode (about 3K tokens). A 400-episode show means 400 small calls, never one huge prompt.
- **Runs without any model.** A rules-only mode produces promos end to end. It is the fallback, the free demo mode, and the baseline we measure the LLM against.
- **The model is a config value.** Anthropic, OpenAI, Gemini, a local Ollama model, or `offline` sit behind one interface. Swapping models also becomes a quality check: where two models disagree, a human looks.
- **Quality is measured, not claimed.** Measurement uses author-written cliffhanger labels hidden in the pack, a small gold set written before the first run, a "blind viewer" comprehension test, ablations and a failure gallery.

## 1. Framing and assumptions

**Input:** one show, 50 episode screenplays (about 2 min each, 103 min in total) with a timestamp on every shot, action, sound cue and line.
**Output:** ranked promos. Each promo has, for every segment in order: episode, start, end, plus the hook, the cliffhanger, one line on why it works, scores and flags.

A promo, in the operational terms the code checks:

| Brief says | Rule the code enforces |
|---|---|
| 20–60 s clip | Sum of segment durations is within [20, 60] s; 25–45 s scores best for Reels/TikTok/Shorts |
| One stretch, or a few joined moments | 1 to 3 segments, each at least 3 s, played in story order |
| Hook in the first 3 s | The hook beat starts at most 3.0 s into the promo (target 1.5 s or less) |
| Ends on a cliffhanger | The last beat raises a question the clip does not answer; cut *before* the answer |
| Makes sense to a stranger | Passes the stranger test (judge) and the blind-viewer test (QC) |
| Right start and end points | Cuts only at block boundaries, never inside a spoken line; shot boundaries preferred |

**Assumptions where the brief is silent** (each goes in the README):
1. Screenplay timestamps equal the timestamps in the final episode video, relative to the episode (every episode starts at 00:00.00).
2. There are no text overlays or voice-over (out of scope), so a promo must make sense from the clip alone.
3. Segments stay in story order in v1. Cold opens (show the payoff first, then rewind) are a v2 option.
4. The goal is cold acquisition of new US viewers. Promos from earlier episodes get a small ranking boost, because a viewer who taps can keep watching straight away. A typical app paywall after the first few free episodes is assumed, not confirmed.
5. Spoilers are allowed when they are the hook (microdrama ads routinely show the twist) but are flagged. Graphic violence (e.g. a hot iron held to an eye) is flagged for policy review, not dropped automatically.
6. The tool must not hard-code anything about this show (no character names in code), because a new show arrives every week.

**Why this problem** (draft; you adjust it against the other OA options): the input is structured and timestamped, so the answer can be precise and checkable. The pain is recurring (every show, every week). The brief's quality bar (hook, cliffhanger, standalone) can be measured. The parsed show index can be reused for other jobs (§12).

## 2. What the data says (full detail in `schema/EPISODE_FORMAT.md`)

| Finding | Consequence for the design |
|---|---|
| The format is very regular: 0 bad timestamps, 0 odd lines, every block body is one line | A deterministic parser can be 100% reliable. No LLM is needed for structure |
| 3,727 blocks on 4 parallel tracks; 171 spoken lines run across a camera cut | A safe cut is a block boundary that is not inside any line, not just any shot boundary |
| 17–57 safe cut points per episode (about one every 3.4 s) | Start and end points can be exact; candidate windows can be listed exhaustively |
| Episodes run 70–200 s; the whole show is about 130K tokens by a 4-characters-per-token estimate (M5 measured about 5K tokens per episode with qwen3.5's tokenizer, so nearer 250K) | It fits in one long-context call on a hosted model, but we deliberately don't do that (§13) |
| Every dialogue line carries delivery tags (619 distinct, e.g. "Screaming", "Gloating, Smug") | A free emotion-intensity signal for rules |
| 64 speaker labels with aliases and hidden identities (three descriptor labels for one stranger whose identity is revealed late) | An alias map is needed, confirmed by a person once per show; it is spoiler-sensitive |
| Place names drift: one house is spelled five ways across episodes | Linking across episodes uses characters, not exact strings; montages that mix spellings get a flag |
| Continuity notes of episode N+1 describe episode N's cliffhanger (12 explicit; 27 more name the episode they continue) | Free labels for evaluation, kept out of the model input |
| 14 episodes literally write "cliffhanger" into their final beat | A useful signal, but new shows may not have it; the tool must work without it |

## 3. Pipeline

```text
 Input/*.txt
    │
 [1] Parse ───────────────► parsed/E###.json        code   (schema-validated, block IDs)
 [2] Show index ──────────► show/cast.toml, arcs    code + LLM suggestion + human OK (once per show)
 [3] Timeline ────────────► safe cut points         code
 [4] Beat signals ────────► signals/E###.json       code   (lexicons, delivery tags, SFX/camera cues)
 [5] Candidates ──────────► candidates.json         code   (single, bridge, tightened, montage menu)
 [6] Judge ───────────────► judge/E###.json         LLM    (scores + IDs only; any provider; cached)
 [7] Validate & snap ─────► promos_valid.json       code   (hard rules; invalid IDs are rejected)
 [8] Rank ────────────────► ranking                 code   (weighted score, diversity, funnel prior)
 [9] QC ──────────────────► qc_report.md            code + LLM (blind test) + human review
[10] Export ──────────────► promos.json / .md / .csv, run_meta.json, raw LLM responses
```

Every stage writes a file. Any stage can be re-run, inspected or reused alone.

### [1] Parse (code)
Regex grammar from the format doc. Keys are matched case-insensitively (Episode 6), blocks are sorted by start time (8 small out-of-order steps), and the Episode 22 sound bridge is allowed. Output follows `episode.schema.json`. Unit tests cover each quirk. **Exit check:** 50/50 files are schema-valid, with exactly the 2 known warnings.

### [2] Show index (code, then LLM suggestion, then human confirmation)
- **Cast and alias map:** code proposes groups (shared name tokens, `(Name)` hints in labels, a name in nearby action text). One small LLM call per show suggests the rest. The result is a YAML file that a person confirms in about 2 minutes. Each entry records the episode where a hidden identity is revealed, so spoilers can be flagged.
- **Arcs:** runs of episodes chained by "Picks up from Episode N". Used to keep multi-segment promos local, so costumes, locations and spoiler depth stay consistent.
- Summaries are not generated. The pack already has a logline per episode, and promo finding doesn't need summaries anyway.

### [3] Timeline (code)
Per episode, list the **safe cut-in** points (block starts not inside a line; a fresh shot is preferred) and **safe cut-out** points (block ends not inside a line or an unfinished action).

### [4] Beat signals (code)
Every block gets transparent scores from YAML lexicons that work for any show:
- **Hook:** physical shock (strike, slap, seize, burn, collapse, explosion, chains), reveals ("recognizes", "who are you", "alive", "true identity"), short punchy lines with a cruel or intense delivery, a "!" ending, SFX stings and gasps, camera emphasis (extreme close-up, push-in, insert).
- **Cliffhanger:** unanswered questions, threats and ultimatums ("or watch him hang"), arrivals and interruptions ("door swings open", "storms in"), explicit markers ("cliffhanger", "cut to black"), and a prior for the last ~15 s of an episode, since episodes in this format usually end on a cliffhanger (the quieter ones, such as Episode 6 by the hearth, are the test of whether that prior misleads).
- **Resolution (bad at the cut):** relief, embrace, smile, thanks, a calm delivery.
- **Context debt (hurts standalone):** an object or name the clip never introduces (the ring, the ledger), a pronoun with nothing to refer to, off-screen voices, alias labels.
- **Look-ahead:** what comes right after a cut point. A cut just before an answer or payoff is a strong cliffhanger cut.

### [5] Candidates
As built (M2 + M4); the evidence is in `eval/FINDINGS.md`.
- **Single:** every legal window is scored: a safe cut-in with a hook in its first 3 s, and a safe cut-out 20–60 s later (about 34K windows per show). The best 6 per episode that overlap at most 50% are kept. Each opening the rules rate 0.6 or more also gets its own best window, up to 2 extra per episode, so a strong line can't be dropped just because a higher-scoring window overlaps it.
- **Tightened (2 parts, same episode):** a strong opening, then a strong ending at least 25 s later, with the middle cut out; the best one per episode. This matters for long episodes such as Episode 36 (200 s).
- **Bridge (2 parts):** the end of episode N straight into the start of N+1, when N+1's continuity note says it picks up "directly" or "immediately". Example: a break-in in the last seconds of one episode joined to the first line of the next.
- **Montage (2–3 parts, across episodes):** built from a *beat menu*: the beats the model rated 4–5 in the episode calls, 4 per episode, as `ID + time + rating + line`, about 30 tokens each. Beats from proposals that couldn't be built as one window count too (Episode 50's discovery). One call per 10 episodes proposes montages by ID; code builds the parts and the hard checks decide. With 25 episodes per call, the model only picked from the top half of each menu (`eval/FINDINGS.md`). The menu stays small at 400 episodes: 40 calls of about 2.5K tokens.
- Every part is built by code at safe cut points, at least 5 s long; each jump costs 0.25 on "makes sense to a stranger", because rules can't tell whether a jump makes sense.
- A later part never opens on an answer to a question the join cuts off (added after M5's QC found 5 of 58 joined promos doing it). It starts at the question instead, or the join is dropped.

### [6] Judge (LLM, provider-agnostic)
**One call per episode.** The prompt holds:
- the rubric, with anchored 1–5 examples for hook, cliffhanger and standalone
- platform rules
- the cast list
- the episode as a compact numbered transcript, e.g. `B037 00:39.1-00:41.2 DLG STATION MANAGER [Breathless, Thrilled]: "Don't you dare dump this call."`, with safe cut points marked
- the candidate windows as ID ranges

For each candidate, the model returns JSON, validated against a schema:
- `hook_block`, `hook_score`, `cliff_block`, `cliff_score`, `open_question`
- `standalone_score`, `missing_context`
- optional `trim_start` / `trim_end` (block IDs)
- `why_it_works` (25 words or fewer)
- `trope` (a fixed list: betrayal, reveal, rescue, cruelty, danger/ultimatum, romance, revenge, scheme)
- `flags` (spoiler, graphic, confusing, slow start, resolves at end)

The model may also propose up to 2 windows the rules missed, as ID ranges, so the LLM adds recall as well as precision. It is told to judge as a stranger who sees only the clip.

### [7] Validate and snap (code)
Every ID must exist and sit inside its window. Trims snap to the nearest safe cut. The hard rules are re-checked: duration, hook within 3 s, segment order, no cut inside a line. Hook and cliffhanger *text in the output is copied from the script by code*. Anything that fails is dropped and counted as a "grounding error" in QC.

### [8] Rank (code)
`final = 0.35 hook + 0.30 cliffhanger + 0.20 standalone + 0.10 pacing + 0.05 length fit`
- Each score blends 70% LLM and 30% rules, so the LLM alone cannot lift a window that has no structural signal. Large disagreements are flagged.
- Penalties apply for graphic content and deep spoilers (built in M5: 0.05 each, `[rank.flag_penalty]`). A small **funnel prior** favours earlier episodes.
- **Diversity:** re-ranking skips near-duplicate time ranges (overlap above 30%) and spreads tropes and episodes. Without it, the top 10 could be ten cuts of the altar scene.
- The weights are starting guesses. They are tuned later from editor approvals (§9).

**"Which to post first" output:** a top 10 with a reason for each, e.g. "strongest cold-audience hook (public jilting in the first 2 s), fully standalone, Episode 1 so viewers can continue instantly, different trope from #2". There is also a posting order (built in M5 as the posting plan in `promos_ranked.md`: premise = Episodes 1–5, payoff = the last 30% of episodes or a spoiler flag):
1. Premise promos from Episodes 1–5 for cold audiences.
2. Escalation promos next.
3. Late payoff promos (the Duke's return, the villains exposed) for re-engaging viewers who already started. For them the spoiler is a reward, not a cost.

## 4. AI, code, or human

| Task | Owner | Why |
|---|---|---|
| Parsing, timestamps, durations, cut points | Code | Exact, free, checkable |
| Hard rules (20–60 s, hook within 3 s, no mid-line cut, at most 3 segments, order) | Code | Must never break; LLMs can't count seconds reliably |
| Candidate windows (recall) | Code, plus up to 2 LLM proposals per episode | Rules are exhaustive and cheap; the LLM catches meaning-only hooks |
| "Is this a hook? Unresolved? Clear to a stranger?" | LLM | A calm line that changes everything ("I didn't call you tonight.") has no shock keyword; only language understanding catches it |
| Hook and cliffhanger text in the output | Code (copied by ID) | Cannot be hallucinated |
| "Why it works" line | LLM | Free text; only length is checked |
| Alias map | Code and LLM propose, human confirms | Once per show; errors here cascade |
| Ranking | Code | Transparent and tunable |
| Spoiler and graphic-content flags | LLM flags, human decides | Policy decision |
| What actually gets posted | Human editor | Taste, brand, legal |

The editor no longer watches 103 minutes. They review a ranked shortlist of about 15 promos as transcripts with exact timecodes, then approve, trim or reject. Each decision is logged to `feedback.jsonl`, which is the training data for later versions.

## 5. Swapping the model

Built in M3 (no API key was available, so the AI judge runs locally):

```text
promofinder/llm/
  base.py        client interface: complete_json(system, user, json_schema) -> parsed JSON + usage
  ollama.py      local models through Ollama (standard library only; no key, no cost)
  cache.py       disk cache keyed by hash(provider, model digest, options, prompts, schema)
promofinder/judges/
  offline.py     no LLM: rule scores mapped onto the same Judgement shape
  llm.py         one call per episode; prompt, per-call schema, blending, grounding checks, fallback
prompts/judge_v2.md, montage_v2.md   the judge's instructions, versioned files (what GEPA would optimise later)
```

Switching is one flag: `--judge ollama:qwen3.5:4b`, `--judge ollama:gemma3:4b`, or `--judge offline`.
- Prompt, schema and validation are shared across providers. A hosted provider (Anthropic, OpenAI, Gemini) is one more class with `fingerprint()` and `complete_json()`; none was added in M3 because it could not be tested without a key.
- Ollama constrains decoding to the JSON Schema, and the schema lists the allowed block IDs per clip, so a reply can't name a block outside its clip.
- Reproducibility comes from the cache plus temperature 0 and a fixed seed. Run-to-run variance is *measured* (§7.6).
- Token usage, model time and the equivalent API cost are logged into `run_meta.json`.
- **Default judge:** a 4B local model (chosen in M3 on speed, reliability and score spread; see `eval/FINDINGS.md`). **Second judge for agreement checks:** a model from another family. With an API key, Claude Opus 5.5 (`claude-opus-5-5`) would be the default judge and Claude Haiku 5.5 the cheap second judge.

## 6. Scale and cost

**Measured in M4** (50 episode calls and 5 montage calls, qwen3.5:4b): 212,978 input and 37,494 output tokens, about 4.3K in and 0.7K out per episode. M3 needed 181,297 and 23,555; M4 judges 54% more clips (joined promos and extra anchors). The original estimate (6K in, 2.5K out) assumed a model that thinks before answering.

**Quality checks (M5), per run, optional:**
- **Blind-viewer test** (gemma3:4b, 116 promos): 89,106 input and 13,773 output tokens, 52 min on the iGPU. That is about $0.02 on Claude Haiku 5.5.
- **Naive baseline** (one call, Episodes 1–3): 15,312 input and 849 output tokens, 7 min.

| Judge | Per 50-ep show | Per 400-ep show | Notes |
|---|---|---|---|
| Local qwen3.5:4b on this laptop (Iris Xe iGPU) | $0, 2 h 24 min | $0, ~19 h | measured; no key, data never leaves the machine |
| Claude Haiku 5.5 ($0.10 / $0.50 per MTok) | ~$0.04 | ~$0.32 | same token counts, list price |
| Claude Sonnet 5.5 ($2 / $10) | ~$0.80 | ~$6.40 | Batch API halves it |
| Claude Opus 5.5 ($4 / $20) | ~$1.60 | ~$12.80 | add thinking tokens if enabled |
| Offline (rules only) | $0, 12 s | $0, ~2 min | |

API figures reuse the local model's token counts, so treat them as ±20%.

- **Time:** parsing, signals and candidates take about 12 s per show. On a laptop the model takes about 2 min per episode: two-thirds writing the reply (about 5 tokens/s) and one-third reading the prompt (camera descriptions are 60% of the prompt text). On a hosted API, calls run in parallel or overnight through the Batch API; a weekly cadence isn't urgent.
- **Growth:** cost grows linearly with episodes, and only new episodes are processed. Context per call stays the same at 400 episodes.
- **People:** today an editor watches about 103 min per show and scrubs for timecodes. Afterwards they review about 15 shortlisted promos, an estimated 20–30 min. (The estimate is an assumption; it should be checked with an editor.)

## 7. Quality checks

1. **Hard validator, 100% pass required:** duration, hook within 3 s, no cut inside a line, segment order and overlap, every ID and timestamp exists. Failures are listed, never hidden.
2. **Silver cliffhanger labels:** for each episode N with a label in N+1's continuity notes, check whether the detected end-of-episode cliffhanger lands on the labelled beat (within ±5 s, matched by text overlap). Reported as a hit rate.
3. **Gold set:** about 15 must-have moments, labelled by hand from the scripts **before the first LLM run** so they can't be tuned to the output. Reported as recall@10 / @20 / @all, plus a precision@10 manual review ("would I post this?").
4. **Blind-viewer test (the stranger proxy):** a separate call, ideally on a different model, sees only the clip's transcript with no show context. It must explain who is fighting about what and what the open question is. Pass rate is reported. It also limits a model grading its own work.
5. **Ablations:** rules-only vs rules+LLM, and optionally a naive "paste all 50 episodes into one prompt" baseline, counting how many of its timestamps are invalid or cut mid-line. That shows, with numbers, why the structured design is needed.
6. **Model agreement and stability:** judge A vs judge B (rank correlation, top-10 overlap), and the same model run twice. Low agreement marks where a human should look.
7. **Failure gallery, the "where it picks badly" answer.** Hypotheses to confirm or reject with real runs:
   - verbal hooks that need setup (a two-word reply that only lands if you heard the question)
   - long, talky trial scenes where intensity words are everywhere and the rules over-score
   - quiet romantic beats the rules under-score
   - montages that jump location or costume
   - name drift and alias labels confusing a stranger
   - a lenient judge giving everything 4/5
   - screenplay timing that differs from the final cut, which can't be verified without video

As built in M5 (`python -m promofinder qc <run>`; results and the verdict on each hypothesis are in `eval/FINDINGS.md`):
- **Precision@10:** a blind reviewer agent judged the top 10 shuffled with 10 random lower-ranked controls. Its verdicts stay a draft until a person reviews them.
- **Blind viewer:** the 4B model's ratings barely vary, so its own account of each promo and what it couldn't place are reported instead of a pass rate.
- **Naive baseline:** Episodes 1–3, not all 50, because the full show doesn't fit this laptop's memory.

## 8. Deliverables and repo layout

```text
README.md                  run steps (3 commands), assumptions, cost, findings
PLAN.md, schema/           this plan + input format
config/default.toml        thresholds, weights, judge provider/model (TOML: read by the standard library, allows comments)
lexicons/*.toml            show-agnostic hook/cliff/resolution vocab
promofinder/               parse, timeline, signals, candidates, llm/, judge, validate, rank, qc, export, cli
promofinder/viewer.py      writes viewer.html per run: ranked promos, transcript per segment, approve/reject
                           (built in M6 as one static page instead of a Streamlit app: no install, opens anywhere)
eval/gold_moments.toml     hand-labelled gold set (+ eval/FINDINGS.md, the evidence log)
tests/                     parser quirks, validator, snapping, ranking
outputs/<run_id>/          UNEDITED outputs on the full pack (required deliverable):
    promos.json            every validated promo with segments, scores, flags
    viewer.html            the editor's page (self-contained; decisions export as CSV)
    promos_ranked.md       top list: segments + hook + cliffhanger + why + why-post-first
    promos.csv             editor sheet with an "approve" column
    qc_report.md           checks, metrics, failure gallery
    run_meta.json          model, config hash, tokens, $ cost, timings
    llm_raw/               raw model responses, untouched
    parsed/                parsed episode JSON (reusable)
```

**Hosting:** a GitHub repo with clear run steps; `--judge offline` runs with no API key. `viewer.html` is a static page, so the committed outputs can also be served as they are (for example on GitHub Pages).

## 9. Later versions (where the "learning" ideas fit)

- **v2, prompt optimisation:** once editor approve/reject data exists, run GEPA-style prompt optimisation and re-fit the ranking weights against approvals and real post metrics (3-second hold, completion, click-through).
- **v3, weight-space:** with thousands of labelled decisions, fine-tune a small open model (LoRA) as the judge. It would cut cost per show to near zero at volume.
- **Video:** with the actual cut, check that the visual hook truly lands in the first 3 s and catch timing drift (shot detection or a vision model). This is also where video embedding models would belong.

## 10. Milestones

| # | Deliverable | Done when |
|---|---|---|
| M0 | Data survey, schema, this plan | ✅ Done |
| M1 | Parser + schema validation + tests | ✅ Done: 50/50 schema-valid, exactly the 2 known warnings, 61 tests pass, all 3,727 blocks trace back to their source lines |
| M2 | Timeline, signals, candidates, rules-only end-to-end run; gold set written | ✅ Done: 33,991 windows scored, 184/184 candidates pass the 8 hard checks, 144 ranked promos from 49/50 episodes in `outputs/run_offline/`; gold set drafted blind (20 moments + 3 montages), since reviewed and accepted unchanged |
| M3 | LLM layer, judge, validator, cache, cost log | ✅ Done with local models (no API key): 3 models trialled, qwen3.5:4b judged all 50 episodes with 0 failed calls; 189/189 candidates (5 proposed by the model) pass the hard checks; 141 promos in `outputs/run_ollama_qwen3.5_4b/`; grounding errors counted (1 invented quote, 1 quote from another clip, in 189 "why" lines); draft-gold top-20 recall 4 → 6 of 20. A second judge (gemma3:4b) measures agreement |
| M4 | Bridge, tightened and arc-montage promos; keep a window for every strong hook (M3 found the altar betrayal dropped by the overlap filter) | ✅ Done: 299/299 candidates pass (33 tightened, 13 bridges, 12 montages); 199 promos, 2 joined ones in the top 10. G02 found (#52); G01's window is built and judged but loses to an overlapping one. Two bugs found and fixed in the full run (montage menu, prompt field order). Montage menus cut from 25 to 10 episodes after an experiment: the model had only picked from the top half of each menu. Episode 50 still has no promo: the model paired its discovery with itself |
| M5 | Ranking + QC report (silver, gold, blind, ablation, agreement, failure gallery) | ✅ Done: `python -m promofinder qc` writes `qc_report.md` from the run folder. 0 of 199 ranked promos fail the hard checks. A blind reviewer said yes to 5 of the top 10 and to 1 of 10 lower-ranked promos (you reviewed the verdicts and kept them). The naive one-prompt baseline broke the brief in 6 of 6 promos. The 4B blind viewer proved too flat to score promos. Graphic/spoiler penalties and a posting plan are in the ranking. New failure found and fixed: 5 of 58 joined promos opened on a reply to a cut question; later parts now start at the question |
| M6 | README, Streamlit viewer, final unedited outputs, one-page write-up | ✅ Done: the README runs in 3 commands (checked on a fresh copy of the project; `pip install` not re-run); `viewer.html` in each run folder (a static page instead of Streamlit, so nothing to install; tested in a browser: transcripts, decisions, filters, CSV export, phone width, light and dark); `WRITEUP.md`; both runs regenerated with the final code |

## 11. Open questions

1. ~~Which API keys do you have?~~ None, so M3 uses local models through Ollama. A hosted provider is one adapter class away.
2. What are the other problems in the OA? This sharpens the "why this problem" paragraph.
3. Is there a deadline? It decides whether arc montages (M4) and the Streamlit viewer (M6) are in scope or stretch goals.

## 12. Reuse beyond promos

The parsed episodes, cut points, beat signals and judge outputs form a **show index**. It can also drive:
- longer trailers and "previously on" recaps
- thumbnail moment picks
- hook text for ad copy
- search ("every slap in season 1")
- continuity QA for writers (e.g. the five spellings of the ducal house)

## 13. Options considered and rejected for v1

| Option | Why not now |
|---|---|
| One long-context call over all 50 episodes (about 130K tokens fits) | Timestamps become generated text that can't be checked; recall drops in the middle of long inputs; every re-run pays for the whole show; doesn't grow to 400 episodes a week. Kept only as an ablation baseline |
| RAG / vector database | RAG answers a question by fetching a few chunks. Here every episode must be scanned anyway (exhaustive mining, not lookup), and one episode is about 3K tokens. Cross-episode linking uses a small structured index (characters × episodes × beats), which is more exact than embeddings |
| V-JEPA / video embeddings | There is no video; embeddings are not decisions (a labelled head and GPUs would still be needed); the screenplay already describes every shot in text |
| GEPA / DSPy prompt optimisation now | Needs a metric to optimise. That metric is editor approvals, which don't exist yet; 12 silver labels would overfit. Planned for v2 |
| Fine-tuning now | No labelled data. Planned for v3 |
| Agent frameworks | The pipeline is fixed. An agent adds variance, cost and opacity |
| Rules only | Misses hooks that are only meaning (a calm line that changes everything). Kept as the baseline and fallback |
