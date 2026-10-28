import argparse
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch
from uuid import uuid4

from tempfile import TemporaryDirectory

from django.test import SimpleTestCase

from tests.benchmark_heritage_asset_names import (
    COMPARISON_WORKERS,
    WORKER_PREFIX,
    fresh_measurements,
    fresh_request,
    invocation,
    positive_int,
    parse_args,
    sql_body,
    validate_rows,
)
from tests.heritage_asset_names_benchmark import (
    TimingStats,
    measure_implementations,
    measure_requests,
)
from tests.heritage_asset_names_report import (
    ImplementationBatch,
    ImplementationSource,
    write_implementation_report,
)


class HeritageAssetNamesBenchmarkTests(SimpleTestCase):
    def test_three_warmups_thirty_samples_and_balanced_order(self):
        order = []
        validations = []

        def request(name):
            order.append(name)
            return ["Name"]

        # Two clock reads per request; the first six requests are warm-ups.
        with patch(
            "tests.heritage_asset_names_benchmark.perf_counter",
            side_effect=[value / 1000 for value in range(132)],
        ):
            result = measure_requests(
                lambda: request("sql"),
                lambda: request("aqs"),
                lambda sql, aqs: validations.append((sql, aqs)),
            )
        self.assertEqual(len(validations), 33)
        self.assertEqual(result.warmups, 3)
        self.assertEqual(len(result.sql.samples), 30)
        self.assertEqual(len(result.aqs.samples), 30)
        self.assertEqual(order[:6], ["sql", "aqs", "aqs", "sql", "sql", "aqs"])
        self.assertEqual(order[6:], ["sql", "aqs", "aqs", "sql"] * 15)
        for sample in result.sql.samples + result.aqs.samples:
            self.assertAlmostEqual(sample, 1)

    def test_warmups_are_excluded_but_measured_outliers_are_retained(self):
        # Alternating order: warm SQL/AQS, measured SQL/AQS, measured AQS/SQL.
        clock = [0, 100, 100, 200, 200, 201, 201, 203, 203, 206, 206, 306]
        with patch(
            "tests.heritage_asset_names_benchmark.perf_counter", side_effect=clock
        ):
            result = measure_requests(
                lambda: [],
                lambda: [],
                lambda sql, aqs: None,
                warmups=1,
                repetitions=2,
            )
        self.assertEqual(result.sql.samples, (1000, 100000))
        self.assertEqual(result.aqs.samples, (2000, 3000))

    def test_single_run_mode_has_no_warmups(self):
        seen = []
        result = measure_requests(
            lambda: seen.append("sql"),
            lambda: seen.append("aqs"),
            lambda sql, aqs: None,
            warmups=0,
            repetitions=1,
        )
        self.assertEqual(seen, ["sql", "aqs"])
        self.assertEqual(len(result.sql.samples), 1)

    def test_validation_failure_is_not_ignored_even_during_warmup(self):
        def reject(sql, aqs):
            raise AssertionError("different results")

        with self.assertRaisesMessage(AssertionError, "different results"):
            measure_requests(lambda: ["A"], lambda: ["B"], reject)

    def test_invalid_sample_counts_fail_before_request_execution(self):
        for warmups, repetitions in ((-1, 30), (3, 0), (3, -1)):
            with self.subTest(warmups=warmups, repetitions=repetitions):
                with self.assertRaises(ValueError):
                    measure_requests(
                        lambda: self.fail("Request executed"),
                        lambda: self.fail("Request executed"),
                        lambda sql, aqs: None,
                        warmups=warmups,
                        repetitions=repetitions,
                    )

    def test_statistics_use_median_and_nearest_rank_p95_without_dropping_outlier(self):
        stats = TimingStats(tuple(range(1, 30)) + (1000,))
        self.assertEqual(stats.median, 15.5)
        self.assertEqual(stats.p95, 29)
        self.assertEqual(
            stats.cells(), ("15.500", "29.000", "47.833", "1.000", "1000.000")
        )
        with self.assertRaises(ValueError):
            TimingStats(())

    def test_live_sql_is_select_only_and_uses_parameters(self):
        body = sql_body()
        self.assertTrue(body.strip().startswith("WITH requested_resources"))
        self.assertNotIn("CREATE", body)
        self.assertIn("%(ids)s::uuid[]", body)
        self.assertIn("%(language)s", body)
        self.assertIn("LIKE '%%s'", body)

    def test_live_result_validation_detects_missing_duplicates_and_shape(self):
        pk = uuid4()
        validate_rows([(pk, None)], [(pk, None)], [(pk, None)])
        with self.assertRaises(AssertionError):
            validate_rows([(pk, "A")], [(pk, "A")], [(pk, "A"), (pk, "A")])
        with self.assertRaises(AssertionError):
            validate_rows([(str(pk), "A")], [(str(pk), "A")], [(str(pk), "A")])

    def test_worker_result_keeps_its_request_timing_and_uuid_type(self):
        pk = uuid4()
        stdout = (
            "Startup log\n"
            + WORKER_PREFIX
            + ('{"ms": 2.5, "rows": [["' + str(pk) + '", "Name"]]}')
        )
        with patch(
            "tests.benchmark_heritage_asset_names.subprocess.run",
            return_value=CompletedProcess([], 0, stdout, ""),
        ) as run:
            elapsed, rows = fresh_request("sql", [pk], "en")
        self.assertEqual(elapsed, 2.5)
        self.assertEqual(rows, [(pk, "Name")])
        self.assertTrue(run.call_args.kwargs["check"])
        self.assertEqual(run.call_args.kwargs["timeout"], 120)
        self.assertIn(str(pk), run.call_args.kwargs["input"])
        self.assertNotIn("--ids", run.call_args.args[0])

    def test_all_resources_flag_and_existing_batch_defaults(self):
        self.assertTrue(parse_args(["--all-resources"]).all_resources)
        self.assertEqual(parse_args([]).batch_sizes, [1, 10, 100])
        self.assertEqual(
            parse_args(["--batch-sizes", "100", "1000"]).batch_sizes, [100, 1000]
        )

    def test_all_resources_rejects_conflicting_selection_flags(self):
        for flags in (
            ["--all-resources", "--ids", str(uuid4())],
            ["--all-resources", "--batch-sizes", "100"],
        ):
            with self.subTest(flags=flags), redirect_stderr(StringIO()):
                with self.assertRaises(SystemExit) as error:
                    parse_args(flags)
                self.assertEqual(error.exception.code, 2)

    def test_worker_ids_are_read_from_stdin(self):
        pk = uuid4()
        with patch("sys.stdin", StringIO('["' + str(pk) + '"]')):
            args = parse_args(["--worker", "sql", "--worker-stdin"])
        self.assertEqual(args.ids, [pk])

    def test_public_aqs_selector_has_separate_default_report(self):
        args = parse_args(["--aqs-implementation", "public", "--all-resources"])
        self.assertEqual(args.aqs_implementation, "public")
        self.assertEqual(
            args.output.name, "heritage_asset_names_live_public_aqs_benchmark.md"
        )
        self.assertEqual(parse_args([]).aqs_implementation, "original")

    def test_fresh_process_keeps_selected_aqs_implementation(self):
        pk = uuid4()
        stdout = WORKER_PREFIX + '{"ms": 1, "rows": []}'
        with patch(
            "tests.benchmark_heritage_asset_names.subprocess.run",
            return_value=CompletedProcess([], 0, stdout, ""),
        ) as run:
            fresh_request("aqs", [pk], "en", aqs_implementation="public")
        command = run.call_args.args[0]
        self.assertEqual(command[command.index("--aqs-implementation") + 1], "public")

    def test_worker_missing_output_is_an_error(self):
        with patch(
            "tests.benchmark_heritage_asset_names.subprocess.run",
            return_value=CompletedProcess([], 0, "No result", "Error detail"),
        ):
            with self.assertRaisesMessage(RuntimeError, "Error detail"):
                fresh_request("aqs", [uuid4()], "en")

    def test_batch_size_must_be_positive(self):
        self.assertEqual(positive_int("10"), 10)
        with self.assertRaises(argparse.ArgumentTypeError):
            positive_int("0")

    def test_three_implementations_cycle_through_every_order(self):
        order = []
        validations = []

        def request(name):
            order.append(name)
            return ["Name"]

        with patch(
            "tests.heritage_asset_names_benchmark.perf_counter",
            side_effect=[value / 1000 for value in range(198)],
        ):
            result = measure_implementations(
                {name: lambda name=name: request(name) for name in COMPARISON_WORKERS},
                validations.append,
            )
        self.assertEqual(len(validations), 33)
        self.assertEqual(set(validations[0]), {"sql", "original", "public"})
        measured = [tuple(order[i : i + 3]) for i in range(9, len(order), 3)]
        self.assertEqual(len(set(measured)), 6)
        for position in range(3):
            for name in COMPARISON_WORKERS:
                self.assertEqual([o[position] for o in measured].count(name), 10)
        for stats in result.timings.values():
            self.assertEqual(len(stats.samples), 30)

    def test_three_implementation_validation_failure_is_not_ignored(self):
        def fail(results):
            raise AssertionError("Mismatch")

        with self.assertRaisesMessage(AssertionError, "Mismatch"):
            measure_implementations(
                {"sql": list, "original": list, "public": list}, fail
            )

    def test_comparison_mode_has_separate_default_report(self):
        args = parse_args(["--aqs-implementation", "both", "--all-resources"])
        self.assertEqual(
            args.output.name, "heritage_asset_names_live_aqs_comparison.md"
        )
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
            parse_args(
                [
                    "--worker",
                    "aqs",
                    "--ids",
                    str(uuid4()),
                    "--aqs-implementation",
                    "both",
                ]
            )

    def test_invocation_records_command_and_resolved_defaults(self):
        argv = ["--aqs-implementation", "both", "--all-resources", "--output", "a b.md"]
        command, parameters = invocation(argv, parse_args(argv))
        self.assertEqual(
            command,
            "python -m tests.benchmark_heritage_asset_names "
            "--aqs-implementation both --all-resources --output 'a b.md'",
        )
        self.assertEqual(parameters["--repetitions"], 30)
        self.assertEqual(parameters["--warmups"], 3)
        self.assertEqual(parameters["--batch-sizes"], "1 10 100")
        self.assertNotIn("--worker", parameters)

    def test_report_includes_command_section(self):
        batch = ImplementationBatch(
            "live-warm-batch-1",
            "en",
            1,
            1,
            {"sql": (1.0,), "original": (2.0,), "public": (3.0,)},
        )
        with TemporaryDirectory() as directory:
            path = Path(directory) / "report.md"
            write_implementation_report(
                path,
                [batch],
                data_description="test.",
                command="python -m tests.benchmark_heritage_asset_names --all-resources",
                parameters={"--repetitions": 30},
            )
            report = path.read_text()
        self.assertIn(
            "```bash\npython -m tests.benchmark_heritage_asset_names --all-resources\n```",
            report,
        )
        self.assertIn("| `--repetitions` | `30` |", report)

    def test_fresh_comparison_runs_each_aqs_implementation_in_a_worker(self):
        calls = []

        def request(worker, ids, language, *, aqs_implementation):
            calls.append((worker, aqs_implementation))
            return 1.0, []

        with patch(
            "tests.benchmark_heritage_asset_names.fresh_request", side_effect=request
        ):
            timings, _ = fresh_measurements(
                COMPARISON_WORKERS, [uuid4()], "en", 6, lambda results: None
            )
        self.assertEqual(
            set(calls), {("sql", "original"), ("aqs", "original"), ("aqs", "public")}
        )
        self.assertEqual(len(calls), 18)
        self.assertEqual({len(samples) for samples in timings.values()}, {6})

    def test_implementation_report_compares_aqs_without_names(self):
        batch = ImplementationBatch(
            "live-warm-batch-2",
            "en",
            2,
            3,
            {
                "sql": (10.0, 10.0, 10.0),
                "original": (20.0, 30.0, 10.0),
                "public": (10.0, 15.0, 12.0),
            },
            3,
        )
        with TemporaryDirectory() as directory:
            path = Path(directory) / "report.md"
            write_implementation_report(path, [batch], data_description="test.")
            report = path.read_text()
        self.assertIn(
            "| live-warm-batch-2 | en | 2 | 3 | 20.000 | 12.000 | 0.600 | -10.000 "
            "| 2 / 3 | Alias AQS |",
            report,
        )
        self.assertIn(
            "| live-warm-batch-2 | UUID AQS (heritage_asset_names) | 20.000 ", report
        )
        self.assertNotIn("Resource UUID", report)
        self.assertNotIn("Resource-name comparisons", report)

    def test_implementation_report_lists_sources(self):
        batch = ImplementationBatch(
            "live-warm-batch-1",
            "en",
            1,
            1,
            {"sql": (1.0,), "original": (2.0,), "public": (3.0,)},
        )
        sources = [
            ImplementationSource("UUID AQS", "a.py:1", "a.f()", "0" * 64),
            ImplementationSource("Alias AQS", "b.py:2", "b.f()", "1" * 64),
        ]
        with TemporaryDirectory() as directory:
            path = Path(directory) / "report.md"
            write_implementation_report(
                path, [batch], data_description="test.", implementations=sources
            )
            report = path.read_text()
        self.assertIn("## Implementations benchmarked", report)
        self.assertIn(f"| UUID AQS | `a.py:1` | `a.f()` | `{'0' * 64}` |", report)
        self.assertLess(
            report.index("## Implementations benchmarked"),
            report.index("## UUID AQS vs alias AQS"),
        )
