import { useEffect, useState } from "react";
import { api } from "../api";

/** Mini price sparkline for one held position, with a dashed line at the entry
 *  (avg) price so you can see where you bought relative to recent prices. */
export function PositionChart({ symbol, entry }: { symbol: string; entry: number }) {
  const [closes, setCloses] = useState<number[] | null>(null);

  useEffect(() => {
    let alive = true;
    api.positionHistory(symbol).then((r) => { if (alive) setCloses(r.closes); }).catch(() => setCloses([]));
    return () => { alive = false; };
  }, [symbol]);

  if (closes === null) return <div className="muted" style={{ fontSize: 11 }}>loading chart…</div>;
  if (closes.length < 2) return <div className="muted" style={{ fontSize: 11 }}>no price history for {symbol}</div>;

  const W = 320, H = 60, pad = 4;
  const all = [...closes, entry];
  const min = Math.min(...all), max = Math.max(...all);
  const span = max - min || 1;
  const x = (i: number) => pad + (i / (closes.length - 1)) * (W - 2 * pad);
  const y = (v: number) => pad + (1 - (v - min) / span) * (H - 2 * pad);

  const path = closes.map((c, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(1)},${y(c).toFixed(1)}`).join(" ");
  const last = closes[closes.length - 1];
  const up = last >= entry;
  const entryY = y(entry).toFixed(1);
  const line = up ? "var(--pos, #16a34a)" : "var(--neg, #dc2626)";

  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} style={{ maxWidth: W }} preserveAspectRatio="none">
      {/* entry (buy) reference line */}
      <line x1={pad} y1={entryY} x2={W - pad} y2={entryY} stroke="var(--border, #888)" strokeDasharray="3 3" strokeWidth="1" />
      <text x={pad + 2} y={Number(entryY) - 2} fontSize="9" fill="var(--muted, #888)">entry {entry.toFixed(2)}</text>
      {/* price line */}
      <path d={path} fill="none" stroke={line} strokeWidth="1.5" />
    </svg>
  );
}
