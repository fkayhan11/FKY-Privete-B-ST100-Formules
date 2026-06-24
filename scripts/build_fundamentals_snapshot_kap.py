#!/usr/bin/env python3
"""
Build fundamentals snapshot from KAP disclosures.

Flow:
1) Resolve mkkMemberOid via /api/member/filter/{ticker}
2) Fetch FR disclosures via /api/company-detail/sgbf-data/{oid}/FR/{range}
3) Parse latest financial disclosure table(s) from /tr/Bildirim/{disclosureIndex}
4) Compute core ratios and write data/fundamentals_snapshot.json
"""

from __future__ import annotations

import argparse
import json
import random
import re
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup


ROOT = Path(__file__).resolve().parents[1]
BIST_CONFIG = ROOT / "bist100-config.js"
OUT_PATH = ROOT / "data" / "fundamentals_snapshot.json"

BASE_URL = "https://www.kap.org.tr"
LANG = "tr"
HEADERS = {"Accept-Language": LANG, "Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}
MIN_HTTP_INTERVAL_S = 0.40
LAST_HTTP_TS = 0.0

PREFERRED_MEMBER_TITLE_SUBSTR = {
    "GARAN": ["BANKASI"],
}

BANK_SYMBOLS = ["AKBNK", "GARAN", "ISCTR", "YKBNK", "VAKBN", "HALKB", "TSKB", "SKBNK", "ALBRK"]


FIELDS = [
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
    # Raw KAP base fields for server-side derived metrics
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
]

TEXT_FIELDS = [
    "pegStatus",
]


def to_num(v) -> Optional[float]:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    return None


def unpack_list_payload(payload) -> List[dict]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for key in ("data", "items", "result", "results", "value"):
            val = payload.get(key)
            if isinstance(val, list):
                return [x for x in val if isinstance(x, dict)]
    return []


def iter_object_lists(node, depth: int = 0, max_depth: int = 6):
    if depth > max_depth:
        return
    if isinstance(node, list):
        if node and all(isinstance(x, dict) for x in node):
            yield node
        for x in node:
            yield from iter_object_lists(x, depth + 1, max_depth)
        return
    if isinstance(node, dict):
        for v in node.values():
            yield from iter_object_lists(v, depth + 1, max_depth)


def extract_member_rows(payload) -> List[dict]:
    rows = unpack_list_payload(payload)
    if rows and any(isinstance(r, dict) and r.get("mkkMemberOid") for r in rows):
        return rows
    for lst in iter_object_lists(payload):
        if any(isinstance(r, dict) and r.get("mkkMemberOid") for r in lst):
            return [x for x in lst if isinstance(x, dict)]
    return rows


def disclosure_row_score(row: dict) -> int:
    if not isinstance(row, dict):
        return 0
    basic = row.get("disclosureBasic") if isinstance(row.get("disclosureBasic"), dict) else {}
    score = 0
    if basic:
        score += 2
    for k in ("disclosureIndex", "notificationIndex", "title", "subject", "publishDate", "year"):
        if k in row or k in basic:
            score += 1
    return score


def extract_disclosure_rows(payload) -> List[dict]:
    candidates: List[List[dict]] = []
    direct = unpack_list_payload(payload)
    if direct:
        candidates.append(direct)
    for lst in iter_object_lists(payload):
        c = [x for x in lst if isinstance(x, dict)]
        if c:
            candidates.append(c)
    best: List[dict] = []
    best_score = -1
    for cand in candidates:
        score = sum(disclosure_row_score(r) for r in cand)
        if score > best_score:
            best_score = score
            best = cand
    if best_score <= 0:
        return []
    return best


def empty_row() -> Dict[str, object]:
    out = {k: None for k in FIELDS}
    for k in TEXT_FIELDS:
        out[k] = None
    return out


def sanitize_existing_row(input_row: dict) -> Dict[str, object]:
    out = empty_row()
    if not isinstance(input_row, dict):
        return out
    for k in FIELDS:
        out[k] = to_num(input_row.get(k))
    if input_row.get("pegStatus") in {"Valid", "Negative Trend"}:
        out["pegStatus"] = input_row.get("pegStatus")
    return out


def load_existing_snapshot_data() -> Dict[str, Dict[str, Optional[float]]]:
    if not OUT_PATH.exists():
        return {}
    try:
        parsed = json.loads(OUT_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    data = parsed.get("data") or {}
    if not isinstance(data, dict):
        return {}
    out: Dict[str, Dict[str, Optional[float]]] = {}
    for k, row in data.items():
        symbol = str(k or "").strip().upper()
        if not symbol:
            continue
        out[symbol] = sanitize_existing_row(row)
    return out


def parse_bist100_codes() -> List[str]:
    text = BIST_CONFIG.read_text(encoding="utf-8")
    m = re.search(r"window\.BIST100_CODES\s*=\s*\[(.*?)\];", text, re.S)
    if not m:
        raise RuntimeError("BIST100_CODES block not found")
    return re.findall(r'"([A-Z0-9]+)"', m.group(1))


def normalize_label(label: str) -> str:
    s = (label or "").upper()
    repl = {
        "İ": "I",
        "İ": "I",
        "Ş": "S",
        "Ğ": "G",
        "Ü": "U",
        "Ö": "O",
        "Ç": "C",
        "Â": "A",
        "Ê": "E",
        "Î": "I",
        "Û": "U",
    }
    for a, b in repl.items():
        s = s.replace(a, b)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def parse_tr_number(raw: str) -> Optional[float]:
    if raw is None:
        return None
    t = str(raw).strip()
    if not t or t in {"-", "--", "—", "N/A"}:
        return None
    neg = t.startswith("(") and t.endswith(")")
    t = t.strip("()")
    t = t.replace("\xa0", "").replace(" ", "")
    t = t.replace("%", "")
    t = re.sub(r"[^0-9,.\-]", "", t)
    if not t:
        return None

    if "," in t and "." in t:
        # Turkish style: 1.234.567,89
        t = t.replace(".", "").replace(",", ".")
    elif "," in t and "." not in t:
        t = t.replace(",", ".")
    elif t.count(".") > 1:
        # Thousand groups with dot: 1.234.567
        t = t.replace(".", "")
    elif "." in t:
        # Ambiguous dot usage; if decimal part is exactly 3 digits, treat as thousand separator.
        left, right = t.split(".", 1)
        if right.isdigit() and len(right) == 3 and left.replace("-", "").isdigit():
            t = left + right
    try:
        val = float(t)
        return -val if neg else val
    except ValueError:
        return None


def parse_kap_time_ms(raw) -> Optional[float]:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        x = float(raw)
        if x <= 0:
            return None
        # Heuristic: seconds vs milliseconds.
        return x * 1000.0 if x < 10_000_000_000 else x
    s = str(raw).strip()
    if not s:
        return None
    # KAP sometimes uses .NET date format: /Date(1735689600000)/
    m = re.search(r"(\d{10,13})", s)
    if m:
        val = float(m.group(1))
        return val * 1000.0 if val < 10_000_000_000 else val
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt.timestamp() * 1000.0
    except Exception:
        return None


def extract_disclosure_meta(disclosure: dict) -> Dict[str, Optional[float]]:
    basic = (disclosure or {}).get("disclosureBasic") or {}
    idx = to_num(basic.get("disclosureIndex"))
    year = to_num(basic.get("year"))
    date_ms = None
    for k in [
        "publishDate",
        "publishDateTime",
        "publishedDate",
        "publishedDateTime",
        "disclosureDate",
        "disclosureDateTime",
        "date",
        "createdDate",
        "createdDateTime",
    ]:
        date_ms = parse_kap_time_ms(basic.get(k))
        if date_ms is not None:
            break
    return {
        "kapDisclosureIndex": idx,
        "kapDisclosureYear": year,
        "kapDisclosureDateMs": date_ms,
    }


def fetch_json_with_retry(
    session: requests.Session,
    url: str,
    timeout: int = 30,
    attempts: int = 6,
    base_sleep_s: float = 2.0,
):
    global LAST_HTTP_TS
    last_exc: Optional[Exception] = None
    for attempt in range(1, attempts + 1):
        try:
            wait_http = MIN_HTTP_INTERVAL_S - (time.time() - LAST_HTTP_TS)
            if wait_http > 0:
                time.sleep(wait_http)
            r = session.get(url, headers=HEADERS, timeout=timeout)
            LAST_HTTP_TS = time.time()
            if r.status_code == 429:
                raise RuntimeError("http_429_rate_limited")
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            last_exc = exc
            msg = str(exc).lower()
            blocked = "excessive request" in msg
            transient = (
                blocked
                or "rate" in msg
                or "timed out" in msg
                or "badstatusline" in msg
                or "connection aborted" in msg
                or "http_429" in msg
            )
            if attempt >= attempts or not transient:
                break
            wait_s = max(90.0 * attempt, base_sleep_s * attempt) if blocked else (base_sleep_s * attempt)
            time.sleep(wait_s)
    if last_exc:
        raise last_exc
    raise RuntimeError("unknown_fetch_error")


def get_member_oid(session: requests.Session, ticker: str) -> Optional[str]:
    url = f"{BASE_URL}/{LANG}/api/member/filter/{ticker}"
    payload = fetch_json_with_retry(session, url, timeout=30, attempts=3, base_sleep_s=1.5)
    arr = extract_member_rows(payload)
    if not arr:
        return None
    if len(arr) == 1:
        return arr[0].get("mkkMemberOid")
    prefs = PREFERRED_MEMBER_TITLE_SUBSTR.get(ticker.upper()) or []
    if prefs:
        for item in arr:
            title = normalize_label(str(item.get("title") or ""))
            if all(p in title for p in prefs):
                return item.get("mkkMemberOid")
    return arr[0].get("mkkMemberOid")


def get_fr_disclosures(session: requests.Session, mkk_member_oid: str, date_range: int = 365) -> List[dict]:
    url = f"{BASE_URL}/{LANG}/api/company-detail/sgbf-data/{mkk_member_oid}/FR/{date_range}"
    payload = fetch_json_with_retry(session, url, timeout=30, attempts=3, base_sleep_s=1.5)
    return extract_disclosure_rows(payload)


def get_disclosures_by_type(
    session: requests.Session,
    mkk_member_oid: str,
    disclosure_type: str,
    date_range: int = 1095,
) -> List[dict]:
    url = f"{BASE_URL}/{LANG}/api/company-detail/sgbf-data/{mkk_member_oid}/{disclosure_type}/{date_range}"
    try:
        payload = fetch_json_with_retry(session, url, timeout=30, attempts=2, base_sleep_s=1.0)
    except Exception:
        return []
    return extract_disclosure_rows(payload)


def pick_financial_disclosure(disclosures: List[dict]) -> Optional[dict]:
    if not disclosures:
        return None
    preferred = []
    fallback = []
    for item in disclosures:
        basic = item.get("disclosureBasic") or {}
        title = normalize_label(str(basic.get("title") or ""))
        if "FINANSAL RAPOR" in title or "FINANCIAL REPORT" in title:
            preferred.append(item)
        else:
            fallback.append(item)
    pool = preferred or fallback
    # Keep most recent by disclosureIndex (monotonic on KAP).
    pool.sort(key=lambda x: (x.get("disclosureBasic") or {}).get("disclosureIndex") or 0, reverse=True)
    return pool[0] if pool else None


def list_financial_candidates(disclosures: List[dict], max_items: int = 12) -> List[dict]:
    preferred = []
    fallback = []
    for item in disclosures:
        basic = item.get("disclosureBasic") or {}
        title = normalize_label(str(basic.get("title") or ""))
        if "FINANSAL RAPOR" in title or "FINANCIAL REPORT" in title:
            preferred.append(item)
        else:
            fallback.append(item)
    preferred.sort(key=lambda x: (x.get("disclosureBasic") or {}).get("disclosureIndex") or 0, reverse=True)
    fallback.sort(key=lambda x: (x.get("disclosureBasic") or {}).get("disclosureIndex") or 0, reverse=True)
    return (preferred + fallback)[:max_items]


def score_policy_from_disclosures(disclosures: List[dict]) -> Dict[str, Optional[float]]:
    items = []
    for it in disclosures:
        b = it.get("disclosureBasic") if isinstance(it.get("disclosureBasic"), dict) else {}
        idx = b.get("disclosureIndex") or it.get("disclosureIndex") or it.get("id")
        title_raw = (
            b.get("title")
            or b.get("subject")
            or it.get("title")
            or it.get("subject")
            or it.get("notificationSubject")
            or ""
        )
        title = normalize_label(str(title_raw))
        year = b.get("year") or it.get("year")
        items.append({"idx": idx, "title": title, "year": year})

    def clamp(v: float, lo: float = 0.10, hi: float = 0.90) -> float:
        return max(lo, min(hi, v))

    # Recency weighting (official KAP flow): recent years impact score more.
    years = sorted({y for y in (it.get("year") for it in items) if isinstance(y, int)}, reverse=True)
    year_weights: Dict[int, float] = {}
    for i, y in enumerate(years[:5]):
        year_weights[y] = [1.00, 0.75, 0.55, 0.40, 0.30][i]

    def weighted_count(words: List[str]) -> Tuple[float, Dict[int, int]]:
        total = 0.0
        by_year: Dict[int, int] = {}
        for it in items:
            t = it["title"]
            if not any(w in t for w in words):
                continue
            y = it.get("year")
            w = year_weights.get(y, 0.35)
            total += w
            if isinstance(y, int):
                by_year[y] = by_year.get(y, 0) + 1
        return total, by_year

    div_w, div_year = weighted_count(["KAR PAYI", "TEMETTU", "DIVIDEND"])
    buy_w, _ = weighted_count(["GERI ALIM", "PAY GERI ALIM", "BUYBACK"])
    dilution_w, _ = weighted_count(["SERMAYE ARTIRIM", "BEDELLI", "RUCHAN", "RIGHTS ISSUE"])
    governance_w, _ = weighted_count(["KURUMSAL YONETIM", "CORPORATE GOVERNANCE", "BAGIMSIZ YONETIM"])
    sanction_w, _ = weighted_count(["CEZA", "IDARI PARA", "UYARI", "IHLAL", "SUC DUYURUSU"])
    alloc_w, _ = weighted_count(["YATIRIM", "SATIN ALMA", "BIRLESME", "DEVRALMA", "TESIS", "KAPASITE"])

    # Year trend signal: dividend continuity improves quality.
    div_years_with_events = sum(1 for c in div_year.values() if c > 0)
    div_trend_bonus = 0.08 if div_years_with_events >= 3 else (0.04 if div_years_with_events >= 2 else 0.0)

    # Scores normalized to 0..1.
    div_score = clamp(0.35 + (min(4.5, div_w) / 4.5) * 0.45 + div_trend_bonus)
    buy_score = clamp(0.45 + (min(3.0, buy_w) / 3.0) * 0.30)
    dil_score = clamp(0.80 - (min(4.0, dilution_w) / 4.0) * 0.55)
    alloc_score = clamp(0.40 + (min(4.0, alloc_w) / 4.0) * 0.30)
    gov_score = clamp(0.40 + (min(3.0, governance_w) / 3.0) * 0.35 - (min(2.5, sanction_w) / 2.5) * 0.30)

    return {
        "divPolicyScore": div_score,
        "buybackPolicyScore": buy_score,
        "dilutionPolicyScore": dil_score,
        "capitalAllocationScore": alloc_score,
        "governanceScore": gov_score,
    }


def get_attachment_detail(session: requests.Session, disclosure_index: int) -> dict:
    url = f"{BASE_URL}/{LANG}/api/notification/attachment-detail/{disclosure_index}"
    payload = fetch_json_with_retry(session, url, timeout=45, attempts=3, base_sleep_s=2.0)
    if isinstance(payload, list):
        return payload[0] if payload else {}
    return payload if isinstance(payload, dict) else {}


def _pick_period_values(values: List[Optional[float]]) -> Tuple[Optional[float], Optional[float]]:
    vals = [v for v in values if v is not None]
    if not vals:
        return None, None
    if len(vals) == 1:
        return vals[0], None
    if len(vals) == 2:
        return vals[0], vals[1]
    half = len(vals) // 2
    cur = vals[:half] if half > 0 else vals[:1]
    prev = vals[half:] if half > 0 else vals[1:]
    cur_v = cur[-1] if cur else vals[0]
    prev_v = prev[-1] if prev else None
    return cur_v, prev_v


def _first_available(tag_values: Dict[str, Tuple[Optional[float], Optional[float]]], tags: List[str]) -> Tuple[Optional[float], Optional[float]]:
    for t in tags:
        if t in tag_values:
            return tag_values[t]
    return None, None


def _first_contains(tag_values: Dict[str, Tuple[Optional[float], Optional[float]]], needles: List[str]) -> Tuple[Optional[float], Optional[float]]:
    for k, v in tag_values.items():
        lk = k.lower()
        if all(n.lower() in lk for n in needles):
            return v
    return None, None


def three_year_cagr(values: List[Optional[float]]) -> Optional[float]:
    vals = [to_num(v) for v in values]
    vals = [v for v in vals if v is not None]
    # Expected order: [current, one_year_ago, two_years_ago, three_years_ago].
    if len(vals) < 4:
        return None
    current = vals[0]
    base = vals[3]
    if current is None or base is None or current <= 0 or base <= 0:
        return None
    try:
        return (current / base) ** (1.0 / 3.0) - 1.0
    except Exception:
        return None


def calculate_peg_with_status(
    trailing_pe: Optional[float],
    earnings_growth_1y: Optional[float],
    eps_history: Optional[List[Optional[float]]] = None,
    earnings_history: Optional[List[Optional[float]]] = None,
) -> Dict[str, object]:
    pe = to_num(trailing_pe)
    growth = to_num(earnings_growth_1y)

    if growth is None or growth <= 0:
        growth = three_year_cagr(eps_history or [])
        if growth is None or growth <= 0:
            growth = three_year_cagr(earnings_history or [])

    if growth is None or growth <= 0:
        return {"pegRatio": None, "pegStatus": "Negative Trend"}
    if pe is None or pe <= 0:
        return {"pegRatio": None, "pegStatus": None}

    growth_pct = growth * 100.0
    if growth_pct <= 0:
        return {"pegRatio": None, "pegStatus": "Negative Trend"}
    return {"pegRatio": pe / growth_pct, "pegStatus": "Valid"}


def parse_financial_values_from_disclosure(
    session: requests.Session,
    disclosure_index: int,
    disclosure_meta: Optional[dict] = None,
    symbol: Optional[str] = None,
) -> Dict[str, Optional[float]]:
    detail = get_attachment_detail(session, disclosure_index)
    body_sections = detail.get("disclosureBody") or []
    meta = extract_disclosure_meta(disclosure_meta or {"disclosureBasic": {"disclosureIndex": disclosure_index}})
    is_bank = (symbol or "").strip().upper() in BANK_SYMBOLS

    # key: taxonomy row id (ifrs-full_... / kap-fr_...), value: (current_period, previous_period)
    tag_values: Dict[str, Tuple[Optional[float], Optional[float]]] = {}

    for section_html in body_sections:
        if not section_html or "financial-table" not in section_html:
            continue
        soup = BeautifulSoup(section_html, "html.parser")
        for table in soup.select("table.financial-table"):
            tbody = table.find("tbody")
            if not tbody:
                continue
            for tr in tbody.find_all("tr", recursive=False):
                name_node = tr.select_one(".taxonomy-field-name")
                if not name_node:
                    continue
                raw_key = name_node.get_text(" ", strip=True)
                key = raw_key.split("|", 1)[0].strip()
                if not key:
                    continue

                ctx_cells = tr.select("td.taxonomy-context-value")
                vals = [parse_tr_number(td.get_text(" ", strip=True)) for td in ctx_cells]
                cur, prev = _pick_period_values(vals)
                if cur is None and prev is None:
                    continue
                if key in tag_values:
                    ex_cur, ex_prev = tag_values[key]
                    ex_score = abs(ex_cur) if ex_cur is not None else -1.0
                    nw_score = abs(cur) if cur is not None else -1.0
                    if nw_score > ex_score:
                        tag_values[key] = (cur, prev)
                else:
                    tag_values[key] = (cur, prev)

    if is_bank:
        revenue, revenue_prev = _first_available(
            tag_values,
            [
                "kap-fr_NetInterestIncome",
                "ifrs-full_NetInterestIncome",
                "kap-fr_NetInterestIncomeExpense",
                "ifrs-full_InterestRevenueExpense",
                "ifrs-full_InterestRevenueCalculatedUsingEffectiveInterestMethod",
                "ifrs-full_InterestIncome",
                "kap-fr_TotalOperatingIncome",
                "kap-fr_TotalIncomeFromOperatingActivities",
                "kap-fr_OperatingIncome",
            ],
        )
        if revenue is None:
            revenue, revenue_prev = _first_contains(tag_values, ["net", "interest", "income"])
        if revenue is None:
            revenue, revenue_prev = _first_contains(tag_values, ["total", "operating", "income"])
        if revenue is None:
            revenue, revenue_prev = _first_contains(tag_values, ["operating", "income"])
    else:
        revenue, revenue_prev = _first_available(
            tag_values,
            [
                "ifrs-full_Revenue",
                "ifrs-full_SalesRevenueNet",
                "ifrs-full_RevenueFromContractsWithCustomers",
                "ifrs-full_InterestRevenueExpense",
                "ifrs-full_InterestRevenueCalculatedUsingEffectiveInterestMethod",
                "ifrs-full_InterestRevenue",
                "ifrs-full_InterestIncome",
                "ifrs-full_NetInterestIncome",
            ],
        )
        if revenue is None:
            revenue, revenue_prev = _first_contains(tag_values, ["revenue"])
        if revenue is None:
            revenue, revenue_prev = _first_contains(tag_values, ["sales"])
        if revenue is None:
            revenue, revenue_prev = _first_contains(tag_values, ["interest", "income"])
    net_income, net_income_prev = _first_available(
        tag_values,
        [
            "ifrs-full_ProfitLoss",
            "ifrs-full_ProfitLossAttributableToOwnersOfParent",
            "ifrs-full_ProfitLossAttributableToOrdinaryEquityHoldersOfParentEntity",
            "kap-fr_NetPeriodProfitLoss",
        ],
    )
    if net_income is None:
        net_income, net_income_prev = _first_contains(tag_values, ["profitloss"])
    if net_income is None:
        net_income, net_income_prev = _first_contains(tag_values, ["net", "profit"])
    equity, _ = _first_available(
        tag_values,
        [
            "ifrs-full_Equity",
            "ifrs-full_EquityAttributableToOwnersOfParent",
        ],
    )
    assets, _ = _first_available(tag_values, ["ifrs-full_Assets"])
    current_assets, _ = _first_available(tag_values, ["ifrs-full_CurrentAssets"])
    short_liabilities, _ = _first_available(tag_values, ["ifrs-full_CurrentLiabilities"])
    total_liabilities, _ = _first_available(
        tag_values,
        [
            "ifrs-full_Liabilities",
            "ifrs-full_TotalLiabilities",
            "kap-fr_TotalLiabilities",
        ],
    )
    if total_liabilities is None:
        eql, _ = _first_available(tag_values, ["ifrs-full_EquityAndLiabilities"])
        if eql is not None and equity is not None:
            total_liabilities = eql - equity
    if is_bank:
        gross_profit = None
    else:
        gross_profit, _ = _first_available(
            tag_values,
            [
                "ifrs-full_GrossProfit",
                "ifrs-full_GrossProfitLoss",
                "kap-fr_GrossProfitLossFromOperatingActivitiesForBankingSector",
            ],
        )
    operating_profit, _ = _first_available(
        tag_values,
        [
            "ifrs-full_OperatingProfitLoss",
            "ifrs-full_ProfitLossFromOperatingActivities",
        ],
    )
    total_cash, _ = _first_available(
        tag_values,
        [
            "ifrs-full_CashAndCashEquivalents",
            "kap-fr_CashAndCashBalancesAtCentralBanks",
            "ifrs-full_CashAndCashEquivalentsForCashFlowStatement",
        ],
    )
    if is_bank:
        ebitda = None
    else:
        ebitda, _ = _first_available(
            tag_values,
            [
                "ifrs-full_EarningsBeforeInterestTaxesDepreciationAndAmortisation",
                "ifrs-full_EarningsBeforeInterestTaxDepreciationAmortization",
                "kap-fr_Ebitda",
            ],
        )
        if ebitda is None:
            ebitda, _ = _first_contains(tag_values, ["ebitda"])

    depreciation_amortization, _ = _first_available(
        tag_values,
        [
            "ifrs-full_DepreciationAmortisationAndImpairment",
            "ifrs-full_DepreciationAndAmortisationExpense",
            "ifrs-full_DepreciationAndAmortisationExpenseRecognisedInProfitOrLoss",
            "ifrs-full_AmortisationExpense",
        ],
    )
    if depreciation_amortization is None:
        depreciation_amortization, _ = _first_contains(tag_values, ["depreciation"])
    if depreciation_amortization is None:
        depreciation_amortization, _ = _first_contains(tag_values, ["amortisation"])
    if depreciation_amortization is None:
        depreciation_amortization, _ = _first_contains(tag_values, ["amortization"])
    if not is_bank and ebitda is None and operating_profit is not None and depreciation_amortization is not None:
        ebitda = operating_profit + abs(depreciation_amortization)

    current_borrowings, _ = _first_available(
        tag_values,
        [
            "ifrs-full_CurrentBorrowings",
            "ifrs-full_CurrentFinancialLiabilities",
            "ifrs-full_CurrentPortionOfNoncurrentBorrowings",
            "ifrs-full_CurrentPortionOfNonCurrentBorrowings",
            "kap-fr_CurrentBorrowings",
        ],
    )
    noncurrent_borrowings, _ = _first_available(
        tag_values,
        [
            "ifrs-full_NoncurrentBorrowings",
            "ifrs-full_NonCurrentBorrowings",
            "ifrs-full_NoncurrentFinancialLiabilities",
            "ifrs-full_LongtermBorrowings",
            "ifrs-full_LongTermBorrowings",
            "kap-fr_NoncurrentBorrowings",
        ],
    )
    if current_borrowings is None:
        current_borrowings, _ = _first_contains(tag_values, ["current", "borrow"])
    if current_borrowings is None:
        current_borrowings, _ = _first_contains(tag_values, ["current", "financial", "liabilit"])
    if noncurrent_borrowings is None:
        noncurrent_borrowings, _ = _first_contains(tag_values, ["noncurrent", "borrow"])
    if noncurrent_borrowings is None:
        noncurrent_borrowings, _ = _first_contains(tag_values, ["non", "current", "borrow"])
    if noncurrent_borrowings is None:
        noncurrent_borrowings, _ = _first_contains(tag_values, ["longterm", "borrow"])
    if noncurrent_borrowings is None:
        noncurrent_borrowings, _ = _first_contains(tag_values, ["noncurrent", "financial", "liabilit"])
    borrowings_total, _ = _first_available(tag_values, ["ifrs-full_Borrowings"])
    if borrowings_total is None and (current_borrowings is not None or noncurrent_borrowings is not None):
        borrowings_total = (current_borrowings or 0.0) + (noncurrent_borrowings or 0.0)

    inventories, _ = _first_available(tag_values, ["ifrs-full_Inventories", "kap-fr_Inventories"])
    if inventories is None:
        inventories, _ = _first_contains(tag_values, ["inventor"])
    cfo, _ = _first_available(
        tag_values,
        [
            "ifrs-full_CashFlowsFromUsedInOperatingActivities",
            "ifrs-full_NetCashFlowsFromUsedInOperatingActivities",
        ],
    )
    if cfo is None:
        cfo, _ = _first_contains(tag_values, ["cash", "operating", "activities"])

    capex_ppe, _ = _first_available(
        tag_values,
        [
            "ifrs-full_PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities",
            "ifrs-full_PurchaseOfPropertyPlantAndEquipment",
        ],
    )
    capex_int, _ = _first_available(
        tag_values,
        [
            "ifrs-full_PurchaseOfIntangibleAssetsClassifiedAsInvestingActivities",
            "ifrs-full_PurchaseOfIntangibleAssets",
        ],
    )
    capex_invprop, _ = _first_available(tag_values, ["ifrs-full_PurchaseOfInvestmentProperty"])
    capex_vals = [v for v in [capex_ppe, capex_int, capex_invprop] if v is not None]
    capex = sum(capex_vals) if capex_vals else None
    if capex is None:
        capex, _ = _first_contains(tag_values, ["purchase", "property", "plant", "equipment"])
    if capex is None:
        capex, _ = _first_contains(tag_values, ["purchase", "intangible"])

    interest_expense, _ = _first_available(
        tag_values,
        [
            "ifrs-full_FinanceCosts",
            "ifrs-full_FinanceCostsRecognisedInProfitOrLoss",
            "ifrs-full_FinanceExpense",
            "ifrs-full_InterestExpense",
            "ifrs-full_InterestExpenseCalculatedUsingEffectiveInterestMethod",
        ],
    )
    if interest_expense is None:
        interest_expense, _ = _first_contains(tag_values, ["finance", "cost"])

    fx_net_pos, _ = _first_contains(tag_values, ["foreign", "currency", "position"])

    eps_basic, eps_basic_prev = _first_available(
        tag_values,
        [
            "ifrs-full_BasicEarningsLossPerShare",
            "ifrs-full_BasicEarningsPerShare",
            "ifrs-full_BasicEarningsLossPerShareFromContinuingOperations",
            "ifrs-full_DilutedEarningsLossPerShareFromContinuingOperations",
            "ifrs-full_DilutedEarningsLossPerShare",
            "kap-fr_EarningsPerShare",
        ],
    )
    if eps_basic is None:
        eps_basic, eps_basic_prev = _first_contains(tag_values, ["earnings", "per", "share"])
    issued_capital, issued_capital_prev = _first_available(
        tag_values,
        [
            "ifrs-full_IssuedCapital",
            "kap-fr_PaidInCapital",
            "kap-fr_IssuedCapital",
        ],
    )
    shares_outstanding, shares_outstanding_prev = _first_available(
        tag_values,
        [
            "ifrs-full_NumberOfSharesOutstanding",
            "ifrs-full_NumberOfSharesIssued",
            "ifrs-full_WeightedAverageNumberOfOrdinarySharesOutstandingBasic",
            "ifrs-full_WeightedAverageNumberOfSharesOutstandingBasicAndDiluted",
            "kap-fr_NumberOfSharesIssued",
        ],
    )
    if shares_outstanding is None:
        shares_outstanding, shares_outstanding_prev = _first_contains(tag_values, ["number", "shares", "issued"])
    if shares_outstanding is None:
        shares_outstanding, shares_outstanding_prev = _first_contains(tag_values, ["weighted", "average", "shares"])
    # Proxy: in many BIST reports issued capital approximates share count at nominal 1 TL.
    if shares_outstanding is None and issued_capital not in (None, 0):
        shares_outstanding = issued_capital
    if shares_outstanding_prev is None and issued_capital_prev not in (None, 0):
        shares_outstanding_prev = issued_capital_prev
    if shares_outstanding_prev is None and shares_outstanding not in (None, 0):
        shares_outstanding_prev = shares_outstanding
    # If per-share line is blank, derive EPS from net income and share count proxy.
    if eps_basic in (None, 0) and net_income not in (None, 0) and shares_outstanding not in (None, 0):
        eps_basic = net_income / shares_outstanding
    # Previous-period EPS is often not explicitly reported in some KAP contexts.
    # Derive from previous net income and previous/current share base when possible.
    if eps_basic_prev in (None, 0) and net_income_prev not in (None, 0) and shares_outstanding_prev not in (None, 0):
        eps_basic_prev = net_income_prev / shares_outstanding_prev
    if eps_basic_prev in (None, 0) and eps_basic not in (None, 0) and net_income not in (None, 0) and net_income_prev not in (None, 0):
        # Fallback with net-income growth when share base is unchanged or unavailable.
        eps_basic_prev = eps_basic * (net_income_prev / net_income)

    out = empty_row()
    out["revenue"] = to_num(revenue)
    out["netIncome"] = to_num(net_income)
    out["equity"] = to_num(equity)
    out["assets"] = to_num(assets)
    out["cfo"] = to_num(cfo)
    out["capex"] = to_num(capex)
    out["sharesOutstanding"] = to_num(shares_outstanding)
    out["interestExpense"] = to_num(interest_expense)
    out["currentBorrowings"] = to_num(current_borrowings)
    out["nonCurrentBorrowings"] = to_num(noncurrent_borrowings)
    out["inventories"] = to_num(inventories)
    out["currentAssets"] = to_num(current_assets)
    out["currentLiabilities"] = to_num(short_liabilities)
    out["totalLiabilities"] = to_num(total_liabilities)
    out["grossProfit"] = to_num(gross_profit)
    out["operatingProfit"] = to_num(operating_profit)
    out["depreciationAmortization"] = to_num(depreciation_amortization)
    out["kapDisclosureIndex"] = to_num(meta.get("kapDisclosureIndex"))
    out["kapDisclosureYear"] = to_num(meta.get("kapDisclosureYear"))
    out["kapDisclosureDateMs"] = to_num(meta.get("kapDisclosureDateMs"))
    out["trailingEps"] = to_num(eps_basic)
    out["totalCash"] = to_num(total_cash)
    out["totalDebt"] = to_num(borrowings_total if borrowings_total is not None else total_liabilities)
    out["ebitda"] = to_num(ebitda)

    if net_income is not None and equity not in (None, 0):
        out["returnOnEquity"] = net_income / equity
    if net_income is not None and assets not in (None, 0):
        out["returnOnAssets"] = net_income / assets
    if net_income is not None and revenue not in (None, 0):
        out["profitMargins"] = net_income / revenue
    if not is_bank and gross_profit is not None and revenue not in (None, 0):
        out["grossMargins"] = gross_profit / revenue
    if operating_profit is not None and revenue not in (None, 0):
        out["operatingMargins"] = operating_profit / revenue
    if current_assets is not None and short_liabilities not in (None, 0):
        out["currentRatio"] = current_assets / short_liabilities
    if total_liabilities is not None and equity not in (None, 0):
        out["debtToEquity"] = (total_liabilities / equity) * 100.0

    if revenue is not None and revenue_prev not in (None, 0):
        out["revenueGrowth"] = (revenue - revenue_prev) / abs(revenue_prev)
    if net_income is not None and net_income_prev not in (None, 0):
        out["earningsGrowth"] = (net_income - net_income_prev) / abs(net_income_prev)
    if eps_basic is not None and eps_basic_prev not in (None, 0):
        out["epsGrowth"] = (eps_basic - eps_basic_prev) / abs(eps_basic_prev)
    peg_info = calculate_peg_with_status(
        out.get("trailingPE"),
        out.get("earningsGrowth"),
        eps_history=[eps_basic, eps_basic_prev],
        earnings_history=[net_income, net_income_prev],
    )
    out["pegRatio"] = peg_info["pegRatio"]
    out["pegStatus"] = peg_info["pegStatus"]
    if revenue not in (None, 0) and assets not in (None, 0):
        out["assetTurnover"] = revenue / assets
    if current_assets is not None and short_liabilities not in (None, 0):
        inv = inventories or 0.0
        out["quickRatio"] = (current_assets - inv) / short_liabilities
    debt_for_nd = borrowings_total if borrowings_total is not None else total_liabilities
    if not is_bank and debt_for_nd is not None and ebitda not in (None, 0):
        out["netDebtToEbitda"] = (debt_for_nd - (total_cash or 0.0)) / ebitda
    if operating_profit is not None and interest_expense not in (None, 0):
        out["interestCoverage"] = operating_profit / abs(interest_expense)
    if cfo is not None and net_income not in (None, 0):
        out["cfoToNetIncome"] = cfo / net_income
    if cfo is not None and capex is not None:
        out["freeCashflow"] = cfo - abs(capex)
    if current_borrowings is not None and noncurrent_borrowings not in (None, 0):
        out["debtMaturityRatio"] = current_borrowings / noncurrent_borrowings
    if fx_net_pos is not None and equity not in (None, 0):
        out["fxNetPositionRatio"] = fx_net_pos / equity

    # Auto proxies for fully automated scoring in UI.
    if out["returnOnEquity"] is not None and out["profitMargins"] is not None:
        score = 0.0
        if out["returnOnEquity"] > 0:
            score += 0.4
        if out["profitMargins"] > 0:
            score += 0.3
        if out["earningsGrowth"] is not None and out["earningsGrowth"] > 0:
            score += 0.3
        out["profitabilityStability"] = score
    if out["revenueGrowth"] is not None and out["earningsGrowth"] is not None:
        rg = out["revenueGrowth"]
        eg = out["earningsGrowth"]
        out["growthStability"] = 1.0 - min(1.0, abs((rg or 0.0) - (eg or 0.0)))

    # 2.6 scores are attached in build_snapshot via disclosure-history scoring.
    out["divPolicyScore"] = None
    out["buybackPolicyScore"] = None
    out["dilutionPolicyScore"] = None
    out["capitalAllocationScore"] = None
    out["governanceScore"] = None

    return out


def build_snapshot(
    limit: Optional[int] = None,
    sleep_s: float = 1.0,
    start: int = 0,
    count: Optional[int] = None,
    max_candidates: int = 3,
    include_policy_history: bool = False,
    merge_existing: bool = True,
) -> dict:
    all_codes = parse_bist100_codes()
    if limit is not None:
        all_codes = all_codes[:limit]

    start = max(0, int(start or 0))
    if count is None:
        end = len(all_codes)
    else:
        end = min(len(all_codes), start + max(0, int(count)))
    work_codes = all_codes[start:end]

    sess = requests.Session()
    existing = load_existing_snapshot_data() if merge_existing else {}
    data: Dict[str, Dict[str, Optional[float]]] = {
        c.upper(): sanitize_existing_row(existing.get(c.upper()) or {}) for c in all_codes
    }
    failures = []
    processed = 0
    updated = 0
    kept_existing_on_fail = 0

    print(
        f"[INFO] KAP snapshot start total={len(all_codes)} work={len(work_codes)} "
        f"start={start} end={end} sleep={sleep_s}s candidates={max_candidates} "
        f"policy={include_policy_history} merge_existing={merge_existing}",
        flush=True,
    )
    for i, code in enumerate(work_codes, start=1):
        symbol = code.upper()
        row = data.get(symbol) or empty_row()
        try:
            oid = get_member_oid(sess, symbol)
            if not oid:
                failures.append({"symbol": symbol, "reason": "member_oid_not_found"})
                data[symbol] = row
                print(f"[{i}/{len(work_codes)}] {symbol} -> member oid not found", flush=True)
                continue

            disclosures = get_fr_disclosures(sess, oid, 365)
            current = pick_financial_disclosure(disclosures)
            if not current:
                failures.append({"symbol": symbol, "reason": "fr_disclosure_not_found"})
                data[symbol] = row
                print(f"[{i}/{len(work_codes)}] {symbol} -> FR disclosure not found", flush=True)
                continue

            best_vals = row
            best_idx = None
            best_filled = -1
            candidates = list_financial_candidates(disclosures, max_items=max_candidates)
            for cand in candidates:
                c_idx = (cand.get("disclosureBasic") or {}).get("disclosureIndex")
                if not c_idx:
                    continue
                vals = parse_financial_values_from_disclosure(sess, int(c_idx), cand, symbol=symbol)
                filled = sum(1 for k in FIELDS if vals.get(k) is not None)
                if filled > best_filled:
                    best_vals = vals
                    best_idx = c_idx
                    best_filled = filled
                if filled >= 8:
                    break

            if best_idx is None:
                failures.append({"symbol": symbol, "reason": "disclosure_index_missing"})
                data[symbol] = row
                print(f"[{i}/{len(work_codes)}] {symbol} -> disclosure index missing", flush=True)
                continue

            if include_policy_history:
                # Optional heavy step: disclosure-history scoring.
                all_items = []
                seen_idx = set()
                for t in ["ALL", "FR"]:
                    for it in get_disclosures_by_type(sess, oid, t, 1095):
                        idx = ((it.get("disclosureBasic") or {}).get("disclosureIndex"))
                        if idx in seen_idx:
                            continue
                        seen_idx.add(idx)
                        all_items.append(it)
                policy_scores = score_policy_from_disclosures(all_items)
                for k, v in policy_scores.items():
                    best_vals[k] = to_num(v)

            data[symbol] = best_vals
            updated += 1
            print(f"[{i}/{len(work_codes)}] {symbol} -> ok ({best_filled} fields) disclosure={best_idx}", flush=True)
        except Exception as exc:
            failures.append({"symbol": symbol, "reason": str(exc)})
            if any(v is not None for v in (row or {}).values()):
                kept_existing_on_fail += 1
            data[symbol] = row
            print(f"[{i}/{len(work_codes)}] {symbol} -> ERROR: {exc}", flush=True)
            msg = str(exc).lower()
            if "excessive request" in msg or "http_429" in msg:
                print("[WARN] KAP rate limit detected, stopping early to avoid temporary block escalation.", flush=True)
                break
        finally:
            processed += 1
            wait_s = max(0.0, sleep_s) + random.uniform(0.0, 0.25)
            time.sleep(wait_s)

    now = datetime.now(timezone.utc)
    field_non_null = {k: 0 for k in FIELDS}
    for row in data.values():
        for k in FIELDS:
            if row.get(k) is not None:
                field_non_null[k] += 1
    symbol_count = len(all_codes)
    field_coverage = {
        k: {
            "nonNull": field_non_null[k],
            "coveragePct": round((field_non_null[k] / symbol_count) * 100, 1) if symbol_count else 0.0,
        }
        for k in FIELDS
    }
    governance_auto = sum(
        1
        for row in data.values()
        if any(
            row.get(k) is not None
            for k in ["divPolicyScore", "buybackPolicyScore", "dilutionPolicyScore", "capitalAllocationScore", "governanceScore"]
        )
    )
    payload = {
        "generatedAt": now.isoformat(),
        "generatedAtMs": int(now.timestamp() * 1000),
        "source": "kap",
        "symbolCount": symbol_count,
        "failureCount": len(failures),
        "failures": failures,
        "audit": {
            "fieldCoverage": field_coverage,
            "governanceAutoSymbols": governance_auto,
            "processedSymbols": processed,
            "updatedSymbols": updated,
            "workRange": {"start": start, "end": end},
            "keptExistingOnFail": kept_existing_on_fail,
            "mergeExisting": merge_existing,
            "policyHistoryEnabled": include_policy_history,
            "maxCandidates": max_candidates,
            "sleepSeconds": sleep_s,
        },
        "data": data,
    }
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="Process first N symbols for quick test")
    parser.add_argument("--start", type=int, default=0, help="Start index in BIST list (0-based)")
    parser.add_argument("--count", type=int, default=None, help="How many symbols to process from --start")
    parser.add_argument("--sleep", type=float, default=1.0, help="Sleep seconds between symbols")
    parser.add_argument("--max-candidates", type=int, default=3, help="Max FR disclosure candidates per symbol")
    parser.add_argument(
        "--policy-history",
        action="store_true",
        help="Enable heavier disclosure-history scans for 2.6 policy scores",
    )
    parser.add_argument(
        "--no-merge-existing",
        action="store_true",
        help="Do not merge with existing snapshot rows",
    )
    args = parser.parse_args()

    payload = build_snapshot(
        limit=args.limit,
        sleep_s=args.sleep,
        start=args.start,
        count=args.count,
        max_candidates=max(1, int(args.max_candidates)),
        include_policy_history=bool(args.policy_history),
        merge_existing=not bool(args.no_merge_existing),
    )
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    non_null_symbols = sum(1 for d in (payload.get("data") or {}).values() if any(v is not None for v in d.values()))
    symbol_count = int(payload.get("symbolCount") or 0)
    failure_count = int(payload.get("failureCount") or 0)
    processed_count = int(((payload.get("audit") or {}).get("processedSymbols")) or symbol_count)
    updated_count = int(((payload.get("audit") or {}).get("updatedSymbols")) or 0)
    work_range = ((payload.get("audit") or {}).get("workRange")) or {}
    work_start = int(work_range.get("start") or 0)
    work_end = int(work_range.get("end") or symbol_count)
    work_size = max(0, work_end - work_start)
    is_partial_run = symbol_count > 0 and work_size < symbol_count
    coverage = (non_null_symbols / symbol_count) if symbol_count else 0.0
    payload.setdefault("audit", {})
    payload["audit"]["symbolCoveragePct"] = round(coverage * 100, 1) if symbol_count else 0.0

    # Full run: keep strict guard.
    # Partial run: allow incremental promotion unless it's a near-total failure with zero updates.
    full_run_bad = (processed_count and failure_count > processed_count * 0.7) or (symbol_count and coverage < 0.5)
    partial_run_bad = (processed_count and failure_count > processed_count * 0.9 and updated_count == 0)
    if (not is_partial_run and full_run_bad) or (is_partial_run and partial_run_bad):
        payload["audit"]["lastRunStatus"] = "failed_guard"
        fail_path = OUT_PATH.parent / "fundamentals_snapshot.last_failed.json"
        fail_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(
            f"[WARN] snapshot not promoted due to low coverage ({non_null_symbols}/{symbol_count}) "
            f"or high failures ({failure_count}/{processed_count}) partial={is_partial_run} updated={updated_count}",
            flush=True,
        )
        print(f"[WARN] failed run saved: {fail_path}", flush=True)
        return 2

    payload["audit"]["lastRunStatus"] = (
        "ok" if (not is_partial_run and coverage >= 0.85 and failure_count == 0) else "partial"
    )

    if OUT_PATH.exists():
        backup_path = OUT_PATH.parent / "fundamentals_snapshot.backup.json"
        shutil.copyfile(OUT_PATH, backup_path)
    OUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[OK] snapshot written: {OUT_PATH}", flush=True)
    print(f"[OK] source={payload['source']} symbols={payload['symbolCount']} failures={payload['failureCount']} coverage={non_null_symbols}/{symbol_count}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
