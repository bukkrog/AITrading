import { useState } from "react";
import { api } from "../api";
import type { AutomationInfo, MarketHours, Monitoring, Portfolio } from "../types";

function LimitBar({ label, value, limit, util }: { label: string; value: number; limit: number; util: number }) {
  const cls = util >= 90 ? "hot" : util >= 60 ? "mid" : "";
  return (
    <div className="limitbar">
      <div className="row">
        <span>{label}</span>
        <span>{value.toFixed(1)}% / {limit.toFixed(1)}%</span>
      </div>
      <div className="track"><div className={`fill-bar ${cls}`} style={{ width: `${util}%` }} /></div>
    </div>
  );
}

interface Props {
  monitoring: Monitoring;
  automation: AutomationInfo;
  portfolio: Portfolio;
  marketHours: MarketHours | null;
  onChanged: () => void;
  onToast: (m: string) => void;
}

export function MonitoringPanel({ monitoring, automation, portfolio, marketHours, onChanged, onToast }: Props) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [interval, setIntervalSec] = useState(String(automation.state.interval_seconds));
  const a = automation.state;
  const L = monitoring.limits;

  // Seconds until the next scheduled tick (best-effort; refreshes on poll).
  let nextRun: string | null = null;
  if (a.enabled && !a.emergency_stopped) {
    if (!a.last_run_at) {
      nextRun = "imminent";
    } else {
      const elapsed = (Date.now() - new Date(a.last_run_at).getTime()) / 1000;
      const remaining = Math.max(0, Math.round(a.interval_seconds - elapsed));
      nextRun = remaining <= 0 ? "imminent" : `~${remaining}s`;
    }
  }

  async function act(fn: () => Promise<unknown>, msg?: string) {
    setBusy(true);
    setErr(null);
    try {
      await fn();
      if (msg) onToast(msg);
      onChanged();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const dot = a.emergency_stopped ? "stopped" : a.enabled ? "on" : "off";
  const statusLabel = a.emergency_stopped ? "EMERGENCY STOPPED" : a.enabled ? "running" : "stopped";

  return (
    <div className="card">
      <h2>Monitoring &amp; automation</h2>

      <div style={{ marginBottom: 12 }}>
        <span className={`status-dot ${dot}`} />
        <strong>{statusLabel}</strong>
        <span className="muted">
          {" "}· {a.live_mode ? "LIVE" : "paper"} · {monitoring.automation.runs_count} runs · risk: {monitoring.effective_risk}
        </span>
      </div>

      {marketHours && marketHours.enabled && (
        <div
          className="killbar"
          style={{
            marginBottom: 12,
            background: marketHours.paused ? undefined : "transparent",
            border: "1px solid var(--border)",
            color: "var(--text)",
            fontSize: 12,
          }}
        >
          {marketHours.paused ? "⏸ On pause — market closed. " : (marketHours.any_open ? "🟢 Market open. " : "🌙 Market closed. ")}
          {marketHours.exchanges.map((e) => (
            <span key={e.key} style={{ marginRight: 10 }}>
              {e.open ? "🟢" : "🔴"} {e.name} ({e.local_time}, {e.hours})
              {!e.open && e.next_open_local ? ` · opens ${e.next_open_local}` : ""}
            </span>
          ))}
        </div>
      )}

      <div className="control-row" style={{ marginBottom: 12 }}>
        <label className="field">Interval (sec) {nextRun && <span className="muted">· next run {nextRun}</span>}</label>
        <span style={{ display: "flex", gap: 6 }}>
          <input type="text" value={interval} onChange={(e) => setIntervalSec(e.target.value)} style={{ maxWidth: 70 }} />
          <button className="secondary" disabled={busy}
            onClick={() => act(() => api.configureAutomation({ interval_seconds: Number(interval) || 300 }), `Interval set to ${interval}s`)}>
            Set
          </button>
        </span>
      </div>

      <LimitBar label="Exposure" value={L.exposure_pct} limit={L.exposure_limit_pct} util={L.exposure_util_pct} />
      <LimitBar label="Drawdown" value={L.drawdown_pct} limit={L.drawdown_limit_pct} util={L.drawdown_util_pct} />
      <LimitBar label="Daily loss" value={L.daily_loss_pct} limit={L.daily_loss_limit_pct} util={L.daily_loss_util_pct} />
      <div className="muted" style={{ fontSize: 12 }}>
        Positions {L.open_positions}/{L.max_open_positions} · active alerts {monitoring.active_alerts}
      </div>

      <h2 style={{ marginTop: 16 }}>Live-readiness gate</h2>
      <div className={`badge ${automation.live_gate.ready ? "ok" : ""}`}>
        {automation.live_gate.ready ? "READY for live" : "NOT ready for live"}
      </div>
      <ul className="gate">
        {automation.live_gate.checks.map((c) => (
          <li key={c.name}>
            <span className={`mark ${c.passed ? "pass" : "fail"}`}>{c.passed ? "✓" : "✗"}</span>
            <span>{c.name}</span>
            <span className="detail">— {c.detail}</span>
          </li>
        ))}
      </ul>

      <div className="btn-row" style={{ marginTop: 14 }}>
        {!a.enabled ? (
          <button disabled={busy || a.emergency_stopped} onClick={() => act(api.automationStart, "Automation started")}>
            Start automation
          </button>
        ) : (
          <button className="secondary" disabled={busy} onClick={() => act(api.automationStop, "Automation stopped")}>
            Stop automation
          </button>
        )}
        <button className="secondary" disabled={busy} onClick={() => act(api.automationTick, "Tick executed")}>
          Run tick now
        </button>
        {!a.emergency_stopped ? (
          <button className="danger" disabled={busy} onClick={() => act(api.emergencyStop, "EMERGENCY STOP")}>
            🛑 Emergency stop
          </button>
        ) : (
          <button className="warn" disabled={busy} onClick={() => act(api.clearEmergency, "Emergency cleared")}>
            Clear emergency
          </button>
        )}
        <button
          className={portfolio.kill_switch_engaged ? "warn" : "danger"}
          disabled={busy}
          onClick={() => act(() => api.setKillSwitch(!portfolio.kill_switch_engaged),
            portfolio.kill_switch_engaged ? "Kill switch released" : "Kill switch engaged")}
        >
          {portfolio.kill_switch_engaged ? "Release kill switch" : "Engage kill switch"}
        </button>
      </div>
      {err && <div className="error">{err}</div>}
    </div>
  );
}
