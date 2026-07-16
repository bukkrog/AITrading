import { useEffect, useState } from "react";
import { api } from "../api";
import type { DiscoveryCandidate, SettingsView } from "../types";

function Field({ label, children, hint }: { label: string; children: React.ReactNode; hint?: string }) {
  return (
    <div style={{ marginBottom: 12 }}>
      <label className="field" style={{ display: "block", marginBottom: 4 }}>{label}</label>
      {children}
      {hint && <div className="muted" style={{ fontSize: 11, marginTop: 3 }}>{hint}</div>}
    </div>
  );
}

export function SettingsMenu({ onChanged, onToast }: { onChanged: () => void; onToast: (m: string) => void }) {
  const [s, setS] = useState<SettingsView | null>(null);
  const [form, setForm] = useState<Record<string, unknown>>({});
  const [secrets, setSecrets] = useState({ anthropic_auth_token: "", anthropic_api_key: "", saxo_access_token: "", saxo_app_secret: "" });
  const [allocation, setAllocationAmt] = useState("100000");
  const [picks, setPicks] = useState<DiscoveryCandidate[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [saxoResult, setSaxoResult] = useState<string | null>(null);
  const [testSymbol, setTestSymbol] = useState("AAPL");
  const [selfTest, setSelfTest] = useState<{ ok: boolean; placed_order: boolean; steps: { name: string; ok: boolean; detail?: unknown; error?: string }[] } | null>(null);

  const load = () =>
    api.getSettings().then((v) => {
      setS(v);
      setForm({
        ai_auth_mode: v.ai_auth_mode,
        ai_model: v.ai_model,
        saxo_environment: v.saxo_environment,
        saxo_app_key: v.saxo_app_key,
        saxo_auth_endpoint: v.saxo_auth_endpoint,
        saxo_token_endpoint: v.saxo_token_endpoint,
        default_broker_mode: v.default_broker_mode,
        live_trading_enabled: v.live_trading_enabled,
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
        automation_interval_seconds: v.automation_interval_seconds,
        automation_universe: v.automation_universe,
      });
    });

  useEffect(() => { load(); }, []);

  const set = (k: string, v: unknown) => setForm((f) => ({ ...f, [k]: v }));

  async function guard(fn: () => Promise<void>, msg?: string) {
    setBusy(true); setErr(null);
    try { await fn(); if (msg) onToast(msg); onChanged(); }
    catch (e) { setErr((e as Error).message); }
    finally { setBusy(false); }
  }

  const save = () =>
    guard(async () => {
      const payload: Record<string, unknown> = { ...form };
      for (const [k, v] of Object.entries(secrets)) if (v.trim()) payload[k] = v.trim();
      await api.updateSettings(payload);
      setSecrets({ anthropic_auth_token: "", anthropic_api_key: "", saxo_access_token: "", saxo_app_secret: "" });
      await load();
    }, "Settings saved (runtime)");

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

  const screen = () => guard(async () => { setPicks((await api.discovery(Number(form.discovery_top_n) || 6)).candidates); });
  const applyDisc = () => guard(async () => { const r = await api.applyDiscovery(Number(form.discovery_top_n) || 6); onToast(`Universe set: ${r.universe.join(", ")}`); await load(); });
  const setAlloc = () => guard(async () => { await api.setAllocation(Number(allocation)); }, `Trading capital set to ${allocation}`);

  if (!s) return <div className="card"><h2>Setup</h2><p className="muted">Loading…</p></div>;
  const opt = s.options;

  return (
    <div className="card">
      <h2>⚙ Setup &amp; settings</h2>
      <div className="muted" style={{ fontSize: 12, marginBottom: 12 }}>{s.persistence}</div>

      <div className="grid three-col">
        {/* --- AI / Claude --- */}
        <div>
          <h3 style={{ fontSize: 13 }}>Claude AI</h3>
          <Field label="Auth mode">
            <select value={String(form.ai_auth_mode)} onChange={(e) => set("ai_auth_mode", e.target.value)}>
              {opt.ai_auth_mode.map((o) => <option key={o}>{o}</option>)}
            </select>
          </Field>
          <Field label="OAuth token (ANTHROPIC_AUTH_TOKEN)" hint={s.anthropic_auth_token.set ? `saved ${s.anthropic_auth_token.hint} · leave blank to keep` : "get via: ant auth print-credentials --access-token"}>
            <input type="password" placeholder="sk-ant-oat01-…" value={secrets.anthropic_auth_token}
              onChange={(e) => setSecrets((x) => ({ ...x, anthropic_auth_token: e.target.value }))} />
          </Field>
          <Field label="API key (only if auth mode = api_key)" hint={s.anthropic_api_key.set ? `saved ${s.anthropic_api_key.hint}` : ""}>
            <input type="password" placeholder="sk-ant-…" value={secrets.anthropic_api_key}
              onChange={(e) => setSecrets((x) => ({ ...x, anthropic_api_key: e.target.value }))} />
          </Field>
          <Field label="Model"><input type="text" value={String(form.ai_model)} onChange={(e) => set("ai_model", e.target.value)} /></Field>
        </div>

        {/* --- Saxo / broker / capital --- */}
        <div>
          <h3 style={{ fontSize: 13 }}>Broker &amp; capital</h3>
          <Field label="Broker">
            <select value={String(form.default_broker_mode)} onChange={(e) => set("default_broker_mode", e.target.value)}>
              {opt.default_broker_mode.map((o) => <option key={o}>{o}</option>)}
            </select>
          </Field>
          <Field label="Saxo environment" hint="sim = simulated money (safe). live is paused until enabled below.">
            <select value={String(form.saxo_environment)} onChange={(e) => set("saxo_environment", e.target.value)}>
              {opt.saxo_environment.map((o) => <option key={o}>{o}</option>)}
            </select>
          </Field>
          <Field label="Saxo access token (24h SIM token)" hint={s.saxo_access_token.set ? `saved ${s.saxo_access_token.hint} · leave blank to keep` : "paste your Saxo 24h token"}>
            <input type="password" placeholder="Saxo bearer token" value={secrets.saxo_access_token}
              onChange={(e) => setSecrets((x) => ({ ...x, saxo_access_token: e.target.value }))} />
          </Field>
          <Field label="App key" hint="from your Saxo app registration">
            <input type="text" value={String(form.saxo_app_key ?? "")} onChange={(e) => set("saxo_app_key", e.target.value)} />
          </Field>
          <Field label="App secret" hint={s.saxo_app_secret.set ? `saved ${s.saxo_app_secret.hint}` : "for the OAuth code flow (later)"}>
            <input type="password" placeholder="app secret" value={secrets.saxo_app_secret}
              onChange={(e) => setSecrets((x) => ({ ...x, saxo_app_secret: e.target.value }))} />
          </Field>
          <Field label="Authorization endpoint">
            <input type="text" value={String(form.saxo_auth_endpoint ?? "")} onChange={(e) => set("saxo_auth_endpoint", e.target.value)} />
          </Field>
          <Field label="Token endpoint">
            <input type="text" value={String(form.saxo_token_endpoint ?? "")} onChange={(e) => set("saxo_token_endpoint", e.target.value)} />
          </Field>
          <div className="btn-row">
            <button className="secondary" disabled={busy} onClick={save}>Save first</button>
            <button className="secondary" disabled={busy} onClick={testSaxo}>Test Saxo connection</button>
          </div>
          {saxoResult && <div className="muted" style={{ fontSize: 12, marginTop: 6 }}>{saxoResult}</div>}

          <Field label="Verify full trading path" hint="account → balance → instrument → quote → daily bars (read-only). The order button places a real 1-share SIM buy.">
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
          <Field label="Live trading" hint="Stays PAUSED until you connect Saxo and enable this.">
            <label style={{ display: "flex", gap: 8, alignItems: "center" }}>
              <input type="checkbox" checked={Boolean(form.live_trading_enabled)} onChange={(e) => set("live_trading_enabled", e.target.checked)} />
              <span>{form.live_trading_enabled ? "ENABLED" : "disabled (paused)"}</span>
            </label>
          </Field>
          <Field label="Trading capital (allocation)" hint="Amount the platform trades with. Resets positions.">
            <div className="btn-row">
              <input type="text" value={allocation} onChange={(e) => setAllocationAmt(e.target.value)} style={{ maxWidth: 140 }} />
              <button className="secondary" disabled={busy} onClick={setAlloc}>Set</button>
            </div>
          </Field>
        </div>

        {/* --- Data & discovery --- */}
        <div>
          <h3 style={{ fontSize: 13 }}>Data &amp; discovery</h3>
          <Field label="Market data source" hint="yfinance = real Yahoo prices + news">
            <select value={String(form.market_data_source)} onChange={(e) => set("market_data_source", e.target.value)}>
              {opt.market_data_source.map((o) => <option key={o}>{o}</option>)}
            </select>
          </Field>
          <Field label="Use real news">
            <label style={{ display: "flex", gap: 8, alignItems: "center" }}>
              <input type="checkbox" checked={Boolean(form.news_enabled)} onChange={(e) => set("news_enabled", e.target.checked)} />
              <span>{form.news_enabled ? "on" : "off"}</span>
            </label>
          </Field>
          <Field label="Auto-discover (screener picks the universe)">
            <label style={{ display: "flex", gap: 8, alignItems: "center" }}>
              <input type="checkbox" checked={Boolean(form.discovery_enabled)} onChange={(e) => set("discovery_enabled", e.target.checked)} />
              <span>{form.discovery_enabled ? "on" : "off"}</span>
            </label>
          </Field>
          <Field label="Momentum sources (scan the market)" hint="Selected sources are gathered, ranked by momentum, and the top N are traded. Overrides the static pool below.">
            <div style={{ display: "flex", flexWrap: "wrap", gap: 10 }}>
              {(opt.discovery_sources ?? []).map((src) => {
                const cur = String(form.discovery_sources || "").split(",").map((x) => x.trim()).filter(Boolean);
                const on = cur.includes(src);
                const label = { day_gainers: "Top gainers", most_actives: "Most active", wsb: "WallStreetBets", sp500: "S&P 500", dow30: "Dow 30" }[src] ?? src;
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
          <Field label="Top N"><input type="text" value={String(form.discovery_top_n)} onChange={(e) => set("discovery_top_n", Number(e.target.value) || 6)} style={{ maxWidth: 80 }} /></Field>
          <Field label="Max pool (before ranking)" hint="Cap on gathered tickers; momentum sources prioritised."><input type="text" value={String(form.discovery_max_pool)} onChange={(e) => set("discovery_max_pool", Number(e.target.value) || 100)} style={{ maxWidth: 80 }} /></Field>
          <Field label="Static candidate pool (used only if no source above)">
            <textarea value={String(form.discovery_candidates)} onChange={(e) => set("discovery_candidates", e.target.value)}
              rows={3} style={{ width: "100%", background: "var(--panel-2)", color: "var(--text)", border: "1px solid var(--border)", borderRadius: 8, padding: 8, fontSize: 12 }} />
          </Field>
          <div className="btn-row">
            <button className="secondary" disabled={busy} onClick={screen}>Screen now</button>
            <button disabled={busy} onClick={applyDisc}>Apply to universe</button>
          </div>
        </div>

        {/* --- Trading cadence & costs --- */}
        <div>
          <h3 style={{ fontSize: 13 }}>Cadence &amp; costs</h3>
          <Field label="Bar timeframe" hint="Shorter = trades more often (intraday). Daily = calm.">
            <select value={String(form.market_horizon_minutes)} onChange={(e) => set("market_horizon_minutes", Number(e.target.value))}>
              <option value={1440}>Daily</option>
              <option value={60}>1 hour</option>
              <option value={30}>30 min</option>
              <option value={15}>15 min</option>
              <option value={5}>5 min</option>
            </select>
          </Field>
          <Field label="Commission per trade (fixed)" hint="Real Saxo kurtage-like cost, applied in paper P&L.">
            <input type="text" value={String(form.commission_per_trade)} onChange={(e) => set("commission_per_trade", Number(e.target.value) || 0)} style={{ maxWidth: 90 }} />
          </Field>
          <Field label="Commission % (fraction)" hint="e.g. 0.0008 = 8 bps of notional">
            <input type="text" value={String(form.commission_pct)} onChange={(e) => set("commission_pct", Number(e.target.value) || 0)} style={{ maxWidth: 90 }} />
          </Field>
          <Field label="Slippage (bps)">
            <input type="text" value={String(form.slippage_bps)} onChange={(e) => set("slippage_bps", Number(e.target.value) || 0)} style={{ maxWidth: 90 }} />
          </Field>
          <Field label="Trade cooldown (min)" hint="Min minutes between trades on the same symbol. Raise to curb commission churn.">
            <input type="text" value={String(form.trade_cooldown_minutes)} onChange={(e) => set("trade_cooldown_minutes", Number(e.target.value) || 0)} style={{ maxWidth: 90 }} />
          </Field>
          <Field label="Min trade notional" hint="Skip trades smaller than this (avoids tiny, commission-heavy orders). 0 = off.">
            <input type="text" value={String(form.min_trade_notional)} onChange={(e) => set("min_trade_notional", Number(e.target.value) || 0)} style={{ maxWidth: 110 }} />
          </Field>
        </div>
      </div>

      {picks.length > 0 && (
        <div style={{ marginTop: 12 }}>
          <h3 style={{ fontSize: 13 }}>Screened candidates</h3>
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
      )}

      <div className="btn-row" style={{ marginTop: 16 }}>
        <button disabled={busy} onClick={save}>Save settings</button>
      </div>
      {err && <div className="error">{err}</div>}
    </div>
  );
}
