#!/usr/bin/env python3
"""October 2026 page layouts, fail-closed parsing, the build's receipt and
capture gates, and regressions over real captures in both layouts.

The tests/fixtures/*-20260813* pages are August 2026 captures of the same URLs
(same fetch method), kept so the older layout stays parseable.
Run: python3 -m pytest -q tests
"""
from __future__ import annotations

import hashlib
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "pipeline"))
import fetch_parse as F  # noqa: E402
import parser as P  # noqa: E402

ANTHROPIC_OCT = """
<table><thead><tr><th>API model name</th><th>Current state</th><th>Deprecated</th><th>Tentative retirement date</th></tr></thead><tbody>
<tr><td>claude-mythos-preview</td><td>Deprecated</td><td>June 9, 2026</td><td>To be announced</td></tr>
<tr><td>claude-opus-5-5</td><td>Active</td><td>N/A</td><td>Not sooner than September 22, 2027</td></tr>
<tr><td>claude-sonnet-4-5-20250929</td><td>Deprecated</td><td>September 30, 2026</td><td>November 30, 2026</td></tr>
</tbody></table>
<h2 id="deprecation-history">Deprecation history</h2>
<h3 id="2026-09-30-claude-sonnet-4-5-model" class="group">2026-09-30: Claude Sonnet 4.5 model<span><button aria-label="Copy link"></button></span></h3>
<table><thead><tr><th>Retirement date</th><th>Deprecated model</th><th>Recommended replacement</th></tr></thead><tbody>
<tr><td>November 30, 2026</td><td><code>claude-sonnet-4-5-20250929</code></td><td><code>claude-sonnet-5-5</code></td></tr>
</tbody></table>
<h3 id="2024-09-04-claude-1-and-instant-models">2024-09-04: Claude 1 and Instant models</h3>
<table><thead><tr><th>Retirement date</th><th>Deprecated model</th><th>Recommended replacement</th></tr></thead><tbody>
<tr><td>November 6, 2024</td><td><code>claude-instant-1.2</code></td><td><code>claude-haiku-4-5-20251001</code></td></tr>
</tbody></table>
<table><thead><tr><th>Parameter</th><th>Status</th><th>Behavior</th><th>Recommended replacement</th></tr></thead><tbody>
<tr><td>temperature, top_p, top_k</td><td>Deprecated (Claude Opus 4.7 and later)</td><td>Returns a 400 error</td><td>Prompting</td></tr>
</tbody></table>
"""

DEEPSEEK_OCT = """
<table><tbody><tr><td>model</td><td><code>deepseek-flash</code><sup>(1)</sup><br><code>deepseek-v4-pro</code></td></tr></tbody></table>
<p>(1) Use <code>deepseek-flash</code> as the model name. The legacy names <code>deepseek-v4-flash</code> and
<code>deepseek-v4-flash-vision-exp</code> are still accepted, but the corresponding models have been retired, their
requests are served by the DeepSeek-V4.1-Flash model and billed at the Flash price.</p>
"""


class TestOctoberLayout(unittest.TestCase):
    def test_deprecated_state_dates_and_replacement(self):
        by_id = {r["model_id"]: r for r in P.parse_anthropic(ANTHROPIC_OCT)}
        s45 = by_id["claude-sonnet-4-5-20250929"]
        self.assertEqual((s45["status"], s45["announced"], s45["retirement"], s45["replacement"]),
                         ("deprecated", "2026-09-30", "2026-11-30", "claude-sonnet-5-5"))
        mp = by_id["claude-mythos-preview"]
        self.assertEqual((mp["status"], mp["announced"], mp["retirement"]), ("deprecated", "2026-06-09", None))
        self.assertEqual(by_id["claude-opus-5-5"]["retirement_not_before"], "2027-09-22")
        self.assertEqual(by_id["claude-opus-5-5"]["family"], "Claude Opus 5.5")

    def test_history_h3_id_dates_replacement_only_models(self):
        by_id = {r["model_id"]: r for r in P.parse_anthropic(ANTHROPIC_OCT)}
        inst = by_id["claude-instant-1.2"]
        self.assertEqual((inst["status"], inst["announced"], inst["retirement"]), ("retired", "2024-09-04", "2024-11-06"))
        self.assertFalse(any("temperature" in k for k in by_id))

    def test_fails_closed(self):
        for html in (ANTHROPIC_OCT.replace("<td>Deprecated</td><td>June 9", "<td>Legacy</td><td>June 9"),
                     ANTHROPIC_OCT.replace("<th>Tentative retirement date</th>", "<th>Retires</th>"),
                     "<p>moved</p>"):
            with self.assertRaises(ValueError):
                P.parse_anthropic(html)
        with self.assertRaises(ValueError):
            P.parse_deepseek(DEEPSEEK_OCT.replace("have been retired", "are being phased out"))
        with self.assertRaises(ValueError):
            P.parse_openai("<p>no tables</p>")

    def test_deepseek_stated_retirements_without_dates(self):
        recs = {r["model_id"]: r for r in P.parse_deepseek(DEEPSEEK_OCT)}
        self.assertEqual(set(recs), {"deepseek-v4-flash", "deepseek-v4-flash-vision-exp"})
        for r in recs.values():
            self.assertEqual((r["status"], r["retirement"], r["replacement"]), ("retired", None, "deepseek-flash"))
            self.assertIn("No retirement date is published.", r["note"])
            self.assertNotIn("1017", r["note"])

    def test_deepseek_oct30_serving_id_is_quoted_and_must_agree(self):
        oct30 = DEEPSEEK_OCT.replace(
            "Use <code>deepseek-flash</code> as the model name. The legacy names <code>deepseek-v4-flash</code> and\n"
            "<code>deepseek-v4-flash-vision-exp</code> are still accepted, but the corresponding models have been retired, their\n"
            "requests are served by the DeepSeek-V4.1-Flash model and billed at the Flash price.",
            "Use <code>deepseek-flash</code> as the model name. It is now served by DeepSeek-V4.1-Flash-1017, "
            "and the calling method remains unchanged. The legacy names <code>deepseek-v4-flash</code> and\n"
            "<code>deepseek-v4-flash-vision-exp</code> are still accepted, but the corresponding models have been retired; their\n"
            "requests are served by DeepSeek-V4.1-Flash-1017 and billed at the Flash price.",
        )
        recs = {r["model_id"]: r for r in P.parse_deepseek(oct30)}
        self.assertEqual(set(recs), {"deepseek-v4-flash", "deepseek-v4-flash-vision-exp"})
        for r in recs.values():
            self.assertEqual((r["status"], r["retirement"], r["replacement"]), ("retired", None, "deepseek-flash"))
            self.assertIn("DeepSeek-V4.1-Flash-1017", r["note"])
        disagree = oct30.replace("served by DeepSeek-V4.1-Flash-1017 and billed", "served by DeepSeek-V4.1-Flash and billed")
        with self.assertRaises(ValueError):
            P.parse_deepseek(disagree)


class TestRealCaptures(unittest.TestCase):
    def _parse(self, path):
        slug = path.name.split("-", 1)[0]
        fn = {"anthropic": P.parse_anthropic, "openai": P.parse_openai, "deepseek": P.parse_deepseek}[slug]
        return {r["model_id"]: r for r in fn(path.read_text(encoding="utf-8"))}

    def test_august_layout(self):
        a = self._parse(HERE / "fixtures" / "anthropic-20260813T100039Z.html")
        self.assertEqual(a["claude-opus-4-1-20250805"]["announced"], "2026-06-05")
        self.assertEqual(a["claude-3-opus-20240229"]["announced"], "2025-06-30")
        self.assertEqual(self._parse(HERE / "fixtures" / "deepseek-20260813T100039Z.html"), {})
        self.assertIn("gpt-4o-2024-05-13", self._parse(HERE / "fixtures" / "openai-20260813T100039Z.html"))

    def test_october_layout(self):
        raw = HERE.parent / "data" / "raw"
        a = self._parse(sorted(raw.glob("anthropic-*.html"))[-1])
        self.assertEqual(a["claude-sonnet-4-5-20250929"]["status"], "deprecated")
        self.assertEqual(a["claude-3-opus-20240229"]["announced"], "2025-06-30")
        d = self._parse(sorted(raw.glob("deepseek-*.html"))[-1])
        self.assertEqual(d["deepseek-v4-flash"]["status"], "retired")


class TestBuildGates(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = Path(tempfile.mkdtemp())
        self.saved = F.RAW_DIR
        F.RAW_DIR = self.tmp

    def tearDown(self):
        F.RAW_DIR = self.saved

    def _capture(self, name, body=b"<html></html>", receipt=None):
        (self.tmp / f"{name}.html").write_bytes(body)
        (self.tmp / f"{name}.sha256").write_text((receipt or hashlib.sha256(body).hexdigest()) + f"  {name}.html\n")
        return self.tmp / f"{name}.html"

    def test_receipt_mismatch_stops(self):
        path = self._capture("anthropic-20261001T000000Z", receipt="0" * 64)
        with self.assertRaises(RuntimeError):
            F._verified_sha256(path)

    def test_newer_failed_capture_stops(self):
        self._capture("anthropic-20261001T000000Z")
        (self.tmp / "anthropic-20261002T000000Z.failed").write_text("x")
        with self.assertRaises(RuntimeError):
            F._latest_capture("anthropic")

    def test_newest_good_capture_used(self):
        (self.tmp / "anthropic-20261001T000000Z.failed").write_text("x")
        path = self._capture("anthropic-20261002T000000Z")
        self.assertEqual(F._latest_capture("anthropic"), path)


if __name__ == "__main__":
    unittest.main()
