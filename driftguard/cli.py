from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .baselines import BaselineStore
from .database import DriftGuardDatabase
from .environment import capture_environment
from .models import AnalysisResult
from .pipeline import analyze_samples, load_jsonl
from .quality import QualityPolicy
from .reports import render_result


REPORT_FORMATS = ["json", "markdown", "sarif", "junit"]


def _add_quality_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--min-samples", type=int, default=20, help="minimum retained samples per side after warmup/outlier filtering")
    parser.add_argument("--warmup", type=int, default=3, help="iterations to discard from each side before analysis")
    parser.add_argument("--outlier-mad", type=float, default=6.0, help="MAD threshold for robust outlier filtering")
    parser.add_argument("--max-outlier-fraction", type=float, default=0.15, help="max allowed fraction of filtered samples per side")
    parser.add_argument("--max-cv", type=float, default=0.20, help="max allowed coefficient of variation per side")
    parser.add_argument("--max-mad-ratio", type=float, default=0.15, help="max allowed robust MAD/median noise ratio per side")
    parser.add_argument("--no-environment", action="store_true", help="skip host environment fingerprinting")
    parser.add_argument("--require-clean-environment", action="store_true", help="block trusted regressions when environment warnings are present")


def _quality_policy(args: argparse.Namespace) -> QualityPolicy:
    return QualityPolicy(
        min_samples=args.min_samples,
        warmup_iterations=args.warmup,
        outlier_mad_threshold=args.outlier_mad,
        max_outlier_fraction=args.max_outlier_fraction,
        max_cv=args.max_cv,
        max_mad_ratio=args.max_mad_ratio,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="driftguard", description="Continuous performance regression intelligence")
    subparsers = parser.add_subparsers(dest="command", required=True)

    analyze = subparsers.add_parser("analyze", help="analyze JSONL benchmark samples")
    analyze.add_argument("input", type=Path, help="benchmark JSONL input")
    analyze.add_argument("--baseline", help="baseline commit id")
    analyze.add_argument("--candidate", help="candidate commit id")
    analyze.add_argument("--min-effect", type=float, default=0.05, help="minimum meaningful slowdown, default: 0.05")
    analyze.add_argument("--alpha", type=float, default=0.01, help="SPRT false positive rate")
    analyze.add_argument("--beta", type=float, default=0.05, help="SPRT false negative rate")
    analyze.add_argument("--p-value", type=float, default=0.05, help="Mann-Whitney p-value threshold")
    analyze.add_argument("--format", choices=REPORT_FORMATS, default="json")
    analyze.add_argument("--output", type=Path, help="optional output file")
    _add_quality_args(analyze)

    record = subparsers.add_parser("record", help="record a benchmark stream into a local baseline store")
    record.add_argument("input", type=Path, help="benchmark JSONL input")
    record.add_argument("--suite", default="default", help="benchmark suite name")
    record.add_argument("--store", type=Path, default=Path(".driftguard/history"), help="baseline store directory")

    compare = subparsers.add_parser("compare", help="compare separate baseline and candidate streams")
    compare.add_argument("--baseline-stream", type=Path, required=True)
    compare.add_argument("--candidate-stream", type=Path, required=True)
    compare.add_argument("--baseline", help="baseline commit id")
    compare.add_argument("--candidate", help="candidate commit id")
    compare.add_argument("--min-effect", type=float, default=0.05)
    compare.add_argument("--alpha", type=float, default=0.01)
    compare.add_argument("--beta", type=float, default=0.05)
    compare.add_argument("--p-value", type=float, default=0.05)
    compare.add_argument("--format", choices=REPORT_FORMATS, default="json")
    compare.add_argument("--output", type=Path)
    _add_quality_args(compare)

    ci = subparsers.add_parser("ci", help="compare a candidate stream against recorded baseline history")
    ci.add_argument("--candidate-stream", type=Path, required=True)
    ci.add_argument("--candidate", help="candidate commit id")
    ci.add_argument("--baseline", help="baseline commit id")
    ci.add_argument("--suite", default="default", help="benchmark suite name")
    ci.add_argument("--store", type=Path, default=Path(".driftguard/history"), help="baseline store directory")
    ci.add_argument("--bootstrap-if-missing", action="store_true", help="record candidate as the first baseline when history is empty")
    ci.add_argument("--promote-on-pass", action="store_true", help="record candidate as a baseline when no regression is found")
    ci.add_argument("--min-effect", type=float, default=0.05)
    ci.add_argument("--alpha", type=float, default=0.01)
    ci.add_argument("--beta", type=float, default=0.05)
    ci.add_argument("--p-value", type=float, default=0.05)
    ci.add_argument("--format", choices=REPORT_FORMATS, default="json")
    ci.add_argument("--output", type=Path)
    _add_quality_args(ci)

    db_init = subparsers.add_parser("db-init", help="initialize a SQLite DriftGuard history database")
    db_init.add_argument("--database", type=Path, default=Path(".driftguard/driftguard.db"))

    db_ingest = subparsers.add_parser("db-ingest", help="ingest benchmark JSONL into SQLite history")
    db_ingest.add_argument("input", type=Path)
    db_ingest.add_argument("--suite", default="default")
    db_ingest.add_argument("--database", type=Path, default=Path(".driftguard/driftguard.db"))

    db_analyze = subparsers.add_parser("db-analyze", help="analyze two commits stored in SQLite history")
    db_analyze.add_argument("--suite", default="default")
    db_analyze.add_argument("--database", type=Path, default=Path(".driftguard/driftguard.db"))
    db_analyze.add_argument("--baseline", help="baseline commit id")
    db_analyze.add_argument("--candidate", help="candidate commit id")
    db_analyze.add_argument("--save-run", action="store_true", help="store the generated analysis report in SQLite")
    db_analyze.add_argument("--min-effect", type=float, default=0.05)
    db_analyze.add_argument("--alpha", type=float, default=0.01)
    db_analyze.add_argument("--beta", type=float, default=0.05)
    db_analyze.add_argument("--p-value", type=float, default=0.05)
    db_analyze.add_argument("--format", choices=REPORT_FORMATS, default="json")
    db_analyze.add_argument("--output", type=Path)
    _add_quality_args(db_analyze)

    db_export = subparsers.add_parser("db-export", help="export SQLite samples back to JSONL")
    db_export.add_argument("--suite", default="default")
    db_export.add_argument("--database", type=Path, default=Path(".driftguard/driftguard.db"))
    db_export.add_argument("--output", type=Path, required=True)

    doctor = subparsers.add_parser("doctor", help="print benchmark host diagnostics")
    doctor.add_argument("--format", choices=["json", "markdown"], default="markdown")
    return parser


def _render_result(args: argparse.Namespace, result: AnalysisResult) -> str:
    return render_result(result, args.format)


def _write_or_print(rendered: str, output: Path | None) -> None:
    if output:
        output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "analyze":
        samples = load_jsonl(args.input)
        result = analyze_samples(
            samples,
            baseline_commit=args.baseline,
            candidate_commit=args.candidate,
            min_effect=args.min_effect,
            alpha=args.alpha,
            beta=args.beta,
            p_value_threshold=args.p_value,
            quality_policy=_quality_policy(args),
            capture_env=not args.no_environment,
            require_clean_environment=args.require_clean_environment,
        )
        rendered = _render_result(args, result)
        _write_or_print(rendered, args.output)
        return 1 if result.regressions else 0

    if args.command == "record":
        samples = load_jsonl(args.input)
        records = BaselineStore(args.store).record_samples(samples, suite=args.suite)
        print(json.dumps({"recorded": [record.__dict__ for record in records]}, indent=2, sort_keys=True))
        return 0

    if args.command == "compare":
        samples = load_jsonl(args.baseline_stream) + load_jsonl(args.candidate_stream)
        result = analyze_samples(
            samples,
            baseline_commit=args.baseline,
            candidate_commit=args.candidate,
            min_effect=args.min_effect,
            alpha=args.alpha,
            beta=args.beta,
            p_value_threshold=args.p_value,
            quality_policy=_quality_policy(args),
            capture_env=not args.no_environment,
            require_clean_environment=args.require_clean_environment,
        )
        rendered = _render_result(args, result)
        _write_or_print(rendered, args.output)
        return 1 if result.regressions else 0

    if args.command == "ci":
        store = BaselineStore(args.store)
        candidate_samples = load_jsonl(args.candidate_stream)
        candidate_commit = args.candidate or candidate_samples[-1].commit
        baseline_commit = args.baseline or store.latest_commit(suite=args.suite, exclude=candidate_commit)
        if baseline_commit is None:
            if args.bootstrap_if_missing:
                store.record_samples(candidate_samples, suite=args.suite)
                result = AnalysisResult(baseline_commit="none", candidate_commit=candidate_commit, comparisons=[])
                rendered = _render_result(args, result)
                _write_or_print(rendered, args.output)
                return 0
            raise ValueError(f"no baseline found in suite {args.suite!r}; run `driftguard record` first")
        baseline_samples = store.load_commit(baseline_commit, suite=args.suite)
        result = analyze_samples(
            baseline_samples + candidate_samples,
            baseline_commit=baseline_commit,
            candidate_commit=candidate_commit,
            min_effect=args.min_effect,
            alpha=args.alpha,
                beta=args.beta,
                p_value_threshold=args.p_value,
                quality_policy=_quality_policy(args),
                capture_env=not args.no_environment,
                require_clean_environment=args.require_clean_environment,
            )
        rendered = _render_result(args, result)
        _write_or_print(rendered, args.output)
        if args.promote_on_pass and not result.regressions:
            store.record_samples(candidate_samples, suite=args.suite)
        return 1 if result.regressions else 0

    if args.command == "db-init":
        DriftGuardDatabase(args.database).init()
        print(json.dumps({"database": str(args.database), "initialized": True}, indent=2, sort_keys=True))
        return 0

    if args.command == "db-ingest":
        samples = load_jsonl(args.input)
        count = DriftGuardDatabase(args.database).ingest_samples(samples, suite=args.suite)
        print(json.dumps({"database": str(args.database), "ingested": count, "suite": args.suite}, indent=2, sort_keys=True))
        return 0

    if args.command == "db-analyze":
        database = DriftGuardDatabase(args.database)
        commits = database.list_commits(suite=args.suite)
        candidate_commit = args.candidate or (commits[-1] if commits else None)
        if candidate_commit is None:
            raise ValueError(f"no samples found in database suite {args.suite!r}")
        baseline_commit = args.baseline or database.latest_commit(suite=args.suite, exclude=candidate_commit)
        if baseline_commit is None:
            raise ValueError(f"no baseline commit found in database suite {args.suite!r}")
        samples = database.load_samples(commits, suite=args.suite)
        result = analyze_samples(
            samples,
            baseline_commit=baseline_commit,
            candidate_commit=candidate_commit,
            min_effect=args.min_effect,
            alpha=args.alpha,
            beta=args.beta,
            p_value_threshold=args.p_value,
            quality_policy=_quality_policy(args),
            capture_env=not args.no_environment,
            require_clean_environment=args.require_clean_environment,
        )
        if args.save_run:
            database.save_analysis(result, suite=args.suite)
        rendered = _render_result(args, result)
        _write_or_print(rendered, args.output)
        return 1 if result.regressions else 0

    if args.command == "db-export":
        count = DriftGuardDatabase(args.database).export_samples_jsonl(args.output, suite=args.suite)
        print(json.dumps({"exported": count, "output": str(args.output), "suite": args.suite}, indent=2, sort_keys=True))
        return 0

    if args.command == "doctor":
        environment = capture_environment()
        if args.format == "json":
            print(json.dumps(environment.__dict__, indent=2, sort_keys=True))
        else:
            print("## DriftGuard Host Diagnostics")
            print("")
            print(f"- Platform: {environment.platform}")
            print(f"- Machine: {environment.machine}")
            print(f"- CPU count: {environment.cpu_count}")
            print(f"- 1m load average: {environment.load_average_1m}")
            print(f"- CPU governor: {environment.cpu_governor or 'unknown'}")
            print(f"- perf_event_paranoid: {environment.perf_event_paranoid}")
            if environment.warnings:
                print("")
                print("Warnings:")
                for warning in environment.warnings:
                    print(f"- {warning}")
            else:
                print("")
                print("No host warnings detected.")
        return 1 if environment.warnings else 0

    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
