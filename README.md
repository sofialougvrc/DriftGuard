# DriftGuard

Continuous performance regression intelligence for CI/CD.

DriftGuard decides whether a change made code meaningfully slower with statistical evidence, not a flat threshold. It combines:

- Sequential Probability Ratio Test (SPRT) for evidence accumulation across benchmark runs.
- Mann-Whitney U tests for noisy, non-normal benchmark distributions.
- Bayesian change-point detection to identify the commit that most likely introduced a regression.
- Warmup trimming, robust outlier filtering, sample-size gates, and noise diagnostics before any regression is trusted.
- Environment fingerprinting for load, CPU governor, perf permissions, platform, and runtime metadata.
- Linux perf event instrumentation for sub-microsecond timing and hardware counters.
- TypeScript GitHub and dashboard surfaces for PR feedback.

Example PR output:

```text
processOrder() regressed P99 latency by 12.1% - 99.3% confidence - introduced in commit 4a8f
```

## Repository Layout

```text
driftguard/
  driftguard/              Python statistics package and CLI
  cpp/                     C++ perf_event collector and CLI wrapper
  typescript/              GitHub comment and dashboard renderers
  tests/                   Python regression tests
  configs/                 Example DriftGuard policy
  examples/                Example benchmark stream
  .github/workflows/       Example CI workflow
```

## Quick Start

Run the Python analysis on the included benchmark stream:

```bash
python3 -m driftguard.cli analyze examples/benchmark_samples.jsonl --format markdown
```

Expected output includes a high-confidence regression for `processOrder` and a likely introducing commit.

By default DriftGuard requires at least 20 retained samples per side after discarding 3 warmup iterations and filtering extreme outliers. Low-sample or noisy benchmark streams are reported as blocked rather than trusted.

Record a passing baseline into local history:

```bash
python3 -m driftguard.cli record baseline.jsonl --suite order-service --store .driftguard/history
```

Compare a candidate stream against stored history in CI:

```bash
python3 -m driftguard.cli ci \
  --candidate-stream candidate.jsonl \
  --candidate HEAD \
  --suite order-service \
  --store .driftguard/history \
  --bootstrap-if-missing \
  --promote-on-pass \
  --format json \
  --output driftguard-report.json
```

Store history in SQLite when you want durable local/service mode:

```bash
python3 -m driftguard.cli db-ingest baseline.jsonl --suite order-service --database .driftguard/driftguard.db
python3 -m driftguard.cli db-ingest candidate.jsonl --suite order-service --database .driftguard/driftguard.db
python3 -m driftguard.cli db-analyze \
  --suite order-service \
  --database .driftguard/driftguard.db \
  --baseline BASE \
  --candidate HEAD \
  --save-run \
  --format sarif \
  --output driftguard.sarif
```

Run tests:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests
```

Check whether the current machine is suitable for benchmark runs:

```bash
python3 -m driftguard.cli doctor
```

Build the C++ collector:

```bash
cmake -S cpp -B cpp/build
cmake --build cpp/build
```

Use the collector to wrap a command:

```bash
./cpp/build/driftguard-perf --commit HEAD --function processOrder --iterations 25 -- ./your-benchmark
```

It emits JSONL records compatible with the Python pipeline.

Run the included order-service benchmark harness:

```bash
BASELINE_SHA=$(git rev-parse HEAD~1 2>/dev/null || echo BASE_SHA)
CANDIDATE_SHA=$(git rev-parse HEAD 2>/dev/null || echo HEAD_SHA)

./cpp/build/driftguard-perf \
  --commit "$BASELINE_SHA" \
  --function processOrder \
  --iterations 50 \
  --prefault-stack \
  -- ./cpp/build/order-service-bench --variant baseline --orders 100000 --repeat 60 --quiet > baseline.jsonl

./cpp/build/driftguard-perf \
  --commit "$CANDIDATE_SHA" \
  --function processOrder \
  --iterations 50 \
  --prefault-stack \
  -- ./cpp/build/order-service-bench --variant regressed --orders 100000 --repeat 60 --quiet > candidate.jsonl

python3 -m driftguard.cli compare \
  --baseline-stream baseline.jsonl \
  --candidate-stream candidate.jsonl \
  --baseline "$BASELINE_SHA" \
  --candidate "$CANDIDATE_SHA" \
  --min-samples 30 \
  --warmup 5 \
  --format markdown
```

The `regressed` variant adds an extra validation pass over the same synthetic order set. It is useful for proving the DriftGuard workflow, but your production conclusions should come from your own service benchmarks.

On Linux self-hosted runners, add stricter collector controls:

```bash
./cpp/build/driftguard-perf \
  --commit "$CANDIDATE_SHA" \
  --function processOrder \
  --iterations 50 \
  --pin-cpu 0 \
  --realtime \
  --realtime-priority 80 \
  --prefault-stack \
  --require-runner-controls \
  -- ./your-real-benchmark > candidate.jsonl
```

`--pin-cpu` and `--realtime` require Linux permissions. GitHub-hosted runners usually cannot provide those guarantees; use a dedicated self-hosted runner for production measurement quality.

Instrument a function directly from C++:

```cpp
#include "driftguard/perf_collector.hpp"

void processOrder(driftguard::JsonlSink& sink) {
  DRIFTGUARD_SCOPE(sink, "HEAD", "processOrder");
  // hot path
}
```

## Benchmark Record Format

Each JSONL row is one benchmark observation:

```json
{
  "commit": "4a8f",
  "function": "processOrder",
  "metric": "latency_ns",
  "value": 172300,
  "unit": "ns",
  "iteration": 14,
  "counters": {
    "cycles": 612019,
    "instructions": 991220,
    "cache_misses": 312
  }
}
```

Lower values are assumed to be better for latency metrics.

## Statistical Model

DriftGuard uses three complementary signals:

1. SPRT models log-latency under two hypotheses: no meaningful slowdown and a configured minimum slowdown. It keeps collecting evidence until the log-likelihood ratio crosses a decision boundary.
2. Mann-Whitney U provides a non-parametric distribution shift check. It is robust when latency data has outliers, long tails, or cold-start effects.
3. Bayesian change-point detection scans commit-ordered observations and returns the split with the highest posterior support under a two-segment model.

The final report only calls a regression when the candidate change is slower, SPRT supports the regression hypothesis, and the Mann-Whitney p-value passes policy.

## CI Integration

The example workflow in `.github/workflows/driftguard.yml` shows the intended flow:

1. Build benchmark target.
2. Resolve real git SHAs with `git rev-parse`.
3. Restore recorded baseline history from a branch-scoped GitHub Actions cache.
4. Collect the candidate benchmark stream with the real candidate SHA.
5. Run `python -m driftguard.cli ci`.
6. Write JSON and Markdown reports even when a regression is found.
7. Post or update a PR comment with `actions/github-script`.
8. Upload dashboard and baseline artifacts.

The TypeScript code still provides a dependency-free GitHub REST implementation, and the workflow uses `actions/github-script` as the minimal production bot path. Both update an existing DriftGuard PR comment when one exists, keeping PR discussion clean across repeated pushes.

## Report Formats

All analysis commands support:

- `json` for dashboards and automation.
- `markdown` for PR comments and human review.
- `sarif` for GitHub code scanning or security-style report ingestion.
- `junit` for CI systems that already display test reports.

Example:

```bash
python3 -m driftguard.cli analyze examples/benchmark_samples.jsonl --format junit --output driftguard-junit.xml
```

## Reliability Gates

Production-grade results depend on disciplined benchmark input. DriftGuard now applies these safeguards before it marks a regression as trusted:

- `--warmup`: discard early iterations before analysis.
- `--min-samples`: require enough retained samples on both baseline and candidate.
- `--outlier-mad`: remove extreme outliers with a robust MAD filter.
- `--max-outlier-fraction`: block reports if too much data had to be filtered.
- `--max-cv`: block high-variance benchmark groups.
- `--max-mad-ratio`: block high robust noise relative to the median.
- environment fingerprinting: record load average, CPU count, CPU governor, perf counter permission state, platform, and Python runtime.
- `--require-clean-environment`: block trusted regression decisions when host diagnostics report warnings.

Example stricter CI policy:

```bash
python3 -m driftguard.cli ci \
  --candidate-stream candidate.jsonl \
  --suite order-service \
  --store .driftguard/history \
  --min-samples 50 \
  --warmup 5 \
  --max-cv 0.10 \
  --max-mad-ratio 0.08 \
  --require-clean-environment
```

## Storage Modes

DriftGuard now has two history options:

- File baseline store: simple JSONL files plus a manifest, useful for GitHub Actions cache artifacts.
- SQLite database: raw samples and analysis reports in one database, useful for local dashboards, service mode, or longer-lived history.

## Design Notes

This is built after a streaming analytics architecture: benchmark records are append-only events, the Python layer is an analysis operator, and the TypeScript surfaces are sinks. That makes it natural to run in CI today and to evolve into a service that continuously ingests benchmark streams later.
