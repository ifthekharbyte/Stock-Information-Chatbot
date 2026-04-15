from __future__ import annotations

import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.error import HTTPError

from stock_analysis import build_stock_report, detect_stock_response_focus
from stock_compare import maybe_handle_stock_comparison_request
from stock_data import (
    get_stock_news,
    get_stock_ticker_overview,
    get_stock_volatility_metrics,
    load_cache,
    save_cache_if_changed,
    search_stock_tickers,
)


DEFAULT_OLLAMA_MODEL = "llama2-uncensored:latest"
STOCK_CACHE_FILE = "stock_cache.json"
SEC_TICKERS_FILE = "sec_company_tickers.json"
TICKER_PATTERN = re.compile(r"\b[A-Z]{1,5}(?:\.[A-Z]{1,2})?\b")
STOPWORDS = {"tell", "me", "about", "the", "a", "an", "stock", "stocks", "company", "of", "for", "please", "price", "quote", "info", "information"}
NON_STOCK_SINGLE_WORDS = {
    "hi",
    "hello",
    "hey",
    "thanks",
    "thank",
    "yes",
    "no",
    "help",
    "joke",
    "time",
    "weather",
}
NON_STOCK_INTENT_WORDS = {
    "how",
    "what",
    "why",
    "when",
    "where",
    "who",
    "explain",
    "write",
    "create",
    "make",
    "debug",
    "fix",
    "code",
    "python",
    "chatbot",
}
STOCK_QUERY_NOISE_WORDS = {
    "what",
    "which",
    "who",
    "is",
    "are",
    "was",
    "were",
    "do",
    "does",
    "did",
    "tell",
    "show",
    "give",
    "me",
    "about",
    "for",
    "on",
    "the",
    "a",
    "an",
    "stock",
    "stocks",
    "ticker",
    "company",
    "price",
    "quote",
    "news",
    "latest",
    "recent",
    "risk",
    "risks",
    "summary",
}
COMPANY_SUFFIXES = {
    "inc",
    "inc.",
    "corp",
    "corp.",
    "corporation",
    "co",
    "co.",
    "company",
    "ltd",
    "ltd.",
    "limited",
    "plc",
    "llc",
    "holdings",
    "holding",
    "group",
}
COMPARISON_SPLIT_PATTERN = re.compile(r"\b(?:vs\.?|versus|and|or|against)\b", re.IGNORECASE)
COMPARISON_HINT_WORDS = {"compare", "comparison", "versus", "vs", "against", "between"}
NON_STOCK_REPLY = "I only answer stock questions and the app commands we already use here."


def normalize_company_text(text: str) -> str:
    words = re.findall(r"[A-Za-z0-9]+", text.lower())
    return " ".join(words).strip()


def strip_company_suffixes(text: str) -> str:
    words = normalize_company_text(text).split()
    while words and words[-1] in COMPANY_SUFFIXES:
        words.pop()
    return " ".join(words).strip()


def load_company_aliases(filename: str = SEC_TICKERS_FILE) -> dict[str, str]:
    path = Path(filename)
    if not path.exists():
        return {}

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}

    records: list[dict] = []
    if isinstance(data, dict):
        records = [item for item in data.values() if isinstance(item, dict)]
    elif isinstance(data, list):
        records = [item for item in data if isinstance(item, dict)]

    aliases: dict[str, str] = {}
    for item in records:
        ticker = str(item.get("ticker", "")).strip().upper()
        title = str(item.get("title", "")).strip()
        if not ticker or not title:
            continue

        full_name = normalize_company_text(title)
        if full_name:
            aliases[full_name] = ticker

        stripped_name = strip_company_suffixes(title)
        if stripped_name:
            aliases[stripped_name] = ticker

    return aliases


def resolve_company_alias(query: str, aliases: dict[str, str]) -> str | None:
    if not aliases:
        return None

    normalized = normalize_company_text(query)
    if normalized in aliases:
        return aliases[normalized]

    stripped = strip_company_suffixes(query)
    if stripped in aliases:
        return aliases[stripped]

    if normalized:
        for alias, ticker in aliases.items():
            if alias.startswith(normalized + " ") or normalized.startswith(alias + " "):
                return ticker

    return None


def build_cached_stock_report_for_ticker(ticker: str, cache: dict, focus: str) -> str | None:
    ticker = ticker.upper().strip()
    overview = cache.get(f"ticker_overview:{ticker}")
    news = cache.get(f"stock_news:{ticker}:limit:3", {"results": []})
    volatility = cache.get(f"stock_volatility:{ticker}:window:30:lookback:60")
    if not overview:
        return None
    return build_stock_report(ticker, overview, news, focus, volatility)


def format_stock_search_results(data: dict) -> str:
    results = data.get("results") or []
    if not results:
        return "No matching stock ticker found."

    top = results[0]
    symbol = top.get("ticker", "?")
    name = top.get("name", "Unknown")
    exchange = top.get("primary_exchange", "unknown exchange")
    market = top.get("market", "stocks")
    return f"{symbol} - {name}\nMarket: {market} | Exchange: {exchange}"


def select_best_search_result(results: list[dict], query_text: str) -> dict:
    query = query_text.strip().upper()
    query_lower = query_text.strip().lower()

    for item in results:
        if str(item.get("ticker", "")).upper() == query:
            return item

    for item in results:
        if str(item.get("name", "")).strip().lower() == query_lower:
            return item

    for item in results:
        ticker = str(item.get("ticker", "")).upper()
        name = str(item.get("name", "")).lower()
        if query in ticker or query_lower in name:
            return item

    return results[0]


def extract_ticker(text: str) -> str | None:
    matches = TICKER_PATTERN.findall(text)
    if matches:
        return matches[0]
    return None


def summarize_stock_memory_entry(stock_reply: str) -> str:
    first_line = stock_reply.splitlines()[0] if stock_reply else ""
    ticker_match = re.search(r"\(([A-Z]{1,5}(?:\.[A-Z]{1,2})?)\)", first_line)
    if ticker_match:
        return f"Stock lookup completed for {ticker_match.group(1)}."

    symbol_match = re.search(r"\b([A-Z]{1,5}(?:\.[A-Z]{1,2})?)\b", first_line)
    if symbol_match:
        return f"Stock lookup completed for {symbol_match.group(1)}."

    return "Stock lookup completed."


def extract_company_query(text: str) -> str | None:
    words = re.findall(r"[A-Za-z][A-Za-z'.-]*", text)
    filtered = [word for word in words if word.lower() not in STOPWORDS]
    if not filtered:
        return None

    if filtered[-1].lower() == "stock":
        filtered = filtered[:-1]
    if not filtered:
        return None

    return " ".join(filtered)


def build_company_search_queries(text: str) -> list[str]:
    words = re.findall(r"[A-Za-z][A-Za-z'.-]*", text)
    if not words:
        return []

    candidates: list[str] = []
    base_query = extract_company_query(text)
    if base_query:
        candidates.append(base_query)

    cleaned_words = [word for word in words if word.lower() not in STOCK_QUERY_NOISE_WORDS]
    if cleaned_words:
        candidates.append(" ".join(cleaned_words))
        if len(cleaned_words) >= 2:
            candidates.append(" ".join(cleaned_words[-2:]))
        candidates.append(cleaned_words[-1])

    unique: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        normalized = candidate.strip().lower()
        if normalized and normalized not in seen:
            seen.add(normalized)
            unique.append(candidate.strip())
    return unique


def looks_like_stock_request(text: str) -> bool:
    lowered = text.lower()
    keywords = ["stock", "ticker", "company", "market cap", "exchange", "listed", "shares", "fundamental", "overview"]
    if any(keyword in lowered for keyword in keywords) or extract_ticker(text) is not None:
        return True

    company_query = extract_company_query(text)
    if not company_query:
        return False

    words = re.findall(r"[A-Za-z][A-Za-z'.-]*", text)
    lowered_words = [word.lower() for word in words]
    if not lowered_words:
        return False

    if len(lowered_words) == 1:
        token = lowered_words[0]
        return token not in NON_STOCK_SINGLE_WORDS

    has_non_stock_intent = any(word in NON_STOCK_INTENT_WORDS for word in lowered_words)
    if has_non_stock_intent and "about" not in lowered_words:
        return False

    return len(lowered_words) <= 5


def build_non_stock_reply() -> str:
    return "Sorry, I can only help you with stock questions"


def maybe_handle_stock_request(
    user_input: str,
    massive_api_key: str,
    cache: dict,
    company_aliases: dict[str, str] | None = None,
    ollama_model: str = DEFAULT_OLLAMA_MODEL,
) -> str | None:
    if not looks_like_stock_request(user_input):
        return None

    ticker = extract_ticker(user_input)
    company_queries = build_company_search_queries(user_input)
    response_focus = detect_stock_response_focus(user_input)
    aliases = company_aliases or {}
    cache_size_before = len(cache)

    try:
        comparison_reply = maybe_handle_stock_comparison_request(
            user_input,
            massive_api_key,
            cache,
            aliases,
            ollama_model,
        )
        if comparison_reply is not None:
            save_cache_if_changed(cache, cache_size_before)
            return comparison_reply

        if ticker:
            try:
                data = get_stock_ticker_overview(ticker, massive_api_key, cache)
                news = get_stock_news(ticker, massive_api_key, cache)
                volatility = None
                if response_focus in {"full", "risks", "volatility"}:
                    volatility = get_stock_volatility_metrics(ticker, massive_api_key, cache)
                reply = build_stock_report(ticker, data, news, response_focus, volatility)
                save_cache_if_changed(cache, cache_size_before)
                return reply
            except HTTPError as exc:
                if exc.code == 429:
                    cached_reply = build_cached_stock_report_for_ticker(ticker, cache, response_focus)
                    if cached_reply is not None:
                        return f"Massive is rate-limited (HTTP 429). Showing cached data.\n\n{cached_reply}"
                    return "Massive is rate-limited (HTTP 429) and no cached data is available yet. Please try again in a moment."
                if exc.code != 404:
                    return f"Massive request failed: HTTP {exc.code}."

        if company_queries:
            for company_query in company_queries:
                alias_ticker = resolve_company_alias(company_query, aliases)
                if alias_ticker:
                    try:
                        data = get_stock_ticker_overview(alias_ticker, massive_api_key, cache)
                        news = get_stock_news(alias_ticker, massive_api_key, cache)
                        volatility = None
                        if response_focus in {"full", "risks", "volatility"}:
                            volatility = get_stock_volatility_metrics(alias_ticker, massive_api_key, cache)
                        reply = build_stock_report(alias_ticker, data, news, response_focus, volatility)
                        save_cache_if_changed(cache, cache_size_before)
                        return reply
                    except HTTPError as exc:
                        if exc.code == 429:
                            cached_reply = build_cached_stock_report_for_ticker(alias_ticker, cache, response_focus)
                            if cached_reply is not None:
                                return f"Massive is rate-limited (HTTP 429). Showing cached data.\n\n{cached_reply}"
                            return "Massive is rate-limited (HTTP 429) and no cached data is available yet. Please try again in a moment."
                        if exc.code != 404:
                            return f"Massive request failed: HTTP {exc.code}."

                search_data = search_stock_tickers(company_query, massive_api_key, cache)
                results = search_data.get("results") or []
                if not results:
                    continue

                best = select_best_search_result(results, company_query if not ticker else ticker)
                best_ticker = str(best.get("ticker", "")).strip() if best else ""
                if not best_ticker:
                    continue

                try:
                    data = get_stock_ticker_overview(best_ticker, massive_api_key, cache)
                    news = get_stock_news(best_ticker, massive_api_key, cache)
                    volatility = None
                    if response_focus in {"full", "risks", "volatility"}:
                        volatility = get_stock_volatility_metrics(best_ticker, massive_api_key, cache)
                    reply = build_stock_report(best_ticker, data, news, response_focus, volatility)
                    save_cache_if_changed(cache, cache_size_before)
                    return reply
                except HTTPError:
                    save_cache_if_changed(cache, cache_size_before)
                    return format_stock_overview({"results": best})

            return "No matching stock ticker found."

        return None
    except HTTPError as exc:
        if exc.code == 429:
            return "Massive is rate-limited (HTTP 429). Please wait briefly and retry."
        return f"Massive request failed: HTTP {exc.code}."
    except Exception as exc:
        return f"Massive request failed: {exc}"
