import type {
  Alert,
  Attribution,
  AuditEntry,
  AutomationInfo,
  BrokerHealth,
  BrokerModeInfo,
  ComparisonRow,
  Config,
  DiscoveryCandidate,
  DiscoveryStatus,
  MarketHours,
  Monitoring,
  Performance,
  Portfolio,
  Realized,
  SettingsView,
  Signal,
  Snapshot,
  StreamingStatus,
} from "./types";

// Base URL for the API. Override with VITE_API_BASE at build time. Defaults:
// dev server (port 5173) talks to the local FastAPI on :8000; when the UI is
// SERVED BY the backend itself (server deployment), use the same origin.
const BASE =
  import.meta.env.VITE_API_BASE ??
  (window.location.port === "5173" ? "http://localhost:8000" : "");

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`);
  if (!res.ok) throw new Error(`GET ${path} -> ${res.status}`);
  return res.json() as Promise<T>;
}

// Optional API key (set once in the browser console when the backend enforces it):
//   localStorage.setItem("aitp_api_key", "<your key>")
const authHeaders = (): Record<string, string> => {
  const k = localStorage.getItem("aitp_api_key");
  return k ? { "X-API-Key": k } : {};
};

async function post<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { method: "POST", headers: authHeaders() });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail ?? `POST ${path} -> ${res.status}`);
  }
  return res.json() as Promise<T>;
}

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail ?? `POST ${path} -> ${res.status}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  config: () => get<Config>("/config"),
  portfolio: () => get<Portfolio>("/portfolio"),
  snapshots: () => get<Snapshot[]>("/portfolio/snapshots"),
  signals: () => get<Signal[]>("/signals?limit=25"),
  audit: (limit = 150) => get<AuditEntry[]>(`/audit?limit=${limit}`),
  brokerMode: () => get<BrokerModeInfo>("/control/broker-mode"),
  brokerHealth: () => get<BrokerHealth>("/control/broker-health"),
  setBrokerMode: (mode: string) =>
    post<{ broker_mode: string }>(`/control/broker-mode?mode=${mode}`),
  setKillSwitch: (engaged: boolean) =>
    post<{ kill_switch_engaged: boolean }>(
      `/control/kill-switch?engaged=${engaged}`,
    ),
  runCycle: (symbols: string[]) =>
    postJson<{ evaluated: number; approved: string[] }>("/signals/run-cycle", {
      symbols,
    }),

  // ---- v3 ----
  monitoring: () => get<Monitoring>("/automation/monitoring"),
  automation: () => get<AutomationInfo>("/automation"),
  automationStart: () => post<unknown>("/automation/start"),
  automationStop: () => post<unknown>("/automation/stop"),
  automationTick: () => post<{ ran: boolean; reason?: string }>("/automation/tick"),
  configureAutomation: (body: { interval_seconds?: number; universe?: string; live_mode?: boolean }) =>
    postJson<unknown>("/automation/configure", body),
  emergencyStop: () => post<{ emergency_stopped: boolean; flattened: string[] }>("/automation/emergency-stop"),
  clearEmergency: () => post<unknown>("/automation/clear-emergency"),
  alerts: (onlyActive = true) => get<Alert[]>(`/alerts?only_active=${onlyActive}&limit=40`),
  acknowledgeAlerts: () => post<{ acknowledged: number }>("/alerts/acknowledge"),
  runChecks: () => post<{ raised: string[] }>("/alerts/check"),
  attribution: () => get<Attribution>("/portfolio/attribution"),
  compare: (symbol: string) =>
    get<{ symbol: string; bars: number; results: ComparisonRow[] }>(
      `/backtest/compare?symbol=${symbol}`,
    ),
  getSettings: () => get<SettingsView>("/settings"),
  updateSettings: (body: Record<string, unknown>) =>
    postJson<{ updated: string[]; settings: SettingsView }>("/settings", body),
  discovery: (topN?: number) =>
    get<{ candidates: DiscoveryCandidate[] }>(`/discovery${topN ? `?top_n=${topN}` : ""}`),
  applyDiscovery: (topN?: number) =>
    post<{ universe: string[] }>(`/discovery/apply${topN ? `?top_n=${topN}` : ""}`),
  discoveryStatus: () => get<DiscoveryStatus>("/discovery/status"),
  marketHours: () => get<MarketHours>("/automation/market-hours"),
  streamingStatus: () => get<StreamingStatus>("/control/streaming/status"),
  streamingStart: () => post<{ started: boolean; error?: string; symbols?: string[] }>("/control/streaming/start"),
  streamingStop: () => post<{ stopped: boolean }>("/control/streaming/stop"),
  setAllocation: (amount: number) =>
    post<{ cash: number }>(`/control/allocation?amount=${amount}`),
  closePosition: (symbol: string) =>
    post<{ closed: string; quantity: number }>(`/control/close-position?symbol=${encodeURIComponent(symbol)}`),
  positionHistory: (symbol: string) =>
    get<{ symbol: string; base: string; closes: number[] }>(`/portfolio/history?symbol=${encodeURIComponent(symbol)}`),
  realized: () => get<Realized>("/portfolio/realized"),
  performance: () => get<Performance>("/portfolio/performance"),
  saxoTest: () =>
    get<{ connected: boolean; environment?: string; error?: string; balance?: Record<string, unknown>; account_key?: string }>(
      "/control/saxo-test",
    ),
  cancelSaxoOrder: (orderId?: string) =>
    post<{ cancelled: string[] }>(`/control/saxo-cancel${orderId ? `?order_id=${orderId}` : ""}`),
  saxoOauthStatus: () =>
    get<{ configured: boolean; connected: boolean; access_token_expires_in: number; environment: string }>(
      "/control/saxo/oauth-status",
    ),
  saxoLoginUrl: () => `${BASE || window.location.origin}/control/saxo/login`,
  saxoSelfTest: (symbol: string, placeOrder = false, quantity = 1) =>
    get<{ ok: boolean; symbol: string; placed_order: boolean; steps: { name: string; ok: boolean; detail?: unknown; error?: string }[] }>(
      `/control/saxo-selftest?symbol=${encodeURIComponent(symbol)}&place_order=${placeOrder}&quantity=${quantity}`,
    ),
};
