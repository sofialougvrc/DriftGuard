from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable

from .models import BenchmarkSample, ChangePointResult


def _normal_inverse_gamma_log_evidence(values: list[float]) -> float:
    """Closed-form Gaussian marginal likelihood with weak conjugate priors."""

    if not values:
        return float("-inf")

    n = len(values)
    mean = sum(values) / n
    alpha0 = 1.0
    beta0 = 1.0
    kappa0 = 1e-3
    mu0 = mean

    squared_error = sum((value - mean) ** 2 for value in values)
    kappa_n = kappa0 + n
    alpha_n = alpha0 + n / 2.0
    beta_n = beta0 + 0.5 * squared_error + (kappa0 * n * (mean - mu0) ** 2) / (2.0 * kappa_n)

    return (
        math.lgamma(alpha_n)
        - math.lgamma(alpha0)
        + alpha0 * math.log(beta0)
        - alpha_n * math.log(beta_n)
        + 0.5 * (math.log(kappa0) - math.log(kappa_n))
        - (n / 2.0) * math.log(math.pi)
    )


def bayesian_change_point(samples: Iterable[BenchmarkSample], function: str, metric: str) -> ChangePointResult:
    by_commit: dict[str, list[float]] = defaultdict(list)
    commit_order: list[str] = []

    for sample in samples:
        if sample.function != function or sample.metric != metric:
            continue
        if sample.commit not in by_commit:
            commit_order.append(sample.commit)
        by_commit[sample.commit].append(math.log(max(sample.value, 1e-12)))

    if len(commit_order) < 3:
        return ChangePointResult(None, 0.0, [])

    scores: list[tuple[str, float]] = []
    all_values = [value for commit in commit_order for value in by_commit[commit]]
    null_score = _normal_inverse_gamma_log_evidence(all_values)

    for split_index in range(2, len(commit_order)):
        left = [value for commit in commit_order[:split_index] for value in by_commit[commit]]
        right = [value for commit in commit_order[split_index:] for value in by_commit[commit]]
        if len(left) < 3 or len(right) < 3:
            continue
        penalty = math.log(len(all_values))
        imbalance_penalty = penalty * abs(split_index - (len(commit_order) - split_index))
        score = _normal_inverse_gamma_log_evidence(left) + _normal_inverse_gamma_log_evidence(right) - penalty - imbalance_penalty
        scores.append((commit_order[split_index], score - null_score))

    if not scores:
        return ChangePointResult(None, 0.0, [])

    max_score = max(score for _, score in scores)
    weights = [(commit, math.exp(score - max_score)) for commit, score in scores]
    total = sum(weight for _, weight in weights)
    posterior = [(commit, weight / total) for commit, weight in weights]
    posterior.sort(key=lambda item: item[1], reverse=True)
    introduced_at, probability = posterior[0]

    return ChangePointResult(
        introduced_at=introduced_at,
        posterior_probability=probability,
        candidates=posterior[:5],
    )
