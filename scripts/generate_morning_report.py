import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    import render_morning_report as rmr
except ImportError:  # pragma: no cover - used when imported as a package in tests
    from scripts import render_morning_report as rmr


DEFAULT_PAYLOADS_DIR = Path("payloads")
DEFAULT_REPORTS_DIR = Path("reports")
FETCHER_PATH = Path(__file__).with_name("fetch_morning_data.py")
TAIWAN_TIMEZONE = timezone(timedelta(hours=8))
SUMMARY_REQUIRED_KEYS = {"market_summary"}
GEMINI_API_URL_TEMPLATE = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
OPENAI_API_URL = "https://api.openai.com/v1/responses"
XAI_API_URL = "https://api.x.ai/v1/responses"
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
DEFAULT_OPENAI_MODEL = "gpt-5.4-nano"
DEFAULT_XAI_MODEL = "grok-4-1-fast"
GEMINI_TIMEOUT_SECONDS = 120
OPENAI_TIMEOUT_SECONDS = 120
XAI_TIMEOUT_SECONDS = 120
GEMINI_RETRY_STATUS_CODES = {429, 503}
OPENAI_RETRY_STATUS_CODES = {429, 500, 502, 503, 504}
XAI_RETRY_STATUS_CODES = {429, 500, 502, 503, 504}
GEMINI_RETRY_DELAYS_SECONDS = [2, 5, 10]
OPENAI_RETRY_DELAYS_SECONDS = [2, 5, 10]
XAI_RETRY_DELAYS_SECONDS = [2, 5, 10]
SUMMARY_PROVIDERS = {"none", "gemini", "openai", "grok"}


class MorningReportError(RuntimeError):
    pass


class RetryableGeminiError(MorningReportError):
    pass


class RetryableOpenAIError(MorningReportError):
    pass


class RetryableXAIError(MorningReportError):
    pass


class SummaryUnavailable(MorningReportError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch, render deterministic tables, and finalize the morning HTML report."
    )
    parser.add_argument("positional_date", nargs="?", help="Optional report date in YYYY-MM-DD format.")
    parser.add_argument("--date", help="Report date in YYYY-MM-DD format. Defaults to yesterday in Taiwan time.")
    parser.add_argument("--input", dest="input_path", help="Optional existing JSON payload path.")
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Refetch the default dated payload even when payloads/<date>.json already exists. Cannot be used with --input.",
    )
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--live", action="store_true", help="Fetch live provider data. This is the default.")
    mode_group.add_argument("--mock", action="store_true", help="Use the checked-in mock payload instead of live providers.")
    parser.add_argument(
        "--output",
        dest="output_path",
        help="Optional final HTML output path. Defaults to reports/morning-<date>.html",
    )
    parser.add_argument(
        "--summaries-output",
        dest="summaries_output_path",
        help="Optional LLM summaries JSON output path. Defaults to reports/morning-<date>.summaries.json when summaries are enabled.",
    )
    parser.add_argument(
        "--summary-provider",
        choices=sorted(SUMMARY_PROVIDERS),
        default=os.getenv("SUMMARY_PROVIDER", "openai"),
        help="LLM provider for section summary text. Defaults to SUMMARY_PROVIDER or openai.",
    )
    parser.add_argument(
        "--summary-model",
        dest="summary_model",
        help="Optional model override. Defaults to GEMINI_MODEL, OPENAI_MODEL, or XAI_MODEL for the selected provider.",
    )
    parser.add_argument(
        "--template-output",
        dest="template_output_path",
        help="Optional rendered template output path. Defaults to reports/morning-<date>.template.html",
    )
    parser.add_argument(
        "--print-report",
        action="store_true",
        help="Print the final HTML report to stdout after writing files. Status is always printed to stderr.",
    )
    return parser.parse_args()


def default_report_date() -> str:
    report_date = datetime.now(TAIWAN_TIMEZONE).date() - timedelta(days=1)
    return report_date.strftime("%Y-%m-%d")


def resolve_report_date(args: argparse.Namespace) -> str:
    positional_date = getattr(args, "positional_date", None)
    option_date = getattr(args, "date", None)
    if not isinstance(positional_date, str):
        positional_date = None
    if not isinstance(option_date, str):
        option_date = None
    if positional_date and option_date:
        raise MorningReportError("Use either positional date or --date, not both")
    return option_date or positional_date or default_report_date()


def payload_path_for(report_date: str, explicit_path: str | None) -> Path:
    if explicit_path:
        return Path(explicit_path)
    return DEFAULT_PAYLOADS_DIR / f"{report_date}.json"


def report_output_path_for(report_date: str, explicit_path: str | None) -> Path:
    if explicit_path:
        return Path(explicit_path)
    return DEFAULT_REPORTS_DIR / f"morning-{report_date}.html"


def template_output_path_for(report_date: str, explicit_path: str | None) -> Path:
    if explicit_path:
        return Path(explicit_path)
    return DEFAULT_REPORTS_DIR / f"morning-{report_date}.template.html"


def summaries_output_path_for(report_date: str, explicit_path: str | None) -> Path:
    if explicit_path:
        return Path(explicit_path)
    return DEFAULT_REPORTS_DIR / f"morning-{report_date}.summaries.json"


def resolve_fetch_mode(args: argparse.Namespace) -> str:
    if getattr(args, "mock", False):
        return "mock"
    return "live"


def run_fetcher(report_date: str, fetch_mode: str) -> None:
    command = [sys.executable, str(FETCHER_PATH), "--date", report_date, f"--{fetch_mode}"]
    try:
        subprocess.run(
            command,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.CalledProcessError as exc:
        details = (exc.stderr or exc.stdout or str(exc)).strip()
        raise MorningReportError(f"Fetcher failed: {details}") from exc


def status_line(message: str) -> None:
    print(f"[morning-report] {message}", file=sys.stderr)


def summarize_source_status(payload: dict) -> list[str]:
    source_status = payload.get("meta", {}).get("source_status", {})
    if not isinstance(source_status, dict) or not source_status:
        return ["sources: no provider status recorded"]

    lines = []
    for provider in sorted(source_status):
        item = source_status[provider]
        if not isinstance(item, dict):
            lines.append(f"{provider}: {item}")
            continue
        status = item.get("status", "unknown")
        message = item.get("message", "")
        lines.append(f"{provider}: {status}" + (f" - {message}" if message else ""))
    return lines


def summarize_missing_sections(payload: dict) -> str:
    missing = payload.get("meta", {}).get("missing_sections", [])
    if not missing:
        return "missing sections: 0"
    return f"missing sections: {len(missing)} - {', '.join(str(item) for item in missing)}"


def result_status(payload: dict) -> str:
    if payload.get("meta", {}).get("partial_success"):
        return "partial"
    return "ok"


def print_status_report(
    *,
    report_date: str,
    payload: dict,
    payload_path: Path,
    template_output_path: Path,
    summary_provider: str,
    summary_status: str,
    final_output_path: Path,
    summaries_output_path: Path | None = None,
) -> None:
    mode = payload.get("meta", {}).get("mode", "unknown")
    status_line(f"date: {report_date}")
    status_line(f"mode: {mode}")
    status_line("fetch status:")
    for line in summarize_source_status(payload):
        status_line(f"  {line}")
    status_line(summarize_missing_sections(payload))
    status_line(summary_status)
    status_line(f"payload: {payload_path}")
    status_line(f"template: {template_output_path}")
    if summaries_output_path:
        status_line(f"summaries: {summaries_output_path}")
    status_line(f"report: {final_output_path}")
    status_line(f"result: {result_status(payload)}")


def remove_summary_placeholders(report_html: str) -> str:
    return rmr.remove_summary_placeholder(report_html)


def looks_like_summary_payload(candidate) -> bool:
    return isinstance(candidate, dict) and SUMMARY_REQUIRED_KEYS.issubset(candidate.keys())


def extract_json_object(text: str) -> dict:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()

    try:
        candidate = json.loads(stripped)
        if looks_like_summary_payload(candidate):
            return candidate
    except json.JSONDecodeError:
        pass

    decoder = json.JSONDecoder()
    for index, char in enumerate(stripped):
        if char != "{":
            continue
        try:
            candidate, _ = decoder.raw_decode(stripped[index:])
        except json.JSONDecodeError:
            continue
        if looks_like_summary_payload(candidate):
            return candidate

    raise RuntimeError("LLM did not return the required summary JSON object")


def build_summary_input(template_html: str) -> str:
    schema = {
        "market_summary": [
            {
                "heading": "美股方面",
                "bullets": [
                    "Traditional Chinese bullet with actual index close and percentage move from the table",
                    "Traditional Chinese bullet with another sourced figure",
                ],
            }
        ],
    }
    return "\n".join(
        [
            "You are given a deterministic HTML market report template.",
            "The tables and links are source-of-truth and must not be changed.",
            "Generate the top market summary only from facts already present in the HTML tables below.",
            "Requirements:",
            "- Return exactly one JSON object and nothing else.",
            "- Use exactly one top-level key: market_summary.",
            "- market_summary must be a list of 4 to 6 objects.",
            "- Each object must have heading and bullets.",
            "- Each heading must be a short Traditional Chinese section name, such as 美股方面, 台股方面, 其他亞股方面, 匯率與商品, 債市觀察, 財經新聞.",
            "- Each bullets field must contain 2 to 5 Traditional Chinese bullet strings.",
            "- Include actual figures from the tables whenever available: closes, point changes, percentage changes, institutional net buy/sell amounts, yield levels, spreads, commodity prices, and FX levels.",
            "- Compare relative strength or divergence only when the required figures are present in the tables.",
            "- Do not add any number, date, link, source, provider, cause, or news fact that is not already in the HTML template.",
            "- Do not output HTML or Markdown.",
            "- Do not wrap the JSON in code fences.",
            "JSON schema example:",
            json.dumps(schema, ensure_ascii=False, indent=2),
            "",
            "HTML template:",
            template_html,
        ]
    )


def extract_gemini_response_text(response_payload: dict) -> str:
    candidates = response_payload.get("candidates")
    if not isinstance(candidates, list):
        return ""

    text_parts = []
    for candidate in candidates:
        parts = candidate.get("content", {}).get("parts", []) if isinstance(candidate, dict) else []
        for part in parts:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                text_parts.append(part["text"])
    return "\n".join(text_parts).strip()


def build_gemini_request(api_key: str, model: str, input_text: str) -> urllib.request.Request:
    request_body = {
        "contents": [
            {
                "role": "user",
                "parts": [{"text": input_text}],
            }
        ]
    }
    request = urllib.request.Request(
        GEMINI_API_URL_TEMPLATE.format(model=model),
        data=json.dumps(request_body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": api_key,
        },
        method="POST",
    )
    return request


def fetch_gemini_response_text(api_key: str, model: str, input_text: str) -> str:
    request = build_gemini_request(api_key, model, input_text)
    try:
        with urllib.request.urlopen(request, timeout=GEMINI_TIMEOUT_SECONDS) as response:
            return response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace").strip() or str(exc)
        error_message = f"Gemini API summary generation failed: {details}"
        if exc.code in GEMINI_RETRY_STATUS_CODES:
            raise RetryableGeminiError(error_message) from exc
        raise SummaryUnavailable(error_message) from exc
    except urllib.error.URLError as exc:
        raise SummaryUnavailable(f"Gemini API summary generation failed: {exc.reason}") from exc


def call_gemini_for_summaries(template_markdown: str, model_override: str | None = None) -> tuple[dict, str]:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise SummaryUnavailable("GEMINI_API_KEY is not set")

    model = model_override or os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)
    input_text = build_summary_input(template_markdown)
    retry_delays = [0] + GEMINI_RETRY_DELAYS_SECONDS
    last_error = None
    for attempt, delay_seconds in enumerate(retry_delays, start=1):
        if delay_seconds:
            status_line(f"gemini retry {attempt}/{len(retry_delays)} after {delay_seconds}s")
            time.sleep(delay_seconds)
        try:
            response_text = fetch_gemini_response_text(api_key, model, input_text)
            break
        except RetryableGeminiError as exc:
            last_error = exc
    else:
        raise SummaryUnavailable(str(last_error)) from last_error

    try:
        response_payload = json.loads(response_text)
    except json.JSONDecodeError as exc:
        raise SummaryUnavailable("Gemini API returned invalid JSON") from exc

    raw_output = extract_gemini_response_text(response_payload)
    if not raw_output:
        raise SummaryUnavailable("Gemini API returned no summary text")

    summaries = extract_json_object(raw_output)
    return summaries, raw_output


def extract_openai_response_text(response_payload: dict) -> str:
    output_text = response_payload.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()

    text_parts = []
    output_items = response_payload.get("output", [])
    if not isinstance(output_items, list):
        return ""
    for item in output_items:
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []):
            if isinstance(content, dict) and isinstance(content.get("text"), str):
                text_parts.append(content["text"])
    return "\n".join(text_parts).strip()


def build_responses_api_request(api_url: str, api_key: str, model: str, input_text: str) -> urllib.request.Request:
    request_body = {
        "model": model,
        "input": input_text,
        "text": {"format": {"type": "text"}},
    }
    return urllib.request.Request(
        api_url,
        data=json.dumps(request_body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )


def build_openai_request(api_key: str, model: str, input_text: str) -> urllib.request.Request:
    return build_responses_api_request(OPENAI_API_URL, api_key, model, input_text)


def fetch_openai_response_text(api_key: str, model: str, input_text: str) -> str:
    request = build_openai_request(api_key, model, input_text)
    try:
        with urllib.request.urlopen(request, timeout=OPENAI_TIMEOUT_SECONDS) as response:
            return response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace").strip() or str(exc)
        error_message = f"OpenAI summary generation failed: {details}"
        if exc.code in OPENAI_RETRY_STATUS_CODES:
            raise RetryableOpenAIError(error_message) from exc
        raise SummaryUnavailable(error_message) from exc
    except urllib.error.URLError as exc:
        raise SummaryUnavailable(f"OpenAI summary generation failed: {exc.reason}") from exc


def call_openai_for_summaries(template_markdown: str, model_override: str | None = None) -> tuple[dict, str]:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise SummaryUnavailable("OPENAI_API_KEY is not set")

    model = model_override or os.getenv("OPENAI_MODEL", DEFAULT_OPENAI_MODEL)
    input_text = build_summary_input(template_markdown)
    retry_delays = [0] + OPENAI_RETRY_DELAYS_SECONDS
    last_error = None
    for attempt, delay_seconds in enumerate(retry_delays, start=1):
        if delay_seconds:
            status_line(f"openai retry {attempt}/{len(retry_delays)} after {delay_seconds}s")
            time.sleep(delay_seconds)
        try:
            response_text = fetch_openai_response_text(api_key, model, input_text)
            break
        except RetryableOpenAIError as exc:
            last_error = exc
    else:
        raise SummaryUnavailable(str(last_error)) from last_error

    try:
        response_payload = json.loads(response_text)
    except json.JSONDecodeError as exc:
        raise SummaryUnavailable("OpenAI returned invalid JSON") from exc

    raw_output = extract_openai_response_text(response_payload)
    if not raw_output:
        raise SummaryUnavailable("OpenAI returned no summary text")

    summaries = extract_json_object(raw_output)
    return summaries, raw_output


def build_xai_request(api_key: str, model: str, input_text: str) -> urllib.request.Request:
    return build_responses_api_request(XAI_API_URL, api_key, model, input_text)


def fetch_xai_response_text(api_key: str, model: str, input_text: str) -> str:
    request = build_xai_request(api_key, model, input_text)
    try:
        with urllib.request.urlopen(request, timeout=XAI_TIMEOUT_SECONDS) as response:
            return response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace").strip() or str(exc)
        error_message = f"Grok summary generation failed: {details}"
        if exc.code in XAI_RETRY_STATUS_CODES:
            raise RetryableXAIError(error_message) from exc
        raise SummaryUnavailable(error_message) from exc
    except urllib.error.URLError as exc:
        raise SummaryUnavailable(f"Grok summary generation failed: {exc.reason}") from exc


def call_xai_for_summaries(template_markdown: str, model_override: str | None = None) -> tuple[dict, str]:
    api_key = os.getenv("XAI_API_KEY")
    if not api_key:
        raise SummaryUnavailable("XAI_API_KEY is not set")

    model = model_override or os.getenv("XAI_MODEL", DEFAULT_XAI_MODEL)
    input_text = build_summary_input(template_markdown)
    retry_delays = [0] + XAI_RETRY_DELAYS_SECONDS
    last_error = None
    for attempt, delay_seconds in enumerate(retry_delays, start=1):
        if delay_seconds:
            status_line(f"grok retry {attempt}/{len(retry_delays)} after {delay_seconds}s")
            time.sleep(delay_seconds)
        try:
            response_text = fetch_xai_response_text(api_key, model, input_text)
            break
        except RetryableXAIError as exc:
            last_error = exc
    else:
        raise SummaryUnavailable(str(last_error)) from last_error

    try:
        response_payload = json.loads(response_text)
    except json.JSONDecodeError as exc:
        raise SummaryUnavailable("Grok returned invalid JSON") from exc

    raw_output = extract_openai_response_text(response_payload)
    if not raw_output:
        raise SummaryUnavailable("Grok returned no summary text")

    summaries = extract_json_object(raw_output)
    return summaries, raw_output


def generate_summaries(summary_provider: str, summary_model: str | None, template_markdown: str) -> tuple[dict | None, str | None]:
    if summary_provider == "none":
        return None, None
    if summary_provider == "gemini":
        return call_gemini_for_summaries(template_markdown, summary_model)
    if summary_provider == "openai":
        return call_openai_for_summaries(template_markdown, summary_model)
    if summary_provider == "grok":
        return call_xai_for_summaries(template_markdown, summary_model)
    raise MorningReportError(f"Unsupported summary provider: {summary_provider}")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8-sig")


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8-sig")


def fallback_summary_status(summary_provider: str, error: SummaryUnavailable) -> str:
    message = str(error)
    if " is not set" in message:
        return f"summaries: skipped ({summary_provider}, missing key)"
    return f"summaries: failed ({summary_provider}) - {message}"


def main() -> int:
    args = parse_args()
    report_date = resolve_report_date(args)
    fetch_mode = resolve_fetch_mode(args)
    if args.summary_provider not in SUMMARY_PROVIDERS:
        raise MorningReportError(f"Unsupported summary provider: {args.summary_provider}")
    payload_path = payload_path_for(report_date, args.input_path)
    if args.input_path and args.refresh:
        raise MorningReportError("--refresh cannot be used with --input")
    if args.input_path and not payload_path.exists():
        raise MorningReportError(f"Input payload does not exist: {payload_path}")
    if not args.input_path and (args.refresh or not payload_path.exists()):
        if args.refresh:
            status_line(f"refresh: refetching {payload_path}")
        run_fetcher(report_date, fetch_mode)

    payload = rmr.read_json_file(payload_path)
    template_report = rmr.render_report(payload)

    template_output_path = template_output_path_for(report_date, args.template_output_path)
    summaries_output_path = summaries_output_path_for(report_date, args.summaries_output_path)
    final_output_path = report_output_path_for(report_date, args.output_path)

    write_text(template_output_path, template_report)

    summary_status = "summaries: disabled"
    try:
        summaries, _ = generate_summaries(args.summary_provider, args.summary_model, template_report)
    except SummaryUnavailable as exc:
        summaries = None
        summary_status = fallback_summary_status(args.summary_provider, exc)
    if summaries is None:
        final_report = remove_summary_placeholders(template_report)
        summaries_output_for_status = None
    else:
        write_json(summaries_output_path, summaries)
        final_report = rmr.apply_summaries(template_report, summaries)
        summaries_output_for_status = summaries_output_path
        summary_status = f"summaries: ok ({args.summary_provider})"
    write_text(final_output_path, final_report)
    print_status_report(
        report_date=report_date,
        payload=payload,
        payload_path=payload_path,
        template_output_path=template_output_path,
        summary_provider=args.summary_provider,
        summary_status=summary_status,
        final_output_path=final_output_path,
        summaries_output_path=summaries_output_for_status,
    )
    if args.print_report:
        print(final_report, end="")
    return 0


def cli_main() -> int:
    try:
        return main()
    except MorningReportError as exc:
        status_line(f"error: {exc}")
        status_line("result: failed")
        return 1


if __name__ == "__main__":
    raise SystemExit(cli_main())
