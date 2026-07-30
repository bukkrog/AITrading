// Shapes returned by the FastAPI backend (see app/api/routes/*).

export interface Position {
  symbol: string;
  quantity: number;
  avg_price: number;
  last_price: number;
  market_value: number;
  unrealized_pnl: number;
  pnl_pct?: number;
  stop_price?: number | null;
  stop_distance_pct?: number | null;
  exchange?: string;
  region?: string;
}

export interface Portfolio {
  cash: number;
  positions_value: number;
  total_value: number;
  margin_available?: number;
  dkk_rate?: number;
  total_value_dkk?: number;
  cash_dkk?: number;
  margin_available_dkk?: number;
  positions_value_dkk?: number;
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

export interface RealizedRow {
  symbol: string;
  realized_pnl: number;
  trades: number;
}

export interface Realized {
  source: string;
  currency?: string;
  per_symbol: RealizedRow[];
  total: number;
  error?: string;
}

export interface Performance {
  source: string;
  currency?: string;
  trades_today: number;
  realized: { today: number | null; week: number | null; month: number | null };
  realized_dkk?: { today: number | null; week: number | null; month: number | null };
  dkk_rate?: number;
  total_value?: number;
  error?: string;
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
  reject_reason: string;
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
  active_strategy: string;
  quant_score_threshold: number;
  news_score_threshold: number;
  news_gate_mode: string;
  saxo_redirect_uri: string;
  stop_loss_pct: number;
  take_profit_pct: number;
  trailing_stop_pct: number;
  risk_max_open_positions: number;
  risk_max_position_pct: number;
  risk_max_total_exposure_pct: number;
  risk_max_risk_per_trade_pct: number;
  risk_max_daily_loss_pct: number;
  risk_max_total_drawdown_pct: number;
  market_hours_enabled: boolean;
  enforce_loss_halts: boolean;
  market_data_source: string;
  news_enabled: boolean;
  market_lookback_days: number;
  market_horizon_minutes: number;
  commission_per_trade: number;
  commission_pct: number;
  slippage_bps: number;
  trade_cooldown_minutes: number;
  min_trade_notional: number;
  discovery_enabled: boolean;
  discovery_top_n: number;
  discovery_candidates: string;
  discovery_sources: string;
  discovery_max_pool: number;
  discovery_open_market_only: boolean;
  discovery_preopen_minutes: number;
  discovery_region_weights: string;
  streaming_autostart: boolean;
  auto_size_from_capital: boolean;
  risk_appetite: number;
  circuit_breaker_enabled: boolean;
  circuit_breaker_win_rate: number;
  circuit_breaker_min_trades: number;
  overnight_news_watch: boolean;
  automation_interval_seconds: number;
  automation_universe: string;
  options: Record<string, string[]>;
  persistence: string;
}

export interface ExchangeStatus {
  key: string;
  name: string;
  open: boolean;
  local_time: string;
  hours: string;
  next_open: string | null;
  next_open_local: string | null;
}

export interface Concentration {
  proxies: { label: string; proxy: string; is_sector: boolean; exposure_pct: number; covered_pct: number }[];
  concentration_pct: number;
  n_holdings: number;
  warning: string | null;
  error?: string;
}

export interface ScenarioStress {
  scenarios: { label: string; proxy: string; shock_pct: number; pnl: number; pnl_pct: number; covered_pct: number }[];
  n_holdings: number;
  error?: string;
}

export interface BellwetherRisk {
  bellwethers: { symbol: string; sector_proxy: string; next_earnings: string | null; days_until: number | null; imminent: boolean; news_score: number | null }[];
  note?: string;
  watched?: string[];
  exposed_sectors?: { label: string; proxy: string; exposure_pct: number }[];
}

export interface PositionAssessment {
  symbol: string;
  quant_score: number | null;
  verdict: "HOLD" | "SELL" | "UNKNOWN";
  reason: string;
  last_price?: number;
  avg_price?: number;
  pnl_pct?: number | null;
  stop_price?: number | null;
  take_profit_price?: number | null;
  trailing_stop_price?: number | null;
}

export interface MarketIndex {
  symbol: string;
  name: string;
  last: number | null;
  change_pct: number | null;
  spark: number[];
}

export interface MarketHours {
  any_open: boolean;
  all_open: boolean;
  exchanges: ExchangeStatus[];
  enabled: boolean;
  paused: boolean;
}

export interface StreamingStatus {
  running: boolean;
  connected?: boolean;
  uics?: number[];
  prices_by_symbol?: Record<string, number>;
  messages_received?: number;
  reconnects?: number;
  last_error?: string | null;
}

export interface DiscoveryStatus {
  last_scan_at: string | null;
  next_earliest_at: string | null;
  ttl_seconds: number;
  sources: string[];
  enabled: boolean;
  open_market_only?: boolean;
}

export interface DiscoveryCandidate {
  symbol: string;
  score: number;
  momentum: number;
  trend_gap: number;
  avg_volume: number;
  rationale: string;
}
