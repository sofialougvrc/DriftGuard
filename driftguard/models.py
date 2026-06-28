from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class BenchmarkSample:
    commit: str
    function: str
    metric: str
    value: float
    unit: str = "ns"
    iteration: int | None = None
    counters: dict[str, float] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MannWhitneyResult:
    u_statistic: float
    p_value: float
    probability_candidate_slower: float
    cliffs_delta: float


@dataclass(frozen=True)
class SprtResult:
    decision: str
    log_likelihood_ratio: float
    upper_boundary: float
    lower_boundary: float
    confidence: float
    observations: int


@dataclass(frozen=True)
class ChangePointResult:
    introduced_at: str | None
    posterior_probability: float
    candidates: list[tuple[str, float]]


@dataclass(frozen=True)
class SampleQuality:
    raw_baseline_count: int
    raw_candidate_count: int
    baseline_count: int
    candidate_count: int
    baseline_outliers_removed: int
    candidate_outliers_removed: int
    baseline_cv: float
    candidate_cv: float
    baseline_mad_ratio: float
    candidate_mad_ratio: float
    warmup_iterations: int
    passed: bool
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class EnvironmentFingerprint:
    platform: str
    machine: str
    processor: str
    python_version: str
    cpu_count: int | None
    load_average_1m: float | None
    cpu_governor: str | None
    perf_event_paranoid: int | None
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ComparisonResult:
    function: str
    metric: str
    unit: str
    baseline_commit: str
    candidate_commit: str
    baseline_p99: float
    candidate_p99: float
    p99_delta_percent: float
    baseline_median: float
    candidate_median: float
    median_delta_percent: float
    mann_whitney: MannWhitneyResult
    sprt: SprtResult
    change_point: ChangePointResult
    quality: SampleQuality
    is_regression: bool


@dataclass(frozen=True)
class AnalysisResult:
    baseline_commit: str
    candidate_commit: str
    comparisons: list[ComparisonResult]
    environment: EnvironmentFingerprint | None = None

    @property
    def regressions(self) -> list[ComparisonResult]:
        return [item for item in self.comparisons if item.is_regression]

    @property
    def has_quality_warnings(self) -> bool:
        if self.environment and self.environment.warnings:
            return True
        return any(item.quality.warnings for item in self.comparisons)
