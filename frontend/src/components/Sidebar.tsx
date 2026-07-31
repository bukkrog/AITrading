import { useState } from "react";
import { api } from "../api";
import type { AutomationInfo, Monitoring } from "../types";

export type View = "dashboard" | "trading" | "discovery" | "analytics" | "setup" | "audit";

const NAV: { id: View; label: string; icon: string }[] = [
  { id: "dashboard", label: "Dashboard", icon: "📊" },
  { id: "trading", label: "Auto Trading", icon: "🤖" },
  { id: "discovery", label: "Discovery", icon: "🔭" },
  { id: "analytics", label: "Analytics", icon: "📈" },
  { id: "setup", label: "Setup", icon: "⚙" },
  { id: "audit", label: "Audit log", icon: "📜" },
];

interface Props {
  view: View;
  setView: (v: View) => void;
  monitoring: Monitoring | null;
  automation: AutomationInfo | null;
  alertsCount: number;
  onChanged: () => void;
  onToast: (m: string) => void;
}

export function Sidebar({ view, setView, monitoring, automation, alertsCount, onChanged, onToast }: Props) {
  const [busy, setBusy] = useState(false);
  const a = automation?.state;
  const emergency = Boolean(a?.emergency_stopped);
  const running = Boolean(a?.enabled);
  const stateLabel = emergency ? "EMERGENCY STOP" : running ? "RUNNING" : "STOPPED";
  const dot = emergency ? "stopped" : running ? "on" : "off";

  async function toggle() {
    setBusy(true);
    try {
      if (running) {
        await api.automationStop();
        onToast("Auto Trading stopped");
      } else {
        await api.automationStart();
        onToast("Auto Trading started");
      }
      onChanged();
    } catch (e) {
      onToast((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <aside className="sidebar">
      <div className="brand">
        📈 BukTrader AI
        <small>{a?.live_mode ? "LIVE mode" : "paper / sim"}</small>
      </div>

      {NAV.map((n) => (
        <button
          key={n.id}
          className={`navitem ${view === n.id ? "active" : ""}`}
          onClick={() => setView(n.id)}
        >
          <span>{n.icon}</span>
          <span>{n.label}</span>
          {n.id === "trading" && alertsCount > 0 && <span className="count">{alertsCount}</span>}
        </button>
      ))}

      <div className="nav-spacer" />

      <div className="auto-box">
        <div className="title">Auto Trading</div>
        <div className="state">
          <span className={`status-dot ${dot}`} />
          {stateLabel}
        </div>
        {monitoring && (
          <div className="muted" style={{ fontSize: 11, marginBottom: 10 }}>
            {monitoring.automation.runs_count} runs · {monitoring.broker_mode}
            {a?.last_run_at ? ` · last ${new Date(a.last_run_at).toLocaleTimeString()}` : ""}
          </div>
        )}
        <button
          onClick={toggle}
          disabled={busy || emergency}
          className={running ? "secondary" : ""}
          style={{ width: "100%" }}
        >
          {emergency ? "Emergency — clear in Auto Trading" : running ? "■ Stop" : "▶ Start Auto Trading"}
        </button>
      </div>
    </aside>
  );
}
