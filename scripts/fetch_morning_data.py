import argparse
import csv
import io
import json
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from pathlib import Path


FINMIND_API_URL = "https://api.finmindtrade.com/api/v4/data"
FMP_BASE_URL = "https://financialmodelingprep.com/stable"
FMP_QUOTE_URL = f"{FMP_BASE_URL}/quote"
FMP_HISTORICAL_EOD_FULL_URL = f"{FMP_BASE_URL}/historical-price-eod/full"
EODHD_EOD_URL = "https://eodhd.com/api/eod"
MARKETAUX_API_URL = "https://api.marketaux.com/v1/news/all"
TREASURY_XML_URL = (
    "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml"
)
TPEX_INDEX_URL = "https://www.tpex.org.tw/www/zh-tw/indexInfo/inx"
TPEX_PRICE_INDEX_NAME = "TPEx OTC Index"
TPEX_PRICE_INDEX_SYMBOL = "TPEX"
SYMBOL_MAP_PATH = Path(__file__).with_name("symbol_map.json")
PAYLOADS_DIR = Path("payloads")
REQUEST_EXCERPT_LIMIT = 400
ASIA_INDEX_METADATA = {
    "N225": {"name": "Nikkei 225"},
    "HSI": {"name": "Hang Seng Index"},
    "KS11": {"name": "KOSPI"},
}


def default_headers(accept: str) -> dict:
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": accept,
        "Accept-Language": "en-US,en;q=0.9,zh-TW;q=0.8",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Referer": "https://www.google.com/",
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch morning market data as JSON.")
    parser.add_argument("--date", required=True, help="Report date in YYYY-MM-DD format.")
    return parser.parse_args()


def validate_date(date_text: str) -> str:
    try:
        datetime.strptime(date_text, "%Y-%m-%d")
    except ValueError as exc:
        raise SystemExit(f"Invalid date format: {date_text}. Expected YYYY-MM-DD.") from exc
    return date_text


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def clip_text(value: str | bytes | None, limit: int = REQUEST_EXCERPT_LIMIT) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    trimmed = value.strip().replace("\r", " ").replace("\n", " ")
    if len(trimmed) > limit:
        return trimmed[:limit] + "..."
    return trimmed


def build_url(url: str, params: dict | None = None) -> str:
    if not params:
        return url
    return f"{url}?{urllib.parse.urlencode(params)}"


def fetch_response(
    url: str,
    params: dict | None = None,
    headers: dict | None = None,
    *,
    accept: str,
    insecure_ssl: bool = False,
) -> tuple[str, dict]:
    final_url = build_url(url, params)
    request_headers = default_headers(accept)
    if headers:
        request_headers.update(headers)
    request = urllib.request.Request(final_url, headers=request_headers)
    context = ssl._create_unverified_context() if insecure_ssl else None
    debug = {
        "provider": None,
        "url": final_url,
        "status_code": None,
        "response_excerpt": "",
        "selected_symbol": None,
        "rejected_candidates": [],
    }

    try:
        with urllib.request.urlopen(request, timeout=20, context=context) as response:
            body = response.read().decode("utf-8", errors="replace")
            debug["status_code"] = getattr(response, "status", None)
            debug["response_excerpt"] = clip_text(body)
            return body, debug
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        debug["status_code"] = exc.code
        debug["response_excerpt"] = clip_text(body)
        message = f"HTTP Error {exc.code}: {exc.reason}"
        if body:
            message += f" | body: {clip_text(body)}"
        raise RuntimeError(message) from exc
    except urllib.error.URLError as exc:
        debug["response_excerpt"] = clip_text(str(exc.reason))
        raise RuntimeError(str(exc.reason)) from exc


def fetch_json(
    url: str,
    params: dict,
    headers: dict | None = None,
    *,
    insecure_ssl: bool = False,
) -> tuple[dict, dict]:
    body, debug = fetch_response(
        url,
        params=params,
        headers=headers,
        accept="application/json, text/plain, */*",
        insecure_ssl=insecure_ssl,
    )
    try:
        return json.loads(body), debug
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON response: {clip_text(body)}") from exc


def fetch_text(
    url: str,
    params: dict | None = None,
    headers: dict | None = None,
    *,
    accept: str = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    insecure_ssl: bool = False,
) -> tuple[str, dict]:
    return fetch_response(
        url,
        params=params,
        headers=headers,
        accept=accept,
        insecure_ssl=insecure_ssl,
    )


def finmind_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def finmind_get(dataset: str, token: str, **params) -> list[dict]:
    payload, _ = fetch_json(
        FINMIND_API_URL,
        {"dataset": dataset, **params},
        headers=finmind_headers(token),
    )
    if payload.get("status") != 200:
        raise RuntimeError(payload.get("msg") or f"FinMind {dataset} request failed")
    return payload.get("data") or []


def parse_date(date_text: str) -> datetime:
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(date_text, fmt)
        except ValueError:
            continue
    raise ValueError(f"Unsupported date format: {date_text}")


def window_start(date_text: str, days: int) -> str:
    return (datetime.strptime(date_text, "%Y-%m-%d") - timedelta(days=days)).strftime("%Y-%m-%d")


def shift_calendar_day(date_text: str, days: int) -> str:
    return (datetime.strptime(date_text, "%Y-%m-%d") + timedelta(days=days)).strftime("%Y-%m-%d")


def latest_row_on_or_before(rows: list[dict], target_date: str, date_key: str = "date") -> dict | None:
    target = datetime.strptime(target_date, "%Y-%m-%d").date()
    candidates = []
    for row in rows:
        row_date = parse_date(str(row[date_key])).date()
        if row_date <= target:
            candidates.append((parse_date(str(row[date_key])), row))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    return candidates[-1][1]


def prior_row_before(rows: list[dict], anchor_row: dict, date_key: str = "date") -> dict | None:
    anchor_dt = parse_date(str(anchor_row[date_key]))
    candidates = []
    for row in rows:
        row_dt = parse_date(str(row[date_key]))
        if row_dt < anchor_dt:
            candidates.append((row_dt, row))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    return candidates[-1][1]


def compute_change(current_value: float, previous_value: float | None) -> tuple[float | None, float | None]:
    if previous_value is None:
        return None, None
    change = current_value - previous_value
    change_percent = (change / previous_value * 100) if previous_value else None
    return round(change, 4), round(change_percent, 4) if change_percent is not None else None


def parse_float(value) -> float | None:
    if value in (None, "", "--", "---", "N/A", "None"):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "").replace("%", "")
    if not text:
        return None
    return float(text)


def compute_fx_close(row: dict) -> float | None:
    spot_buy = float(row.get("spot_buy", -99))
    spot_sell = float(row.get("spot_sell", -99))
    cash_buy = float(row.get("cash_buy", -99))
    cash_sell = float(row.get("cash_sell", -99))
    if spot_buy > 0 and spot_sell > 0:
        return round((spot_buy + spot_sell) / 2, 4)
    if cash_buy > 0 and cash_sell > 0:
        return round((cash_buy + cash_sell) / 2, 4)
    if spot_sell > 0:
        return round(spot_sell, 4)
    if cash_sell > 0:
        return round(cash_sell, 4)
    return None


def build_base_payload(report_date: str) -> dict:
    return {
        "report_date": report_date,
        "taiwan_market": {
            "date": report_date,
            "twse_index": None,
            "tpex_index": None,
            "market_breadth": None,
        },
        "taiwan_institutional": {
            "date": report_date,
            "foreign_investors_net_buy_sell": None,
            "investment_trust_net_buy_sell": None,
            "dealer_net_buy_sell": None,
            "three_institutions_net_buy_sell": None,
            "unit": "新台幣億元",
        },
        "us_market": {"date": report_date, "indices": []},
        "fx": {"date": report_date, "usd_twd": None},
        "asia_market": {"date": report_date, "indices": []},
        "commodities": {"date": report_date, "gold": None, "crude_oil": None},
        "bonds": {
            "date": report_date,
            "yield_2y": None,
            "yield_10y": None,
            "yield_30y": None,
            "slope_10y_2y": None,
            "unit": "percent",
        },
        "news": [],
        "meta": {
            "generated_at": now_iso(),
            "mode": "mock",
            "source_status": {},
            "sources_used": [],
            "partial_success": False,
            "missing_sections": [],
            "provider_by_field": {},
            "index_status": {"us_market": {}, "asia_market": {}},
            "request_debug": [],
        },
    }


def record_request_debug(
    payload: dict,
    *,
    provider: str,
    url: str,
    status_code: int | None,
    response_excerpt: str = "",
    selected_symbol: str | None = None,
    rejected_candidates: list | None = None,
    notes: str | None = None,
) -> None:
    payload["meta"]["request_debug"].append(
        {
            "provider": provider,
            "url": url,
            "status_code": status_code,
            "response_excerpt": clip_text(response_excerpt),
            "selected_symbol": selected_symbol,
            "rejected_candidates": rejected_candidates or [],
            "notes": notes,
        }
    )


def add_source_used(payload: dict, provider: str) -> None:
    sources_used = payload["meta"]["sources_used"]
    if provider not in sources_used:
        sources_used.append(provider)


def set_provider_for_field(payload: dict, field_path: str, provider: str | None) -> None:
    if provider:
        payload["meta"]["provider_by_field"][field_path] = provider


def set_index_status(
    payload: dict,
    region: str,
    series_code: str,
    *,
    status: str,
    provider_used: str | None,
    fallback_used: bool,
    selected_symbol: str | None,
    error_category: str | None = None,
    message: str | None = None,
) -> None:
    payload["meta"]["index_status"][region][series_code] = {
        "status": status,
        "provider_used": provider_used,
        "fallback_used": fallback_used,
        "selected_symbol": selected_symbol,
        "error_category": error_category,
        "message": message,
    }


def categorize_index_error(message: str) -> str:
    text = (message or "").lower()
    if "symbol" in text or "candidate" in text or "mapping" in text:
        return "symbol"
    if "missing" in text or "invalid json" in text or "csv" in text or "xml" in text:
        return "parser"
    if "close" in text or "change" in text or "percent" in text or "schema" in text:
        return "schema"
    return "provider"


def normalize_index_series_from_fmp(
    series_code: str,
    metadata: dict,
    *,
    quote_raw: list[dict] | None = None,
    historical_raw: list[dict] | None = None,
) -> dict:
    if quote_raw:
        latest = quote_raw[0] if quote_raw else None
        if latest:
            close_value = parse_float(latest.get("price"))
            change_value = parse_float(latest.get("change"))
            change_percent_value = parse_float(latest.get("changesPercentage"))
            if close_value is not None and change_value is not None and change_percent_value is not None:
                return {
                    "symbol": series_code,
                    "name": metadata["name"],
                    "close": round(close_value, 4),
                    "change": round(change_value, 4),
                    "change_percent": round(change_percent_value, 4),
                }

    if historical_raw and len(historical_raw) >= 2:
        latest_close = parse_float(historical_raw[0].get("close"))
        previous_close = parse_float(historical_raw[1].get("close"))
        if latest_close is not None and previous_close is not None:
            change, change_percent = compute_change(latest_close, previous_close)
            if change is not None and change_percent is not None:
                return {
                    "symbol": series_code,
                    "name": metadata["name"],
                    "close": round(latest_close, 4),
                    "change": change,
                    "change_percent": change_percent,
                }

    raise RuntimeError("FMP response missing usable quote or historical index data")


def normalize_index_series_from_eodhd(
    series_code: str,
    metadata: dict,
    historical_raw: list[dict],
    report_date: str,
) -> dict:
    if not historical_raw:
        raise RuntimeError("EODHD response missing historical index data")

    target_date = datetime.strptime(report_date, "%Y-%m-%d").date()
    eligible_rows = []
    for row in historical_raw:
        raw_date = str(row.get("date") or "").strip()
        if not raw_date:
            continue
        try:
            row_dt = parse_date(raw_date)
        except ValueError:
            continue
        if row_dt.date() <= target_date:
            eligible_rows.append((row_dt, row))

    if not eligible_rows:
        raise RuntimeError("EODHD response has no row on or before report date")

    eligible_rows.sort(key=lambda item: item[0])
    current_dt, current_row = eligible_rows[-1]
    previous_row = eligible_rows[-2][1] if len(eligible_rows) >= 2 else None

    current_close = parse_float(current_row.get("adjusted_close"))
    if current_close is None:
        current_close = parse_float(current_row.get("close"))
    if current_close is None:
        raise RuntimeError("EODHD payload is missing close")

    previous_close = None
    if previous_row:
        previous_close = parse_float(previous_row.get("adjusted_close"))
        if previous_close is None:
            previous_close = parse_float(previous_row.get("close"))

    change_value = parse_float(current_row.get("change"))
    change_percent_value = parse_float(current_row.get("change_p"))
    if change_percent_value is None:
        change_percent_value = parse_float(current_row.get("change_percent"))

    if previous_close is not None:
        computed_change, computed_change_percent = compute_change(current_close, previous_close)
        if change_value is None:
            change_value = computed_change
        if change_percent_value is None:
            change_percent_value = computed_change_percent

    if change_value is None or change_percent_value is None:
        raise RuntimeError("EODHD response missing usable change data")

    return {
        "symbol": series_code,
        "name": metadata["name"],
        "close": round(current_close, 4),
        "change": round(change_value, 4),
        "change_percent": round(change_percent_value, 4),
        "date": current_dt.strftime("%Y-%m-%d"),
    }


def normalize_tpex_index(current_row: dict, previous_row: dict | None) -> dict:
    close_value = parse_float(current_row.get("收市") or current_row.get("close"))
    change_value = parse_float(current_row.get("漲跌") or current_row.get("change"))
    if close_value is None:
        raise RuntimeError("TPEx payload is missing close")
    if change_value is None and previous_row:
        previous_close = parse_float(previous_row.get("收市") or previous_row.get("close"))
        change_value, _ = compute_change(close_value, previous_close)
    previous_close = None
    if previous_row:
        previous_close = parse_float(previous_row.get("收市") or previous_row.get("close"))
    change_percent = parse_float(current_row.get("漲跌幅") or current_row.get("change_percent"))
    if change_percent is None:
        if previous_close is None and change_value is not None and close_value != change_value:
            previous_close = close_value - change_value
        _, change_percent = compute_change(close_value, previous_close)
    return {
        "symbol": TPEX_PRICE_INDEX_SYMBOL,
        "name": TPEX_PRICE_INDEX_NAME,
        "close": round(close_value, 4),
        "change": round(change_value, 4) if change_value is not None else None,
        "change_percent": change_percent,
    }


def fetch_twse_index_block(token: str, report_date: str) -> dict | None:
    def fetch_last_taiex_for_day(day: str) -> tuple[str, float] | None:
        rows = finmind_get(
            "TaiwanVariousIndicators5Seconds",
            token,
            start_date=day,
        )
        if not rows:
            return None
        parsed = []
        for row in rows:
            if "TAIEX" not in row:
                continue
            row_dt = parse_date(str(row["date"]))
            parsed.append((row_dt, float(row["TAIEX"])))
        if not parsed:
            return None
        parsed.sort(key=lambda item: item[0])
        last_dt, last_value = parsed[-1]
        return last_dt.strftime("%Y-%m-%d"), last_value

    current = fetch_last_taiex_for_day(report_date)
    if not current:
        return None

    previous = None
    for offset in range(1, 8):
        candidate_day = shift_calendar_day(report_date, -offset)
        previous = fetch_last_taiex_for_day(candidate_day)
        if previous:
            break

    current_date, current_close = current
    previous_close = previous[1] if previous else None
    change, change_pct = compute_change(current_close, previous_close)
    return {
        "date": current_date,
        "close": round(current_close, 4),
        "change": change,
        "change_percent": change_pct,
    }


def fetch_tpex_index_block(report_date: str, payload: dict) -> dict | None:
    body, debug = fetch_text(
        TPEX_INDEX_URL,
        params={"response": "data"},
        accept="text/csv, text/plain, */*",
        insecure_ssl=True,
    )
    record_request_debug(
        payload,
        provider="tpex",
        url=debug["url"],
        status_code=debug["status_code"],
        response_excerpt=debug["response_excerpt"],
        notes="Official TPEx OTC index dataset fetched with TPEx-specific relaxed TLS context.",
    )
    rows = list(csv.DictReader(io.StringIO(body)))
    if not rows:
        raise RuntimeError("TPEx payload is empty")

    eligible_rows = []
    current_row = None
    for row in rows:
        raw_date = str(row.get("資料日期") or "").strip()
        if len(raw_date) != 8:
            continue
        row_date = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}"
        row["_report_date"] = row_date
        if row_date <= report_date:
            eligible_rows.append(row)
    if current_row is None:
        if not eligible_rows:
            raise RuntimeError("TPEx payload has no row on or before report date")
        current_row = eligible_rows[-1]
    previous_row = eligible_rows[-2] if len(eligible_rows) >= 2 else None
    if current_row is None:
        raise RuntimeError("TPEx payload has no row on or before report date")
    normalized = normalize_tpex_index(current_row, previous_row)
    normalized["date"] = current_row["_report_date"]
    return normalized


def fetch_finmind_block(report_date: str, payload: dict) -> tuple[dict, dict, dict, dict]:
    token = os.getenv("FINMIND_API_TOKEN")
    if not token:
        raise RuntimeError("FINMIND_API_TOKEN is not set")

    start_date = window_start(report_date, 20)
    institutional_rows = finmind_get(
        "TaiwanStockTotalInstitutionalInvestors",
        token,
        start_date=start_date,
        end_date=report_date,
    )
    fx_rows = finmind_get(
        "TaiwanExchangeRate",
        token,
        data_id="USD",
        start_date=start_date,
    )
    gold_rows = finmind_get(
        "GoldPrice",
        token,
        start_date=start_date,
        end_date=report_date,
    )
    crude_rows = finmind_get(
        "CrudeOilPrices",
        token,
        data_id="WTI",
        start_date=start_date,
        end_date=report_date,
    )

    twse_index = fetch_twse_index_block(token, report_date)
    fx_current = latest_row_on_or_before(fx_rows, report_date)
    gold_current = latest_row_on_or_before(gold_rows, report_date)
    crude_current = latest_row_on_or_before(crude_rows, report_date)

    if not twse_index:
        raise RuntimeError("FinMind TWSE index data is unavailable")

    taiwan_market = {
        "date": twse_index["date"],
        "twse_index": {
            "close": twse_index["close"],
            "change": twse_index["change"],
            "change_percent": twse_index["change_percent"],
        },
        "tpex_index": None,
        "market_breadth": None,
    }

    date_rows = [row for row in institutional_rows if str(row.get("date"))[:10] == report_date]
    if not date_rows:
        latest_institutional_date = max((str(row.get("date"))[:10] for row in institutional_rows), default=None)
        if latest_institutional_date:
            date_rows = [row for row in institutional_rows if str(row.get("date"))[:10] == latest_institutional_date]
        else:
            raise RuntimeError("FinMind institutional investors data is unavailable")

    net_by_name = {}
    used_date = str(date_rows[0]["date"])[:10]
    for row in date_rows:
        name = str(row.get("name"))
        buy = float(row.get("buy", 0) or 0)
        sell = float(row.get("sell", 0) or 0)
        net_by_name[name] = buy - sell

    foreign_net = net_by_name.get("Foreign_Investor", 0.0) + net_by_name.get("Foreign_Dealer_Self", 0.0)
    dealer_net = net_by_name.get("Dealer_self", 0.0) + net_by_name.get("Dealer_Hedging", 0.0)
    investment_trust_net = net_by_name.get("Investment_Trust", 0.0)
    three_total_net = foreign_net + investment_trust_net + dealer_net

    taiwan_institutional = {
        "date": used_date,
        "foreign_investors_net_buy_sell": round(foreign_net / 100_000_000, 4),
        "investment_trust_net_buy_sell": round(investment_trust_net / 100_000_000, 4),
        "dealer_net_buy_sell": round(dealer_net / 100_000_000, 4),
        "three_institutions_net_buy_sell": round(three_total_net / 100_000_000, 4),
        "unit": "新台幣億元",
    }

    if not fx_current:
        raise RuntimeError("FinMind USD/TWD data is unavailable")
    fx_previous = prior_row_before(fx_rows, fx_current)
    fx_close = compute_fx_close(fx_current)
    fx_prev_close = compute_fx_close(fx_previous) if fx_previous else None
    if fx_close is None:
        raise RuntimeError("FinMind USD/TWD data has no usable close price")
    fx_change, fx_change_pct = compute_change(fx_close, fx_prev_close)
    fx = {
        "date": str(fx_current["date"])[:10],
        "usd_twd": {
            "close": fx_close,
            "change": fx_change,
            "change_percent": fx_change_pct,
        },
    }

    if not gold_current or not crude_current:
        raise RuntimeError("FinMind commodity data is incomplete")

    gold_previous = prior_row_before(gold_rows, gold_current)
    crude_previous = prior_row_before(crude_rows, crude_current)

    gold_close = float(gold_current["Price"])
    gold_prev_close = float(gold_previous["Price"]) if gold_previous else None
    gold_change, gold_change_pct = compute_change(gold_close, gold_prev_close)

    crude_close = float(crude_current["price"])
    crude_prev_close = float(crude_previous["price"]) if crude_previous else None
    crude_change, crude_change_pct = compute_change(crude_close, crude_prev_close)

    commodities = {
        "date": max(str(gold_current["date"])[:10], str(crude_current["date"])[:10]),
        "gold": {
            "close": round(gold_close, 4),
            "change": gold_change,
            "change_percent": gold_change_pct,
            "unit": "USD/oz",
        },
        "crude_oil": {
            "close": round(crude_close, 4),
            "change": crude_change,
            "change_percent": crude_change_pct,
            "unit": "USD/bbl",
        },
    }

    return taiwan_market, taiwan_institutional, fx, commodities


def apply_tpex_index_to_payload(report_date: str, payload: dict) -> None:
    tpex_index = fetch_tpex_index_block(report_date, payload)
    payload["taiwan_market"]["tpex_index"] = {
        "symbol": tpex_index["symbol"],
        "name": tpex_index["name"],
        "close": tpex_index["close"],
        "change": tpex_index["change"],
        "change_percent": tpex_index["change_percent"],
    }
    current_date = payload["taiwan_market"].get("date")
    payload["taiwan_market"]["date"] = (
        max(current_date, tpex_index["date"]) if current_date else tpex_index["date"]
    )
    payload["meta"]["source_status"]["tpex"] = {"status": "ok", "message": "Fetched successfully"}
    add_source_used(payload, "tpex")
    set_provider_for_field(payload, "taiwan_market.tpex_index", "tpex")


def load_symbol_map(path: Path = SYMBOL_MAP_PATH) -> dict:
    with open(path, "r", encoding="utf-8") as file:
        return json.load(file)


def fmp_symbol_for_entry(metadata: dict) -> str:
    provider_symbols = metadata.get("provider_symbols") or {}
    return provider_symbols.get("fmp") or metadata["symbol"]


def eodhd_symbol_for_entry(metadata: dict) -> str:
    provider_symbols = metadata.get("provider_symbols") or {}
    return provider_symbols.get("eodhd") or metadata["symbol"]


def fetch_fmp_quote(api_key: str, symbol: str) -> tuple[list[dict], dict]:
    raw, debug = fetch_json(
        FMP_QUOTE_URL,
        {
            "symbol": symbol,
            "apikey": api_key,
        },
    )
    if not isinstance(raw, list):
        raise RuntimeError(f"Unexpected FMP quote response: {clip_text(json.dumps(raw, ensure_ascii=False))}")
    return raw, debug


def fetch_fmp_historical_eod_full(api_key: str, symbol: str) -> tuple[list[dict], dict]:
    raw, debug = fetch_json(
        FMP_HISTORICAL_EOD_FULL_URL,
        {
            "symbol": symbol,
            "apikey": api_key,
        },
    )
    if isinstance(raw, dict):
        historical = raw.get("historical") or raw.get("data")
        if isinstance(historical, list):
            return historical, debug
    if isinstance(raw, list):
        return raw, debug
    raise RuntimeError(f"Unexpected FMP historical response: {clip_text(json.dumps(raw, ensure_ascii=False))}")


def fetch_asia_index_series(series_code: str, metadata: dict, payload: dict) -> tuple[dict | None, str | None, bool, str | None]:
    eodhd_api_key = os.getenv("EODHD_API_KEY")
    eodhd_symbol = eodhd_symbol_for_entry(metadata)

    if not eodhd_api_key:
        error_message = "EODHD_API_KEY is not set"
        record_request_debug(
            payload,
            provider="eodhd",
            url="",
            status_code=None,
            response_excerpt="",
            selected_symbol=eodhd_symbol,
            notes=f"Series {series_code} failed because EODHD_API_KEY is not configured.",
        )
        return None, None, False, error_message

    try:
        raw, debug = fetch_json(
            f"{EODHD_EOD_URL}/{eodhd_symbol}",
            {
                "api_token": eodhd_api_key,
                "fmt": "json",
                "period": "d",
                "from": window_start(payload["report_date"], 10),
                "to": payload["report_date"],
            },
        )
        if not isinstance(raw, list):
            raise RuntimeError(f"Unexpected EODHD historical response: {clip_text(json.dumps(raw, ensure_ascii=False))}")
        record_request_debug(
            payload,
            provider="eodhd",
            url=debug["url"],
            status_code=debug["status_code"],
            response_excerpt=debug["response_excerpt"],
            selected_symbol=eodhd_symbol,
        )
        normalized = normalize_index_series_from_eodhd(
            series_code,
            metadata,
            raw,
            payload["report_date"],
        )
        return normalized, "eodhd", False, eodhd_symbol
    except Exception as exc:
        record_request_debug(
            payload,
            provider="eodhd",
            url="",
            status_code=None,
            response_excerpt="",
            selected_symbol=eodhd_symbol,
            rejected_candidates=[{"symbol": eodhd_symbol, "reason": str(exc)}],
            notes=f"Series {series_code} failed on EODHD historical EOD fetch.",
        )
        return None, None, False, str(exc)


def fetch_us_index_series(series_code: str, metadata: dict, payload: dict) -> tuple[dict | None, str | None, bool, str | None]:
    fmp_api_key = os.getenv("FMP_API_KEY")
    fmp_symbol = fmp_symbol_for_entry(metadata)
    rejected_candidates = []

    if not fmp_api_key:
        error_message = "FMP_API_KEY is not set"
        record_request_debug(
            payload,
            provider="fmp",
            url="",
            status_code=None,
            response_excerpt="",
            selected_symbol=fmp_symbol,
            rejected_candidates=rejected_candidates,
            notes=f"Series {series_code} failed because FMP_API_KEY is not configured.",
        )
        return None, None, False, error_message

    try:
        quote_raw, quote_debug = fetch_fmp_quote(fmp_api_key, fmp_symbol)
        record_request_debug(
            payload,
            provider="fmp",
            url=quote_debug["url"],
            status_code=quote_debug["status_code"],
            response_excerpt=quote_debug["response_excerpt"],
            selected_symbol=fmp_symbol,
        )
        normalized = normalize_index_series_from_fmp(
            series_code,
            metadata,
            quote_raw=quote_raw,
        )
        return normalized, "fmp", False, fmp_symbol
    except Exception as exc:
        rejected_candidates.append({"symbol": fmp_symbol, "reason": f"quote failed: {exc}"})

    try:
        historical_raw, historical_debug = fetch_fmp_historical_eod_full(fmp_api_key, fmp_symbol)
        record_request_debug(
            payload,
            provider="fmp",
            url=historical_debug["url"],
            status_code=historical_debug["status_code"],
            response_excerpt=historical_debug["response_excerpt"],
            selected_symbol=fmp_symbol,
            rejected_candidates=rejected_candidates,
            notes="Used FMP historical EOD fallback after quote failure.",
        )
        normalized = normalize_index_series_from_fmp(
            series_code,
            metadata,
            historical_raw=historical_raw,
        )
        return normalized, "fmp", True, fmp_symbol
    except Exception as exc:
        rejected_candidates.append({"symbol": fmp_symbol, "reason": f"historical failed: {exc}"})

    error_message = "; ".join(item["reason"] for item in rejected_candidates) or "No provider returned usable data"
    record_request_debug(
        payload,
        provider="fmp",
        url="",
        status_code=None,
        response_excerpt="",
        selected_symbol=fmp_symbol,
        rejected_candidates=rejected_candidates,
        notes=f"Series {series_code} failed across FMP quote and historical endpoints.",
    )
    return None, None, False, error_message


def fetch_index_block(report_date: str, payload: dict) -> tuple[dict, dict]:
    symbol_map = load_symbol_map()
    us_market = {"date": report_date, "indices": []}
    asia_market = {"date": report_date, "indices": []}
    missing_codes = []

    for series_code, metadata in (symbol_map.get("us_market") or {}).items():
        index_item, provider_used, fallback_used, detail = fetch_us_index_series(series_code, metadata, payload)
        if index_item:
            us_market["indices"].append(index_item)
            set_index_status(
                payload,
                "us_market",
                series_code,
                status="fallback" if fallback_used else "ok",
                provider_used=provider_used,
                fallback_used=fallback_used,
                selected_symbol=detail,
            )
            set_provider_for_field(payload, f"us_market.indices.{series_code}", provider_used)
            add_source_used(payload, provider_used)
        else:
            missing_codes.append(f"us_market.indices.{series_code}")
            set_index_status(
                payload,
                "us_market",
                series_code,
                status="error",
                provider_used=None,
                fallback_used=False,
                selected_symbol=None,
                error_category=categorize_index_error(detail),
                message=detail,
            )

    for series_code in ASIA_INDEX_METADATA:
        asia_metadata = ((symbol_map.get("asia_market") or {}).get(series_code)) or {
            "symbol": series_code,
            "name": ASIA_INDEX_METADATA[series_code]["name"],
            "provider_symbols": {},
        }
        index_item, provider_used, fallback_used, detail = fetch_asia_index_series(series_code, asia_metadata, payload)
        if index_item:
            asia_market["indices"].append(index_item)
            asia_market["date"] = max(asia_market["date"], index_item["date"])
            set_index_status(
                payload,
                "asia_market",
                series_code,
                status="fallback" if fallback_used else "ok",
                provider_used=provider_used,
                fallback_used=fallback_used,
                selected_symbol=detail,
            )
            set_provider_for_field(payload, f"asia_market.indices.{series_code}", provider_used)
            add_source_used(payload, provider_used)
        else:
            missing_codes.append(f"asia_market.indices.{series_code}")
            set_index_status(
                payload,
                "asia_market",
                series_code,
                status="error",
                provider_used=None,
                fallback_used=False,
                selected_symbol=None,
                error_category=categorize_index_error(detail),
                message=detail,
            )

    payload["meta"]["missing_sections"].extend(missing_codes)
    return us_market, asia_market


def fetch_marketaux_block(report_date: str) -> list[dict]:
    api_key = os.getenv("MARKETAUX_API_KEY")
    if not api_key:
        raise RuntimeError("MARKETAUX_API_KEY is not set")

    raw, _ = fetch_json(
        MARKETAUX_API_URL,
        {
            "api_token": api_key,
            "language": "en",
            "countries": "us,tw",
            "limit": 3,
            "published_after": f"{report_date}T00:00:00",
        },
    )
    data = raw.get("data") or []
    items = []
    for item in data[:3]:
        items.append(
            {
                "date": (item.get("published_at") or report_date)[:10],
                "source": item.get("source") or "Marketaux",
                "title": item.get("title"),
                "summary": item.get("description"),
                "summary_text": item.get("description"),
                "brief": item.get("description"),
                "url": item.get("url") or item.get("source_url"),
            }
        )
    return items


def fetch_treasury_block(report_date: str) -> dict:
    xml_text, _ = fetch_text(
        TREASURY_XML_URL,
        params={
            "data": "daily_treasury_yield_curve",
            "field_tdr_date_value_month": datetime.strptime(report_date, "%Y-%m-%d").strftime("%Y%m"),
        },
    )
    root = ET.fromstring(xml_text)
    target = datetime.strptime(report_date, "%Y-%m-%d").date()
    matched_row = None
    matched_date = None

    def parse_treasury_date(raw_date: str) -> date | None:
        if not raw_date:
            return None
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%m/%d/%Y"):
            try:
                return datetime.strptime(raw_date, fmt).date()
            except ValueError:
                continue
        return None

    def flatten_element(element: ET.Element) -> dict:
        record = {}
        for child in element.iter():
            if child is element:
                continue
            if list(child):
                continue
            tag = child.tag.split("}", 1)[-1]
            record[tag] = (child.text or "").strip()
        return record

    root_tag = root.tag.split("}", 1)[-1]
    records = []
    top_level_tags = [child.tag.split("}", 1)[-1] for child in list(root)[:10]]

    for entry in root.findall(".//{*}entry"):
        record = flatten_element(entry)
        if record:
            records.append(record)

    if not records:
        for content in root.findall(".//{*}content"):
            record = flatten_element(content)
            if record:
                records.append(record)

    for row in records:
        raw_date = row.get("NEW_DATE") or row.get("BC_DT") or row.get("DATE") or row.get("Date") or row.get("date")
        row_date = parse_treasury_date(raw_date or "")
        if row_date is None:
            continue
        if row_date <= target and (matched_date is None or row_date > matched_date):
            matched_date = row_date
            matched_row = row

    if not matched_row:
        available_keys = sorted({key for row in records[:5] for key in row.keys()})
        sample_dates = []
        for row in records[:5]:
            sample_dates.append(
                {
                    "NEW_DATE": row.get("NEW_DATE"),
                    "Date": row.get("Date"),
                    "date": row.get("date"),
                }
            )
        raise RuntimeError(
            "Treasury XML returned no matching date"
            + (f"; sample keys: {', '.join(available_keys)}" if available_keys else "")
            + (f"; sample dates: {sample_dates}" if sample_dates else "")
            + (f"; root tag: {root_tag}; top-level tags: {top_level_tags}; record count: {len(records)}")
        )

    try:
        yield_2y = float(matched_row["BC_2YEAR"])
        yield_10y = float(matched_row["BC_10YEAR"])
        yield_30y = float(matched_row["BC_30YEAR"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("Treasury XML did not provide complete 2Y/10Y/30Y data") from exc

    slope = yield_10y - yield_2y
    return {
        "date": matched_date.strftime("%Y-%m-%d"),
        "yield_2y": round(yield_2y, 4),
        "yield_10y": round(yield_10y, 4),
        "yield_30y": round(yield_30y, 4),
        "slope_10y_2y": round(slope, 4),
        "unit": "percent",
    }


def load_mock_payload() -> dict:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    mock_path = os.path.join(root, "data", "raw", "mock_morning.json")
    with open(mock_path, "r", encoding="utf-8-sig") as file:
        return json.load(file)


def save_payload_artifacts(report_date: str, payload: dict) -> None:
    serialized = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    Path("out.json").write_text(serialized, encoding="utf-8-sig")
    PAYLOADS_DIR.mkdir(parents=True, exist_ok=True)
    (PAYLOADS_DIR / f"{report_date}.json").write_text(serialized, encoding="utf-8-sig")


def finalize_payload_meta(payload: dict) -> None:
    missing = list(payload["meta"]["missing_sections"])
    if payload["taiwan_market"].get("tpex_index") is None and "taiwan_market.tpex_index" not in missing:
        missing.append("taiwan_market.tpex_index")
    if not payload["us_market"]["indices"] and "us_market.indices" not in missing:
        missing.append("us_market.indices")
    if not payload["asia_market"]["indices"] and "asia_market.indices" not in missing:
        missing.append("asia_market.indices")
    payload["meta"]["missing_sections"] = missing
    payload["meta"]["partial_success"] = bool(missing)


def finalize_source_status(payload: dict) -> None:
    source_status = payload["meta"]["source_status"]

    if "tpex" not in source_status:
        source_status["tpex"] = {
            "status": "ok" if payload["taiwan_market"].get("tpex_index") else "error",
            "message": "Fetched successfully" if payload["taiwan_market"].get("tpex_index") else "No TPEx value returned",
        }

    us_total = len(payload["meta"]["index_status"]["us_market"])
    us_ok = sum(1 for item in payload["meta"]["index_status"]["us_market"].values() if item["status"] != "error")
    asia_total = len(payload["meta"]["index_status"]["asia_market"])
    asia_ok = sum(1 for item in payload["meta"]["index_status"]["asia_market"].values() if item["status"] != "error")

    if us_total:
        us_status = "ok" if us_ok == us_total else "partial" if us_ok > 0 else "error"
        if us_status == "error":
            us_message = f"Resolved 0/{us_total} U.S. indices; all FMP attempts failed"
        else:
            us_message = f"Fetched {us_ok}/{us_total} U.S. indices through FMP"
        source_status["fmp"] = {
            "status": us_status,
            "message": us_message,
        }

    if asia_total:
        source_status["asia_indices"] = {
            "status": "ok" if asia_ok == asia_total else "partial" if asia_ok > 0 else "error",
            "message": (
                f"Resolved {asia_ok}/{asia_total} Asia indices through EODHD"
                if asia_ok > 0
                else f"Resolved 0/{asia_total} Asia indices through EODHD"
            ),
        }


def main() -> int:
    args = parse_args()
    report_date = validate_date(args.date)
    payload = build_base_payload(report_date)

    if os.getenv("MORNING_REPORT_USE_LIVE") != "1":
        mock_payload = load_mock_payload()
        mock_payload["report_date"] = report_date
        mock_payload.setdefault("meta", {})
        mock_payload["meta"]["generated_at"] = now_iso()
        mock_payload["meta"]["mode"] = "mock"
        mock_payload["meta"].setdefault("source_status", {})
        mock_payload["meta"].setdefault("sources_used", ["finmind", "tpex", "fmp", "marketaux", "treasury"])
        mock_payload["meta"].setdefault("partial_success", False)
        mock_payload["meta"].setdefault("missing_sections", [])
        mock_payload["meta"].setdefault("provider_by_field", {})
        mock_payload["meta"].setdefault("index_status", {"us_market": {}, "asia_market": {}})
        mock_payload["meta"].setdefault("request_debug", [])
        save_payload_artifacts(report_date, mock_payload)
        print(json.dumps(mock_payload, ensure_ascii=False, indent=2))
        return 0

    payload["meta"]["mode"] = "live"

    try:
        (
            payload["taiwan_market"],
            payload["taiwan_institutional"],
            payload["fx"],
            payload["commodities"],
        ) = fetch_finmind_block(report_date, payload)
        payload["meta"]["source_status"]["finmind"] = {"status": "ok", "message": "Fetched successfully"}
        add_source_used(payload, "finmind")
    except Exception as exc:
        payload["meta"]["source_status"]["finmind"] = {"status": "error", "message": str(exc)}

    try:
        apply_tpex_index_to_payload(report_date, payload)
    except Exception as exc:
        payload["meta"]["source_status"]["tpex"] = {"status": "error", "message": str(exc)}
        record_request_debug(
            payload,
            provider="tpex",
            url=TPEX_INDEX_URL,
            status_code=None,
            response_excerpt="",
            notes=f"Failed to build TPEx index: {exc}",
        )

    try:
        payload["us_market"], payload["asia_market"] = fetch_index_block(report_date, payload)
    except Exception as exc:
        payload["meta"]["source_status"]["asia_indices"] = {"status": "error", "message": str(exc)}

    try:
        payload["news"] = fetch_marketaux_block(report_date)
        message = "Fetched successfully"
        if len(payload["news"]) < 3:
            message = f"Fetched {len(payload['news'])} news items"
        payload["meta"]["source_status"]["marketaux"] = {"status": "ok", "message": message}
        add_source_used(payload, "marketaux")
    except Exception as exc:
        payload["meta"]["source_status"]["marketaux"] = {"status": "error", "message": str(exc)}

    try:
        payload["bonds"] = fetch_treasury_block(report_date)
        payload["meta"]["source_status"]["treasury"] = {"status": "ok", "message": "Fetched successfully"}
        add_source_used(payload, "treasury")
    except Exception as exc:
        payload["meta"]["source_status"]["treasury"] = {"status": "error", "message": str(exc)}

    finalize_payload_meta(payload)
    finalize_source_status(payload)
    save_payload_artifacts(report_date, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
