# FKY Terminal – Architecture & Data Rules

## 1. CURRENT SITUATION

Project Goal:
Live BIST data for ~197 stocks to feed scoring system.

Current State:
- Node.js backend
- Yahoo Finance API used
- Batch requests implemented
- 60 second cache
- Yahoo returns "Unauthorized" error

Problem:
Yahoo Finance has restricted v7/finance/quote endpoint.
Direct server-side scraping is unstable and can be blocked anytime.

Conclusion:
Yahoo API is NOT production safe.

---

## 2. FINNHUB FREE PLAN LIMITS

Free Plan Limits:
- 60 REST API calls per minute
- WebSocket supports 50 symbols simultaneously
- All REST endpoints share same rate limit

Implication for 197 Stocks:
- REST polling → ~3–4 minutes to refresh all
- WebSocket → only 50 symbols live at same time

Conclusion:
Full 197 real-time refresh in 60 seconds NOT possible on free tier.

---

## 3. REQUIRED ARCHITECTURE CHANGES

### A) Remove Yahoo dependency
Delete:
- All Yahoo endpoints
- Any direct query1.finance.yahoo.com calls

Replace with:
- Finnhub REST + WebSocket hybrid model

---

### B) Hybrid Data Strategy (Recommended)

1. WebSocket Layer
   - Subscribe top 50 actively monitored stocks
   - Real-time price stream
   - No rate limit consumption

2. REST Polling Layer
   - Remaining stocks updated every 2-3 minutes
   - Batch rotation system
   - Respect 60 calls/min limit

3. Cache Layer
   - Keep 60 second memory cache
   - Prevent duplicate requests

---

## 4. BACKEND CHANGES REQUIRED

### 4.1 Add Finnhub API Key
Store securely in:

.env file:
FINNHUB_API_KEY=YOUR_KEY_HERE

Use dotenv in server.js.

---

### 4.2 Replace /api/quotes Logic

Old:
Yahoo quote endpoint

New:
Finnhub REST endpoint:

GET https://finnhub.io/api/v1/quote?symbol=THYAO.IS&token=API_KEY

Must implement:
- Request queue
- Rate limit guard (max 1 request/sec)
- Auto rotation of stock list

---

### 4.3 Add WebSocket Server

Use:
wss://ws.finnhub.io?token=API_KEY

Server responsibilities:
- Maintain connection
- Auto reconnect
- Manage 50-symbol subscription batches
- Forward live updates to frontend

---

## 5. FRONTEND CHANGES REQUIRED

1. Modify fetchBatch() to call backend only
2. Remove direct external API calls
3. Add WebSocket listener
4. Update scoring system when new price arrives

Important:
UI structure MUST NOT change.

---

## 6. PRODUCTION RULES

- Never expose API key to frontend
- Never call Finnhub directly from browser
- Always route through backend
- Implement exponential backoff on failures
- Log all 429 errors

---

## 7. LONG TERM SCALING PLAN

If 200+ real-time stocks required:

Upgrade to:
Finnhub Starter Plan

OR

Switch to:
Polygon.io / paid BIST provider

---

## FINAL DECISION

Yahoo → NOT reliable
Finnhub Free → Partial real-time
Hybrid model → Required
Full production-grade real-time → Paid plan necessary