# Next Dialogue Handoff

## Current status
- `taiwan_market.tpex_index` is fixed and live via the official TPEx dataset.
- U.S. indices are on an FMP-only path and succeeded live for:
  - `SPX`
  - `IXIC`
  - `DJI`
  - `RUT`
- FinMind, Treasury, and Marketaux all succeeded in the latest live run.
- The morning report is now generated in a deterministic two-step local flow:
  - Python renders all tables and placeholders
  - Gemini returns summary JSON only
  - Python merges summaries and writes the final markdown
- The final saved report is no longer written by Gemini directly, which fixed the markdown garbling issue seen in the earlier live file.
- News links now render as markdown links embedded in the news title cell.
- Each run now writes:
  - `out.json`
  - `payloads/<date>.json`
  - `reports/morning-<date>.template.md`
  - `reports/morning-<date>.summaries.json`
  - `reports/morning-<date>.md`
- Remaining missing live section is still `asia_market.indices`, but it is now intentionally rendered as placeholder rows instead of provider-failure noise.

## Remaining gaps
- `N225` / Nikkei 225
- `HSI` / Hang Seng Index
- `KS11` / KOSPI

## Known code issues
- There is no current live Asia source. The code now treats Asia rows as intentionally unavailable rather than failed provider results.
- The Gemini summary step depends on the local `gemini` CLI working in headless mode, so environment or permission issues there can still block full report generation.
- The old corrupted report file `reports/morning-2026-04-21.md` is stale and should not be used to judge the current pipeline.

## Recommended next work
1. Decide whether to keep Asia indices as placeholders or add a replacement live provider.
2. If Asia coverage is needed, choose the provider strategy first, then restore those rows without reintroducing hallucinated values.
3. Add a higher-level regression check that asserts the final report equals:
   - deterministic template
   - plus Gemini summary sidecar
   - with no table drift
4. Optionally tighten the Gemini summary prompt if the prose should be shorter or more debug-oriented.

## Useful live-check fields
- `meta.source_status`
- `meta.provider_by_field`
- `meta.index_status`
- `meta.request_debug`

## Useful output files
- `out.json`
- `payloads/<date>.json`
- `reports/morning-<date>.template.md`
- `reports/morning-<date>.summaries.json`
- `reports/morning-<date>.md`
