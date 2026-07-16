// Shapes returned by the FastAPI backend (see app/api/routes/*).

export interface Position {
  symbol: string;
  quantity: number;
  avg_price: number;
  last_price: number;
  market_value: number;
  unrealized_pnl: number;
}

export interface Portfolio {
  cash: number;
  positions_value: number;
  total_value: number;
  exposure_pct: number;
  drawdown_pct: number;
  kill_switch_engaged: boolean;
  positions: Position[];
  source?: string; // "saxo" when balances come live from Saxo
  currency?: string;
  open_orders?: OpenOrder[];
}

export interface OpenOrder {
  order_id: string;
  symbol: string;
  uic: number;
  side: string; // "Buy" | "Sell"
  quantity: number;
  order_type: string;
  status: string;
  price: number | null;
}

export interface Snapshot {
  ts: string;
  cash: number;
  total_value: number;
  drawdown_pct: number;
}

export interface Signal {
  id: number;
  ts: string;
  symbol: string;
  direction: string;
  quant_score: number;
  news_score: number;
  combined_score: number;
  risk_score: number;
  decision: string;
  quant_rationale: string;
  news_rationale: string;
  risk_rationale: string;
}

export interface AuditEntry {
  id: number;
  ts: string;
  category: string;
  actor: string;
  action: string;
  symbol: string | null;
  message: string;
}

export interface BrokerModeInfo {
  broker_mode: string;
  available_modes: string[];
  saxo_environment: string;
  live_trading_enabled: boolean;
}

export interface BrokerHealth {
  broker: string;
  connected: boolean;
  environment?: string;
  live_enabled?: boolean;
  error?: string;
}

export interface Config {
  environment: string;
  live_trading_enabled: boolean;
  base_currency: string;
  ai_auth_mode: string;
  ai_model: string;
  default_broker_mode: string;
  saxo_environment: string;
}

// ---- v3 ----
export interface Monitoring {
  total_value: number;
  cash: number;
  broker_mode: string;
  kill_switch_engaged: boolean;
  effective_risk: string;
  limits: {
    exposure_pct: number;
    exposure_limit_pct: number;
    exposure_util_pct: number;
    drawdown_pct: number;
    drawdown_limit_pct: number;
    drawdown_util_pct: number;
    daily_loss_pct: number;
    daily_loss_limit_pct: number;
    daily_loss_util_pct: number;
    open_positions: number;
    max_open_positions: number;
  };
  automation: {
    enabled: boolean;
    live_mode: boolean;
    emergency_stopped: boolean;
    interval_seconds: number;
    runs_count: number;
    last_run_at: string | null;
  };
  active_alerts: number;
}

export interface GateCheck {
  name: string;
  passed: boolean;
  detail: string;
}

export interface AutomationInfo {
  state: {
    enabled: boolean;
    live_mode: boolean;
    emergency_stopped: boolean;
    interval_seconds: number;
    universe: string;
    runs_count: number;
    last_run_at: string | null;
  };
  live_gate: { ready: boolean; checks: GateCheck[] };
}

export interface Alert {
  id: number;
  ts: string;
  severity: string;
  kind: string;
  message: string;
  acknowledged: boolean;
}

export interface AttributionRow {
  symbol: string;
  realized_pnl: number;
  unrealized_pnl: number;
  total_pnl: number;
  commission: number;
  closed_trades: number;
  win_rate: number;
}

export interface Attribution {
  total_realized: number;
  total_unrealized: number;
  total_pnl: number;
  total_commission: number;
  per_symbol: AttributionRow[];
}

export interface ComparisonRow {
  strategy: string;
  total_return_pct: number;
  cagr_pct: number;
  sharpe: number;
  max_drawdown_pct: number;
  num_trades: number;
  final_value: number;
}

// ---- v4 ----
export interface SecretStatus {
  set: boolean;
  hint: string;
}

export interface SettingsView {
  ai_auth_mode: string;
  ai_model: string;
  anthropic_auth_token: SecretStatus;
  anthropic_api_key: SecretStatus;
  saxo_environment: string;
  saxo_access_token: SecretStatus;
  saxo_app_key: string;
  saxo_app_secret: SecretStatus;
  saxo_auth_endpoint: string;
  saxo_token_endpoint: string;
  default_broker_mode: string;
  live_trading_enabled: boolean;
  market_data_source: string;
  news_enabled: boolean;
  market_lookback_days: number;
  discovery_enabled: boolean;
  discovery_top_n: number;
  discovery_candidates: string;
  automation_interval_seconds: number;
  automation_universe: string;
  options: Record<string, string[]>;
  persistence: string;
}

export interface DiscoveryCandidate {
  symbol: string;
  score: number;
  momentum: number;
  trend_gap: number;
  avg_volume: number;
  rationale: string;
}
