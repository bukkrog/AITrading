import { useEffect, useState } from "react";
import { api } from "../api";

type Trace = Awaited<ReturnType<typeof api.signalTrace>>;
type Stage = NonNullable<Trace["stages"]>[number];

const STATUS: Record<string, { color: string; bg: string; ring: string; label: string }> = {
  pass: { color: "#24c07a", bg: "rgba(36,192,122,.12)", ring: "rgba(36,192,122,.5)", label: "Bestået" },
  fail: { color: "#f2545b", bg: "rgba(242,84,91,.12)", ring: "rgba(242,84,91,.5)", label: "Afvist" },
  wait: { color: "#e3a008", bg: "rgba(227,160,8,.12)", ring: "rgba(227,160,8,.5)", label: "Afventer" },
  skip: { color: "#8b949e", bg: "rgba(139,148,158,.10)", ring: "rgba(139,148,158,.35)", label: "Sprunget over" },
  info: { color: "#2f7ff6", bg: "rgba(47,127,246,.12)", ring: "rgba(47,127,246,.5)", label: "Rådgivende" },
};
const ICON: Record<string, string> = { quant: "🧮", news: "📰", gate: "🚪", risk: "🛡️", timing: "⏱️", outcome: "🏁" };
const SENT: Record<string, string> = { good: "#24c07a", bad: "#f2545b", neutral: "#8b949e" };

/** One stage node in the flow. */
function Node({ s, i }: { s: Stage; i: number }) {
  const st = STATUS[s.status] ?? STATUS.skip;
  const hasBar = s.value != null && s.threshold != null && s.threshold > 0;
  const pctOfThr = hasBar ? Math.min(100, (Number(s.value) / (Number(s.threshold) * 1.6)) * 100) : 0;
  const thrPos = hasBar ? Math.min(100, (100 / 1.6)) : 0; // threshold sits at 1/1.6 of the track
  return (
    <div className="tf-node" style={{ ["--i" as string]: i }}>
      <div className="tf-dot" style={{ background: st.bg, borderColor: st.ring, color: st.color }}>
        <span style={{ fontSize: 18 }}>{ICON[s.key] ?? "•"}</span>
      </div>
      <div className="tf-body" style={{ borderColor: st.ring }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          <strong style={{ fontSize: 14 }}>{s.label}</strong>
          <span className="tf-badge" style={{ background: st.bg, color: st.color }}>{st.label}</span>
          {s.value != null && s.key !== "gate" && s.key !== "outcome" && s.key !== "timing" && (
            <span style={{ marginLeft: "auto", fontSize: 18, fontWeight: 800, fontVariantNumeric: "tabular-nums", color: st.color }}>
              {s.key === "risk" ? `${s.value} stk` : s.value}
              {s.threshold != null && s.key !== "risk" && <span className="muted" style={{ fontSize: 11, fontWeight: 400 }}> / {s.threshold}</span>}
            </span>
          )}
        </div>
        {hasBar && s.key !== "risk" && (
          <div className="tf-track">
            <div className="tf-fill" style={{ width: `${pctOfThr}%`, background: st.color }} />
            <div className="tf-thr" style={{ left: `${thrPos}%` }} title={`Tærskel ${s.threshold}`} />
          </div>
        )}
        {s.detail && <div className="muted" style={{ fontSize: 12, marginTop: 5, lineHeight: 1.45 }}>{s.detail}</div>}
        {s.key === "risk" && (s.stop_price != null || s.reference_price != null) && (
          <div className="muted" style={{ fontSize: 11, marginTop: 4, display: "flex", gap: 12 }}>
            {s.reference_price != null && <span>ref {s.reference_price}</span>}
            {s.stop_price != null && <span>stop {s.stop_price}</span>}
            {s.risk_score != null && <span>risk-score {s.risk_score}</span>}
          </div>
        )}
        {s.reasons && s.reasons.length > 0 && s.status === "fail" && (
          <div style={{ fontSize: 11, marginTop: 4, color: STATUS.fail.color }}>{s.reasons.join("; ")}</div>
        )}
        {s.headlines && s.headlines.length > 0 && (
          <div style={{ marginTop: 6, display: "flex", flexDirection: "column", gap: 3 }}>
            {s.headlines.slice(0, 4).map((h, k) => (
              <div key={k} style={{ fontSize: 11, display: "flex", gap: 6, alignItems: "baseline" }}>
                <span style={{ color: SENT[h.sentiment.label], fontWeight: 700, fontVariantNumeric: "tabular-nums", flexShrink: 0 }}>{h.sentiment.score.toFixed(0)}</span>
                <span className="muted" style={{ lineHeight: 1.3 }}>{h.title}</span>
              </div>
            ))}
          </div>
        )}
        {s.note && <div className="muted" style={{ fontSize: 10, marginTop: 4, fontStyle: "italic" }}>{s.note}</div>}
      </div>
    </div>
  );
}

/** Animated, read-only visualisation of the REAL decision path for one symbol:
 *  quant → news → gate → risk → entry-timing → outcome. Executes nothing. */
export function TradeFlow({ symbol, onClose }: { symbol: string; onClose: () => void }) {
  const [t, setT] = useState<Trace | null>(null);
  useEffect(() => {
    let alive = true;
    setT(null);
    api.signalTrace(symbol).then((r) => alive && setT(r)).catch((e) => alive && setT({ symbol, error: String(e) }));
    return () => { alive = false; };
  }, [symbol]);

  const approved = t?.approved;
  return (
    <div className="tf-overlay" onClick={onClose}>
      <div className="tf-panel" onClick={(e) => e.stopPropagation()}>
        <div className="tf-head">
          <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
            <span style={{ fontSize: 20, fontWeight: 800 }}>{symbol.split(":")[0]}</span>
            {t?.price != null && <span className="muted" style={{ fontVariantNumeric: "tabular-nums" }}>{t.price}</span>}
            {t && !t.error && (
              <span className="tf-badge" style={{ background: approved ? STATUS.pass.bg : STATUS.fail.bg, color: approved ? STATUS.pass.color : STATUS.fail.color, fontSize: 12 }}>
                {approved ? (t.entry_mode === "suggest" ? "Ville foreslå køb" : "Ville købe") : "Ville ikke handle"}
              </span>
            )}
          </div>
          <button className="secondary" style={{ padding: "4px 12px", fontSize: 13 }} onClick={onClose}>Luk</button>
        </div>
        <div className="muted" style={{ fontSize: 11, padding: "0 18px 4px" }}>
          Ægte beslutningssti (dry-run) — samme motorer platformen bruger. Der handles/gemmes intet.
        </div>
        <div className="tf-flow">
          {!t ? <p className="muted" style={{ padding: 24, textAlign: "center" }}>kører {symbol} gennem motorerne…</p>
            : t.error ? <p className="error" style={{ padding: 18 }}>{t.error}</p>
            : (t.stages ?? []).map((s, i) => <Node key={s.key} s={s} i={i} />)}
        </div>
      </div>
    </div>
  );
}
