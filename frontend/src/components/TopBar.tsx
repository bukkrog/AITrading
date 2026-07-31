import { useEffect, useState } from "react";
import { api } from "../api";
import type { Alert, Performance, Portfolio } from "../types";

/** Saxo-TraderGO-style persistent top bar: brand · symbol search · account ·
 *  equity / available / daily P&L · notifications. Step 1 of the terminal
 *  layout (DESIGN_trading_terminal_ui.md). Self-fetches, polls 10s. */
function Stat({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", lineHeight: 1.15, minWidth: 76 }}>
      <span style={{ fontSize: 9, textTransform: "uppercase", letterSpacing: ".06em", color: "var(--muted)" }}>{label}</span>
      <span style={{ fontSize: 15, fontWeight: 700, fontVariantNumeric: "tabular-nums", color: tone ?? "var(--text)" }}>{value}</span>
    </div>
  );
}

export function TopBar() {
  const [pf, setPf] = useState<Portfolio | null>(null);
  const [perf, setPerf] = useState<Performance | null>(null);
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [q, setQ] = useState("");

  useEffect(() => {
    let alive = true;
    const load = () => {
      api.portfolio().then((r) => alive && setPf(r)).catch(() => {});
      api.performance().then((r) => alive && setPerf(r)).catch(() => {});
      api.alerts(true).then((r) => alive && setAlerts(r)).catch(() => {});
    };
    load();
    const id = setInterval(load, 10000);
    return () => { alive = false; clearInterval(id); };
  }, []);

  const ccy = pf?.currency ?? "";
  const money = (n?: number | null) =>
    n == null ? "—" : `${n.toLocaleString("da-DK", { maximumFractionDigits: 0 })} ${ccy}`.trim();
  const daily = perf?.realized?.today ?? null;
  const critical = alerts.filter((a) => (a.severity || "").toLowerCase() === "critical").length;
  const isSaxo = pf?.source === "saxo";

  return (
    <header className="term-topbar">
      <div className="term-brand">📈 BukTrader&nbsp;AI</div>

      <input className="term-search" value={q} onChange={(e) => setQ(e.target.value)}
        placeholder="Søg instrument…  (fx AAPL, NVDA)" spellCheck={false} />

      <div className="term-stats">
        <Stat label="Equity" value={money(pf?.total_value)} />
        <Stat label="Available" value={money(pf?.margin_available ?? undefined)} />
        <Stat label="Daily P/L"
          value={daily == null ? "—" : `${daily >= 0 ? "+" : ""}${daily.toLocaleString("da-DK", { maximumFractionDigits: 0 })}`}
          tone={daily == null ? undefined : daily >= 0 ? "var(--green)" : "var(--red)"} />
      </div>

      <div className="term-right">
        <span className={`term-acct ${isSaxo ? "saxo" : "sim"}`}>
          {isSaxo ? `● Saxo ${ccy}` : "● Paper / SIM"}
        </span>
        <span className="term-bell" title={`${alerts.length} alerts, ${critical} kritiske`}>
          🔔{critical > 0 && <span className="term-badge">{critical}</span>}
        </span>
      </div>
    </header>
  );
}
