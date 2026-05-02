import json
import unittest
from io import BytesIO, StringIO
from pathlib import Path
from unittest import mock

from scripts import fetch_morning_data as fmd
from scripts import generate_morning_report as gmr
from scripts import render_morning_report as rmr


FIXTURES = Path(__file__).parent / "fixtures"


def load_json_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def load_text_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def sample_summaries() -> dict:
    return {
        "market_summary": [
            {
                "heading": "美股方面",
                "bullets": [
                    "S&P 500 收在 5246.11，上漲 0.79%。",
                    "Dow Jones 上漲 0.53%，收在 38995.24。",
                ],
            },
            {
                "heading": "台股方面",
                "bullets": [
                    "上市指數收在 20482.12。",
                    "三大法人合計買超 195.6 億元。",
                ],
            },
        ],
    }


class FakeGeminiResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload, ensure_ascii=False).encode("utf-8")


class MorningDataTests(unittest.TestCase):
    def test_print_payload_writes_utf8_bytes(self) -> None:
        fake_stdout = mock.Mock()
        fake_stdout.buffer = BytesIO()
        payload = {"news": [{"summary": "contains\u00a0nbsp"}]}

        with mock.patch.object(fmd.sys, "stdout", fake_stdout):
            fmd.print_payload(payload)

        self.assertIn("contains\u00a0nbsp".encode("utf-8"), fake_stdout.buffer.getvalue())

    def test_normalize_tpex_index_computes_change_percent(self) -> None:
        current = {"收市": "383.50", "漲跌": "11.16"}
        previous = {"收市": "372.34"}
        result = fmd.normalize_tpex_index(current, previous)
        self.assertEqual(result["symbol"], "TPEX")
        self.assertEqual(result["name"], "TPEx OTC Index")
        self.assertEqual(result["close"], 383.5)
        self.assertEqual(result["change"], 11.16)
        self.assertAlmostEqual(result["change_percent"], 2.9973, places=4)

    def test_normalize_fmp_from_quote(self) -> None:
        metadata = {"name": "S&P 500"}
        raw = load_json_fixture("fmp_quote.json")
        result = fmd.normalize_index_series_from_fmp("SPX", metadata, quote_raw=raw)
        self.assertEqual(result["close"], 5246.11)
        self.assertEqual(result["change"], 41.28)
        self.assertEqual(result["change_percent"], 0.79)

    def test_normalize_fmp_from_historical(self) -> None:
        metadata = {"name": "S&P 500"}
        raw = load_json_fixture("fmp_historical.json")
        result = fmd.normalize_index_series_from_fmp("SPX", metadata, historical_raw=raw["historical"])
        self.assertEqual(result["close"], 5246.11)
        self.assertEqual(result["change"], 41.28)
        self.assertEqual(result["change_percent"], 0.7931)

    def test_normalize_eodhd_uses_latest_row_on_or_before_report_date(self) -> None:
        metadata = {"name": "Nikkei 225"}
        raw = load_json_fixture("eodhd_historical.json")
        result = fmd.normalize_index_series_from_eodhd("N225", metadata, raw, "2026-04-21")
        self.assertEqual(result["symbol"], "N225")
        self.assertEqual(result["date"], "2026-04-21")
        self.assertEqual(result["close"], 35907.29)
        self.assertEqual(result["change"], -145.27)
        self.assertAlmostEqual(result["change_percent"], -0.4029, places=4)

    def test_normalize_eodhd_raises_when_close_is_missing(self) -> None:
        metadata = {"name": "Nikkei 225"}
        with self.assertRaisesRegex(RuntimeError, "missing close"):
            fmd.normalize_index_series_from_eodhd(
                "N225",
                metadata,
                [{"date": "2026-04-21"}, {"date": "2026-04-18", "close": 36052.56}],
                "2026-04-21",
            )

    def test_fetch_tpex_index_block_uses_fixture_shape(self) -> None:
        payload = fmd.build_base_payload("2026-04-20")
        fixture_csv = load_text_fixture("tpex_index.csv")
        with mock.patch.object(
            fmd,
            "fetch_text",
            return_value=(fixture_csv, {"url": fmd.TPEX_INDEX_URL, "status_code": 200, "response_excerpt": fixture_csv}),
        ):
            result = fmd.fetch_tpex_index_block("2026-04-20", payload)
        self.assertEqual(result["date"], "2026-04-20")
        self.assertEqual(result["close"], 383.5)
        self.assertEqual(result["change"], 11.16)
        self.assertTrue(payload["meta"]["request_debug"])

    def test_fetch_us_index_series_falls_back_to_fmp_history(self) -> None:
        payload = fmd.build_base_payload("2026-04-20")
        metadata = {
            "symbol": "^GSPC",
            "name": "S&P 500",
            "provider_symbols": {"fmp": "^GSPC"},
        }
        with mock.patch.dict("os.environ", {"FMP_API_KEY": "fmp"}, clear=False):
            with mock.patch.object(fmd, "fetch_fmp_quote", side_effect=RuntimeError("quote blocked")), \
                 mock.patch.object(
                     fmd,
                     "fetch_fmp_historical_eod_full",
                     return_value=(
                         load_json_fixture("fmp_historical.json")["historical"],
                         {"url": "https://example.com", "status_code": 200, "response_excerpt": "ok"},
                     ),
                 ):
                result, provider, fallback_used, selected_symbol = fmd.fetch_us_index_series("SPX", metadata, payload)
        self.assertIsNotNone(result)
        self.assertEqual(provider, "fmp")
        self.assertTrue(fallback_used)
        self.assertEqual(selected_symbol, "^GSPC")

    def test_fetch_asia_index_series_uses_eodhd_history(self) -> None:
        payload = fmd.build_base_payload("2026-04-21")
        metadata = {
            "symbol": "N225",
            "name": "Nikkei 225",
            "provider_symbols": {"eodhd": "N225.INDX"},
        }
        with mock.patch.dict("os.environ", {"EODHD_API_KEY": "eodhd"}, clear=False), mock.patch.object(
            fmd,
            "fetch_json",
            return_value=(
                load_json_fixture("eodhd_historical.json"),
                {"url": "https://eodhd.example/N225.INDX", "status_code": 200, "response_excerpt": "ok"},
            ),
        ):
            result, provider, fallback_used, selected_symbol = fmd.fetch_asia_index_series("N225", metadata, payload)
        self.assertIsNotNone(result)
        self.assertEqual(provider, "eodhd")
        self.assertFalse(fallback_used)
        self.assertEqual(selected_symbol, "N225.INDX")
        self.assertEqual(result["date"], "2026-04-21")
        self.assertEqual(payload["meta"]["request_debug"][0]["provider"], "eodhd")

    def test_fetch_index_block_allows_partial_success(self) -> None:
        payload = fmd.build_base_payload("2026-04-20")
        symbol_map = {
            "us_market": {
                "SPX": {"symbol": "GSPC", "name": "S&P 500"},
                "IXIC": {"symbol": "IXIC", "name": "Nasdaq Composite"},
            },
            "asia_market": {
                "N225": {"symbol": "N225", "name": "Nikkei 225", "provider_symbols": {"eodhd": "N225.INDX"}},
            },
        }
        with mock.patch.object(fmd, "load_symbol_map", return_value=symbol_map), \
            mock.patch.object(
                fmd,
                "fetch_us_index_series",
                side_effect=[
                    ({"symbol": "SPX", "name": "S&P 500", "close": 1.0, "change": 0.1, "change_percent": 1.0}, "fmp", False, "^GSPC"),
                    (None, None, False, "quote failed"),
                ],
            ), \
             mock.patch.object(
                 fmd,
                 "fetch_asia_index_series",
                 side_effect=[
                     ({"symbol": "N225", "name": "Nikkei 225", "close": 10.0, "change": 0.2, "change_percent": 2.0, "date": "2026-04-20"}, "eodhd", False, "N225.INDX"),
                 ],
             ), \
             mock.patch.object(fmd, "ASIA_INDEX_METADATA", {"N225": {"name": "Nikkei 225"}}):
            us_market, asia_market = fmd.fetch_index_block("2026-04-20", payload)
        self.assertEqual(len(us_market["indices"]), 1)
        self.assertEqual(len(asia_market["indices"]), 1)
        self.assertIn("us_market.indices.IXIC", payload["meta"]["missing_sections"])
        self.assertEqual(payload["meta"]["index_status"]["us_market"]["IXIC"]["status"], "error")
        self.assertEqual(payload["meta"]["provider_by_field"]["us_market.indices.SPX"], "fmp")
        self.assertEqual(payload["meta"]["provider_by_field"]["asia_market.indices.N225"], "eodhd")

    def test_fetch_index_block_marks_asia_error_when_eodhd_key_missing(self) -> None:
        payload = fmd.build_base_payload("2026-04-20")
        symbol_map = {"us_market": {}}
        with mock.patch.object(fmd, "load_symbol_map", return_value=symbol_map), \
             mock.patch.dict("os.environ", {}, clear=True):
            us_market, asia_market = fmd.fetch_index_block("2026-04-20", payload)
        self.assertEqual(us_market["indices"], [])
        self.assertEqual(asia_market["indices"], [])
        self.assertEqual(payload["meta"]["index_status"]["asia_market"]["N225"]["status"], "error")
        self.assertIn("asia_market.indices.N225", payload["meta"]["missing_sections"])
        self.assertEqual(payload["meta"]["index_status"]["asia_market"]["N225"]["message"], "EODHD_API_KEY is not set")

    def test_finalize_source_status_reflects_eodhd_results(self) -> None:
        payload = fmd.build_base_payload("2026-04-20")
        payload["meta"]["index_status"]["asia_market"] = {
            "N225": {"status": "ok"},
            "HSI": {"status": "error"},
            "KS11": {"status": "ok"},
        }
        fmd.finalize_source_status(payload)
        self.assertEqual(payload["meta"]["source_status"]["asia_indices"]["status"], "partial")
        self.assertEqual(payload["meta"]["source_status"]["asia_indices"]["message"], "Resolved 2/3 Asia indices through EODHD")

    def test_fetcher_resolve_fetch_mode_prefers_cli_flags(self) -> None:
        with mock.patch.dict("os.environ", {"MORNING_REPORT_USE_LIVE": "1"}, clear=True), \
             mock.patch("sys.stderr", new=StringIO()) as stderr:
            self.assertEqual(fmd.resolve_fetch_mode(mock.Mock(live=False, mock=True)), "mock")

        self.assertEqual(stderr.getvalue(), "")
        self.assertEqual(fmd.resolve_fetch_mode(mock.Mock(live=True, mock=False)), "live")

    def test_fetcher_resolve_fetch_mode_keeps_deprecated_env_fallback(self) -> None:
        with mock.patch.dict("os.environ", {"MORNING_REPORT_USE_LIVE": "1"}, clear=True), \
             mock.patch("sys.stderr", new=StringIO()) as stderr:
            self.assertEqual(fmd.resolve_fetch_mode(mock.Mock(live=False, mock=False)), "live")

        self.assertIn("MORNING_REPORT_USE_LIVE is deprecated", stderr.getvalue())

    def test_fetcher_resolve_fetch_mode_defaults_to_mock_when_called_directly(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertEqual(fmd.resolve_fetch_mode(mock.Mock(live=False, mock=False)), "mock")

    def test_finalize_payload_meta_preserves_top_level_shape(self) -> None:
        payload = fmd.build_base_payload("2026-04-20")
        payload["us_market"]["indices"].append(
            {"symbol": "SPX", "name": "S&P 500", "close": 1.0, "change": 0.1, "change_percent": 1.0}
        )
        fmd.finalize_payload_meta(payload)
        expected_keys = {
            "report_date",
            "taiwan_market",
            "taiwan_institutional",
            "us_market",
            "fx",
            "asia_market",
            "commodities",
            "bonds",
            "news",
            "meta",
        }
        self.assertEqual(set(payload.keys()), expected_keys)
        self.assertIn("sources_used", payload["meta"])
        self.assertIn("request_debug", payload["meta"])
        self.assertTrue(payload["meta"]["partial_success"])
        self.assertIn("taiwan_market.tpex_index", payload["meta"]["missing_sections"])

    def test_render_report_from_mock_payload_uses_expected_values(self) -> None:
        payload = json.loads((Path(__file__).parent.parent / "data" / "raw" / "mock_morning.json").read_text(encoding="utf-8-sig"))
        report_html = rmr.render_report(payload)
        self.assertIn("<!doctype html>", report_html)
        self.assertIn("<h1>市場晨報（2026-04-14）</h1>", report_html)
        self.assertIn("<td>S&amp;P 500</td>", report_html)
        self.assertIn("<td>5246.11</td>", report_html)
        self.assertIn('<td class="num up"><span class="move-icon" aria-hidden="true">▲</span><span>41.28</span></td>', report_html)
        self.assertIn("<td>上市指數</td>", report_html)
        self.assertIn("新台幣億元", report_html)
        self.assertIn("<h3>美股四大指數", report_html)
        self.assertIn("<h3>台股主要指數", report_html)
        self.assertIn("<h3>匯率", report_html)
        self.assertIn("<h3>亞股指數", report_html)
        self.assertIn("<h3>大宗商品", report_html)
        self.assertNotIn("market_breadth", report_html)
        self.assertIn("<td>2026-04-13</td>", report_html)
        self.assertIn("Fed officials signal patience", report_html)
        self.assertIn(rmr.MARKET_SUMMARY_PLACEHOLDER, report_html)
        self.assertNotIn("GEMINI_SECTION", report_html)

    def test_render_report_shows_missing_asia_rows(self) -> None:
        payload = fmd.build_base_payload("2026-04-21")
        payload["us_market"]["indices"] = [
            {"symbol": "SPX", "name": "S&P 500", "close": 7109.13, "change": -16.92, "change_percent": -0.2374},
        ]
        report_html = rmr.render_report(payload)
        self.assertIn("<td>N225</td>", report_html)
        self.assertIn("<td>HSI</td>", report_html)
        self.assertIn("<td>KS11</td>", report_html)
        self.assertIn('<td class="num missing"><span>資料暫缺</span></td>', report_html)

    def test_render_report_uses_bond_date_not_report_date(self) -> None:
        payload = fmd.build_base_payload("2026-04-21")
        payload["bonds"] = {
            "date": "2026-04-20",
            "yield_2y": 3.72,
            "yield_10y": 4.26,
            "yield_30y": 4.88,
            "slope_10y_2y": 0.54,
            "unit": "percent",
        }
        report_html = rmr.render_report(payload)
        self.assertIn("<td>2026-04-20</td>", report_html)
        self.assertIn("<span>3.72</span>", report_html)
        self.assertNotIn("<td>2026-04-21</td>", report_html)

    def test_render_report_adds_data_dates_to_table_headings(self) -> None:
        payload = json.loads((Path(__file__).parent.parent / "data" / "raw" / "mock_morning.json").read_text(encoding="utf-8-sig"))
        payload["news"][1]["date"] = "2026-04-13"
        report_html = rmr.render_report(payload)

        self.assertIn('<span class="data-date">資料日：2026-04-13</span>', report_html)
        self.assertIn('<span class="data-date">資料日：2026-04-14</span>', report_html)
        self.assertIn('<span class="data-date">資料日：多日期：2026-04-13, 2026-04-14</span>', report_html)

    def test_render_script_writes_utf8_sig_html(self) -> None:
        payload = json.loads((Path(__file__).parent.parent / "data" / "raw" / "mock_morning.json").read_text(encoding="utf-8-sig"))
        report_html = rmr.render_report(payload)
        output_path = Path(__file__).parent / "render_test_output.html"
        try:
            output_path.write_text(report_html, encoding="utf-8-sig")
            self.assertEqual(report_html, output_path.read_text(encoding="utf-8-sig"))
        finally:
            if output_path.exists():
                output_path.unlink()

    def test_save_payload_artifacts_writes_out_and_archive(self) -> None:
        payload = fmd.build_base_payload("2026-04-20")
        out_path = Path("out.json")
        archive_path = Path("payloads/2026-04-20.json")
        try:
            fmd.save_payload_artifacts("2026-04-20", payload)
            self.assertTrue(out_path.exists())
            self.assertTrue(archive_path.exists())
            self.assertEqual(
                json.loads(out_path.read_text(encoding="utf-8-sig")),
                json.loads(archive_path.read_text(encoding="utf-8-sig")),
            )
        finally:
            if out_path.exists():
                out_path.unlink()
            if archive_path.exists():
                archive_path.unlink()
            archive_dir = archive_path.parent
            if archive_dir.exists() and not any(archive_dir.iterdir()):
                archive_dir.rmdir()

    def test_fetch_marketaux_block_includes_url(self) -> None:
        raw = {
            "data": [
                {
                    "published_at": "2026-04-14T01:02:03Z",
                    "source": "Example News",
                    "title": "Title",
                    "description": "Desc",
                    "url": "https://example.com/story",
                }
            ]
        }
        with mock.patch.dict("os.environ", {"MARKETAUX_API_KEY": "key"}, clear=False), mock.patch.object(
            fmd, "fetch_json", return_value=(raw, {})
        ):
            items = fmd.fetch_marketaux_block("2026-04-14")
        self.assertEqual(items[0]["url"], "https://example.com/story")

    def test_render_news_title_falls_back_to_plain_text_without_url(self) -> None:
        payload = fmd.build_base_payload("2026-04-21")
        payload["news"] = [
            {"date": "2026-04-21", "source": "Example", "title": "No Link Title", "summary": "Summary", "url": None}
        ]
        report_html = rmr.render_report(payload)
        self.assertIn("<td>2026-04-21</td>", report_html)
        self.assertIn("<td>Example</td>", report_html)
        self.assertIn("<td>No Link Title</td>", report_html)
        self.assertNotIn("<a href", report_html)

    def test_apply_summaries_replaces_top_summary_placeholder_and_escapes_text(self) -> None:
        payload = json.loads((Path(__file__).parent.parent / "data" / "raw" / "mock_morning.json").read_text(encoding="utf-8-sig"))
        report_html = rmr.render_report(payload)
        summaries = {
            "market_summary": [
                {
                    "heading": "美股 <script>",
                    "bullets": ["S&P 500 上漲 0.79% <b>"],
                }
            ],
        }
        final_html = rmr.apply_summaries(report_html, summaries)
        self.assertIn("美股 &lt;script&gt;", final_html)
        self.assertIn('S&amp;P 500 <span class="summary-move up"><span class="move-icon" aria-hidden="true">▲</span>上漲 0.79%</span> &lt;b&gt;', final_html)
        self.assertNotIn(rmr.MARKET_SUMMARY_PLACEHOLDER, final_html)
        self.assertLess(final_html.index("S&amp;P 500 "), final_html.index("1. 美股與台股盤勢"))

    def test_market_summary_styles_movement_phrases(self) -> None:
        html = rmr.render_market_summary([
            {
                "heading": "markets",
                "bullets": [
                    "S&P 500 \u4e0a\u6f32 21.09\uff08+0.2926%\uff09, HSI \u4e0b\u8dcc -335.31\uff08-1.2841%\uff09, flat \u6301\u5e73 0.00%",
                    "yield \u4e0a\u5347 3.88\uff08\u25b23.88\uff09",
                    "unsafe <b> remains escaped",
                ],
            }
        ])

        self.assertIn('<span class="summary-move up"><span class="move-icon" aria-hidden="true">▲</span>\u4e0a\u6f32 21.09\uff08+0.2926%\uff09</span>', html)
        self.assertIn('<span class="summary-move down"><span class="move-icon" aria-hidden="true">▼</span>\u4e0b\u8dcc -335.31\uff08-1.2841%\uff09</span>', html)
        self.assertIn('<span class="summary-move flat"><span class="move-icon" aria-hidden="true">▬</span>\u6301\u5e73 0.00%</span>', html)
        self.assertIn('<span class="summary-move up"><span class="move-icon" aria-hidden="true">▲</span>\u4e0a\u5347 3.88\uff08\u25b23.88\uff09</span>', html)
        self.assertIn("unsafe &lt;b&gt; remains escaped", html)

    def test_extract_json_object_accepts_plain_or_fenced_json(self) -> None:
        plain = '{"market_summary":[{"heading":"a","bullets":["x","y"]}]}'
        fenced = "```json\n" + plain + "\n```"
        self.assertEqual(gmr.extract_json_object(plain)["market_summary"][0]["heading"], "a")
        self.assertEqual(gmr.extract_json_object(fenced)["market_summary"][0]["bullets"], ["x", "y"])

    def test_parse_args_defaults_to_openai_summary_provider(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True), \
             mock.patch("sys.argv", ["generate_morning_report.py", "--date", "2026-04-21"]):
            args = gmr.parse_args()

        self.assertEqual(args.summary_provider, "openai")

    def test_parse_args_accepts_missing_date(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True), \
             mock.patch("sys.argv", ["generate_morning_report.py"]):
            args = gmr.parse_args()

        self.assertIsNone(args.date)
        self.assertIsNone(args.positional_date)
        self.assertFalse(args.live)
        self.assertFalse(args.mock)
        self.assertEqual(gmr.resolve_fetch_mode(args), "live")

    def test_parse_args_accepts_positional_date(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True), \
             mock.patch("sys.argv", ["generate_morning_report.py", "2026-04-21"]):
            args = gmr.parse_args()

        self.assertEqual(gmr.resolve_report_date(args), "2026-04-21")

    def test_resolve_report_date_rejects_positional_and_option_date(self) -> None:
        args = mock.Mock(date="2026-04-21", positional_date="2026-04-20")
        with self.assertRaisesRegex(RuntimeError, "Use either positional date or --date"):
            gmr.resolve_report_date(args)

    def test_default_report_date_uses_taiwan_yesterday(self) -> None:
        real_datetime = gmr.datetime
        fake_now = real_datetime(2026, 4, 29, 0, 30, tzinfo=gmr.TAIWAN_TIMEZONE)
        fake_datetime = mock.Mock(now=mock.Mock(return_value=fake_now))
        with mock.patch.object(gmr, "datetime", fake_datetime):
            self.assertEqual(gmr.default_report_date(), "2026-04-28")

        fake_datetime.now.assert_called_once_with(gmr.TAIWAN_TIMEZONE)

    def test_call_gemini_for_summaries_reads_rest_response(self) -> None:
        summaries = sample_summaries()
        response_payload = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": json.dumps(summaries, ensure_ascii=False)}
                        ]
                    }
                }
            ]
        }
        with mock.patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}, clear=True), \
             mock.patch.object(gmr.urllib.request, "urlopen", return_value=FakeGeminiResponse(response_payload)) as urlopen:
            result, raw_output = gmr.call_gemini_for_summaries("template markdown")

        self.assertEqual(result, summaries)
        self.assertIn("market_summary", raw_output)
        request = urlopen.call_args.args[0]
        self.assertEqual(request.headers["X-goog-api-key"], "test-key")
        self.assertIn(gmr.DEFAULT_GEMINI_MODEL, request.full_url)

    def test_call_gemini_for_summaries_requires_api_key(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "GEMINI_API_KEY is not set"):
                gmr.call_gemini_for_summaries("template markdown")

    def test_call_gemini_for_summaries_reports_http_error(self) -> None:
        error = gmr.urllib.error.HTTPError(
            url="https://example.com",
            code=400,
            msg="Bad Request",
            hdrs=None,
            fp=BytesIO(b'{"error":"bad request"}'),
        )
        with mock.patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}, clear=True), \
             mock.patch.object(gmr.urllib.request, "urlopen", side_effect=error):
            with self.assertRaisesRegex(RuntimeError, "Gemini API summary generation failed"):
                gmr.call_gemini_for_summaries("template markdown")

    def test_call_gemini_for_summaries_retries_retryable_http_error(self) -> None:
        summaries = sample_summaries()
        error = gmr.urllib.error.HTTPError(
            url="https://example.com",
            code=503,
            msg="Service Unavailable",
            hdrs=None,
            fp=BytesIO(b'{"error":{"status":"UNAVAILABLE"}}'),
        )
        response_payload = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": json.dumps(summaries, ensure_ascii=False)}
                        ]
                    }
                }
            ]
        }
        with mock.patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}, clear=True), \
             mock.patch.object(gmr.urllib.request, "urlopen", side_effect=[error, FakeGeminiResponse(response_payload)]) as urlopen, \
             mock.patch.object(gmr.time, "sleep") as sleep, \
             mock.patch("sys.stderr", new=StringIO()) as stderr:
            result, _ = gmr.call_gemini_for_summaries("template markdown")

        self.assertEqual(result, summaries)
        self.assertEqual(urlopen.call_count, 2)
        sleep.assert_called_once_with(gmr.GEMINI_RETRY_DELAYS_SECONDS[0])
        self.assertIn("gemini retry 2/4", stderr.getvalue())

    def test_call_openai_for_summaries_reads_responses_api_output(self) -> None:
        summaries = sample_summaries()
        response_payload = {"output_text": json.dumps(summaries, ensure_ascii=False)}
        with mock.patch.dict("os.environ", {"OPENAI_API_KEY": "test-openai-key"}, clear=True), \
             mock.patch.object(gmr.urllib.request, "urlopen", return_value=FakeGeminiResponse(response_payload)) as urlopen:
            result, raw_output = gmr.call_openai_for_summaries("template markdown")

        self.assertEqual(result, summaries)
        self.assertIn("market_summary", raw_output)
        request = urlopen.call_args.args[0]
        self.assertEqual(request.headers["Authorization"], "Bearer test-openai-key")
        self.assertEqual(request.full_url, gmr.OPENAI_API_URL)
        request_body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(request_body["model"], gmr.DEFAULT_OPENAI_MODEL)

    def test_call_openai_for_summaries_uses_default_model_when_env_is_blank(self) -> None:
        summaries = sample_summaries()
        response_payload = {"output_text": json.dumps(summaries, ensure_ascii=False)}
        with mock.patch.dict("os.environ", {"OPENAI_API_KEY": "test-openai-key", "OPENAI_MODEL": ""}, clear=True), \
             mock.patch.object(gmr.urllib.request, "urlopen", return_value=FakeGeminiResponse(response_payload)) as urlopen:
            result, _ = gmr.call_openai_for_summaries("template markdown")

        self.assertEqual(result, summaries)
        request = urlopen.call_args.args[0]
        request_body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(request_body["model"], gmr.DEFAULT_OPENAI_MODEL)

    def test_call_xai_for_summaries_reads_responses_api_output(self) -> None:
        summaries = sample_summaries()
        response_payload = {
            "output": [
                {
                    "content": [
                        {"type": "output_text", "text": json.dumps(summaries, ensure_ascii=False)}
                    ]
                }
            ]
        }
        with mock.patch.dict("os.environ", {"XAI_API_KEY": "test-xai-key"}, clear=True), \
             mock.patch.object(gmr.urllib.request, "urlopen", return_value=FakeGeminiResponse(response_payload)) as urlopen:
            result, raw_output = gmr.call_xai_for_summaries("template markdown")

        self.assertEqual(result, summaries)
        self.assertIn("market_summary", raw_output)
        request = urlopen.call_args.args[0]
        self.assertEqual(request.headers["Authorization"], "Bearer test-xai-key")
        self.assertEqual(request.full_url, gmr.XAI_API_URL)
        request_body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(request_body["model"], gmr.DEFAULT_XAI_MODEL)

    def test_cli_wrapper_reports_morning_report_error_without_traceback(self) -> None:
        with mock.patch.object(gmr, "main", side_effect=gmr.MorningReportError("boom")), \
             mock.patch("sys.stderr", new=StringIO()) as stderr:
            exit_code = gmr.cli_main()

        self.assertEqual(exit_code, 1)
        status = stderr.getvalue()
        self.assertIn("[morning-report] error: boom", status)
        self.assertIn("[morning-report] result: failed", status)

    def test_generate_report_uses_news_from_explicit_input_without_fetching(self) -> None:
        payload = fmd.build_base_payload("2026-04-21")
        payload["news"] = [
            {
                "date": "2026-04-21",
                "source": "Input Source",
                "title": "Input News Title",
                "summary": "Input news summary",
                "url": "https://example.com/input-news",
            }
        ]
        payload_path = Path(__file__).parent / "input_news_payload.json"
        output_path = Path(__file__).parent / "input_news_report.md"
        template_path = Path(__file__).parent / "input_news_report.template.md"
        summaries_path = Path(__file__).parent / "input_news_report.summaries.json"
        try:
            payload_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8-sig")
            with mock.patch.object(gmr, "parse_args", return_value=mock.Mock(
                date="2026-04-21",
                input_path=str(payload_path),
                output_path=str(output_path),
                template_output_path=str(template_path),
                summaries_output_path=str(summaries_path),
                print_report=False,
                refresh=False,
                live=False,
                mock=False,
                summary_provider="none",
                summary_model=None,
            )), mock.patch.object(gmr, "run_fetcher") as run_fetcher, \
                mock.patch.object(gmr, "call_gemini_for_summaries") as call_gemini, \
                mock.patch("sys.stdout", new=StringIO()) as stdout, \
                mock.patch("sys.stderr", new=StringIO()) as stderr:
                self.assertEqual(gmr.main(), 0)

            run_fetcher.assert_not_called()
            call_gemini.assert_not_called()
            self.assertEqual(stdout.getvalue(), "")
            self.assertIn("[morning-report] date: 2026-04-21", stderr.getvalue())
            self.assertIn("[morning-report] result: ok", stderr.getvalue())
            final_html = output_path.read_text(encoding="utf-8-sig")
            self.assertIn("<td>2026-04-21</td>", final_html)
            self.assertIn("<td>Input Source</td>", final_html)
            self.assertIn('href="https://example.com/input-news"', final_html)
            self.assertIn("Input news summary", final_html)
            self.assertNotIn("GEMINI_SECTION", final_html)
            self.assertIn("本次未產生 LLM 摘要", final_html)
        finally:
            for path in (payload_path, output_path, template_path, summaries_path):
                if path.exists():
                    path.unlink()

    def test_generate_report_without_date_uses_taiwan_yesterday_default_paths(self) -> None:
        payload = fmd.build_base_payload("2099-01-02")
        template_markdown = rmr.render_report(payload)
        with mock.patch.object(gmr, "parse_args", return_value=mock.Mock(
            date=None,
            positional_date=None,
            input_path=None,
            output_path=None,
            template_output_path=None,
            summaries_output_path=None,
            print_report=False,
            refresh=False,
            live=False,
            mock=False,
            summary_provider="none",
            summary_model=None,
        )), mock.patch.object(gmr, "default_report_date", return_value="2099-01-02"), \
            mock.patch.object(gmr, "run_fetcher") as run_fetcher, \
            mock.patch.object(gmr.rmr, "read_json_file", return_value=payload) as read_json_file, \
            mock.patch.object(gmr.rmr, "render_report", return_value=template_markdown), \
            mock.patch.object(gmr, "write_text") as write_text, \
            mock.patch("sys.stdout", new=StringIO()) as stdout, \
            mock.patch("sys.stderr", new=StringIO()) as stderr:
            self.assertEqual(gmr.main(), 0)

        run_fetcher.assert_called_once_with("2099-01-02", "live")
        read_json_file.assert_called_once_with(Path("payloads") / "2099-01-02.json")
        written_paths = [call.args[0] for call in write_text.call_args_list]
        self.assertIn(Path("reports") / "morning-2099-01-02.template.html", written_paths)
        self.assertIn(Path("reports") / "morning-2099-01-02.html", written_paths)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("[morning-report] date: 2099-01-02", stderr.getvalue())

    def test_generate_report_explicit_date_overrides_default_date(self) -> None:
        payload = fmd.build_base_payload("2026-04-21")
        template_markdown = rmr.render_report(payload)
        with mock.patch.object(gmr, "parse_args", return_value=mock.Mock(
            date="2026-04-21",
            positional_date=None,
            input_path=None,
            output_path=None,
            template_output_path=None,
            summaries_output_path=None,
            print_report=False,
            refresh=True,
            live=False,
            mock=False,
            summary_provider="none",
            summary_model=None,
        )), mock.patch.object(gmr, "default_report_date", return_value="2026-04-28") as default_report_date, \
            mock.patch.object(gmr, "run_fetcher") as run_fetcher, \
            mock.patch.object(gmr.rmr, "read_json_file", return_value=payload), \
            mock.patch.object(gmr.rmr, "render_report", return_value=template_markdown), \
            mock.patch.object(gmr, "write_text"), \
            mock.patch("sys.stdout", new=StringIO()), \
            mock.patch("sys.stderr", new=StringIO()):
            self.assertEqual(gmr.main(), 0)

        default_report_date.assert_not_called()
        run_fetcher.assert_called_once_with("2026-04-21", "live")

    def test_generate_report_mock_flag_passes_mock_to_fetcher(self) -> None:
        payload = fmd.build_base_payload("2026-04-21")
        template_markdown = rmr.render_report(payload)
        with mock.patch.object(gmr, "parse_args", return_value=mock.Mock(
            date="2026-04-21",
            positional_date=None,
            input_path=None,
            output_path=None,
            template_output_path=None,
            summaries_output_path=None,
            print_report=False,
            refresh=True,
            live=False,
            mock=True,
            summary_provider="none",
            summary_model=None,
        )), mock.patch.object(gmr, "run_fetcher") as run_fetcher, \
            mock.patch.object(gmr.rmr, "read_json_file", return_value=payload), \
            mock.patch.object(gmr.rmr, "render_report", return_value=template_markdown), \
            mock.patch.object(gmr, "write_text"), \
            mock.patch("sys.stdout", new=StringIO()), \
            mock.patch("sys.stderr", new=StringIO()):
            self.assertEqual(gmr.main(), 0)

        run_fetcher.assert_called_once_with("2026-04-21", "mock")

    def test_run_fetcher_reports_subprocess_stderr(self) -> None:
        error = gmr.subprocess.CalledProcessError(
            returncode=1,
            cmd=["python", "fetch"],
            stderr="fetcher boom",
        )
        with mock.patch.object(gmr.subprocess, "run", side_effect=error):
            with self.assertRaisesRegex(RuntimeError, "Fetcher failed: fetcher boom"):
                gmr.run_fetcher("2026-04-21", "live")

    def test_generate_report_fails_when_explicit_input_is_missing(self) -> None:
        missing_path = Path(__file__).parent / "missing_payload.json"
        with mock.patch.object(gmr, "parse_args", return_value=mock.Mock(
            date="2026-04-21",
            input_path=str(missing_path),
            output_path=None,
            template_output_path=None,
            summaries_output_path=None,
            print_report=False,
            refresh=False,
            live=False,
            mock=False,
            summary_provider="none",
            summary_model=None,
        )), mock.patch.object(gmr, "run_fetcher") as run_fetcher:
            with self.assertRaisesRegex(RuntimeError, "Input payload does not exist"):
                gmr.main()
        run_fetcher.assert_not_called()

    def test_generate_report_rejects_refresh_with_explicit_input(self) -> None:
        payload_path = Path(__file__).parent / "existing_payload.json"
        with mock.patch.object(gmr, "parse_args", return_value=mock.Mock(
            date="2026-04-21",
            input_path=str(payload_path),
            output_path=None,
            template_output_path=None,
            summaries_output_path=None,
            print_report=False,
            refresh=True,
            live=False,
            mock=False,
            summary_provider="none",
            summary_model=None,
        )), mock.patch.object(gmr, "run_fetcher") as run_fetcher:
            with self.assertRaisesRegex(RuntimeError, "--refresh cannot be used with --input"):
                gmr.main()
        run_fetcher.assert_not_called()

    def test_generate_report_refresh_refetches_existing_default_payload(self) -> None:
        payload = fmd.build_base_payload("2026-04-21")
        output_path = Path(__file__).parent / "refresh_report.md"
        template_path = Path(__file__).parent / "refresh_report.template.md"
        summaries_path = Path(__file__).parent / "refresh_report.summaries.json"
        with mock.patch.object(gmr, "parse_args", return_value=mock.Mock(
            date="2026-04-21",
            input_path=None,
            output_path=str(output_path),
            template_output_path=str(template_path),
            summaries_output_path=str(summaries_path),
            print_report=False,
            refresh=True,
            live=False,
            mock=False,
            summary_provider="none",
            summary_model=None,
        )), mock.patch.object(gmr, "run_fetcher") as run_fetcher, \
            mock.patch.object(gmr.rmr, "read_json_file", return_value=payload), \
            mock.patch.object(gmr, "call_gemini_for_summaries") as call_gemini, \
            mock.patch("sys.stdout", new=StringIO()) as stdout, \
            mock.patch("sys.stderr", new=StringIO()) as stderr:
            try:
                self.assertEqual(gmr.main(), 0)
            finally:
                for path in (output_path, template_path, summaries_path):
                    if path.exists():
                        path.unlink()

        run_fetcher.assert_called_once_with("2026-04-21", "live")
        call_gemini.assert_not_called()
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("refresh: refetching payloads\\2026-04-21.json", stderr.getvalue())

    def test_generate_report_writes_table_only_report_without_llm_summaries(self) -> None:
        payload = json.loads((Path(__file__).parent.parent / "data" / "raw" / "mock_morning.json").read_text(encoding="utf-8-sig"))
        payload_path = Path(__file__).parent / "mock_payload.json"
        output_path = Path(__file__).parent / "generated_report.md"
        template_path = Path(__file__).parent / "generated_report.template.md"
        summaries_path = Path(__file__).parent / "generated_report.summaries.json"
        summaries = {
            "section_1": "第一段摘要。",
            "section_2": "第二段摘要。",
            "section_3": "第三段摘要。",
            "section_4": "第四段摘要。",
            "section_5": "第五段摘要。",
            "section_6": "第六段摘要。",
            "final_summary": ["重點一", "重點二", "重點三"],
        }
        try:
            payload_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8-sig")
            with mock.patch.object(gmr, "parse_args", return_value=mock.Mock(
                date="2026-04-14",
                input_path=str(payload_path),
                output_path=str(output_path),
                template_output_path=str(template_path),
                summaries_output_path=str(summaries_path),
                print_report=False,
                refresh=False,
                live=False,
                mock=False,
                summary_provider="none",
                summary_model=None,
            )), mock.patch.object(gmr, "call_gemini_for_summaries") as call_gemini, \
                mock.patch("sys.stdout", new=StringIO()) as stdout, \
                mock.patch("sys.stderr", new=StringIO()) as stderr:
                self.assertEqual(gmr.main(), 0)

            call_gemini.assert_not_called()
            self.assertEqual(stdout.getvalue(), "")
            self.assertIn("[morning-report] summaries: disabled", stderr.getvalue())
            final_html = output_path.read_text(encoding="utf-8-sig")
            self.assertNotIn("GEMINI_SECTION", final_html)
            self.assertNotIn("GEMINI_FINAL", final_html)
            self.assertIn("本次未產生 LLM 摘要", final_html)
            self.assertFalse(summaries_path.exists())
            self.assertTrue(template_path.exists())
        finally:
            for path in (payload_path, output_path, template_path, summaries_path):
                if path.exists():
                    path.unlink()

    def test_generate_report_print_report_writes_html_to_stdout(self) -> None:
        payload = json.loads((Path(__file__).parent.parent / "data" / "raw" / "mock_morning.json").read_text(encoding="utf-8-sig"))
        payload_path = Path(__file__).parent / "print_report_payload.json"
        output_path = Path(__file__).parent / "print_report.md"
        template_path = Path(__file__).parent / "print_report.template.md"
        summaries_path = Path(__file__).parent / "print_report.summaries.json"
        try:
            payload_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8-sig")
            with mock.patch.object(gmr, "parse_args", return_value=mock.Mock(
                date="2026-04-14",
                input_path=str(payload_path),
                output_path=str(output_path),
                template_output_path=str(template_path),
                summaries_output_path=str(summaries_path),
                print_report=True,
                refresh=False,
                live=False,
                mock=False,
                summary_provider="none",
                summary_model=None,
            )), mock.patch.object(gmr, "call_gemini_for_summaries", return_value=(sample_summaries(), "{}")), \
                mock.patch("sys.stdout", new=StringIO()) as stdout, \
                mock.patch("sys.stderr", new=StringIO()) as stderr:
                self.assertEqual(gmr.main(), 0)

            self.assertIn("<!doctype html>", stdout.getvalue())
            self.assertIn("<h1>市場晨報", stdout.getvalue())
            self.assertNotIn("S&amp;P 500 收在 5246.11", stdout.getvalue())
            self.assertNotIn("GEMINI_SECTION", stdout.getvalue())
            self.assertIn("本次未產生 LLM 摘要", stdout.getvalue())
            self.assertIn("[morning-report] result:", stderr.getvalue())
        finally:
            for path in (payload_path, output_path, template_path, summaries_path):
                if path.exists():
                    path.unlink()

    def test_generate_report_can_use_openai_summary_provider(self) -> None:
        payload = json.loads((Path(__file__).parent.parent / "data" / "raw" / "mock_morning.json").read_text(encoding="utf-8-sig"))
        payload_path = Path(__file__).parent / "openai_payload.json"
        output_path = Path(__file__).parent / "openai_report.md"
        template_path = Path(__file__).parent / "openai_report.template.md"
        summaries_path = Path(__file__).parent / "openai_report.summaries.json"
        try:
            payload_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8-sig")
            with mock.patch.object(gmr, "parse_args", return_value=mock.Mock(
                date="2026-04-14",
                input_path=str(payload_path),
                output_path=str(output_path),
                template_output_path=str(template_path),
                summaries_output_path=str(summaries_path),
                print_report=False,
                refresh=False,
                live=False,
                mock=False,
                summary_provider="openai",
                summary_model="paid-model",
            )), mock.patch.object(gmr, "call_openai_for_summaries", return_value=(sample_summaries(), "{}")) as call_openai, \
                mock.patch("sys.stdout", new=StringIO()) as stdout, \
                mock.patch("sys.stderr", new=StringIO()) as stderr:
                self.assertEqual(gmr.main(), 0)

            call_openai.assert_called_once()
            self.assertEqual(call_openai.call_args.args[1], "paid-model")
            self.assertEqual(stdout.getvalue(), "")
            self.assertIn("[morning-report] summaries: ok (openai)", stderr.getvalue())
            final_html = output_path.read_text(encoding="utf-8-sig")
            self.assertIn("S&amp;P 500 收在 5246.11，", final_html)
            self.assertIn('class="summary-move up"', final_html)
            self.assertIn("<h3>1. 美股方面</h3>", final_html)
            self.assertTrue(summaries_path.exists())
        finally:
            for path in (payload_path, output_path, template_path, summaries_path):
                if path.exists():
                    path.unlink()

    def test_generate_report_missing_provider_key_falls_back_to_table_only(self) -> None:
        payload = json.loads((Path(__file__).parent.parent / "data" / "raw" / "mock_morning.json").read_text(encoding="utf-8-sig"))
        providers = ("openai", "gemini", "grok")
        for provider in providers:
            with self.subTest(provider=provider):
                payload_path = Path(__file__).parent / f"{provider}_missing_key_payload.json"
                output_path = Path(__file__).parent / f"{provider}_missing_key_report.md"
                template_path = Path(__file__).parent / f"{provider}_missing_key_report.template.md"
                summaries_path = Path(__file__).parent / f"{provider}_missing_key_report.summaries.json"
                try:
                    payload_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8-sig")
                    with mock.patch.dict("os.environ", {}, clear=True), \
                         mock.patch.object(gmr, "parse_args", return_value=mock.Mock(
                            date="2026-04-14",
                            input_path=str(payload_path),
                            output_path=str(output_path),
                            template_output_path=str(template_path),
                            summaries_output_path=str(summaries_path),
                            print_report=False,
                            refresh=False,
                            live=False,
                            mock=False,
                            summary_provider=provider,
                            summary_model=None,
                         )), mock.patch("sys.stdout", new=StringIO()) as stdout, \
                         mock.patch("sys.stderr", new=StringIO()) as stderr:
                        self.assertEqual(gmr.main(), 0)

                    self.assertEqual(stdout.getvalue(), "")
                    self.assertIn(f"[morning-report] summaries: skipped ({provider}, missing key)", stderr.getvalue())
                    final_html = output_path.read_text(encoding="utf-8-sig")
                    self.assertNotIn("GEMINI_SECTION", final_html)
                    self.assertIn("本次未產生 LLM 摘要", final_html)
                    self.assertFalse(summaries_path.exists())
                finally:
                    for path in (payload_path, output_path, template_path, summaries_path):
                        if path.exists():
                            path.unlink()

    def test_generate_report_provider_failure_falls_back_to_table_only(self) -> None:
        payload = json.loads((Path(__file__).parent.parent / "data" / "raw" / "mock_morning.json").read_text(encoding="utf-8-sig"))
        payload_path = Path(__file__).parent / "openai_failure_payload.json"
        output_path = Path(__file__).parent / "openai_failure_report.md"
        template_path = Path(__file__).parent / "openai_failure_report.template.md"
        summaries_path = Path(__file__).parent / "openai_failure_report.summaries.json"
        try:
            payload_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8-sig")
            with mock.patch.object(gmr, "parse_args", return_value=mock.Mock(
                date="2026-04-14",
                input_path=str(payload_path),
                output_path=str(output_path),
                template_output_path=str(template_path),
                summaries_output_path=str(summaries_path),
                print_report=False,
                refresh=False,
                live=False,
                mock=False,
                summary_provider="openai",
                summary_model=None,
            )), mock.patch.object(gmr, "call_openai_for_summaries", side_effect=gmr.SummaryUnavailable("OpenAI summary generation failed: overload")), \
                mock.patch("sys.stdout", new=StringIO()) as stdout, \
                mock.patch("sys.stderr", new=StringIO()) as stderr:
                self.assertEqual(gmr.main(), 0)

            self.assertEqual(stdout.getvalue(), "")
            self.assertIn("[morning-report] summaries: failed (openai) - OpenAI summary generation failed: overload", stderr.getvalue())
            final_html = output_path.read_text(encoding="utf-8-sig")
            self.assertNotIn("S&amp;P 500 收在 5246.11", final_html)
            self.assertNotIn("GEMINI_SECTION", final_html)
            self.assertIn("本次未產生 LLM 摘要", final_html)
            self.assertFalse(summaries_path.exists())
        finally:
            for path in (payload_path, output_path, template_path, summaries_path):
                if path.exists():
                    path.unlink()

    def test_generate_report_status_uses_payload_source_status(self) -> None:
        payload = fmd.build_base_payload("2026-04-21")
        payload["meta"]["source_status"] = {
            "finmind": {"status": "ok", "message": "Fetched successfully"},
            "marketaux": {"status": "error", "message": "quota exceeded"},
        }
        payload_path = Path(__file__).parent / "status_payload.json"
        output_path = Path(__file__).parent / "status_report.md"
        template_path = Path(__file__).parent / "status_report.template.md"
        summaries_path = Path(__file__).parent / "status_report.summaries.json"
        try:
            payload_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8-sig")
            with mock.patch.object(gmr, "parse_args", return_value=mock.Mock(
                date="2026-04-21",
                input_path=str(payload_path),
                output_path=str(output_path),
                template_output_path=str(template_path),
                summaries_output_path=str(summaries_path),
                print_report=False,
                refresh=False,
                live=False,
                mock=False,
                summary_provider="none",
                summary_model=None,
            )), mock.patch.object(gmr, "call_gemini_for_summaries", return_value=(sample_summaries(), "{}")), \
                mock.patch("sys.stdout", new=StringIO()) as stdout, \
                mock.patch("sys.stderr", new=StringIO()) as stderr:
                self.assertEqual(gmr.main(), 0)

            self.assertEqual(stdout.getvalue(), "")
            status = stderr.getvalue()
            self.assertIn("finmind: ok - Fetched successfully", status)
            self.assertIn("marketaux: error - quota exceeded", status)
        finally:
            for path in (payload_path, output_path, template_path, summaries_path):
                if path.exists():
                    path.unlink()


if __name__ == "__main__":
    unittest.main()
