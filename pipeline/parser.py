#!/usr/bin/env python3
"""
parser.py — Parse vendor model-deprecation pages into normalized lifecycle records.

Pure functions (HTML string in, records out) so tests can run offline with fixtures.

Record shape (one per model family / model snapshot):
{
  "vendor": "Anthropic" | "OpenAI",
  "family": "Claude Opus 4.1",            # human-readable family name
  "model_id": "claude-opus-4-1-20250805",  # vendor model identifier(s)
  "status": "active" | "deprecated" | "retired",
  "announced": "2026-06-05",               # ISO date or None
  "retirement": "2026-08-05",              # ISO date or None (active: None)
  "replacement": "claude-opus-4-8",        # recommended replacement or None
  "source_url": "https://...",             # vendor page this record came from
  "note": "..."                            # optional human note (e.g. notice floor)
}
"""

from __future__ import annotations

import html as html_mod
import re
from datetime import date
from typing import List, Optional

MONTHS = {
    "January": 1, "February": 2, "March": 3, "April": 4, "May": 5, "June": 6,
    "July": 7, "August": 8, "September": 9, "October": 10, "November": 11, "December": 12,
}
MONTHS_ABBR = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}

ANTHROPIC_URL = "https://docs.claude.com/en/docs/about-claude/model-deprecations"
OPENAI_URL = "https://developers.openai.com/api/docs/deprecations"
DEEPSEEK_URL = "https://api-docs.deepseek.com/"

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _strip_tags(s: str) -> str:
    """Remove HTML tags, unescape entities, collapse whitespace."""
    s = re.sub(r"<[^>]+>", " ", s)
    s = html_mod.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def _text(html: str) -> str:
    """Whole-document text (scripts/styles stripped) for prose extraction."""
    s = re.sub(r"<script.*?</script>", " ", html, flags=re.S)
    s = re.sub(r"<style.*?</style>", " ", s, flags=re.S)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html_mod.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def _parse_us_date(s: str) -> Optional[str]:
    """'August 5, 2026' / 'Aug 5, 2026' / ISO '2026-02-17' -> '2026-02-17'.
    None if not parseable. (Vendor pages mix US-form and ISO dates across
    sections — the original parser accepted only US-form and silently dropped
    every ISO-dated retirement. Fix ported from notice-policy, S126.)"""
    if not s:
        return None
    s = s.strip()
    m = re.match(r"([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})", s)
    if m:
        mon = MONTHS.get(m.group(1)) or MONTHS_ABBR.get(m.group(1)[:3])
        if mon is not None:
            return f"{m.group(3)}-{mon:02d}-{int(m.group(2)):02d}"
    m = re.match(r"(\d{4}-\d{2}-\d{2})", s)
    if m:
        return m.group(1)
    return None


def _today_iso() -> str:
    return date.today().isoformat()


# ---------------------------------------------------------------------------
# Anthropic
# ---------------------------------------------------------------------------

def _parse_not_sooner(s: str) -> Optional[str]:
    """'Not sooner than November 24, 2026' -> '2026-11-24'. None if not parseable."""
    if not s or "Not sooner than" not in s:
        return None
    return _parse_us_date(s.replace("Not sooner than", ""))


# Dated history headings, two layouts: Aug 2026 nests id="YYYY-MM-DD-<slug>" in a
# div inside the <h3>; Oct 2026 puts the id on the <h3> itself.
_DATED_H3 = re.compile(
    r'<h3\b[^>]*?\bid="(\d{4}-\d{2}-\d{2})-[^"]+"'
    r'|<h3[^>]*>\s*<div class="group relative pt-6 pb-2" id="(\d{4}-\d{2}-\d{2})-[^"]+"'
)
_STATUS_COLUMNS = ["API model name", "Current state", "Deprecated", "Tentative retirement date"]
_STATES = {"Active": "active", "Deprecated": "deprecated", "Retired": "retired"}


def _header(rows: List[str]) -> List[str]:
    return [_strip_tags(c) for c in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", rows[0], flags=re.S)] if rows else []


def parse_anthropic(html: str) -> List[dict]:
    """
    Parse the Anthropic model-deprecations page.

    1. Model status table, columns mapped by label:
         API model name | Current state | Deprecated | Tentative retirement date
       States are Active / Deprecated / Retired; any other state, a missing
       column or a missing table stops the parse (fail closed). Active rows carry
       a "Not sooner than <date>" bound, which is never shown as a retirement date.
    2. Replacement tables (Retirement date | Deprecated model | Recommended
       replacement), one per dated history section. The section's <h3> id carries
       the announcement date.
    Models only in a replacement table (Claude 1.x/2.x, Instant, early 3.x) are
    emitted from it; their status comes from the retirement date.
    """
    status_rows = None
    replacement_tables = []
    for match in re.finditer(r"<table[^>]*>(.*?)</table>", html, flags=re.S):
        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", match.group(1), flags=re.S)
        header = _header(rows)
        if header and header[0] == "API model name":
            missing = [c for c in _STATUS_COLUMNS if c not in header]
            if missing:
                raise ValueError(f"Anthropic status table: missing column(s) {missing}; header {header}")
            status_rows = (rows[1:], [header.index(c) for c in _STATUS_COLUMNS])
        elif header[:3] == ["Retirement date", "Deprecated model", "Recommended replacement"]:
            heads = list(_DATED_H3.finditer(html, 0, match.start()))
            announced = (heads[-1].group(1) or heads[-1].group(2)) if heads else None
            replacement_tables.append((rows[1:], announced))
    if status_rows is None:
        raise ValueError("Anthropic deprecations page: no model status table ('API model name' header) found")
    if not replacement_tables:
        raise ValueError("Anthropic deprecations page: no replacement tables found")

    records: List[dict] = []
    by_id: dict = {}
    rows, cols = status_rows
    for row in rows:
        cells = [_strip_tags(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", row, flags=re.S)]
        if not cells:
            continue
        model_id, state, deprecated, retirement = [cells[i] if i < len(cells) else "" for i in cols]
        if not model_id:
            continue
        if state not in _STATES:
            raise ValueError(f"Anthropic status table: unknown state {state!r} for {model_id}")
        ret_iso = _parse_us_date(retirement) if retirement not in ("", "N/A", "To be announced") else None
        bound = _parse_not_sooner(retirement)
        if retirement not in ("", "N/A", "To be announced") and not ret_iso and not bound:
            raise ValueError(f"Anthropic status table: unreadable retirement {retirement!r} for {model_id}")
        rec = {
            "vendor": "Anthropic",
            "model_id": model_id,
            "status": _STATES[state],
            "stated_status": state,
            "announced": _parse_us_date(deprecated) if deprecated not in ("", "N/A") else None,
            "retirement": None if bound else ret_iso,
            "retirement_not_before": bound if state == "Active" else None,
            "replacement": None,
            "source_url": ANTHROPIC_URL,
            "note": None,
        }
        if rec["status"] == "active" and not bound:
            rec["note"] = "No retirement announced"
        elif rec["status"] == "deprecated" and not rec["retirement"]:
            rec["note"] = "Deprecated; retirement date to be announced"
        records.append(rec)
        by_id[model_id] = rec

    for rows, announced in replacement_tables:
        for row in rows:
            cells = [_strip_tags(c) for c in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", row, flags=re.S)]
            if len(cells) < 3:
                continue
            ret_iso = _parse_us_date(cells[0])
            if not ret_iso:
                continue
            for old in re.split(r"[,\s]+", cells[1]):
                if not old:
                    continue
                if old in by_id:
                    have = by_id[old]
                    have["replacement"] = have["replacement"] or cells[2] or None
                    have["announced"] = have["announced"] or announced
                    continue
                retired = ret_iso <= _today_iso()
                rec = {
                    "vendor": "Anthropic",
                    "model_id": old,
                    "status": "retired" if retired else "deprecated",
                    "announced": announced,
                    "retirement": ret_iso,
                    "retirement_not_before": None,
                    "replacement": cells[2] or None,
                    "source_url": ANTHROPIC_URL,
                    "note": "Not in the current status table; dates from the vendor's deprecation history" if retired else None,
                }
                records.append(rec)
                by_id[old] = rec

    # Family name from model id (best effort: strip date suffix + version tail)
    # Model ids end in a compact YYYYMMDD date ("claude-opus-4-1-20250805");
    # the version digits before it are joined back with a dot ("Opus 4.1").
    # (Fix ported from notice-policy, S126 — the original regex captured only
    # the first version token and rendered 'claude-opus-4-1-20250805' as
    # 'Claude Opus'.)
    for rec in records:
        mid = rec["model_id"]
        m = re.match(r"claude-(.+)-(\d{8})$", mid)
        if m:
            base = re.sub(r"\b(\d+)\s+(\d+)\b", r"\1.\2", m.group(1).replace("-", " "))
            rec["family"] = f"Claude {base.title()}"
            continue
        m = re.match(r"claude-(.+)$", mid)
        if m:
            base = re.sub(r"\b(\d+)\s+(\d+)\b", r"\1.\2", m.group(1).replace("-", " "))
            rec["family"] = "Claude " + base.title()
        else:
            rec["family"] = mid

    return records


# ---------------------------------------------------------------------------
# OpenAI
# ---------------------------------------------------------------------------

def parse_openai(html: str) -> List[dict]:
    """
    Parse the OpenAI API deprecations page.

    Model deprecation sections have the pattern:
        YYYY-MM-DD: <Title> ... <table>
        where the table has columns: Shutdown date | Model/system | Recommended replacement
    Parsing walks the HTML <table> elements directly (cells stay separated, so
    parenthetical replacements like "gpt-5.6-sol ( reasoning.mode: pro )" survive).
    """
    records: List[dict] = []
    # Every model-deprecation table carries a replacement header
    # ("Recommended replacement", "Recommended replacement base model",
    #  or "Substitute model").
    for tbl in re.findall(r"<table[^>]*>(.*?)</table>", html, flags=re.S):
        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", tbl, flags=re.S)
        if not rows:
            continue
        header = [_strip_tags(c) for c in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", rows[0], flags=re.S)]
        if not any(("replacement" in h.lower() or "substitute" in h.lower()) for h in header):
            continue
        # Locate column indices (header order can vary)
        try:
            i_date = next(i for i, h in enumerate(header) if "shut" in h.lower() or "retir" in h.lower())
            i_model = next(i for i, h in enumerate(header) if "model" in h.lower() or "system" in h.lower())
            i_repl = next(i for i, h in enumerate(header) if "replacement" in h.lower() or "substitute" in h.lower())
        except StopIteration:
            continue
        # Find the section announcement date: walk backward from the table in the
        # raw HTML to the nearest "YYYY-MM-DD:" header.
        tbl_pos = html.find(tbl)
        before = html[max(0, tbl_pos - 30000):tbl_pos]
        m = re.findall(r"(\d{4}-\d{2}-\d{2}):", before)
        ann_date = m[-1] if m else None

        for row in rows[1:]:
            cells = [_strip_tags(c) for c in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", row, flags=re.S)]
            if len(cells) <= max(i_date, i_model, i_repl):
                continue
            ret_date = _parse_us_date(cells[i_date])
            if not ret_date:
                continue
            # Split on [|,] only — never on whitespace. Model IDs never contain
            # spaces/equals/colons, so whitespace-fragmented descriptive cells
            # ("New fine-tuning training on babbage-002") would mint garbage
            # records under whitespace splitting. (Fix ported from notice-policy,
            # S126.)
            for mid in re.split(r"[|,]+", cells[i_model]):
                mid = mid.strip()
                if not mid:
                    continue
                # Row-level exclusions:
                #  - API/system deprecations are not models (Assistants API,
                #    Videos API, /v1/engines, OpenAI-Beta: ...)
                #  - descriptive cells are not model IDs — model IDs never
                #    contain spaces/equals/colons
                if mid.startswith("/") or "api" in mid.lower() or mid.startswith("OpenAI-Beta"):
                    continue
                if " " in mid or "=" in mid or ":" in mid:
                    continue
                status = "retired" if ret_date <= _today_iso() else "deprecated"
                records.append({
                    "vendor": "OpenAI",
                    "family": mid,
                    "model_id": mid,
                    "status": status,
                    "announced": ann_date,
                    "retirement": ret_date,
                    "replacement": cells[i_repl] or None,
                    "source_url": OPENAI_URL,
                    "note": None,
                })

    if not records:
        raise ValueError("OpenAI deprecations page: no shutdown/replacement tables parsed")
    # Deduplicate (a model can appear in multiple sections)
    seen = set()
    out = []
    for rec in records:
        key = (rec["model_id"], rec["retirement"])
        if key in seen:
            continue
        seen.add(key)
        out.append(rec)
    return out


# ---------------------------------------------------------------------------
# DeepSeek (no deprecation page; retirements stated in the API docs footnote)
# ---------------------------------------------------------------------------

def parse_deepseek(html: str) -> List[dict]:
    """
    DeepSeek publishes no deprecation page. Its API docs model table carries a
    footnote, and the only lifecycle facts are the ones that footnote states.
    Known wordings:
      Aug 2026: "The deepseek-v4-flash model has been updated to ..." (no retirement)
      Oct 2026: "Use deepseek-flash as the model name. The legacy names X and Y
                 are still accepted, but the corresponding models have been
                 retired, their requests are served by the DeepSeek-V4.1-Flash
                 model and billed at the Flash price."
    Any other footnote wording stops the parse (fail closed). No retirement date
    is published, so retired records carry none.
    """
    model_cell = None
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", html, flags=re.S):
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, flags=re.S)
        if len(cells) >= 2 and re.sub(r"\(\d+\)", "", _strip_tags(cells[0])).strip().lower() == "model":
            model_cell = cells[1]
            break
    if model_cell is None:
        raise ValueError("DeepSeek docs: model row not found")
    text = _text(html)
    updated = re.search(r"The deepseek-[a-z0-9.-]+ model has been updated to ", text)
    legacy = re.search(
        r"Use (deepseek-[a-z0-9.-]+) as the model name\. The legacy names ([^.]+?) are still accepted, "
        r"but the corresponding models have been retired, their requests are served by the "
        r"[A-Za-z0-9.-]+ model and billed at the [A-Za-z]+ price\.",
        text,
    )
    if "(1)" in text and not (updated or legacy):
        raise ValueError("DeepSeek docs: model footnote wording not recognized; inspect before parsing")
    records: List[dict] = []
    if legacy:
        for old in re.findall(r"deepseek-[a-z0-9.-]+", legacy.group(2)):
            records.append({
                "vendor": "DeepSeek",
                "family": old,
                "model_id": old,
                "status": "retired",
                "stated_status": "retired",
                "announced": None,
                "retirement": None,
                "retirement_not_before": None,
                "replacement": legacy.group(1),
                "source_url": DEEPSEEK_URL,
                "note": "DeepSeek's API docs: " + legacy.group(0) + " No retirement date is published.",
            })
    return records


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    import json
    import sys
    from pathlib import Path

    fixture = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    if fixture:
        html = fixture.read_text(encoding="utf-8")
        if "claude" in fixture.name.lower():
            records = parse_anthropic(html)
        else:
            records = parse_openai(html)
        print(json.dumps(records, indent=2))
        print(f"\n{len(records)} records", file=sys.stderr)
    else:
        print("usage: parser.py <html-fixture>", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
