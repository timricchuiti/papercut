#!/usr/bin/env python3
"""Unit tests for the cut-placement logic in papercut_core (no media needed).

Run:  .venv-crisper/bin/python -m unittest test_papercut_core -v
"""
import unittest

from papercut_core import (
    resolve_word_edits, snap_to_speech, tokenize_with_cuts, has_cut_markers,
)


def _words(spec):
    """'a:0-1 b:1-2' -> whisper-style words list."""
    out = []
    for item in spec.split():
        w, span = item.split(":")
        a, b = span.split("-")
        out.append({"word": w, "start": float(a), "end": float(b)})
    return out


def _wd(*segs):
    return {"segments": [{"words": _words(s)} for s in segs]}


def _blk(start, end, text, orig=None):
    return {"start": start, "end": end, "text": text,
            "originalText": orig if orig is not None else text}


class TokenizeTests(unittest.TestCase):
    def test_close_only_counts_as_marked(self):
        self.assertTrue(has_cut_markers("foo [[/CUT]] bar"))
        self.assertTrue(has_cut_markers("[[CUT]]foo"))
        self.assertFalse(has_cut_markers("plain"))

    def test_state_carries(self):
        toks, state = tokenize_with_cuts("a [[CUT]]b c")
        self.assertEqual(toks, [("a", False), ("b", True), ("c", True)])
        self.assertTrue(state)
        toks, state = tokenize_with_cuts("d[[/CUT]] e", in_cut=state)
        self.assertEqual(toks, [("d", True), ("e", False)])
        self.assertFalse(state)


class ResolveTests(unittest.TestCase):
    def test_positional_cut(self):
        wd = _wd("the:0-0.2 cat:0.2-0.5 sat:0.5-0.8")
        blocks = [_blk(0, 0.8, "the [[CUT]]cat[[/CUT]] sat")]
        r = resolve_word_edits(blocks, wd, 0.2)
        self.assertEqual(r, [{"start": 0.0, "end": 0.2}, {"start": 0.5, "end": 0.8}])

    def test_ghost_words_on_boundary_are_aligned(self):
        # Zero-width ghost loop sits exactly on the boundary between block 1 and 2,
        # so block 2's time window picks up 3 extra words -> positional zip fails.
        wd = _wd("one:0-0.5 two:0.5-1.0 of:1.0-1.0 the:1.0-1.0 investors:1.0-1.0",
                 "three:1.0-1.5 four:1.5-2.0 five:2.0-2.5")
        blocks = [_blk(0, 1.0, "one two of the investors"),
                  _blk(1.0, 2.5, "three [[CUT]]four[[/CUT]] five")]
        warnings = []
        r = resolve_word_edits(blocks, wd, 0.2, warnings=warnings)
        self.assertEqual(warnings, [])
        self.assertEqual(r, [{"start": 0.0, "end": 1.0},
                             {"start": 1.0, "end": 1.5}, {"start": 2.0, "end": 2.5}])

    def test_unplaceable_cut_keeps_whole_and_warns(self):
        wd = _wd("one:0-0.5 two:0.5-1.0 three:1.0-1.5")
        # An extra token the audio doesn't have: counts differ AND alignment can't
        # place every token -> refuse to guess.
        blocks = [_blk(0, 1.5, "one [[CUT]]two[[/CUT]] three extra")]
        warnings = []
        r = resolve_word_edits(blocks, wd, 0.2, warnings=warnings)
        self.assertEqual(r, [{"start": 0, "end": 1.5}])
        self.assertEqual(len(warnings), 1)
        self.assertIn("kept WHOLE", warnings[0])

    def test_cross_block_cut(self):
        wd = _wd("a:0-0.5 b:0.5-1.0 c:1.0-1.5", "d:1.5-2.0 e:2.0-2.5", "f:2.5-3.0 g:3.0-3.5")
        blocks = [_blk(0, 1.5, "a [[CUT]]b c"),      # opens, unclosed
                  _blk(1.5, 2.5, "d e"),               # wholly inside the span
                  _blk(2.5, 3.5, "f[[/CUT]] g")]       # closes
        warnings = []
        r = resolve_word_edits(blocks, wd, 0.2, warnings=warnings)
        self.assertEqual(warnings, [])
        self.assertEqual(r, [{"start": 0.0, "end": 0.5}, {"start": 3.0, "end": 3.5}])

    def test_untouched_block_after_closed_span_is_kept_whole(self):
        wd = _wd("a:0-0.5 b:0.5-1.0", "c:1.0-1.5 d:1.5-2.0")
        blocks = [_blk(0, 1.0, "a [[CUT]]b[[/CUT]]"), _blk(1.0, 2.0, "c d")]
        r = resolve_word_edits(blocks, wd, 0.2)
        self.assertEqual(r, [{"start": 0.0, "end": 0.5}, {"start": 1.0, "end": 2.0}])

    def test_whole_block_cut_is_dropped(self):
        wd = _wd("a:0-0.5 b:0.5-1.0")
        blocks = [_blk(0, 1.0, "[[CUT]]a b[[/CUT]]")]
        self.assertEqual(resolve_word_edits(blocks, wd, 0.2), [])


class SnapTests(unittest.TestCase):
    # words: "hello" 1.0-1.4 (true tail runs to 1.6), then silence, then a cut
    # take "hello" starting at 2.0 whose onset is late by 0.1 (loud from 1.9).
    WD = _wd("hello:1.0-1.4 again:2.0-2.5 ghost:2.5-2.5 world:3.0-3.4")
    LOUD = [(0.95, 1.65), (1.9, 2.55), (2.95, 3.45)]

    def test_tail_extends_to_loud_edge(self):
        r = snap_to_speech([{"start": 1.0, "end": 1.4}], self.LOUD, self.WD)
        # tail -> 1.65; onset also moves back to the loud edge (the margin) 0.95
        self.assertEqual(r, [{"start": 0.95, "end": 1.65}])

    def test_onset_extends_back_to_loud_edge(self):
        r = snap_to_speech([{"start": 2.0, "end": 2.5}], self.LOUD, self.WD)
        self.assertAlmostEqual(r[0]["start"], 1.9)

    def test_never_crosses_neighbour_word(self):
        # "again" end is early (2.5) and loud continues (ghost ignored), but the
        # next real word "world" starts at 3.0 -> bounded by the loud edge 2.55.
        r = snap_to_speech([{"start": 2.0, "end": 2.5}], self.LOUD, self.WD)
        self.assertAlmostEqual(r[0]["end"], 2.55)
        # A word abutting the edge exactly is the bound: no extension at all.
        wd = _wd("one:0-1.0 two:1.0-1.5")
        r = snap_to_speech([{"start": 0, "end": 1.0}], [(0, 1.6)], wd)
        self.assertEqual(r, [{"start": 0, "end": 1.0}])

    def test_cap_and_disable(self):
        wd = _wd("one:0-1.0")
        r = snap_to_speech([{"start": 0, "end": 1.0}], [(0, 5.0)], wd, max_snap=0.3)
        self.assertAlmostEqual(r[0]["end"], 1.3)
        r = snap_to_speech([{"start": 0, "end": 1.0}], [(0, 5.0)], wd, max_snap=0)
        self.assertEqual(r, [{"start": 0, "end": 1.0}])

    def test_edge_in_silence_untouched(self):
        wd = _wd("one:0-1.0")
        r = snap_to_speech([{"start": 0, "end": 1.0}], [(0, 0.9)], wd)
        self.assertEqual(r, [{"start": 0, "end": 1.0}])


if __name__ == "__main__":
    unittest.main()
