from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from driftguard.baselines import BaselineStore, write_jsonl
from driftguard.changepoint import bayesian_change_point
from driftguard.database import DriftGuardDatabase
from driftguard.models import BenchmarkSample
from driftguard.pipeline import analyze_samples, result_to_markdown
from driftguard.reports import result_to_junit, result_to_sarif
from driftguard.sprt import sprt_regression_test
from driftguard.stats import mann_whitney_u, quantile


class DriftGuardStatsTests(unittest.TestCase):
    def test_quantile_interpolates(self) -> None:
        self.assertEqual(quantile([10, 20, 30, 40], 0.5), 25)
        self.assertAlmostEqual(quantile([10, 20, 30, 40], 0.99), 39.7)

    def test_mann_whitney_direction_for_latency_regression(self) -> None:
        baseline = [100, 101, 102, 103, 104]
        candidate = [118, 119, 120, 121, 122]

        result = mann_whitney_u(baseline, candidate)

        self.assertEqual(result.probability_candidate_slower, 1.0)
        self.assertEqual(result.cliffs_delta, 1.0)
        self.assertLess(result.p_value, 0.05)

    def test_sprt_decides_regression_for_large_slowdown(self) -> None:
        baseline = [100_000 + (i % 5) * 300 for i in range(40)]
        candidate = [115_000 + (i % 5) * 300 for i in range(40)]

        result = sprt_regression_test(baseline, candidate, min_effect=0.05)

        self.assertEqual(result.decision, "regression")
        self.assertGreater(result.confidence, 0.99)

    def test_change_point_finds_introducing_commit(self) -> None:
        samples: list[BenchmarkSample] = []
        for commit, center in [("a111", 100), ("b222", 101), ("4a8f", 116), ("d444", 117)]:
            for i in range(20):
                samples.append(
                    BenchmarkSample(
                        commit=commit,
                        function="processOrder",
                        metric="latency_ns",
                        value=center + (i % 4),
                    )
                )

        result = bayesian_change_point(samples, "processOrder", "latency_ns")

        self.assertEqual(result.introduced_at, "4a8f")
        self.assertGreater(result.posterior_probability, 0.5)

    def test_pipeline_flags_regression_and_renders_markdown(self) -> None:
        samples: list[BenchmarkSample] = []
        for commit, center in [("a111", 100_000), ("b222", 101_000), ("4a8f", 116_000)]:
            for i in range(45):
                samples.append(
                    BenchmarkSample(
                        commit=commit,
                        function="processOrder",
                        metric="latency_ns",
                        value=center + (i % 7) * 250,
                    )
                )
                samples.append(
                    BenchmarkSample(
                        commit=commit,
                        function="loadCatalog",
                        metric="latency_ns",
                        value=50_000 + (i % 7) * 120,
                    )
                )

        result = analyze_samples(samples, baseline_commit="b222", candidate_commit="4a8f", min_effect=0.05)
        markdown = result_to_markdown(result)

        self.assertEqual(len(result.regressions), 1)
        self.assertEqual(result.regressions[0].function, "processOrder")
        self.assertIn("processOrder()", markdown)
        self.assertIn("introduced in commit `4a8f`", markdown)

    def test_quality_gate_blocks_tiny_sample_regression(self) -> None:
        samples: list[BenchmarkSample] = []
        for commit, center in [("base", 100_000), ("head", 140_000)]:
            for i in range(6):
                samples.append(
                    BenchmarkSample(
                        commit=commit,
                        function="processOrder",
                        metric="latency_ns",
                        value=center + i,
                        iteration=i,
                    )
                )

        result = analyze_samples(samples, baseline_commit="base", candidate_commit="head", capture_env=False)

        self.assertEqual(len(result.regressions), 0)
        self.assertFalse(result.comparisons[0].quality.passed)
        self.assertIn("need at least 20", " ".join(result.comparisons[0].quality.warnings))

    def test_two_commit_regression_reports_candidate_as_change_point(self) -> None:
        samples: list[BenchmarkSample] = []
        for commit, center in [("base123", 100_000), ("head456", 114_000)]:
            for i in range(40):
                samples.append(
                    BenchmarkSample(
                        commit=commit,
                        function="processOrder",
                        metric="latency_ns",
                        value=center + (i % 5) * 100,
                        iteration=i,
                    )
                )

        result = analyze_samples(samples, baseline_commit="base123", candidate_commit="head456", capture_env=False)

        self.assertEqual(result.regressions[0].change_point.introduced_at, "head456")
        self.assertEqual(result.regressions[0].change_point.posterior_probability, 1.0)

    def test_cli_outputs_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            input_path = Path(directory) / "bench.jsonl"
            rows = []
            for commit, center in [("base", 100_000), ("head", 113_000)]:
                for i in range(35):
                    rows.append(
                        {
                            "commit": commit,
                            "function": "processOrder",
                            "metric": "latency_ns",
                            "value": center + (i % 5) * 100,
                        }
                    )
            input_path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")

            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "driftguard.cli",
                    "analyze",
                    str(input_path),
                    "--baseline",
                    "base",
                    "--candidate",
                    "head",
                    "--format",
                    "json",
                ],
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

        self.assertEqual(completed.returncode, 1)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["regression_count"], 1)

    def test_baseline_store_records_and_loads_latest_commit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = BaselineStore(Path(directory) / "history")
            samples = [
                BenchmarkSample(commit="base", function="processOrder", metric="latency_ns", value=100_000 + i)
                for i in range(10)
            ]

            records = store.record_samples(samples, suite="orders")
            loaded = store.load_commit("base", suite="orders")
            latest = store.latest_commit(suite="orders")

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].commit, "base")
        self.assertEqual(latest, "base")
        self.assertEqual(len(loaded), 10)

    def test_ci_command_compares_against_recorded_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            baseline_path = root / "baseline.jsonl"
            candidate_path = root / "candidate.jsonl"
            store_path = root / "history"
            baseline = [
                BenchmarkSample(commit="base", function="processOrder", metric="latency_ns", value=100_000 + (i % 5) * 100)
                for i in range(40)
            ]
            candidate = [
                BenchmarkSample(commit="head", function="processOrder", metric="latency_ns", value=114_000 + (i % 5) * 100)
                for i in range(40)
            ]
            write_jsonl(baseline, baseline_path)
            write_jsonl(candidate, candidate_path)

            record = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "driftguard.cli",
                    "record",
                    str(baseline_path),
                    "--suite",
                    "orders",
                    "--store",
                    str(store_path),
                ],
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            compare = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "driftguard.cli",
                    "ci",
                    "--candidate-stream",
                    str(candidate_path),
                    "--candidate",
                    "head",
                    "--suite",
                    "orders",
                    "--store",
                    str(store_path),
                    "--format",
                    "json",
                ],
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

        self.assertEqual(record.returncode, 0, record.stderr)
        self.assertEqual(compare.returncode, 1, compare.stderr)
        payload = json.loads(compare.stdout)
        self.assertEqual(payload["baseline_commit"], "base")
        self.assertEqual(payload["candidate_commit"], "head")
        self.assertEqual(payload["regression_count"], 1)

    def test_sarif_and_junit_reports_include_regression(self) -> None:
        samples: list[BenchmarkSample] = []
        for commit, center in [("base", 100_000), ("head", 114_000)]:
            for i in range(35):
                samples.append(
                    BenchmarkSample(
                        commit=commit,
                        function="processOrder",
                        metric="latency_ns",
                        value=center + (i % 5) * 100,
                    )
                )
        result = analyze_samples(samples, baseline_commit="base", candidate_commit="head")

        sarif = json.loads(result_to_sarif(result))
        junit = result_to_junit(result)

        self.assertEqual(sarif["runs"][0]["results"][0]["ruleId"], "DG001")
        self.assertIn("<testsuite", junit)
        self.assertIn("<failure", junit)
        self.assertIn("processOrder", junit)

    def test_sqlite_database_ingests_analyzes_and_exports(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database_path = root / "driftguard.db"
            output_path = root / "export.jsonl"
            database = DriftGuardDatabase(database_path)
            baseline = [
                BenchmarkSample(commit="base", function="processOrder", metric="latency_ns", value=100_000 + (i % 5) * 100)
                for i in range(40)
            ]
            candidate = [
                BenchmarkSample(commit="head", function="processOrder", metric="latency_ns", value=114_000 + (i % 5) * 100)
                for i in range(40)
            ]

            self.assertEqual(database.ingest_samples(baseline + candidate, suite="orders"), 80)
            self.assertEqual(database.list_commits(suite="orders"), ["base", "head"])
            loaded = database.load_samples(["base", "head"], suite="orders")
            result = analyze_samples(loaded, baseline_commit="base", candidate_commit="head")
            run_id = database.save_analysis(result, suite="orders")
            exported = database.export_samples_jsonl(output_path, suite="orders")
            exported_lines = len(output_path.read_text(encoding="utf-8").splitlines())

        self.assertEqual(run_id, 1)
        self.assertEqual(exported, 80)
        self.assertEqual(result.regressions[0].function, "processOrder")
        self.assertEqual(exported_lines, 80)

    def test_db_analyze_cli_outputs_sarif(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database_path = root / "driftguard.db"
            input_path = root / "samples.jsonl"
            samples = []
            for commit, center in [("base", 100_000), ("head", 114_000)]:
                for i in range(35):
                    samples.append(
                        BenchmarkSample(
                            commit=commit,
                            function="processOrder",
                            metric="latency_ns",
                            value=center + (i % 5) * 100,
                        )
                    )
            write_jsonl(samples, input_path)

            ingest = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "driftguard.cli",
                    "db-ingest",
                    str(input_path),
                    "--suite",
                    "orders",
                    "--database",
                    str(database_path),
                ],
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            analyze = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "driftguard.cli",
                    "db-analyze",
                    "--suite",
                    "orders",
                    "--database",
                    str(database_path),
                    "--baseline",
                    "base",
                    "--candidate",
                    "head",
                    "--format",
                    "sarif",
                    "--save-run",
                ],
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

        self.assertEqual(ingest.returncode, 0, ingest.stderr)
        self.assertEqual(analyze.returncode, 1, analyze.stderr)
        payload = json.loads(analyze.stdout)
        self.assertEqual(payload["runs"][0]["results"][0]["ruleId"], "DG001")

    def test_doctor_outputs_environment_json(self) -> None:
        completed = subprocess.run(
            [sys.executable, "-m", "driftguard.cli", "doctor", "--format", "json"],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        self.assertIn(completed.returncode, {0, 1})
        payload = json.loads(completed.stdout)
        self.assertIn("platform", payload)
        self.assertIn("warnings", payload)


if __name__ == "__main__":
    unittest.main()
