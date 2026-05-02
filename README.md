# FinMind Gemini Market Morning Report

This repo builds the structured JSON payload and deterministic HTML used by the morning market report flow. The report-facing structure stays stable while provider-specific fallback and diagnostics live under `meta`.

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

- `FINMIND_API_TOKEN`
- `FMP_API_KEY` for U.S. indices
- `EODHD_API_KEY` for Asia indices
- `MARKETAUX_API_KEY`
- `SUMMARY_PROVIDER` optional summary provider default: `openai`; allowed values are `openai`, `gemini`, `grok`, or `none`
- `OPENAI_API_KEY` and optional `OPENAI_MODEL` for OpenAI summaries. Default model: `gpt-5.4-nano`
- `GEMINI_API_KEY` and optional `GEMINI_MODEL` for Gemini summaries. Default model: `gemini-2.5-flash`
- `XAI_API_KEY` and optional `XAI_MODEL` for Grok summaries. Default model: `grok-4-1-fast`

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

## Morning report generation

Run the Python report generator with a date:

```cmd
python scripts\generate_morning_report.py --date 2026-04-22
```

If `--date` is omitted, the generator uses yesterday in Taiwan time:

```cmd
python scripts\generate_morning_report.py
```

The generator fetches live data by default. To make the mode explicit:

```cmd
python scripts\generate_morning_report.py --live
```

For deterministic local output from the checked-in mock payload:

```cmd
python scripts\generate_morning_report.py --mock
```

For command shortcuts and quick backfills, the date can also be passed positionally:

```cmd
python scripts\generate_morning_report.py 2026-04-22
```

Or render from an existing payload without refetching:

```cmd
python scripts\generate_morning_report.py --date 2026-04-22 --input payloads\2026-04-22.json
```

To refetch the default dated payload even if `payloads\<date>.json` already exists:

```cmd
python scripts\generate_morning_report.py --date 2026-04-22 --refresh
```

By default, the generator prints concise status lines to stderr and writes the report to disk. To also print the final HTML to stdout:

```cmd
python scripts\generate_morning_report.py --date 2026-04-22 --print-report
```

LLM summaries are optional. The default provider is `openai`. If the selected provider key is missing or the selected API fails, the generator still writes the deterministic HTML table/news report, omits the top summary content, and prints a warning status line. To disable summaries explicitly:

```cmd
python scripts\generate_morning_report.py --date 2026-04-22 --summary-provider none
```

To choose a summary provider for one run:

```cmd
python scripts\generate_morning_report.py --date 2026-04-22 --summary-provider openai
```

```cmd
python scripts\generate_morning_report.py --date 2026-04-22 --summary-provider gemini --summary-model gemini-2.0-flash
```

```cmd
python scripts\generate_morning_report.py --date 2026-04-22 --summary-provider grok --summary-model grok-4-1-fast
```

The generator will:

- run `python scripts\generate_morning_report.py` with Taiwan-yesterday as the default date and live data as the default mode, or use `--mock` for deterministic fixture data
- fetch and archive the payload to `payloads/<date>.json`
- refetch an existing dated payload when `--refresh` is passed
- render the deterministic HTML template to `reports/morning-<date>.template.html`
- show each table heading's underlying data date, so stale or mixed-date inputs are visible in the report
- call the selected LLM and merge `reports/morning-<date>.summaries.json` into the top summary card, or omit summary text when `--summary-provider none` is used or summaries are unavailable
- print provider status, missing-section status, summary status, output paths, and final result to stderr

`MORNING_REPORT_USE_LIVE=1` is deprecated. Direct calls to `scripts\fetch_morning_data.py` still accept it as a temporary fallback, but new commands should use `--live` or `--mock`.

## GitHub Pages publishing

The repository can publish the latest morning report through GitHub Pages from the `main` branch root.

Configure GitHub Pages:

1. Open the repository Settings.
2. Go to Pages.
3. Set Source to `Deploy from a branch`.
4. Set Branch to `main` and Folder to `/root`.

The scheduled workflow is `.github/workflows/daily_report.yml`. It runs at `00:30 UTC` Tuesday-Saturday, which is `08:30 Asia/Taipei`, and publishes the prior Taiwan calendar day because the generator defaults to Taiwan-yesterday.

Required repository secrets:

- `FINMIND_API_TOKEN`
- `FMP_API_KEY`
- `EODHD_API_KEY`
- `MARKETAUX_API_KEY`
- `OPENAI_API_KEY`

Optional repository secret:

- `OPENAI_MODEL`

To publish locally with live data:

```cmd
python scripts\publish_daily_page.py --live --summary-provider openai
```

To publish a deterministic mock report for a fixed date:

```cmd
python scripts\publish_daily_page.py --date 2026-05-01 --mock --summary-provider none
```

The publish wrapper writes:

- `index.html` for the latest GitHub Pages report
- `archive/YYYY-MM-DD.html` for the dated report
- `archive/index.html` for newest-first archive links
- `payloads/YYYY-MM-DD.json` and `reports/morning-YYYY-MM-DD.*` audit artifacts
