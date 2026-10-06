#!/usr/bin/env python3
"""
test_parser.py — Unit tests for the vendor deprecation-page parsers.

Tests use small inline HTML fixtures (no network) so they run anywhere.
Run: python3 tests/test_parser.py
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))
import parser as P  # noqa: E402


ANTHROPIC_FIXTURE = """
<html><body>
<h2>Model lifecycle</h2>
<table>
<tr><th>API model name</th><th>Current state</th><th>Deprecated</th><th>Tentative retirement date</th></tr>
<tr><td>claude-opus-4-8</td><td>Active</td><td>N/A</td><td>Not sooner than May 28, 2027</td></tr>
<tr><td>claude-opus-4-1-20250805</td><td>Retired</td><td>June 5, 2026</td><td>August 5, 2026</td></tr>
<tr><td>claude-sonnet-4-20250514</td><td>Retired</td><td>April 14, 2026</td><td>June 15, 2026</td></tr>
</table>
<h2>Retired models and recommended replacements</h2>
<table>
<tr><th>Retirement date</th><th>Deprecated model</th><th>Recommended replacement</th></tr>
<tr><td>August 5, 2026</td><td>claude-opus-4-1-20250805</td><td>claude-opus-4-8</td></tr>
<tr><td>June 15, 2026</td><td>claude-sonnet-4-20250514</td><td>claude-sonnet-4-6</td></tr>
<tr><td>July 21, 2025</td><td>claude-2.0</td><td>claude-opus-4-8</td></tr>
</table>
</body></html>
"""

OPENAI_FIXTURE = """
<html><body>
<h2>Upcoming deprecations</h2>
<p>2026-06-11: GPT-5 and o3 model deprecations</p>
<table>
<tr><th>Shutdown date</th><th>Model / system</th><th>Recommended replacement</th></tr>
<tr><td>Dec 11, 2026</td><td>gpt-5-2025-08-07</td><td>gpt-5.6-sol</td></tr>
<tr><td>Dec 11, 2026</td><td>o3-pro-2025-06-10</td><td>gpt-5.6-sol ( reasoning.mode: pro )</td></tr>
</table>
<p>2026-05-08: Chat model deprecations</p>
<table>
<tr><th>Shutdown date</th><th>Model / system</th><th>Recommended replacement</th></tr>
<tr><td>Aug 10, 2026</td><td>gpt-5.2-chat-latest</td><td>gpt-5.6-sol</td></tr>
</table>
</body></html>
"""


class TestAnthropicParser(unittest.TestCase):
    def test_parses_lifecycle_table(self):
        recs = P.parse_anthropic(ANTHROPIC_FIXTURE)
        by_id = {r["model_id"]: r for r in recs}
        self.assertIn("claude-opus-4-8", by_id)
        self.assertEqual(by_id["claude-opus-4-8"]["status"], "active")
        # "Not sooner than" becomes a retirement floor
        self.assertEqual(by_id["claude-opus-4-8"]["retirement_not_before"], "2027-05-28")
        self.assertIsNone(by_id["claude-opus-4-8"]["retirement"])

    def test_retired_model_has_dates_and_replacement(self):
        recs = P.parse_anthropic(ANTHROPIC_FIXTURE)
        by_id = {r["model_id"]: r for r in recs}
        opus41 = by_id["claude-opus-4-1-20250805"]
        self.assertEqual(opus41["status"], "retired")
        self.assertEqual(opus41["announced"], "2026-06-05")
        self.assertEqual(opus41["retirement"], "2026-08-05")
        self.assertEqual(opus41["replacement"], "claude-opus-4-8")
        self.assertEqual(opus41["vendor"], "Anthropic")

    def test_older_models_from_replacement_table(self):
        recs = P.parse_anthropic(ANTHROPIC_FIXTURE)
        by_id = {r["model_id"]: r for r in recs}
        self.assertIn("claude-2.0", by_id)
        self.assertEqual(by_id["claude-2.0"]["status"], "retired")
        self.assertEqual(by_id["claude-2.0"]["retirement"], "2025-07-21")
        self.assertEqual(by_id["claude-2.0"]["replacement"], "claude-opus-4-8")

    def test_vendor_fields(self):
        for r in P.parse_anthropic(ANTHROPIC_FIXTURE):
            self.assertEqual(r["vendor"], "Anthropic")
            self.assertTrue(r["source_url"].startswith("https://"))


class TestOpenAIParser(unittest.TestCase):
    def test_parses_model_tables(self):
        recs = P.parse_openai(OPENAI_FIXTURE)
        by_id = {r["model_id"]: r for r in recs}
        self.assertIn("gpt-5-2025-08-07", by_id)
        r = by_id["gpt-5-2025-08-07"]
        self.assertEqual(r["status"], "deprecated")  # 2026-12-11 is in the future
        self.assertEqual(r["announced"], "2026-06-11")
        self.assertEqual(r["retirement"], "2026-12-11")
        self.assertEqual(r["replacement"], "gpt-5.6-sol")
        self.assertEqual(r["vendor"], "OpenAI")

    def test_parenthetical_replacement_survives(self):
        recs = P.parse_openai(OPENAI_FIXTURE)
        by_id = {r["model_id"]: r for r in recs}
        self.assertIn("o3-pro-2025-06-10", by_id)
        self.assertIn("reasoning.mode: pro", by_id["o3-pro-2025-06-10"]["replacement"])

    def test_multiple_sections(self):
        recs = P.parse_openai(OPENAI_FIXTURE)
        by_id = {r["model_id"]: r for r in recs}
        self.assertIn("gpt-5.2-chat-latest", by_id)
        self.assertEqual(by_id["gpt-5.2-chat-latest"]["announced"], "2026-05-08")


class TestDateHelpers(unittest.TestCase):
    def test_full_month(self):
        self.assertEqual(P._parse_us_date("August 5, 2026"), "2026-08-05")

    def test_abbreviated_month(self):
        self.assertEqual(P._parse_us_date("Dec 11, 2026"), "2026-12-11")

    def test_not_sooner(self):
        self.assertEqual(P._parse_not_sooner("Not sooner than May 28, 2027"), "2027-05-28")
        self.assertIsNone(P._parse_not_sooner("N/A"))

    def test_garbage(self):
        self.assertIsNone(P._parse_us_date("nonsense"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
