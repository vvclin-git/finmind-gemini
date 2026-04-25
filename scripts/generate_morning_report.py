import argparse
import json
import subprocess
import sys
from pathlib import Path

try:
    import render_morning_report as rmr
except ImportError:  # pragma: no cover - used when imported as a package in tests
    from scripts import render_morning_report as rmr


DEFAULT_PAYLOADS_DIR = Path("payloads")
DEFAULT_REPORTS_DIR = Path("reports")
FETCHER_PATH = Path(__file__).with_name("fetch_morning_data.py")
SUMMARY_REQUIRED_KEYS = {f"section_{index}" for index in range(1, 7)} | {"final_summary"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch, render, summarize with Gemini, and finalize the morning markdown report."
    )
    parser.add_argument("--date", required=True, help="Report date in YYYY-MM-DD format.")
    parser.add_argument("--input", dest="input_path", help="Optional existing JSON payload path.")
    parser.add_argument(
        "--output",
        dest="output_path",
        help="Optional final markdown output path. Defaults to reports/morning-<date>.md",
    )
    parser.add_argument(
        "--summaries-output",
        dest="summaries_output_path",
        help="Optional Gemini summaries JSON output path. Defaults to reports/morning-<date>.summaries.json",
    )
    parser.add_argument(
        "--template-output",
        dest="template_output_path",
        help="Optional rendered template output path. Defaults to reports/morning-<date>.template.md",
    )
    return parser.parse_args()


def payload_path_for(report_date: str, explicit_path: str | None) -> Path:
    if explicit_path:
        return Path(explicit_path)
    return DEFAULT_PAYLOADS_DIR / f"{report_date}.json"


def report_output_path_for(report_date: str, explicit_path: str | None) -> Path:
    if explicit_path:
        return Path(explicit_path)
    return DEFAULT_REPORTS_DIR / f"morning-{report_date}.md"


def template_output_path_for(report_date: str, explicit_path: str | None) -> Path:
    if explicit_path:
        return Path(explicit_path)
    return DEFAULT_REPORTS_DIR / f"morning-{report_date}.template.md"


def summaries_output_path_for(report_date: str, explicit_path: str | None) -> Path:
    if explicit_path:
        return Path(explicit_path)
    return DEFAULT_REPORTS_DIR / f"morning-{report_date}.summaries.json"


def run_fetcher(report_date: str) -> None:
    subprocess.run(
        [sys.executable, str(FETCHER_PATH), "--date", report_date],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )


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

    raise RuntimeError("Gemini did not return the required summary JSON object")


def build_summary_input(template_markdown: str) -> str:
    schema = {
        "section_1": "one short Traditional Chinese paragraph",
        "section_2": "one short Traditional Chinese paragraph",
        "section_3": "one short Traditional Chinese paragraph",
        "section_4": "one short Traditional Chinese paragraph",
        "section_5": "one short Traditional Chinese paragraph",
        "section_6": "one short Traditional Chinese paragraph",
        "final_summary": [
            "Traditional Chinese bullet text 1",
            "Traditional Chinese bullet text 2",
            "Traditional Chinese bullet text 3",
        ],
    }
    return "\n".join(
        [
            "You are given a deterministic markdown market report template.",
            "The tables and links are source-of-truth and must not be changed.",
            "Generate summary text only from facts already present in the markdown below.",
            "Requirements:",
            "- Return exactly one JSON object and nothing else.",
            "- Use keys section_1 through section_6 plus final_summary.",
            "- section_1 through section_6 must each be one short Traditional Chinese paragraph.",
            "- final_summary must be a list of 3 to 5 Traditional Chinese bullet strings.",
            "- Do not add any number, date, link, source, provider, cause, or news fact that is not already in the markdown.",
            "- Do not wrap the JSON in markdown fences.",
            "JSON schema example:",
            json.dumps(schema, ensure_ascii=False, indent=2),
            "",
            "Markdown template:",
            template_markdown,
        ]
    )


def call_gemini_for_summaries(template_markdown: str) -> tuple[dict, str]:
    prompt = "Return only the requested summary JSON object."
    input_text = build_summary_input(template_markdown)
    try:
        proc = subprocess.run(
            ["cmd", "/c", "gemini", "-p", prompt, "--output-format", "text"],
            check=True,
            input=input_text,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        stdout = (exc.stdout or "").strip()
        details = stderr or stdout or str(exc)
        raise RuntimeError(f"Gemini summary generation failed: {details}") from exc
    raw_output = proc.stdout.strip()
    summaries = extract_json_object(raw_output)
    return summaries, raw_output


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8-sig")


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8-sig")


def main() -> int:
    args = parse_args()
    payload_path = payload_path_for(args.date, args.input_path)
    if not payload_path.exists():
        run_fetcher(args.date)

    payload = rmr.read_json_file(payload_path)
    template_markdown = rmr.render_report(payload)

    template_output_path = template_output_path_for(args.date, args.template_output_path)
    summaries_output_path = summaries_output_path_for(args.date, args.summaries_output_path)
    final_output_path = report_output_path_for(args.date, args.output_path)

    write_text(template_output_path, template_markdown)
    summaries, _ = call_gemini_for_summaries(template_markdown)
    write_json(summaries_output_path, summaries)

    final_markdown = rmr.apply_summaries(template_markdown, summaries)
    write_text(final_output_path, final_markdown)
    print(final_markdown, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
