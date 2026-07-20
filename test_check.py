#!/usr/bin/env python3
"""Tests for check.py's pure logic (diff/build/format/validate).

Stdlib only, no dependencies. Run with:
    python -m unittest test_check
    python test_check.py
"""

import importlib.util
import unittest
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "check", Path(__file__).resolve().parent / "check.py"
)
check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check)

PRODUCTS = {"27169": "Eagle Rare 10Y", "27090": "Blanton's Single Barrel"}
WATCHED = {"Asheville ABC Board", "Woodfin ABC Board"}


def rec(code, board, units):
    return {"NCcode": code, "boardName": board, "NUMUNITS": units}


class BuildCurrentTests(unittest.TestCase):
    def test_includes_all_boards_for_watched_products(self):
        # Store-all-boards keeps state small but avoids spurious baseline
        # alerts when a board is added later (SPEC section 5).
        records = [
            rec("27169", "Asheville ABC Board", 12),
            rec("27169", "Marshville ABC Board", 6),  # unwatched board, watched product
        ]
        current = check.build_current(records, PRODUCTS)
        self.assertEqual(
            current,
            {"27169|Asheville ABC Board": 12, "27169|Marshville ABC Board": 6},
        )

    def test_excludes_unwatched_products(self):
        records = [rec("99999", "Asheville ABC Board", 50)]
        self.assertEqual(check.build_current(records, PRODUCTS), {})


class DiffTests(unittest.TestCase):
    def test_increase_on_watched_board_is_a_hit(self):
        current = {"27169|Asheville ABC Board": 12}
        state = {"units": {"27169|Asheville ABC Board": 10}}
        hits = check.diff(current, state, PRODUCTS, WATCHED)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["delta"], 2)
        self.assertEqual(hits[0]["total"], 12)
        self.assertEqual(hits[0]["label"], "Eagle Rare 10Y")

    def test_new_pair_counts_as_full_increase(self):
        current = {"27090|Woodfin ABC Board": 6}
        state = {"units": {}}
        hits = check.diff(current, state, PRODUCTS, WATCHED)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["delta"], 6)

    def test_decrease_is_not_a_hit(self):
        # A decrease just means an old shipment aged out of the 16-day window.
        current = {"27169|Asheville ABC Board": 4}
        state = {"units": {"27169|Asheville ABC Board": 10}}
        self.assertEqual(check.diff(current, state, PRODUCTS, WATCHED), [])

    def test_unchanged_is_not_a_hit(self):
        current = {"27169|Asheville ABC Board": 10}
        state = {"units": {"27169|Asheville ABC Board": 10}}
        self.assertEqual(check.diff(current, state, PRODUCTS, WATCHED), [])

    def test_increase_on_unwatched_board_is_ignored(self):
        current = {"27169|Marshville ABC Board": 12}
        state = {"units": {"27169|Marshville ABC Board": 0}}
        self.assertEqual(check.diff(current, state, PRODUCTS, WATCHED), [])

    def test_first_run_state_none_treated_as_empty(self):
        current = {"27169|Asheville ABC Board": 6}
        hits = check.diff(current, None, PRODUCTS, WATCHED)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["delta"], 6)

    def test_hits_sorted_by_delta_descending(self):
        current = {
            "27169|Asheville ABC Board": 12,  # +2
            "27169|Woodfin ABC Board": 6,     # +6
        }
        state = {"units": {"27169|Asheville ABC Board": 10}}
        hits = check.diff(current, state, PRODUCTS, WATCHED)
        self.assertEqual([h["delta"] for h in hits], [6, 2])


class FormatEmailTests(unittest.TestCase):
    def test_single_hit_subject_and_body(self):
        hits = [
            {"code": "27169", "label": "Eagle Rare 10Y",
             "board": "Asheville ABC Board", "delta": 2, "total": 12}
        ]
        subject, body = check.format_email(hits, "2026-07-20 11:03:56")
        self.assertEqual(subject, "NC ABC: Eagle Rare 10Y +2 at Asheville")
        self.assertIn("Eagle Rare 10Y - Asheville ABC Board: +2 (now 12 bottles)", body)
        self.assertIn("Extract: 2026-07-20 11:03:56", body)
        self.assertIn(check.HUMAN_URL, body)

    def test_multiple_hits_subject_summarizes_count(self):
        hits = [
            {"code": "27169", "label": "Eagle Rare 10Y",
             "board": "Woodfin ABC Board", "delta": 6, "total": 6},
            {"code": "27169", "label": "Eagle Rare 10Y",
             "board": "Asheville ABC Board", "delta": 2, "total": 12},
        ]
        subject, body = check.format_email(hits, "2026-07-20 11:03:56")
        self.assertEqual(subject, "NC ABC: Eagle Rare 10Y +6 at Woodfin (+1 more)")
        # 2 hit lines + blank separator + extract line + url line
        self.assertEqual(len(body.strip().splitlines()), 5)

    def test_short_board_strips_suffix(self):
        self.assertEqual(check.short_board("Asheville ABC Board"), "Asheville")


class ValidateConfigTests(unittest.TestCase):
    def _feed(self):
        return {
            "lookups": {
                "codes": ["27169", "27090", "00124"],
                "boards": ["Asheville ABC Board", "Woodfin ABC Board"],
            }
        }

    def test_valid_config_passes(self):
        # Should not raise / exit.
        check.validate_config(self._feed(), {"27169": "x"}, ["Asheville ABC Board"])

    def test_unknown_code_exits(self):
        with self.assertRaises(SystemExit):
            check.validate_config(self._feed(), {"99999": "x"}, ["Asheville ABC Board"])

    def test_unknown_board_exits(self):
        with self.assertRaises(SystemExit):
            check.validate_config(self._feed(), {"27169": "x"}, ["Nowhere ABC Board"])

    def test_non_zero_padded_code_hint(self):
        # Code 124 (not "00124") should fail with a padding hint on stderr.
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf), self.assertRaises(SystemExit):
            check.validate_config(self._feed(), {"124": "x"}, ["Asheville ABC Board"])
        self.assertIn("5-digit zero-padded", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
