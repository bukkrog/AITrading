import { Fragment, useCallback, useEffect, useState } from "react";
import { api } from "./api";
import { AlertsPanel } from "./components/AlertsPanel";
import { Analytics } from "./components/Analytics";
import { AuditLog } from "./components/AuditLog";
import { DiscoveryView } from "./components/DiscoveryView";
import { EquityChart } from "./components/EquityChart";
import { MonitoringPanel } from "./components/MonitoringPanel";
import { OpenOrders } from "./components/OpenOrders";
import { PositionChart } from "./components/PositionChart";
import { SettingsMenu } from "./components/SettingsMenu";
import { Sidebar, type View } from "./components/Sidebar";
import type {
  Alert,
  AuditEntry,
  AutomationInfo,
  BrokerHealth,
  BrokerModeInfo,
  Config,
  MarketHours,
  Monitoring,
  Portfolio,
  Signal,
  Snapshot,
  StreamingStatus,
} from "./types";

const fmt = (n: number) => n.toLocaleString(undefined, { maximumFractionDigits: 0 });
const pct = (n: number) => `${n.toFixed(1)}%`;

export function App() {
  const [config, setConfig] = useState<Config | null>(null);
  const [portfolio, setPortfolio] = useState<Portfolio | null>(null);
  const [snapshots, setSnapshots] = useState<Snapshot[]>([]);
  const [signals, setSignals] = useState<Signal[]>([]);
  const [audit, setAudit] = useState<AuditEntry[]>([]);
  const [brokerMode, setBrokerMode] = useState<BrokerModeInfo | null>(null);
  const [brokerHealth, setBrokerHealth] = useState<BrokerHealth | null>(null);
  const [monitoring, setMonitoring] = useState<Monitoring | null>(null);
  const [automation, setAutomation] = useState<AutomationInfo | null>(null);
  const [marketHours, setMarketHours] = useState<MarketHours | null>(null);
  const [streaming, setStreaming] = useState<StreamingStatus | null>(null);
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [view, setView] = useState<View>("dashboard");

  const refresh = useCallback(async () => {
    try {
      const [c, p, s, sig, a, bm, mon, auto, al] = await Promise.all([
        api.config(),
        api.portfolio(),
        api.snapshots(),
        api.signals(),
        api.audit(),
        api.brokerMode(),
        api.monitoring(),
        api.automation(),
        api.alerts(true),
      ]);
      setConfig(c);
      setPortfolio(p);
      setSnapshots(s);
      setSignals(sig);
      setAudit(a);
      setBrokerMode(bm);
      setMonitoring(mon);
      setAutomation(auto);
      setAlerts(al);
      setError(null);
      api.brokerHealth().then(setBrokerHealth).catch(() => setBrokerHealth(null));
      api.marketHours().then(setMarketHours).catch(() => setMarketHours(null));
      api.streamingStatus().then(setStreaming).catch(() => setStreaming(null));
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 5000);
    return () => clearInterval(id);
  }, [refresh]);

  const showToast = (msg: string) => {
    setToast(msg);
    setTimeout(() => setToast(null), 4000);
  };

  const closePosition = (symbol: string) => {
    if (!window.confirm(`Sell the entire ${symbol} position at market?`)) return;
    api.closePosition(symbol)
      .then((r) => { showToast(`Closing ${symbol} (${r.quantity})`); refresh(); })
      .catch((e) => showToast((e as Error).message));
  };

  if (error && !portfolio) {
    return (
      <div className="app">
        <h1>AI Trading Platform</h1>
        <div className="killbar">Cannot reach the API. Start it with <code>uvicorn app.main:app</code>.</div>
        <div className="error">{error}</div>
      </div>
    );
  }

  const universe = (automation?.state.universe || "NOVO,MAERSK,ORSTED,DSV,CARLB,GMAB")
    .split(",").map((x) => x.trim().toUpperCase()).filter(Boolean);

  return (
    <div className="layout">
      <Sidebar
        view={view}
        setView={setView}
        monitoring={monitoring}
        automation={automation}
        alertsCount={alerts.length}
        onChanged={refresh}
        onToast={showToast}
      />

      <div className="main-area">
        <header className="topbar">
          <div>
            <h1 style={{ margin: 0, fontSize: 20, textTransform: "capitalize" }}>
              {view === "trading" ? "Auto Trading" : view === "audit" ? "Audit log" : view}
            </h1>
            <div className="subtitle">Controlled · paper-first · Saxo-targeted</div>
          </div>
          <div className="badges">
            {config && (
              <>
                <span className={`badge mode-${brokerMode?.broker_mode ?? "simulation"}`}>
                  broker: {brokerMode?.broker_mode ?? "…"}
                  {brokerMode?.broker_mode === "saxo" ? ` (${brokerMode.saxo_environment})` : ""}
                </span>
                <span className={`badge ${config.live_trading_enabled ? "live" : "ok"}`}>
                  {config.live_trading_enabled ? "LIVE ENABLED" : "paper / sim"}
                </span>
                <span className="badge">AI: {config.ai_auth_mode}</span>
              </>
            )}
          </div>
        </header>

        {automation?.state.emergency_stopped && (
          <div className="killbar">🛑 EMERGENCY STOP LATCHED — clear it under Auto Trading to resume.</div>
        )}
        {portfolio?.kill_switch_engaged && !automation?.state.emergency_stopped && (
          <div className="killbar">🛑 KILL SWITCH ENGAGED — no new trades will open.</div>
        )}

        {/* ---- Dashboard ---- */}
        {view === "dashboard" && portfolio && (
          <>
            {portfolio.source === "saxo" && (
              <div className="badge ok" style={{ display: "inline-block", marginBottom: 10 }}>
                ● Live Saxo account ({portfolio.currency ?? "SIM"}) — balances &amp; positions from Saxo
              </div>
            )}
            <div className="grid metrics">
              <Metric label={`Total value (${portfolio.currency ?? config?.base_currency ?? ""})`} value={fmt(portfolio.total_value)} />
              <Metric label="Cash" value={fmt(portfolio.cash)} />
              <Metric label="Exposure" value={pct(portfolio.exposure_pct)} />
              <Metric label="Drawdown" value={pct(portfolio.drawdown_pct)} tone={portfolio.drawdown_pct > 0 ? "neg" : undefined} />
              <Metric label="Open positions" value={String(portfolio.positions.length)} />
            </div>
            <div className="card section-gap">
              <h2>Equity curve</h2>
              <EquityChart snapshots={snapshots} />
            </div>
            <div className="card section-gap">
              <h2>Positions</h2>
              {portfolio.positions.length > 0 ? (
                <table>
                  <thead><tr><th>Symbol</th><th>Qty</th><th>Avg</th><th>Last</th><th>Value</th><th>Unrealised P/L</th><th>P/L %</th><th>To stop-loss</th><th></th></tr></thead>
                  <tbody>
                    {portfolio.positions.map((p) => (
                      <Fragment key={p.symbol}>
                        <tr>
                          <td>{p.symbol.split(":")[0]}</td><td>{p.quantity}</td><td>{p.avg_price.toFixed(2)}</td>
                          <td>{p.last_price.toFixed(2)}</td><td>{fmt(p.market_value)}</td>
                          <td className={p.unrealized_pnl >= 0 ? "pos" : "neg"}>{p.unrealized_pnl.toFixed(2)}</td>
                          <td className={(p.pnl_pct ?? 0) >= 0 ? "pos" : "neg"}>{(p.pnl_pct ?? 0) >= 0 ? "+" : ""}{(p.pnl_pct ?? 0).toFixed(2)}%</td>
                          <td className={p.stop_distance_pct == null ? "muted" : p.stop_distance_pct <= 2 ? "neg" : "pos"}>
                            {p.stop_distance_pct == null
                              ? "no stop"
                              : `${p.stop_distance_pct.toFixed(1)}% (@ ${p.stop_price?.toFixed(2)})`}
                          </td>
                          <td>
                            <button className="danger" style={{ padding: "2px 8px", fontSize: 12 }}
                              onClick={() => closePosition(p.symbol)}>Sell</button>
                          </td>
                        </tr>
                        <tr>
                          <td colSpan={9} style={{ padding: "2px 8px 10px" }}>
                            <PositionChart symbol={p.symbol} entry={p.avg_price} />
                          </td>
                        </tr>
                      </Fragment>
                    ))}
                  </tbody>
                </table>
              ) : <p className="muted">No open positions.</p>}
            </div>
            {portfolio.source === "saxo" && (
              <div className="section-gap">
                <OpenOrders orders={portfolio.open_orders ?? []} onChanged={refresh} onToast={showToast} />
              </div>
            )}
            <div className="card section-gap">
              <h2>Latest signals</h2>
              {signals.length > 0 ? (
                <table>
                  <thead><tr><th>Symbol</th><th>Decision</th><th>Quant</th><th>News</th><th>Risk</th><th>Combined</th></tr></thead>
                  <tbody>
                    {signals.map((s) => (
                      <tr key={s.id} title={`${s.quant_rationale}\n${s.news_rationale}\n${s.risk_rationale}`}>
                        <td>{s.symbol}</td><td><span className={`tag ${s.decision}`}>{s.decision}</span></td>
                        <td>{s.quant_score.toFixed(1)}</td><td>{s.news_score.toFixed(1)}</td>
                        <td>{s.risk_score.toFixed(1)}</td><td>{s.combined_score.toFixed(1)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : <p className="muted">No signals yet.</p>}
            </div>
          </>
        )}

        {/* ---- Auto Trading ---- */}
        {view === "trading" && monitoring && automation && portfolio && brokerMode && (
          <>
            <div className="grid two-col">
              <MonitoringPanel monitoring={monitoring} automation={automation} portfolio={portfolio} marketHours={marketHours} brokerMode={brokerMode} brokerHealth={brokerHealth} streaming={streaming} onChanged={refresh} onToast={showToast} />
              <AlertsPanel alerts={alerts} onChanged={refresh} />
            </div>
            <div className="card section-gap">
              <h2>Live activity</h2>
              <p className="muted" style={{ fontSize: 12, marginTop: -4 }}>
                What the platform is doing, newest first (auto-refreshes every 5s). Ticks run on the interval above.
              </p>
              {(() => {
                const feed = audit.filter((e) =>
                  ["automation", "signal", "order", "fill", "alert", "risk"].includes(e.category),
                ).slice(0, 20);
                return feed.length > 0 ? (
                  <div style={{ overflowX: "auto" }}>
                    <table>
                      <thead><tr><th>Time</th><th>Type</th><th>Symbol</th><th>What</th></tr></thead>
                      <tbody>
                        {feed.map((e) => (
                          <tr key={e.id}>
                            <td className="muted">{new Date(e.ts).toLocaleTimeString()}</td>
                            <td>{e.category}/{e.action}</td>
                            <td>{e.symbol ?? ""}</td>
                            <td style={{ textAlign: "left" }} className="audit-msg">{e.message}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <p className="muted">No activity yet — press ▶ Start Auto Trading (or “Run tick now”).</p>
                );
              })()}
            </div>
          </>
        )}

        {/* ---- Discovery (live market scan) ---- */}
        {view === "discovery" && <DiscoveryView />}

        {/* ---- Analytics ---- */}
        {view === "analytics" && <Analytics universe={universe} />}

        {/* ---- Setup ---- */}
        {view === "setup" && <SettingsMenu onChanged={refresh} onToast={showToast} />}

        {/* ---- Audit ---- */}
        {view === "audit" && <AuditLog audit={audit} />}

        {toast && <div className="toast">{toast}</div>}
      </div>
    </div>
  );
}

function Metric({ label, value, tone }: { label: string; value: string; tone?: "pos" | "neg" }) {
  return (
    <div className="card metric">
      <div className="label">{label}</div>
      <div className={`value ${tone ?? ""}`}>{value}</div>
    </div>
  );
}
