import { useEffect, useState } from "react";
import { api } from "../api";
import type { Attribution, ComparisonRow } from "../types";

const money = (n: number) => n.toLocaleString(undefined, { maximumFractionDigits: 0 });
type TradeRow = Awaited<ReturnType<typeof api.tradeLog>>[number];
type Analysis = Awaited<ReturnType<typeof api.analyzeStock>>;
const clock = (iso: string) => new Date(iso).toLocaleString("da-DK", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" });
const tone = (n: number | undefined): "pos" | "neg" | undefined => n == null ? undefined : n >= 0 ? "pos" : "neg";

export function Analytics({ universe }: { universe: string[] }) {
  const [attr, setAttr] = useState<Attribution | null>(null);
  const [trades, setTrades] = useState<TradeRow[]>([]);
  const [symbol, setSymbol] = useState(universe[0] ?? "NOVO");
  const [rows, setRows] = useState<ComparisonRow[]>([]);
  const [busy, setBusy] = useState(false);
  const [ticker, setTicker] = useState("");
  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [analyzing, setAnalyzing] = useState(false);

  const runAnalyze = async () => {
    if (!ticker.trim()) return;
    setAnalyzing(true); setAnalysis(null);
    try { setAnalysis(await api.analyzeStock(ticker.trim().toUpperCase())); }
    catch (e) { setAnalysis({ symbol: ticker.toUpperCase(), error: (e as Error).message }); }
    finally { setAnalyzing(false); }
  };

  useEffect(() => {
    const load = () => {
      api.attribution().then(setAttr).catch(() => setAttr(null));
      api.tradeLog().then(setTrades).catch(() => setTrades([]));
    };
    load();
    const id = setInterval(load, 10000);  // keep Analytics live, like the dashboard
    return () => clearInterval(id);
  }, []);

  const runCompare = async () => {
    setBusy(true);
    try {
      const res = await api.compare(symbol);
      setRows(res.results);
    } finally {
      setBusy(false);
    }
  };

  const a = analysis;
  return (
    <>
    <div className="card" style={{ marginBottom: 12 }}>
      <h2>🔎 Analyser en aktie</h2>
      <p className="muted" style={{ fontSize: 12, marginTop: -4 }}>
        Skriv et ticker (fx AAPL, UNH, BBAI) → teknisk billede + platformens score + hvad hver strategi signalerer. Read-only.
      </p>
      <div className="btn-row" style={{ marginBottom: 8 }}>
        <input type="text" value={ticker} placeholder="ticker…" style={{ maxWidth: 140, textTransform: "uppercase" }}
          onChange={(e) => setTicker(e.target.value)} onKeyDown={(e) => e.key === "Enter" && runAnalyze()} />
        <button disabled={analyzing} onClick={runAnalyze}>{analyzing ? "Analyserer…" : "Analysér"}</button>
      </div>
      {a && a.error && <div className="error">{a.symbol}: {a.error}</div>}
      {a && !a.error && (
        <div className="grid two-col" style={{ gap: 16 }}>
          <div>
            <table style={{ fontSize: 13 }}>
              <tbody>
                <tr><td style={{ textAlign: "left" }}>Kurs</td><td><strong>{a.price?.toFixed(2)}</strong></td></tr>
                <tr><td style={{ textAlign: "left" }}>52-ugers</td><td>{a.week52_low?.toFixed(2)} – {a.week52_high?.toFixed(2)}</td></tr>
                <tr><td style={{ textAlign: "left" }}>Fra top / bund</td><td><span className={tone(a.from_high_pct)}>{a.from_high_pct}%</span> / <span className={tone(a.from_low_pct)}>+{a.from_low_pct}%</span></td></tr>
                <tr><td style={{ textAlign: "left" }}>vs SMA20/50/200</td><td>
                  <span className={tone(a.sma20_pct)}>{a.sma20_pct}%</span> / <span className={tone(a.sma50_pct)}>{a.sma50_pct}%</span> / <span className={tone(a.sma200_pct)}>{a.sma200_pct}%</span></td></tr>
                <tr><td style={{ textAlign: "left" }}>12-1 momentum</td><td className={tone(a.mom_12_1_pct)}>{a.mom_12_1_pct}%</td></tr>
                <tr><td style={{ textAlign: "left" }}>RSI 14 / 2</td><td>{a.rsi14} / {a.rsi2}</td></tr>
                <tr><td style={{ textAlign: "left" }}>Volatilitet</td><td>{a.ann_vol_pct}%</td></tr>
                {a.next_earnings && <tr><td style={{ textAlign: "left" }}>Næste earnings</td><td>{a.next_earnings}</td></tr>}
              </tbody>
            </table>
          </div>
          <div>
            <div style={{ marginBottom: 8 }}>
              <span style={{ fontSize: 13 }}>Multi-faktor-score: </span>
              <strong style={{ fontSize: 18 }} className={(a.factor_score ?? 0) >= (a.buy_gate ?? 65) ? "pos" : (a.factor_score ?? 0) < 50 ? "neg" : ""}>{a.factor_score}</strong>
              <span className="muted" style={{ fontSize: 12 }}> / 100 (køb over {a.buy_gate})</span>
            </div>
            <div style={{ fontSize: 13, marginBottom: 4 }}>Strategier ({a.long_count}/6 siger køb):</div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
              {a.signals?.map((s) => (
                <span key={s.strategy} className={`tag ${s.long ? "approved" : "rejected"}`} style={{ fontSize: 11 }}>
                  {s.strategy}: {s.long ? "køb" : "ude"}
                </span>
              ))}
            </div>
            <div className="muted" style={{ fontSize: 11, marginTop: 10 }}>
              ⚠️ Mekanisk output — ikke rådgivning. Fundamentale forhold afgør resten.
            </div>
          </div>
        </div>
      )}
    </div>
    <div className="card" style={{ marginBottom: 12 }}>
      <h2>Trade log</h2>
      <p className="muted" style={{ fontSize: 12, marginTop: -4 }}>
        Every executed buy and sell, newest first — price, value and the reason. Auto-refreshes every 10s.
      </p>
      {trades.length > 0 ? (
        <div style={{ overflowX: "auto", maxHeight: 420, overflowY: "auto" }}>
          <table>
            <thead><tr><th>Time</th><th>Symbol</th><th>Side</th><th>Qty</th><th>Price</th><th>Value</th><th>Reason</th></tr></thead>
            <tbody>
              {trades.map((t, i) => (
                <tr key={i}>
                  <td className="muted" style={{ fontSize: 12, whiteSpace: "nowrap" }}>{clock(t.ts)}</td>
                  <td>{t.symbol}</td>
                  <td><span className={`tag ${t.side === "BUY" ? "approved" : "rejected"}`}>{t.side}</span></td>
                  <td>{t.quantity}</td>
                  <td>{t.price.toFixed(2)}</td>
                  <td>{money(t.value)}</td>
                  <td style={{ fontSize: 12 }}>{t.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : <p className="muted">No trades yet — they appear here as soon as the platform buys or sells.</p>}
    </div>
    <div className="grid two-col">
      <div className="card">
        <h2>Performance attribution</h2>
        {attr && attr.per_symbol.length > 0 ? (
          <>
            <table>
              <thead>
                <tr><th>Symbol</th><th>Realised</th><th>Unrealised</th><th>Total</th><th>Trades</th><th>Win %</th></tr>
              </thead>
              <tbody>
                {attr.per_symbol.map((r) => (
                  <tr key={r.symbol}>
                    <td>{r.symbol}</td>
                    <td className={r.realized_pnl >= 0 ? "pos" : "neg"}>{r.realized_pnl.toFixed(0)}</td>
                    <td className={r.unrealized_pnl >= 0 ? "pos" : "neg"}>{r.unrealized_pnl.toFixed(0)}</td>
                    <td className={r.total_pnl >= 0 ? "pos" : "neg"}>{r.total_pnl.toFixed(0)}</td>
                    <td>{r.closed_trades}</td>
                    <td>{r.win_rate.toFixed(0)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="muted" style={{ fontSize: 12, marginTop: 8 }}>
              Total P/L {money(attr.total_pnl)} (realised {money(attr.total_realized)}, unrealised {money(attr.total_unrealized)}) · commission {money(attr.total_commission)}
            </div>
          </>
        ) : (
          <p className="muted">No fills yet — run cycles to build P&amp;L history.</p>
        )}
      </div>

      <div className="card">
        <h2>Strategy comparison</h2>
        <div className="btn-row" style={{ marginBottom: 10 }}>
          <select value={symbol} onChange={(e) => setSymbol(e.target.value)}>
            {universe.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
          <button disabled={busy} onClick={runCompare}>{busy ? "Backtesting…" : "Compare"}</button>
        </div>
        {rows.length > 0 ? (
          <table>
            <thead>
              <tr><th>Strategy</th><th>Return %</th><th>Sharpe</th><th>Max DD %</th><th>Trades</th></tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.strategy}>
                  <td>{r.strategy}</td>
                  <td className={r.total_return_pct >= 0 ? "pos" : "neg"}>{r.total_return_pct.toFixed(1)}</td>
                  <td>{r.sharpe.toFixed(2)}</td>
                  <td className="neg">{r.max_drawdown_pct.toFixed(1)}</td>
                  <td>{r.num_trades}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="muted">Pick a symbol and compare strategies by Sharpe.</p>
        )}
      </div>
    </div>
    </>
  );
}
