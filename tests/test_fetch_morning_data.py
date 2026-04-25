import json
import unittest
from io import StringIO
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


class MorningDataTests(unittest.TestCase):
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
        markdown = rmr.render_report(payload)
        self.assertIn("# 市場晨報（2026-04-14）", markdown)
        self.assertIn("| S&P 500 | 5246.11 | 41.28 | 0.79 |", markdown)
        self.assertIn("| 上市指數 | 20482.12 | 點 |", markdown)
        self.assertIn("新台幣億元", markdown)
        self.assertIn("### 美股四大指數", markdown)
        self.assertIn("### 台股主要指數", markdown)
        self.assertIn("### 匯率", markdown)
        self.assertIn("### 亞股指數", markdown)
        self.assertIn("### 大宗商品", markdown)
        self.assertNotIn("| market_breadth |", markdown)
        self.assertIn("| 2026-04-13 | 4.71 | 4.34 | 4.49 | -0.37 | percent |", markdown)
        self.assertIn("| 2026-04-14 | Marketaux | [Fed officials signal patience on rate cuts as inflation data stays uneven](https://example.com/news/fed-officials-signal-patience) | 聯準會短期維持審慎立場，美元與債券殖利率維持高檔震盪。 |", markdown)
        self.assertIn("<!-- GEMINI_SECTION_1_SUMMARY -->", markdown)
        self.assertIn("<!-- GEMINI_FINAL_SUMMARY -->", markdown)

    def test_render_report_shows_missing_asia_rows(self) -> None:
        payload = fmd.build_base_payload("2026-04-21")
        payload["us_market"]["indices"] = [
            {"symbol": "SPX", "name": "S&P 500", "close": 7109.13, "change": -16.92, "change_percent": -0.2374},
        ]
        markdown = rmr.render_report(payload)
        self.assertIn("| N225 | 資料暫缺 | 資料暫缺 | 資料暫缺 |", markdown)
        self.assertIn("| HSI | 資料暫缺 | 資料暫缺 | 資料暫缺 |", markdown)
        self.assertIn("| KS11 | 資料暫缺 | 資料暫缺 | 資料暫缺 |", markdown)

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
        markdown = rmr.render_report(payload)
        self.assertIn("| 2026-04-20 | 3.72 | 4.26 | 4.88 | 0.54 | percent |", markdown)
        self.assertNotIn("| 2026-04-21 | 3.72 | 4.26 | 4.88 | 0.54 | percent |", markdown)

    def test_render_script_writes_utf8_sig_markdown(self) -> None:
        payload = json.loads((Path(__file__).parent.parent / "data" / "raw" / "mock_morning.json").read_text(encoding="utf-8-sig"))
        markdown = rmr.render_report(payload)
        output_path = Path(__file__).parent / "render_test_output.md"
        try:
            output_path.write_text(markdown, encoding="utf-8-sig")
            self.assertEqual(markdown, output_path.read_text(encoding="utf-8-sig"))
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
        markdown = rmr.render_report(payload)
        self.assertIn("| 2026-04-21 | Example | No Link Title | Summary |", markdown)
        self.assertNotIn("[No Link Title](", markdown)

    def test_apply_summaries_replaces_all_placeholders(self) -> None:
        payload = json.loads((Path(__file__).parent.parent / "data" / "raw" / "mock_morning.json").read_text(encoding="utf-8-sig"))
        markdown = rmr.render_report(payload)
        summaries = {
            "section_1": "第一段摘要。",
            "section_2": "第二段摘要。",
            "section_3": "第三段摘要。",
            "section_4": "第四段摘要。",
            "section_5": "第五段摘要。",
            "section_6": "第六段摘要。",
            "final_summary": ["重點一", "重點二", "重點三"],
        }
        final_markdown = rmr.apply_summaries(markdown, summaries)
        self.assertIn("第一段摘要。", final_markdown)
        self.assertIn("- 重點一", final_markdown)
        self.assertNotIn("<!-- GEMINI_SECTION_1_SUMMARY -->", final_markdown)
        self.assertNotIn("<!-- GEMINI_FINAL_SUMMARY -->", final_markdown)

    def test_extract_json_object_accepts_plain_or_fenced_json(self) -> None:
        plain = '{"section_1":"a","section_2":"b","section_3":"c","section_4":"d","section_5":"e","section_6":"f","final_summary":["x","y","z"]}'
        fenced = "```json\n" + plain + "\n```"
        self.assertEqual(gmr.extract_json_object(plain)["section_1"], "a")
        self.assertEqual(gmr.extract_json_object(fenced)["final_summary"], ["x", "y", "z"])

    def test_generate_report_merges_gemini_summaries_locally(self) -> None:
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
            )), mock.patch.object(gmr, "call_gemini_for_summaries", return_value=(summaries, json.dumps(summaries, ensure_ascii=False))), \
                mock.patch("sys.stdout", new=StringIO()):
                self.assertEqual(gmr.main(), 0)

            final_markdown = output_path.read_text(encoding="utf-8-sig")
            self.assertIn("第一段摘要。", final_markdown)
            self.assertIn("- 重點三", final_markdown)
            self.assertTrue(template_path.exists())
            self.assertEqual(
                json.loads(summaries_path.read_text(encoding="utf-8-sig"))["section_6"],
                "第六段摘要。",
            )
        finally:
            for path in (payload_path, output_path, template_path, summaries_path):
                if path.exists():
                    path.unlink()


if __name__ == "__main__":
    unittest.main()
