# Quality report: rules only, no model

Run `demo/outputs/run_offline`: 9 promos from 4 of 4 episodes. Every number is computed from files in this folder; nothing is hand-edited. What the numbers mean, failures included: `eval/FINDINGS.md`.

| Check | Result |
|---|---|
| Hard checks | 12/12 candidates pass; 0 ranked promos fail |
| Would I post this? (top 10) | not reviewed yet |
| Blind-viewer test | not run (python -m promofinder qc RUN --blind ollama:<model>) |

## 1. Hard checks (100% required)

12/12 candidates pass all 8 rules; 9 promos ranked; re-checked after ranking: 0 fail.

Every cut is built by code at a safe cut point; hook and cliffhanger text is copied from the script by block ID, so the model can't invent a timestamp or a quote in them.

## 5. Would I post this? (top 10)

Not reviewed yet. Review these and record verdicts in `eval/top10_review.toml`:

- #1 Ep 3 00:06.00–00:16.90 + Ep 3 01:04.00–01:15.50
- #2 Ep 1 00:53.20–01:25.80
- #3 Ep 4 00:19.50–01:16.40
- #4 Ep 2 00:03.00–00:58.30
- #5 Ep 3 00:50.50–01:15.50
- #6 Ep 2 01:16.40–01:36.40
- #7 Ep 2 00:03.00–00:13.30 + Ep 2 01:24.90–01:34.70
- #8 Ep 3 00:06.00–00:34.70
- #9 Ep 2 01:19.20–01:36.40 + Ep 3 00:00.00–00:07.90

## 9. Failure gallery: where it picks badly

Selected by code from this run's files; `eval/FINDINGS.md` says which hypotheses they confirm.

**Joined promos whose later part opens on a reply to a line the cut removed**: 0 of 3. A stranger hears an answer to a question they never heard.
