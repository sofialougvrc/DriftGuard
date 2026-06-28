export interface MannWhitneyResult {
  u_statistic: number;
  p_value: number;
  probability_candidate_slower: number;
  cliffs_delta: number;
}

export interface SprtResult {
  decision: "regression" | "no_regression" | "continue";
  log_likelihood_ratio: number;
  upper_boundary: number;
  lower_boundary: number;
  confidence: number;
  observations: number;
}

export interface ChangePointResult {
  introduced_at: string | null;
  posterior_probability: number;
  candidates: Array<[string, number]>;
}

export interface SampleQuality {
  raw_baseline_count: number;
  raw_candidate_count: number;
  baseline_count: number;
  candidate_count: number;
  baseline_outliers_removed: number;
  candidate_outliers_removed: number;
  baseline_cv: number;
  candidate_cv: number;
  baseline_mad_ratio: number;
  candidate_mad_ratio: number;
  warmup_iterations: number;
  passed: boolean;
  warnings: string[];
}

export interface EnvironmentFingerprint {
  platform: string;
  machine: string;
  processor: string;
  python_version: string;
  cpu_count: number | null;
  load_average_1m: number | null;
  cpu_governor: string | null;
  perf_event_paranoid: number | null;
  warnings: string[];
}

export interface DriftGuardComparison {
  function: string;
  metric: string;
  unit: string;
  baseline_commit: string;
  candidate_commit: string;
  baseline_p99: number;
  candidate_p99: number;
  p99_delta_percent: number;
  baseline_median: number;
  candidate_median: number;
  median_delta_percent: number;
  is_regression: boolean;
  mann_whitney: MannWhitneyResult;
  sprt: SprtResult;
  change_point: ChangePointResult;
  quality: SampleQuality;
}

export interface DriftGuardReport {
  baseline_commit: string;
  candidate_commit: string;
  regression_count: number;
  quality_warning_count: number;
  environment: EnvironmentFingerprint | null;
  comparisons: DriftGuardComparison[];
}
