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
import { IndicesBar } from "./components/IndicesBar";
import { RiskRadar } from "./components/RiskRadar";
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
  Performance,
  Portfolio,
  PositionAssessment,
  Realized,
  Signal,
  Snapshot,
  StreamingStatus,
} from "./types";

const fmt = (n: number) => n.toLocaleString(undefined, { maximumFractionDigits: 0 });
const pnl = (n: number | null | undefined) =>
  n == null ? "—" : `${n >= 0 ? "+" : ""}${n.toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
const tone = (n: number | null | undefined): "pos" | "neg" | undefined =>
  n == null ? undefined : n >= 0 ? "pos" : "neg";
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
  const [realized, setRealized] = useState<Realized | null>(null);
  const [perf, setPerf] = useState<Performance | null>(null);
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [view, setView] = useState<View>("dashboard");
  const [oauth, setOauth] = useState<{ configured: boolean; connected: boolean; environment: string } | null>(null);
  const [activity, setActivity] = useState<{ current: { text: string; seconds_ago: number }; recent: { text: string; seconds_ago: number }[] } | null>(null);
  const [assessments, setAssessments] = useState<Record<string, PositionAssessment>>({});

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
      api.realized().then(setRealized).catch(() => setRealized(null));
      api.performance().then(setPerf).catch(() => setPerf(null));
      api.saxoOauthStatus().then(setOauth).catch(() => setOauth(null));
      api.activity().then(setActivity).catch(() => setActivity(null));
      api.assessment()
        .then((r) => setAssessments(Object.fromEntries(r.assessments.map((x) => [x.symbol, x]))))
        .catch(() => setAssessments({}));
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 10000);
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
                <span
                  className={`badge ${oauth?.connected || brokerHealth?.connected ? "ok" : ""}`}
                  style={oauth?.connected || brokerHealth?.connected ? {} : { background: "#5c2b2b", color: "#ffb4b4" }}
                  title={oauth?.connected ? "OAuth-session — fornyes automatisk" : brokerHealth?.connected ? "Forbundet via token" : brokerHealth?.error ?? "Ikke forbundet"}
                >
                  {oauth?.connected
                    ? `● Saxo ✓ (${oauth.environment}, auto)`
                    : brokerHealth?.connected
                      ? `● Saxo ✓ (${brokerHealth.environment ?? "sim"})`
                      : "○ Saxo: ikke forbundet"}
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
            {/* Headline US indices — S&P 500, Dow 30, Nasdaq (1-day) */}
            <IndicesBar />

            {portfolio.source === "saxo" && (
              <div className="badge ok" style={{ display: "inline-block", marginBottom: 10 }}>
                ● Live Saxo account ({portfolio.currency ?? "SIM"}) — balances &amp; positions from Saxo
              </div>
            )}

            {/* ---- Live activity: what the platform is doing right now ---- */}
            {activity && (
              <div className="card" style={{ marginBottom: 12, padding: "10px 14px" }}>
                <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                  <span style={{
                    width: 9, height: 9, borderRadius: "50%", flexShrink: 0,
                    background: automation?.state.enabled ? "#3fb950" : "#8b949e",
                    boxShadow: automation?.state.enabled ? "0 0 6px #3fb950" : "none",
                  }} />
                  <div>
                    <span style={{ fontSize: 13 }}>
                      <strong>Now:</strong> {automation?.state.enabled ? activity.current.text : "Auto Trading is stopped"}
                    </span>
                    <span className="muted" style={{ fontSize: 11, marginLeft: 8 }}>
                      {activity.current.seconds_ago < 90
                        ? `${Math.round(activity.current.seconds_ago)}s ago`
                        : `${Math.round(activity.current.seconds_ago / 60)} min ago`}
                    </span>
                  </div>
                </div>
                {activity.recent.length > 0 && (
                  <div className="muted" style={{ fontSize: 11, marginTop: 6, paddingLeft: 19 }}>
                    {activity.recent.slice(0, 3).map((r, i) => (
                      <div key={i}>· {r.text}</div>
                    ))}
                  </div>
                )}
              </div>
            )}

            {/* ---- Top box: today's activity + realised P&L by period ---- */}
            {perf && perf.realized && (
              <div className="card" style={{ marginBottom: 12 }}>
                <div className="grid metrics">
                  <Metric label="Trades today" value={String(perf.trades_today ?? 0)} />
                  <Metric label="Gain today (DKK)"
                    value={pnl((perf.realized_dkk ?? perf.realized).today)} tone={tone(perf.realized.today)} />
                  <Metric label="Gain this week (DKK)" value={pnl((perf.realized_dkk ?? perf.realized).week)} tone={tone(perf.realized.week)} />
                  <Metric label="Gain this month (DKK)" value={pnl((perf.realized_dkk ?? perf.realized).month)} tone={tone(perf.realized.month)} />
                </div>
                <div className="muted" style={{ fontSize: 11, marginTop: 6 }}>
                  Realised P&amp;L from closed trades{perf.source === "saxo" ? " (Saxo)" : ""}. Open positions' unrealised P&amp;L is in the table below.
                </div>
              </div>
            )}

            <div className="grid metrics">
              <Metric label="Total value (DKK)" value={fmt(portfolio.total_value_dkk ?? portfolio.total_value)} />
              <Metric label="Cash (DKK)" value={fmt(portfolio.cash_dkk ?? portfolio.cash)} />
              {portfolio.margin_available != null && portfolio.margin_available > 0 && (
                <Metric label="Available to trade (DKK)" value={fmt(portfolio.margin_available_dkk ?? portfolio.margin_available)} />
              )}
              <Metric label="Exposure" value={pct(portfolio.exposure_pct)} />
              <Metric label="Drawdown" value={pct(portfolio.drawdown_pct)} tone={portfolio.drawdown_pct > 0 ? "neg" : undefined} />
              <Metric label="Open positions" value={String(portfolio.positions.length)} />
            </div>
            <div className="card section-gap">
              <h2>Equity curve</h2>
              <EquityChart snapshots={snapshots} />
            </div>

            <RiskRadar />

            <div className="card section-gap">
              <h2>Positions</h2>
              {portfolio.positions.length > 0 ? (
                <table>
                  <thead><tr><th>Symbol</th><th>Exchange</th><th>Qty</th><th>Avg</th><th>Last</th><th>Value</th><th>Unrealised P/L</th><th>P/L %</th><th>Verdict</th><th>To stop-loss</th><th></th></tr></thead>
                  <tbody>
                    {portfolio.positions.map((p) => {
                      const a = assessments[p.symbol.split(":")[0].toUpperCase()];
                      const vColor = a?.verdict === "SELL" ? "var(--neg, #dc2626)"
                        : a?.verdict === "HOLD" ? "var(--pos, #16a34a)" : "var(--muted, #888)";
                      return (
                      <Fragment key={p.symbol}>
                        <tr>
                          <td>{p.symbol.split(":")[0]}</td>
                          <td className="muted" style={{ fontSize: 12 }}>{p.exchange ?? "—"}</td>
                          <td>{p.quantity}</td><td>{p.avg_price.toFixed(2)}</td>
                          <td>{p.last_price.toFixed(2)}</td><td>{fmt(p.market_value)}</td>
                          <td className={p.unrealized_pnl >= 0 ? "pos" : "neg"}>{p.unrealized_pnl.toFixed(2)}</td>
                          <td className={(p.pnl_pct ?? 0) >= 0 ? "pos" : "neg"}>{(p.pnl_pct ?? 0) >= 0 ? "+" : ""}{(p.pnl_pct ?? 0).toFixed(2)}%</td>
                          <td title={a?.reason ?? ""} style={{ fontSize: 12, whiteSpace: "nowrap" }}>
                            {a ? (
                              <>
                                <span style={{ color: vColor, fontWeight: 600 }}>
                                  {a.verdict === "SELL" ? "● SELL" : a.verdict === "HOLD" ? "● HOLD" : "?"}
                                </span>
                                {a.quant_score != null && <span className="muted"> · q{a.quant_score.toFixed(0)}</span>}
                              </>
                            ) : <span className="muted">—</span>}
                          </td>
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
                          <td colSpan={11} style={{ padding: "10px 8px 16px" }}>
                            <div style={{ display: "flex", gap: 18, alignItems: "center", flexWrap: "wrap" }}>
                              <div style={{ flex: "0 0 320px", maxWidth: 320 }}>
                                <PositionChart symbol={p.symbol} entry={p.avg_price} />
                              </div>
                              {a && (
                                <div style={{ fontSize: 12, lineHeight: 1.6, minWidth: 220 }}>
                                  <div style={{ fontWeight: 600, color: vColor, fontSize: 13 }}>
                                    {a.verdict === "SELL" ? "Will SELL next cycle"
                                      : a.verdict === "HOLD" ? "Will HOLD"
                                      : "No assessment"}
                                    {a.quant_score != null && (
                                      <span className="muted" style={{ fontWeight: 400 }}> · quant {a.quant_score.toFixed(0)}/100</span>
                                    )}
                                  </div>
                                  <div className="muted">{a.reason}</div>
                                  <div className="muted" style={{ marginTop: 3 }}>
                                    {a.stop_price != null && <>stop-loss @ {a.stop_price.toFixed(2)}</>}
                                    {a.take_profit_price != null && <> · take-profit @ {a.take_profit_price.toFixed(2)}</>}
                                    {a.trailing_stop_price != null && <> · trailing @ {a.trailing_stop_price.toFixed(2)}</>}
                                  </div>
                                </div>
                              )}
                            </div>
                          </td>
                        </tr>
                      </Fragment>
                    );
                    })}
                  </tbody>
                </table>
              ) : <p className="muted">No open positions.</p>}
            </div>

            {/* ---- Realised P&L by stock (which names you actually gained/lost on) ---- */}
            {realized && realized.per_symbol.length > 0 && (
              <div className="card section-gap">
                <h2>Realised P&amp;L by stock</h2>
                <p className="muted" style={{ fontSize: 12, marginTop: -4 }}>
                  From closed trades{realized.source === "saxo" ? " (Saxo)" : ""}. Total realised{" "}
                  <strong className={realized.total >= 0 ? "pos" : "neg"}>
                    {realized.total >= 0 ? "+" : ""}{realized.total.toLocaleString()} {realized.currency ?? ""}
                  </strong>. Open positions' gains are in the table above.
                </p>
                <table>
                  <thead><tr><th>Symbol</th><th>Realised P/L</th><th>Closed trades</th></tr></thead>
                  <tbody>
                    {realized.per_symbol.filter((r) => r.realized_pnl !== 0).map((r) => (
                      <tr key={r.symbol}>
                        <td>{r.symbol}</td>
                        <td className={r.realized_pnl >= 0 ? "pos" : "neg"}>
                          {r.realized_pnl >= 0 ? "+" : ""}{r.realized_pnl.toLocaleString()} {realized.currency ?? ""}
                        </td>
                        <td>{r.trades}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {portfolio.source === "saxo" && (
              <div className="section-gap">
                <OpenOrders orders={portfolio.open_orders ?? []} onChanged={refresh} onToast={showToast} />
              </div>
            )}
            <div className="card section-gap">
              <h2>Latest signals</h2>
              {signals.length > 0 ? (
                <div style={{ overflowX: "auto" }}>
                  <table>
                    <thead><tr><th>Symbol</th><th>Decision</th><th>Quant</th><th>News</th><th>Risk</th><th>Reason</th></tr></thead>
                    <tbody>
                      {signals.map((s) => (
                        <tr key={s.id} title={`${s.quant_rationale}\n${s.news_rationale}\n${s.risk_rationale}`}>
                          <td>{s.symbol}</td><td><span className={`tag ${s.decision}`}>{s.decision}</span></td>
                          <td>{s.quant_score.toFixed(1)}</td><td>{s.news_score.toFixed(1)}</td>
                          <td>{s.risk_score.toFixed(1)}</td>
                          <td className="muted" style={{ fontSize: 12, maxWidth: 420 }}>
                            {s.decision === "rejected" ? (s.reject_reason || "—") : "✓ approved"}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
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
