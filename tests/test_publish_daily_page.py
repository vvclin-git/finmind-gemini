import argparse
import unittest
from pathlib import Path
from unittest import mock

from scripts import publish_daily_page as pdp


class PublishDailyPageTests(unittest.TestCase):
    def test_resolve_report_date_rejects_conflicting_dates(self) -> None:
        args = argparse.Namespace(positional_date="2026-05-01", date="2026-05-02")

        with self.assertRaisesRegex(pdp.PublishError, "Use either positional date or --date"):
            pdp.resolve_report_date(args)

    def test_resolve_report_date_uses_generator_default(self) -> None:
        args = argparse.Namespace(positional_date=None, date=None)

        with mock.patch.object(pdp.gmr, "default_report_date", return_value="2026-05-01"):
            self.assertEqual(pdp.resolve_report_date(args), "2026-05-01")

    def test_build_archive_index_sorts_newest_first_links(self) -> None:
        html = pdp.build_archive_index(
            [
                Path("archive/2026-05-02.html"),
                Path("archive/2026-05-01.html"),
            ]
        )

        self.assertLess(html.index("2026-05-02"), html.index("2026-05-01"))
        self.assertIn('href="2026-05-02.html"', html)
        self.assertIn('href="../index.html"', html)

    def test_publish_report_copies_latest_and_archive(self) -> None:
        report_path = Path("reports/morning-2026-05-01.html")

        with mock.patch.object(pdp, "INDEX_PATH", Path("index.html")), \
             mock.patch.object(pdp, "ARCHIVE_DIR", Path("archive")), \
             mock.patch.object(type(Path("archive")), "mkdir") as mkdir, \
             mock.patch.object(pdp.shutil, "copy2") as copy2:
            pdp.publish_report("2026-05-01", report_path)

        mkdir.assert_called_once_with(parents=True, exist_ok=True)
        copy2.assert_has_calls(
            [
                mock.call(report_path, Path("index.html")),
                mock.call(report_path, Path("archive/2026-05-01.html")),
            ]
        )


if __name__ == "__main__":
    unittest.main()
