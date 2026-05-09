export interface Portfolio {
  id: number;
  user_id: number;
  name: string;
  description: string | null;
  broker: "manual" | "ibkr" | "paper";
  currency: string;
  is_active: boolean;
  created_at: string;
  total_value?: number;
  total_cost?: number;
  total_pnl?: number;
  total_pnl_pct?: number;
  day_change?: number;
  day_change_pct?: number;
  position_count?: number;
  initial_cash?: number | null;
  cash_balance?: number | null;
}

export interface Position {
  id: number;
  portfolio_id: number;
  ticker: string;
  asset_type: string;
  quantity: number;
  avg_cost: number;
  currency: string;
  current_price: number | null;
  unrealized_pnl: number | null;
  unrealized_pnl_pct: number | null;
  opened_at: string;
}

export interface Trade {
  id: number;
  portfolio_id: number;
  ticker: string;
  asset_type: string;
  action: "buy" | "sell" | "short" | "cover";
  quantity: number;
  price: number;
  fees: number;
  currency: string;
  notes: string | null;
  traded_at: string;
  external_id: string | null;
}

export interface Signal {
  id: number;
  ticker: string;
  signal_type: "technical" | "insider" | "ai_news" | "options_flow" | "fundamental";
  direction: "bullish" | "bearish" | "neutral";
  strength: number;
  entry_price: number | null;
  stop_loss: number | null;
  take_profit: number | null;
  rationale: string | null;
  indicators: string | null;
  timeframe: string | null;
  expires_at: string | null;
  created_at: string;
}

export interface Alert {
  id: number;
  user_id: number;
  ticker: string;
  alert_type: string;
  threshold_value: number | null;
  message: string | null;
  is_active: boolean;
  is_triggered: boolean;
  triggered_at: string | null;
  acknowledged_at: string | null;
  channels: string;
  created_at: string;
}

export interface WatchlistItem {
  id: number;
  ticker: string;
  notes: string | null;
  created_at: string;
  last_price: number | null;
  last_change: number | null;
  last_change_pct: number | null;
  previous_close: number | null;
  fifty_two_week_high: number | null;
  fifty_two_week_low: number | null;
  pe_ratio: number | null;
  rsi14: number | null;
  price_updated_at: string | null;
}

export interface Quote {
  ticker: string;
  price: number;
  previous_close: number;
  change: number;
  change_pct: number;
  volume: number;
  market_cap: number | null;
  fifty_two_week_high: number;
  fifty_two_week_low: number;
}

export interface Candle {
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface ChatMessage {
  id: number;
  role: "user" | "assistant";
  content: string;
  created_at: string;
}

export interface Conversation {
  id: number;
  title: string;
  portfolio_id: number | null;
  created_at: string;
  updated_at: string;
  messages?: ChatMessage[];
}

export interface UnusualContract {
  strike: number;
  expiry: string;
  volume: number;
  open_interest: number;
  vol_oi_ratio: number;
  premium: number;
  last_price: number;
  itm: boolean;
}

export interface UWFlowItem {
  type: "call" | "put";
  strike: number;
  expiry: string;
  premium: number;
  volume: number;
  open_interest: number;
  is_sweep: boolean;
  is_block: boolean;
  sentiment: string;
  executed_at: string;
}

export interface OptionsFlowSummary {
  ticker: string;
  as_of: string;
  current_price: number | null;
  expirations_used: string[];
  call_volume: number;
  put_volume: number;
  pc_ratio: number | null;
  net_call_premium: number;
  net_put_premium: number;
  unusual_calls: UnusualContract[];
  unusual_puts: UnusualContract[];
  uw_flow?: UWFlowItem[];
}

export interface AccountBalance {
  id: number;
  currency: string;
  balance: number;
}

export interface AccountTransaction {
  id: number;
  account_id: number;
  transaction_type: "deposit" | "withdrawal";
  amount: number;
  currency: string;
  notes: string | null;
  transacted_at: string;
  created_at: string;
}

export interface Account {
  id: number;
  user_id: number;
  name: string;
  description: string | null;
  is_active: boolean;
  created_at: string;
  balances: AccountBalance[];
}

export interface AccountDetail extends Account {
  transactions: AccountTransaction[];
}

export interface LiquidityResponse {
  balances: Record<string, number>;
  total_usd: number;
}

export interface InsiderTrade {
  id: number;
  ticker: string;
  company_name: string | null;
  insider_name: string;
  insider_title: string | null;
  is_director: boolean;
  is_officer: boolean;
  transaction_type: "buy" | "sell" | "gift" | "option_exercise";
  shares: number;
  price_per_share: number | null;
  total_value: number | null;
  filed_at: string;
}

// ── Briefing ──────────────────────────────────────────────────────────────────

export interface BriefingPortfolioItem {
  ticker: string;
  action: "hold" | "trim" | "add" | "watch" | "exit";
  verdict: string;
  reasoning: string;
}

export interface BriefingWatchlistItem {
  ticker: string;
  action: "buy" | "watch" | "avoid";
  verdict: string;
  reasoning: string;
  catalyst: string;
}

export interface BriefingSP500Item {
  ticker: string;
  action: "buy" | "watch" | "avoid";
  verdict: string;
  reasoning: string;
  catalyst: string;
}

export interface BriefingMacroEvent {
  event: string;
  date: string;
  impact: string;
}

export interface SignalOutcome {
  id: number;
  signal_id: number | null;
  ticker: string;
  signal_type: "technical" | "insider" | "ai_news" | "options_flow" | "fundamental" | "earnings_upcoming" | "macro_event" | "cross_impact";
  direction: "bullish" | "bearish" | "neutral";
  strength: number;
  rationale: string | null;
  entry_price: number;
  signal_created_at: string;
  price_1d: number | null;
  price_5d: number | null;
  price_30d: number | null;
  price_90d: number | null;
  snapshot_1d_at: string | null;
  snapshot_5d_at: string | null;
  snapshot_30d_at: string | null;
  snapshot_90d_at: string | null;
}

export interface PerformanceBucket {
  hit_rate: number | null;
  avg_gain_pct: number | null;
  sample_size: number;
}

export type PerformanceTimeframe = "1d" | "5d" | "30d" | "90d";
export type PerformanceByTimeframe = Record<PerformanceTimeframe, PerformanceBucket>;

export interface SignalPerformance {
  total_outcomes: number;
  overall: PerformanceByTimeframe;
  by_signal_type: Record<string, PerformanceByTimeframe>;
  by_direction: Record<string, PerformanceByTimeframe>;
  by_strength: Record<string, PerformanceByTimeframe>;
}

export interface BacktestMetrics {
  n_trades: number;
  wins: number;
  losses: number;
  hit_rate_pct: number;
  total_pnl: number;
  total_return_pct: number;
  avg_trade_pnl: number;
  max_drawdown: number;
  max_drawdown_pct: number;
  first_date: string | null;
  last_exit_date: string | null;
}

export interface BacktestEquityPoint {
  date: string;
  ticker: string;
  signal_type: string;
  direction: string;
  strength: number;
  trade_pnl: number;
  cumulative_pnl: number;
  trade_return_pct: number;
}

export interface BacktestBenchmark {
  return_pct: number | null;
  first_close: number | null;
  last_close: number | null;
}

export interface BacktestResult {
  strategy: Record<string, unknown>;
  metrics: BacktestMetrics;
  equity_curve: BacktestEquityPoint[];
  benchmark: BacktestBenchmark;
}

export interface AggregatedRule {
  id: number;
  direction: "bullish" | "bearish" | "neutral";
  strength: number;
  rationale: string | null;
  indicators: string | null;
  timeframe: string | null;
  created_at: string | null;
  expires_at: string | null;
}

export interface AggregatedCategory {
  ticker: string;
  signal_type: string;
  net_direction: "bullish" | "bearish" | "neutral";
  net_strength: number;
  score: number;
  confidence: "strong" | "moderate" | "mixed";
  bullish_count: number;
  bearish_count: number;
  neutral_count: number;
  rule_count: number;
  rules: AggregatedRule[];
}

export interface AggregatedTicker {
  ticker: string;
  overall_direction: "bullish" | "bearish" | "neutral";
  overall_score: number;
  total_rules: number;
  total_bullish: number;
  total_bearish: number;
  category_count: number;
  categories: AggregatedCategory[];
}

export interface StockDetailsQuote {
  price: number | null;
  previous_close: number | null;
  change: number | null;
  change_pct: number | null;
  day_low: number | null;
  day_high: number | null;
  volume: number;
  avg_volume: number;
  market_cap: number | null;
  fifty_two_week_high: number | null;
  fifty_two_week_low: number | null;
}

export interface StockDetailsValuation {
  pe_trailing: number | null;
  /** Forward P/E using current fiscal year EPS estimate — matches what
   *  most retail platforms show as "Forward P/E". */
  pe_forward_current_fy: number | null;
  /** Forward P/E using next fiscal year EPS estimate (yfinance forwardPE). */
  pe_forward_next_fy: number | null;
  pb_ratio: number | null;
  peg_ratio: number | null;
  ev_ebitda: number | null;
  eps_trailing: number | null;
  eps_current_fy: number | null;
  eps_forward: number | null;
  dividend_yield_pct: number | null;
  payout_ratio_pct: number | null;
}

export interface StockDetailsGrowth {
  revenue_ttm: number | null;
  revenue_growth_pct: number | null;
  earnings_growth_pct: number | null;
  net_margin_pct: number | null;
  operating_margin_pct: number | null;
  roe_pct: number | null;
  free_cash_flow: number | null;
  debt_to_equity: number | null;
}

export interface StockDetailsTechnicals {
  rsi14: number | null;
  macd_signal: "bullish" | "bearish" | "neutral" | null;
  sma50: number | null;
  sma200: number | null;
  above_sma50: boolean | null;
  above_sma200: boolean | null;
  beta: number | null;
}

export interface StockDetailsIVAnalytics {
  atm_iv: number;
  hv_20: number | null;
  iv_hv_ratio: number | null;
  implied_move_pct: number | null;
  skew: number | null;
  days_to_earnings: number | null;
  expiry_used: string;
  days_to_expiry: number;
  current_price: number;
}

export interface StockDetailsAnalyst {
  recommendation_key: string | null;
  recommendation_mean: number | null;
  n_analysts: number | null;
  target_mean: number | null;
  target_high: number | null;
  target_low: number | null;
  upside_pct: number | null;
}

export interface StockDetails {
  ticker: string;
  name: string;
  asset_type: string;
  exchange: string | null;
  sector: string | null;
  industry: string | null;
  currency: string;
  quote: StockDetailsQuote;
  valuation: StockDetailsValuation | null;
  growth: StockDetailsGrowth | null;
  technicals: StockDetailsTechnicals;
  iv_analytics: StockDetailsIVAnalytics | null;
  analyst: StockDetailsAnalyst | null;
  options_listed: boolean;
}

export interface NewsItemDetail {
  id: number | null;
  ticker: string;
  headline: string;
  url: string | null;
  source: string | null;
  sentiment_score: number | null;
  ai_signal: string | null;
  published_at: string | null;
}

export interface CalendarEvent {
  type: "earnings" | "ex_dividend" | "macro";
  ticker: string | null;
  date: string;        // ISO YYYY-MM-DD
  title: string;
  details: string | null;
  in_portfolio: boolean;
}

export interface InsiderTradeDetail {
  id: number;
  insider_name: string;
  insider_title: string | null;
  transaction_type: string | null;
  shares: number | null;
  price_per_share: number | null;
  total_value: number | null;
  filed_at: string | null;
  transaction_date: string | null;
}

export interface BriefingSession {
  state: "open" | "pre_market" | "after_hours" | "closed_overnight" | "closed_weekend" | "closed_holiday";
  is_trading_day: boolean;
  is_weekend: boolean;
  is_holiday: boolean;
  current_et: string;
  next_trading_day: string;
  description: string;
}

export interface BriefingContent {
  overall_sentiment: "bullish" | "neutral" | "cautious" | "bearish";
  market_context: string;
  macro_events: BriefingMacroEvent[];
  portfolio: BriefingPortfolioItem[];
  watchlist_opportunities: BriefingWatchlistItem[];
  sp500_opportunities: BriefingSP500Item[];
  summary_bullets: string[];
  session?: BriefingSession;
}

export interface Briefing {
  id: number;
  briefing_date: string;
  overall_sentiment: string;
  summary: string | null;
  content_json: string | null;
  content: BriefingContent | null;
  generated_at: string;
}
