from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .baselines import sample_to_json
from .models import AnalysisResult, BenchmarkSample
from .pipeline import result_to_dict, sample_from_json


SCHEMA_VERSION = 1


class DriftGuardDatabase:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def init(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
                create table if not exists schema_meta (
                  key text primary key,
                  value text not null
                );

                create table if not exists samples (
                  id integer primary key autoincrement,
                  suite text not null,
                  commit_sha text not null,
                  function_name text not null,
                  metric text not null,
                  value real not null,
                  unit text not null,
                  iteration integer,
                  counters_json text not null,
                  metadata_json text not null,
                  observed_at text not null
                );

                create index if not exists idx_samples_suite_commit
                  on samples (suite, commit_sha);

                create index if not exists idx_samples_function_metric
                  on samples (suite, function_name, metric);

                create table if not exists analysis_runs (
                  id integer primary key autoincrement,
                  suite text not null,
                  baseline_commit text not null,
                  candidate_commit text not null,
                  regression_count integer not null,
                  report_json text not null,
                  created_at text not null
                );
                """
            )
            connection.execute(
                "insert or replace into schema_meta (key, value) values (?, ?)",
                ("schema_version", str(SCHEMA_VERSION)),
            )

    def ingest_samples(self, samples: list[BenchmarkSample], *, suite: str = "default") -> int:
        if not samples:
            return 0
        self.init()
        observed_at = datetime.now(timezone.utc).isoformat()
        rows = [
            (
                suite,
                sample.commit,
                sample.function,
                sample.metric,
                sample.value,
                sample.unit,
                sample.iteration,
                json.dumps(sample.counters, sort_keys=True, separators=(",", ":")),
                json.dumps(sample.metadata, sort_keys=True, separators=(",", ":")),
                observed_at,
            )
            for sample in samples
        ]
        with self.connect() as connection:
            connection.executemany(
                """
                insert into samples (
                  suite, commit_sha, function_name, metric, value, unit, iteration,
                  counters_json, metadata_json, observed_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
        return len(rows)

    def list_commits(self, *, suite: str = "default") -> list[str]:
        self.init()
        with self.connect() as connection:
            rows = connection.execute(
                """
                select commit_sha, min(id) as first_seen
                from samples
                where suite = ?
                group by commit_sha
                order by first_seen
                """,
                (suite,),
            ).fetchall()
        return [str(row["commit_sha"]) for row in rows]

    def latest_commit(self, *, suite: str = "default", exclude: str | None = None) -> str | None:
        commits = [commit for commit in self.list_commits(suite=suite) if commit != exclude]
        return commits[-1] if commits else None

    def load_samples(self, commits: list[str], *, suite: str = "default") -> list[BenchmarkSample]:
        if not commits:
            return []
        self.init()
        placeholders = ",".join("?" for _ in commits)
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                select commit_sha, function_name, metric, value, unit, iteration, counters_json, metadata_json
                from samples
                where suite = ? and commit_sha in ({placeholders})
                order by id
                """,
                [suite, *commits],
            ).fetchall()

        samples: list[BenchmarkSample] = []
        for row in rows:
            samples.append(
                sample_from_json(
                    {
                        "commit": row["commit_sha"],
                        "function": row["function_name"],
                        "metric": row["metric"],
                        "value": row["value"],
                        "unit": row["unit"],
                        "iteration": row["iteration"],
                        "counters": json.loads(row["counters_json"]),
                        **json.loads(row["metadata_json"]),
                    }
                )
            )
        return samples

    def save_analysis(self, result: AnalysisResult, *, suite: str = "default") -> int:
        self.init()
        payload = result_to_dict(result)
        created_at = datetime.now(timezone.utc).isoformat()
        with self.connect() as connection:
            cursor = connection.execute(
                """
                insert into analysis_runs (
                  suite, baseline_commit, candidate_commit, regression_count, report_json, created_at
                ) values (?, ?, ?, ?, ?, ?)
                """,
                (
                    suite,
                    result.baseline_commit,
                    result.candidate_commit,
                    len(result.regressions),
                    json.dumps(payload, sort_keys=True, separators=(",", ":")),
                    created_at,
                ),
            )
            return int(cursor.lastrowid)

    def latest_analysis(self, *, suite: str = "default") -> dict[str, Any] | None:
        self.init()
        with self.connect() as connection:
            row = connection.execute(
                """
                select report_json
                from analysis_runs
                where suite = ?
                order by id desc
                limit 1
                """,
                (suite,),
            ).fetchone()
        if row is None:
            return None
        return json.loads(row["report_json"])

    def export_samples_jsonl(self, output: str | Path, *, suite: str = "default") -> int:
        samples = self.load_samples(self.list_commits(suite=suite), suite=suite)
        output_path = Path(output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8") as handle:
            for sample in samples:
                handle.write(json.dumps(sample_to_json(sample), sort_keys=True, separators=(",", ":")) + "\n")
        return len(samples)
