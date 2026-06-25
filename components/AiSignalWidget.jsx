import React, { useEffect, useMemo, useState } from "react";

const DEFAULT_SIGNALS_URL = "/data/signals.json";

function formatGeneratedAt(value) {
  if (!value) return "N/A";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat("tr-TR", {
    dateStyle: "short",
    timeStyle: "short",
  }).format(date);
}

function scoreLabel(score) {
  if (score === null || score === undefined || Number.isNaN(Number(score))) return "-";
  return Number(score).toFixed(2);
}

function SignalRows({ rows, type }) {
  if (!rows.length) {
    return (
      <tr>
        <td className="asw-empty" colSpan={type === "buy" ? 3 : 2}>
          Aktif sinyal yok
        </td>
      </tr>
    );
  }

  return rows.map((row) => (
    <tr key={`${type}-${row.symbol}`}>
      <td>
        <span className={`asw-ticker asw-glow ${type === "buy" ? "buy" : "sell"}`}>
          {row.symbol}
        </span>
      </td>
      {type === "buy" ? <td className="asw-score">{scoreLabel(row.score)}</td> : null}
      <td className="asw-reason">{row.reason || (type === "buy" ? "Buy Signal" : "Exit Signal")}</td>
    </tr>
  ));
}

export default function AiSignalWidget({
  signalsUrl = DEFAULT_SIGNALS_URL,
  refreshMs = 5 * 60 * 1000,
  className = "",
}) {
  const [activeTab, setActiveTab] = useState("buy");
  const [signals, setSignals] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    let timerId;

    async function loadSignals() {
      try {
        setError("");
        const response = await fetch(`${signalsUrl}?t=${Date.now()}`, {
          headers: { Accept: "application/json" },
          cache: "no-store",
        });
        if (!response.ok) {
          throw new Error(`signals fetch failed: ${response.status}`);
        }
        const payload = await response.json();
        if (alive) setSignals(payload);
      } catch (err) {
        if (alive) setError(err instanceof Error ? err.message : "signals fetch failed");
      } finally {
        if (alive) setLoading(false);
      }
    }

    loadSignals();
    if (refreshMs > 0) timerId = window.setInterval(loadSignals, refreshMs);

    return () => {
      alive = false;
      if (timerId) window.clearInterval(timerId);
    };
  }, [signalsUrl, refreshMs]);

  const buySignals = useMemo(
    () => (Array.isArray(signals?.buy_candidates) ? signals.buy_candidates : []),
    [signals],
  );
  const sellSignals = useMemo(
    () => (Array.isArray(signals?.sell_candidates) ? signals.sell_candidates : []),
    [signals],
  );
  const activeRows = activeTab === "buy" ? buySignals : sellSignals;
  const isBullish = String(signals?.market_regime || "").toLowerCase() === "bullish";

  return (
    <section className={`ai-signal-widget ${className}`.trim()}>
      <style>{`
        .ai-signal-widget {
          --asw-bg: #080b12;
          --asw-panel: #0c1018;
          --asw-panel-2: #10151f;
          --asw-line: rgba(255,255,255,.1);
          --asw-line-soft: rgba(255,255,255,.055);
          --asw-gold: #d4a843;
          --asw-gold-2: #e8c060;
          --asw-text: #eef0f6;
          --asw-muted: #9ba5bc;
          --asw-faint: #5a6478;
          --asw-green: #2dd67a;
          --asw-red: #e05252;
          background: linear-gradient(180deg, var(--asw-panel), var(--asw-bg));
          border: 1px solid var(--asw-line);
          border-radius: 8px;
          color: var(--asw-text);
          font-family: 'IBM Plex Sans', system-ui, -apple-system, BlinkMacSystemFont, sans-serif;
          overflow: hidden;
        }
        .asw-head {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 12px;
          padding: 12px 14px;
          border-bottom: 1px solid var(--asw-line-soft);
        }
        .asw-title {
          display: flex;
          flex-direction: column;
          gap: 2px;
          min-width: 0;
        }
        .asw-kicker {
          color: var(--asw-gold);
          font-family: 'Barlow Condensed', 'IBM Plex Sans', sans-serif;
          font-size: 16px;
          font-weight: 700;
          letter-spacing: .8px;
          line-height: 1;
          text-transform: uppercase;
        }
        .asw-meta {
          color: var(--asw-faint);
          font-family: 'IBM Plex Mono', monospace;
          font-size: 10px;
          line-height: 1.3;
        }
        .asw-regime {
          border: 1px solid var(--asw-line);
          border-radius: 4px;
          color: var(--asw-muted);
          font-family: 'IBM Plex Mono', monospace;
          font-size: 10px;
          padding: 5px 8px;
          text-transform: uppercase;
          white-space: nowrap;
        }
        .asw-regime.bullish {
          background: rgba(45,214,122,.08);
          border-color: rgba(45,214,122,.24);
          color: var(--asw-green);
        }
        .asw-regime.bearish {
          background: rgba(224,82,82,.08);
          border-color: rgba(224,82,82,.24);
          color: var(--asw-red);
        }
        .asw-tabs {
          display: grid;
          grid-template-columns: 1fr 1fr;
          background: var(--asw-panel-2);
          border-bottom: 1px solid var(--asw-line-soft);
        }
        .asw-tab {
          appearance: none;
          background: transparent;
          border: 0;
          border-right: 1px solid var(--asw-line-soft);
          color: var(--asw-faint);
          cursor: pointer;
          font-family: 'IBM Plex Mono', monospace;
          font-size: 10px;
          font-weight: 600;
          letter-spacing: .4px;
          padding: 9px 10px;
          text-transform: uppercase;
          transition: background .15s ease, color .15s ease;
        }
        .asw-tab:last-child { border-right: 0; }
        .asw-tab:hover { background: rgba(212,168,67,.06); color: var(--asw-muted); }
        .asw-tab.active {
          background: rgba(212,168,67,.1);
          color: var(--asw-gold-2);
        }
        .asw-body {
          min-height: 132px;
          overflow-x: auto;
        }
        .asw-state {
          align-items: center;
          color: var(--asw-faint);
          display: flex;
          font-family: 'IBM Plex Mono', monospace;
          font-size: 11px;
          justify-content: center;
          min-height: 132px;
          padding: 20px;
          text-align: center;
        }
        .asw-state.error { color: var(--asw-red); }
        .asw-table {
          border-collapse: collapse;
          table-layout: fixed;
          width: 100%;
        }
        .asw-table th {
          background: rgba(255,255,255,.018);
          border-bottom: 1px solid var(--asw-line-soft);
          color: var(--asw-faint);
          font-family: 'IBM Plex Mono', monospace;
          font-size: 9px;
          font-weight: 600;
          letter-spacing: .8px;
          padding: 8px 10px;
          text-align: left;
          text-transform: uppercase;
        }
        .asw-table td {
          border-bottom: 1px solid var(--asw-line-soft);
          color: var(--asw-muted);
          font-size: 12px;
          padding: 10px;
          vertical-align: middle;
        }
        .asw-table tr:last-child td { border-bottom: 0; }
        .asw-table tr:hover td { background: rgba(255,255,255,.018); }
        .asw-ticker {
          display: inline-flex;
          font-family: 'IBM Plex Mono', monospace;
          font-size: 12px;
          font-weight: 700;
          letter-spacing: .2px;
          line-height: 1;
        }
        .asw-ticker.buy { color: var(--asw-gold-2); }
        .asw-ticker.sell { color: var(--asw-red); }
        .asw-glow.buy {
          text-shadow: 0 0 10px rgba(212,168,67,.5), 0 0 24px rgba(212,168,67,.25);
        }
        .asw-glow.sell {
          text-shadow: 0 0 10px rgba(224,82,82,.45), 0 0 24px rgba(224,82,82,.22);
        }
        .asw-score {
          color: var(--asw-text);
          font-family: 'IBM Plex Mono', monospace;
          width: 72px;
        }
        .asw-reason {
          color: var(--asw-muted);
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
        .asw-empty {
          color: var(--asw-faint);
          font-family: 'IBM Plex Mono', monospace;
          height: 82px;
          text-align: center;
        }
      `}</style>

      <header className="asw-head">
        <div className="asw-title">
          <div className="asw-kicker">AI Signals</div>
          <div className="asw-meta">{loading ? "Loading..." : `Updated ${formatGeneratedAt(signals?.generatedAt)}`}</div>
        </div>
        <div className={`asw-regime ${isBullish ? "bullish" : "bearish"}`}>
          {signals?.market_regime || "N/A"}
        </div>
      </header>

      <div className="asw-tabs" role="tablist" aria-label="AI signal tabs">
        <button
          className={`asw-tab ${activeTab === "buy" ? "active" : ""}`}
          type="button"
          role="tab"
          aria-selected={activeTab === "buy"}
          onClick={() => setActiveTab("buy")}
        >
          Buy Signals ({buySignals.length})
        </button>
        <button
          className={`asw-tab ${activeTab === "sell" ? "active" : ""}`}
          type="button"
          role="tab"
          aria-selected={activeTab === "sell"}
          onClick={() => setActiveTab("sell")}
        >
          Exit/Stop Signals ({sellSignals.length})
        </button>
      </div>

      <div className="asw-body">
        {loading ? (
          <div className="asw-state">Sinyaller yükleniyor</div>
        ) : error ? (
          <div className="asw-state error">{error}</div>
        ) : (
          <table className="asw-table">
            <thead>
              <tr>
                <th>Symbol</th>
                {activeTab === "buy" ? <th>Score</th> : null}
                <th>Reason</th>
              </tr>
            </thead>
            <tbody>
              <SignalRows rows={activeRows} type={activeTab} />
            </tbody>
          </table>
        )}
      </div>
    </section>
  );
}
