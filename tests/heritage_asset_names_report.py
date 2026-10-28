"""Markdown output for SQL/AQS comparison requests."""

from dataclasses import dataclass
from collections import defaultdict
from hashlib import sha256
from html import escape
import inspect
from itertools import zip_longest
import json
from pathlib import Path
from uuid import UUID

from tests.heritage_asset_names_benchmark import TimingStats

ResultRow = tuple[UUID, str | None]
MISSING = object()
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SQL_FIXTURE = REPOSITORY_ROOT / "tests" / "fixtures" / "heritage_asset_names.sql"


@dataclass(frozen=True)
class ImplementationSource:
    """Where a benchmarked implementation's code lives, for report traceability."""

    label: str
    path: str
    method: str
    sha256: str

    def markdown_row(self) -> str:
        return f"| {self.label} | `{self.path}` | `{self.method}` | `{self.sha256}` |"


def file_sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def repository_path(path: Path) -> str:
    path = Path(path).resolve()
    try:
        return path.relative_to(REPOSITORY_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def callable_source(label: str, function) -> ImplementationSource:
    """Describe the file and method a callable is actually defined in."""
    function = inspect.unwrap(function)
    path = Path(inspect.getsourcefile(function))
    line = inspect.getsourcelines(function)[1]
    return ImplementationSource(
        label,
        f"{repository_path(path)}:{line}",
        f"{function.__module__}.{function.__qualname__}()",
        file_sha256(path),
    )


def sql_source(label: str, method: str) -> ImplementationSource:
    return ImplementationSource(
        label, repository_path(SQL_FIXTURE), method, file_sha256(SQL_FIXTURE)
    )


def implementation_section(sources: list[ImplementationSource]) -> list[str]:
    if not sources:
        return []
    return [
        "## Implementations benchmarked",
        "",
        "Each implementation's source file (with the method's line number), the "
        "method called, and the SHA-256 of that source file when this report "
        "was generated.",
        "",
        "| Implementation | Source file | Method | Source SHA-256 |",
        "|---|---|---|---|",
        *(source.markdown_row() for source in sources),
        "",
    ]


def command_section(command: str | None, parameters: dict[str, object]) -> list[str]:
    """The exact CLI invocation and every resolved parameter, defaults included."""
    if command is None:
        return []
    return [
        "## How this report was generated",
        "",
        "Command, as run inside the `marinerapp` container from `/web_root/mariner_app`:",
        "",
        "```bash",
        command,
        "```",
        "",
        "Resolved parameters, including defaults not given on the command line:",
        "",
        "| Parameter | Value |",
        "|---|---|",
        *(f"| `{name}` | `{value}` |" for name, value in parameters.items()),
        "",
    ]


def markdown_name(value: object) -> str:
    if value is MISSING:
        return "NO ROW"
    if value is None:
        return "NULL"
    return escape(json.dumps(value, ensure_ascii=True)).replace("|", "&#124;")


@dataclass
class Comparison:
    test: str
    language: str
    requested_ids: list[UUID]
    sql_rows: list[ResultRow]
    aqs_rows: list[ResultRow]
    sql_ms: float
    aqs_ms: float
    sql_samples: tuple[float, ...] = ()
    aqs_samples: tuple[float, ...] = ()
    warmups: int = 0

    def timing_cells(self) -> tuple[str, ...]:
        sql = TimingStats(self.sql_samples or (self.sql_ms,))
        aqs = TimingStats(self.aqs_samples or (self.aqs_ms,))
        return (*sql.cells(), *aqs.cells(), str(len(sql.samples)), str(self.warmups))

    def markdown_summary(self, request_id: int) -> str:
        cells = (
            str(request_id),
            self.test,
            self.language,
            str(len(set(self.requested_ids))),
            str(len(self.sql_rows)),
            str(len(self.aqs_rows)),
            *self.timing_cells(),
        )
        return "| " + " | ".join(cells) + " |"

    def markdown_rows(self, request_id: int = 1) -> list[str]:
        sql_by_resource: dict[UUID, list[str | None]] = defaultdict(list)
        aqs_by_resource: dict[UUID, list[str | None]] = defaultdict(list)
        for resource_id, name in self.sql_rows:
            sql_by_resource[resource_id].append(name)
        for resource_id, name in self.aqs_rows:
            aqs_by_resource[resource_id].append(name)
        resource_ids = sorted(
            set(self.requested_ids) | sql_by_resource.keys() | aqs_by_resource.keys()
        )
        rows = []
        # UUIDs have no secondary SQL ordering: compare each resource's sorted
        # label multiset, preserving duplicate rows and distinguishing NULL/absence.
        for resource_id in resource_ids:
            sql_names = sorted(
                sql_by_resource.get(resource_id, []),
                key=lambda name: (name is not None, name or ""),
            )
            aqs_names = sorted(
                aqs_by_resource.get(resource_id, []),
                key=lambda name: (name is not None, name or ""),
            )
            pairs = list(zip_longest(sql_names, aqs_names, fillvalue=MISSING))
            for sql_name, aqs_name in pairs or [(MISSING, MISSING)]:
                cells = (
                    str(request_id),
                    self.test,
                    self.language,
                    str(resource_id),
                    markdown_name(sql_name),
                    markdown_name(aqs_name),
                    str(sql_name == aqs_name).lower(),
                )
                rows.append("| " + " | ".join(cells) + " |")
        if not resource_ids:
            cells = (
                str(request_id),
                self.test,
                self.language,
                "EMPTY INPUT",
                "NO ROW",
                "NO ROW",
                "true",
            )
            rows.append("| " + " | ".join(cells) + " |")
        return rows


def write_report(
    path: Path,
    comparisons: list[Comparison],
    *,
    data_description: str = "synthetic fixtures in Django's isolated test database, not live data.",
    implementations: list[ImplementationSource] = (),
    command: str | None = None,
    parameters: dict[str, object] | None = None,
) -> None:
    lines = [
        "# Heritage asset names: SQL vs AQS",
        "",
        "Data: " + data_description,
        "",
        *command_section(command, parameters or {}),
        *implementation_section(list(implementations)),
        f"Completed comparison requests: {len(comparisons)}.",
        "",
        "Times are elapsed milliseconds for the **whole-batch request**, shown once "
        "per comparison in the performance summary; they are not per-resource timings. "
        "SQL includes execution and "
        "fetching. AQS includes queryset construction, metadata queries, execution, "
        "and fetching. Fixture creation and result comparisons are excluded. "
        "Single-sample correctness runs are not benchmarks. Benchmark requests "
        "alternate SQL-first/AQS-first, exclude warm-ups, and retain every sample. "
        "p95 uses the nearest-rank method. Warm runs measure steady-state behavior, "
        "not cold database caches; shared-container load can still affect timings. "
        "No timing thresholds determine test success.",
        "",
        "Names are JSON-quoted to expose whitespace and empty strings. NULL means "
        "a returned null name; NO ROW means no result. Duplicate rows are retained. "
        "Rows sharing a resource UUID are paired by sorted name, not database order. "
        "Both absent is true. Empty requests are shown explicitly. Error-only tests "
        "are not name comparisons and are excluded; this report is not a test-pass summary.",
        "",
        "## Batch performance summary",
        "",
        "Each request ID identifies one comparison batch and links its summary, "
        "resource-name comparisons, and raw samples. Resource count is the number "
        "of distinct requested UUIDs. SQL/AQS row counts are actual returned rows, "
        "which may differ from the resource count because of excluded references "
        "or duplicate results.",
        "",
        "| Request | Test / batch | Language | Requested resources | SQL rows | AQS rows "
        "| SQL median (ms) | SQL p95 (ms) | SQL mean (ms) | SQL min (ms) | SQL max (ms) "
        "| AQS median (ms) | AQS p95 (ms) | AQS mean (ms) | AQS min (ms) | AQS max (ms) "
        "| Samples per implementation | Warm-ups per implementation |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for index, comparison in enumerate(comparisons, 1):
        lines.append(comparison.markdown_summary(index))
    lines.extend(
        [
            "",
            "## Resource-name comparisons",
            "",
            "Timing statistics belong to the batch summary above, not individual resources.",
            "",
            "| Request | Test / batch | Language | Resource UUID | SQL resource name | AQS resource name | Same |",
            "|---|---|---|---|---|---|---|",
        ]
    )
    for index, comparison in enumerate(comparisons, 1):
        lines.extend(comparison.markdown_rows(index))
    lines.extend(
        [
            "",
            "## Raw measured samples",
            "",
            "Warm-ups are excluded; all measured samples, including outliers, are retained.",
            "",
        ]
    )
    for index, comparison in enumerate(comparisons, 1):
        lines.extend(
            [
                f"### Request {index}: {comparison.test} ({comparison.language})",
                "",
                "SQL (ms): "
                + ", ".join(
                    f"{sample:.6f}"
                    for sample in comparison.sql_samples or (comparison.sql_ms,)
                ),
                "",
                "AQS (ms): "
                + ", ".join(
                    f"{sample:.6f}"
                    for sample in comparison.aqs_samples or (comparison.aqs_ms,)
                ),
                "",
            ]
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


IMPLEMENTATION_LABELS = {
    "sql": "Reference SQL",
    "original": "UUID AQS (heritage_asset_names)",
    "public": "Alias AQS (heritage_asset_names_aqs)",
}


@dataclass
class ImplementationBatch:
    test: str
    language: str
    resource_count: int
    row_count: int
    samples: dict[str, tuple[float, ...]]
    warmups: int = 0

    def stats(self, implementation: str) -> TimingStats:
        return TimingStats(self.samples[implementation])

    def head_to_head_row(self) -> str:
        original = self.stats("original")
        public = self.stats("public")
        # Samples are index-aligned: both ran in the same repetition.
        differences = TimingStats(
            tuple(
                alias - uuid
                for uuid, alias in zip(original.samples, public.samples, strict=True)
            )
        )
        public_wins = sum(
            alias < uuid for uuid, alias in zip(original.samples, public.samples)
        )
        if public.median < original.median:
            faster = "Alias AQS"
        elif original.median < public.median:
            faster = "UUID AQS"
        else:
            faster = "Tie"
        cells = (
            self.test,
            self.language,
            str(self.resource_count),
            str(self.row_count),
            f"{original.median:.3f}",
            f"{public.median:.3f}",
            f"{public.median / original.median:.3f}",
            f"{differences.median:+.3f}",
            f"{public_wins} / {len(public.samples)}",
            faster,
        )
        return "| " + " | ".join(cells) + " |"

    def implementation_rows(self) -> list[str]:
        sql_median = self.stats("sql").median
        rows = []
        for implementation, samples in self.samples.items():
            stats = self.stats(implementation)
            cells = (
                self.test,
                IMPLEMENTATION_LABELS[implementation],
                *stats.cells(),
                f"{stats.median / sql_median:.3f}",
                str(len(samples)),
                str(self.warmups),
            )
            rows.append("| " + " | ".join(cells) + " |")
        return rows


def write_implementation_report(
    path: Path,
    batches: list[ImplementationBatch],
    *,
    data_description: str,
    implementations: list[ImplementationSource] = (),
    command: str | None = None,
    parameters: dict[str, object] | None = None,
) -> None:
    lines = [
        "# Heritage asset names: UUID AQS vs alias AQS",
        "",
        "Data: " + data_description,
        "",
        *command_section(command, parameters or {}),
        *implementation_section(list(implementations)),
        "Times are elapsed milliseconds for the **whole-batch request**, not "
        "per-resource timings. SQL includes execution and fetching. Each AQS "
        "implementation includes queryset construction, metadata queries, "
        "execution, and fetching. Every repetition runs all three implementations, "
        "cycling through all six request orders; warm-ups are excluded and every "
        "measured sample is retained. Every repetition's results must match the "
        "initial SQL result. p95 uses the nearest-rank method. No resource names "
        "or UUIDs are included.",
        "",
        "## UUID AQS vs alias AQS",
        "",
        "Ratio is alias median / UUID median: below 1 means the alias "
        "implementation is faster. The paired difference is the median of "
        "per-repetition (alias − UUID) times; negative means alias is faster. "
        "Alias wins counts repetitions in which alias was faster.",
        "",
        "| Batch | Language | Requested resources | Rows | UUID AQS median (ms) "
        "| Alias AQS median (ms) | Alias / UUID ratio | Median paired difference (ms) "
        "| Alias wins | Faster by median |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    lines.extend(batch.head_to_head_row() for batch in batches)
    lines.extend(
        [
            "",
            "## All implementations",
            "",
            "Median vs SQL is each implementation's median / the SQL median.",
            "",
            "| Batch | Implementation | Median (ms) | p95 (ms) | Mean (ms) | Min (ms) "
            "| Max (ms) | Median vs SQL | Samples | Warm-ups |",
            "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for batch in batches:
        lines.extend(batch.implementation_rows())
    lines.extend(["", "## Raw measured samples", ""])
    for batch in batches:
        lines.extend([f"### {batch.test} ({batch.language})", ""])
        for implementation, samples in batch.samples.items():
            lines.extend(
                [
                    f"{IMPLEMENTATION_LABELS[implementation]} (ms): "
                    + ", ".join(f"{sample:.3f}" for sample in samples),
                    "",
                ]
            )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
