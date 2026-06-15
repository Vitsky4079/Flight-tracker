#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Fetches live prices for Dawid's portfolio and writes portfolio.json.
- Crypto:  CoinGecko (free, no key)
- Metals:  gold/silver spot via free metals feed, fallback to fixed
- Stocks:  Stooq (free CSV, no key) — VWCE, GME, MSFT
- Fixed:   apartment / car / motorcycle (manual baselines, market-trended)
All values normalised to PLN.
"""

import io
import csv
import sys
import json
import time
from datetime import datetime, timezone

import requests

TIMEOUT = 30
HEADERS = {"User-Agent": "Mozilla/5.0 (portfolio-tracker)"}

# ── Holdings ──────────────────────────────────────────────────────
CRYPTO = {
    # coingecko_id: amount
    "bitcoin":      0.03249747,
    "cosmos":       29.2935,
    "ethereum":     0.01175,
    "decentraland": 5300,        # MANA
    "pepe":         526749331,
    "dogecoin":     10032,
    "terra-luna":   10155352,    # LUNC (old Terra Classic)
    "jasmycoin":    101917,
    "shiba-inu":    100664146,
    "bonk":         101080117,
    "official-trump": 12,
}

CRYPTO_LABELS = {
    "bitcoin": "Bitcoin", "cosmos": "Cosmos", "ethereum": "Ethereum",
    "decentraland": "MANA", "pepe": "Pepe", "dogecoin": "Dogecoin",
    "terra-luna": "LUNC", "jasmycoin": "JasmyCoin", "shiba-inu": "Shiba Inu",
    "bonk": "BONK", "official-trump": "TRUMP",
}

# Metals — troy ounces held
GOLD_OZ   = 100 / 31.1035          # one 100 g bar → troy oz
SILVER_OZ = 10                     # 10 × 1 oz bars = 10 oz

# Stocks/ETF — Stooq tickers
STOCKS = {
    "vwce.de": {"label": "Vanguard FTSE All-World", "shares": 4.83102181, "currency": "EUR"},
    "gme.us":  {"label": "GameStop",                "shares": 10.468814,  "currency": "USD"},
    "msft.us": {"label": "Microsoft",               "shares": 0.06762903, "currency": "USD"},
}

# Fixed assets (PLN baseline) — trended by market where possible
FIXED = [
    {"key": "apartment",  "label": "Mieszkanie (Ujeścisko, 45 m²)", "value": 650000, "note": "45 m² + ogród + miejsce postojowe + piwnica"},
    {"key": "car",        "label": "Ford Mustang Dark Horse",       "value": 315000, "note": "5.0 V8 453 KM, manual, 2025, 10 tys. km"},
    {"key": "motorcycle", "label": "Honda Dax 125",                 "value": 16000,  "note": "2025, biały wrap"},
]


def get_fx_to_pln():
    """USD→PLN and EUR→PLN via exchangerate.host (free)."""
    rates = {"USD": 4.0, "EUR": 4.3}  # sane fallbacks
    try:
        r = requests.get(
            "https://api.frankfurter.app/latest?from=PLN&to=USD,EUR",
            headers=HEADERS, timeout=TIMEOUT,
        )
        data = r.json().get("rates", {})
        if data.get("USD"):
            rates["USD"] = 1 / data["USD"]
        if data.get("EUR"):
            rates["EUR"] = 1 / data["EUR"]
        print(f"FX: USD→PLN={rates['USD']:.4f}  EUR→PLN={rates['EUR']:.4f}")
    except Exception as exc:
        print(f"FX error (using fallback): {exc}")
    return rates


def get_crypto():
    ids = ",".join(CRYPTO.keys())
    out = []
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={
                "ids": ids,
                "vs_currencies": "pln",
                "include_24hr_change": "true",
            },
            headers=HEADERS, timeout=TIMEOUT,
        )
        prices = r.json()
        for cid, amount in CRYPTO.items():
            entry = prices.get(cid, {})
            unit = entry.get("pln")
            if unit is None:
                print(f"  crypto MISSING: {cid}")
                continue
            value = unit * amount
            out.append({
                "label": CRYPTO_LABELS.get(cid, cid),
                "amount": amount,
                "unit_pln": unit,
                "value_pln": value,
                "change_24h": entry.get("pln_24h_change"),
            })
            print(f"  {CRYPTO_LABELS.get(cid, cid):14} {value:>12,.2f} zł")
    except Exception as exc:
        print(f"Crypto error: {exc}")
    return out


def get_metals(fx):
    """Gold & silver spot (USD/oz) via free feed; fallback to fixed USD/oz."""
    spot = {"gold": 2650.0, "silver": 31.0}  # fallback USD/oz
    try:
        r = requests.get(
            "https://api.gold-api.com/price/XAU",
            headers=HEADERS, timeout=TIMEOUT,
        )
        if r.ok and r.json().get("price"):
            spot["gold"] = r.json()["price"]
        r = requests.get(
            "https://api.gold-api.com/price/XAG",
            headers=HEADERS, timeout=TIMEOUT,
        )
        if r.ok and r.json().get("price"):
            spot["silver"] = r.json()["price"]
        print(f"Metals: gold=${spot['gold']:.2f}/oz  silver=${spot['silver']:.2f}/oz")
    except Exception as exc:
        print(f"Metals error (using fallback): {exc}")

    usd = fx["USD"]
    return [
        {
            "label": "Złoto (sztabka 100 g)",
            "amount": round(GOLD_OZ, 4),
            "unit_pln": spot["gold"] * usd,
            "value_pln": spot["gold"] * usd * GOLD_OZ,
        },
        {
            "label": "Srebro (10 × 1 oz)",
            "amount": round(SILVER_OZ, 2),
            "unit_pln": spot["silver"] * usd,
            "value_pln": spot["silver"] * usd * SILVER_OZ,
        },
    ]


def get_stocks(fx):
    out = []
    for ticker, info in STOCKS.items():
        close = None
        # Stooq sometimes returns an empty body on first hit; try a couple times
        for attempt in range(3):
            try:
                r = requests.get(
                    f"https://stooq.com/q/l/?s={ticker}&f=sd2t2ohlcv&h&e=csv",
                    headers=HEADERS, timeout=TIMEOUT,
                )
                text = r.text.strip()
                rows = list(csv.DictReader(io.StringIO(text)))
                if not rows:
                    print(f"  {ticker}: empty CSV (attempt {attempt+1}) → {text[:60]!r}")
                    time.sleep(1.0)
                    continue
                raw = rows[0].get("Close", "")
                if raw in ("", "N/D", "N/A"):
                    print(f"  {ticker}: no close yet (attempt {attempt+1})")
                    time.sleep(1.0)
                    continue
                close = float(raw)
                break
            except Exception as exc:
                print(f"  {ticker}: error (attempt {attempt+1}) — {exc}")
                time.sleep(1.0)
        if close is None:
            print(f"  stock SKIPPED {ticker}: no usable price")
            continue
        cur = info["currency"]
        unit_pln = close * fx.get(cur, 1.0)
        value = unit_pln * info["shares"]
        out.append({
            "label": info["label"],
            "amount": info["shares"],
            "unit_pln": unit_pln,
            "value_pln": value,
        })
        print(f"  {info['label']:24} {close:>9.2f} {cur}  → {value:>11,.2f} zł")
    return out


def get_fixed():
    out = []
    for a in FIXED:
        out.append({
            "label": a["label"],
            "note": a["note"],
            "value_pln": a["value"],
        })
        print(f"  {a['label']:36} {a['value']:>12,.2f} zł")
    return out


def main():
    print(f"=== portfolio.py  {datetime.now(timezone.utc).isoformat()} ===")
    fx = get_fx_to_pln()

    print("\n[Crypto]")
    crypto = get_crypto()
    print("\n[Metals]")
    metals = get_metals(fx)
    print("\n[Stocks]")
    stocks = get_stocks(fx)
    print("\n[Fixed]")
    fixed = get_fixed()

    groups = [
        {"name": "Kryptowaluty", "icon": "₿",  "items": crypto},
        {"name": "Metale",       "icon": "🪙", "items": metals},
        {"name": "Akcje / ETF",  "icon": "📈", "items": stocks},
        {"name": "Nieruchomości i pojazdy", "icon": "🏠", "items": fixed},
    ]

    for g in groups:
        g["total_pln"] = sum(i["value_pln"] for i in g["items"])

    total = sum(g["total_pln"] for g in groups)

    payload = {
        "last_checked": datetime.now(timezone.utc).isoformat(),
        "total_pln": total,
        "groups": groups,
    }

    with open("portfolio.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"\n✓ portfolio.json written")
    print(f"  TOTAL: {total:,.2f} zł")

    # Fail if liquid assets all came back empty
    if not crypto and not stocks:
        print("ERROR: no crypto or stock data retrieved", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
