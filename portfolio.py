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
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.9",
}

# ── Holdings ──────────────────────────────────────────────────────
CRYPTO = {
    # coingecko_id: amount
    "bitcoin":      0.04281247,
    "cosmos":       29.2935,
    "ethereum":     0.01175,
    "decentraland": 5300,        # MANA
    "pepe":         526749331,
    "dogecoin":     10032,
    "jasmycoin":    101917,
    "shiba-inu":    100664146,
    "bonk":         101080117,
    "official-trump": 12,
}

CRYPTO_LABELS = {
    "bitcoin": "Bitcoin", "cosmos": "Cosmos", "ethereum": "Ethereum",
    "decentraland": "MANA", "pepe": "Pepe", "dogecoin": "Dogecoin",
    "jasmycoin": "JasmyCoin", "shiba-inu": "Shiba Inu",
    "bonk": "BONK", "official-trump": "TRUMP",
}

# Metals — troy ounces held
GOLD_OZ   = 100 / 31.1035          # one 100 g bar → troy oz
SILVER_OZ = 10                     # 10 × 1 oz bars = 10 oz
COPPER_OZ = 1000 / 31.1035         # one 1 kg bar → troy oz

# Stocks/ETF — Yahoo Finance symbols
STOCKS = {
    "VWCE.DE": {"label": "Vanguard FTSE All-World", "shares": 4.83102181},
    "GME":     {"label": "GameStop",                "shares": 10.468814},
    "MSFT":    {"label": "Microsoft",               "shares": 0.06762903},
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
    """Gold/silver/copper spot (USD/oz) via free feed; fallback to fixed USD/oz."""
    # fallbacks (USD per troy oz): copper ≈ $9,650/tonne ≈ $0.30/oz
    spot = {"gold": 2650.0, "silver": 31.0, "copper": 0.30}
    for key, sym in (("gold", "XAU"), ("silver", "XAG"), ("copper", "XCU")):
        try:
            r = requests.get(
                f"https://api.gold-api.com/price/{sym}",
                headers=HEADERS, timeout=TIMEOUT,
            )
            if r.ok and r.json().get("price"):
                spot[key] = r.json()["price"]
            elif key == "copper":
                print(f"  copper (XCU) not available from feed — using fallback ${spot['copper']}/oz")
        except Exception as exc:
            print(f"  {key} fetch error (using fallback): {exc}")
    print(f"Metals: gold=${spot['gold']:.2f}/oz  silver=${spot['silver']:.2f}/oz  copper=${spot['copper']:.4f}/oz")

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
        {
            "label": "Miedź (sztabka 1 kg)",
            "amount": round(COPPER_OZ, 2),
            "unit_pln": spot["copper"] * usd,
            "value_pln": spot["copper"] * usd * COPPER_OZ,
        },
    ]


def get_stocks(fx):
    out = []
    for symbol, info in STOCKS.items():
        price = cur = prev = None

        # Primary: Yahoo Finance chart API (JSON, reliable from cloud runners)
        for attempt in range(3):
            try:
                r = requests.get(
                    f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
                    params={"interval": "1d", "range": "5d"},
                    headers=HEADERS, timeout=TIMEOUT,
                )
                meta = r.json()["chart"]["result"][0]["meta"]
                price = meta.get("regularMarketPrice")
                cur = meta.get("currency", "USD")
                prev = meta.get("chartPreviousClose") or meta.get("previousClose")
                if price:
                    break
            except Exception as exc:
                print(f"  {symbol}: Yahoo attempt {attempt+1} — {exc}")
                time.sleep(1.0)

        if price is None:
            print(f"  stock SKIPPED {symbol}: no usable price")
            continue

        # GBp (London pence) → GBP
        if cur == "GBp":
            price /= 100; cur = "GBP"

        unit_pln = price * fx.get(cur, 1.0)
        value = unit_pln * info["shares"]
        change = ((price - prev) / prev * 100) if prev else None
        row = {
            "label": info["label"],
            "amount": info["shares"],
            "unit_pln": unit_pln,
            "value_pln": value,
        }
        if change is not None:
            row["change_24h"] = change
        out.append(row)
        print(f"  {info['label']:24} {price:>9.2f} {cur}  → {value:>11,.2f} zł")
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
