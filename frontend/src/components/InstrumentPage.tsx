import { useEffect, useState } from "react";
import { api } from "../api";
import { CandleChart } from "./CandleChart";
import { PortfolioView, OrdersView, HistoryView, SignalsView } from "./TerminalViews";

type Analysis = Awaited<ReturnType<typeof api.analyzeStock>>;

const POS = "var(--pos, #24c07a)";
const NEG = "var(--neg, #f2545b)";
const num = (n?: number | null, d = 2) => (n == null ? "—" : n.toLocaleString("da-DK", { maximumFractionDigits: d }));
const pct = (n?: number | null) => (n == null ? "—" : `${n >= 0 ? "+" : ""}${n.toFixed(1)}%`);
const toneOf = (n?: number | null) => (n == null ? undefined : n >= 0 ? POS : NEG);

function Stat({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div style={{ background: "var(--panel-2)", border: "1px solid var(--border)", borderRadius: 6, padding: "8px 10px" }}>
      <div style={{ fontSize: 10, textTransform: "uppercase", letterSpacing: ".05em", color: "var(--muted)" }}>{label}</div>
      <div style={{ fontSize: 16, fontWeight: 700, fontVariantNumeric: "tabular-nums", color: tone ?? "var(--text)" }}>{value}</div>
    </div>
  );
}

/** Level-1 market depth (top of book) — real from Saxo when connected, else indicative. */
function DepthPanel({ symbol }: { symbol: string }) {
  const [q, setQ] = useState<Awaited<ReturnType<typeof api.marketQuote>> | null>(null);
  useEffect(() => {
    let alive = true;
    const load = () => api.marketQuote(symbol).then((r) => alive && setQ(r)).catch(() => {});
    load(); const id = setInterval(load, 5000); return () => { alive = false; clearInterval(id); };
  }, [symbol]);
  const spreadPct = q?.spread && q?.mid ? (q.spread / q.mid) * 100 : null;
  const Row = ({ label, price, size, tone }: { label: string; price?: number | null; size?: number | null; tone: string }) => (
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "5px 8px", background: "var(--panel-2)", borderRadius: 5 }}>
      <span style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: ".04em", color: "var(--muted)" }}>{label}</span>
      <span style={{ fontVariantNumeric: "tabular-nums", fontWeight: 700, color: tone }}>{num(price)}</span>
      <span className="muted" style={{ fontSize: 11, fontVariantNumeric: "tabular-nums", minWidth: 44, textAlign: "right" }}>{size == null ? "" : num(size, 0)}</span>
    </div>
  );
  return (
    <div style={{ borderTop: "1px solid var(--border)", paddingTop: 10, marginBottom: 12 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 6 }}>
        <span className="muted" style={{ fontSize: 10, textTransform: "uppercase", letterSpacing: ".05em" }}>Markedsdybde (L1)</span>
        {q && <span className="muted" style={{ fontSize: 10 }}>{q.source === "saxo" ? "● Saxo live" : q.source === "indicative" ? "○ indikativ" : "—"}</span>}
      </div>
      <div style={{ display: "grid", gap: 4 }}>
        <Row label="Ask" price={q?.ask} size={q?.ask_size} tone={NEG} />
        <Row label="Bid" price={q?.bid} size={q?.bid_size} tone={POS} />
      </div>
      <div className="muted" style={{ fontSize: 11, marginTop: 6, textAlign: "center", fontVariantNumeric: "tabular-nums" }}>
        Spread {num(q?.spread)} {spreadPct != null && `· ${spreadPct.toFixed(2)}%`}
      </div>
    </div>
  );
}

const WS_TABS = ["Positioner", "Ordrer", "Historik", "AI-signaler"] as const;

/** Bottom terminal workspace — tabbed Positions / Orders / History / Signals. */
function WorkspaceTabs({ onOpen, onToast }: { onOpen?: (s: string) => void; onToast?: (m: string) => void }) {
  const [tab, setTab] = useState<(typeof WS_TABS)[number]>("Positioner");
  return (
    <div className="card" style={{ marginTop: 14 }}>
      <div style={{ display: "flex", gap: 4, marginBottom: 10, borderBottom: "1px solid var(--border)", paddingBottom: 8 }}>
        {WS_TABS.map((t) => (
          <button key={t} type="button" onClick={() => setTab(t)}
            className={t === tab ? "" : "secondary"} style={{ padding: "4px 12px", fontSize: 12 }}>{t}</button>
        ))}
      </div>
      {tab === "Positioner" && <PortfolioView onOpen={onOpen} />}
      {tab === "Ordrer" && <OrdersView onToast={onToast} />}
      {tab === "Historik" && <HistoryView />}
      {tab === "AI-signaler" && <SignalsView onOpen={onOpen} />}
    </div>
  );
}

/** Center analysis workstation for a single instrument: candlestick chart + a
 *  risk-checked Buy/Sell ticket (with SL/TP + position calculator + L1 depth). */
export function InstrumentPage({ symbol, onClose, onTraded, onOpen, onToast }: { symbol: string; onClose?: () => void; onTraded?: () => void; onOpen?: (s: string) => void; onToast?: (m: string) => void }) {
  const [a, setA] = useState<Analysis | null>(null);
  const [side, setSide] = useState<"BUY" | "SELL">("BUY");
  const [qty, setQty] = useState("1");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  const [riskDkk, setRiskDkk] = useState("500");
  const [stopPct, setStopPct] = useState("8");
  const [tpPct, setTpPct] = useState("16");

  useEffect(() => {
    let alive = true;
    setA(null); setResult(null);
    api.analyzeStock(symbol).then((r) => alive && setA(r)).catch((e) => alive && setA({ symbol, error: String(e) }));
    return () => { alive = false; };
  }, [symbol]);

  const submit = async () => {
    const q = Number(qty);
    if (!Number.isFinite(q) || q <= 0) { setResult("Angiv et antal > 0"); return; }
    setBusy(true); setResult(null);
    try {
      if (side === "BUY") {
        const r = await api.manualBuy(symbol, q);
        if (r.placed) {
          setResult(`✅ Købt ${r.quantity} ${r.symbol} @ ${num(r.price)}${r.capped ? ` (afkortet fra ${r.requested} af risk-motoren)` : ""}${r.stop_price ? ` · stop @ ${num(r.stop_price)}` : ""}`);
          onTraded?.();
        } else {
          setResult(`⛔ Afvist af risk-motoren: ${(r.reasons || []).join("; ") || "ukendt"}`);
        }
      } else {
        const r = await api.manualSell(symbol, q);
        const sold = r.quantity ?? 0;
        setResult(`✅ Solgt ${num(sold, 0)} ${symbol}${r.price ? ` @ ${num(r.price)}` : ""}${r.capped ? " (afkortet til beholdning)" : ""}`);
        onTraded?.();
      }
    } catch (e) {
      setResult(`Fejl: ${(e as Error).message}`);
    } finally { setBusy(false); }
  };

  const isBuy = side === "BUY";
  const sideColor = isBuy ? POS : NEG;
  const estCost = a?.price ? Number(qty) * a.price : null;
  const stopPrice = a?.price && Number(stopPct) > 0 ? a.price * (1 - Number(stopPct) / 100) : null;
  const tpPrice = a?.price && Number(tpPct) > 0 ? a.price * (1 + Number(tpPct) / 100) : null;
  const suggestedQty = a?.price && Number(stopPct) > 0 && Number(riskDkk) > 0
    ? Math.max(1, Math.floor(Number(riskDkk) / (a.price * (Number(stopPct) / 100))))
    : null;

  return (
    <>
    <div style={{ display: "grid", gridTemplateColumns: "1fr 320px", gap: 16, alignItems: "start" }}>
      {/* ---- Center: chart + analysis ---- */}
      <div style={{ display: "flex", flexDirection: "column", gap: 14, minWidth: 0 }}>
        <div className="card">
          <div style={{ display: "flex", alignItems: "baseline", gap: 12, marginBottom: 6 }}>
            <h2 style={{ margin: 0, border: "none", padding: 0, fontSize: 22, color: "var(--text)", textTransform: "none", letterSpacing: 0 }}>{symbol}</h2>
            {a?.price != null && <span style={{ fontSize: 20, fontWeight: 700, fontVariantNumeric: "tabular-nums" }}>{num(a.price)}</span>}
            {a?.from_high_pct != null && <span className="muted" style={{ fontSize: 12 }}>{pct(a.from_high_pct)} fra 52u-høj</span>}
            {onClose && <button className="secondary" style={{ marginLeft: "auto", padding: "3px 10px", fontSize: 12 }} onClick={onClose}>Luk</button>}
          </div>
          {a?.error ? <p className="error">{a.error}</p> : <CandleChart symbol={symbol} />}
        </div>

        {a && !a.error && (
          <div className="card">
            <h2>Analyse</h2>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(120px, 1fr))", gap: 8 }}>
              <Stat label="Factor score" value={num(a.factor_score, 0)} tone={a.factor_score != null && a.factor_score >= (a.buy_gate ?? 65) ? POS : NEG} />
              <Stat label="12-1 momentum" value={pct(a.mom_12_1_pct)} tone={toneOf(a.mom_12_1_pct)} />
              <Stat label="5-dags" value={pct(a.ret_5d_pct)} tone={toneOf(a.ret_5d_pct)} />
              <Stat label="vs SMA50" value={pct(a.sma50_pct)} tone={toneOf(a.sma50_pct)} />
              <Stat label="vs SMA200" value={pct(a.sma200_pct)} tone={toneOf(a.sma200_pct)} />
              <Stat label="RSI(14)" value={num(a.rsi14, 0)} />
              <Stat label="Ann. vol" value={pct(a.ann_vol_pct)} />
              <Stat label="Næste earnings" value={a.next_earnings ?? "—"} />
            </div>
            {a.signals && a.signals.length > 0 && (
              <>
                <div className="muted" style={{ fontSize: 11, margin: "12px 0 6px", textTransform: "uppercase", letterSpacing: ".05em" }}>Strategi-signaler</div>
                <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                  {a.signals.map((s) => (
                    <span key={s.strategy} style={{
                      fontSize: 11, padding: "3px 9px", borderRadius: 5, border: "1px solid var(--border)",
                      color: s.long ? POS : "var(--muted)", background: s.long ? "rgba(36,192,122,.12)" : "transparent", fontWeight: 600,
                    }}>{s.strategy} {s.long ? "▲ long" : "flat"}</span>
                  ))}
                </div>
              </>
            )}
          </div>
        )}
      </div>

      {/* ---- Right: order ticket ---- */}
      <div className="card" style={{ position: "sticky", top: 68 }}>
        {/* Buy / Sell toggle */}
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6, marginBottom: 10 }}>
          <button onClick={() => setSide("BUY")} className={isBuy ? "" : "secondary"}
            style={{ padding: "8px 0", fontWeight: 700, background: isBuy ? POS : undefined }}>KØB</button>
          <button onClick={() => setSide("SELL")} className={!isBuy ? "" : "secondary"}
            style={{ padding: "8px 0", fontWeight: 700, background: !isBuy ? NEG : undefined }}>SÆLG</button>
        </div>
        <div style={{ fontSize: 12, marginBottom: 8 }} className="muted">
          {isBuy
            ? <>Køb går <strong style={{ color: "var(--text)" }}>gennem risk-motoren</strong> — kill switch, max-position, eksponering og gearing håndhæves (kan afkorte/afvise).</>
            : <>Salg <strong style={{ color: "var(--text)" }}>reducerer</strong> positionen (afkortes til din beholdning). Fuldt salg lukker positionen.</>}
        </div>
        <label className="field" style={{ fontSize: 12 }}>Antal</label>
        <input type="number" min="1" step="1" value={qty} onChange={(e) => setQty(e.target.value)} style={{ width: "100%", margin: "4px 0 8px" }} />
        <div style={{ fontSize: 12, marginBottom: 12 }} className="muted">
          Est. værdi: <span style={{ color: "var(--text)", fontVariantNumeric: "tabular-nums" }}>{estCost == null ? "—" : num(estCost, 0)}</span>
          {a?.price != null && ` @ ${num(a.price)}/stk`}
        </div>

        {/* Level-1 depth */}
        <DepthPanel symbol={symbol} />

        {/* SL / TP */}
        <div style={{ borderTop: "1px solid var(--border)", paddingTop: 10, marginBottom: 12 }}>
          <div className="muted" style={{ fontSize: 10, textTransform: "uppercase", letterSpacing: ".05em", marginBottom: 6 }}>Stop-loss / take-profit</div>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
            <div>
              <label className="field" style={{ fontSize: 11 }}>Stop %</label>
              <input type="number" min="0" step="0.5" value={stopPct} onChange={(e) => setStopPct(e.target.value)} style={{ width: "100%", margin: "3px 0 0" }} />
              <div className="muted" style={{ fontSize: 11, marginTop: 3 }}>≈ <span style={{ color: NEG }}>{num(stopPrice)}</span></div>
            </div>
            <div>
              <label className="field" style={{ fontSize: 11 }}>Take-profit %</label>
              <input type="number" min="0" step="0.5" value={tpPct} onChange={(e) => setTpPct(e.target.value)} style={{ width: "100%", margin: "3px 0 0" }} />
              <div className="muted" style={{ fontSize: 11, marginTop: 3 }}>≈ <span style={{ color: POS }}>{num(tpPrice)}</span></div>
            </div>
          </div>
        </div>

        {/* Position calculator (buy sizing) */}
        {isBuy && (
          <div style={{ borderTop: "1px solid var(--border)", paddingTop: 10, marginBottom: 12 }}>
            <div className="muted" style={{ fontSize: 10, textTransform: "uppercase", letterSpacing: ".05em", marginBottom: 6 }}>Positions-beregner</div>
            <label className="field" style={{ fontSize: 11 }}>Risiko (DKK)</label>
            <input type="number" min="0" step="50" value={riskDkk} onChange={(e) => setRiskDkk(e.target.value)} style={{ width: "100%", margin: "3px 0 8px" }} />
            <div style={{ fontSize: 12, display: "flex", justifyContent: "space-between", alignItems: "center" }} className="muted">
              <span>Forslag: <span style={{ color: "var(--text)", fontWeight: 700 }}>{suggestedQty ?? "—"} stk</span></span>
              {suggestedQty != null && (
                <button className="secondary" style={{ padding: "2px 9px", fontSize: 11 }} onClick={() => setQty(String(suggestedQty))}>Brug</button>
              )}
            </div>
            <div className="muted" style={{ fontSize: 10, marginTop: 4, lineHeight: 1.4 }}>
              Antal så tabet ved stop ≈ risiko-beløbet. Risk-motoren fastsætter det endelige stop.
            </div>
          </div>
        )}

        <button onClick={submit} disabled={busy || !a || !!a.error}
          style={{ width: "100%", background: sideColor, fontSize: 15, padding: "11px 0", fontWeight: 700 }}>
          {busy ? (isBuy ? "Køber…" : "Sælger…") : `${isBuy ? "KØB" : "SÆLG"} ${symbol}`}
        </button>
        {result && <div style={{ fontSize: 12, marginTop: 10, lineHeight: 1.5 }}>{result}</div>}
      </div>
    </div>
    <WorkspaceTabs onOpen={onOpen} onToast={onToast} />
    </>
  );
}
