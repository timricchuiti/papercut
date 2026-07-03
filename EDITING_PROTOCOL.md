# PaperCut Editing Protocol (for Claude)

How Claude edits transcripts in the headless batch workflow. Goal: hand Tim FCP
XMLs that are basically ready, with anything uncertain clearly flagged. This file
is the durable record of the editing methodology — edit the way it's written here
rather than re-deriving it each session.

## The loop

```
batch.py transcribe <dir>     # verbatim transcripts (MLX engine by default,
                              #   ~15-30x faster than the transformers path)
  → Claude edits each <stem>.srt + writes <stem>.notes.md
batch.py export <dir>         # OR per file: main.py … --export final-cut-pro
  → Claude reads the report + notes for sense
```

Run everything under the CrisperWhisper venv: `.venv-crisper/bin/python …`
(transcription shells out to `.venv-mlx` automatically for the default engine).

**Export knobs** (defaults are baked in — normally pass nothing):
- **margin** — edge tightness. Default **0.07** (Tim's tuned preference: a little
  breath around each clip). `0` sits right at the speech; **negative = tighter**,
  positive = looser. NOTE: the sign is the reverse of Tim's old auto-editor
  instinct — his "-0.1 feel" maps to a small POSITIVE value here.
- **bridge** — keep silent gaps up to ~2× this inside speech (default 0.20s) so
  sentences don't fragment; longer pauses are cut.

Output `<stem>_ALTERED.fcpxml` + `<stem>.notes.md` next to the source `.mp4`,
referencing the original. Leave `<stem>.srt.orig` untouched (diff baseline).

## What Claude edits

Per `<stem>.srt`: delete flubbed/duplicate blocks, trim within-block flubs with
`[[CUT]]…[[/CUT]]` markers, and **never edit the timestamps** — timing comes from
the word-level JSON. Then write `<stem>.notes.md` and sense-check.

## THE core rule: keep the LAST take

When Tim records live and flubs, he immediately re-says it. **The later take is
always the keeper** — earlier takes are throwaways. This holds whether the takes
are in separate blocks or the same block.

### Recognizing a re-take
- **Proximity.** Re-takes almost always land within ~15–30 seconds of each
  other — usually back-to-back blocks, sometimes within one block. That tight
  spacing is the signal to look for a keeper.
- **Exact OR adjusted.** A re-take may be word-for-word ("That's when we call it
  a multiple." ×3) **or slightly adjusted** — and the adjusted case is the common
  one: a corrected number ("…is 24, sorry, 28"), a relabel ("the first → the
  second component"), a rephrase ("we'll determine → we'll learn how to
  determine"), or a fuller version of a partial. Treat all of these as re-takes:
  keep the last.
- **Partial → full (multi-block).** A clipped false start ("To figure that out,
  we can divide 42.") immediately followed by the full sentence ("To figure that
  out, we can divide 42 by fourteen, and…") → delete the partial, keep the full.
- **Stacked partials building to a full take.** Several restarts that assemble
  into one clean sentence ("You might notice / you might have noticed that when…")
  → keep the final complete take, delete the build-up.

### The ONE exception — keep-last when the last take is clean
If the **last** take contains a content/math error and an **earlier** take is
correct, keep the correct earlier take and **flag it loudly** in notes ("I broke
keep-the-last here on purpose because the last take mislabeled X"). Tim wants the
last take *and* a correct video; correctness wins, but he must be told.
(Real examples: a stuttered final "that's when that's when…"; a product-rule
re-take that called the 2nd component "the first"; keep the clean/correct one.)

## Two ways to cut (both resolve "keep last")

1. **`[[CUT]]…[[/CUT]]` markers — preferred for same-block / surgical cuts.**
   Leave the text verbatim and wrap exactly the words to remove. Because the text
   stays 1:1 with the word-level JSON, each marked word maps to its exact
   timestamp — no fuzzy matching, and Tim can see precisely what was removed.
   Multiple cut spans per block are fine. **Reproduce the block's original text
   exactly** (only insert the markers); if the token count drifts, the exporter
   falls back to fuzzy matching.
2. **Plain deletion — fine for whole takes / lines.** Delete the words you don't
   want; when a fragment repeats, the matcher resolves **rightmost**, so deleting
   down to one copy keeps the last occurrence. Whole-block deletion is the
   cleanest form.

## What else to cut (besides re-takes)

- **Fillers:** CrisperWhisper marks them `[UM]` / `[UH]`. Trim them (usually a
  within-block marker around the filler word).
- **Doubled words / stutters:** "is is", "at at", "the the", "to to". Cut one.
- **False starts / abandoned fragments:** "So,", "We can then move.", "Seven
  time—" → delete or trim.
- **Director's / meta notes left in the recording.** Tim sometimes talks to
  himself on tape: "this is where I cut in the other bit, blah blah blah," or
  "That's an insert, Tim, if the transcriber/LLM did…". These are NOT lesson
  content — cut them, and **flag the spot** in case it marks where Tim plans to
  splice separately-recorded footage.

## What NOT to touch

- **Transcription artifacts are display-only — the audio is the truth.** Do not
  try to "fix" them and do not let them drive a cut:
  - Garbled numbers ("342 goes into a 1263 times" for "3. 42 goes into 126, 3
    times"; "negative 128th" for "−1/28").
  - Mis-hearings ("natural algorithm" for "logarithm", "Even the function" for
    "Given the function").
  - Glued stutter tokens you can't split cleanly ("denomindenominator", "6x toto
    the third", "numeratornow"). Leave them; optionally note.
- **Math.** Verify every result, but **flag-not-fix** — never silently "correct"
  a misspeak or a wrong number. Surface it in notes and let Tim decide.

## Useful signals in the data

- **Reversed / corrupt SRT timestamps** (end before start) almost always mark a
  flub take — they're safe deletion candidates (the exporter would skip them
  anyway). Kept blocks should have clean forward timestamps; the word-level JSON
  is reliable even when the segment SRT line is messy.
- **A long no-speech gap** between two blocks usually = Tim working/writing on
  screen silently. That dead air gets trimmed automatically (non-destructive). If
  a needed word seems to fall in the gap (e.g. a stated answer that never appears
  in the audio), **flag it** — the on-screen visual may cover it, but confirm.

## Flagging — `[[FLAG]]` markers + `<stem>.notes.md`

**`[[FLAG: short note]]` in the SRT → an FCP timeline marker.** Drop one inside
any block that needs Tim's eyes (content error kept per flag-not-fix, a stitch
across a re-record, a suspected missing answer, a broken keep-last). On export it
becomes a named marker on that clip in Final Cut, right where the issue is — so
Tim doesn't have to hunt timestamps from the notes. The flag text is stripped
before word matching (it never affects the cut), and works alongside `[[CUT]]`
markers in the same block. Keep flag notes short (they render as marker names);
the full explanation still goes in notes.md.

**`<stem>.notes.md`** — one per file. Sections: **Deletions** (what & why, with
timestamps), **Within-block trims**, **⚠ Flags** (anything uncertain or any
suspected content/math issue — flag-not-fix), and **Sense check** (does it read
end to end; note the per-example math is intact). Lead with the flags when the
file needs real review (meta-notes, broken keep-last, missing answer, etc.).
Every ⚠ flag in notes.md should normally have a matching `[[FLAG]]` in the SRT.

## Sense check (after editing, and after export)

- Re-read the edited transcript end to end. Does it flow? Anything missing?
- After export, the pipeline runs an automatic **output check** — it warns if any
  clip is ≤4 frames or any clip boundary fails to tile. It should never fire;
  if it does, something regressed — investigate before delivering.
- Cuts are non-destructive (Tim expands any cut in FCP later), so when genuinely
  unsure whether to cut, **flag and lean toward keeping** rather than dropping
  content silently.

## Reduction is usually high — that's fine

Tim's raw recordings are ~40–60% dead air + flubs (he pauses while interacting
with the screen). A 40–55% reduction is normal and not a sign of over-cutting.
Judge over-cut by whether real *content* survives, not by the percentage.
