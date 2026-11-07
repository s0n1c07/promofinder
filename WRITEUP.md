# Finding promos in microdrama screenplays: one-page write-up

**The task.** Take a 50-episode microdrama screenplay pack and find promo clips: 20–60 s, a hook in the first 3 s, ending on a cliffhanger, clear to someone who has never seen the show. Say why each works, rank which to post first, and check where the tool picks badly.

**The approach: code decides time; the model decides taste.**
- A parser gives every shot, action and line an ID and exact times: 3,727 blocks, 0 errors.
- Code lists every legal window (33,991). It never cuts inside a spoken line, and scores hooks and cliffhangers with show-agnostic word lists.
- Candidates are single stretches, two moments with the middle cut out, episode-break bridges, and montages whose beats the model picks.
- A local 4B model (qwen3.5 through Ollama: no key, $0) judges candidates by naming block IDs. It can't invent a timestamp or a quote, because the text is copied from the script.
- Eight hard checks gate every promo. The ranking adds diversity, an early-episode bonus and penalties for graphic content and spoilers, and ends in a posting plan by audience.
- An editor gets `viewer.html` (approve / reject, with CSV export), `promos_ranked.md` and `qc_report.md`. Rules alone also work (12 s for the pack) as the fallback and the baseline.

**Results on the full pack** (`outputs/run_ollama_qwen3.5_4b/`, in the private submission: the pack is confidential)
- **Volume:** 199 promos from 49 of 50 episodes; all 298 candidates pass the hard checks.
- **Variety:** two of the top 10 join moments: #4 (Episode 18, an explosion cut to its aftermath) and #6 (Episode 25, a one-word accusation cut to a later line of fear).
- **Blind review:** a reviewer agent that never saw ranks or scores would post 5 of the top 10 and 1 of 10 random lower-ranked promos. A person reviewed the verdicts and kept them. It is still a small sample.
- **Why the structure matters:** asked naively for timestamps from raw screenplays, the same model broke the brief in 6 of 6 promos (all 1.6–10.9 s long).
- **Gold set:** of 20 moments drafted blind and reviewed by a person, 15 are found and 3 reach the top 20.
- **Cost:** $0 and about 2.5 h on a laptop. The same tokens would cost about $0.04 on Claude Haiku 5.5, $0.80 on Sonnet 5.5 or $1.60 on Opus 5.5. Each call sees one episode, so 400 episodes means 400 small calls.

**Where it picks badly**
1. **Endings that resolve:** a tender look or the payoff instead of an open question. This was the reviewer's most common "maybe".
2. **Hooks that need setup:** #3 opens on a two-word question in the middle of an argument, and nothing scores that as flat.
3. **Who is who:** strangers follow the conflict, but how people are related was the gap a blind reader named most (104 of 240).
4. **Episode 50 has no promo.** Its one strong moment sits in its last 6 s. The model even paired it with itself instead of with an earlier episode.
5. **A soft top of the list:** with a revised prompt, the same model kept its scores within one point 91–98% of the time, yet only 5 of the top 10 stayed. Read the ranking as tiers.
6. **Under-ranked montages:** the cost per jump buries good ones. A montage ranked #198 of 199 got a "post".
7. **Lenient or flat small models:** as a blind viewer, gemma3 rated almost every clip 3/5 and would tap on all of them, so it can't score promos.

Found and fixed along the way:
- joins that opened on an answer to a question the cut removed (5 of 58, now 0);
- the montage step never offering Episode 50;
- a prompt whose field order made the model write JSON into a text field;
- two flaws in my own quality checks.

**What I'd do next**
1. Feed the editor's approvals from `viewer.html` back in, to tune weights and prompts on real decisions.
2. Try a stronger judge and blind viewer through an API (about $1.60 per show on Opus 5.5; $0.02 for the blind test on Haiku 5.5), scored on the same gold set and review.
3. Repair endings by trimming to the last unresolved beat, and prefer openings that say who is who.
4. With video, check that the hook really lands in the first 3 s.

**Assumptions:**
- screenplay times match the final video;
- there are no overlays or voice-over;
- parts play in story order;
- the goal is new viewers;
- spoilers and graphic content are allowed but flagged.

The evidence for every number, failures included, is in `eval/FINDINGS.md` in the private submission (it quotes the pack); the design is in `PLAN.md`.
