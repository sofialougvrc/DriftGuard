import type { DriftGuardComparison, DriftGuardReport } from "../common/types.js";

declare const process: {
  argv: string[];
  exitCode?: number;
};

declare function require(name: string): any;

const fs = require("node:fs");

function escapeHtml(value: string): string {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function formatNumber(value: number): string {
  return new Intl.NumberFormat("en-US", { maximumFractionDigits: 1 }).format(value);
}

function statusLabel(item: DriftGuardComparison): string {
  if (item.is_regression) {
    return "Regression";
  }
  if (!item.quality.passed) {
    return "Blocked";
  }
  if (item.sprt.decision === "continue") {
    return "Needs data";
  }
  return "Stable";
}

function barWidth(item: DriftGuardComparison, maxAbsDelta: number): string {
  if (maxAbsDelta <= 0) {
    return "0%";
  }
  return `${Math.min(100, Math.abs(item.p99_delta_percent / maxAbsDelta) * 100).toFixed(1)}%`;
}

function renderRows(report: DriftGuardReport): string {
  const maxAbsDelta = Math.max(1, ...report.comparisons.map((item) => Math.abs(item.p99_delta_percent)));
  return report.comparisons
    .map((item) => {
      const status = statusLabel(item);
      const statusClass = item.is_regression ? "bad" : !item.quality.passed || item.sprt.decision === "continue" ? "warn" : "good";
      const barClass = item.p99_delta_percent >= 0 ? "slower" : "faster";
      const quality = item.quality.passed ? "pass" : "blocked";
      return `
        <tr>
          <td>
            <strong>${escapeHtml(item.function)}()</strong>
            <span>${escapeHtml(item.metric)}</span>
          </td>
          <td><span class="pill ${statusClass}">${status}</span></td>
          <td class="numeric">${formatNumber(item.p99_delta_percent)}%</td>
          <td>
            <div class="delta-track">
              <div class="delta-bar ${barClass}" style="width: ${barWidth(item, maxAbsDelta)}"></div>
            </div>
          </td>
          <td>${item.quality.baseline_count}/${item.quality.candidate_count} <span>${quality}</span></td>
          <td class="numeric">${formatNumber(item.sprt.confidence * 100)}%</td>
          <td class="numeric">${item.mann_whitney.p_value.toFixed(4)}</td>
          <td><code>${escapeHtml(item.change_point.introduced_at || "unknown")}</code></td>
        </tr>
      `;
    })
    .join("");
}

export function renderDashboard(report: DriftGuardReport): string {
  const regressions = report.comparisons.filter((item) => item.is_regression);
  const maxDelta = Math.max(0, ...report.comparisons.map((item) => item.p99_delta_percent));
  return `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>DriftGuard Dashboard</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f7f8fa;
      --panel: #ffffff;
      --ink: #17202a;
      --muted: #667085;
      --line: #d9dee7;
      --bad: #b42318;
      --bad-bg: #fee4e2;
      --good: #027a48;
      --good-bg: #d1fadf;
      --warn: #b54708;
      --warn-bg: #fef0c7;
      --accent: #155eef;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      letter-spacing: 0;
    }
    main {
      max-width: 1180px;
      margin: 0 auto;
      padding: 28px 20px 40px;
    }
    header {
      display: flex;
      align-items: flex-end;
      justify-content: space-between;
      gap: 24px;
      margin-bottom: 22px;
    }
    h1 {
      margin: 0 0 6px;
      font-size: 28px;
      line-height: 1.15;
    }
    .subtle {
      color: var(--muted);
      font-size: 14px;
    }
    .summary {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 12px;
      margin-bottom: 20px;
    }
    .metric {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px 16px;
    }
    .metric span {
      display: block;
      color: var(--muted);
      font-size: 12px;
      margin-bottom: 8px;
      text-transform: uppercase;
    }
    .metric strong {
      font-size: 24px;
      line-height: 1;
    }
    section {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: hidden;
    }
    table {
      border-collapse: collapse;
      width: 100%;
      table-layout: fixed;
    }
    th, td {
      border-bottom: 1px solid var(--line);
      padding: 12px 14px;
      text-align: left;
      vertical-align: middle;
      font-size: 14px;
    }
    th {
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
      text-transform: uppercase;
      background: #fbfcfe;
    }
    td strong, td span {
      display: block;
      min-width: 0;
      overflow-wrap: anywhere;
    }
    td span {
      color: var(--muted);
      margin-top: 2px;
      font-size: 12px;
    }
    tr:last-child td { border-bottom: 0; }
    .numeric {
      text-align: right;
      font-variant-numeric: tabular-nums;
    }
    .pill {
      border-radius: 999px;
      display: inline-flex;
      align-items: center;
      font-size: 12px;
      font-weight: 700;
      min-height: 24px;
      padding: 0 9px;
      white-space: nowrap;
    }
    .bad { color: var(--bad); background: var(--bad-bg); }
    .good { color: var(--good); background: var(--good-bg); }
    .warn { color: var(--warn); background: var(--warn-bg); }
    .delta-track {
      height: 9px;
      width: 100%;
      background: #eef2f7;
      border-radius: 999px;
      overflow: hidden;
    }
    .delta-bar {
      height: 100%;
      min-width: 2px;
    }
    .slower { background: var(--bad); }
    .faster { background: var(--good); }
    code {
      background: #f2f4f7;
      border: 1px solid #e4e7ec;
      border-radius: 5px;
      padding: 2px 5px;
      font-family: "SFMono-Regular", Consolas, monospace;
      font-size: 12px;
    }
    @media (max-width: 760px) {
      header, .summary {
        display: block;
      }
      .metric {
        margin-bottom: 10px;
      }
      section {
        overflow-x: auto;
      }
      table {
        min-width: 820px;
      }
    }
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>DriftGuard</h1>
        <div class="subtle">Baseline <code>${escapeHtml(report.baseline_commit)}</code> vs candidate <code>${escapeHtml(report.candidate_commit)}</code></div>
      </div>
      <div class="subtle">SPRT + Mann-Whitney U + Bayesian change points</div>
    </header>
    <div class="summary">
      <div class="metric"><span>Regressions</span><strong>${regressions.length}</strong></div>
      <div class="metric"><span>Largest P99 Delta</span><strong>${formatNumber(maxDelta)}%</strong></div>
      <div class="metric"><span>Quality Warnings</span><strong>${report.quality_warning_count}</strong></div>
    </div>
    <section>
      <table>
        <thead>
          <tr>
            <th>Function</th>
            <th>Status</th>
            <th>P99 Delta</th>
            <th>Magnitude</th>
            <th>Samples</th>
            <th>Confidence</th>
            <th>MWU p</th>
            <th>Change Point</th>
          </tr>
        </thead>
        <tbody>
          ${renderRows(report)}
        </tbody>
      </table>
    </section>
  </main>
</body>
</html>`;
}

function main(): void {
  const input = process.argv[2] || "driftguard-report.json";
  const output = process.argv[3] || "driftguard-dashboard.html";
  const report = JSON.parse(fs.readFileSync(input, "utf8")) as DriftGuardReport;
  fs.writeFileSync(output, renderDashboard(report));
}

try {
  main();
} catch (error) {
  console.error(error instanceof Error ? error.message : String(error));
  process.exitCode = 1;
}
