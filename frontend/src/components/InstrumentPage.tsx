import { useEffect, useMemo, useState } from "react";
import { api } from "../api";

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

/** Simple SVG price line from the on-demand history endpoint. */
function PriceLine({ symbol }: { symbol: string }) {
  const [closes, setCloses] = useState<number[] | null>(null);
  useEffect(() => {
    let alive = true;
    setCloses(null);
    api.positionHistory(symbol).then((r) => alive && setCloses(r.closes)).catch(() => alive && setCloses([]));
    return () => { alive = false; };
  }, [symbol]);
  const path = useMemo(() => {
    if (!closes || closes.length < 2) return null;
    const W = 900, H = 220, pad = 8;
    const min = Math.min(...closes), max = Math.max(...closes), span = max - min || 1;
    const x = (i: number) => pad + (i / (closes.length - 1)) * (W - 2 * pad);
    const y = (v: number) => pad + (1 - (v - min) / span) * (H - 2 * pad);
    const up = closes[closes.length - 1] >= closes[0];
    return { d: closes.map((c, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(c).toFixed(1)}`).join(" "), up, W, H };
  }, [closes]);
  if (closes === null) return <div className="muted" style={{ fontSize: 12, padding: 40, textAlign: "center" }}>indlæser kurshistorik…</div>;
  if (!path) return <div className="muted" style={{ fontSize: 12, padding: 40, textAlign: "center" }}>ingen kurshistorik for {symbol}</div>;
  return (
    <svg viewBox={`0 0 ${path.W} ${path.H}`} width="100%" height={220} preserveAspectRatio="none">
      <path d={path.d} fill="none" stroke={path.up ? POS : NEG} strokeWidth="1.6" />
    </svg>
  );
}

/** Center analysis page for a single instrument + a risk-checked manual BUY ticket. */
export function InstrumentPage({ symbol, onClose, onTraded }: { symbol: string; onClose?: () => void; onTraded?: () => void }) {
  const [a, setA] = useState<Analysis | null>(null);
  const [qty, setQty] = useState("1");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setA(null); setResult(null);
    api.analyzeStock(symbol).then((r) => alive && setA(r)).catch((e) => alive && setA({ symbol, error: String(e) }));
    return () => { alive = false; };
  }, [symbol]);

  const buy = async () => {
    const q = Number(qty);
    if (!Number.isFinite(q) || q <= 0) { setResult("Angiv et antal > 0"); return; }
    setBusy(true); setResult(null);
    try {
      const r = await api.manualBuy(symbol, q);
      if (r.placed) {
        setResult(`✅ Købt ${r.quantity} ${r.symbol} @ ${num(r.price)}${r.capped ? ` (afkortet fra ${r.requested} af risk-motoren)` : ""}${r.stop_price ? ` · stop @ ${num(r.stop_price)}` : ""}`);
        onTraded?.();
      } else {
        setResult(`⛔ Afvist af risk-motoren: ${(r.reasons || []).join("; ") || "ukendt"}`);
      }
    } catch (e) {
      setResult(`Fejl: ${(e as Error).message}`);
    } finally { setBusy(false); }
  };

  const estCost = a?.price ? Number(qty) * a.price : null;

  return (
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
          {a?.error ? <p className="error">{a.error}</p> : <PriceLine symbol={symbol} />}
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

      {/* ---- Right: BUY ticket ---- */}
      <div className="card" style={{ position: "sticky", top: 68 }}>
        <h2>Køb-ticket</h2>
        <div style={{ fontSize: 12, marginBottom: 8 }} className="muted">
          Manuelt køb går <strong style={{ color: "var(--text)" }}>gennem risk-motoren</strong> — kill switch, max-position, eksponering og gearings-loft håndhæves (kan afkorte/afvise, aldrig udvide).
        </div>
        <label className="field" style={{ fontSize: 12 }}>Antal</label>
        <input type="number" min="1" step="1" value={qty} onChange={(e) => setQty(e.target.value)} style={{ width: "100%", margin: "4px 0 8px" }} />
        <div style={{ fontSize: 12, marginBottom: 12 }} className="muted">
          Est. værdi: <span style={{ color: "var(--text)", fontVariantNumeric: "tabular-nums" }}>{estCost == null ? "—" : num(estCost, 0)}</span>
          {a?.price != null && ` @ ${num(a.price)}/stk`}
        </div>
        <button onClick={buy} disabled={busy || !a || !!a.error}
          style={{ width: "100%", background: POS, fontSize: 15, padding: "11px 0" }}>
          {busy ? "Køber…" : `KØB ${symbol}`}
        </button>
        {result && <div style={{ fontSize: 12, marginTop: 10, lineHeight: 1.5 }}>{result}</div>}
      </div>
    </div>
  );
}
