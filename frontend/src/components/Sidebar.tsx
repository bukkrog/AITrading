import { useState } from "react";
import { api } from "../api";
import type { AutomationInfo, Monitoring } from "../types";

export type View =
  | "dashboard" | "markets" | "portfolio" | "orders" | "history" | "signals" | "news" | "suggestions"
  | "trading" | "discovery" | "analytics" | "setup" | "audit" | "instrument";

const NAV: { id: View; label: string; icon: string; group: string }[] = [
  { id: "dashboard", label: "Dashboard", icon: "📊", group: "Oversigt" },
  { id: "markets", label: "Markets", icon: "🌐", group: "Oversigt" },
  { id: "discovery", label: "Watchlists", icon: "⭐", group: "Oversigt" },
  { id: "news", label: "News", icon: "📰", group: "Oversigt" },
  { id: "suggestions", label: "Forslag", icon: "✅", group: "Konto" },
  { id: "portfolio", label: "Portfolio", icon: "💼", group: "Konto" },
  { id: "orders", label: "Orders", icon: "📋", group: "Konto" },
  { id: "history", label: "History", icon: "🕑", group: "Konto" },
  { id: "signals", label: "AI Signals", icon: "🧠", group: "Konto" },
  { id: "trading", label: "Auto Trading", icon: "🤖", group: "System" },
  { id: "analytics", label: "Analytics", icon: "📈", group: "System" },
  { id: "setup", label: "Settings", icon: "⚙", group: "System" },
  { id: "audit", label: "Audit log", icon: "📜", group: "System" },
];

const GROUPS = ["Oversigt", "Konto", "System"];

interface Props {
  view: View;
  setView: (v: View) => void;
  monitoring: Monitoring | null;
  automation: AutomationInfo | null;
  alertsCount: number;
  suggestionsCount?: number;
  onChanged: () => void;
  onToast: (m: string) => void;
}

export function Sidebar({ view, setView, monitoring, automation, alertsCount, suggestionsCount = 0, onChanged, onToast }: Props) {
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

      {GROUPS.map((g) => (
        <div key={g} className="nav-group">
          <div className="nav-group-label">{g}</div>
          {NAV.filter((n) => n.group === g).map((n) => (
            <button
              key={n.id}
              className={`navitem ${view === n.id ? "active" : ""}`}
              onClick={() => setView(n.id)}
            >
              <span>{n.icon}</span>
              <span>{n.label}</span>
              {n.id === "trading" && alertsCount > 0 && <span className="count">{alertsCount}</span>}
              {n.id === "suggestions" && suggestionsCount > 0 && <span className="count" style={{ background: "var(--green)" }}>{suggestionsCount}</span>}
            </button>
          ))}
        </div>
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
