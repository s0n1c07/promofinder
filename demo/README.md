# Demo show: *Night Line*

A made-up 4-episode microdrama, written for this repo in the same screenplay format as the confidential pack the tool was built on. It shares no characters, places or lines with that pack; [`../scripts/check_public.py`](../scripts/check_public.py) checks this.

**The story.** At 2 a.m., late-night radio host Nora takes a call from someone who knows what happened on a flooded bridge ten years ago, and who says she is inside the building.

| Episode | Length | What happens |
|---|---|---|
| 1 | 89 s | The caller threatens to go to the police unless Nora tells the truth live on air, then says she is in the building |
| 2 | 96 s | The station manager orders Nora to stay on air; the caller turns her questions on Dev, the producer |
| 3 | 97 s | The woman in the yellow raincoat walks in: the girl everyone believed drowned. Dev's secret comes out |
| 4 | 76 s | Nora tells the city the truth, the police arrive, and the caller rings again |

- [`make_show.py`](make_show.py) writes the four screenplays to [`Input/`](Input/). Code lays out the timestamps, so every line sits inside its shot and every shot inside its scene.
- [`outputs/run_ollama_qwen3.5_4b/`](outputs/run_ollama_qwen3.5_4b/) is the tool's unedited output with the local AI judge. [`outputs/run_offline/`](outputs/run_offline/) is the rules-only output.
- Open `viewer.html` in either folder, or see it online at [s0n1c07.github.io/promofinder](https://s0n1c07.github.io/promofinder/).

The demo shows how the tool works. It is not evidence of quality, because the same person wrote the show and the tool. The quality numbers in the main [README](../README.md) come from the original pack.

To regenerate:

```bash
python demo/make_show.py
```

```bash
python -m promofinder run --input demo/Input --judge offline --out demo/outputs/run_offline --show-id night_line
```
