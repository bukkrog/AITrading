import { useState } from "react";
import { api } from "../api";
import type { BrokerHealth, BrokerModeInfo, Portfolio } from "../types";

interface Props {
  portfolio: Portfolio;
  brokerMode: BrokerModeInfo;
  brokerHealth: BrokerHealth | null;
  onChanged: () => void;
  onToast: (msg: string) => void;
}

export function Controls({ portfolio, brokerMode, brokerHealth, onChanged, onToast }: Props) {
  const [symbols, setSymbols] = useState("NOVO,MAERSK,ORSTED,DSV,CARLB,GMAB");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function guard(fn: () => Promise<void>) {
    setBusy(true);
    setErr(null);
    try {
      await fn();
      onChanged();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const runCycle = () =>
    guard(async () => {
      const list = symbols.split(",").map((s) => s.trim().toUpperCase()).filter(Boolean);
      const res = await api.runCycle(list);
      onToast(`Cycle done — evaluated ${res.evaluated}, approved ${res.approved.length}`);
    });

  const toggleKill = () =>
    guard(async () => {
      await api.setKillSwitch(!portfolio.kill_switch_engaged);
    });

  const changeMode = (mode: string) =>
    guard(async () => {
      await api.setBrokerMode(mode);
      onToast(`Broker mode set to ${mode}`);
    });

  return (
    <div className="card">
      <h2>Controls</h2>
      <div className="controls">
        <div className="control-row">
          <label className="field">Broker</label>
          <select
            value={brokerMode.broker_mode}
            disabled={busy}
            onChange={(e) => changeMode(e.target.value)}
          >
            {brokerMode.available_modes.map((m) => (
              <option key={m} value={m}>
                {m === "saxo" ? `saxo (${brokerMode.saxo_environment})` : m}
              </option>
            ))}
          </select>
        </div>

        {brokerHealth && (
          <div className="control-row">
            <label className="field">Broker status</label>
            <span className={`badge ${brokerHealth.connected ? "ok" : ""}`}>
              {brokerHealth.connected ? "connected" : "not connected"}
            </span>
          </div>
        )}

        <div className="control-row">
          <label className="field">Kill switch</label>
          <button
            className={portfolio.kill_switch_engaged ? "warn" : "danger"}
            disabled={busy}
            onClick={toggleKill}
          >
            {portfolio.kill_switch_engaged ? "Release" : "Engage"}
          </button>
        </div>

        <div>
          <label className="field">Universe (comma-separated)</label>
          <input
            type="text"
            value={symbols}
            onChange={(e) => setSymbols(e.target.value)}
            style={{ marginTop: 6 }}
          />
        </div>
        <button onClick={runCycle} disabled={busy}>
          {busy ? "Running…" : "Run paper cycle"}
        </button>

        {err && <div className="error">{err}</div>}
      </div>
    </div>
  );
}
