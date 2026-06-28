from __future__ import annotations

import math
from collections.abc import Sequence

from .models import MannWhitneyResult


def median(values: Sequence[float]) -> float:
    return quantile(values, 0.5)


def quantile(values: Sequence[float], q: float) -> float:
    if not values:
        raise ValueError("quantile requires at least one value")
    if q < 0 or q > 1:
        raise ValueError("q must be in [0, 1]")

    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]

    position = q * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def percent_delta(baseline: float, candidate: float) -> float:
    if baseline == 0:
        return math.inf if candidate > 0 else 0.0
    return ((candidate - baseline) / baseline) * 100.0


def normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _rank_with_ties(values: list[tuple[float, int]]) -> tuple[list[float], list[int]]:
    ranks = [0.0] * len(values)
    tie_sizes: list[int] = []
    i = 0
    while i < len(values):
        j = i + 1
        while j < len(values) and values[j][0] == values[i][0]:
            j += 1
        avg_rank = (i + 1 + j) / 2.0
        for k in range(i, j):
            ranks[values[k][1]] = avg_rank
        if j - i > 1:
            tie_sizes.append(j - i)
        i = j
    return ranks, tie_sizes


def mann_whitney_u(baseline: Sequence[float], candidate: Sequence[float]) -> MannWhitneyResult:
    """Two-sided Mann-Whitney U with tie-corrected normal approximation.

    The returned probability is P(candidate > baseline), which is the useful
    direction for latency regressions where higher is slower.
    """

    if not baseline or not candidate:
        raise ValueError("mann_whitney_u requires two non-empty samples")

    n1 = len(baseline)
    n2 = len(candidate)
    combined = [(value, 0) for value in baseline] + [(value, 1) for value in candidate]
    indexed = sorted((value, idx) for idx, (value, _) in enumerate(combined))
    ranks, tie_sizes = _rank_with_ties(indexed)

    rank_sum_baseline = sum(ranks[i] for i in range(n1))
    u_baseline = rank_sum_baseline - (n1 * (n1 + 1)) / 2.0
    u_candidate = n1 * n2 - u_baseline

    probability_candidate_slower = u_candidate / (n1 * n2)
    cliffs_delta = (2.0 * probability_candidate_slower) - 1.0

    mean_u = n1 * n2 / 2.0
    tie_correction = sum(t**3 - t for t in tie_sizes)
    total = n1 + n2
    variance = (n1 * n2 / 12.0) * ((total + 1) - tie_correction / (total * (total - 1)))
    if variance <= 0:
        p_value = 1.0
    else:
        correction = 0.5 if u_candidate > mean_u else -0.5
        z = (u_candidate - mean_u - correction) / math.sqrt(variance)
        p_value = 2.0 * min(normal_cdf(z), 1.0 - normal_cdf(z))

    return MannWhitneyResult(
        u_statistic=u_candidate,
        p_value=max(0.0, min(1.0, p_value)),
        probability_candidate_slower=probability_candidate_slower,
        cliffs_delta=cliffs_delta,
    )
