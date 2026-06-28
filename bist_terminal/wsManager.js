import { WebSocket, WebSocketServer } from "ws";

const FINNHUB_WS_BASE = "wss://ws.finnhub.io";
const DEFAULT_MAX_SUBSCRIPTIONS = 50;
const DEFAULT_FRONTEND_PATH = "/ws/quotes";
const HEARTBEAT_INTERVAL_MS = 30_000;
const STALE_CLIENT_MS = 75_000;

function now() {
  return Date.now();
}

function normalizeSymbol(symbol) {
  return String(symbol || "").trim().toUpperCase();
}

function normalizeSymbols(symbols) {
  if (!Array.isArray(symbols)) return [];
  return [...new Set(symbols.map(normalizeSymbol).filter(Boolean))];
}

function safeJsonParse(raw) {
  try {
    return JSON.parse(String(raw || ""));
  } catch {
    return null;
  }
}

function sendJson(ws, payload) {
  if (ws?.readyState !== WebSocket.OPEN) return false;
  ws.send(JSON.stringify(payload));
  return true;
}

export class FinnhubWsManager {
  constructor({
    server = null,
    port = null,
    path = DEFAULT_FRONTEND_PATH,
    apiKey = process.env.FINNHUB_API_KEY,
    maxFinnhubSubscriptions = DEFAULT_MAX_SUBSCRIPTIONS,
    logger = console,
  } = {}) {
    if (!apiKey) {
      throw new Error("FINNHUB_API_KEY is required for FinnhubWsManager");
    }

    this.apiKey = apiKey;
    this.path = path;
    this.maxFinnhubSubscriptions = Math.max(1, Math.min(50, Number(maxFinnhubSubscriptions) || 50));
    this.logger = logger;

    this.frontendWss = server
      ? new WebSocketServer({ server, path })
      : new WebSocketServer({ port: Number(port) || 8081, path });

    this.finnhubWs = null;
    this.finnhubConnected = false;
    this.closed = false;
    this.reconnectAttempt = 0;
    this.reconnectTimer = null;
    this.heartbeatTimer = null;

    this.activeSymbols = new Set();
    this.symbolMeta = new Map();
    this.clientSymbols = new Map();

    this.frontendWss.on("connection", (ws, req) => this.handleFrontendConnection(ws, req));
    this.frontendWss.on("error", (err) => this.logger.error("[WSM] frontend server error", err));

    this.connectFinnhub();
    this.startHeartbeat();
  }

  getMeta(symbol) {
    const s = normalizeSymbol(symbol);
    if (!this.symbolMeta.has(s)) {
      this.symbolMeta.set(s, {
        symbol: s,
        clientCount: 0,
        requestCount: 0,
        lastRequestedAt: 0,
        lastTradeAt: 0,
        evictedCount: 0,
      });
    }
    return this.symbolMeta.get(s);
  }

  touchSymbol(symbol) {
    const meta = this.getMeta(symbol);
    meta.lastRequestedAt = now();
    meta.requestCount += 1;
    return meta;
  }

  handleFrontendConnection(ws, req) {
    ws.isAlive = true;
    ws.connectedAt = now();
    ws.lastSeenAt = now();
    this.clientSymbols.set(ws, new Set());

    sendJson(ws, {
      type: "ready",
      path: this.path,
      maxFinnhubSubscriptions: this.maxFinnhubSubscriptions,
      activeSymbols: [...this.activeSymbols],
    });

    ws.on("pong", () => {
      ws.isAlive = true;
      ws.lastSeenAt = now();
    });

    ws.on("message", (raw) => {
      ws.lastSeenAt = now();
      const msg = safeJsonParse(raw);
      if (!msg || typeof msg !== "object") {
        sendJson(ws, { type: "error", error: "invalid_json" });
        return;
      }
      this.handleFrontendMessage(ws, msg);
    });

    ws.on("close", () => this.cleanupClient(ws));
    ws.on("error", () => this.cleanupClient(ws));
  }

  handleFrontendMessage(ws, msg) {
    const action = String(msg.action || "").toLowerCase();
    const symbols = normalizeSymbols(msg.symbols);

    if (action === "watch" || action === "subscribe") {
      const accepted = [];
      const evicted = [];
      for (const symbol of symbols) {
        this.addClientSymbol(ws, symbol);
        const evictedSymbol = this.ensureFinnhubSubscription(symbol);
        if (evictedSymbol) evicted.push(evictedSymbol);
        accepted.push(symbol);
      }
      sendJson(ws, {
        type: "watch_ack",
        symbols: accepted,
        evicted,
        activeSymbols: [...this.activeSymbols],
      });
      return;
    }

    if (action === "unwatch" || action === "unsubscribe") {
      const removed = [];
      for (const symbol of symbols) {
        if (this.removeClientSymbol(ws, symbol)) removed.push(symbol);
      }
      sendJson(ws, { type: "unwatch_ack", symbols: removed, activeSymbols: [...this.activeSymbols] });
      return;
    }

    if (action === "list") {
      sendJson(ws, {
        type: "active_symbols",
        activeSymbols: [...this.activeSymbols],
        count: this.activeSymbols.size,
      });
      return;
    }

    sendJson(ws, { type: "error", error: "unknown_action", action });
  }

  addClientSymbol(ws, symbol) {
    const s = normalizeSymbol(symbol);
    if (!s) return;

    const set = this.clientSymbols.get(ws) || new Set();
    if (!set.has(s)) {
      set.add(s);
      this.clientSymbols.set(ws, set);
      this.getMeta(s).clientCount += 1;
    }
    this.touchSymbol(s);
  }

  removeClientSymbol(ws, symbol) {
    const s = normalizeSymbol(symbol);
    const set = this.clientSymbols.get(ws);
    if (!s || !set || !set.has(s)) return false;

    set.delete(s);
    const meta = this.getMeta(s);
    meta.clientCount = Math.max(0, meta.clientCount - 1);

    if (meta.clientCount === 0 && this.activeSymbols.has(s)) {
      this.unsubscribeFinnhub(s, "no_frontend_watchers");
    }
    return true;
  }

  cleanupClient(ws) {
    const set = this.clientSymbols.get(ws);
    if (!set) return;

    for (const symbol of set) {
      const meta = this.getMeta(symbol);
      meta.clientCount = Math.max(0, meta.clientCount - 1);
      if (meta.clientCount === 0 && this.activeSymbols.has(symbol)) {
        this.unsubscribeFinnhub(symbol, "client_closed");
      }
    }
    this.clientSymbols.delete(ws);
  }

  ensureFinnhubSubscription(symbol) {
    const s = normalizeSymbol(symbol);
    if (!s || this.activeSymbols.has(s)) return null;

    let evicted = null;
    while (this.activeSymbols.size >= this.maxFinnhubSubscriptions) {
      evicted = this.pickEvictionCandidate();
      if (!evicted) {
        throw new Error("subscription_limit_reached_no_eviction_candidate");
      }
      this.unsubscribeFinnhub(evicted, "lru_eviction");
      this.broadcast({
        type: "symbol_evicted",
        symbol: evicted,
        reason: "lru_eviction",
        activeSymbols: [...this.activeSymbols],
      });
    }

    this.subscribeFinnhub(s);
    return evicted;
  }

  pickEvictionCandidate() {
    const candidates = [...this.activeSymbols];
    if (!candidates.length) return null;

    candidates.sort((a, b) => {
      const ma = this.getMeta(a);
      const mb = this.getMeta(b);

      if (ma.clientCount !== mb.clientCount) return ma.clientCount - mb.clientCount;
      if (ma.lastTradeAt !== mb.lastTradeAt) return ma.lastTradeAt - mb.lastTradeAt;
      if (ma.requestCount !== mb.requestCount) return ma.requestCount - mb.requestCount;
      return ma.lastRequestedAt - mb.lastRequestedAt;
    });

    return candidates[0];
  }

  subscribeFinnhub(symbol) {
    const s = normalizeSymbol(symbol);
    if (!s || this.activeSymbols.has(s)) return;

    this.activeSymbols.add(s);
    this.sendFinnhub({ type: "subscribe", symbol: s });
    this.logger.info(`[WSM] subscribed ${s} active=${this.activeSymbols.size}/${this.maxFinnhubSubscriptions}`);
  }

  unsubscribeFinnhub(symbol, reason = "manual") {
    const s = normalizeSymbol(symbol);
    if (!s || !this.activeSymbols.has(s)) return;

    this.activeSymbols.delete(s);
    const meta = this.getMeta(s);
    meta.evictedCount += reason === "lru_eviction" ? 1 : 0;
    this.sendFinnhub({ type: "unsubscribe", symbol: s });
    this.logger.info(`[WSM] unsubscribed ${s} reason=${reason} active=${this.activeSymbols.size}/${this.maxFinnhubSubscriptions}`);
  }

  connectFinnhub() {
    if (this.closed) return;
    if (this.finnhubWs && [WebSocket.OPEN, WebSocket.CONNECTING].includes(this.finnhubWs.readyState)) return;

    const url = `${FINNHUB_WS_BASE}?token=${encodeURIComponent(this.apiKey)}`;
    this.finnhubWs = new WebSocket(url);

    this.finnhubWs.on("open", () => {
      this.finnhubConnected = true;
      this.reconnectAttempt = 0;
      this.logger.info("[WSM] Finnhub connected");
      for (const symbol of this.activeSymbols) {
        this.sendFinnhub({ type: "subscribe", symbol });
      }
      this.broadcast({ type: "finnhub_status", status: "connected" });
    });

    this.finnhubWs.on("message", (raw) => this.handleFinnhubMessage(raw));
    this.finnhubWs.on("close", (code, reason) => {
      this.finnhubConnected = false;
      this.logger.warn(`[WSM] Finnhub closed code=${code} reason=${reason || ""}`);
      this.broadcast({ type: "finnhub_status", status: "disconnected" });
      this.scheduleReconnect();
    });
    this.finnhubWs.on("error", (err) => {
      this.finnhubConnected = false;
      this.logger.error("[WSM] Finnhub error", err?.message || err);
    });
  }

  scheduleReconnect() {
    if (this.closed || this.reconnectTimer) return;
    this.reconnectAttempt += 1;
    const base = Math.min(30_000, 500 * 2 ** Math.min(this.reconnectAttempt, 6));
    const jitter = Math.floor(Math.random() * 500);
    const delay = base + jitter;

    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.connectFinnhub();
    }, delay);
  }

  sendFinnhub(payload) {
    if (this.finnhubWs?.readyState !== WebSocket.OPEN) return false;
    this.finnhubWs.send(JSON.stringify(payload));
    return true;
  }

  handleFinnhubMessage(raw) {
    const msg = safeJsonParse(raw);
    if (!msg) return;

    if (msg.type === "trade" && Array.isArray(msg.data)) {
      const ts = now();
      for (const trade of msg.data) {
        const symbol = normalizeSymbol(trade.s);
        if (symbol) this.getMeta(symbol).lastTradeAt = ts;
      }
    }

    this.broadcast({ type: "finnhub_message", payload: msg });
  }

  broadcast(payload) {
    const data = JSON.stringify(payload);
    for (const ws of this.clientSymbols.keys()) {
      if (ws.readyState === WebSocket.OPEN) ws.send(data);
    }
  }

  startHeartbeat() {
    this.heartbeatTimer = setInterval(() => {
      const cutoff = now() - STALE_CLIENT_MS;
      for (const ws of this.clientSymbols.keys()) {
        if (ws.isAlive === false || ws.lastSeenAt < cutoff) {
          ws.terminate();
          this.cleanupClient(ws);
          continue;
        }
        ws.isAlive = false;
        try {
          ws.ping();
        } catch {
          ws.terminate();
          this.cleanupClient(ws);
        }
      }
    }, HEARTBEAT_INTERVAL_MS);
  }

  close() {
    this.closed = true;
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    if (this.heartbeatTimer) clearInterval(this.heartbeatTimer);
    for (const ws of this.clientSymbols.keys()) ws.close();
    this.frontendWss.close();
    this.finnhubWs?.close();
  }
}

export function createWsManager(options = {}) {
  return new FinnhubWsManager(options);
}

export default FinnhubWsManager;
