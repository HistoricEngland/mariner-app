"""Repeatable request measurements, with validation outside the timed regions."""

from collections.abc import Callable
from dataclasses import dataclass
from itertools import permutations
from math import ceil
from statistics import mean, median
from time import perf_counter
from typing import Generic, TypeVar


T = TypeVar("T")


@dataclass(frozen=True)
class TimingStats:
    samples: tuple[float, ...]

    def __post_init__(self):
        if not self.samples:
            raise ValueError("At least one timing sample is required.")

    @property
    def median(self) -> float:
        return median(self.samples)

    @property
    def p95(self) -> float:
        # Nearest-rank percentile: retain real measurements, including outliers.
        return sorted(self.samples)[ceil(0.95 * len(self.samples)) - 1]

    def cells(self) -> tuple[str, ...]:
        return tuple(
            f"{value:.3f}"
            for value in (
                self.median,
                self.p95,
                mean(self.samples),
                min(self.samples),
                max(self.samples),
            )
        )


@dataclass
class Measurements(Generic[T]):
    sql_rows: T
    aqs_rows: T
    sql: TimingStats
    aqs: TimingStats
    warmups: int


@dataclass
class ImplementationMeasurements(Generic[T]):
    rows: dict[str, T]
    timings: dict[str, TimingStats]
    warmups: int


def measure_implementations(
    requests: dict[str, Callable[[], T]],
    validate: Callable[[dict[str, T]], None],
    *,
    warmups: int = 3,
    repetitions: int = 30,
) -> ImplementationMeasurements[T]:
    if warmups < 0 or repetitions < 1:
        raise ValueError(
            "Warm-ups must be nonnegative and repetitions must be positive."
        )
    samples: dict[str, list[float]] = {name: [] for name in requests}
    results: dict[str, T] = {}
    # Cycle through every request order so no implementation is always first.
    orders = list(permutations(requests))
    for iteration in range(warmups + repetitions):
        # Restart the cycle after warm-up so measured order is balanced.
        index = iteration if iteration < warmups else iteration - warmups
        for implementation in orders[index % len(orders)]:
            start = perf_counter()
            results[implementation] = requests[implementation]()
            elapsed = (perf_counter() - start) * 1000
            if iteration >= warmups:
                samples[implementation].append(elapsed)
        validate(results)
    return ImplementationMeasurements(
        results,
        {name: TimingStats(tuple(values)) for name, values in samples.items()},
        warmups,
    )


def measure_requests(
    sql_request: Callable[[], T],
    aqs_request: Callable[[], T],
    validate: Callable[[T, T], None],
    *,
    warmups: int = 3,
    repetitions: int = 30,
) -> Measurements[T]:
    measured = measure_implementations(
        {"sql": sql_request, "aqs": aqs_request},
        lambda results: validate(results["sql"], results["aqs"]),
        warmups=warmups,
        repetitions=repetitions,
    )
    return Measurements(
        measured.rows["sql"],
        measured.rows["aqs"],
        measured.timings["sql"],
        measured.timings["aqs"],
        measured.warmups,
    )
