# Changelog

## 2026-04-28

### Changed
- Replaced the Gemini CLI summary step with a stdlib Gemini API request from `scripts/generate_morning_report.py`.
- Made explicit `--input` payload paths fail fast when missing so news rows always come from the requested payload.
- Changed `scripts/generate_morning_report.py` to print concise status to stderr by default instead of printing the full markdown.
- Added retry handling for temporary Gemini API 429/503 failures and clean CLI failure status without Python tracebacks.
- Made LLM summary generation provider-selectable with a table-only fallback when the selected provider key is missing or the provider API fails.
- Changed the default summary provider to `openai` and the default OpenAI model to `gpt-5.4-nano`.
- Added data dates to report table headings so each section exposes the timeliness of the fetched data.
- Made the morning report date optional; the generator now defaults to yesterday in Taiwan time and also accepts a positional date.
- Added explicit `--live` and `--mock` modes, made live the generator default, and deprecated `MORNING_REPORT_USE_LIVE=1`.

### Added
- Added tests for disabled-summary report generation and explicit-input news sourcing.
- Added `--print-report` to print the final markdown to stdout when needed.
- Added `--refresh` to force refetching the default dated payload before rendering.
- Added `--summary-provider none|gemini|openai|grok` and `--summary-model` so summary text can use Gemini, OpenAI, or Grok when requested.
- Added xAI/Grok summary support through `XAI_API_KEY`, `XAI_MODEL`, and the default `grok-4-1-fast` model.

## 2026-04-23

### Added
- Added EODHD historical EOD integration for Asia indices in the morning report fetcher.
- Added fixed EODHD symbol mappings for `N225`, `HSI`, and `KS11` in `scripts/symbol_map.json`.
- Added `EODHD_API_KEY` support for live Asia index fetching.
- Added `unittest` coverage and an EODHD historical fixture for Asia index normalization and fetch behavior.

### Changed
- Replaced the old Asia placeholder-only fetch path with live EODHD-backed retrieval while preserving partial-success behavior.
- Updated Asia index diagnostics so `meta.source_status`, `meta.index_status`, `meta.provider_by_field`, and `meta.request_debug` now reflect EODHD results.
- Changed the canonical Taiwan institutional flow unit from `TWD 100 million` to `新台幣億元` in payloads and rendered reports.
- Refreshed checked-in mock/sample payloads and report artifacts to match the new unit string and Asia-source behavior.

### Fixed
- Fixed the remaining live data gap for `asia_market.indices` by sourcing `N225`, `HSI`, and `KS11` from EODHD instead of rendering them as permanently unavailable.
- Fixed stale sample artifacts that still advertised the old Asia placeholder path and English institutional unit string.

## 2026-04-20

### Added
- Added a reusable Gemini CLI market morning report workflow centered on `GEMINI.md` and `.gemini/commands/market/morning.toml`.
- Added `data/raw/mock_morning.json` as a stable mock payload for prompt and formatting validation.
- Added `scripts/fetch_morning_data.py` as the unified JSON fetcher entrypoint for mock and live modes.
- Added structured news fallback fields (`summary_text`, `brief`) to reduce Gemini CLI summary dropouts.
- Added richer HTTP diagnostics so API failures now include response body excerpts when available.

### Changed
- Rewrote `GEMINI.md` to use stable UTF-8 rules for Traditional Chinese output, fixed section ordering, and stricter missing-data handling.
- Reworked `/market:morning` prompt constraints so sections 1 to 5 prefer short paragraphs, section 6 uses a fixed news line format, and summaries stay conservative.
- Defaulted the fetcher to mock mode unless `MORNING_REPORT_USE_LIVE=1` is explicitly set.
- Improved Windows compatibility for the Gemini CLI workflow and local JSON loading behavior.
- Updated Twelve Data and Marketaux HTTP request headers to look more like normal browser traffic.

### Fixed
- Fixed corrupted/garbled command and rules text caused by encoding issues in `GEMINI.md` and `morning.toml`.
- Fixed Gemini CLI prompt behavior so news summaries are preserved more reliably from structured JSON.
- Fixed FinMind live mapping for Taiwan morning report data:
  - TWSE now uses FinMind's intraday indicator feed instead of an incorrect total-return index value.
  - Institutional flows now use a more appropriate "three major institutional investors" aggregation.
  - USD/TWD, gold, and WTI crude are mapped into the shared report schema.
- Fixed WTI previous-value lookup by extending the FinMind lookback window.
- Fixed Treasury integration by replacing earlier broken dataset attempts with the official Treasury daily yield curve XML feed.
- Fixed multiple Treasury parser issues, including wrong source selection, XML structure assumptions, and date-field matching.
- Fixed Marketaux access issues caused by Cloudflare-style `1010` blocking by making request headers look more like standard browser traffic.
- Fixed Twelve Data access issues caused by Cloudflare-style `1010` blocking by making request headers look more like standard browser traffic.
- Improved Twelve Data index retrieval by adding `previous_close=true` handling so one daily bar plus previous close can still produce valid change metrics.

### Current live status
- FinMind: working for Taiwan market, institutional flows, FX, gold, and crude oil.
- U.S. Treasury: working for 2Y, 10Y, 30Y, and 10Y-2Y slope.
- Marketaux: working after request fingerprint/header adjustments.
- Twelve Data: no longer blocked by Cloudflare-style `1010`; current work is focused on more resilient symbol fallback mapping, `previous_close` support, `/quote` fallback usage, and partial-success handling.

### Known limitations
- `tpex_index` is intentionally left `null` in live mode until a plain OTC index source with the correct definition is confirmed.
- Twelve Data still needs final verification of the best symbol mapping for U.S. and Asia index series.

### Remaining missing live data
- `taiwan_market.tpex_index`
  - The report still lacks a confirmed plain-definition OTC index source for live mode.
- `us_market.indices`
  - Missing target series:
    - `SPX` / S&P 500
    - `IXIC` / Nasdaq Composite
    - `DJI` / Dow Jones Industrial Average
    - `RUT` / Russell 2000
- `asia_market.indices`
  - Missing target series:
    - `N225` / Nikkei 225
    - `HSI` / Hang Seng Index
    - `KS11` / KOSPI
- All missing index series require the same schema fields when restored:
  - `symbol`
  - `name`
  - `close`
  - `change`
  - `change_percent`

## 2026-04-21

### Added
- Added official TPEx OTC index fallback wiring from the TPEx / data.gov.tw dataset into `taiwan_market.tpex_index`.
- Added file-backed runtime index mappings in `scripts/symbol_map.json` plus `scripts/bootstrap_symbol_map.py` for one-time Twelve Data discovery.
- Added normalized provider handling for FMP U.S. indices, Twelve Data / Alpha Vantage Asia indices, and TPEx index payloads.
- Added `meta.sources_used`, `meta.partial_success`, `meta.missing_sections`, `meta.provider_by_field`, `meta.index_status`, and `meta.request_debug` for faster diagnostics.
- Added `unittest` coverage and live-like fixtures for TPEx parsing, provider normalization, fallback selection, and partial-success payload assembly.
- Added `README.md` with source, env var, fallback, and troubleshooting guidance.

### Changed
- Refactored U.S. and Asia index retrieval into per-series partial-success handling so one failed symbol no longer breaks the whole regional block.
- Twelve Data now uses a fixed mapping file at runtime instead of ad hoc candidate guessing.
- Alpha Vantage now acts as an optional best-effort per-series fallback instead of a primary provider.
- Replaced U.S. index retrieval with an FMP-only path using fixed `^`-prefixed index symbols and FMP quote/history endpoints.
- Limited Twelve Data symbol discovery bootstrap to Asia indices; U.S. mappings are now fixed FMP symbols.

### Current live status
- FinMind: working in the latest live run for Taiwan market, institutional flows, FX, gold, and WTI crude.
- TPEx official dataset: working for `taiwan_market.tpex_index`.
- FMP: working for all required U.S. indices (`SPX`, `IXIC`, `DJI`, `RUT`) in the latest live run.
- U.S. Treasury: working for 2Y, 10Y, 30Y, and 10Y-2Y slope.
- Marketaux: working.
- Asia indices: still unresolved on the current Twelve Data / Alpha Vantage path.

### Known limitations
- FMP U.S. quote normalization still falls back to FMP historical EOD in live mode because the quote parser expects `changesPercentage` while the live response uses `changePercentage`.
- `meta.index_status.us_market.*.fallback_used` is therefore currently `true` for successful U.S. index runs even though FMP quote data is present.
- Asia index retrieval still depends on provider access that has not produced usable live results for `N225`, `HSI`, or `KS11`.
- TPEx CSV parsing and TPEx test fixtures still contain some encoding-garbled field names internally; live behavior is working, but the parser/helpers should be cleaned up.

### Remaining missing live data
- `asia_market.indices`
  - Missing target series:
    - `N225` / Nikkei 225
    - `HSI` / Hang Seng Index
    - `KS11` / KOSPI

### Next recommended fixes
- Fix FMP quote normalization to accept `changePercentage` so U.S. indices succeed on the quote endpoint without unnecessary historical fallback.
- Reclassify provider-plan/rate-limit failures for Asia indices under a clearer `provider` or `plan` error category instead of the current generic `parser` bucket.
- Decide whether Asia indices should move to a different provider such as FMP or FRED-based partial coverage, or remain on Twelve Data / Alpha Vantage pending plan changes.

## 2026-04-22

### Added
- Added `scripts/render_morning_report.py` as the deterministic markdown renderer for sections 1 to 7.
- Added `scripts/generate_morning_report.py` to orchestrate fetch, payload archive, deterministic template render, Gemini summary generation, and local final markdown merge.
- Added payload archiving under `payloads/<date>.json` as the dated record of each run.
- Added Gemini summary sidecar output under `reports/morning-<date>.summaries.json`.
- Added deterministic template output under `reports/morning-<date>.template.md`.
- Added renderer and wrapper tests covering:
  - missing Asia placeholder rows
  - bond-date preservation
  - local summary merge
  - payload archive writes
  - Marketaux news URL extraction

### Changed
- Reworked `/market:morning` so Gemini no longer writes the final markdown body directly.
- Moved report table generation entirely into Python so headings, rows, numeric values, and missing-data markers are deterministic.
- Changed Gemini usage to summary-only output:
  - sections 1 to 6 are short paragraphs
  - section 7 is 3 to 5 bullets
  - the final markdown file is merged and written locally in Python
- Changed the news table so article URLs are embedded in the `標題` cell as markdown links instead of a separate `連結` column.
- Changed the live fetch flow to keep Asia rows in report shape but mark them as intentionally unavailable.
- Updated docs and command flow to reflect deterministic rendering, payload archives, and local finalization.

### Removed
- Removed Twelve Data from the live fetch path.
- Removed Alpha Vantage from the default live Asia path.
- Removed the old Twelve Data / Alpha Vantage helper functions and symbol bootstrap flow from active use.

### Fixed
- Fixed severe report drift where Gemini-generated markdown could disagree with `out.json`.
- Fixed saved-report corruption by moving the final markdown write path out of Gemini and into Python UTF-8 output.
- Fixed news payload rendering to preserve direct article URLs from Marketaux.
- Fixed Gemini summary parsing so only the required summary JSON object is accepted, avoiding false matches against JSON-looking table cells such as `market_breadth`.
- Fixed Windows command-length issues in the headless Gemini wrapper by passing the markdown template through stdin instead of the command line.

### Current live status
- FinMind: working for Taiwan market, institutional flows, USD/TWD, gold, and WTI crude.
- TPEx official dataset: working for `taiwan_market.tpex_index`.
- FMP: working for all required U.S. indices.
- U.S. Treasury: working for 2Y, 10Y, 30Y, and 10Y-2Y slope.
- Marketaux: working for news payloads and direct article URLs.
- Morning report generation: working with deterministic tables, Gemini-written summaries, local final merge, and UTF-8-safe markdown output.
- Asia indices: intentionally unavailable for now and rendered as `資料暫缺`.

### Known limitations
- Asia indices are no longer sourced live; `N225`, `HSI`, and `KS11` remain placeholders until a replacement provider is chosen.
- The Gemini summary step is still an external CLI dependency, so end-to-end report generation depends on local Gemini CLI access and permissions.
- The latest known corrupted live file was `reports/morning-2026-04-21.md`; current runs use the new local-finalization path and should not reuse that file as a reference artifact.

### Remaining missing live data
- `asia_market.indices`
  - Missing target series:
    - `N225` / Nikkei 225
    - `HSI` / Hang Seng Index
    - `KS11` / KOSPI

### Next recommended fixes
- Decide on a replacement provider strategy for Asia indices, or keep the current placeholder-only behavior explicitly.
- Add a stricter end-to-end regression check that compares the final saved markdown against the deterministic template plus summary sidecar.
- Optionally improve section-summary prompting so Gemini prose stays shorter and more mechanical for debugging-oriented runs.
