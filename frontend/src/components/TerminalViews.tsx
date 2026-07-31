import { useEffect, useState } from "react";
import { api } from "../api";
import type { DiscoveryCandidate, OpenOrder, Portfolio, Signal } from "../types";
import { IndicesBar } from "./IndicesBar";

const POS = "var(--pos, #24c07a)";
const NEG = "var(--neg, #f2545b)";
const n = (v?: number | null, d = 2) => (v == null ? "—" : v.toLocaleString("da-DK", { maximumFractionDigits: d }));
const Sym = ({ s, onOpen }: { s: string; onOpen?: (s: string) => void }) =>
  onOpen ? <button className="secondary" style={{ padding: "1px 7px", fontSize: 12, fontWeight: 700 }} onClick={() => onOpen(s.split(":")[0])}>{s.split(":")[0]}</button> : <>{s.split(":")[0]}</>;

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return <div className="card section-gap"><h2>{title}</h2>{children}</div>;
}

/** Markets — headline indices + the live discovery screener (clickable). */
export function MarketsView({ onOpen }: { onOpen?: (s: string) => void }) {
  const [c, setC] = useState<DiscoveryCandidate[] | null>(null);
  useEffect(() => {
    let alive = true;
    const load = () => api.discovery(30).then((r) => alive && setC(r.candidates)).catch(() => {});
    load(); const id = setInterval(load, 20000); return () => { alive = false; clearInterval(id); };
  }, []);
  return (
    <>
      <IndicesBar />
      <Card title="Markeds-screener (momentum)">
        {!c ? <p className="muted">indlæser…</p> : c.length === 0 ? <p className="muted">Scanner markedet…</p> : (
          <table><thead><tr><th>Symbol</th><th>Score</th><th>Momentum</th><th>Trend</th></tr></thead>
            <tbody>{c.map((x) => (
              <tr key={x.symbol}><td><Sym s={x.symbol} onOpen={onOpen} /></td>
                <td style={{ color: x.score >= 65 ? POS : "var(--muted)", fontWeight: 600 }}>{n(x.score, 1)}</td>
                <td>{n(x.momentum, 1)}%</td><td>{n((x.trend_gap ?? 0) * 100, 1)}%</td></tr>
            ))}</tbody></table>
        )}
      </Card>
    </>
  );
}

/** Portfolio — open positions (clickable to analyse/trade). */
export function PortfolioView({ onOpen }: { onOpen?: (s: string) => void }) {
  const [pf, setPf] = useState<Portfolio | null>(null);
  useEffect(() => {
    let alive = true;
    const load = () => api.portfolio().then((r) => alive && setPf(r)).catch(() => {});
    load(); const id = setInterval(load, 10000); return () => { alive = false; clearInterval(id); };
  }, []);
  const ps = pf?.positions ?? [];
  return (
    <Card title={`Portefølje — ${ps.length} positioner`}>
      {ps.length === 0 ? <p className="muted">Ingen åbne positioner.</p> : (
        <table><thead><tr><th>Symbol</th><th>Antal</th><th>Avg</th><th>Sidst</th><th>Værdi</th><th>Urealiseret</th><th>P/L %</th></tr></thead>
          <tbody>{ps.map((p) => (
            <tr key={p.symbol}><td><Sym s={p.symbol} onOpen={onOpen} /></td><td>{p.quantity}</td>
              <td>{n(p.avg_price)}</td><td>{n(p.last_price)}</td><td>{n(p.market_value, 0)}</td>
              <td style={{ color: p.unrealized_pnl >= 0 ? POS : NEG }}>{n(p.unrealized_pnl)}</td>
              <td style={{ color: (p.pnl_pct ?? 0) >= 0 ? POS : NEG }}>{(p.pnl_pct ?? 0) >= 0 ? "+" : ""}{n(p.pnl_pct, 2)}%</td></tr>
          ))}</tbody></table>
      )}
    </Card>
  );
}

/** Orders — working/pending broker orders (with cancel). */
export function OrdersView({ onToast }: { onToast?: (m: string) => void }) {
  const [orders, setOrders] = useState<OpenOrder[]>([]);
  const load = () => api.portfolio().then((r) => setOrders(r.open_orders ?? [])).catch(() => {});
  useEffect(() => { load(); const id = setInterval(load, 10000); return () => clearInterval(id); }, []);
  const cancel = async (id: string) => { try { await api.cancelSaxoOrder(id); onToast?.("Ordre annulleret"); load(); } catch (e) { onToast?.((e as Error).message); } };
  return (
    <Card title={`Åbne / afventende ordrer — ${orders.length}`}>
      {orders.length === 0 ? <p className="muted">Ingen åbne ordrer.</p> : (
        <table><thead><tr><th>Symbol</th><th>Side</th><th>Antal</th><th>Type</th><th>Pris</th><th>Status</th><th></th></tr></thead>
          <tbody>{orders.map((o) => (
            <tr key={o.order_id}><td>{o.symbol.split(":")[0]}</td>
              <td style={{ color: o.side === "Buy" ? POS : NEG }}>{o.side}</td><td>{o.quantity}</td>
              <td>{o.order_type}</td><td>{o.price == null ? "Market" : n(o.price)}</td><td className="muted">{o.status}</td>
              <td><button className="danger" style={{ padding: "2px 8px", fontSize: 12 }} onClick={() => cancel(o.order_id)}>Annullér</button></td></tr>
          ))}</tbody></table>
      )}
    </Card>
  );
}

/** History — executed buy/sell ledger. */
export function HistoryView() {
  const [rows, setRows] = useState<Awaited<ReturnType<typeof api.tradeLog>>>([]);
  useEffect(() => { let alive = true; const load = () => api.tradeLog(120).then((r) => alive && setRows(r)).catch(() => {}); load(); const id = setInterval(load, 15000); return () => { alive = false; clearInterval(id); }; }, []);
  return (
    <Card title="Handels-historik">
      {rows.length === 0 ? <p className="muted">Ingen handler endnu.</p> : (
        <table><thead><tr><th>Tid</th><th>Symbol</th><th>Side</th><th>Antal</th><th>Pris</th><th>Værdi</th><th>Årsag</th></tr></thead>
          <tbody>{rows.map((r, i) => (
            <tr key={i}><td className="muted" style={{ fontSize: 12 }}>{String(r.ts).replace("T", " ").slice(5, 16)}</td>
              <td>{r.symbol}</td><td style={{ color: r.side === "BUY" ? POS : NEG }}>{r.side}</td>
              <td>{r.quantity}</td><td>{n(r.price)}</td><td>{n(r.value, 0)}</td>
              <td className="muted" style={{ fontSize: 12, textAlign: "left" }}>{r.reason}</td></tr>
          ))}</tbody></table>
      )}
    </Card>
  );
}

/** AI Signals — the engine's latest decisions. */
export function SignalsView({ onOpen }: { onOpen?: (s: string) => void }) {
  const [sig, setSig] = useState<Signal[]>([]);
  useEffect(() => { let alive = true; const load = () => api.signals().then((r) => alive && setSig(r)).catch(() => {}); load(); const id = setInterval(load, 12000); return () => { alive = false; clearInterval(id); }; }, []);
  return (
    <Card title="AI-signaler (seneste beslutninger)">
      {sig.length === 0 ? <p className="muted">Ingen signaler endnu.</p> : (
        <table><thead><tr><th>Symbol</th><th>Beslutning</th><th>Quant</th><th>News</th><th>Risk</th><th>Årsag</th></tr></thead>
          <tbody>{sig.map((s) => (
            <tr key={s.id}><td><Sym s={s.symbol} onOpen={onOpen} /></td>
              <td><span className={`tag ${s.decision === "approved" ? "approved" : "rejected"}`}>{s.decision}</span></td>
              <td>{n(s.quant_score, 0)}</td><td>{n(s.news_score, 0)}</td><td>{n(s.risk_score, 0)}</td>
              <td className="muted" style={{ fontSize: 12, textAlign: "left" }}>{s.reject_reason || s.quant_rationale}</td></tr>
          ))}</tbody></table>
      )}
    </Card>
  );
}

/** News — placeholder until a dedicated news feed endpoint exists. */
export function NewsView() {
  return (
    <Card title="Markeds-nyheder">
      <p className="muted" style={{ fontSize: 13 }}>
        Nyheds-feed kommer — platformen scorer allerede overskrifter pr. aktie (news-agenten),
        men et samlet nyheds-endpoint mangler. Søg en aktie i top-bar'en for dens analyse i mellemtiden.
      </p>
    </Card>
  );
}
