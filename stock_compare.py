from __future__ import annotations

import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from urllib.request import Request, urlopen

from stock_analysis import generate_risk_summary
from stock_data import (
    get_stock_news,
    get_stock_ticker_overview,
    get_stock_volatility_metrics,
    search_stock_tickers,
)


COMPARISON_SPLIT_PATTERN = re.compile(r"\b(?:vs\.?|versus|and|or|against)\b", re.IGNORECASE)
COMPARISON_HINT_WORDS = {"compare", "comparison", "versus", "vs", "against", "between"}
STOPWORDS = {"tell", "me", "about", "the", "a", "an", "stock", "stocks", "company", "of", "for", "please", "price", "quote", "info", "information"}
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
TICKER_PATTERN = re.compile(r"\b[A-Z]{1,5}(?:\.[A-Z]{1,2})?\b")


def normalize_company_text(text: str) -> str:
    words = re.findall(r"[A-Za-z0-9]+", text.lower())
    return " ".join(words).strip()


def strip_company_suffixes(text: str) -> str:
    words = normalize_company_text(text).split()
    while words and words[-1] in COMPANY_SUFFIXES:
        words.pop()
    return " ".join(words).strip()


def extract_ticker(text: str) -> str | None:
    matches = TICKER_PATTERN.findall(text)
    if matches:
        return matches[0]
    return None


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


def choose_best_stock_result(results: list[dict], query_text: str) -> dict:
    common_stock = [item for item in results if str(item.get("type", "")).upper() == "CS"]
    if common_stock:
        return select_best_search_result(common_stock, query_text)
    return select_best_search_result(results, query_text)


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


def resolve_stock_target(query: str, api_key: str, cache: dict, aliases: dict[str, str]) -> str | None:
    ticker = extract_ticker(query)
    if ticker:
        return ticker.upper().strip()

    alias_ticker = resolve_company_alias(query, aliases)
    if alias_ticker:
        return alias_ticker.upper().strip()

    for candidate in build_company_search_queries(query):
        search_data = search_stock_tickers(candidate, api_key, cache)
        results = search_data.get("results") or []
        if not results:
            continue
        best = choose_best_stock_result(results, candidate)
        best_ticker = str(best.get("ticker", "")).strip().upper()
        if best_ticker:
            return best_ticker
    return None


def looks_like_stock_comparison_request(text: str) -> bool:
    lowered = text.lower()
    has_hint = any(word in lowered for word in COMPARISON_HINT_WORDS)
    if not has_hint:
        return False
    return len(COMPARISON_SPLIT_PATTERN.split(text)) >= 2


def extract_comparison_company_queries(text: str) -> tuple[str, str] | None:
    pieces = COMPARISON_SPLIT_PATTERN.split(text)
    if len(pieces) < 2:
        return None

    cleaned: list[str] = []
    for piece in pieces:
        query = extract_company_query(piece) or ""
        query = re.sub(r"\b(compare|comparison|between|with|against|risk|risks)\b", " ", query, flags=re.IGNORECASE)
        query = re.sub(r"\s+", " ", query).strip()
        if query:
            cleaned.append(query)

    if len(cleaned) < 2:
        return None
    return cleaned[0], cleaned[1]


def build_stock_evidence_packet(
    ticker: str,
    overview_data: dict,
    news_data: dict,
    volatility_metrics: dict | None,
) -> dict:
    result = overview_data.get("results") or {}
    news_items = news_data.get("results") or []
    risks = generate_risk_summary(overview_data, news_data, volatility_metrics)
    top_news = [
        {
            "title": item.get("title", "Untitled"),
            "published_utc": item.get("published_utc", ""),
        }
        for item in news_items[:3]
    ]
    return {
        "ticker": ticker,
        "company": result.get("name", "Unknown"),
        "market_cap": result.get("market_cap"),
        "industry": result.get("sic_description", ""),
        "exchange": result.get("primary_exchange", ""),
        "volatility_30d_annualized": (volatility_metrics or {}).get("annualized_vol"),
        "risk_bullets": [line.removeprefix("- ") for line in risks.splitlines() if line.strip()],
        "top_news": top_news,
    }


def build_stock_comparison_fallback(user_input: str, left: dict, right: dict) -> str:
    left_ticker = left["ticker"]
    right_ticker = right["ticker"]
    left_name = left.get("company", left_ticker)
    right_name = right.get("company", right_ticker)
    left_vol = left.get("volatility_30d_annualized")
    right_vol = right.get("volatility_30d_annualized")

    left_risks = left.get("risk_bullets") or []
    right_risks = right.get("risk_bullets") or []
    left_risk_text = "; ".join(left_risks[:2]) if left_risks else "no clear risk signal was available"
    right_risk_text = "; ".join(right_risks[:2]) if right_risks else "no clear risk signal was available"

    vol_text = ""
    if left_vol is not None and right_vol is not None:
        if float(left_vol) >= float(right_vol):
            vol_text = (
                f" Over the recent 30-day window, {left_ticker} has been more volatile, at about "
                f"{float(left_vol) * 100:.2f}% annualized realized volatility, versus "
                f"{float(right_vol) * 100:.2f}% for {right_ticker}."
            )
        else:
            vol_text = (
                f" Over the recent 30-day window, {right_ticker} has been more volatile, at about "
                f"{float(right_vol) * 100:.2f}% annualized realized volatility, versus "
                f"{float(left_vol) * 100:.2f}% for {left_ticker}."
            )

    return (
        f"Looking at {left_name} ({left_ticker}) and {right_name} ({right_ticker}), both names carry the usual large-cap "
        f"market risk, but the current signals suggest different short-term risk profiles. For {left_ticker}, the main "
        f"concerns are {left_risk_text}. For {right_ticker}, the key issues are {right_risk_text}.{vol_text}\n\n"
        f"In plain terms, one may currently show sharper short-term swings while the other looks relatively steadier. "
        f"This is an informational comparison based on fetched data only, not investment advice."
    )


def build_stock_comparison_prompt(user_input: str, left: dict, right: dict) -> str:
    evidence = {
        "question": user_input,
        "left_stock": left,
        "right_stock": right,
    }
    return (
        "You are speaking like a calm TV business news presenter. "
        "Use ONLY the evidence JSON below. Do not invent facts, headlines, or numbers. "
        "Do not use bullet points, labels, section headers, or list formatting. "
        "Write exactly 2 short paragraphs in natural spoken English. "
        "Mention both companies by name and ticker at least once. "
        "Explain which stock appears riskier right now, why, and how volatility differs. "
        "Keep it smooth, human, and neutral. "
        "Do not provide buy/sell advice or price targets.\n\n"
        f"Evidence JSON:\n{json.dumps(evidence, ensure_ascii=True)}"
    )


def generate_stock_comparison_with_ollama(prompt: str, model: str) -> str:
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a financial news presenter. "
                    "Answer only from the provided evidence. "
                    "Write naturally in exactly 2 short paragraphs. "
                    "No bullet points, no headings, no labels."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "stream": False,
        "options": {
            "temperature": 0.3,
        },
    }
    request = Request(
        "http://localhost:11434/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=20) as response:
        data = json.loads(response.read().decode("utf-8"))
    return ((data.get("message") or {}).get("content") or "").strip()


def fetch_stock_bundle_for_comparison(ticker: str, massive_api_key: str, cache: dict) -> tuple[dict, dict, dict | None]:
    overview = get_stock_ticker_overview(ticker, massive_api_key, cache)
    news = get_stock_news(ticker, massive_api_key, cache)
    volatility = get_stock_volatility_metrics(ticker, massive_api_key, cache)
    return overview, news, volatility


def is_valid_stock_comparison_output(
    reply: str,
    left_ticker: str,
    right_ticker: str,
    left_name: str = "",
    right_name: str = "",
) -> bool:
    text = (reply or "").strip()
    lowered = text.lower()

    if len(text) < 60:
        return False

    left_refs = [left_ticker.lower()]
    right_refs = [right_ticker.lower()]
    if left_name:
        left_refs.extend(part.lower() for part in left_name.split()[:2])
    if right_name:
        right_refs.extend(part.lower() for part in right_name.split()[:2])

    left_ok = any(ref and ref in lowered for ref in left_refs)
    right_ok = any(ref and ref in lowered for ref in right_refs)
    if not (left_ok and right_ok):
        return False

    blocked_phrases = ["strong buy", "price target", "target price"]
    if any(token in lowered for token in blocked_phrases):
        return False
    return True


def maybe_handle_stock_comparison_request(
    user_input: str,
    massive_api_key: str,
    cache: dict,
    company_aliases: dict[str, str],
    ollama_model: str,
) -> str | None:
    if not looks_like_stock_comparison_request(user_input):
        return None

    queries = extract_comparison_company_queries(user_input)
    if not queries:
        return "Please specify two companies to compare, for example: Compare Nvidia and Microsoft risk."

    left_query, right_query = queries
    left_ticker = resolve_stock_target(left_query, massive_api_key, cache, company_aliases)
    right_ticker = resolve_stock_target(right_query, massive_api_key, cache, company_aliases)
    if not left_ticker or not right_ticker:
        return "I could not resolve both companies to tickers. Please rephrase with clearer company names."
    if left_ticker == right_ticker:
        return "Please provide two different stocks for comparison."

    with ThreadPoolExecutor(max_workers=2) as executor:
        left_future = executor.submit(fetch_stock_bundle_for_comparison, left_ticker, massive_api_key, cache)
        right_future = executor.submit(fetch_stock_bundle_for_comparison, right_ticker, massive_api_key, cache)
        left_overview, left_news, left_vol = left_future.result()
        right_overview, right_news, right_vol = right_future.result()

    left_evidence = build_stock_evidence_packet(left_ticker, left_overview, left_news, left_vol)
    right_evidence = build_stock_evidence_packet(right_ticker, right_overview, right_news, right_vol)
    fallback = build_stock_comparison_fallback(user_input, left_evidence, right_evidence)
    prompt = build_stock_comparison_prompt(user_input, left_evidence, right_evidence)

    try:
        llm_reply = generate_stock_comparison_with_ollama(prompt, ollama_model)
    except Exception:
        return fallback

    debug_enabled = os.environ.get("STOCK_COMPARE_DEBUG", "false").strip().lower() in {"1", "true", "yes", "on"}
    if debug_enabled:
        print("\n[DEBUG] Raw comparison output:")
        print(llm_reply)
        print("[/DEBUG]\n")

    left_name = left_evidence.get("company", "")
    right_name = right_evidence.get("company", "")
    if not llm_reply or not is_valid_stock_comparison_output(llm_reply, left_ticker, right_ticker, left_name, right_name):
        return fallback
    return llm_reply
