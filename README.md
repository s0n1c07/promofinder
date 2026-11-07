# promofinder

Finds promo clips in timestamped microdrama screenplays and ranks which to post first. A promo here is 20–60 seconds, opens on a hook in its first 3 seconds, cuts on an unresolved moment, and makes sense to someone who has never seen the show. It can be one stretch of an episode or 2–3 moments joined in story order.

**Live demo: [s0n1c07.github.io/promofinder](https://s0n1c07.github.io/promofinder/)**. It shows the promo viewer on a short made-up show: ranked promos, the posting plan and a transcript of every part, with approve / reject and CSV export.

## About the data

The tool was built and measured on a confidential 50-episode screenplay pack. **The pack is not in this repo, and neither is anything made from it**: the runs, the gold set and the reviews were submitted privately.

- To show the tool working, [`demo/`](demo/) holds *Night Line*, a 4-episode show written for this repo in the same screenplay format, and the tool's unedited outputs on it.
- The demo shows how the tool works. It is not evidence of quality, because the same person wrote the show and the tool. The quality numbers below come from the real pack.
- [`scripts/check_public.py`](scripts/check_public.py) compares every file git would publish against the pack: shared 6-word phrases, whole quoted lines, and character and place names. It must pass before every push. Git also ignores everything outside the folders listed in [`.gitignore`](.gitignore).

## Run it

You need Python 3.11 or newer; no API key, no GPU. In a terminal in this folder (a virtual environment is optional: `python -m venv .venv`, then activate it):

```bash
pip install -r requirements.txt
```

```bash
python -m promofinder run --input demo/Input --judge offline --out outputs/demo_offline
```

That runs the rules-only pipeline on the demo show in a few seconds. Open `outputs/demo_offline/viewer.html` to see the result.

**On a real pack:** put one `.txt` screenplay per episode in `Input/` (the format is in [`schema/EPISODE_FORMAT.md`](schema/EPISODE_FORMAT.md)), then:

```bash
python -m promofinder run --input Input --judge offline
```

```bash
python -m promofinder qc outputs/run_offline
```

The first command handles all 50 episodes of the original pack in under 15 seconds and writes `outputs/run_offline/`. The second adds `qc_report.md`.

## What a run writes

| File | What it is |
|---|---|
| `viewer.html` | The editor's page: promos, posting plan, transcripts, approve / maybe / reject, CSV export. Self-contained; works offline |
| `promos_ranked.md` | The top 10 to post first with hook, cliffhanger, why it works and why it ranks there; the posting plan; every promo in a table |
| `promos.json` | Every promo: segments (episode, start, end), hook and cliffhanger (quoted from the script by block ID), scores, flags, the judge's notes |
| `promos.csv` | An editor sheet with an approve column |
| `qc_report.md` | Hard checks, gold and silver labels, the top-10 review, ablations, model agreement and a failure gallery (from `promofinder qc`) |
| `run_meta.json` | Counts, model and prompt versions, tokens, model time and the same tokens priced on a paid API |
| `llm_raw/` | Every prompt and the model's untouched reply (AI runs only) |
| `candidates.json`, `signals.json`, `hard_checks.json`, `parsed/` | Every stage's output, for inspection or reuse |

The demo's outputs are in [`demo/outputs/`](demo/outputs/): the local AI judge in `run_ollama_qwen3.5_4b/`, rules only in `run_offline/`.

## How it works

**Code decides time; the model decides taste.**
1. A parser turns every shot, action, sound and spoken line into a block with an ID (`E001-B037`) and exact times. All 50 episodes of the original pack parse with 0 errors.
2. Code lists every legal window (33,991 on the original pack) at safe cut points, which never fall inside a spoken line. Show-agnostic word lists and delivery tags score each block as a hook, a cliffhanger, or a resolution.
3. Candidates take four forms:
   - the best single windows per episode;
   - two moments of one episode with the middle cut out;
   - the end of one episode straight into the next, where the next picks up directly;
   - montages of moments from different episodes, whose beats the model picks.
4. The judge names block IDs only, so it can't invent a timestamp or a quote. Hook and cliffhanger text is copied from the script. Each score is 70% model and 30% rules.
5. **8 hard checks** apply to every promo:
   - 20–60 s in total;
   - hook within the first 3 s;
   - cliffhanger inside the last part;
   - 1–3 parts, each at least 3 s;
   - parts in story order;
   - inside the episode;
   - no cut inside a spoken line;
   - every block ID exists.
6. Ranking:
   - **Score:** `0.35 hook + 0.30 cliffhanger + 0.20 standalone + 0.10 pacing + 0.05 length`.
   - **Adjustments:** a small bonus for early episodes (a new viewer can keep watching); penalties for graphic content and spoilers; a spread across episodes and tropes.
   - **Posting plan:** premise promos for cold audiences, then escalation, then late payoffs.

The full design, with the options considered and rejected, is in [`PLAN.md`](PLAN.md). [`WRITEUP.md`](WRITEUP.md) is the one-page summary.

## With a local AI judge (no key, no cost)

The judge runs a small open model on your machine through [Ollama](https://ollama.com). Install Ollama (Windows: `winget install Ollama.Ollama`; macOS: `brew install ollama`; Linux: see ollama.com), then:

```bash
ollama pull qwen3.5:4b
```

```bash
python -m promofinder run --input demo/Input --judge ollama:qwen3.5:4b --out outputs/demo_ai
```

- The demo show takes a few minutes. The 50-episode pack took about 2.5 hours on a laptop without an NVIDIA GPU; try `--episodes 1-3` first.
- On an Intel laptop, starting the Ollama server with `OLLAMA_IGPU_ENABLE=1` was about 1.35× faster.
- Answers are cached in `.cache/llm/`, so a re-run with the same model and prompt is instant and identical.
- Swapping the model is one flag, for example `--judge ollama:gemma3:4b`. A hosted model (Claude, OpenAI, Gemini) is one adapter class with two methods (`promofinder/llm/`).

## Quality checks

```bash
python -m promofinder qc outputs/run_ollama_qwen3.5_4b --compare outputs/run_offline
```

- `--blind ollama:gemma3:4b` adds a blind-viewer test: a second model sees only what each promo shows and says.
- `--naive ollama:qwen3.5:4b` adds a naive baseline: the model reads raw screenplays and writes timestamps itself.
- `python -m promofinder evaluate <run> --gold <file>` scores a run against a gold set and the cliffhanger labels in the continuity notes.
- `python -m promofinder compare <run_a> <run_b>` measures how two judges agree.

**Results on the original pack** (the detail is in the private submission, because it quotes the scripts):
- **Volume:** 199 promos from 49 of 50 episodes; all 298 candidates pass the 8 hard checks.
- **Would I post this?** A reviewer agent judged transcripts without seeing ranks or scores. It said yes to 5 of the top 10 and to 1 of 10 random lower-ranked promos. A person then reviewed these verdicts and kept them all.
- **Naive baseline.** The same model, given raw screenplays, broke the brief in 6 of 6 promos (all of them 1.6–10.9 s long). The pipeline can't produce those errors.
- **Gold set.** 20 hand-labelled moments: 15 are found somewhere in the list, and 3 make the top 20.
- **Where it picks badly:**
  - endings that resolve instead of hanging;
  - verbal hooks that need setup;
  - strangers can't tell how people are related;
  - Episode 50's only strong moment sits in its last 6 seconds;
  - the top of the ranking reshuffles when the prompt changes, so read it as tiers.

## Assumptions

1. Screenplay times equal the final video's times, relative to each episode.
2. There are no text overlays or voice-over, so a promo must make sense from the clip alone.
3. Parts play in story order; cold opens are a later option.
4. The goal is to win new viewers, so earlier episodes get a small ranking bonus.
5. Spoilers and graphic content are allowed but flagged, and cost a little in the ranking (`[rank.flag_penalty]` in `config/default.toml`; 0 turns it off).
6. No character, place or plot of a show is hard-coded. The word lists are generic but written with the original pack's genre in mind (rings, ledgers, potions); review them for a very different genre.

## Cost

| Judge | Per 50-episode show | Notes |
|---|---|---|
| Rules only | $0, 12 s | |
| Local qwen3.5:4b (laptop, Intel iGPU) | $0, about 2.5 h | measured; the data never leaves the machine |
| Claude Haiku 5.5 / Sonnet 5.5 / Opus 5.5 | about $0.04 / $0.80 / $1.60 | the same token counts at list price |

Cost grows linearly with episodes. Each call sees one episode (about 3–5K tokens), so a 400-episode show is 400 small calls, never one huge prompt.

## Tests

```bash
python -m pytest
```

The unit tests use small made-up screenplays. Tests that need the original pack or its gold set skip when those files are absent.

## Layout

```text
Input/                 put a screenplay pack here (git ignores its contents)
demo/                  Night Line, the made-up show (make_show.py writes Input/), and the tool's outputs on it
docs/                  the GitHub Pages site: a landing page and a copy of the demo's viewer
schema/                input format notes and the JSON Schema of a parsed episode
config/default.toml    lengths, thresholds, score weights, ranking, quality checks (config/experiments/: overrides)
lexicons/promo_v1.toml show-agnostic word lists for hooks, cliffhangers and resolution
prompts/               the model's instructions, versioned (judge_v2, montage_v2, blind_v2, naive_v2 in use)
promofinder/           parse, timeline (cut points), signals, windows (candidates), multiseg (joined promos),
                       judges/ (rules and AI judge), llm/ (Ollama client, cache), validate (hard checks),
                       rank, export, viewer, compare, evaluate, qc (quality report), pipeline, cli
scripts/               check_public.py: the leak check against the confidential pack
tests/                 unit tests, plus checks that run only when the original pack is present
PLAN.md, WRITEUP.md    the design and milestones; the one-page write-up
```
