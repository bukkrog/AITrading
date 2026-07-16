import { useEffect, useState } from "react";
import { api } from "../api";
import type { Attribution, ComparisonRow } from "../types";

const money = (n: number) => n.toLocaleString(undefined, { maximumFractionDigits: 0 });

export function Analytics({ universe }: { universe: string[] }) {
  const [attr, setAttr] = useState<Attribution | null>(null);
  const [symbol, setSymbol] = useState(universe[0] ?? "NOVO");
  const [rows, setRows] = useState<ComparisonRow[]>([]);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.attribution().then(setAttr).catch(() => setAttr(null));
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

  return (
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
  );
}
