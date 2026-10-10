"""Daily slow data for the crypto dashboard, run by GitHub Actions.
 
Writes slow.json with:
  m2      US M2 money supply, year on year growth (FRED series M2SL, keyless CSV)
  trends  Google Trends, worldwide, last 12 months, one request per keyword
 
Google Trends has no official API and sometimes rate limits automated requests.
When a keyword fails, the previous value is kept and flagged as stale.
"""
import csv
import datetime as dt
import io
import json
import time
import urllib.parse
import urllib.request
 
KEYWORDS = ["crypto", "bitcoin", "altcoins", "Ethereum", "solana", "Chainlink", "Hyperliquid", "Bittensor", "Aave", "QUANT crypto"]
# Wikipedia articles for the attention indicator ("market" = general crypto interest)
WIKI = {"market": "Cryptocurrency", "BTC": "Bitcoin", "ETH": "Ethereum", "SOL": "Solana_(blockchain_platform)",
        "LINK": "Chainlink_(blockchain)", "HYPE": "Hyperliquid", "TAO": "Bittensor", "AAVE": "Aave"}
OUT = "slow.json"
 
 
def load_previous():
    try:
        with open(OUT) as f:
            return json.load(f)
    except Exception:
        return {}
 
 
def fetch_m2():
    req = urllib.request.Request(
        "https://fred.stlouisfed.org/graph/fredgraph.csv?id=M2SL",
        headers={"User-Agent": "Mozilla/5.0 crypto-dashboard"},
    )
    text = urllib.request.urlopen(req, timeout=30).read().decode()
    rows = list(csv.reader(io.StringIO(text)))[1:]
    rows = [(d, float(v)) for d, v in rows if v not in ("", ".")]
    date, latest = rows[-1]
    year_ago = rows[-13][1]
    return {"date": date, "level_bn": latest, "yoy": round((latest / year_ago - 1) * 100, 2)}
 
 
def fetch_trends(previous):
    """One keyword per request, so small keywords such as "QUANT crypto" keep their own 0 to 100 scale.
    Google often answers automated requests with 429 (too many requests): each keyword gets up to
    4 attempts with growing pauses, and a keyword fetched successfully in the last 3 days is skipped."""
    import random
    from pytrends.request import TrendReq
 
    out = {}
    now = dt.datetime.utcnow()
    for kw in KEYWORDS:
        prev = previous.get(kw) or {}
        fetched = prev.get("fetched")
        if fetched and prev.get("series") and not prev.get("stale"):
            age = now - dt.datetime.strptime(fetched, "%Y-%m-%dT%H:%MZ")
            if age < dt.timedelta(days=3):
                out[kw] = prev  # still fresh, no need to ask Google again
                continue
        result, last_error = None, ""
        for attempt in range(4):
            try:
                py = TrendReq(hl="en-US", tz=0, timeout=(10, 30))  # new session each attempt
                py.build_payload([kw], timeframe="today 12-m", geo="")
                df = py.interest_over_time()
                if df.empty:
                    raise ValueError("empty response")
                s = df[kw].astype(float)
                avg12 = s.mean()
                last4 = s.tail(4).mean()
                result = {
                    "latest": float(s.iloc[-1]),
                    "last4w": round(last4, 1),
                    "avg12m": round(avg12, 1),
                    "ratio": round(last4 / avg12, 2) if avg12 else None,
                    "series": [int(x) for x in s.tolist()],
                    "stale": False,
                    "fetched": now.strftime("%Y-%m-%dT%H:%MZ"),
                }
                break
            except Exception as e:
                last_error = str(e)[:200]
                wait = 30 * (attempt + 1) + random.uniform(0, 15)
                print(f"{kw}: attempt {attempt + 1} failed ({last_error}), waiting {wait:.0f}s")
                time.sleep(wait)
        if result:
            out[kw] = result
            print(f"{kw}: ok, ratio {result['ratio']}")
        elif prev.get("series"):
            prev["stale"] = True
            out[kw] = prev
        else:
            out[kw] = {"error": last_error}
        time.sleep(random.uniform(20, 40))
    return out
 
 
def fetch_wiki():
    """Average daily views over the last 7 days vs the previous 90 days, per article."""
    end = dt.date.today() - dt.timedelta(days=1)
    start = end - dt.timedelta(days=97)
    out = {}
    for key, article in WIKI.items():
        url = ("https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia.org/all-access/user/"
               f"{urllib.parse.quote(article)}/daily/{start:%Y%m%d}/{end:%Y%m%d}")
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "crypto-dashboard/2.0 (personal dashboard; github.com/raphmkt)"})
            items = json.loads(urllib.request.urlopen(req, timeout=30).read().decode())["items"]
            views = [i["views"] for i in items]
            recent, base = sum(views[-7:]) / 7, sum(views[:-7]) / max(len(views) - 7, 1)
            out[key] = {"article": article, "ratio": round(recent / base, 2) if base else None}
        except Exception as e:
            out[key] = {"article": article, "error": str(e)[:120]}
        time.sleep(1)
    return out
 
 
def main():
    prev = load_previous()
    data = {"updated": dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%MZ")}
    try:
        data["m2"] = fetch_m2()
    except Exception as e:
        data["m2"] = prev.get("m2")
        data["m2_error"] = str(e)[:200]
    data["wiki"] = fetch_wiki()
    print("wiki:", {k: v.get("ratio") for k, v in data["wiki"].items()})
    try:
        data["trends"] = fetch_trends(prev.get("trends", {}))
    except Exception as e:
        data["trends"] = prev.get("trends", {})
        data["trends_error"] = str(e)[:200]
    with open(OUT, "w") as f:
        json.dump(data, f, indent=1)
    print(json.dumps({k: v for k, v in data.items() if k != "trends"}, indent=1))
    print("trends:", {k: v.get("ratio") for k, v in data["trends"].items()})
 
 
if __name__ == "__main__":
    main()
 
