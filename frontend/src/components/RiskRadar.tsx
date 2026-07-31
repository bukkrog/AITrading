import { useEffect, useState } from "react";
import { api } from "../api";
import type { BellwetherRisk, Concentration, ScenarioStress } from "../types";

const NEG = "var(--neg, #dc2626)";
const POS = "var(--pos, #16a34a)";
const AMBER = "#f59e0b";
const TRACK = "var(--border, #2a2f3a)";

/** Horizontal magnitude bar (0..max scaled to 0..100%). */
function Bar({ pct, color, max }: { pct: number; color: string; max: number }) {
  const w = Math.min(100, (Math.abs(pct) / max) * 100);
  return (
    <div style={{ height: 7, background: TRACK, borderRadius: 4, overflow: "hidden", flex: 1 }}>
      <div style={{ width: `${w}%`, height: "100%", background: color, borderRadius: 4, transition: "width .3s" }} />
    </div>
  );
}

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="card" style={{ padding: "12px 14px", flex: "1 1 260px", minWidth: 260 }}>
      <div style={{ fontSize: 12, fontWeight: 700, letterSpacing: 0.4, textTransform: "uppercase", opacity: 0.8, marginBottom: 10 }}>{title}</div>
      {children}
    </div>
  );
}

const sectorColor = (p: number) => (Math.abs(p) >= 90 ? NEG : Math.abs(p) >= 50 ? AMBER : POS);

export function RiskRadar() {
  const [conc, setConc] = useState<Concentration | null>(null);
  const [scen, setScen] = useState<ScenarioStress | null>(null);
  const [bell, setBell] = useState<BellwetherRisk | null>(null);

  useEffect(() => {
    let alive = true;
    const load = () => {
      api.concentration().then((r) => alive && setConc(r)).catch(() => {});
      api.scenarioStress().then((r) => alive && setScen(r)).catch(() => {});
      api.bellwetherRisk().then((r) => alive && setBell(r)).catch(() => {});
    };
    load();
    const id = setInterval(load, 30000);
    return () => { alive = false; clearInterval(id); };
  }, []);

  if (!conc && !scen && !bell) return null;
  const worstLoss = scen?.scenarios.reduce((m, s) => Math.min(m, s.pnl_pct), 0) ?? 0;

  return (
    <div className="section-gap">
      <h2 style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span>⚡ Risk Radar</span>
        <span className="muted" style={{ fontSize: 12, fontWeight: 400 }}>sektor-koncentration · bellwether-begivenheder · stress-scenarier</span>
      </h2>
      <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>

        {/* ---- Concentration ---- */}
        <Card title="Sektor-koncentration">
          {conc && conc.proxies.length > 0 ? (
            <>
              <div style={{ display: "flex", alignItems: "baseline", gap: 8, marginBottom: 8 }}>
                <span style={{ fontSize: 26, fontWeight: 800, color: sectorColor(conc.concentration_pct) }}>
                  {conc.concentration_pct.toFixed(0)}%
                </span>
                <span className="muted" style={{ fontSize: 11 }}>største sektor-beta · {conc.n_holdings} positioner</span>
              </div>
              {conc.warning && (
                <div style={{ background: "rgba(220,38,38,.12)", border: `1px solid ${NEG}`, color: NEG, borderRadius: 6, padding: "6px 8px", fontSize: 11, marginBottom: 10, lineHeight: 1.4 }}>
                  ⚠ {conc.warning}
                </div>
              )}
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                {conc.proxies.map((p) => {
                  const c = p.is_sector ? sectorColor(p.exposure_pct) : "var(--muted, #8b949e)";
                  return (
                    <div key={p.proxy}>
                      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, marginBottom: 3 }}>
                        <span>{p.label}</span>
                        <span style={{ fontVariantNumeric: "tabular-nums", color: c, fontWeight: 600 }}>{p.exposure_pct.toFixed(0)}%</span>
                      </div>
                      <Bar pct={p.exposure_pct} color={c} max={150} />
                    </div>
                  );
                })}
              </div>
            </>
          ) : conc && conc.n_holdings > 0 ? (
            <p className="muted" style={{ fontSize: 12 }}>Sektor-data utilgængelig lige nu (yfinance) — ikke et "alt-klart".</p>
          ) : <p className="muted" style={{ fontSize: 12 }}>Ingen åbne positioner.</p>}
        </Card>

        {/* ---- Bellwether radar ---- */}
        <Card title="Bellwether-radar">
          {bell?.note ? (
            <p className="muted" style={{ fontSize: 12 }}>{bell.note}</p>
          ) : bell && bell.bellwethers.length > 0 ? (
            <div style={{ display: "flex", flexDirection: "column", gap: 7 }}>
              {bell.bellwethers.map((b) => {
                const newsColor = b.news_score == null ? "var(--muted,#8b949e)" : b.news_score <= 30 ? NEG : b.news_score >= 80 ? POS : "var(--muted,#8b949e)";
                return (
                  <div key={b.symbol} style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12 }}>
                    <span style={{ fontWeight: 700, minWidth: 46 }}>{b.symbol}</span>
                    <span className="muted" style={{ fontSize: 10, border: `1px solid ${TRACK}`, borderRadius: 4, padding: "0 4px" }}>{b.sector_proxy}</span>
                    {b.days_until != null ? (
                      <span style={{ color: b.imminent ? NEG : "var(--muted,#8b949e)", fontWeight: b.imminent ? 700 : 400 }}>
                        {b.imminent ? "⚠ " : ""}earnings om {b.days_until}d
                      </span>
                    ) : <span className="muted">ingen dato</span>}
                    {b.news_score != null && (
                      <span title="news-sentiment" style={{ marginLeft: "auto", color: newsColor, fontVariantNumeric: "tabular-nums" }}>
                        ● news {b.news_score.toFixed(0)}
                      </span>
                    )}
                  </div>
                );
              })}
            </div>
          ) : bell ? (
            <p className="muted" style={{ fontSize: 12 }}>Ingen bellwethers rapporterer i vinduet — sektoren er rolig lige nu.</p>
          ) : <p className="muted" style={{ fontSize: 12 }}>indlæser…</p>}
        </Card>

        {/* ---- Scenario stress ---- */}
        <Card title="Scenarie-stress">
          {scen && scen.scenarios.length > 0 ? (
            <>
              <div style={{ fontSize: 11, marginBottom: 8 }} className="muted">
                Estimeret P&L hvis sektoren falder (lineær beta, ikke en forudsigelse):
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 9 }}>
                {scen.scenarios.map((s) => (
                  <div key={s.proxy}>
                    <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, marginBottom: 3 }}>
                      <span>{s.label}</span>
                      <span style={{ color: NEG, fontWeight: 700, fontVariantNumeric: "tabular-nums" }}>
                        {s.pnl.toLocaleString("da-DK")} · {s.pnl_pct.toFixed(1)}%
                      </span>
                    </div>
                    <Bar pct={s.pnl_pct} color={NEG} max={Math.max(8, Math.abs(worstLoss))} />
                  </div>
                ))}
              </div>
            </>
          ) : <p className="muted" style={{ fontSize: 12 }}>Ingen data.</p>}
        </Card>

      </div>
    </div>
  );
}
