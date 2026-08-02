import { useEffect, useState } from "react";
import { api } from "../api";
import type { DiscoveryCandidate, OpenOrder, Portfolio, Signal } from "../types";
import { IndicesBar } from "./IndicesBar";
import { TradeFlow } from "./TradeFlow";

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

/** Days/hours until an ISO timestamp, e.g. "om 2 d" / "om 5 t". */
function until(ts?: string | null): string {
  if (!ts) return "";
  const s = (new Date(ts).getTime() - Date.now()) / 1000;
  if (isNaN(s)) return "";
  if (s <= 0) return "udløbet";
  if (s < 3600) return `om ${Math.round(s / 60)} min`;
  if (s < 86400) return `om ${Math.round(s / 3600)} t`;
  return `om ${Math.round(s / 86400)} d`;
}

/** Buy suggestions (suggest mode): approve/reject; armed items await good timing.
 *  Includes the Man/Auto entry-mode toggle. Selling stays automatic in both modes. */
export function SuggestionsView({ onOpen, onToast }: { onOpen?: (s: string) => void; onToast?: (m: string) => void }) {
  const [data, setData] = useState<Awaited<ReturnType<typeof api.suggestions>> | null>(null);
  const [mode, setMode] = useState<"suggest" | "auto" | null>(null);
  const [busy, setBusy] = useState<number | null>(null);
  const [flow, setFlow] = useState<string | null>(null);
  const load = () => api.suggestions().then(setData).catch(() => {});
  useEffect(() => {
    load(); api.entryMode().then((r) => setMode(r.entry_mode)).catch(() => {});
    const id = setInterval(load, 10000); return () => clearInterval(id);
  }, []);
  const act = async (id: number, kind: "approve" | "reject") => {
    setBusy(id);
    try {
      await (kind === "approve" ? api.approveSuggestion(id) : api.rejectSuggestion(id));
      onToast?.(kind === "approve" ? "Godkendt — platformen køber på god timing" : "Forslag afvist");
      load();
    } catch (e) { onToast?.((e as Error).message); } finally { setBusy(null); }
  };
  const switchMode = async (m: "suggest" | "auto") => {
    try { await api.setEntryMode(m); setMode(m); onToast?.(m === "suggest" ? "Man-drift: platformen foreslår køb" : "Auto-drift: platformen køber selv"); }
    catch (e) { onToast?.((e as Error).message); }
  };
  const open = data?.open ?? [];
  const proposed = open.filter((s) => s.status === "proposed");
  const armed = open.filter((s) => s.status === "armed");

  return (
    <>
      <div className="card section-gap">
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
          <div>
            <div style={{ fontSize: 10, textTransform: "uppercase", letterSpacing: ".05em", color: "var(--muted)", marginBottom: 4 }}>Køb-drift</div>
            <div style={{ display: "flex", gap: 4 }}>
              <button onClick={() => switchMode("suggest")} className={mode === "suggest" ? "" : "secondary"} style={{ padding: "5px 12px", fontSize: 12, fontWeight: 700 }}>Man — foreslå</button>
              <button onClick={() => switchMode("auto")} className={mode === "auto" ? "" : "secondary"} style={{ padding: "5px 12px", fontSize: 12, fontWeight: 700 }}>Auto — køb selv</button>
            </div>
          </div>
          <div className="muted" style={{ fontSize: 12, maxWidth: 380, lineHeight: 1.45 }}>
            {mode === "auto"
              ? "Platformen åbner selv positioner. Salg er automatisk."
              : "Platformen foreslår køb; du godkender, og den køber på god timing (ikke overkøbt/udstrakt). Salg er altid automatisk."}
          </div>
        </div>
      </div>

      <Card title={`Foreslåede køb${proposed.length ? ` — ${proposed.length}` : ""}`}>
        {proposed.length === 0 ? <p className="muted">Ingen forslag lige nu{mode === "auto" ? " (Auto-drift køber selv)." : "."}</p> : (
          <table><thead><tr><th>Symbol</th><th>Quant</th><th>News</th><th>Antal</th><th>Pris</th><th>Stop</th><th>Begrundelse</th><th></th></tr></thead>
            <tbody>{proposed.map((s) => (
              <tr key={s.id}>
                <td><Sym s={s.symbol} onOpen={onOpen} /></td>
                <td style={{ color: POS, fontWeight: 600 }}>{n(s.quant_score, 0)}</td>
                <td>{n(s.news_score, 0)}</td><td>{n(s.suggested_quantity, 0)}</td>
                <td>{n(s.reference_price)}</td><td className="muted">{s.stop_price == null ? "—" : n(s.stop_price)}</td>
                <td className="muted" style={{ fontSize: 12, textAlign: "left", maxWidth: 260 }}>{s.rationale}</td>
                <td style={{ whiteSpace: "nowrap" }}>
                  <button className="secondary" onClick={() => setFlow(s.symbol)} style={{ padding: "3px 8px", fontSize: 12 }} title="Se hvordan platformen bedømmer aktien">🔬 Flow</button>{" "}
                  <button disabled={busy === s.id} onClick={() => act(s.id, "approve")} style={{ padding: "3px 10px", fontSize: 12, background: POS, fontWeight: 700 }}>Godkend</button>{" "}
                  <button disabled={busy === s.id} className="secondary" onClick={() => act(s.id, "reject")} style={{ padding: "3px 8px", fontSize: 12 }}>Afvis</button>
                </td>
              </tr>
            ))}</tbody></table>
        )}
      </Card>

      <Card title={`Armeret — afventer god timing${armed.length ? ` — ${armed.length}` : ""}`}>
        {armed.length === 0 ? <p className="muted">Ingen armerede forslag.</p> : (
          <table><thead><tr><th>Symbol</th><th>Antal</th><th>Ref-pris</th><th>Stop</th><th>Udløber</th><th></th></tr></thead>
            <tbody>{armed.map((s) => (
              <tr key={s.id}>
                <td><Sym s={s.symbol} onOpen={onOpen} /></td>
                <td>{n(s.suggested_quantity, 0)}</td><td>{n(s.reference_price)}</td>
                <td className="muted">{s.stop_price == null ? "—" : n(s.stop_price)}</td>
                <td className="muted" style={{ fontSize: 12 }}>⏳ {until(s.expires_at)}</td>
                <td style={{ whiteSpace: "nowrap" }}>
                  <button className="secondary" onClick={() => setFlow(s.symbol)} style={{ padding: "3px 8px", fontSize: 12 }}>🔬 Flow</button>{" "}
                  <button disabled={busy === s.id} className="secondary" onClick={() => act(s.id, "reject")} style={{ padding: "3px 8px", fontSize: 12 }}>Annullér</button>
                </td>
              </tr>
            ))}</tbody></table>
        )}
        <p className="muted" style={{ fontSize: 11, marginTop: 8 }}>
          Godkendte forslag købes automatisk gennem risk-motoren når kursen ikke er overkøbt/udstrakt. Rammes timingen ikke inden udløb, bortfalder forslaget.
        </p>
      </Card>

      {(data?.resolved?.length ?? 0) > 0 && (
        <Card title="Afsluttede forslag">
          <table><thead><tr><th>Symbol</th><th>Status</th><th>Antal</th><th>Fyldt @</th><th>Note</th></tr></thead>
            <tbody>{data!.resolved.map((s) => (
              <tr key={s.id}>
                <td>{s.symbol.split(":")[0]}</td>
                <td><span className={`tag ${s.status === "filled" ? "approved" : "rejected"}`}>{s.status}</span></td>
                <td>{n(s.fill_quantity ?? s.suggested_quantity, 0)}</td>
                <td>{s.fill_price == null ? "—" : n(s.fill_price)}</td>
                <td className="muted" style={{ fontSize: 12, textAlign: "left" }}>{s.note}</td>
              </tr>
            ))}</tbody></table>
        </Card>
      )}

      {flow && <TradeFlow symbol={flow} onClose={() => setFlow(null)} />}
    </>
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

/** Relative "for X min siden" from an ISO/epoch timestamp. */
function ago(ts?: string | null): string {
  if (!ts) return "";
  const d = new Date(ts);
  if (isNaN(d.getTime())) return "";
  const s = (Date.now() - d.getTime()) / 1000;
  if (s < 90) return "nu";
  if (s < 3600) return `${Math.round(s / 60)} min siden`;
  if (s < 86400) return `${Math.round(s / 3600)} t siden`;
  return `${Math.round(s / 86400)} d siden`;
}

/** Good/Bad sentiment badge (0-100) for a headline. */
function SentimentBadge({ s }: { s?: { score: number; label: "good" | "bad" | "neutral" } }) {
  if (!s) return null;
  const map = {
    good: { bg: "rgba(36,192,122,.14)", fg: POS, txt: "Godt" },
    bad: { bg: "rgba(242,84,91,.14)", fg: NEG, txt: "Skidt" },
    neutral: { bg: "var(--panel-2)", fg: "var(--muted)", txt: "Neutral" },
  }[s.label];
  return (
    <span title={`Sentiment ${s.score}/100 (keyword-heuristik)`} style={{
      flexShrink: 0, fontSize: 10, fontWeight: 700, padding: "1px 7px", borderRadius: 4,
      background: map.bg, color: map.fg, fontVariantNumeric: "tabular-nums",
    }}>{s.score.toFixed(0)} · {map.txt}</span>
  );
}

/** News — Yahoo headlines centred on your watchlist, owned tickers first, each
 *  with a Good/Bad sentiment score (0-100). */
export function NewsView({ onOpen }: { onOpen?: (s: string) => void }) {
  const [data, setData] = useState<Awaited<ReturnType<typeof api.marketNews>> | null>(null);
  useEffect(() => {
    let alive = true;
    const load = () => api.marketNews().then((r) => alive && setData(r)).catch(() => {});
    load(); const id = setInterval(load, 60000); return () => { alive = false; clearInterval(id); };
  }, []);
  const items = data?.items ?? [];
  const ownedCount = data?.owned?.length ?? 0;
  return (
    <Card title={`Markeds-nyheder${data?.symbols?.length ? ` — ${data.symbols.length} tickere` : ""}`}>
      {ownedCount > 0 && (
        <p className="muted" style={{ fontSize: 11, marginTop: -4, marginBottom: 8 }}>
          💼 Dine {ownedCount} beholdninger vises først · sentiment 0–100 pr. overskrift (keyword-heuristik)
        </p>
      )}
      {!data ? <p className="muted">indlæser…</p>
        : items.length === 0 ? (
          <p className="muted" style={{ fontSize: 13 }}>
            {data.reason === "news disabled or offline (synthetic)"
              ? "Nyheder er slået fra (synthetic/offline datakilde). Sæt markeds-datakilden til yfinance/Saxo i Settings."
              : data.reason === "no positions or universe symbols yet"
                ? "Ingen tickere at hente nyheder for endnu — åbn en position eller sæt et universe under Auto Trading."
                : "Ingen nyheder fundet lige nu."}
          </p>
        ) : (
          <div style={{ display: "flex", flexDirection: "column" }}>
            {items.map((it, i) => (
              <div key={i} style={{ padding: "9px 0", borderTop: i ? "1px solid var(--border)" : "none",
                borderLeft: it.owned ? `2px solid ${POS}` : "2px solid transparent", paddingLeft: it.owned ? 8 : 0 }}>
                <div style={{ display: "flex", alignItems: "baseline", gap: 8, flexWrap: "wrap" }}>
                  <button className="secondary" style={{ padding: "1px 7px", fontSize: 11, fontWeight: 700, flexShrink: 0 }}
                    onClick={() => onOpen?.(it.symbol)}>{it.owned ? "💼 " : ""}{it.symbol}</button>
                  <SentimentBadge s={it.sentiment} />
                  {it.url ? (
                    <a href={it.url} target="_blank" rel="noopener noreferrer"
                      style={{ fontSize: 13, fontWeight: 600, color: "var(--text)", textDecoration: "none", lineHeight: 1.35 }}>
                      {it.title}
                    </a>
                  ) : (
                    <span style={{ fontSize: 13, fontWeight: 600, lineHeight: 1.35 }}>{it.title}</span>
                  )}
                </div>
                <div className="muted" style={{ fontSize: 11, marginTop: 3, marginLeft: 34 }}>
                  {it.publisher || "—"}{it.published ? ` · ${ago(it.published)}` : ""}{it.url ? " · åbner i ny fane ↗" : ""}
                </div>
              </div>
            ))}
          </div>
        )}
    </Card>
  );
}
