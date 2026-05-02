import argparse
import html
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

try:
    import generate_morning_report as gmr
except ImportError:  # pragma: no cover - used when imported as a package in tests
    from scripts import generate_morning_report as gmr


ROOT = Path(__file__).resolve().parents[1]
INDEX_PATH = ROOT / "index.html"
ARCHIVE_DIR = ROOT / "archive"
ARCHIVE_INDEX_PATH = ARCHIVE_DIR / "index.html"
REPORTS_DIR = ROOT / "reports"
GENERATE_REPORT_PATH = ROOT / "scripts" / "generate_morning_report.py"
SUMMARY_PROVIDERS = {"none", "gemini", "openai", "grok"}


class PublishError(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate the morning report and publish it to GitHub Pages files."
    )
    parser.add_argument("positional_date", nargs="?", help="Optional report date in YYYY-MM-DD format.")
    parser.add_argument("--date", help="Report date in YYYY-MM-DD format. Defaults to yesterday in Taiwan time.")
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--live", action="store_true", help="Fetch live provider data. This is the default.")
    mode_group.add_argument("--mock", action="store_true", help="Use the checked-in mock payload.")
    parser.add_argument(
        "--summary-provider",
        choices=sorted(SUMMARY_PROVIDERS),
        default="openai",
        help="LLM provider for the top summary. Defaults to openai.",
    )
    parser.add_argument("--summary-model", help="Optional model override for the selected summary provider.")
    parser.add_argument(
        "--no-refresh",
        action="store_true",
        help="Reuse an existing payloads/<date>.json instead of refetching before publishing.",
    )
    return parser.parse_args()


def resolve_report_date(args: argparse.Namespace) -> str:
    if args.positional_date and args.date:
        raise PublishError("Use either positional date or --date, not both")
    report_date = args.date or args.positional_date or gmr.default_report_date()
    try:
        datetime.strptime(report_date, "%Y-%m-%d")
    except ValueError as exc:
        raise PublishError(f"Invalid date format: {report_date}. Expected YYYY-MM-DD.") from exc
    return report_date


def resolve_fetch_mode(args: argparse.Namespace) -> str:
    if args.mock:
        return "mock"
    return "live"


def run_generator(
    *,
    report_date: str,
    fetch_mode: str,
    summary_provider: str,
    summary_model: str | None,
    refresh: bool,
) -> Path:
    output_path = REPORTS_DIR / f"morning-{report_date}.html"
    command = [
        sys.executable,
        str(GENERATE_REPORT_PATH),
        "--date",
        report_date,
        f"--{fetch_mode}",
        "--summary-provider",
        summary_provider,
    ]
    if refresh:
        command.append("--refresh")
    if summary_model:
        command.extend(["--summary-model", summary_model])

    try:
        subprocess.run(command, cwd=ROOT, check=True)
    except subprocess.CalledProcessError as exc:
        raise PublishError(f"Report generation failed with exit code {exc.returncode}") from exc

    if not output_path.exists():
        raise PublishError(f"Generated report was not found: {output_path}")
    return output_path


def publish_report(report_date: str, report_path: Path) -> None:
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    archive_path = ARCHIVE_DIR / f"{report_date}.html"
    shutil.copy2(report_path, INDEX_PATH)
    shutil.copy2(report_path, archive_path)


def archive_report_files() -> list[Path]:
    if not ARCHIVE_DIR.exists():
        return []
    return sorted(
        [path for path in ARCHIVE_DIR.glob("*.html") if path.name != "index.html"],
        key=lambda path: path.stem,
        reverse=True,
    )


def build_archive_index(report_files: list[Path]) -> str:
    links = "\n".join(
        f'      <li><a href="{html.escape(path.name, quote=True)}">{html.escape(path.stem)}</a></li>'
        for path in report_files
    )
    if not links:
        links = '      <li class="muted">No archived reports yet.</li>'
    return "\n".join(
        [
            "<!doctype html>",
            '<html lang="en">',
            "<head>",
            '  <meta charset="utf-8">',
            '  <meta name="viewport" content="width=device-width, initial-scale=1">',
            "  <title>Morning Report Archive</title>",
            "  <style>",
            "    body { margin: 0; background: #f5f5f2; color: #202124; font-family: Arial, sans-serif; line-height: 1.55; }",
            "    main { max-width: 760px; margin: 0 auto; padding: 32px 20px 48px; }",
            "    h1 { font-size: 30px; margin: 0 0 8px; }",
            "    a { color: #0b57d0; text-decoration: none; }",
            "    a:hover { text-decoration: underline; }",
            "    ul { padding-left: 22px; }",
            "    li { margin: 8px 0; }",
            "    .muted { color: #6f7479; }",
            "  </style>",
            "</head>",
            "<body>",
            "  <main>",
            "    <h1>Morning Report Archive</h1>",
            '    <p><a href="../index.html">Latest report</a></p>',
            "    <ul>",
            links,
            "    </ul>",
            "  </main>",
            "</body>",
            "</html>",
            "",
        ]
    )


def rebuild_archive_index() -> None:
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    ARCHIVE_INDEX_PATH.write_text(build_archive_index(archive_report_files()), encoding="utf-8-sig")


def status_line(message: str) -> None:
    print(f"[daily-page] {message}", file=sys.stderr)


def main() -> int:
    args = parse_args()
    report_date = resolve_report_date(args)
    fetch_mode = resolve_fetch_mode(args)
    report_path = run_generator(
        report_date=report_date,
        fetch_mode=fetch_mode,
        summary_provider=args.summary_provider,
        summary_model=args.summary_model,
        refresh=not args.no_refresh,
    )
    publish_report(report_date, report_path)
    rebuild_archive_index()
    status_line(f"published latest report: {INDEX_PATH}")
    status_line(f"published archive report: {ARCHIVE_DIR / f'{report_date}.html'}")
    status_line(f"updated archive index: {ARCHIVE_INDEX_PATH}")
    return 0


def cli_main() -> int:
    try:
        return main()
    except PublishError as exc:
        status_line(f"error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(cli_main())
