"""Hourly RSI data for the crypto dashboard, run by GitHub Actions. Writes rsi.json.
 
RSI(14), Wilder method, on four candle sizes: 1 hour, 1 day, 1 week, 1 month.
Weekly and monthly candles are built from daily candles, so each coin needs only two requests.
"1 year" is shown as history: the market average daily RSI today vs 1 day, 1 week, 1 month and 1 year ago
(an RSI on yearly candles would need 14 years of data).
 
Sources (free, no key):
  CoinLore      market cap ranking, to pick the largest coins
  Hyperliquid   hourly and daily candles for every coin it lists
  Kraken        fallback candles for tracked coins Hyperliquid does not list (e.g. QNT)
"""
import datetime as dt
import json
import time
import urllib.request
 
TOP_N = 100                      # how many coins to show
TRACKED = ["BTC", "HYPE", "QNT", "LINK"]
PERIOD = 14
HL = "https://api.hyperliquid.xyz/info"
UA = {"User-Agent": "Mozilla/5.0 crypto-dashboard", "Content-Type": "application/json"}
STABLE = {"USDT", "USDC", "DAI", "FDUSD", "TUSD", "USDE", "USDS", "PYUSD", "USD1", "BUSD", "USDD", "RLUSD", "USDTB", "USDF"}
 
 
def get(url, body=None, tries=4):
    for attempt in range(tries):
        try:
            data = json.dumps(body).encode() if body is not None else None
            req = urllib.request.Request(url, data=data, headers=UA, method="POST" if body is not None else "GET")
            return json.loads(urllib.request.urlopen(req, timeout=30).read().decode())
        except Exception as e:
            wait = 5 * (attempt + 1) * (3 if "429" in str(e) else 1)
            print(f"  retry {attempt + 1} for {url[:60]} ({e}), waiting {wait}s")
            time.sleep(wait)
    return None
 
 
def rsi_series(closes, n=PERIOD):
    """Wilder RSI for every point from index n onwards (None before)."""
    out = [None] * len(closes)
    if len(closes) <= n:
        return out
    gains = [max(closes[i] - closes[i - 1], 0) for i in range(1, len(closes))]
    losses = [max(closes[i - 1] - closes[i], 0) for i in range(1, len(closes))]
    ag, al = sum(gains[:n]) / n, sum(losses[:n]) / n
    out[n] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    for i in range(n + 1, len(closes)):
        ag = (ag * (n - 1) + gains[i - 1]) / n
        al = (al * (n - 1) + losses[i - 1]) / n
        out[i] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return out
 
 
def last_rsi(closes):
    s = rsi_series(closes)
    return round(s[-1], 1) if s and s[-1] is not None else None
 
 
def aggregate(daily, key):
    """daily: list of (datetime, close). Returns closes of each week or month (last close of the period)."""
    out, cur = [], None
    for d, c in daily:
        k = key(d)
        if k != cur:
            out.append(c)
            cur = k
        else:
            out[-1] = c
    return out
 
 
def hl_candles(coin, interval, days):
    end = int(time.time() * 1000)
    start = end - days * 86400000
    rows = get(HL, {"type": "candleSnapshot", "req": {"coin": coin, "interval": interval, "startTime": start, "endTime": end}})
    if not rows:
        return []
    return [(dt.datetime.utcfromtimestamp(r["t"] / 1000), float(r["c"])) for r in rows]
 
 
def kraken_candles(sym, minutes):
    pair = f"{sym}USD"
    j = get(f"https://api.kraken.com/0/public/OHLC?pair={pair}&interval={minutes}")
    if not j or j.get("error") or not j.get("result"):
        return []
    key = [k for k in j["result"] if k != "last"][0]
    return [(dt.datetime.utcfromtimestamp(int(r[0])), float(r[4])) for r in j["result"][key]]
 
 
def coin_rsi(hourly, daily):
    hc = [c for _, c in hourly]
    dc = [c for _, c in daily]
    weekly = aggregate(daily, lambda d: d.isocalendar()[:2])
    monthly = aggregate(daily, lambda d: (d.year, d.month))
    ds = rsi_series(dc)
    hist = {}
    for label, back in (("now", 1), ("d1", 2), ("w1", 8), ("m1", 31), ("y1", 366)):
        if len(ds) >= back and ds[-back] is not None:
            hist[label] = round(ds[-back], 1)
    hs = rsi_series(hc)
    return {
        "h1": round(hs[-1], 1) if hs and hs[-1] is not None else None,
        "h1_prev": round(hs[-2], 1) if len(hs) > 1 and hs[-2] is not None else None,
        "d1": hist.get("now"),
        "w1": last_rsi(weekly),
        "m1": last_rsi(monthly),
        "hist": hist,
        "price": hc[-1] if hc else (dc[-1] if dc else None),
        "ch24": round((hc[-1] / hc[-25] - 1) * 100, 2) if len(hc) > 25 and hc[-25] else None,
        # price changes from daily closes (used by the dashboard for 7 day, 1 month and 1 year moves)
        "ch7": round((dc[-1] / dc[-8] - 1) * 100, 2) if len(dc) >= 8 and dc[-8] else None,
        "ch30": round((dc[-1] / dc[-31] - 1) * 100, 2) if len(dc) >= 31 and dc[-31] else None,
        "ch1y": round((dc[-1] / dc[-366] - 1) * 100, 2) if len(dc) >= 366 and dc[-366] else None,
    }
 
 
def main():
    # 1. coin list: largest coins by market cap that Hyperliquid lists, plus tracked coins
    meta = get(HL, {"type": "metaAndAssetCtxs"})
    hl_names, vol = {}, {}
    if meta:
        for u, ctx in zip(meta[0]["universe"], meta[1]):
            if u.get("isDelisted"):
                continue
            name = u["name"]
            base = name[1:] if name.startswith("k") and name[1:].isupper() else name  # kPEPE = 1000 PEPE
            hl_names[base] = name
            vol[base] = float(ctx.get("dayNtlVlm") or 0)
    ranked = []
    for start in (0, 100, 200):
        j = get(f"https://api.coinlore.net/api/tickers/?start={start}&limit=100")
        for t in (j or {}).get("data", []):
            ranked.append((int(t["rank"]), t["symbol"].upper(), t["name"], float(t.get("market_cap_usd") or 0)))
    names = {s: n for _, s, n, _ in ranked}
    mcap = {s: m for _, s, _, m in ranked}
    if ranked:
        order = [s for _, s, _, _ in sorted(ranked) if s in hl_names and s not in STABLE]
    else:  # CoinLore down: fall back to Hyperliquid volume ranking
        order = sorted(hl_names, key=lambda s: -vol.get(s, 0))
    selected = TRACKED + [s for s in order if s not in TRACKED][:TOP_N - len(TRACKED)]
 
    # 2. candles and RSI per coin
    coins = []
    for s in selected:
        if s in hl_names:
            hourly = hl_candles(hl_names[s], "1h", 12)          # 288 hours
            daily = hl_candles(hl_names[s], "1d", 520)          # enough for monthly RSI and 1 year history
            src = "Hyperliquid"
            time.sleep(2.5)                                     # stay under Hyperliquid's rate limit
        else:
            hourly = kraken_candles(s, 60)
            daily = kraken_candles(s, 1440)
            src = "Kraken"
            time.sleep(2)
        if not hourly and not daily:
            print(f"{s}: no candles")
            continue
        r = coin_rsi(hourly, daily)
        r.update({"symbol": s, "name": names.get(s, s), "mcap": mcap.get(s), "src": src, "tracked": s in TRACKED})
        coins.append(r)
        print(f"{s}: 1h {r['h1']}  1d {r['d1']}  1w {r['w1']}  1M {r['m1']}")
 
    coins.sort(key=lambda c: -(c["mcap"] or 0))  # largest first, tracked coins at their real rank
 
    # 3. market averages and overbought / oversold counts
    def avg(vals):
        vals = [v for v in vals if v is not None]
        return round(sum(vals) / len(vals), 1) if vals else None
 
    summary = {}
    for tf in ("h1", "d1", "w1", "m1"):
        vals = [c[tf] for c in coins if c[tf] is not None]
        summary[tf] = {"avg": avg(vals), "overbought": sum(v >= 70 for v in vals), "oversold": sum(v <= 30 for v in vals),
                       "count": len(vals)}
    summary["h1"]["prev"] = avg([c["h1_prev"] for c in coins])
    history = {k: avg([c["hist"].get(k) for c in coins]) for k in ("now", "d1", "w1", "m1", "y1")}
 
    out = {"updated": dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%MZ"), "period": PERIOD, "summary": summary,
           "history": history, "coins": coins}
    with open("rsi.json", "w") as f:
        json.dump(out, f, separators=(",", ":"))
    print(json.dumps({"coins": len(coins), "summary": summary, "history": history}, indent=1))
 
 
if __name__ == "__main__":
    main()
 
