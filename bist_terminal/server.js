import express from "express";
import fetch from "node-fetch";
import cors from "cors";
import compression from "compression";
import path from "path";
import fs from "fs";
import crypto from "crypto";
import { fileURLToPath } from "url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

function loadDotEnvSync() {
  const envPath = path.join(__dirname, ".env");
  if (!fs.existsSync(envPath)) return;
  const raw = fs.readFileSync(envPath, "utf8");
  for (const line of raw.split(/\r?\n/)) {
    const t = line.trim();
    if (!t || t.startsWith("#")) continue;
    const i = t.indexOf("=");
    if (i <= 0) continue;
    const k = t.slice(0, i).trim();
    const v = t.slice(i + 1).trim().replace(/^['"]|['"]$/g, "");
    if (!(k in process.env)) process.env[k] = v;
  }
}
loadDotEnvSync();

// ── AUTH ─────────────────────────────────────────────────────────────────────
const AUTH_USER      = String(process.env.AUTH_USER || "").trim();
const AUTH_PASS      = String(process.env.AUTH_PASS || "").trim();
const AUTH_SECRET    = String(process.env.SESSION_SECRET || "").trim();
const AUTH_ENABLED   = Boolean(AUTH_USER && AUTH_PASS && AUTH_SECRET);
const SESSION_COOKIE = "fky_session";
const SESSION_MAX_AGE = 8 * 60 * 60; // 8 saat (saniye)
const IS_PRODUCTION  = process.env.NODE_ENV === "production" || Boolean(process.env.VERCEL);
const AUTH_PUBLIC    = new Set(["/auth/login", "/auth/logout"]);
const AUTH_RATE_LIMIT_WINDOW_MS = Math.max(30_000, toNum(process.env.AUTH_RATE_LIMIT_WINDOW_MS) || 10 * 60 * 1000);
const AUTH_RATE_LIMIT_MAX_FAILS = Math.max(1, Math.floor(toNum(process.env.AUTH_RATE_LIMIT_MAX_FAILS) || 6));
const AUTH_RATE_LIMIT_BLOCK_MS = Math.max(10_000, toNum(process.env.AUTH_RATE_LIMIT_BLOCK_MS) || 15 * 60 * 1000);
const AUTH_RATE_LIMIT_MAX_KEYS = Math.max(100, Math.floor(toNum(process.env.AUTH_RATE_LIMIT_MAX_KEYS) || 20_000));
const authLoginRate = new Map();

function parseCookies(req) {
  const out = {};
  const raw = req.headers.cookie || "";
  for (const part of raw.split(";")) {
    const idx = part.indexOf("=");
    if (idx < 0) continue;
    const k = part.slice(0, idx).trim();
    const rawVal = part.slice(idx + 1).trim();
    let v = rawVal;
    try {
      v = decodeURIComponent(rawVal);
    } catch {
      // Keep raw cookie value when malformed encoding is supplied.
      v = rawVal;
    }
    out[k] = v;
  }
  return out;
}

function signValue(val) {
  return crypto.createHmac("sha256", AUTH_SECRET).update(String(val)).digest("hex");
}

function makeSessionToken() {
  const ts = String(Date.now());
  return `${ts}.${signValue(ts)}`;
}

function verifySessionToken(token) {
  if (!AUTH_SECRET || !token) return false;
  const dot = token.lastIndexOf(".");
  if (dot < 0) return false;
  const ts  = token.slice(0, dot);
  const sig = token.slice(dot + 1);
  const expected = signValue(ts);
  try {
    if (!crypto.timingSafeEqual(Buffer.from(sig), Buffer.from(expected))) return false;
  } catch { return false; }
  const ageMs = Date.now() - Number(ts);
  return ageMs >= 0 && ageMs < SESSION_MAX_AGE * 1000;
}

function buildSessionCookie(token) {
  const flags = [`Max-Age=${SESSION_MAX_AGE}`, "Path=/", "HttpOnly", "SameSite=Lax"];
  if (IS_PRODUCTION) flags.push("Secure");
  return `${SESSION_COOKIE}=${encodeURIComponent(token)}; ${flags.join("; ")}`;
}

function clearSessionCookie() {
  return `${SESSION_COOKIE}=; Max-Age=0; Path=/; HttpOnly; SameSite=Lax`;
}

function getClientIp(req) {
  const xff = String(req.headers["x-forwarded-for"] || "")
    .split(",")[0]
    .trim();
  const ip = xff || req.ip || req.socket?.remoteAddress || "unknown";
  return String(ip).trim().toLowerCase();
}

function authRateKey(req) {
  return getClientIp(req);
}

function pruneAuthLoginRate(now = Date.now()) {
  const maxAge = Math.max(AUTH_RATE_LIMIT_BLOCK_MS, AUTH_RATE_LIMIT_WINDOW_MS) * 2;
  if (authLoginRate.size <= AUTH_RATE_LIMIT_MAX_KEYS) {
    for (const [key, row] of authLoginRate.entries()) {
      if (!row || now - (row.lastTs || 0) > maxAge) authLoginRate.delete(key);
    }
    return;
  }
  const rows = [...authLoginRate.entries()].sort((a, b) => (a[1]?.lastTs || 0) - (b[1]?.lastTs || 0));
  const removeN = Math.max(0, rows.length - AUTH_RATE_LIMIT_MAX_KEYS);
  for (let i = 0; i < removeN; i++) authLoginRate.delete(rows[i][0]);
}

function getAuthRateInfo(req) {
  const key = authRateKey(req);
  const now = Date.now();
  pruneAuthLoginRate(now);
  const row = authLoginRate.get(key);
  if (!row) return { key, blocked: false, retryAfterSec: 0 };
  const blockedUntil = toNum(row.blockedUntil) || 0;
  if (blockedUntil > now) {
    return { key, blocked: true, retryAfterSec: Math.max(1, Math.ceil((blockedUntil - now) / 1000)) };
  }
  return { key, blocked: false, retryAfterSec: 0 };
}

function recordAuthLoginFailure(key) {
  const now = Date.now();
  const row = authLoginRate.get(key) || {
    failCount: 0,
    windowStart: now,
    blockedUntil: 0,
    lastTs: now,
  };
  if (now - (toNum(row.windowStart) || now) > AUTH_RATE_LIMIT_WINDOW_MS) {
    row.failCount = 0;
    row.windowStart = now;
  }
  row.failCount = (toNum(row.failCount) || 0) + 1;
  row.lastTs = now;
  if (row.failCount >= AUTH_RATE_LIMIT_MAX_FAILS) {
    row.blockedUntil = now + AUTH_RATE_LIMIT_BLOCK_MS;
    row.failCount = 0;
    row.windowStart = now;
  }
  authLoginRate.set(key, row);
}

function clearAuthLoginFailures(key) {
  authLoginRate.delete(key);
}

function escapeHtml(str) {
  return String(str || "")
    .replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function buildLoginPage(errorMsg) {
  const err = errorMsg
    ? `<div class="err">${escapeHtml(errorMsg)}</div>`
    : "";
  return `<!DOCTYPE html>
<html lang="tr">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>FKY — Giriş</title>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@300;400;500&family=Barlow+Condensed:wght@400;600;700&display=swap" rel="stylesheet">
<style>
:root{--b0:#04050a;--b1:#080b12;--b2:#0c1018;--l3:rgba(255,255,255,.12);
  --au:#d4a843;--au2:#e8c060;--au-bg:rgba(212,168,67,.08);--au-bd:rgba(212,168,67,.2);
  --r:#e05252;--r-bg:rgba(224,82,82,.08);--r-bd:rgba(224,82,82,.2);
  --t1:#eef0f6;--t2:#9ba5bc;--t3:#5a6478;
  --mono:'IBM Plex Mono',monospace;--disp:'Barlow Condensed',sans-serif}
*,*::before,*::after{margin:0;padding:0;box-sizing:border-box}
html{font-size:13px;-webkit-font-smoothing:antialiased}
body{background:var(--b0);color:var(--t1);font-family:var(--mono);min-height:100vh;
  display:flex;align-items:center;justify-content:center;
  background-image:radial-gradient(ellipse 80% 50% at 50% -20%,rgba(212,168,67,.04) 0%,transparent 60%)}
.card{background:var(--b2);border:1px solid var(--l3);border-radius:8px;padding:40px 36px;width:100%;max-width:360px}
.logo{font-family:var(--disp);font-size:28px;font-weight:700;color:var(--au);letter-spacing:2px;margin-bottom:4px}
.sub{font-size:10px;color:var(--t3);letter-spacing:1px;text-transform:uppercase;margin-bottom:32px}
label{display:block;font-size:10px;color:var(--t3);letter-spacing:1px;text-transform:uppercase;margin-bottom:6px}
input{width:100%;background:var(--b1);border:1px solid var(--l3);border-radius:4px;color:var(--t1);
  font-family:var(--mono);font-size:13px;padding:10px 12px;outline:none;transition:border-color .15s;margin-bottom:16px}
input:focus{border-color:var(--au-bd)}
input[type=password]{letter-spacing:2px}
.btn{width:100%;background:var(--au-bg);border:1px solid var(--au-bd);border-radius:4px;color:var(--au2);
  font-family:var(--disp);font-size:15px;font-weight:600;letter-spacing:1px;padding:11px;
  cursor:pointer;transition:background .15s,border-color .15s;margin-top:4px}
.btn:hover{background:rgba(212,168,67,.14);border-color:var(--au)}
.err{background:var(--r-bg);border:1px solid var(--r-bd);border-radius:4px;color:var(--r);
  font-size:11px;padding:10px 12px;margin-bottom:20px}
</style>
</head>
<body>
<div class="card">
  <div class="logo">FKY</div>
  <div class="sub">Terminal — BİST Analiz</div>
  ${err}
  <form method="POST" action="/auth/login">
    <label for="u">Kullanıcı Adı</label>
    <input id="u" name="username" type="text" autocomplete="username" required autofocus>
    <label for="p">Şifre</label>
    <input id="p" name="password" type="password" autocomplete="current-password" required>
    <button class="btn" type="submit">GİRİŞ YAP</button>
  </form>
</div>
</body></html>`;
}

function requireAuth(req, res, next) {
  if (!AUTH_ENABLED) return next();
  if (AUTH_PUBLIC.has(req.path)) return next();
  const cookies = parseCookies(req);
  const token = cookies[SESSION_COOKIE];
  if (!verifySessionToken(token)) {
    if (token) res.setHeader("Set-Cookie", clearSessionCookie());
    if (req.path.startsWith("/api/")) {
      return res.status(401).json({ error: "unauthorized" });
    }
    return res.redirect("/auth/login");
  }
  next();
}
// ─────────────────────────────────────────────────────────────────────────────

const app = express();
app.use(cors());
app.use(compression());
app.use(express.urlencoded({ extended: false }));
app.use(requireAuth);
const distPath = path.join(__dirname, "../dist");
if (fs.existsSync(distPath)) {
  app.use(express.static(distPath));
} else {
  app.use(express.static(path.join(__dirname, "..")));
}
app.use("/data", express.static(path.join(__dirname, "data")));


const PRICE_CACHE_MS = 60_000;
const FUND_CACHE_MS = 36 * 60 * 60 * 1000;
const BETA_CACHE_MS = 24 * 60 * 60 * 1000;
const SERIES_CACHE_MS = 6 * 60 * 60 * 1000;
const REQUEST_INTERVAL_MS = Math.max(50, toNum(process.env.REQUEST_INTERVAL_MS) || 300);
const REQUEST_TIMEOUT_MS = Math.max(1_000, toNum(process.env.REQUEST_TIMEOUT_MS) || 15_000);
const SYMBOL_REGEX = /^[A-Z0-9.]{1,15}$/;
const BETA_BENCHMARK_SYMBOL = "XU100.IS";
const FUND_SOURCE = String(process.env.FUND_SOURCE || "snapshot").toLowerCase();
const FUND_SYNC_TOPUP = String(process.env.FUND_SYNC_TOPUP || "1") !== "0";
const FUND_ALLOW_YAHOO_FUND = String(process.env.FUND_ALLOW_YAHOO_FUND || "0") === "1";
const FUND_SNAPSHOT_PATH = path.join(__dirname, "data", "fundamentals_snapshot.json");
const FUND_SNAPSHOT_RELOAD_MS = 15_000;
const DIVIDEND_SNAPSHOT_PATH = path.join(__dirname, "data", "dividend_snapshot.json");
const DIVIDEND_SNAPSHOT_RELOAD_MS = 20_000;
const PRICE_LOCAL_SNAPSHOT_PATH = path.join(__dirname, "data", "prices_snapshot.json");
const PRICE_ALLOW_LOCAL_SNAPSHOT_FALLBACK = String(process.env.PRICE_ALLOW_LOCAL_SNAPSHOT_FALLBACK || "1") !== "0";
const PRICE_SNAPSHOT_RELOAD_MS = Math.max(2_000, toNum(process.env.PRICE_SNAPSHOT_RELOAD_MS) || 15_000);
const PRICE_SNAPSHOT_PERSIST_MS = Math.max(500, toNum(process.env.PRICE_SNAPSHOT_PERSIST_MS) || 2_000);
const FRED_API_KEY = String(process.env.FRED_API_KEY || "").trim();
const EVDS_API_KEY = String(process.env.EVDS_API_KEY || "").trim();
const EVDS_SERIES_USDTRY = String(process.env.EVDS_SERIES_USDTRY || "TP.DK.USD.S.YTL").trim();
const EVDS_SERIES_POLICY_RATE = String(process.env.EVDS_SERIES_POLICY_RATE || "").trim();
const EVDS_SERIES_CPI = String(process.env.EVDS_SERIES_CPI || "").trim();
const EVDS_SERIES_GROWTH = String(process.env.EVDS_SERIES_GROWTH || "").trim();
const EVDS_SERIES_UNEMP = String(process.env.EVDS_SERIES_UNEMP || "").trim();
const FUND_METRIC_MODES = ["kap_truth", "tv_parity"];
const FUND_METRIC_MODE_DEFAULT = normalizeFundMetricMode(process.env.FUND_METRIC_MODE || "kap_truth");
const TV_SCANNER_URL = "https://scanner.tradingview.com/turkey/scan";
const PRICE_ALLOW_TV_FALLBACK = String(process.env.PRICE_ALLOW_TV_FALLBACK || "1") !== "0";
const PRICE_ALLOW_SNAPSHOT_ESTIMATE = String(process.env.PRICE_ALLOW_SNAPSHOT_ESTIMATE || "1") !== "0";
const PRICE_PROVIDER_COOLDOWN_MS = Math.max(10_000, toNum(process.env.PRICE_PROVIDER_COOLDOWN_MS) || 2 * 60 * 1000);
const PRICE_FORCE_LOCAL_SNAPSHOT = String(process.env.PRICE_FORCE_LOCAL_SNAPSHOT || "0") === "1";
const TV_PRICE_CACHE_MS = Math.max(20_000, toNum(process.env.TV_PRICE_CACHE_MS) || 90_000);
const TV_PRICE_CHUNK_SIZE = Math.max(5, toNum(process.env.TV_PRICE_CHUNK_SIZE) || 50);
const TV_PRICE_COLUMNS = [
  "name",
  "close",
  "change",
  "change_abs",
  "volume",
  "average_volume_60d_calc",
  "average_volume_10d_calc",
  "market_cap_basic",
  "price_earnings_ttm",
  "earnings_per_share_basic_ttm",
  "price_book_fq",
  "beta_1_year",
  "dividend_yield_recent",
];
const TV_PARITY_CACHE_MS = Math.max(30_000, toNum(process.env.TV_PARITY_CACHE_MS) || 2 * 60 * 1000);
const TV_PARITY_CHUNK_SIZE = Math.max(5, toNum(process.env.TV_PARITY_CHUNK_SIZE) || 50);
const TV_PARITY_COLUMNS = [
  "price_earnings_ttm",
  "earnings_per_share_basic_ttm",
  "price_book_fq",
  "return_on_equity",
  "return_on_assets",
  "gross_margin_ttm",
  "operating_margin_ttm",
  "net_margin_ttm",
  "current_ratio",
  "quick_ratio",
  "debt_to_equity",
  "beta_1_year",
  "market_cap_basic",
  "price_sales",
  "dividend_yield_recent",
  "average_volume_60d_calc",
  "average_volume_10d_calc",
  "volume",
  "total_debt",
  "net_debt",
];
const TV_PARITY_FIELDS = [
  "trailingPE",
  "trailingEps",
  "priceToBook",
  "returnOnEquity",
  "returnOnAssets",
  "grossMargins",
  "operatingMargins",
  "profitMargins",
  "currentRatio",
  "quickRatio",
  "debtToEquity",
  "beta",
  "marketCap",
  "priceToSalesTrailing12Months",
  "dividendYield",
  "averageVolume",
  "totalDebt",
  "totalCash",
];

const priceCache = new Map();
const fundCache = new Map();
const dividendCache = new Map();
const betaCache = new Map();
const chartSeriesCache = new Map();
const tvParityCache = new Map();
const tvPriceCache = new Map();
const localPriceSnapshotCache = new Map();
const providerBackoffUntil = { yahoo_price: 0, tv_price: 0 };
const fundRefreshInFlight = new Set();
const priceQueue = [];
const fundQueue = [];
let priceQueueRunning = false;
let fundQueueRunning = false;
let lastPriceRequestTime = 0;
let lastFundRequestTime = 0;
let lastSnapshotCheckMs = 0;
let lastSnapshotMtimeMs = 0;
let lastFundSnapshotHealth = null;
let lastDividendSnapshotCheckMs = 0;
let lastDividendSnapshotMtimeMs = 0;
let lastDividendSnapshotHealth = null;
let lastPriceSnapshotCheckMs = 0;
let lastPriceSnapshotMtimeMs = 0;
let priceSnapshotDirty = false;
let priceSnapshotFlushTimer = null;

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function makeTimeoutSignal(ms) {
  if (typeof AbortSignal !== "undefined" && typeof AbortSignal.timeout === "function") {
    return AbortSignal.timeout(ms);
  }
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), Math.max(1, Number(ms) || 1));
  if (typeof timer?.unref === "function") timer.unref();
  return controller.signal;
}

function isProviderOnCooldown(name) {
  const until = toNum(providerBackoffUntil?.[name]) || 0;
  return until > Date.now();
}

function markProviderSuccess(name) {
  if (!Object.prototype.hasOwnProperty.call(providerBackoffUntil, name)) return;
  providerBackoffUntil[name] = 0;
}

function isNetworkLikeError(err) {
  const msg = String(err?.message || err || "").toLowerCase();
  if (!msg) return false;
  return (
    msg.includes("enotfound") ||
    msg.includes("eai_again") ||
    msg.includes("econnrefused") ||
    msg.includes("econnreset") ||
    msg.includes("etimedout") ||
    msg.includes("timeout") ||
    msg.includes("aborted") ||
    msg.includes("network") ||
    msg.includes("failed")
  );
}

function markProviderFailure(name, err) {
  if (!Object.prototype.hasOwnProperty.call(providerBackoffUntil, name)) return;
  if (!isNetworkLikeError(err)) return;
  providerBackoffUntil[name] = Date.now() + PRICE_PROVIDER_COOLDOWN_MS;
}

function toDdMmYyyy(date) {
  const d = new Date(date);
  const dd = String(d.getDate()).padStart(2, "0");
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const yyyy = d.getFullYear();
  return `${dd}-${mm}-${yyyy}`;
}

function normalizeSymbol(raw) {
  const clean = String(raw || "").trim().toUpperCase();
  if (!clean || !SYMBOL_REGEX.test(clean)) return null;
  return clean.endsWith(".IS") ? clean : `${clean}.IS`;
}

function baseSymbol(symbol) {
  return symbol.replace(/\.IS$/i, "");
}

function toNum(value) {
  if (value == null) return null;
  if (typeof value === "boolean") return null;
  if (typeof value === "string" && !value.trim()) return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function normalizeFundMetricMode(raw) {
  const v = String(raw || "")
    .trim()
    .toLowerCase();
  if (v === "tv_parity" || v === "tv" || v === "parity") return "tv_parity";
  if (v === "kap_truth" || v === "kap" || v === "truth") return "kap_truth";
  return "kap_truth";
}

function toUnitRatioFromPercent(value) {
  const n = toNum(value);
  return Number.isFinite(n) ? n / 100 : null;
}

function chunkArray(items, size) {
  const out = [];
  for (let i = 0; i < items.length; i += size) {
    out.push(items.slice(i, i + size));
  }
  return out;
}

function tvSymbolToBase(raw) {
  const s = String(raw || "").toUpperCase();
  if (!s) return "";
  const withNoExchange = s.includes(":") ? s.split(":").pop() : s;
  return String(withNoExchange || "")
    .replace(/\.IS$/i, "")
    .trim();
}

function median(values) {
  const nums = (values || []).map(toNum).filter((v) => Number.isFinite(v)).sort((a, b) => a - b);
  if (!nums.length) return null;
  const mid = Math.floor(nums.length / 2);
  if (nums.length % 2 === 1) return nums[mid];
  return (nums[mid - 1] + nums[mid]) / 2;
}

function lastFiniteFrom(arr, min = -Infinity) {
  if (!Array.isArray(arr)) return null;
  for (let i = arr.length - 1; i >= 0; i--) {
    const n = toNum(arr[i]);
    if (Number.isFinite(n) && n >= min) return n;
  }
  return null;
}

function meanTailFinite(arr, take = 20, min = -Infinity) {
  if (!Array.isArray(arr) || take <= 0) return null;
  const vals = [];
  for (let i = arr.length - 1; i >= 0 && vals.length < take; i--) {
    const n = toNum(arr[i]);
    if (Number.isFinite(n) && n >= min) vals.push(n);
  }
  if (!vals.length) return null;
  const sum = vals.reduce((a, b) => a + b, 0);
  return sum / vals.length;
}

function fromYahooValue(value) {
  if (value == null) return null;
  if (typeof value === "object" && value.raw != null) return toNum(value.raw);
  return toNum(value);
}

function sanitizeFundRecord(input) {
  if (!input || typeof input !== "object") return null;
  const out = {};
  for (const key of [
    "marketCap",
    "trailingPE",
    "forwardPE",
    "priceToBook",
    "trailingEps",
    "dividendYield",
    "returnOnEquity",
    "returnOnAssets",
    "debtToEquity",
    "currentRatio",
    "revenueGrowth",
    "earningsGrowth",
    "grossMargins",
    "operatingMargins",
    "profitMargins",
    "freeCashflow",
    "totalDebt",
    "totalCash",
    "enterpriseValue",
    "ebitda",
    "pegRatio",
    "beta",
    "priceToSalesTrailing12Months",
    "assetTurnover",
    "epsGrowth",
    "netDebtToEbitda",
    "interestCoverage",
    "quickRatio",
    "cfoToNetIncome",
    "debtMaturityRatio",
    "fxNetPositionRatio",
    "profitabilityStability",
    "growthStability",
    "divPolicyScore",
    "buybackPolicyScore",
    "dilutionPolicyScore",
    "capitalAllocationScore",
    "governanceScore",
    // Raw KAP base fields for derived metrics
    "revenue",
    "netIncome",
    "equity",
    "assets",
    "cfo",
    "capex",
    "sharesOutstanding",
    "interestExpense",
    "currentBorrowings",
    "nonCurrentBorrowings",
    "inventories",
    "currentAssets",
    "currentLiabilities",
    "totalLiabilities",
    "grossProfit",
    "operatingProfit",
    "depreciationAmortization",
    "kapDisclosureIndex",
    "kapDisclosureYear",
    "kapDisclosureDateMs",
  ]) {
    const v = toNum(input[key]);
    out[key] = Number.isFinite(v) ? v : null;
  }
  return out;
}

const FUND_FIELDS = [
  "marketCap",
  "trailingPE",
  "forwardPE",
  "priceToBook",
  "trailingEps",
  "dividendYield",
  "returnOnEquity",
  "returnOnAssets",
  "debtToEquity",
  "currentRatio",
  "revenueGrowth",
  "earningsGrowth",
  "grossMargins",
  "operatingMargins",
  "profitMargins",
  "freeCashflow",
  "totalDebt",
  "totalCash",
  "enterpriseValue",
  "ebitda",
  "pegRatio",
  "beta",
  "priceToSalesTrailing12Months",
  "assetTurnover",
  "epsGrowth",
  "netDebtToEbitda",
  "interestCoverage",
  "quickRatio",
  "cfoToNetIncome",
  "debtMaturityRatio",
  "fxNetPositionRatio",
  "profitabilityStability",
  "growthStability",
  "divPolicyScore",
  "buybackPolicyScore",
  "dilutionPolicyScore",
  "capitalAllocationScore",
  "governanceScore",
  "revenue",
  "netIncome",
  "equity",
  "assets",
  "cfo",
  "capex",
  "sharesOutstanding",
  "interestExpense",
  "currentBorrowings",
  "nonCurrentBorrowings",
  "inventories",
  "currentAssets",
  "currentLiabilities",
  "totalLiabilities",
  "grossProfit",
  "operatingProfit",
  "depreciationAmortization",
  "kapDisclosureIndex",
  "kapDisclosureYear",
  "kapDisclosureDateMs",
];

const FUND_REFRESH_MIN_FIELDS = [
  "trailingPE",
  "priceToBook",
  "returnOnEquity",
  "returnOnAssets",
  "debtToEquity",
  "revenueGrowth",
  "earningsGrowth",
  "profitMargins",
];

function mergeFundRecords(primary, secondary) {
  const a = sanitizeFundRecord(primary) || {};
  const b = sanitizeFundRecord(secondary) || {};
  const out = {};
  for (const k of FUND_FIELDS) {
    out[k] = a[k] != null ? a[k] : b[k] != null ? b[k] : null;
  }
  out._fundProvider = [primary?._fundProvider, secondary?._fundProvider].filter(Boolean).join("+") || "kap_snapshot";
  out._fundProviderTs = Date.now();
  return out;
}

function needsFundRefresh(fund) {
  if (!fund) return true;
  const filled = FUND_REFRESH_MIN_FIELDS.filter((k) => fund[k] != null).length;
  return filled < Math.ceil(FUND_REFRESH_MIN_FIELDS.length * 0.5);
}

function hasAnyFundValue(row) {
  if (!row || typeof row !== "object") return false;
  return FUND_FIELDS.some((k) => row[k] != null);
}

function sanitizeDividendEvent(input) {
  if (!input || typeof input !== "object") return null;
  const dateMs = toNum(input.dateMs);
  const amount = toNum(input.amountPerShare);
  const type = String(input.type || "").trim() || "cash";
  const title = String(input.title || "").trim();
  if (!Number.isFinite(dateMs) && !Number.isFinite(amount) && !title) return null;
  return {
    dateMs: Number.isFinite(dateMs) ? dateMs : null,
    amountPerShare: Number.isFinite(amount) ? amount : null,
    type,
    title: title || null,
  };
}

function sanitizeDividendRecord(input) {
  if (!input || typeof input !== "object") return null;
  const events = Array.isArray(input.events)
    ? input.events.map(sanitizeDividendEvent).filter(Boolean).slice(0, 8)
    : [];
  const out = {
    lastDividendDateMs: toNum(input.lastDividendDateMs),
    lastDividendPerShare: toNum(input.lastDividendPerShare),
    annualDividendPerShare: toNum(input.annualDividendPerShare),
    dividendPayoutPct: toNum(input.dividendPayoutPct),
    paidYears3y: toNum(input.paidYears3y),
    regularityScore: toNum(input.regularityScore),
    eventCount: toNum(input.eventCount),
    events,
    _dividendProvider: String(input._dividendProvider || input.source || "kap_dividend_snapshot"),
    _dividendProviderTs: Date.now(),
  };
  return out;
}

function hasAnyDividendValue(row) {
  if (!row || typeof row !== "object") return false;
  if (Number.isFinite(toNum(row.lastDividendDateMs))) return true;
  if (Number.isFinite(toNum(row.lastDividendPerShare))) return true;
  if (Number.isFinite(toNum(row.annualDividendPerShare))) return true;
  if (Number.isFinite(toNum(row.dividendPayoutPct))) return true;
  if (Number.isFinite(toNum(row.paidYears3y)) && toNum(row.paidYears3y) > 0) return true;
  if (Number.isFinite(toNum(row.regularityScore)) && toNum(row.regularityScore) > 0) return true;
  if (Number.isFinite(toNum(row.eventCount)) && toNum(row.eventCount) > 0) return true;
  return Array.isArray(row.events) && row.events.length > 0;
}

function buildFailuresSummary(failures, maxItems = 8) {
  const buckets = new Map();
  for (const f of failures || []) {
    const key = String(f?.reason || "unknown").slice(0, 180);
    buckets.set(key, (buckets.get(key) || 0) + 1);
  }
  return [...buckets.entries()]
    .sort((a, b) => b[1] - a[1])
    .slice(0, maxItems)
    .map(([reason, count]) => ({ reason, count }));
}

function analyzeFundSnapshot(parsed) {
  const data = parsed?.data && typeof parsed.data === "object" ? parsed.data : {};
  const symbolCount = toNum(parsed?.symbolCount) || Object.keys(data).length;
  const rows = Object.values(data);
  const nonNullSymbols = rows.filter((row) => hasAnyFundValue(row)).length;
  const failureCount = toNum(parsed?.failureCount) || 0;
  const failures = Array.isArray(parsed?.failures) ? parsed.failures : [];
  const coveragePct = symbolCount ? +((nonNullSymbols / symbolCount) * 100).toFixed(1) : 0;

  const fieldNonNull = {};
  for (const k of FUND_FIELDS) fieldNonNull[k] = 0;
  for (const row of rows) {
    for (const k of FUND_FIELDS) {
      if (row?.[k] != null) fieldNonNull[k] += 1;
    }
  }
  const computedFieldCoverage = {};
  for (const k of FUND_FIELDS) {
    computedFieldCoverage[k] = {
      nonNull: fieldNonNull[k],
      coveragePct: symbolCount ? +((fieldNonNull[k] / symbolCount) * 100).toFixed(1) : 0,
    };
  }
  const fieldCoverage = { ...computedFieldCoverage };
  if (parsed?.audit?.fieldCoverage && typeof parsed.audit.fieldCoverage === "object") {
    for (const [k, v] of Object.entries(parsed.audit.fieldCoverage)) {
      if (!k) continue;
      fieldCoverage[k] = v;
    }
  }

  const lastRunStatus =
    parsed?.audit?.lastRunStatus ||
    (coveragePct >= 85 && failureCount === 0 ? "ok" : nonNullSymbols > 0 ? "partial" : "failed");

  return {
    generatedAt: parsed?.generatedAt || null,
    generatedAtMs: toNum(parsed?.generatedAtMs) || null,
    source: parsed?.source || "kap",
    symbolCoverage: {
      symbolCount,
      nonNullSymbols,
      coveragePct,
    },
    failureCount,
    failuresSummary: buildFailuresSummary(failures),
    fieldCoverage,
    lastRunStatus,
    lowConfidence: coveragePct < 85 || failureCount > 0,
  };
}

function analyzeDividendSnapshot(parsed) {
  const data = parsed?.data && typeof parsed.data === "object" ? parsed.data : {};
  const symbolCount = toNum(parsed?.symbolCount) || Object.keys(data).length;
  const rows = Object.values(data);
  const nonNullSymbols = rows.filter((row) => hasAnyDividendValue(row)).length;
  const failureCount = toNum(parsed?.failureCount) || 0;
  const failures = Array.isArray(parsed?.failures) ? parsed.failures : [];
  const coveragePct = symbolCount ? +((nonNullSymbols / symbolCount) * 100).toFixed(1) : 0;

  const fieldKeys = [
    "lastDividendDateMs",
    "lastDividendPerShare",
    "annualDividendPerShare",
    "dividendPayoutPct",
    "paidYears3y",
    "regularityScore",
    "eventCount",
  ];
  const fieldCoverage = {};
  for (const k of fieldKeys) {
    let c = 0;
    for (const row of rows) {
      const v = toNum(row?.[k]);
      if (k === "eventCount" || k === "paidYears3y" || k === "regularityScore") {
        if (Number.isFinite(v) && v > 0) c += 1;
      } else if (row?.[k] != null) {
        c += 1;
      }
    }
    fieldCoverage[k] = {
      nonNull: c,
      coveragePct: symbolCount ? +((c / symbolCount) * 100).toFixed(1) : 0,
    };
  }
  let eventRows = 0;
  for (const row of rows) if (Array.isArray(row?.events) && row.events.length) eventRows += 1;
  fieldCoverage.events = {
    nonNull: eventRows,
    coveragePct: symbolCount ? +((eventRows / symbolCount) * 100).toFixed(1) : 0,
  };

  return {
    generatedAt: parsed?.generatedAt || null,
    generatedAtMs: toNum(parsed?.generatedAtMs) || null,
    source: parsed?.source || "kap_dividend",
    symbolCoverage: { symbolCount, nonNullSymbols, coveragePct },
    failureCount,
    failuresSummary: buildFailuresSummary(failures),
    fieldCoverage,
    lastRunStatus: parsed?.audit?.lastRunStatus || (coveragePct >= 70 ? "ok" : nonNullSymbols > 0 ? "partial" : "failed"),
    lowConfidence: coveragePct < 70 || failureCount > 0,
  };
}

function reloadFundCacheFromSnapshotIfNeeded(force = false) {
  if (FUND_SOURCE !== "snapshot" && FUND_SOURCE !== "hybrid") return;
  const now = Date.now();
  if (!force && now - lastSnapshotCheckMs < FUND_SNAPSHOT_RELOAD_MS) return;
  lastSnapshotCheckMs = now;

  let stat;
  try {
    stat = fs.statSync(FUND_SNAPSHOT_PATH);
  } catch {
    return;
  }
  if (!force && stat.mtimeMs === lastSnapshotMtimeMs) return;

  try {
    const raw = fs.readFileSync(FUND_SNAPSHOT_PATH, "utf8");
    const parsed = JSON.parse(raw);
    lastFundSnapshotHealth = analyzeFundSnapshot(parsed);
    const data = parsed?.data || {};
    const ts = toNum(parsed?.generatedAtMs) || now;
    const next = new Map(fundCache);
    for (const [k, row] of Object.entries(data)) {
      const base = String(k || "").trim().toUpperCase();
      if (!base) continue;
      const symbol = normalizeSymbol(base);
      if (!symbol) continue;
      const sanitized = sanitizeFundRecord(row);
      if (!sanitized) continue;
      if (!hasAnyFundValue(sanitized)) continue;
      next.set(symbol, { ts, data: sanitized });
    }
    fundCache.clear();
    for (const [k, v] of next) fundCache.set(k, v);
    lastSnapshotMtimeMs = stat.mtimeMs;
  } catch (err) {
    lastFundSnapshotHealth = {
      generatedAt: null,
      generatedAtMs: null,
      source: "kap",
      symbolCoverage: { symbolCount: 0, nonNullSymbols: 0, coveragePct: 0 },
      failureCount: 0,
      failuresSummary: [{ reason: String(err?.message || err), count: 1 }],
      fieldCoverage: {},
      lastRunStatus: "parse_error",
      lowConfidence: true,
    };
    console.warn(`[FUND] snapshot parse failed: ${String(err?.message || err)}`);
  }
}

function reloadDividendCacheFromSnapshotIfNeeded(force = false) {
  const now = Date.now();
  if (!force && now - lastDividendSnapshotCheckMs < DIVIDEND_SNAPSHOT_RELOAD_MS) return;
  lastDividendSnapshotCheckMs = now;

  let stat;
  try {
    stat = fs.statSync(DIVIDEND_SNAPSHOT_PATH);
  } catch {
    return;
  }
  if (!force && stat.mtimeMs === lastDividendSnapshotMtimeMs) return;

  try {
    const raw = fs.readFileSync(DIVIDEND_SNAPSHOT_PATH, "utf8");
    const parsed = JSON.parse(raw);
    lastDividendSnapshotHealth = analyzeDividendSnapshot(parsed);
    const data = parsed?.data || {};
    const ts = toNum(parsed?.generatedAtMs) || now;
    const next = new Map(dividendCache);
    for (const [k, row] of Object.entries(data)) {
      const base = String(k || "").trim().toUpperCase();
      if (!base) continue;
      const symbol = normalizeSymbol(base);
      if (!symbol) continue;
      const sanitized = sanitizeDividendRecord(row);
      if (!sanitized) continue;
      if (!hasAnyDividendValue(sanitized)) continue;
      next.set(symbol, { ts, data: sanitized });
    }
    dividendCache.clear();
    for (const [k, v] of next) dividendCache.set(k, v);
    lastDividendSnapshotMtimeMs = stat.mtimeMs;
  } catch (err) {
    lastDividendSnapshotHealth = {
      generatedAt: null,
      generatedAtMs: null,
      source: "kap_dividend",
      symbolCoverage: { symbolCount: 0, nonNullSymbols: 0, coveragePct: 0 },
      failureCount: 0,
      failuresSummary: [{ reason: String(err?.message || err), count: 1 }],
      fieldCoverage: {},
      lastRunStatus: "parse_error",
      lowConfidence: true,
    };
    console.warn(`[DIVIDEND] snapshot parse failed: ${String(err?.message || err)}`);
  }
}

function sanitizeLocalPriceSnapshotRecord(symbol, input) {
  const price = pickBestNumber(toNum(input?.regularMarketPrice), toNum(input?.price));
  if (!Number.isFinite(price) || price <= 0) return null;
  const out = stabilizePriceFields({
    symbol,
    shortName: String(input?.shortName || baseSymbol(symbol)),
    regularMarketPrice: price,
    regularMarketPreviousClose: pickBestNumber(
      toNum(input?.regularMarketPreviousClose),
      toNum(input?.previousClose)
    ),
    regularMarketChange: pickBestNumber(toNum(input?.regularMarketChange), toNum(input?.change)),
    regularMarketChangePercent: pickBestNumber(
      toNum(input?.regularMarketChangePercent),
      toNum(input?.changePercent)
    ),
    regularMarketOpen: toNum(input?.regularMarketOpen),
    regularMarketDayHigh: toNum(input?.regularMarketDayHigh),
    regularMarketDayLow: toNum(input?.regularMarketDayLow),
    regularMarketTime: pickBestNumber(toNum(input?.regularMarketTime), toNum(input?.time), toNum(input?.ts), Date.now()),
    regularMarketVolume: pickBestNumber(toNum(input?.regularMarketVolume), toNum(input?.volume)),
    averageVolume: pickBestNumber(toNum(input?.averageVolume), toNum(input?.regularMarketVolume), toNum(input?.volume)),
    marketCap: toNum(input?.marketCap),
    trailingPE: toNum(input?.trailingPE),
    priceToBook: toNum(input?.priceToBook),
    trailingEps: toNum(input?.trailingEps),
    dividendYield: toNum(input?.dividendYield),
    beta: toNum(input?.beta),
    _provider: "local_price_snapshot",
    _providerTs: Date.now(),
    _priceFallbackProvider: "local_price_snapshot",
    _isStalePrice: true,
  });
  return out;
}

function reloadLocalPriceSnapshotIfNeeded(force = false) {
  if (!PRICE_ALLOW_LOCAL_SNAPSHOT_FALLBACK) return;
  const now = Date.now();
  if (!force && now - lastPriceSnapshotCheckMs < PRICE_SNAPSHOT_RELOAD_MS) return;
  lastPriceSnapshotCheckMs = now;

  let stat;
  try {
    stat = fs.statSync(PRICE_LOCAL_SNAPSHOT_PATH);
  } catch {
    return;
  }
  if (!force && stat.mtimeMs === lastPriceSnapshotMtimeMs) return;

  try {
    const raw = fs.readFileSync(PRICE_LOCAL_SNAPSHOT_PATH, "utf8");
    const parsed = JSON.parse(raw);
    const data = parsed?.data || parsed?.prices || {};
    const next = new Map();
    for (const [k, row] of Object.entries(data)) {
      const base = String(k || "").trim().toUpperCase();
      if (!base) continue;
      const symbol = normalizeSymbol(base);
      if (!symbol) continue;
      const sanitized = sanitizeLocalPriceSnapshotRecord(symbol, row);
      if (!sanitized) continue;
      next.set(symbol, { ts: Date.now(), data: sanitized });
    }
    localPriceSnapshotCache.clear();
    for (const [k, v] of next) localPriceSnapshotCache.set(k, v);
    lastPriceSnapshotMtimeMs = stat.mtimeMs;
  } catch (err) {
    console.warn(`[PRICE] local snapshot parse failed: ${String(err?.message || err)}`);
  }
}

function flushLocalPriceSnapshotNow() {
  if (!priceSnapshotDirty) return;
  const data = {};
  for (const [symbol, row] of localPriceSnapshotCache.entries()) {
    const d = row?.data;
    const price = toNum(d?.regularMarketPrice);
    if (!Number.isFinite(price) || price <= 0) continue;
    const base = baseSymbol(symbol);
    data[base] = {
      symbol,
      shortName: d?.shortName || base,
      regularMarketPrice: price,
      regularMarketPreviousClose: toNum(d?.regularMarketPreviousClose),
      regularMarketChange: toNum(d?.regularMarketChange),
      regularMarketChangePercent: toNum(d?.regularMarketChangePercent),
      regularMarketOpen: toNum(d?.regularMarketOpen),
      regularMarketDayHigh: toNum(d?.regularMarketDayHigh),
      regularMarketDayLow: toNum(d?.regularMarketDayLow),
      regularMarketTime: pickBestNumber(toNum(d?.regularMarketTime), Date.now()),
      regularMarketVolume: toNum(d?.regularMarketVolume),
      averageVolume: toNum(d?.averageVolume),
      marketCap: toNum(d?.marketCap),
      trailingPE: toNum(d?.trailingPE),
      priceToBook: toNum(d?.priceToBook),
      trailingEps: toNum(d?.trailingEps),
      dividendYield: toNum(d?.dividendYield),
      beta: toNum(d?.beta),
      ts: Date.now(),
    };
  }
  const payload = {
    generatedAt: new Date().toISOString(),
    generatedAtMs: Date.now(),
    source: "local_runtime_cache",
    symbolCount: Object.keys(data).length,
    data,
  };
  try {
    const tmpPath = `${PRICE_LOCAL_SNAPSHOT_PATH}.tmp`;
    fs.writeFileSync(tmpPath, JSON.stringify(payload, null, 2), "utf8");
    fs.renameSync(tmpPath, PRICE_LOCAL_SNAPSHOT_PATH);
    priceSnapshotDirty = false;
    try {
      const stat = fs.statSync(PRICE_LOCAL_SNAPSHOT_PATH);
      lastPriceSnapshotMtimeMs = stat.mtimeMs;
    } catch {}
  } catch (err) {
    console.warn(`[PRICE] local snapshot write failed: ${String(err?.message || err)}`);
  }
}

function scheduleLocalPriceSnapshotFlush() {
  if (priceSnapshotFlushTimer) return;
  priceSnapshotFlushTimer = setTimeout(() => {
    priceSnapshotFlushTimer = null;
    flushLocalPriceSnapshotNow();
  }, PRICE_SNAPSHOT_PERSIST_MS);
  if (typeof priceSnapshotFlushTimer?.unref === "function") priceSnapshotFlushTimer.unref();
}

function setLocalPriceSnapshot(symbol, quote) {
  const sanitized = sanitizeLocalPriceSnapshotRecord(symbol, quote);
  if (!sanitized) return;
  localPriceSnapshotCache.set(symbol, { ts: Date.now(), data: sanitized });
  priceSnapshotDirty = true;
  scheduleLocalPriceSnapshotFlush();
}

function getLocalPriceSnapshot(symbol) {
  reloadLocalPriceSnapshotIfNeeded();
  const row = localPriceSnapshotCache.get(symbol);
  if (!row?.data) return null;
  return {
    ...row.data,
    _provider: "local_price_snapshot",
    _providerTs: Date.now(),
    _priceFallbackProvider: "local_price_snapshot",
    _isStalePrice: true,
  };
}

function buildHeaders() {
  return {
    "User-Agent": "Mozilla/5.0",
    Accept: "application/json",
  };
}

async function fetchWithRetry(url, options = {}, retryCount = 2) {
  let lastErr;
  for (let attempt = 0; attempt <= retryCount; attempt++) {
    try {
      const res = await fetch(url, {
        ...options,
        signal: makeTimeoutSignal(REQUEST_TIMEOUT_MS),
      });
      if (res.status === 429 || res.status >= 500) {
        if (attempt < retryCount) {
          const backoff = 300 * Math.pow(2, attempt) + Math.floor(Math.random() * 200);
          await sleep(backoff);
          continue;
        }
      }
      return res;
    } catch (err) {
      lastErr = err;
      if (attempt < retryCount) {
        const backoff = 300 * Math.pow(2, attempt) + Math.floor(Math.random() * 200);
        await sleep(backoff);
        continue;
      }
    }
  }
  throw lastErr || new Error("fetch_retry_failed");
}

function mapYahooChartRow(symbol, payload) {
  const result = payload?.chart?.result?.[0];
  const meta = result?.meta || {};
  const quote = result?.indicators?.quote?.[0] || {};
  const timestamps = result?.timestamp || [];
  const closes = quote.close || [];
  const volumes = quote.volume || [];
  const lastSeriesVolume = lastFiniteFrom(volumes, 0);
  const avgVol20 = meanTailFinite(volumes, 20, 0);
  const avgVol63 = meanTailFinite(volumes, 63, 0);

  let lastIdx = -1;
  for (let i = closes.length - 1; i >= 0; i--) {
    if (Number.isFinite(closes[i])) {
      lastIdx = i;
      break;
    }
  }
  let prevIdx = -1;
  for (let i = lastIdx - 1; i >= 0; i--) {
    if (Number.isFinite(closes[i])) {
      prevIdx = i;
      break;
    }
  }

  const seriesLastClose = lastIdx >= 0 ? toNum(closes[lastIdx]) : null;
  const seriesPrevClose = prevIdx >= 0 ? toNum(closes[prevIdx]) : null;

  const price = pickBestNumber(toNum(meta.regularMarketPrice), seriesLastClose);
  // For BIST tickers, Yahoo meta.previousClose can be inconsistent. Prefer previous bar close.
  const prevClose = pickBestNumber(seriesPrevClose, toNum(meta.previousClose), toNum(meta.chartPreviousClose));
  const change = toNum(meta.regularMarketChange) ?? (price != null && prevClose != null ? price - prevClose : null);
  const changePct =
    toNum(meta.regularMarketChangePercent) ??
    (change != null && prevClose ? (change / prevClose) * 100 : null);

  return {
    symbol,
    shortName: meta.symbol || baseSymbol(symbol),
    regularMarketPrice: price,
    regularMarketChange: change,
    regularMarketChangePercent: changePct,
    regularMarketPreviousClose: prevClose,
    regularMarketOpen: toNum(meta.regularMarketOpen),
    regularMarketDayHigh: toNum(meta.regularMarketDayHigh),
    regularMarketDayLow: toNum(meta.regularMarketDayLow),
    regularMarketTime: toNum(meta.regularMarketTime) ? Number(meta.regularMarketTime) * 1000 : Date.now(),
    regularMarketVolume: pickBestNumber(lastIdx >= 0 ? toNum(volumes[lastIdx]) : null, lastSeriesVolume, toNum(meta.regularMarketVolume)),
    marketCap: null,
    trailingPE: null,
    forwardPE: null,
    priceToBook: null,
    trailingEps: null,
    dividendYield: null,
    returnOnEquity: null,
    returnOnAssets: null,
    debtToEquity: null,
    currentRatio: null,
    revenueGrowth: null,
    earningsGrowth: null,
    grossMargins: null,
    operatingMargins: null,
    profitMargins: null,
    freeCashflow: null,
    totalDebt: null,
    totalCash: null,
    enterpriseValue: null,
    ebitda: null,
    pegRatio: null,
    fiftyTwoWeekHigh: toNum(meta.fiftyTwoWeekHigh),
    fiftyTwoWeekLow: toNum(meta.fiftyTwoWeekLow),
    averageVolume: pickBestNumber(
      toNum(meta.averageDailyVolume3Month),
      avgVol63,
      toNum(meta.averageDailyVolume10Day),
      avgVol20,
      toNum(meta.averageDailyVolume),
      toNum(meta.regularMarketVolume),
      lastSeriesVolume
    ),
    priceToSalesTrailing12Months: null,
    beta: pickBestNumber(toNum(meta.beta), toNum(meta.beta3Year), toNum(meta.beta5Year)),
    annualDividendPerShare: null,
    lastDividendPerShare: null,
    lastDividendDateMs: null,
    dividendPayoutPct: null,
    paidYears3y: null,
    regularityScore: null,
    eventCount: null,
    events: [],
    _provider: "yahoo_chart",
    _providerTs: Date.now(),
  };
}

function mapYahooQuotePriceRow(symbol, payload) {
  const row = payload?.quoteResponse?.result?.[0] || {};
  const annualDividendPerShare = toNum(row.trailingAnnualDividendRate ?? row.dividendRate);
  const dividendYield = toNum(row.trailingAnnualDividendYield ?? row.dividendYield);
  const exDate = toNum(row.exDividendDate);
  const lastDividendDateMs = Number.isFinite(exDate) ? (exDate < 10_000_000_000 ? exDate * 1000 : exDate) : null;
  const divEvents =
    Number.isFinite(lastDividendDateMs) || Number.isFinite(annualDividendPerShare)
      ? [
          {
            dateMs: Number.isFinite(lastDividendDateMs) ? lastDividendDateMs : Date.now(),
            amountPerShare: Number.isFinite(annualDividendPerShare) ? annualDividendPerShare : null,
            type: "cash",
            title: "Yahoo fallback dividend",
          },
        ]
      : [];
  return {
    symbol,
    shortName: row.shortName || baseSymbol(symbol),
    regularMarketPrice: toNum(row.regularMarketPrice),
    regularMarketChange: toNum(row.regularMarketChange),
    regularMarketChangePercent: toNum(row.regularMarketChangePercent),
    regularMarketPreviousClose: toNum(row.regularMarketPreviousClose),
    regularMarketOpen: toNum(row.regularMarketOpen),
    regularMarketDayHigh: toNum(row.regularMarketDayHigh),
    regularMarketDayLow: toNum(row.regularMarketDayLow),
    regularMarketTime: toNum(row.regularMarketTime) ? Number(row.regularMarketTime) * 1000 : Date.now(),
    regularMarketVolume: toNum(row.regularMarketVolume),
    marketCap: null,
    trailingPE: null,
    forwardPE: null,
    priceToBook: null,
    trailingEps: null,
    dividendYield,
    returnOnEquity: null,
    returnOnAssets: null,
    debtToEquity: null,
    currentRatio: null,
    revenueGrowth: null,
    earningsGrowth: null,
    grossMargins: null,
    operatingMargins: null,
    profitMargins: null,
    freeCashflow: null,
    totalDebt: null,
    totalCash: null,
    enterpriseValue: null,
    ebitda: null,
    pegRatio: null,
    fiftyTwoWeekHigh: toNum(row.fiftyTwoWeekHigh),
    fiftyTwoWeekLow: toNum(row.fiftyTwoWeekLow),
    averageVolume: pickBestNumber(
      toNum(row.averageDailyVolume3Month),
      toNum(row.averageDailyVolume10Day),
      toNum(row.averageVolume),
      toNum(row.regularMarketVolume)
    ),
    priceToSalesTrailing12Months: null,
    beta: pickBestNumber(toNum(row.beta), toNum(row.beta3Year)),
    annualDividendPerShare: annualDividendPerShare,
    lastDividendPerShare: annualDividendPerShare,
    lastDividendDateMs,
    dividendPayoutPct: null,
    paidYears3y: null,
    regularityScore: null,
    eventCount: divEvents.length || null,
    events: divEvents,
    _dividendProvider: divEvents.length ? "yahoo_quote_fallback" : null,
    _dividendProviderTs: divEvents.length ? Date.now() : null,
    _provider: "yahoo_quote_single",
    _providerTs: Date.now(),
  };
}

function pickBestNumber(...values) {
  for (const v of values) {
    if (Number.isFinite(v)) return v;
  }
  return null;
}

function mergePriceSources(primary, secondary) {
  if (!primary && !secondary) return null;
  if (!primary) return stabilizePriceFields({ ...secondary });
  if (!secondary) return stabilizePriceFields({ ...primary });

  const p = { ...primary };
  const s = secondary;

  p.shortName = s.shortName || p.shortName;
  p.regularMarketPrice = pickBestNumber(s.regularMarketPrice, p.regularMarketPrice);
  // Keep chart-derived previous close as primary source; it is more stable for BIST.
  p.regularMarketPreviousClose = pickBestNumber(p.regularMarketPreviousClose, s.regularMarketPreviousClose);
  p.regularMarketOpen = pickBestNumber(s.regularMarketOpen, p.regularMarketOpen);
  p.regularMarketDayHigh = pickBestNumber(s.regularMarketDayHigh, p.regularMarketDayHigh);
  p.regularMarketDayLow = pickBestNumber(s.regularMarketDayLow, p.regularMarketDayLow);
  p.regularMarketVolume = pickBestNumber(s.regularMarketVolume, p.regularMarketVolume);
  p.averageVolume = pickBestNumber(s.averageVolume, p.averageVolume, p.regularMarketVolume);
  p.beta = pickBestNumber(s.beta, p.beta);

  p.regularMarketChange = pickBestNumber(s.regularMarketChange, p.regularMarketChange);
  p.regularMarketChangePercent = pickBestNumber(s.regularMarketChangePercent, p.regularMarketChangePercent);
  p.dividendYield = pickBestNumber(s.dividendYield, p.dividendYield);
  p.annualDividendPerShare = pickBestNumber(s.annualDividendPerShare, p.annualDividendPerShare);
  p.lastDividendPerShare = pickBestNumber(s.lastDividendPerShare, p.lastDividendPerShare);
  p.lastDividendDateMs = pickBestNumber(s.lastDividendDateMs, p.lastDividendDateMs);
  p.eventCount = pickBestNumber(s.eventCount, p.eventCount);
  if ((!Array.isArray(p.events) || !p.events.length) && Array.isArray(s.events) && s.events.length) {
    p.events = s.events;
  }
  if (s._dividendProvider && !p._dividendProvider) p._dividendProvider = s._dividendProvider;
  if (Number.isFinite(toNum(s._dividendProviderTs)) && !Number.isFinite(toNum(p._dividendProviderTs))) {
    p._dividendProviderTs = toNum(s._dividendProviderTs);
  }

  p.regularMarketTime = pickBestNumber(s.regularMarketTime, p.regularMarketTime) || Date.now();
  stabilizePriceFields(p);
  p._provider = "yahoo_chart+quote_crosscheck";
  p._providerTs = Date.now();
  return p;
}

function stabilizePriceFields(quote) {
  const px = toNum(quote?.regularMarketPrice);
  let pc = toNum(quote?.regularMarketPreviousClose);
  let ch = toNum(quote?.regularMarketChange);
  let cp = toNum(quote?.regularMarketChangePercent);

  if (Number.isFinite(px) && !Number.isFinite(pc) && Number.isFinite(ch)) {
    pc = px - ch;
  }

  // Normalize potential scale mismatch (some sources may return ratio instead of percent).
  if (Number.isFinite(cp) && Number.isFinite(ch) && Number.isFinite(pc) && pc !== 0) {
    const expectedPct = (ch / pc) * 100;
    const delta = Math.abs(expectedPct - cp);
    const deltaX100 = Math.abs(expectedPct - cp * 100);
    const deltaDiv100 = Math.abs(expectedPct - cp / 100);
    if (deltaX100 < delta && deltaX100 < deltaDiv100) cp = cp * 100;
    else if (deltaDiv100 < delta && deltaDiv100 < deltaX100) cp = cp / 100;
  }

  // Keep percent and absolute change mathematically consistent with displayed price.
  if (Number.isFinite(px) && Number.isFinite(pc) && pc !== 0) {
    ch = px - pc;
    cp = (ch / pc) * 100;
  } else if (Number.isFinite(cp) && Number.isFinite(px) && !Number.isFinite(ch)) {
    if (Number.isFinite(pc) && pc !== 0) {
      ch = (cp / 100) * pc;
    } else if ((1 + cp / 100) !== 0) {
      pc = px / (1 + cp / 100);
      ch = px - pc;
    }
  } else if (Number.isFinite(ch) && Number.isFinite(px) && !Number.isFinite(cp) && (px - ch) !== 0) {
    pc = px - ch;
    cp = (ch / pc) * 100;
  }

  quote.regularMarketPrice = px;
  quote.regularMarketPreviousClose = Number.isFinite(pc) ? pc : null;
  quote.regularMarketChange = Number.isFinite(ch) ? ch : null;
  quote.regularMarketChangePercent = Number.isFinite(cp) ? cp : null;
  return quote;
}

function mapYahooFundamentalSummary(symbol, payload) {
  const row = payload?.quoteSummary?.result?.[0] || {};
  const fd = row.financialData || {};
  const ks = row.defaultKeyStatistics || {};
  const sd = row.summaryDetail || {};

  return {
    symbol,
    marketCap: fromYahooValue(sd.marketCap ?? fd.marketCap),
    trailingPE: fromYahooValue(sd.trailingPE),
    forwardPE: fromYahooValue(sd.forwardPE),
    priceToBook: fromYahooValue(sd.priceToBook),
    trailingEps: fromYahooValue(ks.trailingEps),
    dividendYield: fromYahooValue(sd.trailingAnnualDividendYield ?? sd.dividendYield),
    returnOnEquity: fromYahooValue(fd.returnOnEquity),
    returnOnAssets: fromYahooValue(fd.returnOnAssets),
    debtToEquity: fromYahooValue(fd.debtToEquity),
    currentRatio: fromYahooValue(fd.currentRatio),
    revenueGrowth: fromYahooValue(fd.revenueGrowth),
    earningsGrowth: fromYahooValue(fd.earningsGrowth),
    grossMargins: fromYahooValue(fd.grossMargins),
    operatingMargins: fromYahooValue(fd.operatingMargins),
    profitMargins: fromYahooValue(fd.profitMargins),
    freeCashflow: fromYahooValue(fd.freeCashflow),
    totalDebt: fromYahooValue(fd.totalDebt),
    totalCash: fromYahooValue(fd.totalCash),
    enterpriseValue: fromYahooValue(fd.enterpriseValue),
    ebitda: fromYahooValue(fd.ebitda),
    pegRatio: fromYahooValue(ks.pegRatio),
    beta: fromYahooValue(ks.beta),
    priceToSalesTrailing12Months: fromYahooValue(sd.priceToSalesTrailing12Months),
    _fundProvider: "yahoo_quoteSummary",
    _fundProviderTs: Date.now(),
  };
}

function mapYahooFundamentalQuote(symbol, payload) {
  const row = payload?.quoteResponse?.result?.[0] || {};
  return {
    symbol,
    marketCap: toNum(row.marketCap),
    trailingPE: toNum(row.trailingPE),
    forwardPE: toNum(row.forwardPE),
    priceToBook: toNum(row.priceToBook),
    trailingEps: toNum(row.epsTrailingTwelveMonths),
    dividendYield: toNum(row.trailingAnnualDividendYield),
    profitMargins: toNum(row.profitMargins),
    enterpriseValue: toNum(row.enterpriseValue),
    ebitda: toNum(row.ebitda),
    pegRatio: toNum(row.pegRatio),
    beta: toNum(row.beta),
    priceToSalesTrailing12Months: toNum(row.priceToSalesTrailing12Months),
    _fundProvider: "yahoo_quote",
    _fundProviderTs: Date.now(),
  };
}

async function fetchTradingViewScannerChunk(baseSymbols, columns) {
  if (!Array.isArray(baseSymbols) || !baseSymbols.length) return new Map();
  const payload = {
    symbols: {
      tickers: baseSymbols.map((b) => `BIST:${b}`),
      query: { types: [] },
    },
    columns,
  };
  const response = await fetchWithRetry(
    TV_SCANNER_URL,
    {
      method: "POST",
      headers: {
        ...buildHeaders(),
        "Content-Type": "application/json",
        Origin: "https://www.tradingview.com",
        Referer: "https://www.tradingview.com/",
      },
      body: JSON.stringify(payload),
    },
    1
  );
  if (!response.ok) {
    const detail = await response.text().catch(() => "");
    throw new Error(`tv_scan_${response.status}:${detail.slice(0, 180)}`);
  }
  const json = await response.json();
  const rows = Array.isArray(json?.data) ? json.data : [];
  const out = new Map();
  for (const row of rows) {
    const base = tvSymbolToBase(row?.s);
    if (!base) continue;
    const arr = Array.isArray(row?.d) ? row.d : [];
    const raw = {};
    for (let i = 0; i < columns.length; i++) raw[columns[i]] = arr[i];
    out.set(base, raw);
  }
  return out;
}

function mapTradingViewParityFund(raw) {
  const out = {
    trailingPE: toNum(raw.price_earnings_ttm),
    trailingEps: toNum(raw.earnings_per_share_basic_ttm),
    priceToBook: toNum(raw.price_book_fq),
    returnOnEquity: toUnitRatioFromPercent(raw.return_on_equity),
    returnOnAssets: toUnitRatioFromPercent(raw.return_on_assets),
    grossMargins: toUnitRatioFromPercent(raw.gross_margin_ttm),
    operatingMargins: toUnitRatioFromPercent(raw.operating_margin_ttm),
    profitMargins: toUnitRatioFromPercent(raw.net_margin_ttm),
    currentRatio: toNum(raw.current_ratio),
    quickRatio: toNum(raw.quick_ratio),
    debtToEquity: toNum(raw.debt_to_equity),
    beta: toNum(raw.beta_1_year),
    marketCap: toNum(raw.market_cap_basic),
    priceToSalesTrailing12Months: toNum(raw.price_sales),
    dividendYield: toUnitRatioFromPercent(raw.dividend_yield_recent),
    averageVolume: pickBestNumber(
      toNum(raw.average_volume_60d_calc),
      toNum(raw.average_volume_10d_calc),
      toNum(raw.volume)
    ),
    totalDebt: toNum(raw.total_debt),
  };
  const netDebt = toNum(raw.net_debt);
  if (Number.isFinite(out.totalDebt) && Number.isFinite(netDebt)) {
    out.totalCash = out.totalDebt - netDebt;
  } else {
    out.totalCash = null;
  }
  return out;
}

async function fetchTradingViewParityChunk(baseSymbols) {
  const rows = await fetchTradingViewScannerChunk(baseSymbols, TV_PARITY_COLUMNS);
  const out = new Map();
  for (const [base, raw] of rows.entries()) {
    const mapped = mapTradingViewParityFund(raw);
    if (hasAnyFundValue(mapped)) out.set(base, mapped);
  }
  return out;
}

async function getTradingViewParityForSymbols(symbols) {
  const bases = [
    ...new Set((symbols || []).map((s) => baseSymbol(String(s || "").toUpperCase())).filter(Boolean)),
  ];
  const now = Date.now();
  const out = new Map();
  const toFetch = [];

  for (const base of bases) {
    const cached = tvParityCache.get(base);
    if (cached && now - cached.ts < TV_PARITY_CACHE_MS) {
      if (cached.data) out.set(base, cached.data);
      continue;
    }
    toFetch.push(base);
  }
  if (!toFetch.length) return out;

  const chunks = chunkArray(toFetch, TV_PARITY_CHUNK_SIZE);
  for (const chunk of chunks) {
    try {
      const fetched = await enqueueFundRequest(() => fetchTradingViewParityChunk(chunk));
      const ts = Date.now();
      for (const base of chunk) {
        const row = fetched.get(base) || null;
        tvParityCache.set(base, { ts, data: row });
        if (row) out.set(base, row);
      }
    } catch (err) {
      console.warn(`[TV] parity chunk failed (${chunk.join(",")}): ${String(err?.message || err)}`);
      const ts = Date.now();
      for (const base of chunk) tvParityCache.set(base, { ts, data: null });
    }
  }
  return out;
}

function mapTradingViewPriceRow(symbol, raw) {
  const price = toNum(raw.close);
  const changeAbs = toNum(raw.change_abs);
  const changePctRaw = toNum(raw.change);
  const volume = toNum(raw.volume);
  const avgVol = pickBestNumber(toNum(raw.average_volume_60d_calc), toNum(raw.average_volume_10d_calc), volume);

  let prevClose = null;
  if (Number.isFinite(price) && Number.isFinite(changeAbs)) {
    prevClose = price - changeAbs;
  } else if (Number.isFinite(price) && Number.isFinite(changePctRaw) && (1 + changePctRaw / 100) !== 0) {
    prevClose = price / (1 + changePctRaw / 100);
  }
  const change =
    Number.isFinite(price) && Number.isFinite(prevClose)
      ? price - prevClose
      : Number.isFinite(changeAbs)
      ? changeAbs
      : null;
  const changePct =
    Number.isFinite(price) && Number.isFinite(prevClose) && prevClose !== 0
      ? ((price - prevClose) / prevClose) * 100
      : Number.isFinite(changePctRaw)
      ? changePctRaw
      : null;

  return {
    symbol,
    shortName: String(raw.name || baseSymbol(symbol)).trim() || baseSymbol(symbol),
    regularMarketPrice: price,
    regularMarketChange: Number.isFinite(change) ? change : null,
    regularMarketChangePercent: Number.isFinite(changePct) ? changePct : null,
    regularMarketPreviousClose: Number.isFinite(prevClose) ? prevClose : null,
    regularMarketOpen: null,
    regularMarketDayHigh: null,
    regularMarketDayLow: null,
    regularMarketTime: Date.now(),
    regularMarketVolume: Number.isFinite(volume) ? volume : null,
    marketCap: toNum(raw.market_cap_basic),
    trailingPE: toNum(raw.price_earnings_ttm),
    forwardPE: null,
    priceToBook: toNum(raw.price_book_fq),
    trailingEps: toNum(raw.earnings_per_share_basic_ttm),
    dividendYield: toUnitRatioFromPercent(raw.dividend_yield_recent),
    returnOnEquity: null,
    returnOnAssets: null,
    debtToEquity: null,
    currentRatio: null,
    revenueGrowth: null,
    earningsGrowth: null,
    grossMargins: null,
    operatingMargins: null,
    profitMargins: null,
    freeCashflow: null,
    totalDebt: null,
    totalCash: null,
    enterpriseValue: null,
    ebitda: null,
    pegRatio: null,
    fiftyTwoWeekHigh: null,
    fiftyTwoWeekLow: null,
    averageVolume: Number.isFinite(avgVol) ? avgVol : null,
    priceToSalesTrailing12Months: null,
    beta: toNum(raw.beta_1_year),
    annualDividendPerShare: null,
    lastDividendPerShare: null,
    lastDividendDateMs: null,
    dividendPayoutPct: null,
    paidYears3y: null,
    regularityScore: null,
    eventCount: null,
    events: [],
    _provider: "tradingview_scan_price_fallback",
    _providerTs: Date.now(),
    _priceFallbackProvider: "tradingview_scan",
  };
}

async function fetchTradingViewPriceChunk(baseSymbols) {
  const rows = await fetchTradingViewScannerChunk(baseSymbols, TV_PRICE_COLUMNS);
  const out = new Map();
  for (const [base, raw] of rows.entries()) {
    const symbol = normalizeSymbol(base);
    if (!symbol) continue;
    const mapped = mapTradingViewPriceRow(symbol, raw);
    if (Number.isFinite(toNum(mapped.regularMarketPrice))) out.set(base, stabilizePriceFields(mapped));
  }
  return out;
}

async function getTradingViewPriceForSymbols(symbols) {
  if (!PRICE_ALLOW_TV_FALLBACK) return new Map();
  const bases = [
    ...new Set((symbols || []).map((s) => baseSymbol(String(s || "").toUpperCase())).filter(Boolean)),
  ];
  const now = Date.now();
  const out = new Map();
  const toFetch = [];

  for (const base of bases) {
    const cached = tvPriceCache.get(base);
    if (cached && now - cached.ts < TV_PRICE_CACHE_MS) {
      if (cached.data) out.set(base, cached.data);
      continue;
    }
    toFetch.push(base);
  }
  if (!toFetch.length) return out;
  if (isProviderOnCooldown("tv_price")) return out;

  const chunks = chunkArray(toFetch, TV_PRICE_CHUNK_SIZE);
  for (const chunk of chunks) {
    try {
      const fetched = await enqueuePriceRequest(() => fetchTradingViewPriceChunk(chunk));
      markProviderSuccess("tv_price");
      const ts = Date.now();
      for (const base of chunk) {
        const row = fetched.get(base) || null;
        tvPriceCache.set(base, { ts, data: row });
        if (row) out.set(base, row);
      }
    } catch (err) {
      markProviderFailure("tv_price", err);
      console.warn(`[TV] price chunk failed (${chunk.join(",")}): ${String(err?.message || err)}`);
      const ts = Date.now();
      for (const base of chunk) tvPriceCache.set(base, { ts, data: null });
    }
  }
  return out;
}

function buildSnapshotPriceEstimate(symbol) {
  if (!PRICE_ALLOW_SNAPSHOT_ESTIMATE) return null;
  const fund = getFundFromCache(symbol);
  if (!fund) return null;

  const marketCap = toNum(fund.marketCap);
  const shares = toNum(fund.sharesOutstanding);
  const trailingPE = toNum(fund.trailingPE);
  const trailingEps = toNum(fund.trailingEps);
  const priceToBook = toNum(fund.priceToBook);
  const equity = toNum(fund.equity);

  const byMcap = Number.isFinite(marketCap) && Number.isFinite(shares) && shares > 0 ? marketCap / shares : null;
  const byPe = Number.isFinite(trailingPE) && Number.isFinite(trailingEps) ? trailingPE * trailingEps : null;
  const byBook =
    Number.isFinite(priceToBook) && Number.isFinite(equity) && Number.isFinite(shares) && shares > 0
      ? priceToBook * (equity / shares)
      : null;
  const price = [byMcap, byPe, byBook].find((v) => Number.isFinite(v) && v > 0);
  if (!Number.isFinite(price)) return null;

  return stabilizePriceFields({
    symbol,
    shortName: baseSymbol(symbol),
    regularMarketPrice: price,
    regularMarketChange: 0,
    regularMarketChangePercent: 0,
    regularMarketPreviousClose: price,
    regularMarketOpen: null,
    regularMarketDayHigh: null,
    regularMarketDayLow: null,
    regularMarketTime: pickBestNumber(toNum(fund.kapDisclosureDateMs), Date.now()),
    regularMarketVolume: null,
    marketCap: Number.isFinite(marketCap) ? marketCap : null,
    trailingPE: Number.isFinite(trailingPE) ? trailingPE : null,
    forwardPE: null,
    priceToBook: Number.isFinite(priceToBook) ? priceToBook : null,
    trailingEps: Number.isFinite(trailingEps) ? trailingEps : null,
    dividendYield: toNum(fund.dividendYield),
    returnOnEquity: null,
    returnOnAssets: null,
    debtToEquity: null,
    currentRatio: null,
    revenueGrowth: null,
    earningsGrowth: null,
    grossMargins: null,
    operatingMargins: null,
    profitMargins: null,
    freeCashflow: null,
    totalDebt: null,
    totalCash: null,
    enterpriseValue: null,
    ebitda: null,
    pegRatio: null,
    fiftyTwoWeekHigh: null,
    fiftyTwoWeekLow: null,
    averageVolume: null,
    priceToSalesTrailing12Months: null,
    beta: toNum(fund.beta),
    annualDividendPerShare: null,
    lastDividendPerShare: null,
    lastDividendDateMs: null,
    dividendPayoutPct: null,
    paidYears3y: null,
    regularityScore: null,
    eventCount: null,
    events: [],
    _provider: "kap_snapshot_price_estimate",
    _providerTs: Date.now(),
    _priceFallbackProvider: "kap_snapshot_estimate",
  });
}

function applyFundMetricMode(quote, symbol, metricMode, tvParityMap) {
  const mode = normalizeFundMetricMode(metricMode || FUND_METRIC_MODE_DEFAULT);
  const out = { ...quote, _fundMetricMode: mode };
  if (mode !== "tv_parity") {
    out._fundMetricModeStatus = "kap_truth";
    return out;
  }
  const base = baseSymbol(symbol);
  const tv = tvParityMap instanceof Map ? tvParityMap.get(base) : null;
  if (!tv) {
    out._fundMetricModeStatus = "fallback_kap";
    out._fundMetricModeReason = "tv_row_missing";
    return out;
  }
  const sourceMap = { ...(out._fundSourceMap || {}) };
  // Strict parity: if TradingView metric is null, keep it null instead of KAP fallback.
  for (const k of TV_PARITY_FIELDS) {
    const v = Object.prototype.hasOwnProperty.call(tv, k) ? tv[k] : null;
    out[k] = v == null ? null : v;
    if (v == null) delete sourceMap[k];
    else sourceMap[k] = "tv_parity";
  }
  out._fundSourceMap = sourceMap;
  out._fundSourceLabel = "TradingView Parity";
  out._fundProvider = "tradingview_parity";
  out._fundProviderTs = Date.now();
  out._fundMetricModeStatus = "applied";
  return out;
}

function deriveKapFundamentals(quote) {
  const out = { ...quote };
  const sourceMap = { ...(out._fundSourceMap || {}) };
  const price = toNum(out.regularMarketPrice);
  const shares = toNum(out.sharesOutstanding);
  const eps = toNum(out.trailingEps);
  const equity = toNum(out.equity);
  const revenue = toNum(out.revenue);
  const totalDebt = toNum(out.totalDebt);
  const totalLiabilities = toNum(out.totalLiabilities);
  const totalCash = toNum(out.totalCash) || 0;
  const operatingProfit = toNum(out.operatingProfit);
  const depreciationAmortization = toNum(out.depreciationAmortization);
  let ebitda = toNum(out.ebitda);
  const cfo = toNum(out.cfo);
  const capexRaw = toNum(out.capex);
  const earningsGrowth = toNum(out.earningsGrowth);
  const epsGrowth = toNum(out.epsGrowth);
  const debtBase = Number.isFinite(totalDebt) ? totalDebt : totalLiabilities;

  if (!Number.isFinite(ebitda) && Number.isFinite(operatingProfit) && Number.isFinite(depreciationAmortization)) {
    ebitda = operatingProfit + Math.abs(depreciationAmortization);
    out.ebitda = ebitda;
    sourceMap.ebitda = "derived_kap";
  }

  if (Number.isFinite(price) && Number.isFinite(shares) && shares > 0) {
    out.marketCap = price * shares;
    sourceMap.marketCap = "derived_kap_yahoo_price";
  }

  if (Number.isFinite(price) && Number.isFinite(eps) && eps !== 0) {
    out.trailingPE = price / eps;
    sourceMap.trailingPE = "derived_kap_yahoo_price";
  }

  if (Number.isFinite(out.marketCap) && Number.isFinite(equity) && equity !== 0) {
    out.priceToBook = out.marketCap / equity;
    sourceMap.priceToBook = "derived_kap";
  }

  if (Number.isFinite(out.marketCap) && Number.isFinite(debtBase)) {
    out.enterpriseValue = out.marketCap + debtBase - totalCash;
    sourceMap.enterpriseValue = "derived_kap";
  }

  if (Number.isFinite(cfo) && Number.isFinite(capexRaw)) {
    const capex = Math.abs(capexRaw);
    out.freeCashflow = cfo - capex;
    sourceMap.freeCashflow = "derived_kap";
  }

  if (Number.isFinite(out.marketCap) && Number.isFinite(revenue) && revenue !== 0) {
    out.priceToSalesTrailing12Months = out.marketCap / revenue;
    sourceMap.priceToSalesTrailing12Months = "derived_kap";
  }

  if (Number.isFinite(earningsGrowth) && earningsGrowth > 0 && Number.isFinite(out.trailingPE)) {
    const growthPct = earningsGrowth * 100;
    if (growthPct !== 0) {
      out.pegRatio = out.trailingPE / growthPct;
      sourceMap.pegRatio = "derived_kap";
    }
  }

  if (Number.isFinite(debtBase) && Number.isFinite(ebitda) && ebitda !== 0) {
    out.netDebtToEbitda = (debtBase - totalCash) / ebitda;
    sourceMap.netDebtToEbitda = "derived_kap";
  }

  // KAP disclosures frequently miss prior-period EPS context.
  // As a safe fallback, use net-income growth as EPS growth proxy.
  if (!Number.isFinite(epsGrowth) && Number.isFinite(earningsGrowth)) {
    out.epsGrowth = earningsGrowth;
    sourceMap.epsGrowth = "derived_from_earnings_growth";
  }

  if (Object.keys(sourceMap).length) {
    out._fundSourceMap = sourceMap;
    out._fundSourceLabel = "KAP + Yahoo Fiyat (Turetilmis)";
  } else if (!out._fundSourceLabel && out._fundProvider) {
    out._fundSourceLabel = out._fundProvider;
  }
  return out;
}

function mergeQuoteFund(quote, fund) {
  if (!fund) return quote;
  const mergedFund = mergeFundRecords({}, fund);
  const merged = {
    ...quote,
    ...mergedFund,
    symbol: quote.symbol,
    shortName: quote.shortName,
    regularMarketPrice: quote.regularMarketPrice,
    regularMarketChange: quote.regularMarketChange,
    regularMarketChangePercent: quote.regularMarketChangePercent,
    regularMarketPreviousClose: quote.regularMarketPreviousClose,
    regularMarketOpen: quote.regularMarketOpen,
    regularMarketDayHigh: quote.regularMarketDayHigh,
    regularMarketDayLow: quote.regularMarketDayLow,
    regularMarketTime: quote.regularMarketTime,
    regularMarketVolume: quote.regularMarketVolume,
    averageVolume: quote.averageVolume,
    beta: pickBestNumber(mergedFund.beta, quote.beta),
    _provider: `${quote._provider || "price"}+fundamentals`,
    _providerTs: Date.now(),
  };
  return deriveKapFundamentals(merged);
}

function mergeQuoteDividend(quote, dividend) {
  const merged = dividend ? { ...quote, ...dividend } : { ...quote };
  const price = toNum(merged.regularMarketPrice);
  const annualPerShare = toNum(merged.annualDividendPerShare);
  const lastPerShare = toNum(merged.lastDividendPerShare);
  const payoutPct = toNum(merged.dividendPayoutPct);

  if (!Number.isFinite(merged.eventCount) && Array.isArray(merged.events) && merged.events.length > 0) {
    merged.eventCount = merged.events.length;
  }

  if (!Number.isFinite(payoutPct) && Number.isFinite(annualPerShare) && Number.isFinite(price) && price > 0) {
    merged.dividendPayoutPct = (annualPerShare / price) * 100;
  }
  if (!Number.isFinite(merged.dividendPayoutPct) && Number.isFinite(lastPerShare) && Number.isFinite(price) && price > 0) {
    merged.dividendPayoutPct = (lastPerShare / price) * 100;
  }
  if (!Number.isFinite(toNum(merged.dividendYield)) && Number.isFinite(merged.dividendPayoutPct)) {
    merged.dividendYield = merged.dividendPayoutPct / 100;
  }
  const hasDividendData =
    Number.isFinite(toNum(merged.lastDividendDateMs)) ||
    Number.isFinite(toNum(merged.lastDividendPerShare)) ||
    Number.isFinite(toNum(merged.annualDividendPerShare)) ||
    Number.isFinite(toNum(merged.dividendYield)) ||
    Number.isFinite(toNum(merged.dividendPayoutPct)) ||
    (Number.isFinite(toNum(merged.eventCount)) && toNum(merged.eventCount) > 0) ||
    (Array.isArray(merged.events) && merged.events.length > 0);

  if (hasDividendData && !merged._dividendProvider) {
    merged._dividendProvider = "kap_dividend_snapshot";
  }
  if (hasDividendData && !Number.isFinite(toNum(merged._dividendProviderTs))) {
    merged._dividendProviderTs = Date.now();
  }
  if (!hasDividendData) {
    merged._dividendProvider = null;
    merged._dividendProviderTs = null;
  }
  return merged;
}

function withQualityFlags(quote) {
  const keyFields = [
    "trailingPE",
    "priceToBook",
    "returnOnEquity",
    "returnOnAssets",
    "debtToEquity",
    "revenueGrowth",
    "earningsGrowth",
    "profitMargins",
  ];
  const filled = keyFields.filter((k) => quote[k] != null).length;
  const coverage = Math.round((filled / keyFields.length) * 100);
  const quality = coverage >= 70 ? "LIVE" : coverage >= 30 ? "PARTIAL" : "PRICE_ONLY";
  return {
    ...quote,
    price: quote?.price ?? quote?.regularMarketPrice ?? null,
    previousClose: quote?.previousClose ?? quote?.regularMarketPreviousClose ?? null,
    changePercent: quote?.changePercent ?? quote?.regularMarketChangePercent ?? null,
    _coverage: coverage,
    _quality: quality,
  };
}

function enqueuePriceRequest(task) {
  return new Promise((resolve, reject) => {
    priceQueue.push({ task, resolve, reject });
    runPriceQueue();
  });
}

function enqueueFundRequest(task) {
  return new Promise((resolve, reject) => {
    fundQueue.push({ task, resolve, reject });
    runFundQueue();
  });
}

async function runPriceQueue() {
  if (priceQueueRunning) return;
  priceQueueRunning = true;

  while (priceQueue.length > 0) {
    const waitFor = REQUEST_INTERVAL_MS - (Date.now() - lastPriceRequestTime);
    if (waitFor > 0) await sleep(waitFor);
    const item = priceQueue.shift();
    if (!item) continue;

    try {
      const value = await item.task();
      item.resolve(value);
    } catch (err) {
      item.reject(err);
    } finally {
      lastPriceRequestTime = Date.now();
    }
  }

  priceQueueRunning = false;
}

async function runFundQueue() {
  if (fundQueueRunning) return;
  fundQueueRunning = true;

  while (fundQueue.length > 0) {
    const waitFor = REQUEST_INTERVAL_MS - (Date.now() - lastFundRequestTime);
    if (waitFor > 0) await sleep(waitFor);
    const item = fundQueue.shift();
    if (!item) continue;

    try {
      const value = await item.task();
      item.resolve(value);
    } catch (err) {
      item.reject(err);
    } finally {
      lastFundRequestTime = Date.now();
    }
  }

  fundQueueRunning = false;
}

async function fetchChart(symbol) {
  const url = new URL(`https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(symbol)}`);
  url.searchParams.set("interval", "1d");
  url.searchParams.set("range", "5d");
  const response = await fetchWithRetry(url, { headers: buildHeaders() });
  if (!response.ok) {
    const detail = await response.text().catch(() => "");
    throw new Error(`yahoo_chart_${response.status}:${detail.slice(0, 180)}`);
  }
  const json = await response.json();
  const payloadErr = json?.chart?.error;
  if (payloadErr) {
    throw new Error(`yahoo_chart_payload_${payloadErr.code || "error"}`);
  }
  return mapYahooChartRow(symbol, json);
}

async function fetchMarketChart(symbol) {
  const url = new URL(`https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(symbol)}`);
  url.searchParams.set("interval", "1d");
  url.searchParams.set("range", "5d");
  const response = await fetchWithRetry(url, { headers: buildHeaders() });
  if (!response.ok) {
    const detail = await response.text().catch(() => "");
    throw new Error(`market_chart_${symbol}_${response.status}:${detail.slice(0, 140)}`);
  }
  const json = await response.json();
  const result = json?.chart?.result?.[0];
  const meta = result?.meta || {};
  const quote = result?.indicators?.quote?.[0] || {};
  const closes = quote.close || [];

  let lastIdx = -1;
  for (let i = closes.length - 1; i >= 0; i--) {
    if (Number.isFinite(closes[i])) {
      lastIdx = i;
      break;
    }
  }
  let prevIdx = -1;
  for (let i = lastIdx - 1; i >= 0; i--) {
    if (Number.isFinite(closes[i])) {
      prevIdx = i;
      break;
    }
  }

  const seriesLastClose = lastIdx >= 0 ? toNum(closes[lastIdx]) : null;
  const seriesPrevClose = prevIdx >= 0 ? toNum(closes[prevIdx]) : null;

  const price = pickBestNumber(toNum(meta.regularMarketPrice), seriesLastClose);
  // Prefer previous bar close; meta chartPreviousClose is often stale/inconsistent for non-equity symbols.
  const prevClose = pickBestNumber(seriesPrevClose, toNum(meta.previousClose), toNum(meta.chartPreviousClose));
  const pct =
    Number.isFinite(price) && Number.isFinite(prevClose) && prevClose !== 0
      ? ((price - prevClose) / prevClose) * 100
      : toNum(meta.regularMarketChangePercent);
  return {
    symbol,
    price,
    changePercent: Number.isFinite(pct) ? pct : null,
    time: toNum(meta.regularMarketTime) ? Number(meta.regularMarketTime) * 1000 : Date.now(),
  };
}

async function fetchChartSeries(symbol, range = "1y") {
  const key = `${symbol}|${range}`;
  const cached = chartSeriesCache.get(key);
  if (cached && Date.now() - cached.ts < SERIES_CACHE_MS) return cached.data;

  const url = new URL(`https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(symbol)}`);
  url.searchParams.set("interval", "1d");
  url.searchParams.set("range", range);
  const response = await fetchWithRetry(url, { headers: buildHeaders() });
  if (!response.ok) {
    const detail = await response.text().catch(() => "");
    throw new Error(`yahoo_chart_series_${symbol}_${response.status}:${detail.slice(0, 140)}`);
  }
  const json = await response.json();
  const payloadErr = json?.chart?.error;
  if (payloadErr) throw new Error(`yahoo_chart_series_payload_${payloadErr.code || "error"}`);
  const result = json?.chart?.result?.[0];
  if (!result) throw new Error(`yahoo_chart_series_empty_${symbol}`);

  const data = {
    timestamps: Array.isArray(result.timestamp) ? result.timestamp : [],
    closes: Array.isArray(result?.indicators?.quote?.[0]?.close) ? result.indicators.quote[0].close : [],
  };
  chartSeriesCache.set(key, { ts: Date.now(), data });
  return data;
}

function buildDailyReturnMap(series) {
  const ts = Array.isArray(series?.timestamps) ? series.timestamps : [];
  const closes = Array.isArray(series?.closes) ? series.closes : [];
  const len = Math.min(ts.length, closes.length);
  const out = new Map();
  let prev = null;

  for (let i = 0; i < len; i++) {
    const t = toNum(ts[i]);
    const c = toNum(closes[i]);
    if (!Number.isFinite(t) || !Number.isFinite(c) || c <= 0) continue;
    if (Number.isFinite(prev) && prev > 0) {
      const r = c / prev - 1;
      if (Number.isFinite(r)) out.set(Number(t), r);
    }
    prev = c;
  }
  return out;
}

function calcBetaFromReturnMaps(assetRet, marketRet) {
  if (!(assetRet instanceof Map) || !(marketRet instanceof Map)) return null;
  const xs = [];
  const ys = [];
  for (const [t, ar] of assetRet.entries()) {
    const mr = marketRet.get(t);
    if (!Number.isFinite(ar) || !Number.isFinite(mr)) continue;
    ys.push(ar);
    xs.push(mr);
  }
  if (xs.length < 40) return null;

  const mx = xs.reduce((a, b) => a + b, 0) / xs.length;
  const my = ys.reduce((a, b) => a + b, 0) / ys.length;
  let cov = 0;
  let varX = 0;
  for (let i = 0; i < xs.length; i++) {
    const dx = xs[i] - mx;
    const dy = ys[i] - my;
    cov += dx * dy;
    varX += dx * dx;
  }
  cov /= xs.length;
  varX /= xs.length;
  if (!Number.isFinite(varX) || Math.abs(varX) < 1e-12) return null;
  const beta = cov / varX;
  if (!Number.isFinite(beta)) return null;
  return Math.max(-2, Math.min(4, beta));
}

async function estimateBetaFromCharts(symbol) {
  if (symbol === BETA_BENCHMARK_SYMBOL) return 1;
  const cached = betaCache.get(symbol);
  if (cached && Date.now() - cached.ts < BETA_CACHE_MS) return cached.beta;

  try {
    const getSeries = async (sym) => {
      const key = `${sym}|1y`;
      const c = chartSeriesCache.get(key);
      if (c && Date.now() - c.ts < SERIES_CACHE_MS) return c.data;
      return enqueuePriceRequest(() => fetchChartSeries(sym, "1y"));
    };
    const [assetSeries, marketSeries] = await Promise.all([
      getSeries(symbol),
      getSeries(BETA_BENCHMARK_SYMBOL),
    ]);
    const beta = calcBetaFromReturnMaps(buildDailyReturnMap(assetSeries), buildDailyReturnMap(marketSeries));
    if (Number.isFinite(beta)) {
      betaCache.set(symbol, { ts: Date.now(), beta });
      return beta;
    }
  } catch {
    // keep null and fallback later
  }
  return null;
}

async function ensureQuoteCompleteness(quote, symbol) {
  const out = { ...(quote || {}) };
  const avgVol = toNum(out.averageVolume);
  if (!Number.isFinite(avgVol)) {
    out.averageVolume = pickBestNumber(toNum(out.regularMarketVolume), 0);
  }

  let betaVal = toNum(out.beta);
  if (!Number.isFinite(betaVal)) {
    const est = await estimateBetaFromCharts(symbol);
    if (Number.isFinite(est)) {
      betaVal = est;
      out._betaSource = "derived_chart_covariance_1y";
    }
  }
  if (!Number.isFinite(betaVal)) {
    betaVal = 1;
    out._betaSource = out._betaSource || "fallback_neutral_1_00";
  }
  out.beta = betaVal;

  if (!Number.isFinite(toNum(out.averageVolume))) out.averageVolume = 0;
  return out;
}

function enforceBatchCompleteness(dataBySymbol) {
  const rows = Object.values(dataBySymbol || {});
  if (!rows.length) return;

  const avgVals = rows.map((r) => toNum(r?.averageVolume)).filter((v) => Number.isFinite(v) && v > 0);
  const volVals = rows.map((r) => toNum(r?.regularMarketVolume)).filter((v) => Number.isFinite(v) && v > 0);
  const betaVals = rows.map((r) => toNum(r?.beta)).filter((v) => Number.isFinite(v));

  const avgFallback = pickBestNumber(median(avgVals), median(volVals), 0);
  const betaFallback = pickBestNumber(median(betaVals), 1);

  for (const row of rows) {
    if (!row || typeof row !== "object") continue;
    if (!Number.isFinite(toNum(row.averageVolume))) {
      row.averageVolume = pickBestNumber(toNum(row.regularMarketVolume), avgFallback, 0);
    }
    if (!Number.isFinite(toNum(row.beta))) {
      row.beta = betaFallback;
      row._betaSource = row._betaSource || "batch_median_fallback";
    }
  }
}

async function fetchFredSeriesLastTwo(seriesId) {
  if (!seriesId) return { last: null, prev: null, source: "fred" };
  // Official public endpoint on FRED domain; no scraping.
  const csvUrl = `https://fred.stlouisfed.org/graph/fredgraph.csv?id=${encodeURIComponent(seriesId)}`;
  const r = await fetchWithRetry(csvUrl, { headers: buildHeaders() });
  if (!r.ok) throw new Error(`fred_csv_${seriesId}_${r.status}`);
  const txt = await r.text();
  const lines = txt.trim().split(/\r?\n/);
  if (lines.length < 2) return { last: null, prev: null, source: "fred" };
  let last = null;
  let prev = null;
  for (let i = lines.length - 1; i >= 1; i--) {
    const parts = lines[i].split(",");
    const val = toNum(parts[1]);
    if (Number.isFinite(val)) {
      if (last == null) last = val;
      else {
        prev = val;
        break;
      }
    }
  }
  return { last, prev, source: "fred_csv" };
}

function extractLatestNumericFromEvdsPayload(payload, seriesCode) {
  const up = String(seriesCode || "").toUpperCase();
  const arr = payload?.items || payload?.data || payload?.series || [];
  const rows = Array.isArray(arr) ? arr : [];
  const points = [];

  const dateToMs = (value) => {
    if (value == null) return null;
    const t = String(value).trim();
    if (!t) return null;
    const tr = t.replace(/[./]/g, "-");
    let m = tr.match(/^(\d{2})-(\d{2})-(\d{4})$/);
    if (m) {
      const dd = Number(m[1]);
      const mm = Number(m[2]) - 1;
      const yy = Number(m[3]);
      const d = Date.UTC(yy, mm, dd);
      return Number.isFinite(d) ? d : null;
    }
    m = tr.match(/^(\d{4})-(\d{2})-(\d{2})$/);
    if (m) {
      const yy = Number(m[1]);
      const mm = Number(m[2]) - 1;
      const dd = Number(m[3]);
      const d = Date.UTC(yy, mm, dd);
      return Number.isFinite(d) ? d : null;
    }
    m = tr.match(/^(\d{4})-(\d{2})$/);
    if (m) {
      const yy = Number(m[1]);
      const mm = Number(m[2]) - 1;
      const d = Date.UTC(yy, mm, 1);
      return Number.isFinite(d) ? d : null;
    }
    if (/^\d{8}$/.test(t)) {
      const yy = Number(t.slice(0, 4));
      const mm = Number(t.slice(4, 6)) - 1;
      const dd = Number(t.slice(6, 8));
      const d = Date.UTC(yy, mm, dd);
      return Number.isFinite(d) ? d : null;
    }
    if (/^\d{6}$/.test(t)) {
      const yy = Number(t.slice(0, 4));
      const mm = Number(t.slice(4, 6)) - 1;
      const d = Date.UTC(yy, mm, 1);
      return Number.isFinite(d) ? d : null;
    }
    const parsed = Date.parse(t);
    return Number.isFinite(parsed) ? parsed : null;
  };

  const isDateLikeKey = (k) => {
    const x = String(k || "").toUpperCase();
    return x.includes("TARIH") || x.includes("TARİH") || x.includes("DATE") || x.includes("DATETIME");
  };

  for (let idx = 0; idx < rows.length; idx++) {
    const row = rows[idx];
    if (!row || typeof row !== "object") continue;

    let value = null;
    for (const [k, v] of Object.entries(row)) {
      if (String(k).toUpperCase() === up || String(k).toUpperCase().endsWith(up)) {
        const n = toNum(v);
        if (Number.isFinite(n)) {
          value = n;
          break;
        }
      }
    }
    // Fallback: find first non-date numeric field if series key shape differs.
    if (!Number.isFinite(value)) {
      for (const [k, v] of Object.entries(row)) {
        if (isDateLikeKey(k)) continue;
        const n = toNum(v);
        if (Number.isFinite(n)) {
          value = n;
          break;
        }
      }
    }

    if (!Number.isFinite(value)) continue;

    let dateMs = null;
    for (const [k, v] of Object.entries(row)) {
      if (!isDateLikeKey(k)) continue;
      dateMs = dateToMs(v);
      if (Number.isFinite(dateMs)) break;
    }
    points.push({ idx, dateMs, value });
  }

  if (!points.length) return { last: null, prev: null };

  const hasAnyDate = points.some((p) => Number.isFinite(p.dateMs));
  if (hasAnyDate) {
    points.sort((a, b) => {
      const ad = Number.isFinite(a.dateMs) ? a.dateMs : Number.NEGATIVE_INFINITY;
      const bd = Number.isFinite(b.dateMs) ? b.dateMs : Number.NEGATIVE_INFINITY;
      if (ad === bd) return a.idx - b.idx;
      return ad - bd;
    });
  }

  const last = points[points.length - 1]?.value ?? null;
  const prev = points.length > 1 ? points[points.length - 2].value : null;
  return { last, prev };
}

async function fetchEvdsSeriesLastTwo(seriesCode) {
  if (!seriesCode || !EVDS_API_KEY) return { last: null, prev: null, source: "evds_missing_key" };
  const end = toDdMmYyyy(Date.now());
  const start = toDdMmYyyy(Date.now() - 370 * 24 * 60 * 60 * 1000);
  const url = new URL("https://evds2.tcmb.gov.tr/service/evds/series=");
  url.pathname = `/service/evds/series=${encodeURIComponent(seriesCode)}`;
  url.searchParams.set("startDate", start);
  url.searchParams.set("endDate", end);
  url.searchParams.set("type", "json");
  const r = await fetchWithRetry(url, {
    headers: {
      ...buildHeaders(),
      key: EVDS_API_KEY,
    },
  });
  if (!r.ok) throw new Error(`evds_${seriesCode}_${r.status}`);
  const json = await r.json();
  const { last, prev } = extractLatestNumericFromEvdsPayload(json, seriesCode);
  return { last, prev, source: "evds" };
}

function gradeByBands(value, bands) {
  if (!Number.isFinite(value)) return "C";
  if (value >= bands.A[0] && value <= bands.A[1]) return "A";
  if ((value >= bands.B1[0] && value <= bands.B1[1]) || (value >= bands.B2[0] && value <= bands.B2[1])) return "B";
  if ((value >= bands.C1[0] && value <= bands.C1[1]) || (value >= bands.C2[0] && value <= bands.C2[1])) return "C";
  return "D";
}

async function fetchSummaryFund(symbol) {
  let lastErr = null;
  for (const host of ["query1.finance.yahoo.com", "query2.finance.yahoo.com"]) {
    const url = new URL(`https://${host}/v10/finance/quoteSummary/${encodeURIComponent(symbol)}`);
    url.searchParams.set("modules", "financialData,defaultKeyStatistics,summaryDetail");
    try {
      const response = await fetchWithRetry(url, { headers: buildHeaders() });
      if (!response.ok) {
        const detail = await response.text().catch(() => "");
        lastErr = new Error(`yahoo_summary_${host}_${response.status}:${detail.slice(0, 180)}`);
        continue;
      }
      const json = await response.json();
      return mapYahooFundamentalSummary(symbol, json);
    } catch (err) {
      lastErr = err;
    }
  }
  throw lastErr || new Error("yahoo_summary_unavailable");
}

async function fetchQuoteFund(symbol) {
  let lastErr = null;
  for (const host of ["query1.finance.yahoo.com", "query2.finance.yahoo.com"]) {
    const url = new URL(`https://${host}/v7/finance/quote`);
    url.searchParams.set("symbols", symbol);
    try {
      const response = await fetchWithRetry(url, { headers: buildHeaders() });
      if (!response.ok) {
        const detail = await response.text().catch(() => "");
        lastErr = new Error(`yahoo_quote_${host}_${response.status}:${detail.slice(0, 180)}`);
        continue;
      }
      const json = await response.json();
      return mapYahooFundamentalQuote(symbol, json);
    } catch (err) {
      lastErr = err;
    }
  }
  throw lastErr || new Error("yahoo_quote_unavailable");
}

async function fetchQuotePrice(symbol) {
  let lastErr = null;
  for (const host of ["query1.finance.yahoo.com", "query2.finance.yahoo.com"]) {
    const url = new URL(`https://${host}/v7/finance/quote`);
    url.searchParams.set("symbols", symbol);
    try {
      const response = await fetchWithRetry(url, { headers: buildHeaders() });
      if (!response.ok) {
        const detail = await response.text().catch(() => "");
        lastErr = new Error(`yahoo_quote_price_${host}_${response.status}:${detail.slice(0, 180)}`);
        continue;
      }
      const json = await response.json();
      const row = json?.quoteResponse?.result?.[0];
      if (!row) {
        lastErr = new Error(`yahoo_quote_price_empty_${host}`);
        continue;
      }
      return mapYahooQuotePriceRow(symbol, json);
    } catch (err) {
      lastErr = err;
    }
  }
  throw lastErr || new Error("yahoo_quote_price_unavailable");
}

async function fetchFundNow(symbol, currentFund = null) {
  if (!FUND_ALLOW_YAHOO_FUND) return currentFund;
  let fetched = null;
  try {
    fetched = await enqueueFundRequest(() => fetchSummaryFund(symbol));
  } catch (summaryErr) {
    try {
      fetched = await enqueueFundRequest(() => fetchQuoteFund(symbol));
    } catch (quoteErr) {
      console.warn(
        `[YAHOO] sync fundamentals unavailable for ${symbol}: summary=${String(
          summaryErr?.message || summaryErr
        )} | quote=${String(quoteErr?.message || quoteErr)}`
      );
    }
  }
  if (!fetched) return currentFund;
  const merged = mergeFundRecords(currentFund || {}, fetched);
  fundCache.set(symbol, { ts: Date.now(), data: merged });
  return merged;
}

function getFundFromCache(symbol) {
  reloadFundCacheFromSnapshotIfNeeded();
  const cached = fundCache.get(symbol);
  if (!cached) return null;
  // Snapshot fundamentals are periodic (quarterly) and should remain usable
  // until replaced by a newer snapshot file.
  if (FUND_SOURCE !== "snapshot" && Date.now() - cached.ts > FUND_CACHE_MS) return null;
  return cached.data;
}

function getDividendFromCache(symbol) {
  reloadDividendCacheFromSnapshotIfNeeded();
  const cached = dividendCache.get(symbol);
  if (!cached) return null;
  // Snapshot dividend data should remain valid until next snapshot replace.
  return cached.data;
}

function getFundSnapshotHealth(forceReload = false) {
  if (forceReload) reloadFundCacheFromSnapshotIfNeeded(true);
  if (lastFundSnapshotHealth) {
    return {
      ...lastFundSnapshotHealth,
      cacheSymbols: fundCache.size,
      fundSourceMode: FUND_SOURCE,
      externalFundEnabled: FUND_ALLOW_YAHOO_FUND,
    };
  }
  try {
    const raw = fs.readFileSync(FUND_SNAPSHOT_PATH, "utf8");
    const parsed = JSON.parse(raw);
    const health = analyzeFundSnapshot(parsed);
    lastFundSnapshotHealth = health;
    return {
      ...health,
      cacheSymbols: fundCache.size,
      fundSourceMode: FUND_SOURCE,
      externalFundEnabled: FUND_ALLOW_YAHOO_FUND,
    };
  } catch (err) {
    return {
      generatedAt: null,
      generatedAtMs: null,
      source: "kap",
      symbolCoverage: { symbolCount: 0, nonNullSymbols: 0, coveragePct: 0 },
      failureCount: 0,
      failuresSummary: [{ reason: String(err?.message || err), count: 1 }],
      fieldCoverage: {},
      lastRunStatus: "unavailable",
      lowConfidence: true,
      cacheSymbols: fundCache.size,
      fundSourceMode: FUND_SOURCE,
      externalFundEnabled: FUND_ALLOW_YAHOO_FUND,
    };
  }
}

function getDividendSnapshotHealth(forceReload = false) {
  if (forceReload) reloadDividendCacheFromSnapshotIfNeeded(true);
  if (lastDividendSnapshotHealth) {
    return {
      ...lastDividendSnapshotHealth,
      cacheSymbols: dividendCache.size,
    };
  }
  try {
    const raw = fs.readFileSync(DIVIDEND_SNAPSHOT_PATH, "utf8");
    const parsed = JSON.parse(raw);
    const health = analyzeDividendSnapshot(parsed);
    lastDividendSnapshotHealth = health;
    return { ...health, cacheSymbols: dividendCache.size };
  } catch (err) {
    return {
      generatedAt: null,
      generatedAtMs: null,
      source: "kap_dividend",
      symbolCoverage: { symbolCount: 0, nonNullSymbols: 0, coveragePct: 0 },
      failureCount: 0,
      failuresSummary: [{ reason: String(err?.message || err), count: 1 }],
      fieldCoverage: {},
      lastRunStatus: "unavailable",
      lowConfidence: true,
      cacheSymbols: dividendCache.size,
    };
  }
}

function refreshFundInBackground(symbol) {
  if (!FUND_ALLOW_YAHOO_FUND) return;
  const current = getFundFromCache(symbol);
  if (FUND_SOURCE === "snapshot" && !needsFundRefresh(current)) return;
  if (fundRefreshInFlight.has(symbol)) return;
  fundRefreshInFlight.add(symbol);
  (async () => {
    try {
      let fund = null;
      try {
        fund = await enqueueFundRequest(() => fetchSummaryFund(symbol));
      } catch (summaryErr) {
        try {
          fund = await enqueueFundRequest(() => fetchQuoteFund(symbol));
        } catch (quoteErr) {
          console.warn(
            `[YAHOO] fundamentals unavailable for ${symbol}: summary=${String(
              summaryErr?.message || summaryErr
            )} | quote=${String(quoteErr?.message || quoteErr)}`
          );
        }
      }
      if (fund) {
        const merged = mergeFundRecords(current || {}, fund);
        fundCache.set(symbol, { ts: Date.now(), data: merged });
      }
    } finally {
      fundRefreshInFlight.delete(symbol);
    }
  })();
}

async function getQuote(symbol, options = {}) {
  const metricMode = normalizeFundMetricMode(options?.metricMode || FUND_METRIC_MODE_DEFAULT);
  const tvParityMap = options?.tvParityMap instanceof Map ? options.tvParityMap : null;
  const tvPriceMap = options?.tvPriceMap instanceof Map ? options.tvPriceMap : null;
  const cached = priceCache.get(symbol);
  if (cached && Date.now() - cached.ts < PRICE_CACHE_MS) {
    let fund = getFundFromCache(symbol);
    if (FUND_ALLOW_YAHOO_FUND && FUND_SYNC_TOPUP && needsFundRefresh(fund)) {
      fund = await fetchFundNow(symbol, fund);
    }
    const dividend = getDividendFromCache(symbol);
    let mergedCached = mergeQuoteDividend(mergeQuoteFund(cached.data, fund), dividend);
    mergedCached = await ensureQuoteCompleteness(mergedCached, symbol);
    mergedCached = applyFundMetricMode(mergedCached, symbol, metricMode, tvParityMap);
    refreshFundInBackground(symbol);
    return mergedCached;
  }

  if (PRICE_FORCE_LOCAL_SNAPSHOT) {
    const localOnly = getLocalPriceSnapshot(symbol);
    if (localOnly && Number.isFinite(toNum(localOnly.regularMarketPrice))) {
      priceCache.set(symbol, { ts: Date.now(), data: localOnly });
      let fund = getFundFromCache(symbol);
      if (FUND_ALLOW_YAHOO_FUND && FUND_SYNC_TOPUP && needsFundRefresh(fund)) {
        fund = await fetchFundNow(symbol, fund);
      }
      const dividend = getDividendFromCache(symbol);
      let mergedLocal = mergeQuoteDividend(mergeQuoteFund(localOnly, fund), dividend);
      mergedLocal = await ensureQuoteCompleteness(mergedLocal, symbol);
      mergedLocal = applyFundMetricMode(mergedLocal, symbol, metricMode, tvParityMap);
      refreshFundInBackground(symbol);
      return mergedLocal;
    }
  }

  let chartQuote = null;
  let quotePrice = null;
  if (!isProviderOnCooldown("yahoo_price")) {
    try {
      chartQuote = await enqueuePriceRequest(() => fetchChart(symbol));
      markProviderSuccess("yahoo_price");
    } catch (err) {
      markProviderFailure("yahoo_price", err);
      // keep trying with quote endpoint below when provider is still healthy
    }
  }

  const hasChartPrice = Number.isFinite(toNum(chartQuote?.regularMarketPrice));
  if (!hasChartPrice && !isProviderOnCooldown("yahoo_price")) {
    try {
      quotePrice = await enqueuePriceRequest(() => fetchQuotePrice(symbol));
      markProviderSuccess("yahoo_price");
    } catch (err) {
      markProviderFailure("yahoo_price", err);
      // keep chart result if quote failed
    }
  }

  let quote = mergePriceSources(chartQuote, quotePrice);
  if ((!quote || !Number.isFinite(quote.regularMarketPrice)) && PRICE_ALLOW_TV_FALLBACK) {
    const base = baseSymbol(symbol);
    let tvPrice = tvPriceMap instanceof Map ? tvPriceMap.get(base) : null;
    if (!tvPrice) {
      const fetched = await getTradingViewPriceForSymbols([symbol]);
      tvPrice = fetched.get(base) || null;
    }
    if (tvPrice && Number.isFinite(toNum(tvPrice.regularMarketPrice))) {
      quote = { ...tvPrice };
    }
  }
  if ((!quote || !Number.isFinite(quote.regularMarketPrice)) && PRICE_ALLOW_SNAPSHOT_ESTIMATE) {
    const snapshotPrice = buildSnapshotPriceEstimate(symbol);
    if (snapshotPrice && Number.isFinite(toNum(snapshotPrice.regularMarketPrice))) {
      quote = { ...snapshotPrice };
    }
  }
  if ((!quote || !Number.isFinite(quote.regularMarketPrice)) && PRICE_ALLOW_LOCAL_SNAPSHOT_FALLBACK) {
    const localPrice = getLocalPriceSnapshot(symbol);
    if (localPrice && Number.isFinite(toNum(localPrice.regularMarketPrice))) {
      quote = { ...localPrice };
    }
  }
  if (!quote || !Number.isFinite(quote.regularMarketPrice)) {
    const code = PRICE_ALLOW_TV_FALLBACK
      ? (PRICE_ALLOW_SNAPSHOT_ESTIMATE ? "price_unavailable_yahoo_tv_snapshot" : "price_unavailable_yahoo_tv")
      : "yahoo_price_unavailable";
    throw new Error(code);
  }
  stabilizePriceFields(quote);
  setLocalPriceSnapshot(symbol, quote);

  priceCache.set(symbol, { ts: Date.now(), data: quote });
  let fund = getFundFromCache(symbol);
  if (FUND_ALLOW_YAHOO_FUND && FUND_SYNC_TOPUP && needsFundRefresh(fund)) {
    fund = await fetchFundNow(symbol, fund);
  }
  const dividend = getDividendFromCache(symbol);
  let merged = mergeQuoteDividend(mergeQuoteFund(quote, fund), dividend);
  merged = await ensureQuoteCompleteness(merged, symbol);
  merged = applyFundMetricMode(merged, symbol, metricMode, tvParityMap);
  refreshFundInBackground(symbol);
  return merged;
}

// ── RSS ───────────────────────────────────────────────────────────────────────
const RSS_URL      = "https://bigpara.hurriyet.com.tr/rss/";
const RSS_CACHE_MS = 55_000;
let _rssCacheData  = null;
let _rssCacheTime  = 0;

function extractRssTag(xml, tag) {
  const cdataRe = new RegExp(`<${tag}[^>]*>\\s*<!\\[CDATA\\[([\\s\\S]*?)\\]\\]>\\s*<\\/${tag}>`, "i");
  const plainRe = new RegExp(`<${tag}[^>]*>([^<]*)<\\/${tag}>`, "i");
  const m = xml.match(cdataRe) || xml.match(plainRe);
  return m ? m[1].trim() : "";
}

function parseRssXml(xml) {
  const items = [];
  const itemRe = /<item>([\s\S]*?)<\/item>/g;
  let m;
  while ((m = itemRe.exec(xml)) !== null && items.length < 20) {
    const inner = m[1];
    const link = extractRssTag(inner, "link") || extractRssTag(inner, "guid");
    items.push({
      title:       extractRssTag(inner, "title"),
      link,
      pubDate:     extractRssTag(inner, "pubDate"),
      description: extractRssTag(inner, "description"),
    });
  }
  return items.filter(it => it.title);
}

app.get("/api/rss", async (_req, res) => {
  const now = Date.now();
  if (_rssCacheData && (now - _rssCacheTime) < RSS_CACHE_MS) {
    return res.json(_rssCacheData);
  }
  try {
    const r = await fetch(RSS_URL, {
      headers: { "User-Agent": "Mozilla/5.0 (compatible; FKY/1.0)" },
      signal: makeTimeoutSignal(10_000),
    });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const xml = await r.text();
    const items = parseRssXml(xml);
    _rssCacheData = { items, ts: now };
    _rssCacheTime = now;
    res.json(_rssCacheData);
  } catch (err) {
    if (_rssCacheData) return res.json(_rssCacheData);
    res.status(502).json({ error: String(err?.message || err), items: [] });
  }
});
// ─────────────────────────────────────────────────────────────────────────────

// ── AUTH ROUTES ───────────────────────────────────────────────────────────────
app.get("/auth/login", (_req, res) => {
  res.setHeader("Content-Type", "text/html; charset=utf-8");
  res.send(buildLoginPage());
});

app.post("/auth/login", (req, res) => {
  if (!AUTH_ENABLED) return res.redirect("/");
  const rate = getAuthRateInfo(req);
  if (rate.blocked) {
    res.setHeader("Retry-After", String(rate.retryAfterSec));
    res.status(429);
    res.setHeader("Content-Type", "text/html; charset=utf-8");
    return res.send(buildLoginPage(`Çok fazla hatalı giriş denemesi. ${rate.retryAfterSec} sn sonra tekrar deneyin.`));
  }
  const { username = "", password = "" } = req.body || {};
  let valid = false;
  try {
    const uBuf = Buffer.from(username.trim());
    const pBuf = Buffer.from(password);
    const uExp = Buffer.from(AUTH_USER);
    const pExp = Buffer.from(AUTH_PASS);
    const uOk = uBuf.length === uExp.length && crypto.timingSafeEqual(uBuf, uExp);
    const pOk = pBuf.length === pExp.length && crypto.timingSafeEqual(pBuf, pExp);
    valid = uOk && pOk;
  } catch { valid = false; }
  if (valid) {
    clearAuthLoginFailures(rate.key);
    res.setHeader("Set-Cookie", buildSessionCookie(makeSessionToken()));
    return res.redirect("/");
  }
  recordAuthLoginFailure(rate.key);
  res.setHeader("Content-Type", "text/html; charset=utf-8");
  res.send(buildLoginPage("Kullanıcı adı veya şifre hatalı."));
});

app.get("/auth/logout", (_req, res) => {
  res.setHeader("Set-Cookie", clearSessionCookie());
  res.redirect("/auth/login");
});
// ─────────────────────────────────────────────────────────────────────────────

app.get("/api/quotes", async (req, res) => {
  try {
    const symbolsParam = String(req.query.symbols || "");
    if (!symbolsParam.trim()) return res.status(400).json({ error: "symbols gerekli" });

    const symbols = [...new Set(symbolsParam.split(",").map(normalizeSymbol).filter(Boolean))];
    if (!symbols.length) return res.status(400).json({ error: "geçerli sembol bulunamadı" });
    const metricMode = normalizeFundMetricMode(req.query.metricMode || FUND_METRIC_MODE_DEFAULT);
    const tvParityMap = metricMode === "tv_parity" ? await getTradingViewParityForSymbols(symbols) : null;
    const tvPriceMap = PRICE_ALLOW_TV_FALLBACK ? await getTradingViewPriceForSymbols(symbols) : null;

    const data = {};
    const failures = [];

    // Correctness-first: process sequentially to reduce provider pressure and partial failures.
    for (const symbol of symbols) {
      try {
        const quote = await getQuote(symbol, { metricMode, tvParityMap, tvPriceMap });
        data[baseSymbol(symbol)] = withQualityFlags(quote);
      } catch (err) {
        failures.push({ symbol, reason: String(err?.message || err) });
      }
    }
    enforceBatchCompleteness(data);
    const tvMatched =
      metricMode === "tv_parity" && tvParityMap instanceof Map
        ? symbols.map(baseSymbol).filter((b) => tvParityMap.has(b)).length
        : null;
    const tvPriceMatched =
      PRICE_ALLOW_TV_FALLBACK && tvPriceMap instanceof Map
        ? symbols.map(baseSymbol).filter((b) => tvPriceMap.has(b)).length
        : null;
    const priceFallbackUsage = Object.values(data).reduce(
      (acc, row) => {
        const p = row?._priceFallbackProvider;
        if (p === "tradingview_scan") acc.tradingview += 1;
        if (p === "kap_snapshot_estimate") acc.snapshotEstimate += 1;
        if (p === "local_price_snapshot") acc.localSnapshot += 1;
        return acc;
      },
      { tradingview: 0, snapshotEstimate: 0, localSnapshot: 0 }
    );
    const providerCooldown = {
      yahooPriceRetryInSec: Math.max(0, Math.ceil(((toNum(providerBackoffUntil.yahoo_price) || 0) - Date.now()) / 1000)),
      tvPriceRetryInSec: Math.max(0, Math.ceil(((toNum(providerBackoffUntil.tv_price) || 0) - Date.now()) / 1000)),
    };
    const priceProviderBase = PRICE_FORCE_LOCAL_SNAPSHOT ? "local_snapshot_forced" : "yahoo_price";

    res.json({
      provider:
        metricMode === "tv_parity"
          ? `${priceProviderBase}${PRICE_ALLOW_TV_FALLBACK ? "+tradingview_price_fallback" : ""}${PRICE_ALLOW_SNAPSHOT_ESTIMATE ? "+snapshot_price_estimate_fallback" : ""}${PRICE_ALLOW_LOCAL_SNAPSHOT_FALLBACK ? "+local_snapshot_price_fallback" : ""}+tradingview_parity+kap_dividend`
          : `${priceProviderBase}${PRICE_ALLOW_TV_FALLBACK ? "+tradingview_price_fallback" : ""}${PRICE_ALLOW_SNAPSHOT_ESTIMATE ? "+snapshot_price_estimate_fallback" : ""}${PRICE_ALLOW_LOCAL_SNAPSHOT_FALLBACK ? "+local_snapshot_price_fallback" : ""}+kap_fund+kap_dividend`,
      metricMode,
      metricModeDefault: FUND_METRIC_MODE_DEFAULT,
      quoteCount: Object.keys(data).length,
      failedCount: failures.length,
      tvParityCoverage:
        metricMode === "tv_parity"
          ? {
              requestedSymbols: symbols.length,
              matchedSymbols: tvMatched || 0,
              coveragePct: symbols.length ? +(((tvMatched || 0) / symbols.length) * 100).toFixed(1) : 0,
            }
          : null,
      tvPriceFallbackCoverage:
        PRICE_ALLOW_TV_FALLBACK
          ? {
              enabled: true,
              requestedSymbols: symbols.length,
              matchedSymbols: tvPriceMatched || 0,
              coveragePct: symbols.length ? +(((tvPriceMatched || 0) / symbols.length) * 100).toFixed(1) : 0,
            }
          : { enabled: false },
      priceFallbackUsage,
      providerCooldown,
      data,
      failures,
      ts: Date.now(),
    });
  } catch (err) {
    console.error("[/api/quotes] fatal", err);
    res.status(500).json({ error: "Veri alınamadı" });
  }
});

app.get("/api/debug-symbol", async (req, res) => {
  const symbol = normalizeSymbol(req.query.symbol);
  if (!symbol) return res.status(400).json({ error: "geçerli symbol gerekli" });

  const out = { symbol };
  try {
    out.chart = await enqueuePriceRequest(() => fetchChart(symbol));
  } catch (err) {
    out.chart = { error: String(err?.message || err) };
  }
  try {
    if (FUND_SOURCE === "snapshot") {
      reloadFundCacheFromSnapshotIfNeeded(true);
      out.summary = getFundFromCache(symbol) || { error: "snapshot_fund_not_found" };
    } else if (FUND_ALLOW_YAHOO_FUND) {
      out.summary = await enqueueFundRequest(() => fetchSummaryFund(symbol));
    } else {
      out.summary = { info: "external_fund_disabled" };
    }
  } catch (err) {
    out.summary = { error: String(err?.message || err) };
  }
  try {
    if (FUND_SOURCE === "snapshot") {
      out.quote = { info: "snapshot_mode_enabled" };
    } else if (FUND_ALLOW_YAHOO_FUND) {
      out.quote = await enqueueFundRequest(() => fetchQuoteFund(symbol));
    } else {
      out.quote = { info: "external_fund_disabled" };
    }
  } catch (err) {
    out.quote = { error: String(err?.message || err) };
  }
  try {
    reloadDividendCacheFromSnapshotIfNeeded(true);
    out.dividend = getDividendFromCache(symbol) || { info: "dividend_snapshot_not_found" };
  } catch (err) {
    out.dividend = { error: String(err?.message || err) };
  }
  res.json(out);
});

app.get("/api/fundamentals-health", (_req, res) => {
  try {
    const health = getFundSnapshotHealth(true);
    const dividend = getDividendSnapshotHealth(true);
    res.json({
      provider: "kap_snapshot",
      ts: Date.now(),
      metricModeDefault: FUND_METRIC_MODE_DEFAULT,
      supportedMetricModes: FUND_METRIC_MODES,
      ...health,
      dividend,
    });
  } catch (err) {
    res.status(500).json({ error: String(err?.message || err) });
  }
});

app.get("/api/macro-official", async (_req, res) => {
  try {
    const [vix, spx, dxy, usdtry, trRate, trCpi, trGrowth, trUnemp] = await Promise.all([
      fetchFredSeriesLastTwo("VIXCLS").catch((e) => ({ last: null, prev: null, error: String(e?.message || e) })),
      fetchFredSeriesLastTwo("SP500").catch((e) => ({ last: null, prev: null, error: String(e?.message || e) })),
      // DXY official proxy from FRED broad dollar index
      fetchFredSeriesLastTwo("DTWEXBGS").catch((e) => ({ last: null, prev: null, error: String(e?.message || e) })),
      fetchEvdsSeriesLastTwo(EVDS_SERIES_USDTRY).catch((e) => ({ last: null, prev: null, error: String(e?.message || e) })),
      fetchEvdsSeriesLastTwo(EVDS_SERIES_POLICY_RATE).catch((e) => ({ last: null, prev: null, error: String(e?.message || e) })),
      fetchEvdsSeriesLastTwo(EVDS_SERIES_CPI).catch((e) => ({ last: null, prev: null, error: String(e?.message || e) })),
      fetchEvdsSeriesLastTwo(EVDS_SERIES_GROWTH).catch((e) => ({ last: null, prev: null, error: String(e?.message || e) })),
      fetchEvdsSeriesLastTwo(EVDS_SERIES_UNEMP).catch((e) => ({ last: null, prev: null, error: String(e?.message || e) })),
    ]);

    const vixGrade = gradeByBands(vix.last, {
      A: [15, 22],
      B1: [12, 15],
      B2: [22, 28],
      C1: [10, 12],
      C2: [28, 35],
    });

    let spxGrade = "C";
    if (Number.isFinite(spx.last) && Number.isFinite(spx.prev) && spx.prev !== 0) {
      const chg = ((spx.last - spx.prev) / spx.prev) * 100;
      if (chg >= 0.7) spxGrade = "A";
      else if (chg >= -0.3) spxGrade = "B";
      else if (chg >= -1.5) spxGrade = "C";
      else spxGrade = "D";
    }

    let dxyGrade = "C";
    if (Number.isFinite(dxy.last) && Number.isFinite(dxy.prev) && dxy.prev !== 0) {
      const chg = ((dxy.last - dxy.prev) / dxy.prev) * 100;
      if (chg <= -0.2) dxyGrade = "A";
      else if (chg <= 0.2) dxyGrade = "B";
      else if (chg <= 0.6) dxyGrade = "C";
      else dxyGrade = "D";
    }

    // TR block: if official EVDS series missing, default neutral C.
    let trRateGrade = "C";
    if (Number.isFinite(trRate.last) && Number.isFinite(trCpi.last)) {
      const real = trRate.last - trCpi.last;
      if (real >= 0.5) trRateGrade = "A";
      else if (real >= -1.0) trRateGrade = "B";
      else if (real >= -4.0) trRateGrade = "C";
      else trRateGrade = "D";
    } else if (Number.isFinite(trRate.last)) {
      trRateGrade = "B";
    }

    let trInflGrade = "C";
    if (Number.isFinite(trCpi.last) && Number.isFinite(trCpi.prev) && trCpi.prev !== 0) {
      const chg = ((trCpi.last - trCpi.prev) / Math.abs(trCpi.prev)) * 100;
      if (chg <= -0.5) trInflGrade = "A";
      else if (chg <= 0.2) trInflGrade = "B";
      else if (chg <= 1.0) trInflGrade = "C";
      else trInflGrade = "D";
    }

    let trGrowthGrade = "C";
    if (Number.isFinite(trGrowth.last) && Number.isFinite(trUnemp.last)) {
      if (trGrowth.last >= 3 && trUnemp.last <= 10) trGrowthGrade = "A";
      else if (trGrowth.last >= 1) trGrowthGrade = "B";
      else if (trGrowth.last >= 0) trGrowthGrade = "C";
      else trGrowthGrade = "D";
    } else if (Number.isFinite(trGrowth.last)) {
      if (trGrowth.last >= 3) trGrowthGrade = "A";
      else if (trGrowth.last >= 1) trGrowthGrade = "B";
      else if (trGrowth.last >= 0) trGrowthGrade = "C";
      else trGrowthGrade = "D";
    } else if (Number.isFinite(trUnemp.last)) {
      if (trUnemp.last <= 8) trGrowthGrade = "A";
      else if (trUnemp.last <= 10.5) trGrowthGrade = "B";
      else if (trUnemp.last <= 13) trGrowthGrade = "C";
      else trGrowthGrade = "D";
    }

    let trFxGrade = "C";
    if (Number.isFinite(usdtry.last) && Number.isFinite(usdtry.prev) && usdtry.prev !== 0) {
      const fxChg = ((usdtry.last - usdtry.prev) / usdtry.prev) * 100;
      if (fxChg < 0.2) trFxGrade = "A";
      else if (fxChg < 0.8) trFxGrade = "B";
      else if (fxChg < 2.0) trFxGrade = "C";
      else trFxGrade = "D";
    }

    const missingEvdsSeries = [];
    if (!EVDS_SERIES_POLICY_RATE) missingEvdsSeries.push("policyRate");
    if (!EVDS_SERIES_CPI) missingEvdsSeries.push("cpi");
    if (!EVDS_SERIES_GROWTH) missingEvdsSeries.push("growth");
    if (!EVDS_SERIES_UNEMP) missingEvdsSeries.push("unemp");
    const trInputsMissing = !Number.isFinite(trRate.last) || !Number.isFinite(trCpi.last) || !Number.isFinite(usdtry.last);

    res.json({
      provider: "official_fred_evds",
      ts: Date.now(),
      grades: {
        vix: vixGrade,
        spx: spxGrade,
        dxy: dxyGrade,
        trRate: trRateGrade,
        trInfl: trInflGrade,
        trGrowth: trGrowthGrade,
        trFx: trFxGrade,
      },
      raw: { vix, spx, dxy, usdtry, trRate, trCpi, trGrowth, trUnemp },
      config: {
        evdsSeries: {
          usdtry: EVDS_SERIES_USDTRY,
          policyRate: EVDS_SERIES_POLICY_RATE,
          cpi: EVDS_SERIES_CPI,
          growth: EVDS_SERIES_GROWTH,
          unemp: EVDS_SERIES_UNEMP,
        },
        evdsKeyConfigured: Boolean(EVDS_API_KEY),
        fredKeyConfigured: Boolean(FRED_API_KEY),
        missingEvdsSeries,
        trBlockLowConfidence: !EVDS_API_KEY || missingEvdsSeries.length > 0 || trInputsMissing,
      },
    });
  } catch (err) {
    res.status(500).json({ error: String(err?.message || err) });
  }
});

app.get("/api/market-overview", async (_req, res) => {
  const marketSymbols = [
    { id: "XU100", symbol: "XU100.IS" },
    { id: "XU030", symbol: "XU030.IS" },
    { id: "SPX", symbol: "^GSPC" },
    { id: "DXY", symbol: "DX-Y.NYB" },
    { id: "USDTRY", symbol: "TRY=X" },
    { id: "EURTRY", symbol: "EURTRY=X" },
    { id: "XAU", symbol: "GC=F" },
    { id: "XAG", symbol: "SI=F" },
    { id: "BRENT", symbol: "BZ=F" },
    { id: "VIX", symbol: "^VIX" },
  ];

  const data = {};
  const failures = [];
  await Promise.all(
    marketSymbols.map(async (item) => {
      try {
        // Keep top-bar data fresh; these are only a few symbols.
        const row = await fetchMarketChart(item.symbol);
        if (Number.isFinite(row.price)) data[item.id] = row;
        else failures.push({ id: item.id, symbol: item.symbol, reason: "price_unavailable" });
      } catch (err) {
        failures.push({ id: item.id, symbol: item.symbol, reason: String(err?.message || err) });
      }
    })
  );

  res.json({
    provider: "yahoo_chart",
    data,
    failures,
    ts: Date.now(),
  });
});

const PORT = Number(process.env.PORT || 3000);
const HOST = process.env.HOST ? String(process.env.HOST) : "127.0.0.1";
const PORT_RETRY_COUNT = Math.max(0, Math.floor(toNum(process.env.PORT_RETRY_COUNT) || 5));

function logStartUrl(port) {
  console.log(`[START] Project root -> ${__dirname}`);
  console.log(`[START] PID -> ${process.pid}`);
  if (HOST) {
    console.log(`FKY LIVE çalışıyor -> http://${HOST}:${port}`);
    return;
  }
  console.log(`FKY LIVE çalışıyor -> http://localhost:${port}`);
  console.log(`Yerel erişim -> http://127.0.0.1:${port}`);
}

function startServer(port, retriesLeft) {
  const server = HOST
    ? app.listen(port, HOST, () => {
        logStartUrl(port);
      })
    : app.listen(port, () => {
        logStartUrl(port);
      });

  server.on("error", (err) => {
    if (err?.code === "EADDRINUSE") {
      if (retriesLeft > 0) {
        const nextPort = port + 1;
        console.warn(
          `[START] Port kullanımda: ${HOST || "0.0.0.0"}:${port}. Otomatik olarak ${nextPort} deneniyor...`
        );
        setTimeout(() => startServer(nextPort, retriesLeft - 1), 0);
        return;
      }
      console.error(
        `[START] Port kullanımda: ${HOST || "0.0.0.0"}:${port}. Otomatik deneme limiti doldu. ` +
          `PORT=3001 npm start gibi farklı bir port deneyin.`
      );
      return;
    }
    if (err?.code === "EPERM") {
      console.error(
        `[START] Port dinleme izni yok: ${HOST || "0.0.0.0"}:${port}. HOST=127.0.0.1 ve farklı bir PORT (örn. 3001) deneyin.`
      );
      return;
    }
    console.error("[START] Sunucu başlatma hatası:", err);
  });
}

startServer(PORT, PORT_RETRY_COUNT);
