from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from .models import BenchmarkSample, SampleQuality
from .stats import median


@dataclass(frozen=True)
class QualityPolicy:
    min_samples: int = 20
    warmup_iterations: int = 3
    outlier_mad_threshold: float = 6.0
    max_outlier_fraction: float = 0.15
    max_cv: float = 0.20
    max_mad_ratio: float = 0.15


@dataclass(frozen=True)
class PreparedSamples:
    baseline: list[BenchmarkSample]
    candidate: list[BenchmarkSample]
    quality: SampleQuality


def _sorted_samples(samples: Sequence[BenchmarkSample]) -> list[BenchmarkSample]:
    return sorted(
        samples,
        key=lambda sample: sample.iteration if sample.iteration is not None else 1_000_000_000,
    )


def _trim_warmup(samples: Sequence[BenchmarkSample], warmup_iterations: int) -> list[BenchmarkSample]:
    if warmup_iterations <= 0:
        return list(samples)
    ordered = _sorted_samples(samples)
    if len(ordered) <= warmup_iterations:
        return []
    return ordered[warmup_iterations:]


def _mad(values: Sequence[float], center: float) -> float:
    if not values:
        return 0.0
    return median([abs(value - center) for value in values])


def _coefficient_of_variation(values: Sequence[float]) -> float:
    if len(values) < 2:
        return math.inf
    mean = sum(values) / len(values)
    if mean == 0:
        return math.inf
    variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    return math.sqrt(variance) / abs(mean)


def _remove_outliers(samples: Sequence[BenchmarkSample], threshold: float) -> list[BenchmarkSample]:
    if threshold <= 0 or len(samples) < 4:
        return list(samples)
    values = [sample.value for sample in samples]
    center = median(values)
    mad = _mad(values, center)
    if mad == 0:
        return list(samples)
    limit = threshold * 1.4826 * mad
    return [sample for sample in samples if abs(sample.value - center) <= limit]


def _mad_ratio(values: Sequence[float]) -> float:
    if not values:
        return math.inf
    center = median(values)
    if center == 0:
        return math.inf
    return (1.4826 * _mad(values, center)) / abs(center)


def prepare_samples(
    baseline: Sequence[BenchmarkSample],
    candidate: Sequence[BenchmarkSample],
    *,
    policy: QualityPolicy,
) -> PreparedSamples:
    baseline_warm = _trim_warmup(baseline, policy.warmup_iterations)
    candidate_warm = _trim_warmup(candidate, policy.warmup_iterations)
    baseline_filtered = _remove_outliers(baseline_warm, policy.outlier_mad_threshold)
    candidate_filtered = _remove_outliers(candidate_warm, policy.outlier_mad_threshold)

    baseline_values = [sample.value for sample in baseline_filtered]
    candidate_values = [sample.value for sample in candidate_filtered]
    baseline_removed = len(baseline_warm) - len(baseline_filtered)
    candidate_removed = len(candidate_warm) - len(candidate_filtered)
    baseline_cv = _coefficient_of_variation(baseline_values)
    candidate_cv = _coefficient_of_variation(candidate_values)
    baseline_mad_ratio = _mad_ratio(baseline_values)
    candidate_mad_ratio = _mad_ratio(candidate_values)

    warnings: list[str] = []
    if len(baseline_filtered) < policy.min_samples:
        warnings.append(f"baseline has {len(baseline_filtered)} retained samples; need at least {policy.min_samples}")
    if len(candidate_filtered) < policy.min_samples:
        warnings.append(f"candidate has {len(candidate_filtered)} retained samples; need at least {policy.min_samples}")

    for label, warm_count, removed in [
        ("baseline", len(baseline_warm), baseline_removed),
        ("candidate", len(candidate_warm), candidate_removed),
    ]:
        fraction = removed / warm_count if warm_count else 1.0
        if fraction > policy.max_outlier_fraction:
            warnings.append(f"{label} removed {fraction:.1%} of samples as outliers")

    if baseline_cv > policy.max_cv:
        warnings.append(f"baseline coefficient of variation is {baseline_cv:.1%}; max is {policy.max_cv:.1%}")
    if candidate_cv > policy.max_cv:
        warnings.append(f"candidate coefficient of variation is {candidate_cv:.1%}; max is {policy.max_cv:.1%}")
    if baseline_mad_ratio > policy.max_mad_ratio:
        warnings.append(f"baseline robust noise ratio is {baseline_mad_ratio:.1%}; max is {policy.max_mad_ratio:.1%}")
    if candidate_mad_ratio > policy.max_mad_ratio:
        warnings.append(f"candidate robust noise ratio is {candidate_mad_ratio:.1%}; max is {policy.max_mad_ratio:.1%}")

    quality = SampleQuality(
        raw_baseline_count=len(baseline),
        raw_candidate_count=len(candidate),
        baseline_count=len(baseline_filtered),
        candidate_count=len(candidate_filtered),
        baseline_outliers_removed=baseline_removed,
        candidate_outliers_removed=candidate_removed,
        baseline_cv=baseline_cv,
        candidate_cv=candidate_cv,
        baseline_mad_ratio=baseline_mad_ratio,
        candidate_mad_ratio=candidate_mad_ratio,
        warmup_iterations=policy.warmup_iterations,
        passed=not warnings,
        warnings=warnings,
    )
    return PreparedSamples(list(baseline_filtered), list(candidate_filtered), quality)
