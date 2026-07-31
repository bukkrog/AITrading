import type { Snapshot } from "../types";

// Dependency-free SVG equity + drawdown chart.
export function EquityChart({ snapshots }: { snapshots: Snapshot[] }) {
  if (snapshots.length < 2) {
    return <p className="muted">No snapshots yet — run a cycle to populate the equity curve.</p>;
  }

  const w = 640;
  const h = 200;
  const pad = 8;
  const values = snapshots.map((s) => s.total_value);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;

  const x = (i: number) => pad + (i / (snapshots.length - 1)) * (w - 2 * pad);
  const y = (v: number) => pad + (1 - (v - min) / range) * (h - 2 * pad);

  const line = snapshots.map((s, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(1)},${y(s.total_value).toFixed(1)}`).join(" ");
  const area = `${line} L${x(snapshots.length - 1).toFixed(1)},${h - pad} L${x(0).toFixed(1)},${h - pad} Z`;
  const last = values[values.length - 1];
  const first = values[0];
  const up = last >= first;
  const color = up ? "#3fb950" : "#f2545b";

  // ~6 evenly spaced date ticks along the bottom.
  const shortTs = (ts: string) => {
    const d = new Date(ts);
    return isNaN(d.getTime()) ? String(ts).slice(5, 10) : `${String(d.getDate()).padStart(2, "0")}/${String(d.getMonth() + 1).padStart(2, "0")}`;
  };
  const N = Math.min(6, snapshots.length);
  const ticks = Array.from({ length: N }, (_, k) => {
    const i = Math.round((k / (N - 1)) * (snapshots.length - 1));
    return { label: shortTs(snapshots[i].ts), left: (i / (snapshots.length - 1)) * 100 };
  });

  return (
    <div style={{ overflowX: "auto" }}>
      <svg viewBox={`0 0 ${w} ${h}`} width="100%" height={h} preserveAspectRatio="none">
        <defs>
          <linearGradient id="eq" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={color} stopOpacity="0.25" />
            <stop offset="100%" stopColor={color} stopOpacity="0" />
          </linearGradient>
        </defs>
        <path d={area} fill="url(#eq)" />
        <path d={line} fill="none" stroke={color} strokeWidth="2" />
      </svg>
      <div style={{ position: "relative", height: 16, marginTop: 2, borderTop: "1px solid var(--border)" }}>
        {ticks.map((t, i) => (
          <span key={i} className="muted" style={{
            position: "absolute", top: 3, left: `${t.left}%`, fontSize: 10, fontVariantNumeric: "tabular-nums",
            transform: i === 0 ? "none" : i === ticks.length - 1 ? "translateX(-100%)" : "translateX(-50%)",
            whiteSpace: "nowrap",
          }}>{t.label}</span>
        ))}
      </div>
      <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>
        {snapshots.length} snapshots · min {min.toLocaleString()} · max {max.toLocaleString()} · last {last.toLocaleString()}
      </div>
    </div>
  );
}
