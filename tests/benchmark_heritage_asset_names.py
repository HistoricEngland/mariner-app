"""Opt-in live-data benchmark; never installs functions or changes database data."""

import argparse
from collections import Counter
from itertools import permutations, zip_longest
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
from time import perf_counter
from uuid import UUID

from tests.heritage_asset_names_benchmark import (
    measure_implementations,
    measure_requests,
)
from tests.heritage_asset_names_report import (
    IMPLEMENTATION_LABELS,
    Comparison,
    ImplementationBatch,
    callable_source,
    sql_source,
    write_implementation_report,
    write_report,
)


WORKER_PREFIX = "HERITAGE_BENCHMARK_RESULT="
# Request name -> (worker, AQS implementation) for --aqs-implementation both.
COMPARISON_WORKERS = {
    "sql": ("sql", "original"),
    "original": ("aqs", "original"),
    "public": ("aqs", "public"),
}


def positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("Must be a positive integer.")
    return number


def sql_body() -> str:
    source = (
        Path(__file__).parent / "fixtures" / "heritage_asset_names.sql"
    ).read_text()
    # Execute only the SELECT body, not the fixture's schema/function DDL.
    return (
        source.split("$function$")[1]
        .replace("%", "%%")
        .replace("p_ids", "%(ids)s::uuid[]")
        .replace("p_language", "%(language)s")
    )


def validate_rows(sql_rows, aqs_rows, expected):
    validate_results((sql_rows, aqs_rows), expected)


def validate_results(results, expected):
    for rows in results:
        if Counter(rows) != Counter(expected):
            raise AssertionError("Results changed or SQL/AQS names differ.")
        if [pk for pk, _ in rows] != sorted(pk for pk, _ in rows):
            raise AssertionError("Results are not in resource UUID order.")
        if any(
            not isinstance(pk, UUID) or (name is not None and not isinstance(name, str))
            for pk, name in rows
        ):
            raise AssertionError("Results must contain UUIDs and text/null names.")


def fresh_request(
    implementation: str,
    resource_ids: list[UUID],
    language: str,
    *,
    aqs_implementation: str = "original",
):
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "tests.benchmark_heritage_asset_names",
                "--worker",
                implementation,
                "--worker-stdin",
                "--language",
                language,
                "--aqs-implementation",
                aqs_implementation,
            ],
            input=json.dumps(list(map(str, resource_ids))),
            capture_output=True,
            text=True,
            check=True,
            timeout=120,
        )
    except subprocess.CalledProcessError as error:
        raise RuntimeError(
            f"{implementation} worker failed:\n{error.stdout}\n{error.stderr}"
        ) from error
    payloads = [
        line[len(WORKER_PREFIX) :]
        for line in result.stdout.splitlines()
        if line.startswith(WORKER_PREFIX)
    ]
    if len(payloads) != 1:
        raise RuntimeError(f"Missing worker result:\n{result.stdout}\n{result.stderr}")
    payload = json.loads(payloads[0])
    return payload["ms"], [(UUID(pk), name) for pk, name in payload["rows"]]


def fresh_measurements(workers, resource_ids, language, runs, validate):
    """Run each named (worker, AQS implementation) in its own fresh process."""
    timings: dict[str, list[float]] = {name: [] for name in workers}
    results = {}
    orders = list(permutations(workers))
    for iteration in range(runs):
        for name in orders[iteration % len(orders)]:
            worker, aqs_implementation = workers[name]
            elapsed, rows = fresh_request(
                worker,
                resource_ids,
                language,
                aqs_implementation=aqs_implementation,
            )
            timings[name].append(elapsed)
            results[name] = rows
        validate(results)
    return {name: tuple(values) for name, values in timings.items()}, results


def parse_args(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(description=__doc__)
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument(
        "--ids",
        nargs="+",
        type=UUID,
        help="Explicit resource IDs; otherwise sample all three models.",
    )
    selection.add_argument(
        "--all-resources",
        action="store_true",
        help="Benchmark all supported resources with a nonblank primary reference in one batch.",
    )
    parser.add_argument("--batch-sizes", nargs="+", type=positive_int)
    parser.add_argument("--language", default="en")
    parser.add_argument("--repetitions", type=positive_int, default=30)
    parser.add_argument("--warmups", type=int, default=3)
    parser.add_argument("--cold-start-runs", type=int, default=0)
    parser.add_argument(
        "--aqs-implementation",
        choices=("original", "public", "both"),
        default="original",
        help=(
            "Choose the UUID-based implementation, the public alias-based AQS API, "
            "or both to compare them head to head in the same snapshot."
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
    )
    parser.add_argument("--worker", choices=("sql", "aqs"), help=argparse.SUPPRESS)
    parser.add_argument("--worker-stdin", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.warmups < 0 or args.cold_start_runs < 0:
        parser.error("Warm-ups and cold-start runs must be nonnegative.")
    if args.all_resources and args.batch_sizes is not None:
        parser.error("--all-resources cannot be combined with --batch-sizes.")
    if args.worker_stdin:
        if not args.worker or args.ids or args.all_resources:
            parser.error(
                "--worker-stdin requires --worker and cannot combine with resource selection."
            )
        args.ids = [UUID(value) for value in json.load(sys.stdin)]
    if args.worker and not args.ids:
        parser.error("A fresh-process worker requires explicit resource IDs.")
    if args.worker and args.aqs_implementation == "both":
        parser.error("A fresh-process worker runs a single AQS implementation.")
    if args.batch_sizes is None:
        args.batch_sizes = [1, 10, 100]
    if args.output is None:
        args.output = Path(
            {
                "original": "tests/heritage_asset_names_live_benchmark.md",
                "public": "tests/heritage_asset_names_live_public_aqs_benchmark.md",
                "both": "tests/heritage_asset_names_live_aqs_comparison.md",
            }[args.aqs_implementation]
        )
    return args


def invocation(argv: list[str], args) -> tuple[str, dict[str, object]]:
    """The command line as run, and every resolved parameter for the report."""
    command = shlex.join(
        ["python", "-m", "tests.benchmark_heritage_asset_names", *argv]
    )
    parameters = {
        f"--{name.replace('_', '-')}": (
            " ".join(map(str, value)) if isinstance(value, list) else value
        )
        for name, value in vars(args).items()
        if name not in ("worker", "worker_stdin")
    }
    return command, parameters


def eligible_resource_ids() -> list[UUID]:
    from django.core.exceptions import ImproperlyConfigured
    from django.db.models import Case, TextField, Value, When
    from django.db.models.fields.json import KeyTextTransform
    from django.db.models.functions import NullIf, Trim
    from arches.app.models.models import Node, TileModel
    from mariner_app.heritage_asset_names import REFERENCE_NODES

    reference_nodes = list(
        Node.objects.filter(pk__in=[node for _, node in REFERENCE_NODES])
    )
    if len(reference_nodes) != len(REFERENCE_NODES):
        raise ImproperlyConfigured("Missing Mariner primary-reference nodes.")
    graph_ids = {node.graph_id for node in reference_nodes}
    references = (
        TileModel.objects.filter(
            resourceinstance__graph_id__in=graph_ids,
            nodegroup_id__in=[group for group, _ in REFERENCE_NODES],
        )
        .alias(
            reference_text=Case(
                *(
                    When(nodegroup_id=group, then=KeyTextTransform(node, "data"))
                    for group, node in REFERENCE_NODES
                ),
                output_field=TextField(),
            )
        )
        .alias(
            nonblank_reference=NullIf(
                Trim("reference_text"), Value(""), output_field=TextField()
            )
        )
    )
    return list(
        references.filter(nonblank_reference__isnull=False)
        .order_by("resourceinstance_id")
        .values_list("resourceinstance_id", flat=True)
        .distinct()
    )


def main():
    argv = sys.argv[1:]
    args = parse_args(argv)
    command, parameters = invocation(argv, args)
    comparing = args.aqs_implementation == "both"
    if comparing and args.repetitions % 6:
        print(
            "Warning: use a multiple of 6 repetitions to balance all request orders.",
            flush=True,
        )
    implementation_prefix = "public-aqs-" if args.aqs_implementation == "public" else ""

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "mariner_app.settings")
    import django

    django.setup()

    from django.db import connection, transaction
    from arches.app.models.models import ResourceInstance, Node
    from mariner_app import heritage_asset_names, heritage_asset_names_aqs
    from mariner_app.heritage_asset_names import REFERENCE_NODES

    aqs_functions = {
        "original": heritage_asset_names.get_heritage_asset_names,
        "public": heritage_asset_names_aqs.get_heritage_asset_names,
    }
    measured_aqs = list(aqs_functions) if comparing else [args.aqs_implementation]
    implementations = [
        sql_source(
            IMPLEMENTATION_LABELS["sql"],
            "SELECT body of hapi.get_heritage_asset_names(), executed via "
            "tests.benchmark_heritage_asset_names.sql_body()",
        ),
        *(
            callable_source(IMPLEMENTATION_LABELS[name], aqs_functions[name])
            for name in measured_aqs
        ),
    ]
    body = sql_body()
    comparisons = []
    batches = []
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            cursor.execute("SET LOCAL statement_timeout = '60s'")
        resource_ids = args.ids
        if args.all_resources:
            resource_ids = eligible_resource_ids()
            print(
                f"Selected all {len(resource_ids)} eligible live resources.", flush=True
            )
        elif resource_ids is None:
            graph_ids = [
                Node.objects.get(pk=node_id).graph_id for _, node_id in REFERENCE_NODES
            ]
            samples = [
                list(
                    ResourceInstance.objects.filter(graph_id=graph_id)
                    .order_by("pk")
                    .values_list("pk", flat=True)[: max(args.batch_sizes)]
                )
                for graph_id in graph_ids
            ]
            # Interleave models rather than filling the batch from just one graph.
            resource_ids = [
                pk for group in zip_longest(*samples) for pk in group if pk is not None
            ]
        resource_ids = list(dict.fromkeys(resource_ids))
        sizes = (
            [len(resource_ids)]
            if args.worker or args.all_resources
            else args.batch_sizes
        )
        if args.all_resources and not resource_ids:
            raise ValueError(
                "No eligible live resources have a nonblank primary reference."
            )
        for size in sizes:
            if size > len(resource_ids):
                raise ValueError(
                    f"Batch size {size} exceeds {len(resource_ids)} available resource IDs."
                )
            ids = resource_ids[:size]

            def sql_request():
                with connection.cursor() as cursor:
                    cursor.execute(
                        body, {"ids": list(map(str, ids)), "language": args.language}
                    )
                    if [column.name for column in cursor.description] != [
                        "resourceinstanceid",
                        "heritageassetname",
                    ]:
                        raise AssertionError("Unexpected SQL output columns.")
                    return cursor.fetchall()

            def aqs_request(implementation=args.aqs_implementation):
                rows = list(
                    aqs_functions[implementation](ids, language_code=args.language)
                )
                if any(
                    set(row) != {"resourceinstanceid", "heritageassetname"}
                    for row in rows
                ):
                    raise AssertionError("Unexpected AQS output columns.")
                return [
                    (row["resourceinstanceid"], row["heritageassetname"])
                    for row in rows
                ]

            if args.worker:
                start = perf_counter()
                rows = sql_request() if args.worker == "sql" else aqs_request()
                elapsed = (perf_counter() - start) * 1000
                print(
                    WORKER_PREFIX
                    + json.dumps({"ms": elapsed, "rows": rows}, default=str)
                )
                return

            expected = sql_request()
            if comparing:
                measured_all = measure_implementations(
                    {
                        "sql": sql_request,
                        "original": lambda: aqs_request("original"),
                        "public": lambda: aqs_request("public"),
                    },
                    lambda results: validate_results(results.values(), expected),
                    warmups=args.warmups,
                    repetitions=args.repetitions,
                )
                batches.append(
                    ImplementationBatch(
                        f"live-warm-batch-{size}",
                        args.language,
                        len(ids),
                        len(expected),
                        {
                            name: stats.samples
                            for name, stats in measured_all.timings.items()
                        },
                        measured_all.warmups,
                    )
                )
                print(
                    f"Batch {size}: "
                    + ", ".join(
                        f"{name} median={stats.median:.3f} ms"
                        for name, stats in measured_all.timings.items()
                    ),
                    flush=True,
                )
                if args.cold_start_runs:
                    timings, _ = fresh_measurements(
                        COMPARISON_WORKERS,
                        ids,
                        args.language,
                        args.cold_start_runs,
                        lambda results: validate_results(results.values(), expected),
                    )
                    batches.append(
                        ImplementationBatch(
                            f"live-fresh-process-batch-{size}",
                            args.language,
                            len(ids),
                            len(expected),
                            timings,
                        )
                    )
                continue

            measured = measure_requests(
                sql_request,
                aqs_request,
                lambda sql, aqs: validate_rows(sql, aqs, expected),
                warmups=args.warmups,
                repetitions=args.repetitions,
            )
            comparisons.append(
                Comparison(
                    f"live-{implementation_prefix}warm-batch-{size}",
                    args.language,
                    ids,
                    measured.sql_rows,
                    measured.aqs_rows,
                    measured.sql.median,
                    measured.aqs.median,
                    measured.sql.samples,
                    measured.aqs.samples,
                    measured.warmups,
                )
            )
            print(
                f"Batch {size}: SQL median={measured.sql.median:.3f} ms, AQS median={measured.aqs.median:.3f} ms",
                flush=True,
            )

            if args.cold_start_runs:
                timings, results = fresh_measurements(
                    {
                        "sql": ("sql", args.aqs_implementation),
                        "aqs": ("aqs", args.aqs_implementation),
                    },
                    ids,
                    args.language,
                    args.cold_start_runs,
                    lambda results: validate_rows(
                        results["sql"], results["aqs"], expected
                    ),
                )
                comparisons.append(
                    Comparison(
                        f"live-{implementation_prefix}fresh-process-batch-{size}",
                        args.language,
                        ids,
                        results["sql"],
                        results["aqs"],
                        0,
                        0,
                        timings["sql"],
                        timings["aqs"],
                        0,
                    )
                )
    selection = (
        " Selection: all resources in the three supported models with a "
        "nonblank supported primary reference, as one complete batch."
        if args.all_resources
        else ""
    )
    if comparing:
        write_implementation_report(
            args.output,
            batches,
            data_description=(
                "live application data, read in read-only transactions; no function "
                "installed. SQL executes the reference SELECT body, not a stored "
                "function. Warm runs of all three implementations share one "
                "repeatable-read snapshot. Fresh-process runs use independent "
                "snapshots and fail if results differ; their timings measure the "
                "first request after Django initialization, excluding process "
                "startup, Django setup, and connection setup. Database/OS caches are "
                "not cleared." + selection
            ),
            implementations=implementations,
            command=command,
            parameters=parameters,
        )
        print(f"Report: {args.output.resolve()}")
        return
    write_report(
        args.output,
        comparisons,
        data_description=(
            "live application data, read in read-only transactions; no function installed. "
            "SQL executes the reference SELECT body, not a stored function. "
            "Warm runs share a repeatable-read snapshot. Fresh-process runs use "
            "independent snapshots and fail if results differ. Fresh-process timings "
            "measure the first request after Django initialization; process startup, "
            "Django setup, and connection setup are excluded. Database/OS caches are "
            "not cleared. Live resource names appear in this local report."
            f" AQS implementation: {args.aqs_implementation}." + selection
        ),
        implementations=implementations,
        command=command,
        parameters=parameters,
    )
    print(f"Report: {args.output.resolve()}")


if __name__ == "__main__":
    main()
