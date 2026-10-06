#!/usr/bin/env python3
"""
test_regressions.py — Regression tests for the S130 parser port (quest 7367a486).

Covers the four data-completeness fixes ported from notice-policy (S126):
  1. ISO-dated OpenAI retirements are parsed (not silently dropped)
  2. Anthropic deprecation-history section announced dates (h3 div ids)
  3. Family-name digit-token join ('4 1' -> '4.1')
  4. Whitespace-fragment model cells are excluded (no garbage records)

Run: python3 tests/test_regressions.py
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))
import parser as P  # noqa: E402


ANTHROPIC_HISTORY_FIXTURE = """
<html><body>
<h2>Model lifecycle</h2>
<table>
<tr><th>API model name</th><th>Current state</th><th>Deprecated</th><th>Tentative retirement date</th></tr>
<tr><td>claude-opus-4-8</td><td>Active</td><td>N/A</td><td>Not sooner than May 28, 2027</td></tr>
</table>
<h2>Retired models and recommended replacements</h2>
<table>
<tr><th>Retirement date</th><th>Deprecated model</th><th>Recommended replacement</th></tr>
<tr><td>August 5, 2026</td><td>claude-opus-4-1-20250805</td><td>claude-opus-4-8</td></tr>
</table>
<h2>Deprecation history</h2>
<h3><div class="group relative pt-6 pb-2" id="2026-06-05-claude-opus-4-1-model">Opus 4.1</div></h3>
<table>
<tr><th>Retirement date</th><th>Deprecated model</th><th>Recommended replacement</th></tr>
<tr><td>August 5, 2026</td><td>claude-opus-4-1-20250805</td><td>claude-opus-4-8</td></tr>
</table>
</body></html>
"""

OPENAI_ISO_FIXTURE = """
<html><body>
<h2>Deprecations</h2>
<p>2026-04-22: Legacy model snapshot retirements</p>
<table>
<tr><th>Shutdown date</th><th>Model / system</th><th>Recommended replacement</th></tr>
<tr><td>2026-02-17</td><td>dall-e-2</td><td>gpt-5.6-sol</td></tr>
<tr><td>2026-02-17</td><td>o1-preview</td><td>gpt-5.6-sol</td></tr>
<tr><td>2026-02-17</td><td>chatgpt-4o-latest</td><td>gpt-5.6-sol</td></tr>
</table>
</body></html>
"""

OPENAI_GARBAGE_FIXTURE = """
<html><body>
<h2>Deprecations</h2>
<p>2025-05-20: Legacy fine-tuning model deprecations</p>
<table>
<tr><th>Shutdown date</th><th>Model / system</th><th>Recommended replacement</th></tr>
<tr><td>July 21, 2025</td><td>New fine-tuning training on babbage-002</td><td>gpt-5.6-sol</td></tr>
<tr><td>July 21, 2025</td><td>Assistants API</td><td>gpt-5.6-sol</td></tr>
<tr><td>July 21, 2025</td><td>/v1/engines</td><td>gpt-5.6-sol</td></tr>
<tr><td>July 21, 2025</td><td>OpenAI-Beta: realtime=v1</td><td>gpt-5.6-sol</td></tr>
<tr><td>July 21, 2025</td><td>text-ada-001</td><td>gpt-5.6-sol</td></tr>
</table>
</body></html>
"""


class TestISODates(unittest.TestCase):
    """Fix 1: ISO-dated OpenAI retirements are parsed, not silently dropped."""

    def test_iso_retirement_dates_parsed(self):
        recs = P.parse_openai(OPENAI_ISO_FIXTURE)
        by_id = {r["model_id"]: r for r in recs}
        self.assertIn("dall-e-2", by_id)
        self.assertEqual(by_id["dall-e-2"]["retirement"], "2026-02-17")
        self.assertEqual(by_id["dall-e-2"]["announced"], "2026-04-22")
        self.assertIn("o1-preview", by_id)
        self.assertIn("chatgpt-4o-latest", by_id)
        # ISO dates before today => retired
        self.assertEqual(by_id["dall-e-2"]["status"], "retired")

    def test_parse_us_date_accepts_iso(self):
        self.assertEqual(P._parse_us_date("2026-02-17"), "2026-02-17")
        self.assertEqual(P._parse_us_date("August 5, 2026"), "2026-08-05")


class TestAnthropicHistory(unittest.TestCase):
    """Fix 2: deprecation-history section announced dates from h3 div ids."""

    def test_history_announced_date_merged(self):
        recs = P.parse_anthropic(ANTHROPIC_HISTORY_FIXTURE)
        by_id = {r["model_id"]: r for r in recs}
        opus41 = by_id["claude-opus-4-1-20250805"]
        self.assertEqual(opus41["status"], "retired")
        # Without the history fix, announced would be None (lifecycle table only
        # covers current models).
        self.assertEqual(opus41["announced"], "2026-06-05")
        self.assertEqual(opus41["retirement"], "2026-08-05")
        self.assertEqual(opus41["replacement"], "claude-opus-4-8")


class TestFamilyNames(unittest.TestCase):
    """Fix 3: family-name digit-token join ('4 1' -> '4.1')."""

    def test_family_digit_join(self):
        recs = P.parse_anthropic(ANTHROPIC_HISTORY_FIXTURE)
        by_id = {r["model_id"]: r for r in recs}
        opus41 = by_id["claude-opus-4-1-20250805"]
        # Old regex rendered 'Claude Opus'; fixed regex renders 'Claude Opus 4.1'
        self.assertEqual(opus41["family"], "Claude Opus 4.1")

    def test_family_plain(self):
        recs = P.parse_anthropic(ANTHROPIC_HISTORY_FIXTURE)
        by_id = {r["model_id"]: r for r in recs}
        opus48 = by_id["claude-opus-4-8"]
        self.assertEqual(opus48["family"], "Claude Opus 4.8")


class TestGarbageCellExclusion(unittest.TestCase):
    """Fix 4: whitespace-fragment cells and API/system rows are excluded."""

    def test_no_whitespace_fragments(self):
        recs = P.parse_openai(OPENAI_GARBAGE_FIXTURE)
        mids = [r["model_id"] for r in recs]
        self.assertNotIn("New fine-tuning training on babbage-002", mids)
        self.assertNotIn("training", mids)
        self.assertNotIn("New", mids)
        self.assertNotIn("fine-tuning", mids)
        self.assertNotIn("on", mids)
        # The legitimate model in the row survives
        self.assertIn("text-ada-001", mids)

    def test_no_api_system_rows(self):
        recs = P.parse_openai(OPENAI_GARBAGE_FIXTURE)
        mids = [r["model_id"] for r in recs]
        self.assertNotIn("Assistants API", mids)
        self.assertNotIn("/v1/engines", mids)
        self.assertNotIn("OpenAI-Beta: realtime=v1", mids)


if __name__ == "__main__":
    unittest.main(verbosity=2)
