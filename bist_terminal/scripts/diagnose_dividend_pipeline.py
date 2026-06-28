#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

import requests

import build_dividend_snapshot_kap as div


def fmt_dt(ms):
    x = div.to_num(ms)
    if x is None:
        return None
    try:
        return datetime.fromtimestamp(x / 1000, tz=timezone.utc).isoformat()
    except Exception:
        return None


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", default="SAHOL", help="Ticker without .IS")
    p.add_argument("--recent", type=int, default=15, help="How many recent disclosures to inspect")
    args = p.parse_args()

    symbol = str(args.symbol or "").strip().upper().replace(".IS", "")
    out = {
        "symbol": symbol,
        "steps": {},
        "candidates": [],
        "recent_scan": [],
        "errors": [],
    }

    sess = requests.Session()
    try:
        oid = div.get_member_oid(sess, symbol)
        out["steps"]["memberOid"] = oid
        if not oid:
            out["errors"].append("member_oid_not_found")
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 2

        disclosures = div.get_all_disclosures(sess, oid, 1825)
        out["steps"]["disclosuresTotal"] = len(disclosures)
        if not disclosures:
            out["errors"].append("all_disclosures_empty")
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 2

        candidates = []
        for item in disclosures:
            idx = div.extract_disclosure_index(item)
            if not idx:
                continue
            title = div.extract_disclosure_title(item)
            blob_parts = []
            div.collect_text_fragments(item, blob_parts)
            blob = div.normalize_label(" ".join(blob_parts))
            is_div = div.is_dividend_disclosure(title) or div.is_dividend_disclosure(blob)
            if is_div:
                candidates.append(
                    {
                        "idx": idx,
                        "title": title or (blob[:180] if blob else ""),
                        "dateMs": div.disclosure_date_ms(item),
                    }
                )

        candidates.sort(key=lambda x: (x.get("dateMs") or 0, x.get("idx") or 0), reverse=True)
        out["steps"]["candidateCount"] = len(candidates)

        for cand in candidates[: max(1, args.recent)]:
            idx = cand["idx"]
            rec = {
                "idx": idx,
                "title": cand.get("title"),
                "dateMs": cand.get("dateMs"),
                "dateIso": fmt_dt(cand.get("dateMs")),
            }
            try:
                page_info = div.extract_dividend_from_notification_page(sess, idx)
                rec["page"] = page_info
            except Exception as exc:
                rec["pageError"] = str(exc)
            try:
                att = div.extract_dividend_per_share_from_attachment(sess, idx)
                rec["attachment"] = att
            except Exception as exc:
                rec["attachmentError"] = str(exc)
            out["candidates"].append(rec)

        # Even if no keyword candidate found, scan some recent disclosures via notification page.
        recent = []
        for item in disclosures:
            idx = div.extract_disclosure_index(item)
            if not idx:
                continue
            recent.append(
                {
                    "idx": idx,
                    "title": div.extract_disclosure_title(item),
                    "dateMs": div.disclosure_date_ms(item),
                }
            )
        recent.sort(key=lambda x: (x.get("dateMs") or 0, x.get("idx") or 0), reverse=True)
        for rec in recent[: max(5, args.recent)]:
            idx = rec["idx"]
            row = {
                "idx": idx,
                "title": rec.get("title"),
                "dateMs": rec.get("dateMs"),
                "dateIso": fmt_dt(rec.get("dateMs")),
            }
            try:
                txt = div.fetch_notification_page_text(sess, idx)
                row["pageHasDividendKeyword"] = div.is_dividend_disclosure(txt)
                pi = div.extract_dividend_from_notification_page(sess, idx)
                row["pageExtract"] = pi
            except Exception as exc:
                row["pageError"] = str(exc)
            out["recent_scan"].append(row)

    except Exception as exc:
        out["errors"].append(str(exc))
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 2

    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
