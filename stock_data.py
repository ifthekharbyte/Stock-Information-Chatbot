from __future__ import annotations

import json
import math
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen


STOCK_CACHE_FILE = "stock_cache.json"
MASSIVE_API_BASE = "https://api.massive.com/v3"
MASSIVE_API_BASE_V2 = "https://api.massive.com/v2"


def load_cache(filename: str = STOCK_CACHE_FILE) -> dict:
    if Path(filename).exists():
        try:
            with open(filename, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_cache(cache: dict, filename: str = STOCK_CACHE_FILE) -> None:
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, sort_keys=False)


def save_cache_if_changed(cache: dict, previous_size: int, filename: str = STOCK_CACHE_FILE) -> None:
    if len(cache) > previous_size:
        save_cache(cache, filename)


def is_valid_overview_payload(data: dict) -> bool:
    result = data.get("results")
    return isinstance(result, dict) and bool(result.get("ticker"))


def is_valid_news_payload(data: dict) -> bool:
    return data.get("status") == "OK" and isinstance(data.get("results"), list)


def is_valid_search_payload(data: dict) -> bool:
    results = data.get("results")
    return data.get("status") == "OK" and isinstance(results, list) and len(results) > 0


def is_valid_aggregates_payload(data: dict) -> bool:
    return data.get("status") == "OK" and isinstance(data.get("results"), list)


def massive_request(path: str, api_key: str, query: dict | None = None) -> dict:
    params = dict(query or {})
    params["apiKey"] = api_key
    url = f"{MASSIVE_API_BASE}{path}?{urlencode(params)}"
    request = Request(url, method="GET")
    with urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def massive_request_v2(path: str, api_key: str, query: dict | None = None) -> dict:
    params = dict(query or {})
    params["apiKey"] = api_key
    url = f"{MASSIVE_API_BASE_V2}{path}?{urlencode(params)}"
    request = Request(url, method="GET")
    with urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def search_stock_tickers(query_text: str, api_key: str, cache: dict) -> dict:
    normalized = query_text.strip().lower()
    cache_key = f"ticker_search:{normalized}"
    if cache_key in cache:
        return cache[cache_key]

    data = massive_request(
        "/reference/tickers",
        api_key,
        {
            "market": "stocks",
            "active": "true",
            "search": query_text,
            "order": "asc",
            "limit": "10",
            "sort": "ticker",
        },
    )
    if is_valid_search_payload(data):
        cache[cache_key] = data
    return data


def get_stock_ticker_overview(ticker: str, api_key: str, cache: dict) -> dict:
    ticker = ticker.upper().strip()
    cache_key = f"ticker_overview:{ticker}"
    if cache_key in cache:
        return cache[cache_key]

    data = massive_request(f"/reference/tickers/{ticker}", api_key)
    if is_valid_overview_payload(data):
        cache[cache_key] = data
    return data


def get_stock_news(ticker: str, api_key: str, cache: dict, limit: int = 3) -> dict:
    ticker = ticker.upper().strip()
    cache_key = f"stock_news:{ticker}:limit:{limit}"
    if cache_key in cache:
        return cache[cache_key]

    data = massive_request_v2("/reference/news", api_key, {"ticker": ticker, "limit": str(limit), "order": "desc"})
    if is_valid_news_payload(data):
        cache[cache_key] = data
    return data


def get_stock_ticker_list(api_key: str, cache: dict) -> dict:
    cache_key = "ticker_list:stocks:active"
    if cache_key in cache:
        return cache[cache_key]

    data = massive_request("/reference/tickers", api_key, {"market": "stocks", "active": "true", "order": "asc", "limit": "100", "sort": "ticker"})
    if is_valid_search_payload(data):
        cache[cache_key] = data
    return data


def get_stock_aggregates_daily(ticker: str, api_key: str, cache: dict, lookback_days: int = 60) -> dict:
    ticker = ticker.upper().strip()
    cache_key = f"stock_aggs_daily:{ticker}:lookback:{lookback_days}"
    if cache_key in cache:
        return cache[cache_key]

    end_date = date.today()
    start_date = end_date - timedelta(days=lookback_days)
    data = massive_request_v2(
        f"/aggs/ticker/{ticker}/range/1/day/{start_date.isoformat()}/{end_date.isoformat()}",
        api_key,
        {"adjusted": "true", "sort": "asc", "limit": "5000"},
    )
    if is_valid_aggregates_payload(data):
        cache[cache_key] = data
    return data


def compute_realized_volatility_metrics(aggs_data: dict, window_days: int = 30) -> dict | None:
    rows = aggs_data.get("results") or []
    closes = [float(item.get("c")) for item in rows if item.get("c") is not None]
    if len(closes) < window_days + 1:
        return None

    window_closes = closes[-(window_days + 1):]
    returns: list[float] = []
    for previous, current in zip(window_closes[:-1], window_closes[1:]):
        if previous <= 0 or current <= 0:
            continue
        returns.append(math.log(current / previous))

    if len(returns) < 2:
        return None

    mean_return = sum(returns) / len(returns)
    variance = sum((value - mean_return) ** 2 for value in returns) / (len(returns) - 1)
    daily_vol = math.sqrt(variance)
    annualized_vol = daily_vol * math.sqrt(252)

    return {
        "window_days": window_days,
        "observations": len(returns),
        "daily_vol": daily_vol,
        "annualized_vol": annualized_vol,
    }


def get_stock_volatility_metrics(
    ticker: str,
    api_key: str,
    cache: dict,
    window_days: int = 30,
    lookback_days: int = 60,
) -> dict | None:
    ticker = ticker.upper().strip()
    cache_key = f"stock_volatility:{ticker}:window:{window_days}:lookback:{lookback_days}"
    if cache_key in cache:
        return cache[cache_key]

    aggs_data = get_stock_aggregates_daily(ticker, api_key, cache, lookback_days=lookback_days)
    metrics = compute_realized_volatility_metrics(aggs_data, window_days=window_days)
    if metrics is not None:
        cache[cache_key] = metrics
    return metrics
