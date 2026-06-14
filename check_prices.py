#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Fetches charter flight prices from r.pl and writes data.json.
Run by GitHub Actions on a schedule — no browser needed (prices are SSR).
"""

import re
import json
from datetime import datetime, timezone
from pathlib import Path

import requests

# ── Config ────────────────────────────────────────────────────────
THRESHOLD = 500
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
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "pl-PL,pl;q=0.9",
}

MONTHS = {
    "sty": 1, "lut": 2, "mar": 3, "kwi": 4, "maj": 5, "cze": 6,
    "lip": 7, "sie": 8, "wrz": 9, "paź": 10, "lis": 11, "gru": 12,
}

# Matches: "sob. 20 cze / 799 zł"  or  "wt. 16 cze / 1 149 zł"
ROW_RE = re.compile(
    r"[A-Za-z\u00C0-\u017E]+\.\s+"   # day abbrev (e.g. "sob.")
    r"(\d{1,2})\s+"                   # day number
    r"([A-Za-z\u00C0-\u017E]+)"       # month abbrev (e.g. "cze")
    r"\s*/\s*"
    r"([\d\s\u00A0]+?)"               # price — may have NBSP thousands separator
    r"\s*z\u0142",                    # "zł"
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
    print(f"Fetching: {route['name']} …", end=" ", flush=True)
    try:
        resp = requests.get(route["url"], headers=HEADERS, timeout=25)
        resp.raise_for_status()
    except requests.RequestException as exc:
        print(f"ERROR — {exc}")
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

    if not all_flights:
        print("no flights parsed")
        return {
            "name":           route["name"],
            "url":            route["url"],
            "cheapest_price": None,
            "cheapest_date":  None,
            "alerts":         [],
        }

    all_flights.sort(key=lambda x: x["price"])
    cheapest    = all_flights[0]
    alerts      = [f for f in all_flights if f["price"] <= THRESHOLD]

    print(f"cheapest={cheapest['price']} zł  alerts={len(alerts)}")
    return {
        "name":           route["name"],
        "url":            route["url"],
        "cheapest_price": cheapest["price"],
        "cheapest_date":  cheapest["date"],
        "alerts":         alerts,
    }


def main() -> None:
    results = [scrape(r) for r in ROUTES]
    payload = {
        "last_checked": datetime.now(timezone.utc).isoformat(),
        "threshold":    THRESHOLD,
        "routes":       results,
    }
    out = Path(__file__).parent / "data.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")
    print(f"\n✓ data.json written → {out}")


if __name__ == "__main__":
    main()
