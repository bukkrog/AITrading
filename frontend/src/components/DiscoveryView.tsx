import { useEffect, useState } from "react";
import { api } from "../api";
import type { DiscoveryCandidate, DiscoveryStatus } from "../types";

function ago(iso: string | null): string {
  if (!iso) return "never";
  const s = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  return `${Math.round(s / 3600)}h ago`;
}

const SOURCE_LABEL: Record<string, string> = {
  day_gainers: "Top gainers", most_actives: "Most active", small_cap_gainers: "Small-cap gainers",
  aggressive_small_caps: "Aggressive small caps", growth_tech: "Growth tech", wsb: "WallStreetBets",
  sp500: "S&P 500", dow30: "Dow 30", omxc25: "OMX C25", dax: "DAX", cac: "CAC 40", europe: "Europe",
};

export function DiscoveryView() {
  const [cands, setCands] = useState<DiscoveryCandidate[]>([]);
  const [status, setStatus] = useState<DiscoveryStatus | null>(null);
  const [universe, setUniverse] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [lastLoaded, setLastLoaded] = useState<number>(0);

  const load = () => {
    setBusy(true);
    Promise.all([api.discovery(25), api.discoveryStatus(), api.automation()])
      .then(([d, s, a]) => {
        setCands(d.candidates);
        setStatus(s);
        setUniverse((a.state.universe || "").split(",").map((x) => x.trim().toUpperCase()).filter(Boolean));
        setErr(null);
        setLastLoaded(Date.now());
      })
      .catch((e) => setErr((e as Error).message))
      .finally(() => setBusy(false));
  };

  useEffect(() => {
    load();
    const id = setInterval(load, 15000);
    return () => clearInterval(id);
  }, []);

  const inUniverse = (sym: string) => universe.includes(sym.toUpperCase());

  return (
    <div className="card">
      <h2>🔭 Discovery — live market scan</h2>
      <p className="muted" style={{ fontSize: 12, marginTop: -4 }}>
        The platform scans these sources, ranks every candidate by momentum, and trades the top ones
        (highlighted). Auto-refreshes every 15s.
      </p>

      {/* status strip */}
      <div style={{ display: "flex", flexWrap: "wrap", gap: 16, alignItems: "center", margin: "10px 0 4px" }}>
        <span>
          <span className={`status-dot ${status?.enabled ? "on" : "off"}`} />
          <strong>{status?.enabled ? "Auto-discover ON" : "Auto-discover OFF"}</strong>
        </span>
        <span className="muted" style={{ fontSize: 12 }}>
          Last scan <strong>{ago(status?.last_scan_at ?? null)}</strong>
          {status?.ttl_seconds ? ` · re-scans ≤ every ${Math.round(status.ttl_seconds / 60)} min` : ""}
        </span>
        <button className="secondary" disabled={busy} onClick={load} style={{ marginLeft: "auto" }}>
          {busy ? "Scanning…" : "Refresh"}
        </button>
      </div>

      {/* source chips */}
      <div style={{ display: "flex", flexWrap: "wrap", gap: 6, margin: "8px 0 14px" }}>
        {(status?.sources ?? []).map((s) => (
          <span key={s} style={{
            fontSize: 11, padding: "2px 8px", borderRadius: 12,
            border: "1px solid var(--border)", background: "var(--panel-2)",
          }}>{SOURCE_LABEL[s] ?? s}</span>
        ))}
        {(status?.sources ?? []).length === 0 && (
          <span className="muted" style={{ fontSize: 12 }}>No sources selected — using the static pool (set sources in Setup).</span>
        )}
      </div>

      {err && <div className="error">{err}</div>}

      {/* ranked candidates with momentum bars */}
      {cands.length === 0 ? (
        <p className="muted">No candidates yet — scanning…</p>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          {cands.map((c, i) => {
            const traded = inUniverse(c.symbol);
            const width = Math.max(2, Math.min(100, c.score));
            return (
              <div key={c.symbol} title={c.rationale}
                style={{ display: "grid", gridTemplateColumns: "26px 110px 1fr 62px", gap: 8, alignItems: "center" }}>
                <span className="muted" style={{ fontSize: 11, textAlign: "right" }}>{i + 1}</span>
                <span style={{ fontWeight: traded ? 700 : 400 }}>
                  {c.symbol.split(":")[0]}
                  {traded && <span style={{ marginLeft: 6, fontSize: 10, color: "var(--pos,#16a34a)" }}>● trading</span>}
                </span>
                <span style={{ background: "var(--panel-2)", borderRadius: 6, overflow: "hidden", height: 16 }}>
                  <span style={{
                    display: "block", height: "100%", width: `${width}%`,
                    background: traded ? "var(--pos,#16a34a)" : "var(--accent,#6366f1)", opacity: traded ? 1 : 0.55,
                  }} />
                </span>
                <span style={{ fontSize: 12, textAlign: "right" }}>
                  <span className={c.momentum >= 0 ? "pos" : "neg"}>{c.momentum >= 0 ? "+" : ""}{c.momentum.toFixed(1)}%</span>
                  <span className="muted" style={{ display: "block", fontSize: 10 }}>score {c.score.toFixed(0)}</span>
                </span>
              </div>
            );
          })}
        </div>
      )}
      <div className="muted" style={{ fontSize: 11, marginTop: 12 }}>
        Bar = momentum score (0–100). Green ● = currently in the traded universe. Hover a row for the reason.
        {lastLoaded > 0 && ` · updated ${new Date(lastLoaded).toLocaleTimeString()}`}
      </div>
    </div>
  );
}
