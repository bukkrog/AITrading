import { useState } from "react";
import { api } from "../api";
import type { Alert } from "../types";

export function AlertsPanel({ alerts, onChanged }: { alerts: Alert[]; onChanged: () => void }) {
  const [busy, setBusy] = useState(false);

  async function act(fn: () => Promise<unknown>) {
    setBusy(true);
    try {
      await fn();
      onChanged();
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <h2 style={{ margin: 0 }}>Alerts</h2>
        <div className="btn-row">
          <button className="secondary" disabled={busy} onClick={() => act(api.runChecks)}>Run checks</button>
          <button className="secondary" disabled={busy || alerts.length === 0} onClick={() => act(api.acknowledgeAlerts)}>
            Acknowledge all
          </button>
        </div>
      </div>
      {alerts.length === 0 ? (
        <p className="muted" style={{ marginTop: 12 }}>No active alerts.</p>
      ) : (
        <div style={{ marginTop: 12 }}>
          {alerts.map((a) => (
            <div key={a.id} className={`alert ${a.severity}`}>
              <div className="kind">{a.severity} · {a.kind} · {new Date(a.ts).toLocaleTimeString()}</div>
              <div>{a.message}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
