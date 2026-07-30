import { useEffect, useState } from "react";
import { api } from "../api";
import type { Costs } from "../types";

const NEG = "var(--neg, #dc2626)";
const POS = "var(--pos, #16a34a)";
const AMBER = "#f59e0b";

/** All-in cost transparency: how much of the equity change is fees/FX, which
 *  price-P&L hides. The #1 gap for judging whether the strategy works net. */
export function CostCard() {
  const [c, setC] = useState<Costs | null>(null);

  useEffect(() => {
    let alive = true;
    const load = () => api.costs().then((r) => alive && setC(r)).catch(() => {});
    load();
    const id = setInterval(load, 30000);
    return () => { alive = false; clearInterval(id); };
  }, []);

  if (!c || c.error) return null;
  const fmt = (n: number) => `${n >= 0 ? "+" : ""}${n.toLocaleString("da-DK")} ${c.currency}`;
  const cost = Math.max(0, c.estimated_cost);
  const priceMag = Math.abs(c.realized_price_pnl) + Math.abs(c.unrealized_pnl);
  const totalMag = cost + priceMag || 1;

  return (
    <div className="card section-gap" style={{ padding: "12px 14px" }}>
      <div style={{ fontSize: 12, fontWeight: 700, textTransform: "uppercase", letterSpacing: 0.4, opacity: 0.8, marginBottom: 8 }}>
        💸 Omkostnings-gennemsigtighed
      </div>
      <div style={{ display: "flex", gap: 24, flexWrap: "wrap", alignItems: "baseline" }}>
        <div>
          <div className="muted" style={{ fontSize: 11 }}>Netto P&L (equity − startkapital)</div>
          <div style={{ fontSize: 24, fontWeight: 800, color: c.net_pnl >= 0 ? POS : NEG }}>{fmt(c.net_pnl)}</div>
        </div>
        <div>
          <div className="muted" style={{ fontSize: 11 }}>heraf omkostninger (kurtage + FX + spread)</div>
          <div style={{ fontSize: 22, fontWeight: 800, color: AMBER }}>−{cost.toLocaleString("da-DK")} {c.currency}</div>
        </div>
        {c.cost_share_of_loss_pct != null && (
          <div>
            <div className="muted" style={{ fontSize: 11 }}>omkostningers andel af tabet</div>
            <div style={{ fontSize: 22, fontWeight: 800, color: NEG }}>{c.cost_share_of_loss_pct.toFixed(0)}%</div>
          </div>
        )}
      </div>
      <div style={{ display: "flex", height: 10, borderRadius: 5, overflow: "hidden", marginTop: 12, background: "var(--border, #2a2f3a)" }}>
        <div title={`omkostninger ${cost}`} style={{ width: `${(cost / totalMag) * 100}%`, background: AMBER }} />
        <div title="pris-bevægelse (realiseret + urealiseret)" style={{ width: `${(priceMag / totalMag) * 100}%`, background: "var(--muted, #8b949e)" }} />
      </div>
      <div className="muted" style={{ fontSize: 10, marginTop: 6, lineHeight: 1.4 }}>
        <span style={{ color: AMBER }}>▮</span> omkostninger · <span style={{ color: "var(--muted,#8b949e)" }}>▮</span> pris-P&L
        ({fmt(c.realized_price_pnl)} realiseret · {fmt(c.unrealized_pnl)} urealiseret). {c.note}
      </div>
    </div>
  );
}
