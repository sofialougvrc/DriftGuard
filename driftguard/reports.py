from __future__ import annotations

import json
from html import escape

from .models import AnalysisResult, ComparisonResult
from .pipeline import result_to_dict, result_to_markdown


def render_result(result: AnalysisResult, format_name: str) -> str:
    if format_name == "json":
        return json.dumps(result_to_dict(result), indent=2, sort_keys=True)
    if format_name == "markdown":
        return result_to_markdown(result)
    if format_name == "sarif":
        return result_to_sarif(result)
    if format_name == "junit":
        return result_to_junit(result)
    raise ValueError(f"unsupported report format: {format_name}")


def _message(item: ComparisonResult) -> str:
    introduced = item.change_point.introduced_at or "unknown"
    return (
        f"{item.function}() regressed P99 {item.metric} by {item.p99_delta_percent:.1f}% "
        f"with {item.sprt.confidence * 100.0:.1f}% confidence; introduced in {introduced}."
    )


def result_to_sarif(result: AnalysisResult) -> str:
    sarif = {
        "version": "2.1.0",
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "DriftGuard",
                        "informationUri": "https://github.com/driftguard/driftguard",
                        "rules": [
                            {
                                "id": "DG001",
                                "name": "PerformanceRegression",
                                "shortDescription": {"text": "Statistically supported performance regression"},
                                "fullDescription": {
                                    "text": "A benchmark regressed with SPRT evidence and a significant Mann-Whitney U test."
                                },
                                "defaultConfiguration": {"level": "error"},
                            }
                        ],
                    }
                },
                "results": [
                    {
                        "ruleId": "DG001",
                        "level": "error",
                        "message": {"text": _message(item)},
                        "properties": {
                            "function": item.function,
                            "metric": item.metric,
                            "baselineCommit": item.baseline_commit,
                            "candidateCommit": item.candidate_commit,
                            "p99DeltaPercent": item.p99_delta_percent,
                            "mannWhitneyPValue": item.mann_whitney.p_value,
                            "sprtDecision": item.sprt.decision,
                            "sprtConfidence": item.sprt.confidence,
                            "changePoint": item.change_point.introduced_at,
                            "qualityPassed": item.quality.passed,
                            "baselineSamples": item.quality.baseline_count,
                            "candidateSamples": item.quality.candidate_count,
                            "qualityWarnings": item.quality.warnings,
                        },
                    }
                    for item in result.regressions
                ],
            }
        ],
    }
    return json.dumps(sarif, indent=2, sort_keys=True)


def result_to_junit(result: AnalysisResult) -> str:
    failures = len(result.regressions)
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        (
            f'<testsuite name="DriftGuard" tests="{len(result.comparisons)}" '
            f'failures="{failures}" errors="0" skipped="0">'
        ),
    ]
    for item in result.comparisons:
        testcase_name = f"{item.function}.{item.metric}"
        lines.append(f'  <testcase classname="DriftGuard" name="{escape(testcase_name)}">')
        if item.is_regression:
            lines.append(f'    <failure message="{escape(_message(item))}">')
            lines.append(escape(json.dumps(result_to_dict(AnalysisResult(result.baseline_commit, result.candidate_commit, [item])), indent=2)))
            lines.append("    </failure>")
        lines.append("  </testcase>")
    lines.append("</testsuite>")
    return "\n".join(lines)
