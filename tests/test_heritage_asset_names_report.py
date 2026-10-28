from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from django.test import SimpleTestCase

from mariner_app import heritage_asset_names_aqs
from tests.heritage_asset_names_report import (
    Comparison,
    MISSING,
    SQL_FIXTURE,
    callable_source,
    file_sha256,
    markdown_name,
    sql_source,
    write_report,
)


class HeritageAssetNamesReportTests(SimpleTestCase):
    def comparison(self, sql_names, aqs_names):
        pk = uuid4()
        return Comparison(
            "example",
            "en",
            [pk],
            [(pk, name) for name in sql_names],
            [(pk, name) for name in aqs_names],
            1.25,
            2.5,
        )

    def test_duplicates_and_unordered_results(self):
        rows = self.comparison(["B", "A", "A"], ["A", "B", "A"]).markdown_rows()
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(row.endswith("| true |") for row in rows))
        self.assertTrue(all(len(row.split("|")[1:-1]) == 7 for row in rows))
        self.assertTrue(all("1.250" not in row and "2.500" not in row for row in rows))

    def test_mismatch_and_missing_duplicate(self):
        rows = self.comparison(["A", "A"], ["B"]).markdown_rows()
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row.endswith("| false |") for row in rows))
        self.assertIn("NO ROW", rows[1])

    def test_null_is_not_absence(self):
        row = self.comparison([None], []).markdown_rows()[0]
        self.assertIn("| NULL | NO ROW |", row)
        self.assertTrue(row.endswith("| false |"))

    def test_both_absent_and_empty_input(self):
        comparison = self.comparison([], [])
        self.assertTrue(comparison.markdown_rows()[0].endswith("| true |"))
        comparison.requested_ids = []
        self.assertIn("EMPTY INPUT", comparison.markdown_rows()[0])

    def test_name_escaping_preserves_table_and_whitespace(self):
        self.assertEqual(markdown_name(None), "NULL")
        self.assertEqual(markdown_name(MISSING), "NO ROW")
        name = markdown_name(" A|\n\t<B>& ")
        self.assertNotIn("|", name)
        self.assertNotIn("\n", name)
        self.assertIn("\\n\\t", name)
        self.assertIn("&lt;B&gt;&amp;", name)
        self.assertIn("&#124;", name)

    def test_report_contains_requested_columns_and_rows(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "report.md"
            write_report(path, [self.comparison(["A"], ["A"])])
            report = path.read_text()
        self.assertIn("SQL resource name | AQS resource name", report)
        self.assertIn("SQL median (ms) | SQL p95 (ms)", report)
        self.assertIn("AQS median (ms) | AQS p95 (ms)", report)
        self.assertIn("Completed comparison requests: 1", report)
        self.assertIn("## Batch performance summary", report)
        self.assertIn("## Resource-name comparisons", report)
        self.assertIn("## Raw measured samples", report)
        self.assertIn("SQL (ms): 1.250000", report)
        self.assertIn("AQS (ms): 2.500000", report)
        self.assertIn("| 1 | 0 |", report)

    def test_benchmark_report_uses_repeated_samples_not_initial_request(self):
        comparison = self.comparison(["A"], ["A"])
        comparison.sql_samples = (1, 2, 100)
        comparison.aqs_samples = (3, 4, 200)
        comparison.warmups = 3
        row = comparison.markdown_summary(1)
        self.assertIn("| 2.000 | 100.000 | 34.333 | 1.000 | 100.000 |", row)
        self.assertIn("| 4.000 | 200.000 | 69.000 | 3.000 | 200.000 |", row)
        self.assertTrue(row.endswith("| 3 | 3 |"))

    def test_summary_has_one_row_per_request_not_per_resource(self):
        first = self.comparison(["A", "A", "B"], ["A", "A", "B"])
        # A resource with no results must still be counted among requested IDs.
        first.requested_ids.extend([first.requested_ids[0], uuid4()])
        second = self.comparison([None], [])
        with TemporaryDirectory() as directory:
            path = Path(directory) / "report.md"
            write_report(path, [first, second])
            report = path.read_text()
        summary, remaining = report.split("## Resource-name comparisons")
        summary = summary.split("## Batch performance summary")[1]
        summary_rows = [line for line in summary.splitlines() if line.startswith("|")]
        self.assertEqual(len(summary_rows), 4)  # Header, separator, two requests.
        self.assertTrue(all(len(row.split("|")[1:-1]) == 18 for row in summary_rows))
        first_cells = [cell.strip() for cell in summary_rows[2].split("|")[1:-1]]
        self.assertEqual(first_cells[:6], ["1", "example", "en", "2", "3", "3"])
        second_cells = [cell.strip() for cell in summary_rows[3].split("|")[1:-1]]
        self.assertEqual(second_cells[:6], ["2", "example", "en", "1", "1", "0"])
        names = remaining.split("## Raw measured samples")[0]
        name_rows = [line for line in names.splitlines() if line.startswith("|")]
        self.assertEqual(len(name_rows), 7)  # Headers plus 4 first/1 second rows.
        self.assertTrue(all(len(row.split("|")[1:-1]) == 7 for row in name_rows))
        self.assertEqual(sum(row.startswith("| 1 |") for row in name_rows), 4)
        self.assertEqual(sum(row.startswith("| 2 |") for row in name_rows), 1)
        self.assertNotIn("median", names)
        self.assertNotIn("p95", names)
        self.assertIn("### Request 1: example (en)", report)
        self.assertIn("### Request 2: example (en)", report)

    def test_empty_batch_summary_has_zero_resources_and_rows(self):
        comparison = self.comparison([], [])
        comparison.requested_ids = []
        cells = [
            cell.strip() for cell in comparison.markdown_summary(7).split("|")[1:-1]
        ]
        self.assertEqual(cells[:6], ["7", "example", "en", "0", "0", "0"])
        self.assertTrue(comparison.markdown_rows(7)[0].startswith("| 7 | example |"))

    def test_raw_samples_keep_outliers_without_repeating_resource_rows(self):
        comparison = self.comparison(["A", "B"], ["B", "A"])
        comparison.sql_samples = (1, 2, 100)
        comparison.aqs_samples = (3, 4, 200)
        with TemporaryDirectory() as directory:
            path = Path(directory) / "report.md"
            write_report(path, [comparison])
            report = path.read_text()
        raw = report.split("## Raw measured samples")[1]
        self.assertEqual(raw.count("### Request "), 1)
        self.assertIn("SQL (ms): 1.000000, 2.000000, 100.000000", raw)
        self.assertIn("AQS (ms): 3.000000, 4.000000, 200.000000", raw)

    def test_callable_source_names_defining_file_and_method(self):
        source = callable_source(
            "Alias AQS", heritage_asset_names_aqs.get_heritage_asset_names
        )
        path, line = source.path.rsplit(":", 1)
        self.assertEqual(path, "mariner_app/heritage_asset_names_aqs.py")
        self.assertGreater(int(line), 0)
        self.assertEqual(
            source.method,
            "mariner_app.heritage_asset_names_aqs.get_heritage_asset_names()",
        )
        self.assertEqual(
            source.sha256, file_sha256(Path(heritage_asset_names_aqs.__file__))
        )

    def test_report_lists_implementation_sources(self):
        sources = [
            sql_source("SQL", "stored function"),
            callable_source("AQS", heritage_asset_names_aqs.get_heritage_asset_names),
        ]
        with TemporaryDirectory() as directory:
            path = Path(directory) / "report.md"
            write_report(path, [self.comparison(["A"], ["A"])], implementations=sources)
            report = path.read_text()
        section = report.split("## Implementations benchmarked")[1].split(
            "Completed comparison requests"
        )[0]
        self.assertIn(
            f"| SQL | `tests/fixtures/heritage_asset_names.sql` | `stored function` "
            f"| `{file_sha256(SQL_FIXTURE)}` |",
            section,
        )
        self.assertIn(
            "`mariner_app.heritage_asset_names_aqs.get_heritage_asset_names()`",
            section,
        )

    def test_report_without_sources_omits_section(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "report.md"
            write_report(path, [self.comparison(["A"], ["A"])])
            report = path.read_text()
        self.assertNotIn("## Implementations benchmarked", report)

    def test_large_batch_groups_each_result_list_once(self):
        class SingleTraversalRows(list):
            traversals = 0

            def __iter__(self):
                self.traversals += 1
                if self.traversals > 1:
                    raise AssertionError("Results rescanned for each resource.")
                return super().__iter__()

        ids = [uuid4() for _ in range(1000)]
        comparison = Comparison(
            "large-batch",
            "en",
            ids,
            SingleTraversalRows((pk, "Named") for pk in ids),
            SingleTraversalRows((pk, "Named") for pk in ids),
            1,
            2,
        )
        rows = comparison.markdown_rows()
        self.assertEqual(len(rows), 1000)
        self.assertTrue(all(row.endswith("| true |") for row in rows))
