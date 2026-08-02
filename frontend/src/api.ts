import type {
  Alert,
  Attribution,
  AuditEntry,
  AutomationInfo,
  BellwetherRisk,
  BrokerHealth,
  BrokerModeInfo,
  ComparisonRow,
  Concentration,
  Config,
  Costs,
  ScenarioStress,
  DiscoveryCandidate,
  DiscoveryStatus,
  MarketHours,
  MarketIndex,
  Monitoring,
  PositionAssessment,
  Performance,
  Portfolio,
  Realized,
  SettingsView,
  Signal,
  Snapshot,
  StreamingStatus,
  Suggestion,
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
  testAlert: () => post<{ sent: boolean; webhook_configured: boolean }>("/alerts/test"),
  attribution: () => get<Attribution>("/portfolio/attribution"),
  analyzeStock: (symbol: string) =>
    get<{
      symbol: string; error?: string;
      price?: number; week52_high?: number; week52_low?: number;
      from_high_pct?: number; from_low_pct?: number;
      sma20?: number; sma20_pct?: number; sma50?: number; sma50_pct?: number; sma200?: number; sma200_pct?: number;
      mom_12_1_pct?: number; ret_5d_pct?: number; ann_vol_pct?: number;
      rsi14?: number; rsi2?: number; factor_score?: number; buy_gate?: number;
      dollar_volume?: number; next_earnings?: string; long_count?: number;
      signals?: { strategy: string; long: boolean; fresh: string }[];
    }>(`/backtest/analyze?symbol=${encodeURIComponent(symbol)}`),
  manualBuy: (symbol: string, quantity: number) =>
    post<{ placed: boolean; symbol: string; quantity?: number; requested: number; price?: number; stop_price?: number | null; capped?: boolean; reasons: string[] }>(
      `/control/manual-buy?symbol=${encodeURIComponent(symbol)}&quantity=${quantity}`),
  manualSell: (symbol: string, quantity: number) =>
    post<{ placed?: boolean; closed?: string; symbol?: string; quantity?: number; requested?: number; price?: number; capped?: boolean }>(
      `/control/manual-sell?symbol=${encodeURIComponent(symbol)}&quantity=${quantity}`),
  marketQuote: (symbol: string) =>
    get<{ symbol: string; source: string; bid: number | null; ask: number | null; mid: number | null; spread: number | null; bid_size?: number | null; ask_size?: number | null }>(
      `/market/quote?symbol=${encodeURIComponent(symbol)}`),
  entryMode: () => get<{ entry_mode: "suggest" | "auto" }>("/control/entry-mode"),
  setEntryMode: (mode: "suggest" | "auto") =>
    post<{ entry_mode: string }>(`/control/entry-mode?mode=${mode}`),
  suggestions: () =>
    get<{ open: Suggestion[]; resolved: Suggestion[]; open_count: number }>("/suggestions"),
  approveSuggestion: (id: number) => post<Suggestion>(`/suggestions/${id}/approve`),
  rejectSuggestion: (id: number) => post<Suggestion>(`/suggestions/${id}/reject`),
  marketNews: (symbols?: string) =>
    get<{ items: { symbol: string; title: string; publisher: string | null; url: string | null; published: string | null; owned?: boolean; sentiment?: { score: number; label: "good" | "bad" | "neutral"; pos: number; neg: number } }[]; symbols: string[]; owned?: string[]; reason: string | null }>(
      `/market/news${symbols ? `?symbols=${encodeURIComponent(symbols)}` : ""}`),
  tradeLog: (limit = 80) =>
    get<{ ts: string; symbol: string; side: string; quantity: number; price: number; value: number; commission: number; reason: string }[]>(
      `/trades/log?limit=${limit}`,
    ),
  compare: (symbol: string) =>
    get<{ symbol: string; bars: number; results: ComparisonRow[] }>(
      `/backtest/compare?symbol=${symbol}`,
    ),
  getSettings: () => get<SettingsView>("/settings"),
  sizingRecommendation: () =>
    get<{
      account_currency: string;
      total_value_native: number;
      total_value_dkk: number;
      to_dkk_rate: number;
      min_notional_dkk: number;
      target_roundtrip_cost_pct: number;
      recommended: {
        min_trade_notional: number;
        risk_max_open_positions: number;
        risk_max_position_pct: number;
        risk_max_risk_per_trade_pct: number;
        discovery_top_n: number;
      };
      rationale: string[];
    }>("/settings/sizing-recommendation"),
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
  activity: () =>
    get<{ current: { text: string; seconds_ago: number }; recent: { text: string; seconds_ago: number }[] }>(
      "/automation/activity",
    ),
  saxoOauthStatus: () =>
    get<{ configured: boolean; connected: boolean; access_token_expires_in: number; environment: string }>(
      "/control/saxo/oauth-status",
    ),
  indices: () => get<{ indices: MarketIndex[] }>("/market/indices"),
  marketHistory: (symbol: string, range: string) =>
    get<{ symbol: string; range: string; closes: number[]; dates: string[]; intraday: boolean; bars: { t: number | string; o: number; h: number; l: number; c: number }[] }>(`/market/history?symbol=${encodeURIComponent(symbol)}&range=${range}`),
  assessment: () => get<{ assessments: PositionAssessment[]; exit_quant_floor: number }>("/portfolio/assessment"),
  costs: () => get<Costs>("/portfolio/costs"),
  concentration: () => get<Concentration>("/portfolio/concentration"),
  scenarioStress: () => get<ScenarioStress>("/portfolio/scenario"),
  bellwetherRisk: () => get<BellwetherRisk>("/portfolio/bellwether-risk"),
  saxoLoginUrl: () => `${BASE || window.location.origin}/control/saxo/login`,
  saxoSelfTest: (symbol: string, placeOrder = false, quantity = 1) =>
    get<{ ok: boolean; symbol: string; placed_order: boolean; steps: { name: string; ok: boolean; detail?: unknown; error?: string }[] }>(
      `/control/saxo-selftest?symbol=${encodeURIComponent(symbol)}&place_order=${placeOrder}&quantity=${quantity}`,
    ),
};
