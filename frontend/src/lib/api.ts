import axios from "axios";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8002";

export const api = axios.create({
  baseURL: `${API_URL}/api/v1`,
  headers: { "Content-Type": "application/json" },
});

// FastAPI expects repeated params for lists: tickers=AAPL&tickers=TSLA
// Axios default sends: tickers[]=AAPL&tickers[]=TSLA (causes 422)
api.defaults.paramsSerializer = {
  serialize: (params: Record<string, unknown>) => {
    const parts: string[] = [];
    for (const key of Object.keys(params)) {
      const val = params[key];
      if (Array.isArray(val)) {
        val.forEach((v) => parts.push(`${key}=${encodeURIComponent(v)}`));
      } else if (val !== undefined && val !== null) {
        parts.push(`${key}=${encodeURIComponent(String(val))}`);
      }
    }
    return parts.join("&");
  },
};

// Attach auth token to every request
api.interceptors.request.use((config) => {
  const token = localStorage.getItem("access_token");
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

// Auto-refresh on 401
api.interceptors.response.use(
  (res) => res,
  async (error) => {
    const original = error.config;
    if (error.response?.status === 401 && !original._retry) {
      original._retry = true;
      const refresh = localStorage.getItem("refresh_token");
      if (refresh) {
        try {
          const { data } = await axios.post(`${API_URL}/api/v1/auth/refresh`, {
            refresh_token: refresh,
          });
          localStorage.setItem("access_token", data.access_token);
          localStorage.setItem("refresh_token", data.refresh_token);
          original.headers.Authorization = `Bearer ${data.access_token}`;
          return api(original);
        } catch {
          localStorage.clear();
          window.location.href = "/login";
        }
      }
    }
    return Promise.reject(error);
  }
);

// ── Typed API calls ───────────────────────────────────────────────────────────

export const authApi = {
  login: (email: string, password: string) =>
    api.post("/auth/login", { email, password }),
  register: (email: string, password: string, full_name?: string) =>
    api.post("/auth/register", { email, password, full_name }),
  me: () => api.get("/users/me"),
  updateMe: (data: { full_name?: string; current_password?: string; password?: string; telegram_chat_id?: string }) =>
    api.patch("/users/me", data),
  testEmail: () => api.post("/users/me/test-email"),
  testTelegram: () => api.post("/users/me/test-telegram"),
};

export const portfolioApi = {
  list: () => api.get("/portfolios/"),
  get: (id: number) => api.get(`/portfolios/${id}`),
  create: (data: object) => api.post("/portfolios/", data),
  update: (id: number, data: object) => api.patch(`/portfolios/${id}`, data),
  positions: (id: number) => api.get(`/portfolios/${id}/positions`),
  trades: (id: number) => api.get(`/portfolios/${id}/trades`),
  addTrade: (id: number, data: object) => api.post(`/portfolios/${id}/trades`, data),
  updateTrade: (id: number, tradeId: number, data: object) =>
    api.patch(`/portfolios/${id}/trades/${tradeId}`, data),
  deleteTrade: (id: number, tradeId: number) =>
    api.delete(`/portfolios/${id}/trades/${tradeId}`),
  paperTrade: (id: number, data: { ticker: string; action: "buy" | "sell"; quantity: number }) =>
    api.post(`/portfolios/${id}/paper-trade`, data),
  importCsv: (id: number, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return api.post(`/portfolios/${id}/import-csv`, form, {
      headers: { "Content-Type": "multipart/form-data" },
    });
  },
};

export const marketApi = {
  quote: (ticker: string) => api.get(`/market/quote/${ticker}`),
  quotes: (tickers: string[]) =>
    api.get("/market/quotes", { params: { tickers } }),
  history: (ticker: string, period = "3mo", interval = "1d") =>
    api.get(`/market/history/${ticker}`, { params: { period, interval } }),
  search: (q: string) => api.get("/market/search", { params: { q } }),
  currency: (ticker: string) => api.get(`/market/currency/${ticker}`),
  fx: (from: string, to: string) => api.get("/market/fx", { params: { from, to } }),
  optionsFlow: (ticker: string) => api.get(`/market/options/${ticker}`),
  heatmap: () => api.get("/market/heatmap"),
};

export const signalsApi = {
  list: (params?: object) => api.get("/signals/", { params }),
  scan: (ticker: string, includeAi = false) =>
    api.post(`/signals/scan/${ticker}`, null, { params: { include_ai: includeAi } }),
  insider: (params?: object) => api.get("/signals/insider", { params }),
  performance: () => api.get("/signals/performance"),
  outcomes: (limit = 50) => api.get("/signals/outcomes", { params: { limit } }),
  aggregated: (params?: { ticker?: string; signal_type?: string; limit?: number }) =>
    api.get("/signals/aggregated", { params }),
  aggregatedByTicker: (limit = 100) =>
    api.get("/signals/aggregated/by-ticker", { params: { limit } }),
  backfillOutcomes: (limit?: number) =>
    api.post("/signals/outcomes/backfill", null, { params: limit ? { limit } : {} }),
  backtest: (data: {
    signal_type?: string | null;
    direction?: string | null;
    min_strength?: number;
    hold_period?: string;
    capital_per_trade?: number;
    ticker?: string | null;
  }) => api.post("/signals/backtest", data),
};

export const advisorApi = {
  chat: (message: string, portfolio_id?: number, conversation_id?: number) =>
    api.post("/advisor/chat", { message, portfolio_id, conversation_id }),
  conversations: () => api.get("/advisor/conversations"),
  getConversation: (id: number) => api.get(`/advisor/conversations/${id}`),
  deleteConversation: (id: number) => api.delete(`/advisor/conversations/${id}`),
  portfolioReview: (portfolio_id: number) =>
    api.get(`/advisor/portfolio-review/${portfolio_id}`),
  newsDigest: (ticker?: string) =>
    api.get("/advisor/news-digest", { params: ticker ? { ticker } : {} }),
};

export const alertsApi = {
  list: () => api.get("/alerts/"),
  check: () => api.post("/alerts/check"),
  create: (data: object) => api.post("/alerts/", data),
  update: (id: number, data: object) => api.patch(`/alerts/${id}`, data),
  acknowledge: (id: number) => api.post(`/alerts/${id}/acknowledge`),
  rearm: (id: number) => api.post(`/alerts/${id}/rearm`),
  delete: (id: number) => api.delete(`/alerts/${id}`),
};

export const accountsApi = {
  list: () => api.get("/accounts/"),
  get: (id: number) => api.get(`/accounts/${id}`),
  create: (data: { name: string; description?: string }) => api.post("/accounts/", data),
  update: (id: number, data: object) => api.patch(`/accounts/${id}`, data),
  delete: (id: number) => api.delete(`/accounts/${id}`),
  deposit: (id: number, data: { amount: number; currency: string; notes?: string; transacted_at?: string }) =>
    api.post(`/accounts/${id}/deposit`, data),
  withdraw: (id: number, data: { amount: number; currency: string; notes?: string; transacted_at?: string }) =>
    api.post(`/accounts/${id}/withdraw`, data),
  transactions: (id: number) => api.get(`/accounts/${id}/transactions`),
  liquidity: () => api.get("/accounts/liquidity"),
};

export const watchlistApi = {
  list: () => api.get("/watchlists/"),
  create: (name: string) => api.post("/watchlists/", { name }),
  addItem: (id: number, ticker: string, notes?: string) =>
    api.post(`/watchlists/${id}/items`, { ticker, notes }),
  removeItem: (id: number, ticker: string) =>
    api.delete(`/watchlists/${id}/items/${ticker}`),
};

export const briefingApi = {
  today: () => api.get("/briefing/today"),
  regenerate: () => api.post("/briefing/regenerate"),
  history: (limit = 30) => api.get("/briefing/history", { params: { limit } }),
  get: (id: number) => api.get(`/briefing/${id}`),
  delete: (id: number) => api.delete(`/briefing/${id}`),
};
