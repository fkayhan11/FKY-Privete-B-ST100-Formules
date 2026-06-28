import React, { useEffect, useMemo, useState } from "react";

const DEFAULT_QUALITY_URL = "/data/metric_quality_report.json";

function fmtDate(value) {
  if (!value) return "N/A";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat("tr-TR", { dateStyle: "short", timeStyle: "short" }).format(date);
}

function pct(value) {
  const n = Number(value);
  return Number.isFinite(n) ? `${n.toFixed(1)}%` : "-";
}

function statusTone(metric) {
  if ((metric?.missingSourceCount || 0) > 0) return "warn";
  if ((metric?.notApplicableCount || 0) > 0) return "muted";
  return "ok";
}

export default function MetricQualityPanel({
  qualityUrl = DEFAULT_QUALITY_URL,
  refreshMs = 60 * 60 * 1000,
  className = "",
}) {
  const [report, setReport] = useState(null);
  const [activeTab, setActiveTab] = useState("metrics");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    let timerId;

    async function loadReport() {
      try {
        setError("");
        const response = await fetch(`${qualityUrl}?t=${Date.now()}`, {
          headers: { Accept: "application/json" },
          cache: "no-store",
        });
        if (!response.ok) throw new Error(`metric quality fetch failed: ${response.status}`);
        const payload = await response.json();
        if (alive) setReport(payload);
      } catch (err) {
        if (alive) setError(err instanceof Error ? err.message : "metric quality fetch failed");
      } finally {
        if (alive) setLoading(false);
      }
    }

    loadReport();
    if (refreshMs > 0) timerId = window.setInterval(loadReport, refreshMs);
    return () => {
      alive = false;
      if (timerId) window.clearInterval(timerId);
    };
  }, [qualityUrl, refreshMs]);

  const worstMetrics = useMemo(() => (Array.isArray(report?.worstMetrics) ? report.worstMetrics : []), [report]);
  const worstSymbols = useMemo(() => (Array.isArray(report?.worstSymbols) ? report.worstSymbols : []), [report]);
  const rows = activeTab === "metrics" ? worstMetrics : worstSymbols;

  return (
    <section className={`metric-quality-panel ${className}`.trim()}>
      <style>{`
        .metric-quality-panel{--mqp-bg:#080b12;--mqp-panel:#0c1018;--mqp-line:rgba(255,255,255,.1);--mqp-soft:rgba(255,255,255,.055);--mqp-gold:#d4a843;--mqp-green:#2dd67a;--mqp-red:#e05252;--mqp-text:#eef0f6;--mqp-muted:#9ba5bc;--mqp-faint:#5a6478;background:var(--mqp-panel);border:1px solid var(--mqp-line);border-radius:8px;color:var(--mqp-text);font-family:'IBM Plex Sans',system-ui,sans-serif;overflow:hidden}
        .mqp-head{align-items:center;border-bottom:1px solid var(--mqp-soft);display:flex;gap:12px;justify-content:space-between;padding:12px 14px}
        .mqp-title{min-width:0}.mqp-kicker{color:var(--mqp-gold);font-family:'Barlow Condensed','IBM Plex Sans',sans-serif;font-size:16px;font-weight:700;letter-spacing:.8px;text-transform:uppercase}.mqp-meta{color:var(--mqp-faint);font-family:'IBM Plex Mono',monospace;font-size:10px;margin-top:2px}
        .mqp-badge{border:1px solid var(--mqp-line);border-radius:4px;color:var(--mqp-muted);font-family:'IBM Plex Mono',monospace;font-size:10px;padding:5px 8px;white-space:nowrap}.mqp-badge.warn{background:rgba(224,82,82,.08);border-color:rgba(224,82,82,.22);color:var(--mqp-red)}
        .mqp-cards{background:var(--mqp-soft);display:grid;gap:1px;grid-template-columns:repeat(4,minmax(0,1fr));border-bottom:1px solid var(--mqp-soft)}.mqp-card{background:rgba(16,21,31,.78);padding:10px 12px}.mqp-label{color:var(--mqp-faint);font-family:'IBM Plex Mono',monospace;font-size:9px;letter-spacing:.7px;text-transform:uppercase}.mqp-value{font-family:'Barlow Condensed','IBM Plex Sans',sans-serif;font-size:24px;font-weight:700;line-height:1.05;margin-top:4px}.mqp-value.good{color:var(--mqp-green)}.mqp-value.warn{color:var(--mqp-red)}.mqp-value.gold{color:var(--mqp-gold)}
        .mqp-tabs{display:grid;grid-template-columns:repeat(2,1fr);border-bottom:1px solid var(--mqp-soft)}.mqp-tab{appearance:none;background:#10151f;border:0;border-right:1px solid var(--mqp-soft);color:var(--mqp-faint);cursor:pointer;font-family:'IBM Plex Mono',monospace;font-size:10px;font-weight:700;padding:9px;text-transform:uppercase}.mqp-tab:last-child{border-right:0}.mqp-tab.active{background:rgba(212,168,67,.1);color:var(--mqp-gold)}
        .mqp-body{min-height:180px;overflow:auto}.mqp-state{align-items:center;color:var(--mqp-faint);display:flex;font-family:'IBM Plex Mono',monospace;font-size:11px;justify-content:center;min-height:180px;padding:20px}.mqp-state.error{color:var(--mqp-red)}
        .mqp-table{border-collapse:collapse;table-layout:fixed;width:100%}.mqp-table th{background:rgba(255,255,255,.018);border-bottom:1px solid var(--mqp-soft);color:var(--mqp-faint);font-family:'IBM Plex Mono',monospace;font-size:9px;font-weight:700;letter-spacing:.7px;padding:8px 10px;text-align:left;text-transform:uppercase}.mqp-table td{border-bottom:1px solid var(--mqp-soft);color:var(--mqp-muted);font-size:12px;padding:9px 10px;vertical-align:middle}.mqp-name{color:var(--mqp-text);font-family:'IBM Plex Mono',monospace;font-weight:700;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.mqp-tone.ok{color:var(--mqp-green)}.mqp-tone.warn{color:var(--mqp-red)}.mqp-tone.muted{color:var(--mqp-faint)}.mqp-list{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.mqp-foot{border-top:1px solid var(--mqp-soft);color:var(--mqp-faint);font-family:'IBM Plex Mono',monospace;font-size:9px;line-height:1.35;padding:8px 12px}
        @media(max-width:720px){.mqp-cards{grid-template-columns:repeat(2,minmax(0,1fr))}.mqp-table{min-width:680px}}
      `}</style>

      <header className="mqp-head">
        <div className="mqp-title">
          <div className="mqp-kicker">Metric Quality</div>
          <div className="mqp-meta">{loading ? "Loading..." : `Updated ${fmtDate(report?.generatedAt)}`}</div>
        </div>
        <div className={`mqp-badge ${(report?.missingSourceTotal || 0) > 0 ? "warn" : ""}`}>
          {report?.missingSourceTotal ?? "-"} Missing Source
        </div>
      </header>

      <div className="mqp-cards">
        <div className="mqp-card"><div className="mqp-label">Symbols</div><div className="mqp-value">{report?.symbolCount ?? "-"}</div></div>
        <div className="mqp-card"><div className="mqp-label">Metrics</div><div className="mqp-value">{report?.metricCount ?? "-"}</div></div>
        <div className="mqp-card"><div className="mqp-label">Valid/Derived</div><div className="mqp-value good">{(report?.statusCounts?.Valid || 0) + (report?.statusCounts?.Derived || 0)}</div></div>
        <div className="mqp-card"><div className="mqp-label">Estimated</div><div className="mqp-value gold">{(report?.statusCounts?.["Provider Estimate"] || 0) + (report?.statusCounts?.["Model Estimate"] || 0)}</div></div>
      </div>

      <div className="mqp-tabs">
        <button className={`mqp-tab ${activeTab === "metrics" ? "active" : ""}`} type="button" onClick={() => setActiveTab("metrics")}>Worst Metrics</button>
        <button className={`mqp-tab ${activeTab === "symbols" ? "active" : ""}`} type="button" onClick={() => setActiveTab("symbols")}>Worst Symbols</button>
      </div>

      <div className="mqp-body">
        {loading ? (
          <div className="mqp-state">Kalite raporu yükleniyor</div>
        ) : error ? (
          <div className="mqp-state error">{error}</div>
        ) : activeTab === "metrics" ? (
          <table className="mqp-table">
            <thead><tr><th>Metric</th><th>Coverage</th><th>Missing</th><th>Status</th><th>Symbols</th></tr></thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.metric}>
                  <td className="mqp-name">{row.metric}</td>
                  <td>{pct(row.coveragePct)}</td>
                  <td className={`mqp-tone ${statusTone(row)}`}>{row.missingSourceCount}</td>
                  <td>{Object.entries(row.statusCounts || {}).map(([k, v]) => `${k}:${v}`).join(" · ")}</td>
                  <td className="mqp-list">{(row.missingSourceSymbols || []).join(", ") || "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <table className="mqp-table">
            <thead><tr><th>Symbol</th><th>Quality</th><th>Missing</th><th>Estimated</th><th>Missing Metrics</th></tr></thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.symbol}>
                  <td className="mqp-name">{row.symbol}</td>
                  <td>{row.qualityScore}</td>
                  <td className={`mqp-tone ${row.missingSourceCount > 0 ? "warn" : "ok"}`}>{row.missingSourceCount}</td>
                  <td>{row.estimatedCount}</td>
                  <td className="mqp-list">{(row.missingSourceMetrics || []).join(", ") || "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <footer className="mqp-foot">{report?.disclaimer || "Metric quality is shown for transparency."}</footer>
    </section>
  );
}
