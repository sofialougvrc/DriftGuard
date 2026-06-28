from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import BenchmarkSample
from .pipeline import load_jsonl


@dataclass(frozen=True)
class BaselineRecord:
    suite: str
    commit: str
    path: str
    sample_count: int
    recorded_at: str


def sample_to_json(sample: BenchmarkSample) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "commit": sample.commit,
        "function": sample.function,
        "metric": sample.metric,
        "value": sample.value,
        "unit": sample.unit,
    }
    if sample.iteration is not None:
        payload["iteration"] = sample.iteration
    if sample.counters:
        payload["counters"] = sample.counters
    payload.update(sample.metadata)
    return payload


def write_jsonl(samples: list[BenchmarkSample], path: str | Path) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        for sample in samples:
            handle.write(json.dumps(sample_to_json(sample), sort_keys=True, separators=(",", ":")) + "\n")


class BaselineStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.manifest_path = self.root / "manifest.json"

    def _load_manifest(self) -> list[BaselineRecord]:
        if not self.manifest_path.exists():
            return []
        with self.manifest_path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return [BaselineRecord(**item) for item in payload.get("records", [])]

    def _write_manifest(self, records: list[BaselineRecord]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        payload = {"records": [record.__dict__ for record in records]}
        self.manifest_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def record_samples(self, samples: list[BenchmarkSample], *, suite: str = "default") -> list[BaselineRecord]:
        if not samples:
            raise ValueError("cannot record an empty benchmark stream")

        records = self._load_manifest()
        by_commit: dict[str, list[BenchmarkSample]] = defaultdict(list)
        for sample in samples:
            by_commit[sample.commit].append(sample)

        written: list[BaselineRecord] = []
        recorded_at = datetime.now(timezone.utc).isoformat()
        for commit, commit_samples in sorted(by_commit.items()):
            relative_path = Path(suite) / f"{commit}.jsonl"
            output_path = self.root / relative_path
            write_jsonl(commit_samples, output_path)
            record = BaselineRecord(
                suite=suite,
                commit=commit,
                path=relative_path.as_posix(),
                sample_count=len(commit_samples),
                recorded_at=recorded_at,
            )
            records = [item for item in records if not (item.suite == suite and item.commit == commit)]
            records.append(record)
            written.append(record)

        records.sort(key=lambda item: (item.suite, item.recorded_at, item.commit))
        self._write_manifest(records)
        return written

    def list_records(self, *, suite: str = "default") -> list[BaselineRecord]:
        return [record for record in self._load_manifest() if record.suite == suite]

    def latest_commit(self, *, suite: str = "default", exclude: str | None = None) -> str | None:
        records = [record for record in self.list_records(suite=suite) if record.commit != exclude]
        if not records:
            return None
        records.sort(key=lambda item: item.recorded_at)
        return records[-1].commit

    def load_commit(self, commit: str, *, suite: str = "default") -> list[BenchmarkSample]:
        for record in reversed(self.list_records(suite=suite)):
            if record.commit == commit:
                return load_jsonl(self.root / record.path)
        raise ValueError(f"baseline commit {commit!r} not found in suite {suite!r}")
