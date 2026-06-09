# PaperCut Editing Protocol (for Claude)

How Claude edits transcripts in the headless batch workflow. The goal: hand Tim
FCP XMLs that are basically ready, with anything uncertain clearly flagged.

## The loop

```
batch.py transcribe <dir>     # verbatim transcripts (CrisperWhisper)
  → Claude edits each <stem>.srt + writes <stem>.notes.md
batch.py export <dir>         # FCP XMLs next to each source file
  → Claude reads papercut_batch_report.md + notes for sense
```

Run everything under the CrisperWhisper venv:
`.venv-crisper/bin/python batch.py ...`

## What Claude edits

For each `<stem>.srt`:

1. **Delete flubs.** Remove false starts, misspeaks, or aborted sentences —
   either by deleting the whole block, or with cut markers (below).
2. **Repeated takes → keep the LAST.** When a sentence/fragment is said 2–3×
   in a row, keep the final clean take and remove the earlier ones. This works
   **whether the takes are in separate blocks or the same block** (see below).
3. **Trim fillers / stray words within a block** by deleting just those words
   from the block's text. CrisperWhisper marks fillers like `[UM]`, `[UH]` —
   easy to spot and delete.
4. **Never edit timestamps.** Timing comes from the word-level JSON; only change
   text, add cut markers, or delete whole blocks.
5. **Leave `<stem>.srt.orig` untouched** — it's the diff baseline.

## Two ways to cut, both keep-the-last-take correct

The exporter resolves edits against CrisperWhisper's **word-level timestamps**,
so it knows the exact time of every word — including repeated ones.

**(a) Cut markers — preferred for same-block flub-and-correction.** Leave the
text verbatim and wrap the part to remove in `[[CUT]]…[[/CUT]]`. Because nothing
else changes, every word maps 1:1 to its timestamp and the cut is exact and
self-documenting (Tim can see precisely what was removed). Example — flubbed
"four", corrected to "two", all in one block:

```
The derivative is [[CUT]]four X. No wait. The derivative is[[/CUT]] two X.
```
→ keeps "The derivative is two X." (the correction).

**(b) Plain deletion — fine for whole takes / lines.** Just delete the words you
don't want. When a fragment repeats, the matcher resolves **rightmost (keep the
last occurrence)**, so deleting down to one copy of a repeated sentence keeps the
final take, not the first. Whole-block deletion is the cleanest form of this.

Rule of thumb: **whole-block delete** for cleanly separated takes; **`[[CUT]]`
markers** when a flub and its correction live in the same block (your most common
case). Both are non-destructive — Tim expands anything in FCP later.

## Flagging (write to `<stem>.notes.md`)

Create `<stem>.notes.md` per file. Log every non-obvious decision:

```markdown
# Notes — <stem>

## Cuts made
- 00:01:12 deleted false start "so the the—"
- 00:03:40 kept 3rd take of "let's define the integral", deleted 2 earlier

## ⚠ Flags (Tim, please check)
- 00:05:20 — two takes both look clean; kept the 2nd. Verify which you want.
- 00:07:05 — unclear if the aside about notation should stay.

## Sense check
- Reads coherently start to finish. No obvious gaps.
- (or) Possible over-cut around 00:09: topic jumps from X to Z.
```

## Sense check (after editing, and after export)

- Re-read the edited transcript end to end. Does it flow? Anything missing?
- After export, read `papercut_batch_report.md`. Investigate any
  **OVER-CUT** (>60% removed) or **almost-nothing-cut** (<2%) warning.
- Cuts are non-destructive: Tim can expand any cut in FCP later, so when unsure
  whether to cut, **flag it and lean toward keeping** rather than dropping
  content silently.

## Optional automation (not built yet)

A Python pre-pass could propose obvious cuts (strip `[UM]/[UH]`, collapse
consecutive identical takes) for Claude to review — faster, same flagging. Ask
Tim before relying on it; editing judgment stays with Claude.
