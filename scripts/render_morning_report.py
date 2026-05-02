import argparse
import html
import json
import re
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
MARKET_SUMMARY_PLACEHOLDER = "<!-- MARKET_SUMMARY -->"
SUMMARY_MOVEMENT_PATTERN = re.compile(
    r"(上漲|上升|上揚|大漲|小漲|買超|收高|走高|增加|攀升|"
    r"下跌|下降|下滑|大跌|小跌|賣超|收低|走低|減少|回落|"
    r"持平|資料暫缺|▲|▼|▬)"
    r"(?:\s*[-+−]?\d[\d,]*(?:\.\d+)?%?(?:\s*[（(][^）)]*[）)])?)?",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render the deterministic morning market report template as HTML.")
    parser.add_argument("--date", required=True, help="Report date in YYYY-MM-DD format.")
    parser.add_argument("--input", dest="input_path", help="Optional JSON payload path. Defaults to running the fetcher.")
    parser.add_argument(
        "--output",
        dest="output_path",
        help="Optional HTML output path. Defaults to reports/morning-<date>.html",
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
    return DEFAULT_REPORTS_DIR / f"morning-{report_date}.html"


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


def escape(value) -> str:
    return html.escape(stringify(value), quote=True)


def numeric_value(value) -> float | None:
    if value in (None, "", MISSING):
        return None
    try:
        return float(str(value).replace(",", "").replace("%", ""))
    except ValueError:
        return None


def movement_class(value) -> str:
    number = numeric_value(value)
    if number is None:
        return "missing"
    if number > 0:
        return "up"
    if number < 0:
        return "down"
    return "flat"


def movement_icon(value) -> str:
    css_class = movement_class(value)
    return {"up": "▲", "down": "▼", "flat": "▬"}.get(css_class, "")


def movement_cell(value) -> dict:
    return {"value": stringify(value), "movement": True}


def plain_cell(value) -> dict:
    return {"value": stringify(value), "movement": False}


def render_cell(cell: dict | str) -> str:
    if not isinstance(cell, dict):
        return f"<td>{escape(cell)}</td>"

    value = stringify(cell.get("value"))
    if not cell.get("movement"):
        return f"<td>{escape(value)}</td>"

    css_class = movement_class(value)
    icon = movement_icon(value)
    icon_html = f'<span class="move-icon" aria-hidden="true">{icon}</span>' if icon else ""
    return f'<td class="num {css_class}">{icon_html}<span>{escape(value)}</span></td>'


def summary_movement_class(text: str) -> str:
    if "資料暫缺" in text:
        return "missing"
    if any(token in text for token in ("下跌", "下降", "下滑", "大跌", "小跌", "賣超", "收低", "走低", "減少", "回落", "▼")):
        return "down"
    if any(token in text for token in ("上漲", "上升", "上揚", "大漲", "小漲", "買超", "收高", "走高", "增加", "攀升", "▲")):
        return "up"
    return "flat"


def render_summary_text(text: str) -> str:
    parts = []
    cursor = 0
    for match in SUMMARY_MOVEMENT_PATTERN.finditer(text):
        start, end = match.span()
        if start > cursor:
            parts.append(escape(text[cursor:start]))
        segment = match.group(0)
        css_class = summary_movement_class(segment)
        icon = {"up": "▲", "down": "▼", "flat": "▬"}.get(css_class, "")
        needs_icon = icon and not segment.lstrip().startswith(icon) and css_class != "missing"
        icon_html = f'<span class="move-icon" aria-hidden="true">{icon}</span>' if needs_icon else ""
        parts.append(f'<span class="summary-move {css_class}">{icon_html}{escape(segment)}</span>')
        cursor = end
    if cursor < len(text):
        parts.append(escape(text[cursor:]))
    return "".join(parts)


def html_table(headers: list[str], rows: list[list[dict | str]]) -> str:
    head = "".join(f"<th>{escape(header)}</th>" for header in headers)
    body_rows = []
    for row in rows:
        body_rows.append("<tr>" + "".join(render_cell(cell) for cell in row) + "</tr>")
    return "\n".join(
        [
            '<div class="table-wrap">',
            "<table>",
            f"<thead><tr>{head}</tr></thead>",
            "<tbody>",
            "\n".join(body_rows),
            "</tbody>",
            "</table>",
            "</div>",
        ]
    )


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


def heading_with_data_date(level: int, heading: str, *dates) -> str:
    return (
        f"<h{level}>{escape(heading)}"
        f'<span class="data-date">資料日：{escape(data_date_label(*dates))}</span>'
        f"</h{level}>"
    )


def item_dates(items: list[dict | None], fallback_date=None) -> list:
    dates = [item.get("date") for item in items if isinstance(item, dict) and item.get("date") not in (None, "")]
    if dates:
        return dates
    if fallback_date not in (None, ""):
        return [fallback_date]
    return []


def index_map(indices: list[dict]) -> dict[str, dict]:
    return {item.get("symbol"): item for item in indices if item.get("symbol")}


def linked_news_title(item: dict) -> str:
    title = stringify(item.get("title"))
    url = item.get("url")
    if title == MISSING or not url:
        return escape(title)
    return f'<a href="{html.escape(str(url), quote=True)}" target="_blank" rel="noopener noreferrer">{escape(title)}</a>'


def preferred_summary(item: dict) -> str:
    for key in ("brief", "summary_text", "summary"):
        value = item.get(key)
        if value not in (None, ""):
            return stringify(value)
    return MISSING


def section_card(section_id: str, title_html: str, content: list[str]) -> str:
    return "\n".join(
        [
            f'<section class="report-section" id="{section_id}">',
            title_html,
            *content,
            "</section>",
        ]
    )


def render_section_1(payload: dict) -> str:
    us_market = payload.get("us_market", {})
    taiwan_market = payload.get("taiwan_market", {})
    us_indices = us_market.get("indices", [])
    us_by_symbol = index_map(us_indices)
    us_rows = []
    for symbol, label in US_INDEX_ORDER:
        item = us_by_symbol.get(symbol)
        us_rows.append([
            plain_cell(label),
            plain_cell(maybe_value(item, "close")),
            movement_cell(maybe_value(item, "change")),
            movement_cell(maybe_value(item, "change_percent")),
        ])

    twse = taiwan_market.get("twse_index")
    tpex = taiwan_market.get("tpex_index")
    tw_rows = [
        [plain_cell("上市指數"), plain_cell(maybe_value(twse, "close")), movement_cell(maybe_value(twse, "change")), movement_cell(maybe_value(twse, "change_percent"))],
        [plain_cell("上櫃指數"), plain_cell(maybe_value(tpex, "close")), movement_cell(maybe_value(tpex, "change")), movement_cell(maybe_value(tpex, "change_percent"))],
    ]

    return section_card(
        "section-1",
        "<h2>1. 美股與台股盤勢</h2>",
        [
            heading_with_data_date(3, "美股四大指數", item_dates(us_indices, us_market.get("date"))),
            html_table(["指數", "收盤", "漲跌", "漲跌幅"], us_rows),
            heading_with_data_date(3, "台股主要指數", item_dates([twse, tpex], taiwan_market.get("date"))),
            html_table(["市場", "收盤", "漲跌", "漲跌幅"], tw_rows),
        ],
    )


def render_section_2(payload: dict) -> str:
    taiwan_market = payload.get("taiwan_market", {})
    twse = taiwan_market.get("twse_index")
    tpex = taiwan_market.get("tpex_index")
    inst = payload.get("taiwan_institutional", {})
    rows = [
        [plain_cell("上市指數"), plain_cell(maybe_value(twse, "close")), plain_cell("點" if twse and twse.get("close") is not None else MISSING)],
        [plain_cell("上櫃指數"), plain_cell(maybe_value(tpex, "close")), plain_cell("點" if tpex and tpex.get("close") is not None else MISSING)],
        [plain_cell("三大法人合計"), movement_cell(inst.get("three_institutions_net_buy_sell")), plain_cell(inst.get("unit"))],
        [plain_cell("外資"), movement_cell(inst.get("foreign_investors_net_buy_sell")), plain_cell(inst.get("unit"))],
        [plain_cell("投信"), movement_cell(inst.get("investment_trust_net_buy_sell")), plain_cell(inst.get("unit"))],
        [plain_cell("自營商"), movement_cell(inst.get("dealer_net_buy_sell")), plain_cell(inst.get("unit"))],
    ]
    return section_card(
        "section-2",
        heading_with_data_date(2, "2. 台股三大法人 / 上市 / 上櫃", taiwan_market.get("date"), inst.get("date")),
        [html_table(["項目", "數值", "單位"], rows)],
    )


def render_section_3(payload: dict) -> str:
    us_market = payload.get("us_market", {})
    us_indices = us_market.get("indices", [])
    us_by_symbol = index_map(us_indices)
    rows = []
    for symbol, label in US_INDEX_ORDER:
        item = us_by_symbol.get(symbol)
        rows.append([
            plain_cell(label),
            plain_cell(maybe_value(item, "close")),
            movement_cell(maybe_value(item, "change")),
            movement_cell(maybe_value(item, "change_percent")),
        ])
    return section_card(
        "section-3",
        heading_with_data_date(2, "3. 美股四大指數", item_dates(us_indices, us_market.get("date"))),
        [html_table(["指數", "收盤", "漲跌", "漲跌幅"], rows)],
    )


def render_section_4(payload: dict) -> str:
    fx = payload.get("fx", {})
    asia_market = payload.get("asia_market", {})
    commodities = payload.get("commodities", {})
    fx_item = fx.get("usd_twd")
    fx_rows = [[plain_cell("USD/TWD"), plain_cell(maybe_value(fx_item, "close")), movement_cell(maybe_value(fx_item, "change")), movement_cell(maybe_value(fx_item, "change_percent"))]]

    asia_indices = asia_market.get("indices", [])
    asia_by_symbol = index_map(asia_indices)
    asia_rows = []
    for symbol, label in ASIA_INDEX_ORDER:
        item = asia_by_symbol.get(symbol)
        asia_rows.append([plain_cell(label), plain_cell(maybe_value(item, "close")), movement_cell(maybe_value(item, "change")), movement_cell(maybe_value(item, "change_percent"))])

    gold = commodities.get("gold")
    crude = commodities.get("crude_oil")
    commodity_rows = [
        [plain_cell("黃金"), plain_cell(maybe_value(gold, "close")), movement_cell(maybe_value(gold, "change")), movement_cell(maybe_value(gold, "change_percent")), plain_cell(maybe_value(gold, "unit"))],
        [plain_cell("原油"), plain_cell(maybe_value(crude, "close")), movement_cell(maybe_value(crude, "change")), movement_cell(maybe_value(crude, "change_percent")), plain_cell(maybe_value(crude, "unit"))],
    ]

    return section_card(
        "section-4",
        "<h2>4. 匯率、亞股、原油、黃金</h2>",
        [
            heading_with_data_date(3, "匯率", item_dates([fx_item], fx.get("date"))),
            html_table(["項目", "收盤", "漲跌", "漲跌幅"], fx_rows),
            heading_with_data_date(3, "亞股指數", item_dates(asia_indices, asia_market.get("date"))),
            html_table(["指數", "收盤", "漲跌", "漲跌幅"], asia_rows),
            heading_with_data_date(3, "大宗商品", item_dates([gold, crude], commodities.get("date"))),
            html_table(["商品", "收盤", "漲跌", "漲跌幅", "單位"], commodity_rows),
        ],
    )


def render_section_5(payload: dict) -> str:
    bonds = payload.get("bonds", {})
    rows = [[
        plain_cell(bonds.get("date")),
        movement_cell(bonds.get("yield_2y")),
        movement_cell(bonds.get("yield_10y")),
        movement_cell(bonds.get("yield_30y")),
        movement_cell(bonds.get("slope_10y_2y")),
        plain_cell(bonds.get("unit")),
    ]]
    return section_card(
        "section-5",
        heading_with_data_date(2, "5. 美國債市與關鍵觀察", bonds.get("date")),
        [html_table(["日期", "2Y", "10Y", "30Y", "10Y-2Y", "單位"], rows)],
    )


def render_section_6(payload: dict) -> str:
    news_items = payload.get("news", [])[:3]
    rows = []
    for item in news_items:
        rows.append([
            plain_cell(item.get("date")),
            plain_cell(item.get("source")),
            {"value": linked_news_title(item), "html": True},
            plain_cell(preferred_summary(item)),
        ])
    while len(rows) < 3:
        rows.append([plain_cell(MISSING), plain_cell(MISSING), plain_cell(MISSING), plain_cell(MISSING)])

    rendered_rows = []
    for row in rows:
        cells = []
        for cell in row:
            if isinstance(cell, dict) and cell.get("html"):
                cells.append(f"<td>{cell['value']}</td>")
            else:
                cells.append(render_cell(cell))
        rendered_rows.append("<tr>" + "".join(cells) + "</tr>")
    table = "\n".join(
        [
            '<div class="table-wrap">',
            "<table>",
            "<thead><tr><th>日期</th><th>來源</th><th>標題</th><th>摘要</th></tr></thead>",
            "<tbody>",
            "\n".join(rendered_rows),
            "</tbody>",
            "</table>",
            "</div>",
        ]
    )
    return section_card(
        "section-6",
        heading_with_data_date(2, "6. 三則重要財經新聞", item_dates(news_items, payload.get("report_date"))),
        [table],
    )


def render_summary_placeholder() -> str:
    return "\n".join(
        [
            '<section class="summary-card">',
            "<h2>指數與市場變化總結</h2>",
            MARKET_SUMMARY_PLACEHOLDER,
            "</section>",
        ]
    )


def render_styles() -> str:
    return """
body { margin: 0; background: #f5f5f2; color: #202124; font-family: "Noto Sans TC", "Microsoft JhengHei", Arial, sans-serif; line-height: 1.55; }
.page { max-width: 1180px; margin: 0 auto; padding: 32px 20px 48px; }
.report-header { border-bottom: 3px solid #1f2933; margin-bottom: 22px; padding-bottom: 12px; }
h1 { font-size: 32px; margin: 0 0 6px; letter-spacing: 0; }
h2 { font-size: 22px; margin: 0 0 14px; letter-spacing: 0; }
h3 { font-size: 17px; margin: 18px 0 10px; letter-spacing: 0; }
.data-date { display: inline-block; margin-left: 10px; color: #5f6368; font-size: 13px; font-weight: 500; }
.summary-card, .report-section { background: #fff; border: 1px solid #d7d5cf; border-radius: 8px; padding: 18px; margin: 18px 0; box-shadow: 0 1px 2px rgba(0,0,0,0.04); }
.summary-card { border-left: 5px solid #a32121; }
.summary-block { margin: 14px 0 0; }
.summary-block h3 { color: #1f2933; margin-top: 16px; }
.summary-block ul { margin: 8px 0 0 20px; padding: 0; }
.summary-block li { margin: 7px 0; }
.summary-move { font-weight: 800; white-space: nowrap; }
.muted { color: #6f7479; }
.table-wrap { overflow-x: auto; margin: 8px 0 18px; }
table { width: 100%; border-collapse: collapse; background: #fff; }
th, td { border-bottom: 1px solid #e4e1da; padding: 10px 12px; text-align: left; vertical-align: top; white-space: nowrap; }
th { background: #2f3a3f; color: #fff; font-weight: 700; }
td.num { font-variant-numeric: tabular-nums; font-weight: 700; }
.up { color: #c62828; }
.down { color: #16833a; }
.flat { color: #6f7479; }
.missing { color: #9aa0a6; font-weight: 500; }
.move-icon { display: inline-block; min-width: 1.1em; margin-right: 4px; font-size: 0.86em; }
a { color: #0b57d0; text-decoration: none; }
a:hover { text-decoration: underline; }
@media (max-width: 720px) { .page { padding: 20px 12px 36px; } h1 { font-size: 26px; } th, td { padding: 8px 10px; } }
""".strip()


def render_report(payload: dict) -> str:
    report_date = payload.get("report_date", MISSING)
    sections = [
        "<!doctype html>",
        '<html lang="zh-Hant">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>市場晨報（{escape(report_date)}）</title>",
        "<style>",
        render_styles(),
        "</style>",
        "</head>",
        "<body>",
        '<main class="page">',
        '<header class="report-header">',
        f"<h1>市場晨報（{escape(report_date)}）</h1>",
        '<p class="muted">資料來源以各表格列示日期為準；紅色代表上漲或買超，綠色代表下跌或賣超。</p>',
        "</header>",
        render_summary_placeholder(),
        render_section_1(payload),
        render_section_2(payload),
        render_section_3(payload),
        render_section_4(payload),
        render_section_5(payload),
        render_section_6(payload),
        "</main>",
        "</body>",
        "</html>",
    ]
    return "\n".join(sections).rstrip() + "\n"


def normalize_market_summary(items) -> list[dict]:
    if not isinstance(items, list):
        raise ValueError("market_summary must be a list")
    normalized = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Each market_summary item must be an object")
        heading = item.get("heading")
        bullets = item.get("bullets")
        if not isinstance(heading, str) or not heading.strip():
            raise ValueError("Each market_summary heading must be a non-empty string")
        if not isinstance(bullets, list) or not bullets:
            raise ValueError("Each market_summary bullets field must be a non-empty list")
        normalized_bullets = []
        for bullet in bullets:
            if not isinstance(bullet, str) or not bullet.strip():
                raise ValueError("Each market_summary bullet must be a non-empty string")
            normalized_bullets.append(bullet.strip())
        normalized.append({"heading": heading.strip(), "bullets": normalized_bullets})
    return normalized


def render_market_summary(summary_items) -> str:
    blocks = []
    for index, item in enumerate(normalize_market_summary(summary_items), start=1):
        bullets = "\n".join(f"<li>{render_summary_text(bullet)}</li>" for bullet in item["bullets"])
        blocks.append(
            "\n".join(
                [
                    '<div class="summary-block">',
                    f"<h3>{index}. {escape(item['heading'])}</h3>",
                    f"<ul>{bullets}</ul>",
                    "</div>",
                ]
            )
        )
    return "\n".join(blocks)


def apply_summaries(report_html: str, summaries: dict) -> str:
    return report_html.replace(MARKET_SUMMARY_PLACEHOLDER, render_market_summary(summaries.get("market_summary")))


def remove_summary_placeholder(report_html: str) -> str:
    empty_summary = '<p class="muted">本次未產生 LLM 摘要；以下保留來源資料表。</p>'
    return report_html.replace(MARKET_SUMMARY_PLACEHOLDER, empty_summary)


def main() -> int:
    args = parse_args()
    payload = load_payload(args.date, args.input_path)
    report_html = render_report(payload)
    output_path = output_path_for(args.date, args.output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report_html, encoding="utf-8-sig")
    print(report_html, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
