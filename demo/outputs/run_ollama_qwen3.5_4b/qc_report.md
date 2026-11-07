# Quality report: ollama:qwen3.5:4b, prompt judge_v2, scores 70% model + 30% rules

Run `demo/outputs/run_ollama_qwen3.5_4b`: 10 promos from 4 of 4 episodes. Every number is computed from files in this folder and other runs; nothing is hand-edited. What the numbers mean, failures included: `eval/FINDINGS.md`.

| Check | Result |
|---|---|
| Hard checks | 13/13 candidates pass; 0 ranked promos fail |
| Model output caught by code | 2 of 3 proposals refused; 0 invented quotes in 13 why lines |
| Would I post this? (top 10) | not reviewed yet |
| Blind-viewer test | not run (python -m promofinder qc RUN --blind ollama:<model>) |

## 1. Hard checks (100% required)

13/13 candidates pass all 8 rules; 10 promos ranked; re-checked after ranking: 0 fail.

Every cut is built by code at a safe cut point; hook and cliffhanger text is copied from the script by block ID, so the model can't invent a timestamp or a quote in them.

## 2. Model output that code checked

| Check | Result |
|---|---|
| Model calls | 5 (0 failed) |
| "Why" lines quoting words the script never says | 0/13 (0%) |
| "Why" lines quoting a line from outside their clip | 0/13 (0%) |
| Questions that were not questions (left out) | 0 |
| Windows proposed by the model / built | 0 / 0 |
| Montages proposed / built | 3 / 1 |

Why code refused model proposals: beats are not in story order (2).

## 5. Would I post this? (top 10)

Not reviewed yet. Review these and record verdicts in `eval/top10_review.toml`:

- #1 Ep 1 00:53.20–01:25.80
- #2 Ep 3 00:50.50–01:15.50
- #3 Ep 2 00:03.00–00:13.30 + Ep 2 01:24.90–01:34.70
- #4 Ep 2 01:16.40–01:36.40
- #5 Ep 3 00:06.00–00:16.90 + Ep 3 01:04.00–01:15.50
- #6 Ep 4 00:03.40–00:26.60
- #7 Ep 2 00:03.00–00:58.30
- #8 Ep 1 00:53.20–00:59.00 + Ep 3 01:00.80–01:16.20
- #9 Ep 3 00:06.00–00:34.70
- #10 Ep 2 01:19.20–01:36.40 + Ep 3 00:00.00–00:07.90

## 7. Ablations

| Run | Judge | Promos | Gold lenient: top 10 / 20 / all | Silver | Blind viewer, top 20: clear; mean score | Top 10 shared |
|---|---|---|---|---|---|---|
| this run | ollama:qwen3.5:4b, prompt judge_v2, scores 70% model + 30% rules | 10 | – | – | – | – |
| run_offline | rules only, no model | 9 | – | – | – | 8 |

## 9. Failure gallery: where it picks badly

Selected by code from this run's files; `eval/FINDINGS.md` says which hypotheses they confirm.

**Openings the rules rate far above the model** (keywords without drama)

| Candidate | Model hook (1–5) | Rules hook (0–1) | Opening beat | Outcome |
|---|---|---|---|---|
| E004-C03 | 2 | 0.79 | Close-up on Hollis lunging for the console. | folded into E004-C02 |

**Joined promos whose later part opens on a reply to a line the cut removed**: 0 of 4. A stranger hears an answer to a question they never heard.

**Model proposals code refused**

| From | Beats | Why |
|---|---|---|
| montage | E004-B034 → E001-B026 | beats are not in story order |
| montage | E003-B023 → E002-B022 | beats are not in story order |
