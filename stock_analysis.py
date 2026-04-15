from __future__ import annotations


def format_stock_overview(data: dict) -> str:
    result = data.get("results") or {}
    if not result:
        return "No stock data found."

    name = result.get("name", "Unknown")
    ticker = result.get("ticker", "?")
    exchange = result.get("primary_exchange", "unknown exchange")
    market = result.get("market", "stocks")
    market_cap = result.get("market_cap")
    description = result.get("description", "")
    list_date = result.get("list_date", "unknown")
    employees = result.get("total_employees")
    industry = result.get("sic_description", "")

    lines = [f"{ticker} - {name}", f"Market: {market} | Exchange: {exchange}", f"Listed: {list_date}"]
    if market_cap is not None:
        lines.append(f"Market cap: {int(round(market_cap)):,}")
    if employees is not None:
        lines.append(f"Employees: {employees:,}")
    if industry:
        lines.append(f"Industry: {industry}")
    if description:
        lines.append(f"About: {description}")
    return "\n".join(lines)


def format_stock_news(news_data: dict, max_items: int = 3) -> str:
    results = news_data.get("results") or []
    if not results:
        return "No recent news found."

    lines: list[str] = []
    for item in results[:max_items]:
        title = item.get("title", "Untitled")
        publisher = (item.get("publisher") or {}).get("name", "Unknown source")
        published = item.get("published_utc", "")
        when = f" ({published})" if published else ""
        lines.append(f"- {title} - {publisher}{when}")
    return "\n".join(lines)


def generate_risk_summary(overview_data: dict, news_data: dict, volatility_metrics: dict | None = None) -> str:
    result = overview_data.get("results") or {}
    market_cap = result.get("market_cap")
    industry = (result.get("sic_description") or "").lower()
    description = (result.get("description") or "").lower()
    news_items = news_data.get("results") or []
    titles = " ".join((item.get("title") or "").lower() for item in news_items)

    risks: list[str] = []
    if market_cap is not None and market_cap < 10_000_000_000:
        risks.append("Smaller market cap can imply higher volatility.")
    else:
        risks.append("Large-cap stocks can still be sensitive to broad market drawdowns.")

    if "software" in industry or "technology" in industry or "cloud" in description:
        risks.append("Tech-sector names are exposed to valuation compression and product-cycle execution risk.")
    if "financial" in industry or "bank" in description:
        risks.append("Financial firms are exposed to credit, liquidity, and interest-rate risks.")

    if any(word in titles for word in ["lawsuit", "probe", "investigation", "regulator", "antitrust"]):
        risks.append("Recent headlines suggest elevated regulatory or legal risk.")
    if any(word in titles for word in ["layoff", "cuts", "restructuring", "guidance cut", "miss"]):
        risks.append("Recent headlines suggest potential operational or earnings pressure.")

    if volatility_metrics and volatility_metrics.get("annualized_vol") is not None:
        annualized_vol = float(volatility_metrics["annualized_vol"])
        if annualized_vol >= 0.50:
            risks.append("Recent realized volatility is elevated, indicating higher short-term price risk.")
        elif annualized_vol >= 0.30:
            risks.append("Recent realized volatility is moderate, suggesting noticeable price swings.")
        else:
            risks.append("Recent realized volatility is relatively low versus many growth names.")

    if not risks:
        risks.append("No obvious red flags from the latest fetched metadata; still assess valuation, balance sheet, and earnings trend.")

    return "\n".join(f"- {item}" for item in risks[:4])


def detect_stock_response_focus(user_input: str) -> str:
    lowered = user_input.lower()
    if any(word in lowered for word in ["volatility", "volatile", "variance", "std dev", "standard deviation"]):
        return "volatility"
    if any(word in lowered for word in ["risk", "risks", "risky", "downside", "concern", "concerns"]):
        return "risks"
    if any(word in lowered for word in ["news", "headline", "headlines"]):
        return "news"
    return "full"


def format_volatility_metrics(volatility_metrics: dict | None) -> str:
    if not volatility_metrics:
        return "Volatility data unavailable."

    window_days = int(volatility_metrics.get("window_days", 0))
    observations = int(volatility_metrics.get("observations", 0))
    daily_vol = float(volatility_metrics.get("daily_vol", 0.0))
    annualized_vol = float(volatility_metrics.get("annualized_vol", 0.0))
    return (
        f"{window_days}d daily volatility: {daily_vol * 100:.2f}%\n"
        f"{window_days}d annualized realized volatility: {annualized_vol * 100:.2f}%\n"
        f"Observations: {observations} trading days"
    )


def build_stock_report(
    ticker: str,
    overview_data: dict,
    news_data: dict,
    focus: str = "full",
    volatility_metrics: dict | None = None,
) -> str:
    overview_block = format_stock_overview(overview_data)
    news_block = format_stock_news(news_data)
    risk_block = generate_risk_summary(overview_data, news_data, volatility_metrics)
    volatility_block = format_volatility_metrics(volatility_metrics)

    if focus == "risks":
        return f"Risks ({ticker})\n{risk_block}"

    if focus == "news":
        return f"News ({ticker})\n{news_block}"

    if focus == "volatility":
        return f"Volatility ({ticker})\n{volatility_block}"

    return (
        f"Overview ({ticker})\n"
        f"{overview_block}\n\n"
        f"News\n"
        f"{news_block}\n\n"
        f"Volatility\n"
        f"{volatility_block}\n\n"
        f"Risks\n"
        f"{risk_block}"
    )
