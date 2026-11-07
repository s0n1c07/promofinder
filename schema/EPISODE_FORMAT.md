# Episode screenplay format: template, grammar, quirks

How each file in `Input/` is laid out, measured across all 50 episodes. The parser follows this
document. The pack is confidential, so the examples here come from the made-up demo show in `demo/`,
which uses the same format. `episode.schema.json` defines what the parser produces.

## 1. The pack at a glance

| Item | Value |
|---|---|
| Files | 50 UTF-8 `.txt` files, one per episode, `episode-{NNN}-Episode {platform_id}.txt` |
| Size | 15,908 lines, 529 KB (about 130K tokens for the whole show, about 2.6K per episode) |
| Runtime | 102.7 min (6,162.62 s) in total; one episode runs 70.5 s to 200.75 s (mean 123 s) |
| Scenes | 100 in total, 1 to 5 per episode |
| Blocks | 3,727: CAMERA 1,754 · DIALOGUE 1,487 · ACTION 372 · SOUND FX 114 |
| Speakers | 64 distinct speaker labels; many are aliases of the same person (see §6.3) |

## 2. Template

```text
EPISODE NUMBER: Episode {n}
EPISODE LOGLINE: {one-sentence synopsis of this episode}
CONTINUITY NOTES: {link to the previous episode, often "Picks up directly from Episode {n-1}'s cliffhanger where ..."}

--- SCRIPT START ---

SCENE {NN}
Scene heading: {INT.|EXT.} {LOCATION}[ - {SUB-LOCATION}] – {TIME OF DAY}
Scene start: {MM:SS.cc}
Scene end: {MM:SS.cc}

Actions and dialogue:

**CAMERA:**
Start: ({MM:SS.cc}) | End: ({MM:SS.cc})
{shot description, one line}

**ACTION:**
Start: ({MM:SS.cc}) | End: ({MM:SS.cc})
{what happens on screen, one line}

**SOUND FX:**
Start: ({MM:SS.cc}) | End: ({MM:SS.cc})
{sound or music cue, one line}

**{SPEAKER LABEL}[ ({VOICE})][ ({ALIAS})] ({DELIVERY, DELIVERY, ...}):**
Start: ({MM:SS.cc}) | End: ({MM:SS.cc})
"{spoken line}"

{... more blocks; next "SCENE {NN+1}" block ...}

--- SCRIPT END ---
```

Example (demo show, Episode 1, 00:41):

```text
**THE CALLER (V.O.) (Cold, Threatening):**
Start: (00:41.50) | End: (00:46.50)
"Cut me off, and my next call is to the police."
```

## 3. Grammar (what the parser accepts)

```ebnf
episode      = header, blank*, "--- SCRIPT START ---", scene+, "--- SCRIPT END ---" ;
header       = "EPISODE NUMBER: Episode ", int, NL,
               "EPISODE LOGLINE: ", text, NL,
               "CONTINUITY NOTES: ", text, NL ;
scene        = "SCENE ", int2, NL,
               "Scene heading: ", heading, NL,          (* key is case-insensitive *)
               "Scene start: ", tc, NL,
               "Scene end: ", tc, NL,
               "Actions and dialogue:", NL,
               block+ ;
block        = "**", label, ":**", NL,
               "Start: (", tc, ") | End: (", tc, ")", NL,
               body_line, NL ;                           (* body is always exactly one line *)
label        = "CAMERA" | "ACTION" | "SOUND FX" | speaker_label ;
speaker_label= SPEAKER, [" (", voice, ")"], [" (", alias, ")"], " (", delivery, ")" ;
voice        = "O.S." | "V.O." | "Off-screen" ;
tc           = digit, digit, ":", digit, digit, ".", digit, digit ;   (* MM:SS.cc *)
```

Regexes the parser will use. All are anchored, and keys are case-insensitive:

| Element | Regex |
|---|---|
| Block label | `^\*\*(?P<label>.+?):\*\*\s*$` |
| Timing line | `^Start:\s*\((?P<s>\d{2}:\d{2}\.\d{2})\)\s*\|\s*End:\s*\((?P<e>\d{2}:\d{2}\.\d{2})\)\s*$` |
| Scene marker | `^SCENE\s+(\d+)\s*$` |
| Scene fields | `(?i)^(scene heading|scene start|scene end|actions and dialogue)\s*:\s*(.*)$` |
| Speaker parts | name = text before the first `(`; parentheticals = `\(([^)]*)\)` in order |

## 4. Field reference

| Field | Level | Type | What it is | How the promo tool uses it |
|---|---|---|---|---|
| EPISODE NUMBER | episode | int | Always equals the number in the file name (checked for all 50) | Episode key |
| EPISODE LOGLINE | episode | text | Author's one-sentence synopsis | Show index (who/what per episode) for linking moments across episodes. Never used as a timestamp source |
| CONTINUITY NOTES | episode | text | How this episode connects to earlier ones. 39/50 name an earlier episode by number (Episodes 34 and 49 mention only their own number); 12/50 describe the previous episode's cliffhanger explicitly | **Evaluation only**: silver labels for "what is episode N's cliffhanger" (§7). Never shown to the judge |
| Scene heading | scene | text | `INT./EXT. LOCATION – TIME` | Location changes (continuity risk in multi-segment promos) |
| Scene start/end | scene | timecode | Scene window; scenes are contiguous, first starts at 00:00.00 | Sanity checks |
| CAMERA | block | text + times | Shot description. Shots are the picture track | Preferred cut points (shot boundaries); visual hook cues ("extreme close-up", "push-in", "insert") |
| ACTION | block | text + times | On-screen action | Physical hooks (strike, kneel, fire, chains) |
| SOUND FX | block | text + times | Sound and music cues | Tension cues ("sting", "swell", "ominous", "cliffhanger") |
| Dialogue | block | quoted line + times | Speaker label, optional voice, optional alias, delivery tags | Shocking lines, questions, threats; cuts must never fall inside a line |

## 5. Timeline model (the part that matters for cutting promos)

- **Times are relative to the episode.** Every episode starts at `00:00.00`. Format `MM:SS.cc`; the longest episode (36) ends at `03:20.75`.
- **There are four tracks running in parallel.** CAMERA is the picture track. DIALOGUE, ACTION and SOUND FX are laid over it, so blocks overlap by design.
  - Camera shots mostly tile the timeline: 123 gaps (median 2.0 s) and 5 overlaps across 1,754 shots.
  - **171 of 1,487 dialogue lines run across a camera cut** (J- and L-cuts), so a shot boundary alone is not a safe place to cut.
  - Blocks are listed almost in time order: there are 8 small steps backwards, each 1.0 s or less, in episodes 4, 12, 20, 21, 32 and 46. Code sorts blocks by start time and never relies on file order.
- **Safe cut point** means a block boundary that does not fall inside any spoken line. There are 17 to 57 per episode (median 36), roughly one every 3.4 s, so promo boundaries can be precise.
- **Typical durations:** shot median 3.0 s; dialogue line median 2.2 s (max 9.0 s); ACTION median 2.7 s; speech median 2.4 words per second.
- **How episodes end:** the last spoken line ends a median 5.5 s before the episode does. The ending is a visual or sound beat (reaction close-up, SFX sting, "Cut to black"). The last block is CAMERA in 32 episodes, ACTION in 11 and SOUND FX in 7.

## 6. Observed variations and quirks (the parser must handle these)

1. **Uppercase scene keys (Episode 6):** `SCENE HEADING:`, `SCENE START:`, `ACTIONS AND DIALOGUE:`. Match keys case-insensitively.
2. **Sound bridge (Episode 22):** one SOUND FX block (00:41.50–00:42.80) runs 0.3 s past the end of its scene. Allow it and log a warning.
3. **Speaker labels are not character names.** There are 64 labels for far fewer people:
   - Name variants: three characters appear both with and without a title (`NAME` / `LADY NAME`).
   - Descriptor labels: a role or a look instead of a name, for main characters too (in the demo show's
     terms, `STATION MANAGER` for Hollis, `WOMAN IN YELLOW RAINCOAT` for Maya).
   - Hidden-identity labels used before a reveal: three different descriptor labels for one stranger whose
     identity is revealed late in the show. This is a spoiler-sensitive mapping.
   - 4 labels name the person outright, in the form `STATION MANAGER (Hollis) (Decisive)`.
   - The mappings above are examples. The full alias map is built in pipeline step 2 (show index, see `PLAN.md`) and confirmed by a person once per show.
4. **Voice modifier, 3 spellings:** `(O.S.)`, `(V.O.)`, `(Off-screen)`. When present it is always the first parenthetical. `Off-screen` is treated as `O.S.`
5. **Delivery tags are always present** (1,487 of 1,487 lines). They are free text, usually adjectives separated by commas. There are 619 distinct tags; the most common are cold, resolute, desperate, whispering, defiant, stern. Some are phrases that name another character ("Reverent toward <name>"). They are a free emotion-intensity signal.
6. **Place names drift between episodes.** One family's house is spelled five different ways across 14 episodes. Cross-episode linking must not depend on exact place names, and multi-segment promos that mix the spellings get a flag.
7. **Characters outside ASCII:** en dash U+2013 (86, in scene headings), em dash U+2014 (14), `é` (1). Read files as UTF-8. Windows consoles mis-render these characters.
8. **Scene headings use two separators:** en dash and hyphen, sometimes with sub-locations (for example `INT. HOUSE - KITCHEN / PANTRY - CONTINUOUS`). Time of day is the last segment: CONTINUOUS 45, DAY 26, NIGHT 24, DUSK 3, MORNING 1, PRE-DAWN 1.
9. **Dialogue is always one line in double quotes**, with no stage directions inside the quotes. Every block body is exactly one line.
10. **The scripts flag cliffhangers themselves.** The word appears in the final beats of 14 episodes (a sound cue such as "the music swells to a cliffhanger peak"). This is a useful signal, but new shows may not include it, so the tool must also work without it.

## 7. Built-in evaluation signal

Each episode's CONTINUITY NOTES describe how the previous episode ended. In the demo show's words:

> "Continues immediately after the Episode 2 cliffhanger: the studio door handle starts to turn."

That gives author-written labels for "what is the cliffhanger of episode N": 12 explicit ones, plus 27 weaker notes that name the episode they pick up from. The pipeline keeps them **out of the model input** and uses them only to score cliffhanger detection.

## 8. Checks the parser runs on every file

All 50 files pass today, with the 2 warnings described in §6.1 and §6.2.

- The three header fields are present, and EPISODE NUMBER equals the number in the file name.
- Every block has exactly one valid timing line, end ≥ start, and a one-line body.
- Block start times go backwards by no more than 1.5 s (observed max 1.0 s).
- Blocks fall inside their scene window. A block more than 0.05 s outside it is kept and logged as a warning.
- Scenes are contiguous, the first starts at 0, and the last scene's end equals the last block's end.

## 9. Parsed form

The parser turns each file into one JSON object that follows [`episode.schema.json`](episode.schema.json):

- Every block gets a stable ID, e.g. `E001-B037`. **Later stages, the LLM included, refer to moments only by these IDs, and code looks up the timestamps.**
- Times are kept twice: as seconds (`start_s`) for arithmetic and as the original timecode (`start_tc`) for output.
- The speaker is split into `label`, `voice`, `alias_hint` and `delivery[]`. `character_id` is filled in later by the alias step.

Abbreviated example (demo show, Episode 3, blocks 1 and 9):

```json
{
  "schema_version": "1.0",
  "show_id": "night_line",
  "episode_number": 3,
  "platform_episode_id": "900003",
  "header": {
    "logline": "The woman in the yellow raincoat walks in, the girl everyone believed drowned, ...",
    "continuity_notes": "Continues immediately after the Episode 2 cliffhanger: the studio door handle starts to turn.",
    "follows_episode": 2,
    "prev_cliffhanger_note": "the studio door handle starts to turn."
  },
  "duration_s": 96.5,
  "scenes": [{ "scene_id": "E003-S01", "index": 1, "heading": "INT. HARBOR FM – STUDIO B – CONTINUOUS",
               "int_ext": "INT", "location": "HARBOR FM – STUDIO B", "time_of_day": "CONTINUOUS",
               "start_s": 0.0, "end_s": 42.5, "start_tc": "00:00.00", "end_tc": "00:42.50",
               "block_ids": ["E003-B001", "E003-B002", "..."] }],
  "blocks": [
    { "block_id": "E003-B001", "index": 1, "scene_id": "E003-S01", "kind": "CAMERA",
      "start_s": 0.0, "end_s": 2.5, "start_tc": "00:00.00", "end_tc": "00:02.50",
      "text": "Close-up on the turning handle.", "speaker": null,
      "raw_label": "CAMERA", "source_line": 14 },
    { "block_id": "E003-B009", "index": 9, "scene_id": "E003-S01", "kind": "DIALOGUE",
      "start_s": 11.1, "end_s": 16.9, "start_tc": "00:11.10", "end_tc": "00:16.90",
      "text": "You knew me as Maya. You held on to me in the river.",
      "speaker": { "label": "WOMAN IN YELLOW RAINCOAT", "character_id": null, "alias_hint": "Maya",
                   "voice": "ON", "delivery": ["Steady", "Hurt"] },
      "raw_label": "WOMAN IN YELLOW RAINCOAT (Maya) (Steady, Hurt)", "source_line": 46 }
  ],
  "warnings": []
}
```
