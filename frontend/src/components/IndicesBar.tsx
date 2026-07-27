import { useEffect, useState } from "react";
import { api } from "../api";
import type { MarketIndex } from "../types";

/** Sparkline for one index — colored by the day's direction. */
function Spark({ pts, up }: { pts: number[]; up: boolean }) {
  if (pts.length < 2) return null;
  const W = 120, H = 34, pad = 3;
  const min = Math.min(...pts), max = Math.max(...pts);
  const span = max - min || 1;
  const x = (i: number) => pad + (i / (pts.length - 1)) * (W - 2 * pad);
  const y = (v: number) => pad + (1 - (v - min) / span) * (H - 2 * pad);
  const d = pts.map((v, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const color = up ? "var(--pos, #16a34a)" : "var(--neg, #dc2626)";
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} preserveAspectRatio="none" style={{ display: "block" }}>
      <path d={d} fill="none" stroke={color} strokeWidth="1.5" />
    </svg>
  );
}

/** Top-of-dashboard strip: S&P 500, Dow 30, Nasdaq with a 1-day sparkline. */
export function IndicesBar() {
  const [data, setData] = useState<MarketIndex[] | null>(null);

  useEffect(() => {
    let alive = true;
    const load = () => api.indices().then((r) => { if (alive) setData(r.indices); }).catch(() => {});
    load();
    const t = setInterval(load, 60_000);
    return () => { alive = false; clearInterval(t); };
  }, []);

  if (!data) return null;

  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 10, marginBottom: 14 }}>
      {data.map((ix) => {
        const up = (ix.change_pct ?? 0) >= 0;
        const color = up ? "var(--pos, #16a34a)" : "var(--neg, #dc2626)";
        return (
          <div key={ix.symbol} className="card" style={{ padding: "10px 12px" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
              <span style={{ fontSize: 12, fontWeight: 600 }}>{ix.name}</span>
              <span style={{ fontSize: 12, color }}>
                {ix.change_pct == null ? "—" : `${up ? "▲" : "▼"} ${Math.abs(ix.change_pct).toFixed(2)}%`}
              </span>
            </div>
            <div style={{ fontSize: 16, fontWeight: 700, margin: "2px 0 4px" }}>
              {ix.last == null ? "n/a" : ix.last.toLocaleString("da-DK")}
            </div>
            <Spark pts={ix.spark} up={up} />
          </div>
        );
      })}
    </div>
  );
}
