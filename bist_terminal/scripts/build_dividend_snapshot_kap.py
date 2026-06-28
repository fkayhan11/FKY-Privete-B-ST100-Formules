#!/usr/bin/env python3
"""
Build dividend snapshot for BIST100 symbols from KAP disclosures.

Output:
  data/dividend_snapshot.json
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import requests
from bs4 import BeautifulSoup

try:
    from openai import OpenAI
except Exception:
    OpenAI = None


ROOT = Path(__file__).resolve().parents[1]
BIST_CONFIG = ROOT / "bist100-config.js"
OUT_PATH = ROOT / "data" / "dividend_snapshot.json"
BASE_URL = "https://www.kap.org.tr"
LANG = "tr"
HEADERS = {"Accept-Language": LANG, "Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}
MIN_HTTP_INTERVAL_S = 0.35
LAST_HTTP_TS = 0.0
PAGE_TEXT_CACHE: Dict[int, str] = {}
OPENAI_DIVIDEND_MODEL = os.getenv("OPENAI_DIVIDEND_MODEL", "gpt-4o-mini")

DIVIDEND_LLM_SYSTEM_PROMPT = """
You are a strict financial data extraction engine for Turkish KAP dividend disclosures.
Extract only cash dividend information from the supplied normalized Turkish text.

Return only one valid JSON object with exactly these keys:
{"gross": number|null, "net": number|null, "paymentDate": string|null}

Rules:
- gross and net are dividend amounts per share in TRY/TL, not total dividend amounts, not percentages, not capital ratios.
- Use decimal numbers with a dot as the decimal separator.
- paymentDate must be the cash dividend payment date as DD.MM.YYYY when available; otherwise null.
- Prefer explicit "brut/brüt pay başına", "net pay başına", "nakit kar payı", "hak kullanım tarihi", and "ödeme tarihi" fields.
- Ignore bonus share/bedelsiz amounts, withholding rates, payout ratios, nominal value, total distributable profit, and total cash dividend amounts.
- If a value is ambiguous or absent, use null. Do not guess.
- Do not include explanations, markdown, or any keys other than gross, net, and paymentDate.
""".strip()

DIVIDEND_FIELDS = [
    "lastDividendDateMs",
    "lastDividendPerShare",
    "annualDividendPerShare",
    "dividendPayoutPct",
    "paidYears3y",
    "regularityScore",
    "eventCount",
    "events",
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
    return re.sub(r"\s+", " ", s).strip()


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
        t = t.replace(".", "").replace(",", ".")
    elif "," in t and "." not in t:
        t = t.replace(",", ".")
    elif t.count(".") > 1:
        t = t.replace(".", "")
    elif "." in t:
        left, right = t.split(".", 1)
        if right.isdigit() and len(right) == 3 and left.replace("-", "").isdigit():
            t = left + right
    try:
        val = float(t)
        return -val if neg else val
    except ValueError:
        return None


def parse_date_ms(raw) -> Optional[float]:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        x = float(raw)
        if x <= 0:
            return None
        return x * 1000.0 if x < 10_000_000_000 else x
    s = str(raw).strip()
    if not s:
        return None
    m = re.search(r"(\d{10,13})", s)
    if m:
        x = float(m.group(1))
        return x * 1000.0 if x < 10_000_000_000 else x
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt.timestamp() * 1000.0
    except Exception:
        return None


def fetch_json_with_retry(
    session: requests.Session,
    url: str,
    timeout: int = 30,
    attempts: int = 4,
    base_sleep_s: float = 1.5,
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
            transient = (
                "rate" in msg
                or "timed out" in msg
                or "badstatusline" in msg
                or "connection aborted" in msg
                or "http_429" in msg
            )
            if attempt >= attempts or not transient:
                break
            time.sleep(base_sleep_s * attempt)
    if last_exc:
        raise last_exc
    raise RuntimeError("unknown_fetch_error")


def get_member_oid(session: requests.Session, ticker: str) -> Optional[str]:
    payload = fetch_json_with_retry(session, f"{BASE_URL}/{LANG}/api/member/filter/{ticker}", timeout=30, attempts=3)
    arr = extract_member_rows(payload)
    if not arr:
        return None
    for row in arr:
        oid = (row or {}).get("mkkMemberOid")
        if oid:
            return oid
    return None


def get_all_disclosures(session: requests.Session, mkk_member_oid: str, day_range: int = 1825) -> List[dict]:
    ranges = []
    for x in [day_range, 1095, 730, 365, 180, 90]:
        xi = int(x)
        if xi > 0 and xi not in ranges:
            ranges.append(xi)
    types = ["ALL", "FR", "DG", "DKM"]

    merged: Dict[int, dict] = {}
    for tp in types:
        for rg in ranges:
            try:
                payload = fetch_json_with_retry(
                    session,
                    f"{BASE_URL}/{LANG}/api/company-detail/sgbf-data/{mkk_member_oid}/{tp}/{rg}",
                    timeout=30,
                    attempts=2,
                )
            except Exception:
                continue
            rows = extract_disclosure_rows(payload)
            if not rows:
                continue
            for r in rows:
                idx = extract_disclosure_index(r)
                if idx is None:
                    continue
                if idx not in merged:
                    merged[idx] = r
            # If we already collected a solid set, stop early.
            if len(merged) >= 40:
                break
        if len(merged) >= 40:
            break

    if not merged:
        return []
    out = list(merged.values())
    out.sort(key=lambda x: (disclosure_date_ms(x) or 0, extract_disclosure_index(x) or 0), reverse=True)
    return out


def get_attachment_detail(session: requests.Session, disclosure_index: int) -> dict:
    url = f"{BASE_URL}/{LANG}/api/notification/attachment-detail/{disclosure_index}"
    payload = fetch_json_with_retry(session, url, timeout=45, attempts=3)
    if isinstance(payload, list):
        return payload[0] if payload and isinstance(payload[0], dict) else {}
    if isinstance(payload, dict):
        for key in ("data", "item", "result"):
            val = payload.get(key)
            if isinstance(val, dict):
                return val
            if isinstance(val, list) and val and isinstance(val[0], dict):
                return val[0]
        return payload
    return {}


def fetch_notification_page_text(session: requests.Session, disclosure_index: int) -> str:
    global LAST_HTTP_TS
    idx = int(disclosure_index)
    if idx in PAGE_TEXT_CACHE:
        return PAGE_TEXT_CACHE[idx]
    url = f"{BASE_URL}/{LANG}/Bildirim/{idx}"
    wait_http = MIN_HTTP_INTERVAL_S - (time.time() - LAST_HTTP_TS)
    if wait_http > 0:
        time.sleep(wait_http)
    resp = session.get(url, headers=HEADERS, timeout=45)
    LAST_HTTP_TS = time.time()
    if resp.status_code >= 400:
        raise RuntimeError(f"notification_page_http_{resp.status_code}")
    soup = BeautifulSoup(resp.text or "", "html.parser")
    text = normalize_label(soup.get_text(" ", strip=True))
    PAGE_TEXT_CACHE[idx] = text
    return text


def extract_disclosure_basic(item: dict) -> dict:
    if not isinstance(item, dict):
        return {}
    basic = item.get("disclosureBasic")
    if isinstance(basic, dict):
        return basic
    return item


def extract_disclosure_title(item: dict) -> str:
    basic = extract_disclosure_basic(item)
    for k in ("title", "subject", "topic", "headline", "notificationSubject"):
        v = basic.get(k)
        if isinstance(v, str) and v.strip():
            return normalize_label(v)
    for k in ("title", "subject", "topic", "headline", "notificationSubject"):
        v = item.get(k) if isinstance(item, dict) else None
        if isinstance(v, str) and v.strip():
            return normalize_label(v)
    return ""


def extract_disclosure_index(item: dict) -> Optional[int]:
    basic = extract_disclosure_basic(item)
    for k in ("disclosureIndex", "notificationIndex", "id", "index"):
        v = basic.get(k)
        if v is None:
            v = item.get(k) if isinstance(item, dict) else None
        try:
            if v is not None:
                return int(v)
        except Exception:
            pass
    return None


def collect_text_fragments(value, out: List[str]):
    if value is None:
        return
    if isinstance(value, str):
        t = value.strip()
        if t:
            out.append(BeautifulSoup(t, "html.parser").get_text(" ", strip=True))
        return
    if isinstance(value, dict):
        for v in value.values():
            collect_text_fragments(v, out)
        return
    if isinstance(value, list):
        for v in value:
            collect_text_fragments(v, out)
        return


def parse_dates_from_text(text: str) -> List[float]:
    if not text:
        return []
    out: List[float] = []
    for m in re.finditer(r"\b(\d{2}\.\d{2}\.\d{4})\b", text):
        raw = m.group(1)
        try:
            dt = datetime.strptime(raw, "%d.%m.%Y").replace(tzinfo=timezone.utc)
            out.append(dt.timestamp() * 1000.0)
        except Exception:
            pass
    return out


def extract_payment_date_from_text(text_norm: str) -> Optional[float]:
    if not text_norm:
        return None
    anchors = [
        "NAKIT KAR PAYI HAK KULLANIM TARIHI",
        "HAK KULLANIM TARIHI",
        "ODEME TARIHI",
        "ODENECEGI TARIH",
        "PAYLARIN DAGITIM TARIHI",
        "DAGITIM TARIHI",
    ]
    for a in anchors:
        for m in re.finditer(re.escape(a), text_norm):
            seg = text_norm[m.start() : m.start() + 220]
            dates = parse_dates_from_text(seg)
            if dates:
                return dates[0]
    all_dates = parse_dates_from_text(text_norm)
    return all_dates[0] if all_dates else None


def parse_llm_payment_date_ms(raw) -> Optional[float]:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return parse_date_ms(raw)
    s = str(raw).strip()
    if not s:
        return None
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            dt = datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
            return dt.timestamp() * 1000.0
        except Exception:
            pass
    parsed = parse_dates_from_text(s)
    if parsed:
        return parsed[0]
    return parse_date_ms(s)


def coerce_llm_amount(raw) -> Optional[float]:
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        val = float(raw)
    else:
        val = parse_tr_number(str(raw))
        if val is None:
            return None
    val = abs(val)
    return val if 0 <= val <= 500 else None


def extract_dividend_with_llm(text_norm: str) -> dict:
    if not text_norm:
        return {"gross": None, "net": None, "paymentDate": None, "paymentDateMs": None}
    if OpenAI is None:
        raise RuntimeError("openai_package_not_available")

    client = OpenAI(timeout=12.0, max_retries=0)
    response = client.chat.completions.create(
        model=OPENAI_DIVIDEND_MODEL,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": DIVIDEND_LLM_SYSTEM_PROMPT},
            {"role": "user", "content": text_norm[:12000]},
        ],
    )
    content = response.choices[0].message.content or ""
    parsed = json.loads(content)
    if not isinstance(parsed, dict):
        raise ValueError("llm_response_not_object")

    gross = coerce_llm_amount(parsed.get("gross"))
    net = coerce_llm_amount(parsed.get("net"))
    payment_date = parsed.get("paymentDate")
    payment_date_ms = parse_llm_payment_date_ms(payment_date)
    return {
        "gross": gross,
        "net": net,
        "paymentDate": str(payment_date).strip() if payment_date not in (None, "") else None,
        "paymentDateMs": payment_date_ms,
    }


def extract_amounts_from_payment_rows(text_norm: str) -> Dict[str, Optional[float]]:
    if not text_norm:
        return {"gross": None, "net": None}
    gross = None
    net = None

    for row_kw in ("PESIN", "PEŞIN", "TAKSIT", "TAKSITLI"):
        for m in re.finditer(re.escape(normalize_label(row_kw)), text_norm):
            seg = text_norm[m.start() : m.start() + 260]
            nums = []
            for z in re.finditer(r"[0-9][0-9\.,]{0,18}", seg):
                v = parse_tr_number(z.group(0))
                if v is None:
                    continue
                v = abs(v)
                if 0 < v <= 500:
                    nums.append(v)
            if not nums:
                continue
            if gross is None:
                gross = nums[0]
            # Common KAP row sequence: brut, oran%, stopaj%, net
            if net is None and len(nums) >= 4 and nums[2] <= 40:
                net = nums[3]
            elif net is None and len(nums) >= 2 and nums[1] <= nums[0]:
                net = nums[1]
            if gross is not None and net is not None:
                return {"gross": gross, "net": net}
    return {"gross": gross, "net": net}


def pick_amounts_from_text(text: str) -> Dict[str, Optional[float]]:
    gross_patterns = [
        r"(?:BRUT|BRÜT)[^0-9]{0,50}([0-9][0-9\.,]{0,18})\s*(?:TL|TRY)?",
        r"(?:PAY BASINA|PAY BAŞINA|HISSE BASINA|HISSE BAŞINA|BEHER PAY)[^0-9]{0,50}(?:BRUT|BRÜT)?[^0-9]{0,30}([0-9][0-9\.,]{0,18})",
        r"(?:NAKIT KAR PAYI|KAR PAYI)[^0-9]{0,50}(?:BRUT|BRÜT)?[^0-9]{0,30}([0-9][0-9\.,]{0,18})",
    ]
    net_patterns = [
        r"(?:NET)[^0-9]{0,50}([0-9][0-9\.,]{0,18})\s*(?:TL|TRY)?",
        r"(?:PAY BASINA|PAY BAŞINA|HISSE BASINA|HISSE BAŞINA|BEHER PAY)[^0-9]{0,50}(?:NET)[^0-9]{0,30}([0-9][0-9\.,]{0,18})",
    ]
    generic_patterns = [
        r"(?:PAY BASINA|PAY BAŞINA|HISSE BASINA|HISSE BAŞINA|BEHER PAY)[^0-9]{0,30}([0-9][0-9\.,]{0,18})",
        r"([0-9][0-9\.,]{0,18})\s*(?:TL|TRY)\s*(?:BRUT|BRÜT|NET)?\s*(?:PAY|HISSE|BEHER PAY)?",
    ]

    def pick(patterns: List[str]) -> Optional[float]:
        vals: List[float] = []
        for p in patterns:
            for m in re.finditer(p, text, re.I):
                v = parse_tr_number(m.group(1))
                if v is None:
                    continue
                # per-share dividends should stay in a plausible range.
                if 0 <= abs(v) <= 500:
                    vals.append(abs(v))
        if not vals:
            return None
        vals.sort(reverse=True)
        return vals[0]

    gross = pick(gross_patterns)
    net = pick(net_patterns)
    if gross is None and net is None:
        gross = pick(generic_patterns)
    return {"gross": gross, "net": net}


def guess_amount_from_title(title_norm: str) -> Optional[float]:
    if not title_norm:
        return None
    amounts = pick_amounts_from_text(title_norm)
    return amounts.get("gross") if amounts.get("gross") is not None else amounts.get("net")


def extract_dividend_from_notification_page(session: requests.Session, disclosure_index: int) -> Dict[str, Optional[float]]:
    try:
        text = fetch_notification_page_text(session, disclosure_index)
    except Exception:
        return {"gross": None, "net": None, "paymentDateMs": None}

    try:
        llm_info = extract_dividend_with_llm(text)
        if any(llm_info.get(k) is not None for k in ("gross", "net", "paymentDateMs")):
            return {
                "gross": llm_info.get("gross"),
                "net": llm_info.get("net"),
                "paymentDateMs": llm_info.get("paymentDateMs"),
            }
    except Exception:
        pass

    row_amounts = extract_amounts_from_payment_rows(text)
    generic_amounts = pick_amounts_from_text(text)
    gross = (
        row_amounts.get("gross")
        if row_amounts.get("gross") is not None
        else generic_amounts.get("gross")
    )
    net = (
        row_amounts.get("net")
        if row_amounts.get("net") is not None
        else generic_amounts.get("net")
    )
    payment_date = extract_payment_date_from_text(text)
    return {"gross": gross, "net": net, "paymentDateMs": payment_date}


def is_meaningful_dividend_row(row: dict) -> bool:
    if not isinstance(row, dict):
        return False
    if to_num(row.get("lastDividendDateMs")) is not None:
        return True
    if to_num(row.get("lastDividendPerShare")) is not None:
        return True
    if to_num(row.get("annualDividendPerShare")) is not None:
        return True
    if to_num(row.get("paidYears3y")) not in (None, 0.0):
        return True
    if to_num(row.get("regularityScore")) not in (None, 0.0):
        return True
    if to_num(row.get("eventCount")) not in (None, 0.0):
        return True
    return bool(isinstance(row.get("events"), list) and row.get("events"))


def is_zero_dividend_row(row: dict) -> bool:
    if not isinstance(row, dict):
        return False
    for k in ("lastDividendDateMs", "lastDividendPerShare", "annualDividendPerShare", "dividendPayoutPct"):
        if to_num(row.get(k)) is not None:
            return False
    if to_num(row.get("paidYears3y")) not in (None, 0.0):
        return False
    if to_num(row.get("regularityScore")) not in (None, 0.0):
        return False
    if to_num(row.get("eventCount")) not in (None, 0.0):
        return False
    return not bool((row.get("events") or []))


def disclosure_date_ms(item: dict) -> Optional[float]:
    basic = extract_disclosure_basic(item)
    keys = [
        "publishDate",
        "publishDateTime",
        "publishedDate",
        "publishedDateTime",
        "disclosureDate",
        "disclosureDateTime",
        "date",
        "createdDate",
        "createdDateTime",
    ]
    for k in keys:
        x = parse_date_ms(basic.get(k))
        if x is not None:
            return x
    if isinstance(item, dict):
        for k in keys:
            x = parse_date_ms(item.get(k))
            if x is not None:
                return x
    return None


def is_dividend_disclosure(title_norm: str) -> bool:
    kws = [
        "KAR PAYI",
        "TEMETTU",
        "TEMETTÜ",
        "KAR DAGITIM",
        "KAR DAĞITIM",
        "DIVIDEND",
        "NAKIT KAR PAYI",
        "KAR PAYI AVANSI",
        "KUPON",
        "DAGITIM",
        "DAĞITIM",
    ]
    return any(k in title_norm for k in kws)


def extract_dividend_per_share_from_attachment(session: requests.Session, disclosure_index: int) -> Dict[str, Optional[float]]:
    try:
        detail = get_attachment_detail(session, disclosure_index)
    except Exception:
        return {"gross": None, "net": None}
    fragments: List[str] = []
    collect_text_fragments(detail, fragments)
    text = " ".join(x for x in fragments if x)
    if not text:
        return {"gross": None, "net": None}
    return pick_amounts_from_text(text)


def empty_row() -> Dict[str, Optional[float]]:
    return {
        "lastDividendDateMs": None,
        "lastDividendPerShare": None,
        "annualDividendPerShare": None,
        "dividendPayoutPct": None,
        "paidYears3y": None,
        "regularityScore": None,
        "eventCount": None,
        "events": [],
    }


def sanitize_existing_row(input_row: dict) -> Dict:
    out = empty_row()
    if not isinstance(input_row, dict):
        return out
    for k in ["lastDividendDateMs", "lastDividendPerShare", "annualDividendPerShare", "dividendPayoutPct", "paidYears3y", "regularityScore", "eventCount"]:
        out[k] = to_num(input_row.get(k))
    if isinstance(input_row.get("events"), list):
        evs = []
        for e in input_row["events"]:
            if not isinstance(e, dict):
                continue
            evs.append(
                {
                    "dateMs": to_num(e.get("dateMs")),
                    "amountPerShare": to_num(e.get("amountPerShare")),
                    "type": str(e.get("type") or "cash"),
                    "title": str(e.get("title") or ""),
                }
            )
        out["events"] = evs[:8]
    return out


def load_existing_snapshot_data() -> Dict[str, Dict]:
    if not OUT_PATH.exists():
        return {}
    try:
        parsed = json.loads(OUT_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    data = parsed.get("data") or {}
    if not isinstance(data, dict):
        return {}
    out: Dict[str, Dict] = {}
    for k, row in data.items():
        symbol = str(k or "").strip().upper()
        if not symbol:
            continue
        out[symbol] = sanitize_existing_row(row)
    return out


def build_symbol_dividend(session: requests.Session, symbol: str, max_items: int = 8) -> Dict:
    oid = get_member_oid(session, symbol)
    if not oid:
        raise RuntimeError("member_oid_not_found")
    disclosures = get_all_disclosures(session, oid, 1825)
    if not disclosures:
        raise RuntimeError(f"all_disclosures_empty oid={oid}")

    candidates = []
    for item in disclosures:
        title = extract_disclosure_title(item)
        idx = extract_disclosure_index(item)
        if not idx:
            continue
        blob_parts: List[str] = []
        collect_text_fragments(item, blob_parts)
        blob = normalize_label(" ".join(blob_parts))
        if not (is_dividend_disclosure(title) or is_dividend_disclosure(blob)):
            continue
        dt = disclosure_date_ms(item)
        candidates.append({"idx": int(idx), "title": title or blob[:180], "dateMs": dt})

    # Fallback: when API title fields are not informative, inspect a few recent notification pages.
    if not candidates:
        recent = []
        for item in disclosures:
            idx = extract_disclosure_index(item)
            if not idx:
                continue
            recent.append({"idx": int(idx), "dateMs": disclosure_date_ms(item), "title": extract_disclosure_title(item)})
        recent.sort(key=lambda x: (x.get("dateMs") or 0, x.get("idx") or 0), reverse=True)
        for rec in recent[:18]:
            try:
                page_info = extract_dividend_from_notification_page(session, rec["idx"])
                text = fetch_notification_page_text(session, rec["idx"])
            except Exception:
                continue
            if is_dividend_disclosure(text):
                title = rec.get("title") or "DIVIDEND_FALLBACK"
                candidates.append(
                    {
                        "idx": rec["idx"],
                        "title": title,
                        "dateMs": rec.get("dateMs") or page_info.get("paymentDateMs"),
                    }
                )
            if len(candidates) >= max_items:
                break

    candidates.sort(key=lambda x: (x.get("dateMs") or 0, x.get("idx") or 0), reverse=True)
    candidates = candidates[:max_items]

    events = []
    for cand in candidates:
        page_info = extract_dividend_from_notification_page(session, cand["idx"])
        amounts = extract_dividend_per_share_from_attachment(session, cand["idx"])
        amount = (
            page_info.get("gross")
            if page_info.get("gross") is not None
            else page_info.get("net")
            if page_info.get("net") is not None
            else amounts.get("gross")
            if amounts.get("gross") is not None
            else amounts.get("net")
        )
        if amount is None:
            amount = guess_amount_from_title(cand["title"])
        kind = "cash"
        if "BEDELSIZ" in cand["title"] or "BONUS" in cand["title"]:
            kind = "bonus"
        event_date = page_info.get("paymentDateMs") if to_num(page_info.get("paymentDateMs")) is not None else cand["dateMs"]
        events.append(
            {
                "dateMs": event_date,
                "amountPerShare": amount,
                "type": kind,
                "title": cand["title"],
            }
        )

    now_ms = int(time.time() * 1000)
    one_year_ms = 365 * 24 * 60 * 60 * 1000
    three_year_ms = 3 * one_year_ms

    annual_sum = 0.0
    annual_count = 0
    years_paid = set()
    for e in events:
        dt = to_num(e.get("dateMs"))
        amt = to_num(e.get("amountPerShare"))
        if dt is not None and dt >= (now_ms - three_year_ms):
            years_paid.add(datetime.fromtimestamp(dt / 1000, tz=timezone.utc).year)
        if dt is not None and dt >= (now_ms - one_year_ms) and amt is not None:
            annual_sum += amt
            annual_count += 1

    last_date = None
    last_amt = None
    for e in events:
        dt = to_num(e.get("dateMs"))
        if last_date is None and dt is not None:
            last_date = dt
        amt = to_num(e.get("amountPerShare"))
        if last_amt is None and amt is not None:
            last_amt = amt
        if last_date is not None and last_amt is not None:
            break

    paid_years_3y = min(3.0, float(len(years_paid)))
    regularity = min(1.0, paid_years_3y / 3.0) if paid_years_3y > 0 else 0.0

    out = empty_row()
    out["lastDividendDateMs"] = to_num(last_date)
    out["lastDividendPerShare"] = to_num(last_amt)
    out["annualDividendPerShare"] = to_num(annual_sum) if annual_count > 0 else None
    out["dividendPayoutPct"] = None  # price-based and computed server-side.
    out["paidYears3y"] = paid_years_3y
    out["regularityScore"] = to_num(regularity)
    out["eventCount"] = float(len(events))
    out["events"] = events
    return out


def build_symbol_dividend_yfinance(symbol: str, max_items: int = 8) -> Dict:
    import yfinance as yf
    ticker_sym = f"{symbol}.IS" if not symbol.endswith(".IS") else symbol
    ticker = yf.Ticker(ticker_sym)
    try:
        divs = ticker.dividends
    except Exception as exc:
        raise RuntimeError(f"yfinance_fetch_failed: {exc}")

    if divs is None or divs.empty:
        return empty_row()

    divs = divs.sort_index(ascending=False)
    events = []
    for dt, val in divs.items():
        if val <= 0:
            continue
        date_ms = float(dt.timestamp() * 1000.0)
        events.append({
            "dateMs": date_ms,
            "amountPerShare": float(val),
            "type": "cash",
            "title": f"yfinance dividend payment: {val:.4f} TRY",
        })
        if len(events) >= max_items:
            break

    now_ms = int(time.time() * 1000)
    one_year_ms = 365 * 24 * 60 * 60 * 1000
    three_year_ms = 3 * one_year_ms

    annual_sum = 0.0
    annual_count = 0
    years_paid = set()
    for e in events:
        dt = to_num(e.get("dateMs"))
        amt = to_num(e.get("amountPerShare"))
        if dt is not None and dt >= (now_ms - three_year_ms):
            years_paid.add(datetime.fromtimestamp(dt / 1000, tz=timezone.utc).year)
        if dt is not None and dt >= (now_ms - one_year_ms) and amt is not None:
            annual_sum += amt
            annual_count += 1

    last_date = None
    last_amt = None
    for e in events:
        dt = to_num(e.get("dateMs"))
        if last_date is None and dt is not None:
            last_date = dt
        amt = to_num(e.get("amountPerShare"))
        if last_amt is None and amt is not None:
            last_amt = amt
        if last_date is not None and last_amt is not None:
            break

    paid_years_3y = min(3.0, float(len(years_paid)))
    regularity = min(1.0, paid_years_3y / 3.0) if paid_years_3y > 0 else 0.0

    out = empty_row()
    out["lastDividendDateMs"] = to_num(last_date)
    out["lastDividendPerShare"] = to_num(last_amt)
    out["annualDividendPerShare"] = to_num(annual_sum) if annual_count > 0 else None
    out["dividendPayoutPct"] = None
    out["paidYears3y"] = paid_years_3y
    out["regularityScore"] = to_num(regularity)
    out["eventCount"] = float(len(events))
    out["events"] = events
    return out


def build_snapshot(
    limit: Optional[int] = None,
    sleep_s: float = 0.8,
    start: int = 0,
    count: Optional[int] = None,
    max_items: int = 8,
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
    data: Dict[str, Dict] = {c.upper(): sanitize_existing_row(existing.get(c.upper()) or {}) for c in all_codes}
    failures = []
    processed = 0
    updated = 0
    meaningful_updates = 0
    kept_existing_on_fail = 0

    print(
        f"[INFO] KAP dividend snapshot start total={len(all_codes)} work={len(work_codes)} "
        f"start={start} end={end} sleep={sleep_s}s maxItems={max_items} merge_existing={merge_existing}",
        flush=True,
    )
    for i, code in enumerate(work_codes, start=1):
        symbol = code.upper()
        prev_row = data.get(symbol) or empty_row()
        try:
            row = build_symbol_dividend(sess, symbol, max_items=max_items)
            data[symbol] = row
            updated += 1
            if is_meaningful_dividend_row(row):
                meaningful_updates += 1
            print(
                f"[{i}/{len(work_codes)}] {symbol} -> ok (KAP, events={int(to_num(row.get('eventCount')) or 0)} "
                f"lastAmt={row.get('lastDividendPerShare')})",
                flush=True,
            )
        except Exception as exc:
            print(f"[{i}/{len(work_codes)}] {symbol} -> KAP error: {exc}. Retrying with yfinance fallback...", flush=True)
            try:
                row = build_symbol_dividend_yfinance(symbol, max_items=max_items)
                data[symbol] = row
                updated += 1
                if is_meaningful_dividend_row(row):
                    meaningful_updates += 1
                print(
                    f"[{i}/{len(work_codes)}] {symbol} -> ok (yfinance fallback, events={int(to_num(row.get('eventCount')) or 0)} "
                    f"lastAmt={row.get('lastDividendPerShare')})",
                    flush=True,
                )
            except Exception as yf_exc:
                failures.append({"symbol": symbol, "reason": f"KAP: {exc}; yf: {yf_exc}"})
                if prev_row:
                    kept_existing_on_fail += 1
                data[symbol] = prev_row
                print(f"[{i}/{len(work_codes)}] {symbol} -> ERROR (KAP & yfinance failed): {yf_exc}", flush=True)
        finally:
            processed += 1
            time.sleep(max(0.0, sleep_s) + random.uniform(0.0, 0.2))

    now = datetime.now(timezone.utc)
    symbol_count = len(all_codes)
    non_null_symbols = sum(1 for row in data.values() if is_meaningful_dividend_row(row))
    event_symbols = sum(1 for row in data.values() if to_num((row or {}).get("eventCount")) not in (None, 0.0))
    amount_symbols = sum(
        1
        for row in data.values()
        if to_num((row or {}).get("lastDividendPerShare")) is not None
        or to_num((row or {}).get("annualDividendPerShare")) is not None
    )
    dated_symbols = sum(1 for row in data.values() if to_num((row or {}).get("lastDividendDateMs")) is not None)
    zero_like_symbols = sum(1 for row in data.values() if is_zero_dividend_row(row))

    payload = {
        "generatedAt": now.isoformat(),
        "generatedAtMs": int(now.timestamp() * 1000),
        "source": "kap_dividend",
        "symbolCount": symbol_count,
        "failureCount": len(failures),
        "failures": failures,
        "audit": {
            "processedSymbols": processed,
            "updatedSymbols": updated,
            "workRange": {"start": start, "end": end},
            "keptExistingOnFail": kept_existing_on_fail,
            "mergeExisting": merge_existing,
            "maxItems": max_items,
            "sleepSeconds": sleep_s,
            "symbolCoveragePct": round((non_null_symbols / symbol_count) * 100, 1) if symbol_count else 0.0,
            "meaningfulUpdatedSymbols": meaningful_updates,
            "eventSymbols": event_symbols,
            "amountSymbols": amount_symbols,
            "datedSymbols": dated_symbols,
            "zeroLikeSymbols": zero_like_symbols,
        },
        "data": data,
    }
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="Process first N symbols for quick test")
    parser.add_argument("--start", type=int, default=0, help="Start index in BIST list (0-based)")
    parser.add_argument("--count", type=int, default=None, help="How many symbols to process from --start")
    parser.add_argument("--sleep", type=float, default=0.8, help="Sleep seconds between symbols")
    parser.add_argument("--max-items", type=int, default=8, help="Max dividend disclosure candidates per symbol")
    parser.add_argument("--no-merge-existing", action="store_true", help="Do not merge with existing snapshot rows")
    args = parser.parse_args()

    payload = build_snapshot(
        limit=args.limit,
        sleep_s=args.sleep,
        start=args.start,
        count=args.count,
        max_items=max(1, int(args.max_items)),
        merge_existing=not bool(args.no_merge_existing),
    )

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    symbol_count = int(payload.get("symbolCount") or 0)
    failure_count = int(payload.get("failureCount") or 0)
    processed_count = int(((payload.get("audit") or {}).get("processedSymbols")) or symbol_count)
    updated_count = int(((payload.get("audit") or {}).get("updatedSymbols")) or 0)
    meaningful_updated = int(((payload.get("audit") or {}).get("meaningfulUpdatedSymbols")) or 0)
    event_symbols = int(((payload.get("audit") or {}).get("eventSymbols")) or 0)
    amount_symbols = int(((payload.get("audit") or {}).get("amountSymbols")) or 0)
    zero_like_symbols = int(((payload.get("audit") or {}).get("zeroLikeSymbols")) or 0)
    work_range = ((payload.get("audit") or {}).get("workRange")) or {}
    work_start = int(work_range.get("start") or 0)
    work_end = int(work_range.get("end") or symbol_count)
    work_size = max(0, work_end - work_start)
    is_partial_run = symbol_count > 0 and work_size < symbol_count

    # Promote unless partial run completely failed.
    partial_run_bad = (processed_count and failure_count > processed_count * 0.9 and updated_count == 0)
    full_run_bad = (processed_count and failure_count > processed_count * 0.8 and updated_count == 0)
    quality_bad = (
        (processed_count > 0 and meaningful_updated == 0 and updated_count > 0)
        or (not is_partial_run and symbol_count > 0 and event_symbols == 0 and amount_symbols == 0 and zero_like_symbols >= int(symbol_count * 0.95))
    )
    if (is_partial_run and partial_run_bad) or ((not is_partial_run) and full_run_bad) or quality_bad:
        payload.setdefault("audit", {})
        payload["audit"]["lastRunStatus"] = "failed_guard"
        fail_path = OUT_PATH.parent / "dividend_snapshot.last_failed.json"
        fail_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(
            f"[WARN] dividend snapshot not promoted (failures={failure_count}/{processed_count}, "
            f"updated={updated_count}, meaningful={meaningful_updated}, eventSymbols={event_symbols}, "
            f"amountSymbols={amount_symbols}, zeroLike={zero_like_symbols})",
            flush=True,
        )
        print(f"[WARN] failed run saved: {fail_path}", flush=True)
        return 2

    payload.setdefault("audit", {})
    payload["audit"]["lastRunStatus"] = "ok" if failure_count == 0 else "partial"

    if OUT_PATH.exists():
        backup_path = OUT_PATH.parent / "dividend_snapshot.backup.json"
        shutil.copyfile(OUT_PATH, backup_path)
    OUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[OK] snapshot written: {OUT_PATH}", flush=True)
    print(f"[OK] source={payload['source']} symbols={payload['symbolCount']} failures={payload['failureCount']}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
