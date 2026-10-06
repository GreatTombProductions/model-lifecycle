# Model Lifecycle Watch

A deprecation radar for AI models. Track which models are **active**, **deprecated**, or **retired** — with announcement dates, shutdown dates, recommended replacements, and the vendor's own page as the source.

**Live site:** https://greattombproductions.github.io/model-lifecycle/

---

## Why this exists

Model vendors publish deprecation notices on docs pages. Most developers never see them. When a model retires, requests start failing — often with little warning.

- **Anthropic** gave 60 days notice for Opus 4.1, retiring it August 5, 2026.
- **OpenAI** maintains a full deprecation table with shutdown dates and replacements.
- Meanwhile, SEO content farms and aggregator trackers monetize the confusion — inventing release names, inflating status claims, selling panic.

This site reads the **vendor's own table** and renders it as searchable status cards. No aggregator in the middle. The vendor page is the source; every record links to it.

## What you can do

- **Search** any model name (`opus`, `gpt-5`, `o3`, `haiku`, `deepseek`, …)
- **Filter** by status (Active / Deprecated / Retired) and vendor
- **See the facts**: shutdown date, announcement date, recommended replacement, and the source page
- **Active models** show their "available until at least" guarantee, so you know how much runway you have

## Data & freshness

The site is a snapshot built from saved captures of the vendor pages:

| Source | What's read | Status |
|--------|-------------|--------|
| Anthropic | Model status table + deprecation-history replacement tables (docs.claude.com) | ✅ Read |
| OpenAI | API deprecations tables (developers.openai.com) | ✅ Read |
| DeepSeek | API docs model-table footnote (api-docs.deepseek.com) | ⚠️ No deprecation page. Only the retirements its docs state are shown, and DeepSeek publishes no dates for them |

Every record carries an **as-of date** (the capture date). Each capture is kept in `data/raw/` with a SHA-256 receipt, and the footer of the site lists the hash of every page used.

## Repository layout

- `index.html`, `app.js`, `style.css`: the site
- `data/models.json`: the snapshot the site reads
- `data/raw/`: the vendor pages as captured, each with a `.sha256` receipt
- `pipeline/fetch_parse.py` (capture and build) and `pipeline/parser.py` (pure HTML-to-records parsers)
- `pipeline/expected_semantics.json`: the reviewed record of what each page stated at the last accepted build
- `tests/`: parser tests (both page layouts seen so far, with August 2026 captures in `tests/fixtures/`), build-gate tests, and a browser smoke test

## Re-running the pipeline

```bash
python3 pipeline/fetch_parse.py --fetch    # capture the pages into data/raw/, then build
python3 pipeline/fetch_parse.py            # rebuild data/models.json from the newest captures
python3 -m pytest -q tests                 # parser and build-gate tests
SMOKE_SITE=. python3 tests/run_browser_smoke.py   # drive the page in headless Chromium (needs Node with Playwright)
```

The build stops rather than publishing something doubtful. It stops when a receipt doesn't match its capture, when the newest capture of a page failed, when a page's layout or wording isn't recognized, and when anything a vendor states differs from `pipeline/expected_semantics.json`. Inspect the parsed records, then rerun with `--accept-source-change`. Fetching uses urllib with a curl fallback, because some vendor pages behave differently per transport, and a hard per-source deadline, so a hanging server becomes a recorded failure instead of a stall.

## Honest coverage

- DeepSeek is **listed as a gap**, not hidden. The absence of a deprecation page is documented, not treated as a clean bill of health.
- A page that moved or changed shape stops the build instead of producing an empty or partial table.
- This is a watch page, not an alerting service. No accounts, no subscriptions, no tracking.

## License / status

Built as an open, static, no-obligation tool. Data is from the vendors' public pages; verify against the source before making decisions. This project is unmaintained by design — it works as a snapshot, and the pipeline is open for anyone to re-run.
