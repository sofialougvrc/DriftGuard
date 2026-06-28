from __future__ import annotations

import math
from collections.abc import Sequence

from .models import SprtResult
from .stats import median, normal_cdf


def _mad(values: Sequence[float], center: float) -> float:
    deviations = [abs(value - center) for value in values]
    return median(deviations)


def _log_normal_pdf(x: float, mean: float, sigma: float) -> float:
    return -math.log(sigma) - 0.5 * math.log(2.0 * math.pi) - ((x - mean) ** 2) / (2.0 * sigma**2)


def sprt_regression_test(
    baseline: Sequence[float],
    candidate: Sequence[float],
    *,
    min_effect: float = 0.05,
    alpha: float = 0.01,
    beta: float = 0.05,
) -> SprtResult:
    """Sequential test for a meaningful slowdown.

    H0: candidate mean log-latency is unchanged.
    H1: candidate mean log-latency is at least `min_effect` slower.
    """

    if not baseline or not candidate:
        raise ValueError("sprt_regression_test requires two non-empty samples")
    if min_effect <= 0:
        raise ValueError("min_effect must be positive")
    if not 0 < alpha < 1 or not 0 < beta < 1:
        raise ValueError("alpha and beta must be probabilities")

    epsilon = 1e-12
    baseline_logs = [math.log(max(value, epsilon)) for value in baseline]
    candidate_logs = [math.log(max(value, epsilon)) for value in candidate]

    mu0 = sum(baseline_logs) / len(baseline_logs)
    robust_sigma = 1.4826 * _mad(baseline_logs, median(baseline_logs))
    sample_sigma = math.sqrt(sum((value - mu0) ** 2 for value in baseline_logs) / max(1, len(baseline_logs) - 1))
    sigma = max(robust_sigma, sample_sigma, 1e-6)
    mu1 = mu0 + math.log1p(min_effect)

    upper = math.log((1.0 - beta) / alpha)
    lower = math.log(beta / (1.0 - alpha))
    llr = 0.0
    decision = "continue"
    observations = 0

    for observation in candidate_logs:
        llr += _log_normal_pdf(observation, mu1, sigma) - _log_normal_pdf(observation, mu0, sigma)
        observations += 1
        if llr >= upper:
            decision = "regression"
            break
        if llr <= lower:
            decision = "no_regression"
            break

    if decision == "continue":
        if llr > 0:
            confidence = normal_cdf(llr / max(1.0, math.sqrt(observations)))
        else:
            confidence = 1.0 - normal_cdf(llr / max(1.0, math.sqrt(observations)))
    else:
        confidence = 1.0 / (1.0 + math.exp(-abs(llr)))

    return SprtResult(
        decision=decision,
        log_likelihood_ratio=llr,
        upper_boundary=upper,
        lower_boundary=lower,
        confidence=max(0.0, min(1.0, confidence)),
        observations=observations,
    )
