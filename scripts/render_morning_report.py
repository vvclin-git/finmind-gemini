import argparse
import json
import subprocess
import sys
from pathlib import Path


MISSING = "資料暫缺"
DEFAULT_INPUT = Path("out.json")
DEFAULT_REPORTS_DIR = Path("reports")
FETCHER_PATH = Path(__file__).with_name("fetch_morning_data.py")

US_INDEX_ORDER = [
    ("SPX", "S&P 500"),
    ("IXIC", "Nasdaq Composite"),
    ("DJI", "Dow Jones Industrial Average"),
    ("RUT", "Russell 2000"),
]
ASIA_INDEX_ORDER = [
    ("N225", "N225"),
    ("HSI", "HSI"),
    ("KS11", "KS11"),
]
SECTION_SUMMARY_PLACEHOLDERS = {
    1: "<!-- GEMINI_SECTION_1_SUMMARY -->",
    2: "<!-- GEMINI_SECTION_2_SUMMARY -->",
    3: "<!-- GEMINI_SECTION_3_SUMMARY -->",
    4: "<!-- GEMINI_SECTION_4_SUMMARY -->",
    5: "<!-- GEMINI_SECTION_5_SUMMARY -->",
    6: "<!-- GEMINI_SECTION_6_SUMMARY -->",
}
FINAL_SUMMARY_PLACEHOLDER = "<!-- GEMINI_FINAL_SUMMARY -->"
SECTION_SUMMARY_KEYS = {index: f"section_{index}" for index in SECTION_SUMMARY_PLACEHOLDERS}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render the deterministic morning market report template as markdown.")
    parser.add_argument("--date", required=True, help="Report date in YYYY-MM-DD format.")
    parser.add_argument("--input", dest="input_path", help="Optional JSON payload path. Defaults to running the fetcher.")
    parser.add_argument(
        "--output",
        dest="output_path",
        help="Optional markdown output path. Defaults to reports/morning-<date>.md",
    )
    return parser.parse_args()


def read_json_file(path: Path) -> dict:
    for encoding in ("utf-8-sig", "utf-8", "cp950"):
        try:
            return json.loads(path.read_text(encoding=encoding))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    raise RuntimeError(f"Unable to decode JSON payload from {path}")


def load_payload(report_date: str, input_path: str | None) -> dict:
    if input_path:
        return read_json_file(Path(input_path))

    if DEFAULT_INPUT.exists():
        try:
            payload = read_json_file(DEFAULT_INPUT)
            if payload.get("report_date") == report_date:
                return payload
        except Exception:
            pass

    proc = subprocess.run(
        [sys.executable, str(FETCHER_PATH), "--date", report_date],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return json.loads(proc.stdout)


def output_path_for(report_date: str, explicit_path: str | None) -> Path:
    if explicit_path:
        return Path(explicit_path)
    return DEFAULT_REPORTS_DIR / f"morning-{report_date}.md"


def stringify(value) -> str:
    if value in (None, ""):
        return MISSING
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, separators=(", ", ": "))
    return str(value)


def maybe_value(item: dict | None, key: str) -> str:
    if not item:
        return MISSING
    return stringify(item.get(key))


def escape_cell(value: str) -> str:
    return value.replace("|", r"\|").replace("\n", " ").strip()


def markdown_table(headers: list[str], rows: list[list[str]]) -> str:
    lines = [
        "| " + " | ".join(escape_cell(header) for header in headers) + " |",
        "| " + " | ".join(":---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(escape_cell(stringify(cell)) for cell in row) + " |")
    return "\n".join(lines)


def data_date_label(*dates) -> str:
    normalized = []
    for value in dates:
        if isinstance(value, (list, tuple, set)):
            normalized.extend(data_date for data_date in value if data_date not in (None, ""))
        elif value not in (None, ""):
            normalized.append(value)
    unique_dates = sorted({stringify(value) for value in normalized if stringify(value) != MISSING})
    if not unique_dates:
        return MISSING
    if len(unique_dates) == 1:
        return unique_dates[0]
    return "多日期：" + ", ".join(unique_dates)


def heading_with_data_date(heading: str, *dates) -> str:
    return f"{heading}（資料日：{data_date_label(*dates)}）"


def item_dates(items: list[dict], fallback_date=None) -> list:
    dates = [item.get("date") for item in items if isinstance(item, dict) and item.get("date") not in (None, "")]
    if dates:
        return dates
    if fallback_date not in (None, ""):
        return [fallback_date]
    return []


def index_map(indices: list[dict]) -> dict[str, dict]:
    return {item.get("symbol"): item for item in indices if item.get("symbol")}


def summary_placeholder(section_number: int) -> str:
    return SECTION_SUMMARY_PLACEHOLDERS[section_number]


def render_section_1(payload: dict) -> str:
    us_market = payload.get("us_market", {})
    taiwan_market = payload.get("taiwan_market", {})
    us_indices = us_market.get("indices", [])
    us_by_symbol = index_map(us_indices)
    us_rows = []
    for symbol, label in US_INDEX_ORDER:
        item = us_by_symbol.get(symbol)
        us_rows.append([label, maybe_value(item, "close"), maybe_value(item, "change"), maybe_value(item, "change_percent")])

    twse = taiwan_market.get("twse_index")
    tpex = taiwan_market.get("tpex_index")
    tw_rows = [
        ["上市指數", maybe_value(twse, "close"), maybe_value(twse, "change"), maybe_value(twse, "change_percent")],
        ["上櫃指數", maybe_value(tpex, "close"), maybe_value(tpex, "change"), maybe_value(tpex, "change_percent")],
    ]

    return "\n".join(
        [
            "## 1. 美股與台股盤勢",
            "",
            heading_with_data_date("### 美股四大指數", item_dates(us_indices, us_market.get("date"))),
            "",
            markdown_table(["指數", "收盤", "漲跌", "漲跌幅"], us_rows),
            "",
            heading_with_data_date("### 台股主要指數", item_dates([twse, tpex], taiwan_market.get("date"))),
            "",
            markdown_table(["市場", "收盤", "漲跌", "漲跌幅"], tw_rows),
            "",
            summary_placeholder(1),
        ]
    )


def render_section_2(payload: dict) -> str:
    taiwan_market = payload.get("taiwan_market", {})
    twse = taiwan_market.get("twse_index")
    tpex = taiwan_market.get("tpex_index")
    inst = payload.get("taiwan_institutional", {})

    rows = [
        ["上市指數", maybe_value(twse, "close"), "點" if twse and twse.get("close") is not None else MISSING],
        ["上櫃指數", maybe_value(tpex, "close"), "點" if tpex and tpex.get("close") is not None else MISSING],
        ["三大法人合計", stringify(inst.get("three_institutions_net_buy_sell")), stringify(inst.get("unit"))],
        ["外資", stringify(inst.get("foreign_investors_net_buy_sell")), stringify(inst.get("unit"))],
        ["投信", stringify(inst.get("investment_trust_net_buy_sell")), stringify(inst.get("unit"))],
        ["自營商", stringify(inst.get("dealer_net_buy_sell")), stringify(inst.get("unit"))],
    ]

    return "\n".join(
        [
            heading_with_data_date("## 2. 台股三大法人 / 上市 / 上櫃", taiwan_market.get("date"), inst.get("date")),
            "",
            markdown_table(["項目", "數值", "單位"], rows),
            "",
            summary_placeholder(2),
        ]
    )


def render_section_3(payload: dict) -> str:
    us_market = payload.get("us_market", {})
    us_indices = us_market.get("indices", [])
    us_by_symbol = index_map(us_indices)
    rows = []
    for symbol, label in US_INDEX_ORDER:
        item = us_by_symbol.get(symbol)
        rows.append([label, maybe_value(item, "close"), maybe_value(item, "change"), maybe_value(item, "change_percent")])

    return "\n".join(
        [
            heading_with_data_date("## 3. 美股四大指數", item_dates(us_indices, us_market.get("date"))),
            "",
            markdown_table(["指數", "收盤", "漲跌", "漲跌幅"], rows),
            "",
            summary_placeholder(3),
        ]
    )


def render_section_4(payload: dict) -> str:
    fx = payload.get("fx", {})
    asia_market = payload.get("asia_market", {})
    commodities = payload.get("commodities", {})
    fx_item = fx.get("usd_twd")
    fx_rows = [["USD/TWD", maybe_value(fx_item, "close"), maybe_value(fx_item, "change"), maybe_value(fx_item, "change_percent")]]

    asia_indices = asia_market.get("indices", [])
    asia_by_symbol = index_map(asia_indices)
    asia_rows = []
    for symbol, label in ASIA_INDEX_ORDER:
        item = asia_by_symbol.get(symbol)
        asia_rows.append([label, maybe_value(item, "close"), maybe_value(item, "change"), maybe_value(item, "change_percent")])

    gold = commodities.get("gold")
    crude = commodities.get("crude_oil")
    commodity_rows = [
        ["黃金", maybe_value(gold, "close"), maybe_value(gold, "change"), maybe_value(gold, "change_percent"), maybe_value(gold, "unit")],
        ["原油", maybe_value(crude, "close"), maybe_value(crude, "change"), maybe_value(crude, "change_percent"), maybe_value(crude, "unit")],
    ]

    return "\n".join(
        [
            "## 4. 匯率、亞股、原油、黃金",
            "",
            heading_with_data_date("### 匯率", item_dates([fx_item], fx.get("date"))),
            "",
            markdown_table(["項目", "收盤", "漲跌", "漲跌幅"], fx_rows),
            "",
            heading_with_data_date("### 亞股指數", item_dates(asia_indices, asia_market.get("date"))),
            "",
            markdown_table(["指數", "收盤", "漲跌", "漲跌幅"], asia_rows),
            "",
            heading_with_data_date("### 大宗商品", item_dates([gold, crude], commodities.get("date"))),
            "",
            markdown_table(["商品", "收盤", "漲跌", "漲跌幅", "單位"], commodity_rows),
            "",
            summary_placeholder(4),
        ]
    )


def render_section_5(payload: dict) -> str:
    bonds = payload.get("bonds", {})
    rows = [[
        stringify(bonds.get("date")),
        stringify(bonds.get("yield_2y")),
        stringify(bonds.get("yield_10y")),
        stringify(bonds.get("yield_30y")),
        stringify(bonds.get("slope_10y_2y")),
        stringify(bonds.get("unit")),
    ]]

    return "\n".join(
        [
            heading_with_data_date("## 5. 美國債市與關鍵觀察", bonds.get("date")),
            "",
            markdown_table(["日期", "2Y", "10Y", "30Y", "10Y-2Y", "單位"], rows),
            "",
            summary_placeholder(5),
        ]
    )


def preferred_summary(item: dict) -> str:
    for key in ("brief", "summary_text", "summary"):
        value = item.get(key)
        if value not in (None, ""):
            return stringify(value)
    return MISSING


def linked_news_title(item: dict) -> str:
    title = stringify(item.get("title"))
    url = item.get("url")
    if title == MISSING or not url:
        return title
    return f"[{title}]({url})"


def render_section_6(payload: dict) -> str:
    news_items = payload.get("news", [])[:3]
    rows = []
    for item in news_items:
        rows.append([
            stringify(item.get("date")),
            stringify(item.get("source")),
            linked_news_title(item),
            preferred_summary(item),
        ])
    while len(rows) < 3:
        rows.append([MISSING, MISSING, MISSING, MISSING])

    return "\n".join(
        [
            heading_with_data_date("## 6. 三則重要財經新聞", item_dates(news_items, payload.get("report_date"))),
            "",
            markdown_table(["日期", "來源", "標題", "摘要"], rows),
            "",
            summary_placeholder(6),
        ]
    )


def render_section_7() -> str:
    return "\n".join(
        [
            "## 7. 晨報總結",
            "",
            FINAL_SUMMARY_PLACEHOLDER,
        ]
    )


def render_report(payload: dict) -> str:
    sections = [
        f"# 市場晨報（{payload.get('report_date', MISSING)}）",
        "",
        render_section_1(payload),
        "",
        render_section_2(payload),
        "",
        render_section_3(payload),
        "",
        render_section_4(payload),
        "",
        render_section_5(payload),
        "",
        render_section_6(payload),
        "",
        render_section_7(),
    ]
    return "\n".join(sections).rstrip() + "\n"


def normalize_summary_text(value) -> str:
    if not isinstance(value, str):
        raise ValueError("Section summary must be a string")
    text = value.strip()
    if not text:
        raise ValueError("Section summary must not be empty")
    return text


def normalize_final_summary(items) -> list[str]:
    if not isinstance(items, list):
        raise ValueError("final_summary must be a list")
    normalized = []
    for item in items:
        if not isinstance(item, str):
            raise ValueError("Each final_summary item must be a string")
        text = item.strip()
        if not text:
            raise ValueError("Each final_summary item must not be empty")
        normalized.append(text)
    if not 3 <= len(normalized) <= 5:
        raise ValueError("final_summary must contain 3 to 5 bullet points")
    return normalized


def apply_summaries(markdown: str, summaries: dict) -> str:
    result = markdown
    for section_number, placeholder in SECTION_SUMMARY_PLACEHOLDERS.items():
        key = SECTION_SUMMARY_KEYS[section_number]
        replacement = normalize_summary_text(summaries.get(key))
        result = result.replace(placeholder, replacement)

    final_bullets = "\n".join(f"- {item}" for item in normalize_final_summary(summaries.get("final_summary")))
    result = result.replace(FINAL_SUMMARY_PLACEHOLDER, final_bullets)
    return result


def main() -> int:
    args = parse_args()
    payload = load_payload(args.date, args.input_path)
    markdown = render_report(payload)
    output_path = output_path_for(args.date, args.output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(markdown, encoding="utf-8-sig")
    print(markdown, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
