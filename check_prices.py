#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Fetches charter flight prices from r.pl and writes data.json.
Run by GitHub Actions on a schedule — no browser needed (prices are SSR).
"""

import re
import sys
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

# ── Config ────────────────────────────────────────────────────────
THRESHOLD = 500              # one-way alert threshold (PLN/person)
ROUNDTRIP_THRESHOLD = 1000   # round-trip alert threshold (out + return)
AGE_PARAM = "1996-06-14"

ROUTES = [
    {
        "name": "GDN → Marsa Alam",
        "url": (
            "https://r.pl/bilety-czarterowe/egipt/marsa-alam"
            f"?skad=GDN&oneWay=true&wiek={AGE_PARAM}"
        ),
    },
    {
        "name": "Marsa Alam → GDN",
        "url": (
            "https://r.pl/bilety-czarterowe/polska/gdansk"
            f"?skad=RMF&oneWay=true&wiek={AGE_PARAM}"
        ),
    },
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pl-PL,pl;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Cache-Control": "no-cache",
}

MONTHS = {
    "sty": 1, "lut": 2, "mar": 3, "kwi": 4, "maj": 5, "cze": 6,
    "lip": 7, "sie": 8, "wrz": 9, "paź": 10, "lis": 11, "gru": 12,
}

ROW_RE = re.compile(
    r"[A-Za-z\u00C0-\u017E]+\.\s+"
    r"(\d{1,2})\s+"
    r"([A-Za-z\u00C0-\u017E]+)"
    r"\s*/\s*"
    r"([\d\s\u00A0]+?)"
    r"\s*z\u0142",
    re.UNICODE,
)


def resolve_date(day: int, month_abbr: str) -> str | None:
    m = MONTHS.get(month_abbr.lower())
    if m is None:
        return None
    now = datetime.now()
    try:
        candidate = datetime(now.year, m, day)
    except ValueError:
        return None
    if candidate.date() < now.date():
        try:
            candidate = datetime(now.year + 1, m, day)
        except ValueError:
            return None
    return candidate.strftime("%Y-%m-%d")


def scrape(route: dict) -> dict:
    print(f"\n── {route['name']} ──")
    print(f"   URL: {route['url']}")

    try:
        resp = requests.get(route["url"], headers=HEADERS, timeout=30)
        print(f"   HTTP {resp.status_code}  |  {len(resp.text):,} chars")

        # Debug: show a snippet to confirm we're getting real content
        snippet_start = resp.text.find("Wylot")
        if snippet_start == -1:
            print("   WARNING: 'Wylot' not found in response — page may be blocked/redirected")
            print(f"   First 300 chars: {repr(resp.text[:300])}")
        else:
            print(f"   Found 'Wylot' at char {snippet_start} ✓")
            print(f"   Snippet: {repr(resp.text[snippet_start:snippet_start+120])}")

        resp.raise_for_status()

    except requests.RequestException as exc:
        print(f"   FETCH ERROR: {exc}")
        return {
            "name":           route["name"],
            "url":            route["url"],
            "cheapest_price": None,
            "cheapest_date":  None,
            "alerts":         [],
            "error":          str(exc),
        }

    all_flights = []
    for m in ROW_RE.finditer(resp.text):
        day_s, month_s, price_s = m.group(1), m.group(2), m.group(3)
        price = int(re.sub(r"[\s\u00A0]", "", price_s))
        date  = resolve_date(int(day_s), month_s)
        if date:
            all_flights.append({"date": date, "price": price})

    print(f"   Parsed {len(all_flights)} flights")

    if not all_flights:
        print("   WARNING: 0 flights parsed — regex may need updating")
        return {
            "name":           route["name"],
            "url":            route["url"],
            "cheapest_price": None,
            "cheapest_date":  None,
            "alerts":         [],
        }

    all_flights.sort(key=lambda x: x["price"])
    cheapest = all_flights[0]
    alerts   = [f for f in all_flights if f["price"] <= THRESHOLD]

    print(f"   Cheapest: {cheapest['price']} zł on {cheapest['date']}")
    print(f"   Alerts (≤{THRESHOLD} zł): {len(alerts)}")

    return {
        "name":           route["name"],
        "url":            route["url"],
        "cheapest_price": cheapest["price"],
        "cheapest_date":  cheapest["date"],
        "alerts":         alerts,
    }


def scrape_roundtrip() -> dict:
    """
    Fetch the round-trip page (oneWay=false) and find the cheapest valid
    out + return pairing. Round-trip leg prices are much lower than one-way
    tickets, so this must be scraped separately.
    """
    # rolling next Saturday (charter flights depart Saturdays)
    today = datetime.now().date()
    days = (5 - today.weekday()) % 7 or 7          # 5 = Saturday
    sat = (today + timedelta(days=days)).strftime("%Y-%m-%d")

    url = (
        "https://r.pl/bilety-czarterowe/egipt/marsa-alam"
        f"?skad=GDN&oneWay=false&wiek={AGE_PARAM}&data={sat}"
    )

    print(f"\n── Round trip (GDN ↔ Marsa Alam) ──")
    print(f"   URL: {url}")

    result = {
        "name": "GDN ↔ Marsa Alam",
        "url": url,
        "threshold": ROUNDTRIP_THRESHOLD,
        "cheapest_total": None,
        "out": None,
        "ret": None,
        "alert": False,
    }

    try:
        resp = requests.get(url, headers=HEADERS, timeout=30)
        print(f"   HTTP {resp.status_code}  |  {len(resp.text):,} chars")
        resp.raise_for_status()
    except requests.RequestException as exc:
        print(f"   FETCH ERROR: {exc}")
        result["error"] = str(exc)
        return result

    text = resp.text
    i_wylot  = text.find("Wylot")
    i_powrot = text.find("Powrót")
    print(f"   'Wylot' @ {i_wylot}   'Powrót' @ {i_powrot}")

    if i_wylot == -1 or i_powrot == -1 or i_powrot < i_wylot:
        print("   WARNING: couldn't locate both Wylot and Powrót sections")
        return result

    out_html = text[i_wylot:i_powrot]   # outbound chips
    ret_html = text[i_powrot:]          # return chips

    def parse(section):
        flights = []
        for m in ROW_RE.finditer(section):
            price = int(re.sub(r"[\s\u00A0]", "", m.group(3)))
            date  = resolve_date(int(m.group(1)), m.group(2))
            if date:
                flights.append({"date": date, "price": price})
        return flights

    outs = parse(out_html)
    rets = parse(ret_html)
    print(f"   Outbound legs: {len(outs)}   Return legs: {len(rets)}")

    if not outs or not rets:
        print("   WARNING: missing legs — cannot compute round trip")
        return result

    # cheapest total where the return is strictly after the outbound
    best = None
    for o in outs:
        for r in rets:
            if r["date"] > o["date"]:
                total = o["price"] + r["price"]
                if best is None or total < best[0]:
                    best = (total, o, r)

    if best is None:
        print("   WARNING: no valid out→return pairing found")
        return result

    total, o, r = best
    result["cheapest_total"] = total
    result["out"] = o
    result["ret"] = r
    result["alert"] = total <= ROUNDTRIP_THRESHOLD
    print(f"   Cheapest round trip: {total} zł  "
          f"({o['date']} {o['price']} zł  +  {r['date']} {r['price']} zł)")
    print(f"   Alert (≤{ROUNDTRIP_THRESHOLD} zł): {result['alert']}")
    return result


def main() -> None:
    print(f"=== check_prices.py  {datetime.now(timezone.utc).isoformat()} ===")
    results = [scrape(r) for r in ROUTES]
    roundtrip = scrape_roundtrip()

    payload = {
        "last_checked":         datetime.now(timezone.utc).isoformat(),
        "threshold":            THRESHOLD,
        "roundtrip_threshold":  ROUNDTRIP_THRESHOLD,
        "routes":               results,
        "roundtrip":            roundtrip,
    }

    out = Path(__file__).parent / "data.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")
    print(f"\n✓ data.json written to {out}")

    # Exit 1 only if BOTH one-way routes failed (round trip is best-effort)
    if all(r.get("cheapest_price") is None for r in results):
        print("ERROR: No prices retrieved for any route — check logs above", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
