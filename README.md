# FinMind Gemini Market Morning Report

This repo builds the structured JSON payload used by the Gemini CLI morning market report flow. The report-facing structure stays stable while provider-specific fallback and diagnostics live under `meta`.

## Maintenance rules

When changing project behavior, keep the repo docs and sample artifacts in sync:

- Update `CHANGELOG.md` for any behavior, provider, schema, or workflow change.
- Update this `README.md` when adding or changing environment variables, data sources, or fallback behavior.
- Refresh checked-in sample payloads and reports when rendered output or canonical units change.
- Update or add tests for any provider mapping, normalization, or report-shape change.

## Live data sources

- FinMind: Taiwan market, institutional flows, USD/TWD, gold, WTI crude
- TPEx official dataset: `taiwan_market.tpex_index`
  - Source: `https://www.tpex.org.tw/www/zh-tw/indexInfo/inx?response=data`
  - This is the plain TPEx OTC price index dataset exposed through TPEx / data.gov.tw, not the FinMind total-return series
- FMP: primary provider for U.S. indices
- EODHD: primary provider for Asia indices (`N225`, `HSI`, `KS11`)
- U.S. Treasury: 2Y / 10Y / 30Y yields and 10Y-2Y slope
- Marketaux: financial news

## Environment variables

- `MORNING_REPORT_USE_LIVE=1` to enable live mode
- `FINMIND_API_TOKEN`
- `FMP_API_KEY` for U.S. indices
- `EODHD_API_KEY` for Asia indices
- `MARKETAUX_API_KEY`

## Symbol mapping

Runtime index resolution uses [scripts/symbol_map.json](/D:/finmind-gemini/scripts/symbol_map.json). The morning fetcher does not search for symbols live.

U.S. index symbols are fixed FMP mappings. Asia index symbols are fixed EODHD mappings.

## Fallback behavior

For each logical index series:

U.S. indices:

1. FMP quote endpoint
2. FMP historical EOD endpoint

Asia indices:

1. EODHD historical EOD endpoint

If one symbol fails, the rest of the report continues. Missing symbols are reported under `meta.index_status`, `meta.missing_sections`, and `meta.request_debug`.

## Troubleshooting

`taiwan_market.tpex_index` is still `null`:
- Check `meta.source_status.tpex`
- Check `meta.request_debug` for the TPEx request and parser notes
- The TPEx endpoint currently needs a TPEx-specific relaxed TLS context on this machine because the server certificate chain fails Python's default verification

FMP U.S. index failure:
- Inspect `meta.source_status.fmp`
- Inspect `meta.index_status.us_market`
- Inspect `meta.request_debug` for the selected `^`-prefixed index symbol and response excerpt

Asia index failure:
- Inspect `meta.index_status.asia_market`
- Inspect `meta.source_status.asia_indices`
- Inspect `meta.request_debug` for the selected EODHD symbol and response excerpt
- If `EODHD_API_KEY` is unset or a symbol mapping is wrong, those rows will degrade to `鞈??怎撩`

Payload archive:
- The fetcher writes the working payload to `out.json`
- It also archives a dated copy to `payloads/<date>.json`

## Gemini command

Run the Gemini command with a date:

```text
/market:morning 2026-04-22
```

The command will:

- run `python scripts\generate_morning_report.py --date <date>`
- fetch and archive the payload to `payloads/<date>.json`
- render the deterministic template to `reports/morning-<date>.template.md`
- ask headless Gemini to return summary text only
- save the structured Gemini summary payload to `reports/morning-<date>.summaries.json`
- merge those summaries into the template locally in Python and write the final markdown to `reports/morning-<date>.md`
