#!/usr/bin/env python3
"""
fetch_parse.py — Capture vendor deprecation pages, then build normalized lifecycle data
from the pinned captures.

  python3 pipeline/fetch_parse.py --fetch     capture each page to data/raw/ with a
                                              SHA-256 receipt (a failure leaves a
                                              .failed marker, never a silent gap)
  python3 pipeline/fetch_parse.py             build data/models.json from the newest
                                              captures
  ... --accept-source-change                  after inspecting a changed parse

The build verifies every receipt against its capture's bytes and stops on a
mismatch, on a failed capture newer than the last good one, and on any change in
what a vendor page states compared with pipeline/expected_semantics.json until
the change is reviewed. Fetching uses urllib with a curl fallback (transport
divergence is real) under a hard per-source deadline.

Output: data/models.json
  {"generated_at", "as_of", "sources": [{vendor, url, status, fetched_at, bytes,
   raw_sha256, note}], "models": [...normalized records...]}
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from parser import (  # noqa: E402
    ANTHROPIC_URL,
    DEEPSEEK_URL,
    OPENAI_URL,
    parse_anthropic,
    parse_deepseek,
    parse_openai,
)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
RAW_DIR = DATA_DIR / "raw"
OUT_PATH = DATA_DIR / "models.json"
PIN = Path(__file__).resolve().parent / "expected_semantics.json"

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
FETCH_DEADLINE_SECONDS = 60   # hard wall-clock deadline per source
RETRY_ATTEMPTS = 2

SOURCES = [
    {"vendor": "Anthropic", "slug": "anthropic", "url": ANTHROPIC_URL, "parser": parse_anthropic},
    {"vendor": "OpenAI", "slug": "openai", "url": OPENAI_URL, "parser": parse_openai},
    {
        "vendor": "DeepSeek",
        "slug": "deepseek",
        "url": DEEPSEEK_URL,
        "parser": parse_deepseek,
        "note": "DeepSeek publishes no model deprecation page. Only retirements stated in its "
                "API docs (the model table's footnote) are listed, and it publishes no "
                "retirement dates. Treat it as a documented gap, not a clean bill of health.",
    },
]


def _fetch_urllib(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def _fetch_curl(url: str) -> bytes:
    proc = subprocess.run(
        ["curl", "-sL", "--max-time", "40", "-A", UA, url],
        capture_output=True,
        check=False,
        timeout=45,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"curl exit {proc.returncode}: {proc.stderr.decode()[:200]}")
    return proc.stdout


def _fetch_with_deadline(url: str) -> bytes:
    """Fetch with a hard wall-clock deadline so a hanging server is a recorded
    failure, never a stall. Tries urllib then curl (transport divergence handling)."""
    result: dict = {"ok": False, "data": None, "error": None}

    def work() -> None:
        try:
            result["data"] = _fetch_urllib(url)
            result["ok"] = True
        except Exception as exc:  # noqa: BLE001 — record, then fall back
            result["error"] = f"urllib: {exc}"
            try:
                result["data"] = _fetch_curl(url)
                result["ok"] = True
            except Exception as exc2:  # noqa: BLE001
                result["error"] = f"urllib: {exc}; curl: {exc2}"

    t = threading.Thread(target=work, daemon=True)
    t.start()
    t.join(timeout=FETCH_DEADLINE_SECONDS)
    if t.is_alive():
        raise RuntimeError(f"timed out after {FETCH_DEADLINE_SECONDS}s (hanging server)")
    if not result["ok"]:
        raise RuntimeError(result["error"])
    return result["data"]


def _fetch_with_retry(url: str) -> bytes:
    last_err = None
    for attempt in range(RETRY_ATTEMPTS):
        try:
            return _fetch_with_deadline(url)
        except Exception as exc:  # noqa: BLE001
            last_err = exc
    raise RuntimeError(f"failed after {RETRY_ATTEMPTS} attempts: {last_err}")


def capture() -> None:
    """Capture each source to data/raw/<slug>-<UTC stamp>.html plus a .sha256 receipt."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    for src in SOURCES:
        base = RAW_DIR / f"{src['slug']}-{stamp}"
        try:
            body = _fetch_with_retry(src["url"])
        except Exception as exc:  # noqa: BLE001 — recorded, and the build refuses to use an older capture silently
            base.with_suffix(".failed").write_text(f"{src['url']}\n{exc}\n", encoding="utf-8")
            print(f"FAIL {src['vendor']}: {exc}", file=sys.stderr)
            continue
        base.with_suffix(".html").write_bytes(body)
        base.with_suffix(".sha256").write_text(f"{hashlib.sha256(body).hexdigest()}  {base.name}.html\n", encoding="utf-8")
        print(f"OK   {src['vendor']}: {len(body)} bytes -> {base.name}.html")


def _latest_capture(slug: str) -> Path:
    captures = sorted(RAW_DIR.glob(f"{slug}-*.html"))
    failures = sorted(RAW_DIR.glob(f"{slug}-*.failed"))
    if not captures:
        raise RuntimeError(f"no capture for {slug}; run with --fetch")
    if failures and failures[-1].stem > captures[-1].stem:
        raise RuntimeError(f"newest {slug} capture failed ({failures[-1].name}); re-fetch before building")
    return captures[-1]


def _verified_sha256(capture_path: Path) -> str:
    receipt = capture_path.with_suffix(".sha256")
    if not receipt.exists():
        raise RuntimeError(f"no receipt for {capture_path.name}")
    stated = receipt.read_text(encoding="utf-8").split()[0]
    actual = hashlib.sha256(capture_path.read_bytes()).hexdigest()
    if stated != actual:
        raise RuntimeError(f"receipt mismatch for {capture_path.name}: receipt {stated}, bytes {actual}")
    return actual


def _semantics(models: list[dict]) -> list:
    """What the vendor page states; statuses derived from today's date are left out."""
    rows = [[m["model_id"], m.get("family"), m.get("stated_status"), m.get("announced"),
             m.get("retirement"), m.get("retirement_not_before"), m.get("replacement")] for m in models]
    return sorted(rows, key=lambda r: [str(v) for v in r])


def build(accept: bool) -> None:
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    sources_out: list[dict] = []
    models_out: list[dict] = []
    semantics: dict = {}
    for src in SOURCES:
        path = _latest_capture(src["slug"])
        sha = _verified_sha256(path)
        stamp = path.stem.split("-", 1)[1]
        fetched = datetime.strptime(stamp, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        body = path.read_bytes()
        models = src["parser"](body.decode("utf-8", errors="replace"))
        for m in models:
            m["as_of"] = fetched.date().isoformat()
            m["fetched_at"] = fetched.isoformat(timespec="seconds")
        semantics[src["slug"]] = _semantics(models)
        models_out.extend(models)
        sources_out.append({
            "vendor": src["vendor"],
            "url": src["url"],
            "status": "ok" if not src.get("note") else "gap",
            "fetched_at": fetched.isoformat(timespec="seconds"),
            "bytes": len(body),
            "raw_sha256": sha,
            "note": src.get("note"),
        })

    if accept:
        PIN.write_text(json.dumps(semantics, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"Accepted semantic pin: {PIN}")
    elif not PIN.exists():
        raise RuntimeError("No semantic pin. Inspect parsed records, then rerun with --accept-source-change.")
    else:
        expected = json.loads(PIN.read_text(encoding="utf-8"))
        changed = sorted(k for k in set(expected) | set(semantics) if expected.get(k) != semantics.get(k))
        if changed:
            raise RuntimeError(f"Vendor statements changed for {changed}. Inspect before --accept-source-change.")

    status_order = {"deprecated": 0, "active": 1, "retired": 2}
    models_out.sort(key=lambda m: (
        status_order.get(m["status"], 9),
        m.get("retirement") or "9999-99-99",
        m["vendor"],
        m["model_id"],
    ))
    payload = {
        "generated_at": generated_at,
        "as_of": max(s["fetched_at"] for s in sources_out)[:10],
        "sources": sources_out,
        "models": models_out,
    }
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    counts: dict = {}
    for m in models_out:
        counts[m["status"]] = counts.get(m["status"], 0) + 1
    print(f"wrote {OUT_PATH}: {len(models_out)} models {counts}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Capture vendor deprecation pages, or build models.json from captures.")
    ap.add_argument("--fetch", action="store_true", help="capture fresh pages into data/raw/ (then build)")
    ap.add_argument("--accept-source-change", action="store_true", help="replace the semantic pin after review")
    args = ap.parse_args()
    if args.fetch:
        capture()
    build(args.accept_source_change)


if __name__ == "__main__":
    main()
