from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import replace
from pathlib import Path
from typing import Any

from .changepoint import bayesian_change_point
from .environment import capture_environment
from .models import AnalysisResult, BenchmarkSample, ChangePointResult, ComparisonResult
from .quality import QualityPolicy, prepare_samples
from .sprt import sprt_regression_test
from .stats import mann_whitney_u, median, percent_delta, quantile


def sample_from_json(row: dict[str, Any]) -> BenchmarkSample:
    return BenchmarkSample(
        commit=str(row["commit"]),
        function=str(row["function"]),
        metric=str(row.get("metric", "latency_ns")),
        value=float(row["value"]),
        unit=str(row.get("unit", "ns")),
        iteration=row.get("iteration"),
        counters={key: float(value) for key, value in dict(row.get("counters", {})).items()},
        metadata={key: value for key, value in row.items() if key not in {"commit", "function", "metric", "value", "unit", "iteration", "counters"}},
    )


def load_jsonl(path: str | Path) -> list[BenchmarkSample]:
    samples: list[BenchmarkSample] = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                samples.append(sample_from_json(json.loads(stripped)))
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise ValueError(f"invalid benchmark record at line {line_number}: {exc}") from exc
    return samples


def _commit_order(samples: Iterable[BenchmarkSample]) -> list[str]:
    order: list[str] = []
    seen: set[str] = set()
    for sample in samples:
        if sample.commit not in seen:
            seen.add(sample.commit)
            order.append(sample.commit)
    return order


def _choose_commits(samples: list[BenchmarkSample], baseline_commit: str | None, candidate_commit: str | None) -> tuple[str, str]:
    order = _commit_order(samples)
    if len(order) < 2 and (baseline_commit is None or candidate_commit is None):
        raise ValueError("at least two commits are required when baseline or candidate is omitted")

    candidate = candidate_commit or order[-1]
    if baseline_commit:
        baseline = baseline_commit
    else:
        candidate_index = order.index(candidate)
        if candidate_index == 0:
            raise ValueError("candidate is first commit; pass --baseline explicitly")
        baseline = order[candidate_index - 1]
    return baseline, candidate


def analyze_samples(
    samples: list[BenchmarkSample],
    *,
    baseline_commit: str | None = None,
    candidate_commit: str | None = None,
    min_effect: float = 0.05,
    alpha: float = 0.01,
    beta: float = 0.05,
    p_value_threshold: float = 0.05,
    quality_policy: QualityPolicy | None = None,
    capture_env: bool = True,
    require_clean_environment: bool = False,
) -> AnalysisResult:
    if not samples:
        raise ValueError("no benchmark samples provided")

    baseline, candidate = _choose_commits(samples, baseline_commit, candidate_commit)
    policy = quality_policy or QualityPolicy()
    environment = capture_environment() if capture_env else None
    grouped: dict[tuple[str, str], dict[str, list[BenchmarkSample]]] = defaultdict(lambda: defaultdict(list))
    for sample in samples:
        grouped[(sample.function, sample.metric)][sample.commit].append(sample)

    comparisons: list[ComparisonResult] = []
    for (function, metric), by_commit in sorted(grouped.items()):
        if baseline not in by_commit or candidate not in by_commit:
            continue
        prepared = prepare_samples(by_commit[baseline], by_commit[candidate], policy=policy)
        baseline_values = [sample.value for sample in prepared.baseline]
        candidate_values = [sample.value for sample in prepared.candidate]
        if len(baseline_values) < 3 or len(candidate_values) < 3:
            continue

        baseline_p99 = quantile(baseline_values, 0.99)
        candidate_p99 = quantile(candidate_values, 0.99)
        baseline_median = median(baseline_values)
        candidate_median = median(candidate_values)
        mann_whitney = mann_whitney_u(baseline_values, candidate_values)
        sprt = sprt_regression_test(
            baseline_values,
            candidate_values,
            min_effect=min_effect,
            alpha=alpha,
            beta=beta,
        )
        change_point = bayesian_change_point(samples, function, metric)
        slower_by_effect = candidate_p99 >= baseline_p99 * (1.0 + min_effect)
        if not slower_by_effect:
            change_point = ChangePointResult(None, 0.0, change_point.candidates)
        elif change_point.introduced_at is None:
            change_point = ChangePointResult(candidate, 1.0, [(candidate, 1.0)])
        quality = prepared.quality
        if require_clean_environment and environment and environment.warnings:
            quality = replace(
                prepared.quality,
                passed=False,
                warnings=[
                    *prepared.quality.warnings,
                    *[f"environment: {warning}" for warning in environment.warnings],
                ],
            )

        is_regression = (
            quality.passed
            and
            slower_by_effect
            and sprt.decision == "regression"
            and mann_whitney.p_value <= p_value_threshold
            and mann_whitney.probability_candidate_slower > 0.5
        )

        unit = by_commit[candidate][0].unit
        comparisons.append(
            ComparisonResult(
                function=function,
                metric=metric,
                unit=unit,
                baseline_commit=baseline,
                candidate_commit=candidate,
                baseline_p99=baseline_p99,
                candidate_p99=candidate_p99,
                p99_delta_percent=percent_delta(baseline_p99, candidate_p99),
                baseline_median=baseline_median,
                candidate_median=candidate_median,
                median_delta_percent=percent_delta(baseline_median, candidate_median),
                mann_whitney=mann_whitney,
                sprt=sprt,
                change_point=change_point,
                quality=quality,
                is_regression=is_regression,
            )
        )

    comparisons.sort(key=lambda item: (not item.is_regression, -item.p99_delta_percent, item.function))
    return AnalysisResult(baseline_commit=baseline, candidate_commit=candidate, comparisons=comparisons, environment=environment)


def result_to_dict(result: AnalysisResult) -> dict[str, Any]:
    return {
        "baseline_commit": result.baseline_commit,
        "candidate_commit": result.candidate_commit,
        "regression_count": len(result.regressions),
        "quality_warning_count": sum(len(item.quality.warnings) for item in result.comparisons)
        + (len(result.environment.warnings) if result.environment else 0),
        "environment": result.environment.__dict__ if result.environment else None,
        "comparisons": [
            {
                "function": item.function,
                "metric": item.metric,
                "unit": item.unit,
                "baseline_commit": item.baseline_commit,
                "candidate_commit": item.candidate_commit,
                "baseline_p99": item.baseline_p99,
                "candidate_p99": item.candidate_p99,
                "p99_delta_percent": item.p99_delta_percent,
                "baseline_median": item.baseline_median,
                "candidate_median": item.candidate_median,
                "median_delta_percent": item.median_delta_percent,
                "is_regression": item.is_regression,
                "mann_whitney": item.mann_whitney.__dict__,
                "sprt": item.sprt.__dict__,
                "quality": item.quality.__dict__,
                "change_point": {
                    "introduced_at": item.change_point.introduced_at,
                    "posterior_probability": item.change_point.posterior_probability,
                    "candidates": item.change_point.candidates,
                },
            }
            for item in result.comparisons
        ],
    }


def result_to_markdown(result: AnalysisResult) -> str:
    lines = [
        "## DriftGuard Performance Report",
        "",
        f"Baseline `{result.baseline_commit}` vs candidate `{result.candidate_commit}`.",
        "",
    ]
    if not result.comparisons:
        lines.append("No comparable benchmark samples were found.")
        return "\n".join(lines)

    if result.environment and result.environment.warnings:
        lines.append("Environment warnings:")
        for warning in result.environment.warnings:
            lines.append(f"- {warning}")
        lines.append("")

    if not result.regressions:
        if result.has_quality_warnings:
            lines.append("No trusted regression decision: benchmark quality gates raised warnings.")
        else:
            lines.append("No statistically supported performance regressions detected.")
    else:
        for item in result.regressions:
            introduced = item.change_point.introduced_at or "unknown"
            confidence = item.sprt.confidence * 100.0
            lines.append(
                f"- `{item.function}()` regressed P99 `{item.metric}` by "
                f"{item.p99_delta_percent:.1f}% - {confidence:.1f}% confidence - "
                f"introduced in commit `{introduced}`."
            )
    quality_warnings = [(item.function, warning) for item in result.comparisons for warning in item.quality.warnings]
    if quality_warnings:
        lines.append("")
        lines.append("Quality warnings:")
        for function, warning in quality_warnings:
            lines.append(f"- `{function}()`: {warning}")
    lines.extend(
        [
            "",
            "| Function | P99 delta | Samples | Quality | SPRT | Mann-Whitney p | Change point |",
            "| --- | ---: | ---: | --- | --- | ---: | --- |",
        ]
    )
    for item in result.comparisons:
        introduced = item.change_point.introduced_at or "-"
        quality = "pass" if item.quality.passed else "blocked"
        samples_text = f"{item.quality.baseline_count}/{item.quality.candidate_count}"
        lines.append(
            f"| `{item.function}()` | {item.p99_delta_percent:.1f}% | {samples_text} | {quality} | "
            f"{item.sprt.decision} ({item.sprt.confidence * 100.0:.1f}%) | "
            f"{item.mann_whitney.p_value:.4f} | `{introduced}` |"
        )
    return "\n".join(lines)
