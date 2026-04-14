export interface Portfolio {
  id: number;
  user_id: number;
  name: string;
  description: string | null;
  broker: "manual" | "ibkr";
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
