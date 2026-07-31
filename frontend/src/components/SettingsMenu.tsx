import { useEffect, useState } from "react";
import { api } from "../api";
import type { DiscoveryCandidate, DiscoveryStatus, SettingsView } from "../types";

function timeAgo(iso: string | null): string {
  if (!iso) return "never";
  const secs = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
  if (secs < 60) return `${secs}s ago`;
  if (secs < 3600) return `${Math.round(secs / 60)} min ago`;
  return `${Math.round(secs / 3600)}h ago`;
}

function Field({ label, children, hint }: { label: string; children: React.ReactNode; hint?: string }) {
  return (
    <div style={{ marginBottom: 12 }}>
      <label className="field" style={{ display: "block", marginBottom: 4 }}>{label}</label>
      {children}
      {hint && <div className="muted" style={{ fontSize: 11, marginTop: 3 }}>{hint}</div>}
    </div>
  );
}

function Toggle({ on, onChange, onLabel = "on", offLabel = "off" }: { on: boolean; onChange: (v: boolean) => void; onLabel?: string; offLabel?: string }) {
  return (
    <label style={{ display: "flex", gap: 8, alignItems: "center" }}>
      <input type="checkbox" checked={on} onChange={(e) => onChange(e.target.checked)} />
      <span>{on ? onLabel : offLabel}</span>
    </label>
  );
}

// Numeric values are kept as PLAIN STRINGS while editing (so you can clear a
// field and retype freely) and only converted to numbers on Save.
const NUM_FIELDS = new Set([
  "quant_score_threshold", "news_score_threshold",
  "stop_loss_pct", "take_profit_pct", "trailing_stop_pct",
  "risk_max_open_positions", "risk_max_position_pct", "risk_max_total_exposure_pct",
  "risk_max_risk_per_trade_pct", "risk_max_daily_loss_pct", "risk_max_total_drawdown_pct",
  "market_lookback_days", "market_horizon_minutes",
  "commission_per_trade", "commission_pct", "slippage_bps",
  "trade_cooldown_minutes", "min_trade_notional",
  "discovery_top_n", "discovery_max_pool", "automation_interval_seconds",
  "discovery_preopen_minutes",
]);
// Shown/typed as percent, stored on the backend as fractions.
const PCT_FIELDS = new Set([
  "stop_loss_pct", "take_profit_pct", "trailing_stop_pct",
  "risk_max_position_pct", "risk_max_total_exposure_pct",
  "risk_max_risk_per_trade_pct", "risk_max_daily_loss_pct", "risk_max_total_drawdown_pct",
]);

type Tab = "broker" | "data" | "strategy" | "risk" | "ai";
const TABS: { id: Tab; label: string }[] = [
  { id: "broker", label: "🏦 Broker & Saxo" },
  { id: "data", label: "🔍 Data & Discovery" },
  { id: "strategy", label: "📐 Strategy & Exits" },
  { id: "risk", label: "🛡 Risk" },
  { id: "ai", label: "🤖 AI" },
];

export function SettingsMenu({ onChanged, onToast }: { onChanged: () => void; onToast: (m: string) => void }) {
  const [s, setS] = useState<SettingsView | null>(null);
  const [tab, setTab] = useState<Tab>("broker");
  const [form, setForm] = useState<Record<string, unknown>>({});
  const [secrets, setSecrets] = useState({ anthropic_auth_token: "", anthropic_api_key: "", saxo_access_token: "", saxo_app_secret: "" });
  const [allocation, setAllocationAmt] = useState("100000");
  const [picks, setPicks] = useState<DiscoveryCandidate[]>([]);
  const [discStatus, setDiscStatus] = useState<DiscoveryStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [saxoResult, setSaxoResult] = useState<string | null>(null);
  const [oauth, setOauth] = useState<{ configured: boolean; connected: boolean; environment: string } | null>(null);
  const [sizing, setSizing] = useState<Awaited<ReturnType<typeof api.sizingRecommendation>> | null>(null);
  const [testSymbol, setTestSymbol] = useState("AAPL");
  const [selfTest, setSelfTest] = useState<{ ok: boolean; placed_order: boolean; steps: { name: string; ok: boolean; detail?: unknown; error?: string }[] } | null>(null);

  const load = () =>
    api.getSettings().then((v) => {
      setS(v);
      const f: Record<string, unknown> = {
        ai_auth_mode: v.ai_auth_mode,
        ai_model: v.ai_model,
        saxo_environment: v.saxo_environment,
        saxo_app_key: v.saxo_app_key,
        saxo_redirect_uri: v.saxo_redirect_uri,
        default_broker_mode: v.default_broker_mode,
        live_trading_enabled: v.live_trading_enabled,
        active_strategy: v.active_strategy,
        quant_score_threshold: v.quant_score_threshold,
        news_score_threshold: v.news_score_threshold,
        news_gate_mode: v.news_gate_mode,
        stop_loss_pct: v.stop_loss_pct * 100,
        take_profit_pct: v.take_profit_pct * 100,
        trailing_stop_pct: v.trailing_stop_pct * 100,
        risk_max_open_positions: v.risk_max_open_positions,
        risk_max_position_pct: v.risk_max_position_pct * 100,
        risk_max_total_exposure_pct: v.risk_max_total_exposure_pct * 100,
        risk_max_risk_per_trade_pct: v.risk_max_risk_per_trade_pct * 100,
        risk_max_daily_loss_pct: v.risk_max_daily_loss_pct * 100,
        risk_max_total_drawdown_pct: v.risk_max_total_drawdown_pct * 100,
        market_hours_enabled: v.market_hours_enabled,
        enforce_loss_halts: v.enforce_loss_halts,
        streaming_autostart: v.streaming_autostart,
        auto_size_from_capital: v.auto_size_from_capital,
        risk_appetite: v.risk_appetite,
        concentration_limit_pct: v.concentration_limit_pct,
        bellwether_freeze: v.bellwether_freeze,
        event_veto_fail_closed: v.event_veto_fail_closed,
        max_bar_age_days: v.max_bar_age_days,
        alert_webhook_url: v.alert_webhook_url,
        circuit_breaker_enabled: v.circuit_breaker_enabled,
        overnight_news_watch: v.overnight_news_watch,
        market_data_source: v.market_data_source,
        news_enabled: v.news_enabled,
        market_lookback_days: v.market_lookback_days,
        market_horizon_minutes: v.market_horizon_minutes,
        commission_per_trade: v.commission_per_trade,
        commission_pct: v.commission_pct,
        slippage_bps: v.slippage_bps,
        trade_cooldown_minutes: v.trade_cooldown_minutes,
        min_trade_notional: v.min_trade_notional,
        discovery_enabled: v.discovery_enabled,
        discovery_top_n: v.discovery_top_n,
        discovery_candidates: v.discovery_candidates,
        discovery_sources: v.discovery_sources,
        discovery_max_pool: v.discovery_max_pool,
        discovery_open_market_only: v.discovery_open_market_only,
        discovery_preopen_minutes: v.discovery_preopen_minutes,
        discovery_region_weights: v.discovery_region_weights,
        automation_interval_seconds: v.automation_interval_seconds,
        automation_universe: v.automation_universe,
      };
      // Numbers become editable strings (round away float noise like 8.000000001).
      for (const k of NUM_FIELDS) if (f[k] != null) f[k] = String(Math.round(Number(f[k]) * 10000) / 10000);
      setForm(f);
    });

  useEffect(() => {
    load();
    const poll = () => {
      api.discoveryStatus().then(setDiscStatus).catch(() => {});
      api.saxoOauthStatus().then(setOauth).catch(() => {});
      api.sizingRecommendation().then(setSizing).catch(() => {});
    };
    poll();
    const id = setInterval(poll, 15000);
    return () => clearInterval(id);
  }, []);

  const set = (k: string, v: unknown) => setForm((f) => ({ ...f, [k]: v }));

  const saxoLogin = () => {
    if (!oauth?.configured) { setErr("Gem App key, App secret og Redirect URL først (Save)."); return; }
    window.open(api.saxoLoginUrl(), "_blank");
  };

  // Recommended exit presets per strategy — pre-filled when you pick one.
  const STRATEGY_PRESETS: Record<string, { sl: number; tp: number; tr: number; cd: number }> = {
    quick_flip: { sl: 1, tp: 2, tr: 0, cd: 2 },
    momentum: { sl: 8, tp: 15, tr: 10, cd: 5 },
    mean_reversion: { sl: 4, tp: 6, tr: 0, cd: 5 },
    rsi2: { sl: 4, tp: 6, tr: 0, cd: 2 },
    donchian: { sl: 8, tp: 0, tr: 12, cd: 5 },
    macd: { sl: 6, tp: 0, tr: 10, cd: 5 },
  };

  const pickStrategy = (name: string) => {
    const p = STRATEGY_PRESETS[name];
    setForm((f) => ({
      ...f,
      active_strategy: name,
      ...(p ? { stop_loss_pct: String(p.sl), take_profit_pct: String(p.tp), trailing_stop_pct: String(p.tr), trade_cooldown_minutes: String(p.cd) } : {}),
    }));
    if (p) onToast(`${name}: pre-filled stop ${p.sl}% / take-profit ${p.tp}% / cooldown ${p.cd}m — adjust & Save`);
  };

  async function guard(fn: () => Promise<void>, msg?: string) {
    setBusy(true); setErr(null);
    try { await fn(); if (msg) onToast(msg); onChanged(); }
    catch (e) { setErr((e as Error).message); }
    finally { setBusy(false); }
  }

  const save = () =>
    guard(async () => {
      const payload: Record<string, unknown> = {};
      for (const [k, v] of Object.entries(form)) {
        if (NUM_FIELDS.has(k)) {
          const str = String(v ?? "").trim().replace(",", ".");
          if (str === "") continue;              // empty = keep current value
          const n = Number(str);
          if (!Number.isFinite(n)) continue;      // garbage = keep current value
          payload[k] = PCT_FIELDS.has(k) ? n / 100 : n;
        } else {
          payload[k] = v;
        }
      }
      for (const [k, v] of Object.entries(secrets)) if (v.trim()) payload[k] = v.trim();
      await api.updateSettings(payload);
      setSecrets({ anthropic_auth_token: "", anthropic_api_key: "", saxo_access_token: "", saxo_app_secret: "" });
      await load();
    }, "Settings saved — persists across restarts ✓");

  const testSaxo = () =>
    guard(async () => {
      const r = await api.saxoTest();
      if (r.connected) {
        const bal = r.balance ?? {};
        setSaxoResult(`✓ Connected (${r.environment}). Cash ${bal.cash ?? "?"} ${bal.currency ?? ""}, total ${bal.total_value ?? "?"}.`);
      } else {
        setSaxoResult(`✗ ${r.error ?? "not connected"}`);
      }
    });

  const runSelfTest = (placeOrder: boolean) =>
    guard(async () => {
      setSelfTest(null);
      const r = await api.saxoSelfTest(testSymbol.trim().toUpperCase(), placeOrder, 1);
      setSelfTest(r);
    }, placeOrder ? "SIM test order sent" : "Self-test done");

  const applySizing = () => guard(async () => {
    if (!sizing) return;
    const r = sizing.recommended;
    setForm((f) => ({
      ...f,
      min_trade_notional: String(r.min_trade_notional),
      risk_max_open_positions: String(r.risk_max_open_positions),
      risk_max_position_pct: String(r.risk_max_position_pct * 100),
      risk_max_risk_per_trade_pct: String(r.risk_max_risk_per_trade_pct * 100),
      discovery_top_n: String(r.discovery_top_n),
    }));
    await api.updateSettings({
      min_trade_notional: r.min_trade_notional,
      risk_max_open_positions: r.risk_max_open_positions,
      risk_max_position_pct: r.risk_max_position_pct,
      risk_max_risk_per_trade_pct: r.risk_max_risk_per_trade_pct,
      discovery_top_n: r.discovery_top_n,
    });
    await load();
  }, "Anbefalede indstillinger anvendt ✓");

  const screen = () => guard(async () => { setPicks((await api.discovery(Number(form.discovery_top_n) || 8)).candidates); });
  const applyDisc = () => guard(async () => { const r = await api.applyDiscovery(Number(form.discovery_top_n) || 8); onToast(`Universe set: ${r.universe.join(", ")}`); await load(); });
  const setAlloc = () => guard(async () => { await api.setAllocation(Number(allocation)); }, `Trading capital set to ${allocation}`);

  if (!s) return <div className="card"><h2>Setup</h2><p className="muted">Loading…</p></div>;
  const opt = s.options;

  const num = (k: string, width = 90, step?: string) => (
    <input type="text" inputMode="decimal" value={String(form[k] ?? "")}
      onChange={(e) => set(k, e.target.value)} style={{ maxWidth: width }} data-step={step} />
  );

  const saxoConnected = Boolean(oauth?.connected);

  return (
    <div className="card">
      {/* --- Sticky header: status + save, always visible --- */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: 8 }}>
        <h2 style={{ margin: 0 }}>⚙ Setup</h2>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <span className={`badge ${saxoConnected ? "ok" : ""}`}
            style={saxoConnected ? {} : { background: "#5c2b2b", color: "#ffb4b4" }}>
            {saxoConnected ? `● Saxo forbundet (${oauth?.environment}, auto-fornyes)` : "○ Saxo ikke forbundet"}
          </span>
          <button disabled={busy} onClick={save}>💾 Save</button>
        </div>
      </div>
      <div className="muted" style={{ fontSize: 11, margin: "6px 0 12px" }}>{s.persistence}</div>

      {/* --- Tabs --- */}
      <div className="btn-row" style={{ marginBottom: 16, borderBottom: "1px solid var(--border)", paddingBottom: 10 }}>
        {TABS.map((t) => (
          <button key={t.id} className={tab === t.id ? "" : "secondary"} onClick={() => setTab(t.id)}>{t.label}</button>
        ))}
      </div>

      {/* ================= BROKER & SAXO ================= */}
      {tab === "broker" && (
        <div className="grid two-col">
          <div>
            <h3 style={{ fontSize: 13 }}>Broker</h3>
            <Field label="Broker" hint="saxo = trades on your Saxo SIM account. simulation = internal paper broker.">
              <select value={String(form.default_broker_mode)} onChange={(e) => set("default_broker_mode", e.target.value)}>
                {opt.default_broker_mode.map((o) => <option key={o}>{o}</option>)}
              </select>
            </Field>
            <Field label="Saxo environment" hint="sim = simulated money (your DEMO app works ONLY here). live requires a Saxo-approved live app.">
              <select value={String(form.saxo_environment)} onChange={(e) => set("saxo_environment", e.target.value)}>
                {opt.saxo_environment.map((o) => <option key={o}>{o}</option>)}
              </select>
            </Field>
            <Field label="Live trading (REAL money)" hint="Leave OFF. SIM trades fully without it. Only for a documented, gated go-live.">
              <Toggle on={Boolean(form.live_trading_enabled)} onChange={(v) => set("live_trading_enabled", v)}
                onLabel="ENABLED (real money armed!)" offLabel="off — SIM works fine" />
            </Field>
            {/* Paper-only: on Saxo the capital IS the live account balance, so this
                field would be misleading. Only shown in simulation mode. */}
            {String(form.default_broker_mode) === "simulation" && (
              <Field label="Trading capital (paper)" hint="Kun i simulation: beløbet paper-brokeren handler med (nulstiller paper-positioner). På Saxo er kapitalen din live konto-saldo.">
                <div className="btn-row">
                  <input type="text" value={allocation} onChange={(e) => setAllocationAmt(e.target.value)} style={{ maxWidth: 140 }} />
                  <button className="secondary" disabled={busy} onClick={setAlloc}>Set</button>
                </div>
              </Field>
            )}
          </div>
          <div>
            <h3 style={{ fontSize: 13 }}>Saxo OAuth (recommended — one login, auto-renews)</h3>
            <Field label="App key" hint="from developer.saxo → Application Management">
              <input type="text" value={String(form.saxo_app_key ?? "")} onChange={(e) => set("saxo_app_key", e.target.value)} />
            </Field>
            <Field label="App secret" hint={s.saxo_app_secret.set ? `saved ${s.saxo_app_secret.hint} · leave blank to keep` : "from your Saxo app page"}>
              <input type="password" placeholder="app secret" value={secrets.saxo_app_secret}
                onChange={(e) => setSecrets((x) => ({ ...x, saxo_app_secret: e.target.value }))} />
            </Field>
            <Field label="Redirect URL" hint="must ALSO be registered on the app at developer.saxo">
              <input type="text" placeholder="http://<server-ip>:8000/control/saxo/callback"
                value={String(form.saxo_redirect_uri ?? "")} onChange={(e) => set("saxo_redirect_uri", e.target.value)} />
            </Field>
            <div className="btn-row">
              <button disabled={busy} onClick={saxoLogin}>🔐 Log ind hos Saxo</button>
              <button className="secondary" disabled={busy} onClick={testSaxo}>Test connection</button>
            </div>
            {oauth && (
              <div className="muted" style={{ fontSize: 12, marginTop: 6 }}>
                {oauth.connected ? `✓ OAuth-session aktiv (${oauth.environment}) — fornyes automatisk, overlever genstart`
                  : oauth.configured ? "OAuth klar — klik 'Log ind hos Saxo'"
                  : "Udfyld App key, App secret og Redirect URL → Save → log ind"}
              </div>
            )}
            {saxoResult && <div className="muted" style={{ fontSize: 12, marginTop: 6 }}>{saxoResult}</div>}

            <details style={{ marginTop: 14 }}>
              <summary className="muted" style={{ cursor: "pointer", fontSize: 12 }}>Fallback: 24h token (only if OAuth is unavailable)</summary>
              <Field label="Saxo access token (24h SIM token)" hint={s.saxo_access_token.set ? `saved ${s.saxo_access_token.hint} · leave blank to keep` : "developer.saxo → Get 24h token"}>
                <input type="password" placeholder="Saxo bearer token" value={secrets.saxo_access_token}
                  onChange={(e) => setSecrets((x) => ({ ...x, saxo_access_token: e.target.value }))} />
              </Field>
            </details>

            <details style={{ marginTop: 8 }}>
              <summary className="muted" style={{ cursor: "pointer", fontSize: 12 }}>Self-test (verify the full trading path)</summary>
              <Field label="Symbol" hint="account → balance → instrument → quote → bars (read-only). The order button places a REAL 1-share SIM buy.">
                <div className="btn-row" style={{ marginTop: 2 }}>
                  <input type="text" value={testSymbol} onChange={(e) => setTestSymbol(e.target.value)} style={{ maxWidth: 100 }} />
                  <button className="secondary" disabled={busy} onClick={() => runSelfTest(false)}>Run self-test</button>
                  <button className="warn" disabled={busy} onClick={() => runSelfTest(true)}>Place 1-share SIM order</button>
                </div>
              </Field>
              {selfTest && (
                <ul className="gate">
                  {selfTest.steps.map((st) => (
                    <li key={st.name}>
                      <span className={`mark ${st.ok ? "pass" : "fail"}`}>{st.ok ? "✓" : "✗"}</span>
                      <span>{st.name}</span>
                      <span className="detail">— {st.ok ? JSON.stringify(st.detail) : st.error}</span>
                    </li>
                  ))}
                </ul>
              )}
            </details>
          </div>
        </div>
      )}

      {/* ================= DATA & DISCOVERY ================= */}
      {tab === "data" && (
        <div className="grid two-col">
          <div>
            <h3 style={{ fontSize: 13 }}>Market data</h3>
            <Field label="Market data source" hint="yfinance (recommended): covers every discovery name + news. Exits still use Saxo streaming prices.">
              <select value={String(form.market_data_source)} onChange={(e) => set("market_data_source", e.target.value)}>
                {opt.market_data_source.map((o) => <option key={o}>{o}</option>)}
              </select>
            </Field>
            <Field label="Use real news"><Toggle on={Boolean(form.news_enabled)} onChange={(v) => set("news_enabled", v)} /></Field>
            <Field label="Pause when market closed" hint="Auto Trading pauses while the traded exchanges are closed.">
              <Toggle on={Boolean(form.market_hours_enabled)} onChange={(v) => set("market_hours_enabled", v)} />
            </Field>
            <Field label="Overnight news watch" hint="While the market is closed, scan news on the stocks you hold and alert you (→ webhook) on strongly negative headlines — a gap-risk heads-up before the open. Alerts only, never trades.">
              <Toggle on={Boolean(form.overnight_news_watch)} onChange={(v) => set("overnight_news_watch", v)} />
            </Field>
            <Field label="Tick interval (seconds)" hint="How often the automation loop runs. 30 is a good default.">
              {num("automation_interval_seconds")}
            </Field>
            <Field label="Auto-reconnect streaming" hint="Keep Saxo streaming (real-time exits) alive by itself — restarts on boot and reconnects if it drops. No more manual 'Start streaming'.">
              <Toggle on={Boolean(form.streaming_autostart)} onChange={(v) => set("streaming_autostart", v)} />
            </Field>
          </div>
          <div>
            <h3 style={{ fontSize: 13 }}>Discovery (the screener picks what to trade)</h3>
            <Field label="Auto-discover"><Toggle on={Boolean(form.discovery_enabled)} onChange={(v) => set("discovery_enabled", v)} /></Field>
            <Field label="Only open markets" hint="Universe only contains stocks whose exchange is open right now.">
              <Toggle on={Boolean(form.discovery_open_market_only)} onChange={(v) => set("discovery_open_market_only", v)} />
            </Field>
            <Field label="Pre-open warmup (min)" hint="Also scan names whose exchange opens within this many minutes, so the universe is ready at the bell. Trading still waits for the real open. 0 = off.">
              {num("discovery_preopen_minutes", 90)}
            </Field>
            <Field label="Momentum sources" hint="Gathered, ranked by the multi-factor score, top N traded.">
              <div style={{ display: "flex", flexWrap: "wrap", gap: 10 }}>
                {(opt.discovery_sources ?? []).map((src) => {
                  const cur = String(form.discovery_sources || "").split(",").map((x) => x.trim()).filter(Boolean);
                  const on = cur.includes(src);
                  const label = { day_gainers: "Top gainers", most_actives: "Most active", small_cap_gainers: "Small-cap gainers", aggressive_small_caps: "Aggressive small caps", growth_tech: "Growth tech", wsb: "WallStreetBets", sp500: "S&P 500", dow30: "Dow 30", omxc25: "OMX C25 (DK)", dax: "DAX (DE)", cac: "CAC 40 (FR)", europe: "Europe (mixed)" }[src] ?? src;
                  return (
                    <label key={src} style={{ display: "flex", gap: 5, alignItems: "center", fontSize: 12 }}>
                      <input type="checkbox" checked={on} onChange={(e) => {
                        const next = e.target.checked ? [...cur, src] : cur.filter((x) => x !== src);
                        set("discovery_sources", next.join(","));
                      }} />
                      <span>{label}</span>
                    </label>
                  );
                })}
              </div>
            </Field>
            <div style={{ display: "flex", gap: 16 }}>
              <Field label="Top N">{num("discovery_top_n", 80)}</Field>
              <Field label="Max pool">{num("discovery_max_pool", 80)}</Field>
            </div>
            <Field label="Region split (US/EU)" hint='Reserverer top-N-pladser pr. region. Fx "US:0.6,EU:0.4" = ca. 60 % USA, 40 % EU. Tom = ren global ranking. Løber en region tør, tager den anden resten.'>
              <input type="text" value={String(form.discovery_region_weights ?? "")}
                onChange={(e) => set("discovery_region_weights", e.target.value)} style={{ maxWidth: 160 }} />
            </Field>
            <Field label="Static pool (only used when no source is ticked)">
              <textarea value={String(form.discovery_candidates)} onChange={(e) => set("discovery_candidates", e.target.value)}
                rows={2} style={{ width: "100%", background: "var(--panel-2)", color: "var(--text)", border: "1px solid var(--border)", borderRadius: 8, padding: 8, fontSize: 12 }} />
            </Field>
            {discStatus && (
              <div className="muted" style={{ fontSize: 11, marginBottom: 8 }}>
                Last market scan: <strong>{timeAgo(discStatus.last_scan_at)}</strong>
                {discStatus.last_scan_at && ` · re-scans at most every ${Math.round(discStatus.ttl_seconds / 60)} min`}
              </div>
            )}
            <div className="btn-row">
              <button className="secondary" disabled={busy} onClick={screen}>Screen now</button>
              <button disabled={busy} onClick={applyDisc}>Apply to universe</button>
            </div>
          </div>
        </div>
      )}

      {/* ================= STRATEGY & EXITS ================= */}
      {tab === "strategy" && (
        <div className="grid two-col">
          <div>
            <h3 style={{ fontSize: 13 }}>Entry</h3>
            <Field label="Active strategy" hint="Picking one pre-fills recommended exits below. Compare under Analytics.">
              <select value={String(form.active_strategy)} onChange={(e) => pickStrategy(e.target.value)}>
                {(opt.active_strategy ?? []).map((o) => {
                  const label = { momentum: "Momentum / trend", mean_reversion: "Mean-reversion", quick_flip: "Quick-flip (profit-target)", rsi2: "RSI(2) mean-reversion", donchian: "Donchian breakout (Turtle)", macd: "MACD / EMA-crossover" }[o] ?? o;
                  return <option key={o} value={o}>{label}</option>;
                })}
              </select>
            </Field>
            <Field label="Quant score gate (buy if above)" hint="Recommended 65. Higher = more selective.">
              {num("quant_score_threshold")}
            </Field>
            <Field label="News mode" hint={form.news_gate_mode === "gate"
              ? "gate: buys require the news score above the threshold below"
              : "advisory (recommended): news score is shown on signals but never blocks a buy"}>
              <select value={String(form.news_gate_mode ?? "advisory")} onChange={(e) => set("news_gate_mode", e.target.value)}>
                {(opt.news_gate_mode ?? ["advisory", "gate"]).map((o) => <option key={o}>{o}</option>)}
              </select>
            </Field>
            {form.news_gate_mode === "gate" && (
              <Field label="News score gate (buy if above)">{num("news_score_threshold")}</Field>
            )}
            <Field label="Bar timeframe" hint="Daily (recommended): the only honest choice on ~15-min-delayed data.">
              <select value={String(form.market_horizon_minutes)} onChange={(e) => set("market_horizon_minutes", e.target.value)}>
                <option value={1440}>Daily</option>
                <option value={60}>1 hour</option>
                <option value={30}>30 min</option>
                <option value={15}>15 min</option>
                <option value={5}>5 min</option>
              </select>
            </Field>
          </div>
          <div>
            <h3 style={{ fontSize: 13 }}>Exits (sell triggers, % of entry — 0 = off)</h3>
            <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
              <Field label="Stop-loss %">{num("stop_loss_pct")}</Field>
              <Field label="Take-profit %">{num("take_profit_pct")}</Field>
              <Field label="Trailing stop %">{num("trailing_stop_pct")}</Field>
            </div>
            <h3 style={{ fontSize: 13, marginTop: 8 }}>Costs & churn control</h3>
            <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
              <Field label="Commission (fixed)">{num("commission_per_trade")}</Field>
              <Field label="Commission % (fraction)" hint="0.0008 = 8 bps">{num("commission_pct")}</Field>
              <Field label="Slippage (bps)">{num("slippage_bps")}</Field>
            </div>
            <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
              <Field label="Trade cooldown (min)" hint="Min minutes between trades on the same symbol.">{num("trade_cooldown_minutes")}</Field>
              <Field label="Min trade notional" hint="Skip smaller orders (commission-heavy).">{num("min_trade_notional", 110)}</Field>
            </div>
          </div>
        </div>
      )}

      {/* ================= RISK ================= */}
      {tab === "risk" && (
        <>
        {sizing && (
          <div className="card" style={{ marginBottom: 16, padding: 14, border: "1px solid var(--border-accent)" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 8 }}>
              <div>
                <div style={{ fontWeight: 500 }}>📊 Anbefalede indstillinger ud fra din kapital</div>
                <div className="muted" style={{ fontSize: 12, marginTop: 2 }}>
                  Konto: <strong>{Math.round(sizing.total_value_dkk).toLocaleString("da-DK")} DKK</strong>
                  {sizing.account_currency !== "DKK" && ` (${Math.round(sizing.total_value_native).toLocaleString("da-DK")} ${sizing.account_currency} × ${sizing.to_dkk_rate})`}
                  {" — opdateres automatisk når kontoen ændrer sig."}
                </div>
              </div>
              <button disabled={busy} onClick={applySizing}>Anvend anbefalinger</button>
            </div>
            <div style={{ marginTop: 10, paddingTop: 8, borderTop: "1px solid var(--border)" }}>
              <Field label="Risiko-appetit" hint="Styrer hvor aggressivt auto-size handler. Selv trin 5 er inden for sikre grænser — max 2% risiko/handel, ingen gearing/short. Kapital-skaleringen (antal positioner efter kontostørrelse) bevares uanset trin.">
                {(() => {
                  const AP = [
                    { n: 1, name: "Meget forsigtig", sub: "0,5% · 40%", c: "#16a34a" },
                    { n: 2, name: "Forsigtig", sub: "0,75% · 60%", c: "#65a30d" },
                    { n: 3, name: "Balanceret", sub: "1% · 80%", c: "#f59e0b" },
                    { n: 4, name: "Aggressiv", sub: "1,5% · 95%", c: "#f97316" },
                    { n: 5, name: "Meget aggressiv", sub: "2% · 95%", c: "#dc2626" },
                  ];
                  const cur = Number(form.risk_appetite ?? 3);
                  const act = AP.find((a) => a.n === cur) ?? AP[2];
                  return (
                    <div style={{ maxWidth: 340 }}>
                      <div style={{ display: "flex", gap: 4, background: "var(--border)", padding: 4, borderRadius: 10 }}>
                        {AP.map((a) => (
                          <button key={a.n} type="button" title={a.name} onClick={() => set("risk_appetite", a.n)}
                            style={{
                              flex: 1, padding: "9px 4px", border: "none", borderRadius: 7, cursor: "pointer",
                              background: a.n === cur ? a.c : "transparent",
                              color: a.n === cur ? "#fff" : "var(--muted, #8b949e)",
                              fontWeight: a.n === cur ? 700 : 500, fontSize: 14, fontVariantNumeric: "tabular-nums",
                              boxShadow: a.n === cur ? `0 0 14px ${a.c}66` : "none", transition: "all .2s",
                            }}>
                            {a.n}
                          </button>
                        ))}
                      </div>
                      <div style={{ marginTop: 7, fontSize: 12 }}>
                        <span style={{ color: act.c, fontWeight: 700 }}>{act.name}</span>
                        <span className="muted"> · {act.sub}  (risiko/handel · maks eksponering)</span>
                      </div>
                    </div>
                  );
                })()}
              </Field>
              <Field label="Auto-size fra kapital" hint="Når slået TIL: platformen anvender selv disse anbefalinger hvert cyklus og skalerer positioner/størrelser op og ned, når kontoen ændrer sig. Appetit-trinnet ovenfor styrer HVOR aggressivt.">
                <Toggle on={Boolean(form.auto_size_from_capital)} onChange={(v) => set("auto_size_from_capital", v)}
                  onLabel="til — styres dynamisk af kapitalen" offLabel="fra — manuel styring" />
              </Field>
            </div>
            <div style={{ marginTop: 10, paddingTop: 8, borderTop: "1px solid var(--border)" }}>
              <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6 }}>Sektor-risiko-gate (Risk Radar — handlende lag, opt-in)</div>
              <Field label="Koncentrations-loft %" hint="0 = FRA. Når &gt;0: pauser NYE entries hvis porteføljens største sektor-beta (SPY/QQQ/SMH) allerede er ≥ dette (fx 150). Kun read-only radar kører når FRA. Kan kun pause — aldrig udvide.">
                <input type="number" min="0" step="10" value={String(form.concentration_limit_pct ?? 0)}
                  onChange={(e) => set("concentration_limit_pct", Number(e.target.value))} style={{ maxWidth: 120 }} />
              </Field>
              <Field label="Bellwether-freeze" hint="Når TIL: pauser nye entries mens en bellwether (MSFT/NVDA…) af en sektor du er eksponeret mod rapporterer inden for event-vindet.">
                <Toggle on={Boolean(form.bellwether_freeze)} onChange={(v) => set("bellwether_freeze", v)}
                  onLabel="til — pauser ved bellwether-earnings" offLabel="fra" />
              </Field>
              <Field label="Event-veto fail-closed" hint="Når TIL: hvis earnings-opslaget FEJLER (yfinance throttlet / ukendt ticker), afvises entry i stedet for at gå igennem. Default FRA = går igennem ved data-fejl. Til = maksimal forsigtighed, koster nogle handler når feed'et er ustabilt.">
                <Toggle on={Boolean(form.event_veto_fail_closed)} onChange={(v) => set("event_veto_fail_closed", v)}
                  onLabel="til — afvis ved data-fejl" offLabel="fra — gå igennem ved data-fejl" />
              </Field>
              <Field label="Max bar-alder (dage)" hint="Spring en NY entry over hvis dens seneste kurs-bar er ældre end dette (forældet kurs sizer ellers forkert). 0 = fra. 4 dækker en lang weekend + helligdag.">
                <input type="number" min="0" step="1" value={String(form.max_bar_age_days ?? 4)}
                  onChange={(e) => set("max_bar_age_days", Number(e.target.value))} style={{ maxWidth: 100 }} />
              </Field>
              <Field label="Alarm-webhook (kritiske hændelser)" hint="Indsæt en Telegram/Slack/Discord/generisk webhook-URL. KRITISKE alarmer (halt, kill switch, stor drawdown, tick-fejl) POSTes dertil, så du får besked out-of-band — også hvis du ikke har dashboardet åbent. Tom = fra.">
                <input type="text" value={String(form.alert_webhook_url ?? "")} placeholder="https://hooks.slack.com/…"
                  onChange={(e) => set("alert_webhook_url", e.target.value)} style={{ minWidth: 240 }} />
              </Field>
            </div>
            <table style={{ width: "100%", marginTop: 10, fontSize: 12 }}>
              <thead><tr><th style={{ textAlign: "left" }}>Indstilling</th><th>Anbefalet</th><th>Nu</th></tr></thead>
              <tbody>
                <tr><td style={{ textAlign: "left" }}>Min. handel</td>
                  <td>{sizing.recommended.min_trade_notional} {sizing.account_currency} (~{Math.round(sizing.min_notional_dkk).toLocaleString("da-DK")} DKK)</td>
                  <td className="muted">{String(form.min_trade_notional ?? "")}</td></tr>
                <tr><td style={{ textAlign: "left" }}>Max positioner</td>
                  <td>{sizing.recommended.risk_max_open_positions}</td>
                  <td className="muted">{String(form.risk_max_open_positions ?? "")}</td></tr>
                <tr><td style={{ textAlign: "left" }}>Top N (univers)</td>
                  <td>{sizing.recommended.discovery_top_n}</td>
                  <td className="muted">{String(form.discovery_top_n ?? "")}</td></tr>
                <tr><td style={{ textAlign: "left" }}>Max pr. position %</td>
                  <td>{(sizing.recommended.risk_max_position_pct * 100).toFixed(0)}%</td>
                  <td className="muted">{String(form.risk_max_position_pct ?? "")}%</td></tr>
                <tr><td style={{ textAlign: "left" }}>Risiko pr. handel %</td>
                  <td>{(sizing.recommended.risk_max_risk_per_trade_pct * 100).toFixed(2)}%</td>
                  <td className="muted">{String(form.risk_max_risk_per_trade_pct ?? "")}%</td></tr>
              </tbody>
            </table>
            <ul className="muted" style={{ fontSize: 11, margin: "8px 0 0", paddingLeft: 18 }}>
              {sizing.rationale.map((r, i) => <li key={i}>{r}</li>)}
            </ul>
          </div>
        )}
        <div className="grid two-col">
          <div>
            <h3 style={{ fontSize: 13 }}>Position sizing</h3>
            <Field label="Max open positions" hint="Recommended 10.">{num("risk_max_open_positions")}</Field>
            <Field label="Max per position %" hint="Biggest single position as % of capital. Recommended 15.">{num("risk_max_position_pct")}</Field>
            <Field label="Max total exposure %" hint="Ceiling on ALL positions. The regime engine scales this down in volatile/bear markets.">{num("risk_max_total_exposure_pct")}</Field>
            <Field label="Max risk per trade %" hint="Capital risked to the stop per trade. Recommended 0.5.">{num("risk_max_risk_per_trade_pct")}</Field>
          </div>
          <div>
            <h3 style={{ fontSize: 13 }}>Loss halts</h3>
            <Field label="Daily-loss halt %" hint="Stop new trades if the day is down this much.">{num("risk_max_daily_loss_pct")}</Field>
            <Field label="Max drawdown halt %" hint="Stop new trades if down this much from peak.">{num("risk_max_total_drawdown_pct")}</Field>
            <Field label="Enforce loss halts" hint="OFF while testing on SIM (keeps trading through drawdowns so you can observe). MUST be ON before live.">
              <Toggle on={Boolean(form.enforce_loss_halts)} onChange={(v) => set("enforce_loss_halts", v)}
                onLabel="on (live-safe)" offLabel="off (SIM testing)" />
            </Field>
            <Field label="Strategy circuit breaker"
              hint={`Halts automation if realised performance degrades — win rate below ${Math.round((s.circuit_breaker_win_rate ?? 0.35) * 100)}% AND a net loss over ${s.circuit_breaker_min_trades ?? 10}+ closed trades. Stays tripped until you restart Auto Trading manually. OFF during SIM data-gathering; consider ON before live.`}>
              <Toggle on={Boolean(form.circuit_breaker_enabled)} onChange={(v) => set("circuit_breaker_enabled", v)}
                onLabel="on (auto-halts bad strategy)" offLabel="off" />
            </Field>
            <div className="muted" style={{ fontSize: 11 }}>
              Kill switch & emergency stop always work regardless of these settings.
              Drawdown de-risking and regime scaling run automatically on top.
            </div>
          </div>
        </div>
        </>
      )}

      {/* ================= AI ================= */}
      {tab === "ai" && (
        <div className="grid two-col">
          <div>
            <h3 style={{ fontSize: 13 }}>Claude (news analysis — optional)</h3>
            <Field label="Auth mode" hint="off = built-in heuristic (fine for SIM). News is advisory anyway.">
              <select value={String(form.ai_auth_mode)} onChange={(e) => set("ai_auth_mode", e.target.value)}>
                {opt.ai_auth_mode.map((o) => <option key={o}>{o}</option>)}
              </select>
            </Field>
            <Field label="Model"><input type="text" value={String(form.ai_model)} onChange={(e) => set("ai_model", e.target.value)} /></Field>
          </div>
          <div>
            <h3 style={{ fontSize: 13 }}>Credentials</h3>
            <Field label="OAuth token (ANTHROPIC_AUTH_TOKEN)" hint={s.anthropic_auth_token.set ? `saved ${s.anthropic_auth_token.hint} · leave blank to keep` : "get via: ant auth print-credentials --access-token"}>
              <input type="password" placeholder="sk-ant-oat01-…" value={secrets.anthropic_auth_token}
                onChange={(e) => setSecrets((x) => ({ ...x, anthropic_auth_token: e.target.value }))} />
            </Field>
            <Field label="API key (only if auth mode = api_key)" hint={s.anthropic_api_key.set ? `saved ${s.anthropic_api_key.hint}` : ""}>
              <input type="password" placeholder="sk-ant-…" value={secrets.anthropic_api_key}
                onChange={(e) => setSecrets((x) => ({ ...x, anthropic_api_key: e.target.value }))} />
            </Field>
          </div>
        </div>
      )}

      {picks.length > 0 && tab === "data" && (
        <div style={{ marginTop: 12 }}>
          <h3 style={{ fontSize: 13 }}>Screened candidates</h3>
          <div style={{ overflowX: "auto" }}>
            <table>
              <thead><tr><th>Symbol</th><th>Score</th><th>Momentum</th><th>Trend gap</th><th>Rationale</th></tr></thead>
              <tbody>
                {picks.map((c) => (
                  <tr key={c.symbol}>
                    <td>{c.symbol}</td><td>{c.score.toFixed(1)}</td><td>{c.momentum.toFixed(1)}%</td>
                    <td>{(c.trend_gap * 100).toFixed(1)}%</td><td style={{ textAlign: "left", fontSize: 11 }}>{c.rationale}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <div className="btn-row" style={{ marginTop: 16 }}>
        <button disabled={busy} onClick={save}>💾 Save settings</button>
      </div>
      {err && <div className="error">{err}</div>}
    </div>
  );
}
