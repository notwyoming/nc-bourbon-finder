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


def hit(code, label, board, delta):
    return {"code": code, "label": label, "board": board, "delta": delta, "total": delta}


SINGLE = {"Woodfin ABC Board": {
    "single_store": True,
    "stores": [{"address": "142 Weaverville Rd Woodfin, NC 28804", "phone": "828-658-9300"}]}}
MULTI = {"Asheville ABC Board": {
    "single_store": False, "stores": [{"address": "a"}, {"address": "b"}]}}


class BoardBlocksTests(unittest.TestCase):
    def test_products_grouped_on_one_line_per_board(self):
        hits = [
            hit("27169", "Eagle Rare 10Y", "Woodfin ABC Board", 6),
            hit("27090", "Blanton's Single Barrel", "Woodfin ABC Board", 4),
        ]
        blocks = check.board_blocks(hits, {})
        self.assertEqual(len(blocks), 1)
        board, products, _ = blocks[0]
        self.assertEqual(board, "Woodfin ABC Board")
        self.assertEqual(
            products, "+6 bottles Eagle Rare 10Y, +4 bottles Blanton's Single Barrel")

    def test_boards_ordered_by_biggest_increase(self):
        hits = [
            hit("27169", "Eagle Rare 10Y", "Woodfin ABC Board", 2),
            hit("27169", "Eagle Rare 10Y", "Asheville ABC Board", 8),
        ]
        blocks = check.board_blocks(hits, {})
        self.assertEqual([b[0] for b in blocks],
                         ["Asheville ABC Board", "Woodfin ABC Board"])


class StoreLinesTests(unittest.TestCase):
    def test_single_store_resolves_to_address_and_phone(self):
        self.assertEqual(
            check.store_lines("Woodfin ABC Board", SINGLE),
            ["142 Weaverville Rd Woodfin, NC 28804", "(828-658-9300)"])

    def test_multi_store_says_count_not_address(self):
        self.assertEqual(
            check.store_lines("Asheville ABC Board", MULTI),
            ["1 of 2 stores - exact store unknown (see locator below)"])

    def test_unknown_board_has_no_store_lines(self):
        self.assertEqual(check.store_lines("Nowhere ABC Board", {}), [])


class FormatEmailTests(unittest.TestCase):
    def test_returns_subject_text_and_html(self):
        hits = [hit("27169", "Eagle Rare 10Y", "Asheville ABC Board", 2)]
        subject, text, html = check.format_email(hits, "2026-07-20 11:03:56")
        self.assertEqual(subject, "NC ABC: Eagle Rare 10Y +2 at Asheville")
        self.assertIn("+2 bottles Eagle Rare 10Y", text)
        self.assertNotIn("bottles)", text)  # dropped the "(now X bottles)" total
        self.assertIn("Extract: 2026-07-20 11:03:56", text)
        self.assertIn(check.HUMAN_URL, text)
        self.assertIn("font-weight:bold", html)  # bold board name in the HTML part

    def test_multiple_hits_subject_summarizes_count(self):
        hits = [
            hit("27169", "Eagle Rare 10Y", "Woodfin ABC Board", 6),
            hit("27169", "Eagle Rare 10Y", "Asheville ABC Board", 2),
        ]
        subject, _, _ = check.format_email(hits, "2026-07-20 11:03:56")
        self.assertEqual(subject, "NC ABC: Eagle Rare 10Y +6 at Woodfin (+1 more)")

    def test_disclaimers_appear_above_extract(self):
        hits = [hit("27169", "Eagle Rare 10Y", "Asheville ABC Board", 2)]
        _, text, _ = check.format_email(hits, "2026-07-20 11:03:56")
        self.assertLess(text.index("Disclaimers:"), text.index("Extract:"))

    def test_single_store_hit_shows_address(self):
        hits = [hit("27090", "Blanton's Single Barrel", "Woodfin ABC Board", 6)]
        _, text, _ = check.format_email(hits, "2026-07-20 11:03:56", SINGLE)
        self.assertIn("142 Weaverville Rd Woodfin, NC 28804", text)
        self.assertNotIn("Store locator:", text)  # no multi-store hit -> no locator

    def test_multi_store_hit_adds_locator_line(self):
        hits = [hit("27169", "Eagle Rare 10Y", "Asheville ABC Board", 8)]
        _, text, _ = check.format_email(hits, "2026-07-20 11:03:56", MULTI)
        self.assertIn("1 of 2 stores", text)
        self.assertIn(f"Store locator: {check.LOCATOR_URL}", text)

    def test_short_board_strips_suffix(self):
        self.assertEqual(check.short_board("Asheville ABC Board"), "Asheville")


class ShouldAlertTests(unittest.TestCase):
    def test_first_ever_alert_sends(self):
        self.assertTrue(check.should_alert([{"delta": 5}], None, "2026-07-20"))

    def test_new_day_sends(self):
        self.assertTrue(check.should_alert([{"delta": 5}], "2026-07-19", "2026-07-20"))

    def test_same_day_is_capped(self):
        self.assertFalse(check.should_alert([{"delta": 5}], "2026-07-20", "2026-07-20"))

    def test_no_hits_never_sends(self):
        self.assertFalse(check.should_alert([], "2026-07-19", "2026-07-20"))


class WriteStateTests(unittest.TestCase):
    def test_last_alert_date_written_and_omitted_when_none(self):
        import json
        import tempfile
        orig = check.STATE_PATH
        try:
            check.STATE_PATH = Path(tempfile.mkdtemp()) / "latest.json"
            check.write_state("2026-07-20 11:03:56", {"k": 1}, "2026-07-20")
            with check.STATE_PATH.open() as f:
                self.assertEqual(json.load(f)["last_alert_date"], "2026-07-20")
            check.write_state("2026-07-20 11:03:56", {"k": 1}, None)
            with check.STATE_PATH.open() as f:
                self.assertNotIn("last_alert_date", json.load(f))
        finally:
            check.STATE_PATH = orig


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
