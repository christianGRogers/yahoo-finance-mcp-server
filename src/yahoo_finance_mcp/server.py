"""Yahoo Finance MCP server.

Exposes the full Yahoo Finance data surface (via the `yfinance` library) as MCP
tools so an agent can retrieve quotes, historical prices, fundamentals,
financial statements, options chains, holders, analyst data, ESG, news, fund
data, search, screening and market status.

Run with:  yahoo-finance-mcp           (stdio transport, for MCP clients)
       or:  python -m yahoo_finance_mcp.server
"""

from __future__ import annotations

import functools
import os
from typing import Any, Callable, Literal, Optional

import yfinance as yf
from mcp.server.fastmcp import FastMCP

from .serialization import to_jsonable

# Network binding for HTTP transports. Defaults suit a containerized/remote
# deploy; overridden per-environment via MCP_HOST / MCP_PORT.
HOST = os.getenv("MCP_HOST", "0.0.0.0")
PORT = int(os.getenv("MCP_PORT", "8081"))

mcp = FastMCP(
    "yahoo-finance",
    host=HOST,
    port=PORT,
    instructions=(
        "Tools for retrieving data from Yahoo Finance via yfinance. "
        "Tickers use Yahoo symbols (e.g. AAPL, MSFT, BTC-USD, ^GSPC, EURUSD=X). "
        "Use `search` or `lookup` to resolve a company name to a symbol. "
        "Most tools return JSON; date-indexed tables are keyed by ISO date."
    ),
)


# --------------------------------------------------------------------------- #
# Error handling
# --------------------------------------------------------------------------- #
def safe_tool(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap a tool so exceptions become a structured error payload instead of
    crashing the transport. Yahoo endpoints are flaky, so this matters."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            result = fn(*args, **kwargs)
            if result is None:
                return {"error": "No data returned by Yahoo Finance for this request."}
            return result
        except Exception as exc:  # noqa: BLE001 - surface any failure to the agent
            return {
                "error": f"{type(exc).__name__}: {exc}",
                "tool": fn.__name__,
            }

    return wrapper


def _ticker(symbol: str) -> yf.Ticker:
    return yf.Ticker(symbol.strip())


# --------------------------------------------------------------------------- #
# Quotes & company info
# --------------------------------------------------------------------------- #
@mcp.tool()
@safe_tool
def get_quote(symbol: str) -> dict:
    """Get a fast, lightweight current-price snapshot for a symbol.

    Returns last price, previous close, open, day high/low, volume, market cap,
    shares, currency, exchange and 52-week range via yfinance `fast_info`.
    Use this for quick price checks; use `get_ticker_info` for the full profile.
    """
    fi = _ticker(symbol).fast_info
    keys = [
        "currency", "exchange", "quote_type", "timezone",
        "last_price", "previous_close", "open", "day_high", "day_low",
        "regular_market_previous_close", "last_volume", "ten_day_average_volume",
        "three_month_average_volume", "year_high", "year_low",
        "year_change", "fifty_day_average", "two_hundred_day_average",
        "market_cap", "shares",
    ]
    out: dict[str, Any] = {"symbol": symbol.upper()}
    for k in keys:
        try:
            out[k] = to_jsonable(fi[k])
        except Exception:
            out[k] = None
    return out


@mcp.tool()
@safe_tool
def get_ticker_info(symbol: str) -> dict:
    """Get the full company/security profile dictionary for a symbol.

    This is yfinance `.info`: a large dict with business summary, sector,
    industry, valuation ratios (PE, PEG, price-to-book), margins, dividend
    yield, beta, analyst target prices, address, employee count, and dozens of
    other fields. Best single source for fundamentals + descriptive data.
    """
    return {"symbol": symbol.upper(), "info": to_jsonable(_ticker(symbol).info)}


@mcp.tool()
@safe_tool
def get_isin(symbol: str) -> dict:
    """Get the ISIN (International Securities Identification Number) for a symbol."""
    return {"symbol": symbol.upper(), "isin": _ticker(symbol).isin}


# --------------------------------------------------------------------------- #
# Historical prices
# --------------------------------------------------------------------------- #
@mcp.tool()
@safe_tool
def get_historical_prices(
    symbol: str,
    period: Optional[Literal[
        "1d", "5d", "1mo", "3mo", "6mo", "1y", "2y", "5y", "10y", "ytd", "max"
    ]] = "1mo",
    interval: Literal[
        "1m", "2m", "5m", "15m", "30m", "60m", "90m", "1h",
        "1d", "5d", "1wk", "1mo", "3mo"
    ] = "1d",
    start: Optional[str] = None,
    end: Optional[str] = None,
    prepost: bool = False,
    actions: bool = True,
    auto_adjust: bool = True,
) -> dict:
    """Get historical OHLCV price data for a symbol.

    Provide either `period` (e.g. "1y") OR an explicit `start`/`end` date range
    ("YYYY-MM-DD"); if start/end are given they take precedence over period.
    `interval` controls granularity — intraday intervals (1m..1h) are only
    available for recent ranges (Yahoo limits ~7-60 days). Returns a dict keyed
    by ISO timestamp with Open/High/Low/Close/Volume (and Dividends/Splits when
    `actions` is true). `history_metadata` includes timezone & instrument info.
    """
    t = _ticker(symbol)
    kwargs: dict[str, Any] = {
        "interval": interval,
        "prepost": prepost,
        "actions": actions,
        "auto_adjust": auto_adjust,
    }
    if start or end:
        kwargs["start"] = start
        kwargs["end"] = end
    else:
        kwargs["period"] = period
    df = t.history(**kwargs)
    return {
        "symbol": symbol.upper(),
        "rows": int(len(df)),
        "history_metadata": to_jsonable(getattr(t, "history_metadata", None)),
        "prices": to_jsonable(df),
    }


@mcp.tool()
@safe_tool
def download_multiple(
    symbols: list[str],
    period: Optional[str] = "1mo",
    interval: str = "1d",
    start: Optional[str] = None,
    end: Optional[str] = None,
    auto_adjust: bool = True,
) -> dict:
    """Download historical prices for MULTIPLE symbols at once.

    Efficient batch alternative to calling `get_historical_prices` repeatedly.
    Returns a per-symbol dict of date-indexed OHLCV data.
    """
    kwargs: dict[str, Any] = {
        "interval": interval,
        "auto_adjust": auto_adjust,
        "group_by": "ticker",
        "progress": False,
    }
    if start or end:
        kwargs["start"] = start
        kwargs["end"] = end
    else:
        kwargs["period"] = period
    df = yf.download(tickers=symbols, **kwargs)
    out: dict[str, Any] = {}
    syms = [s.upper() for s in symbols]
    if len(syms) == 1:
        out[syms[0]] = to_jsonable(df)
    else:
        for sym in syms:
            try:
                out[sym] = to_jsonable(df[sym])
            except Exception:
                out[sym] = {}
    return {"symbols": syms, "data": out}


# --------------------------------------------------------------------------- #
# Corporate actions
# --------------------------------------------------------------------------- #
@mcp.tool()
@safe_tool
def get_corporate_actions(symbol: str) -> dict:
    """Get dividends, stock splits and capital gains history for a symbol.

    Returns three date-keyed series: `dividends` (per-share amounts),
    `splits` (split ratios) and `capital_gains` (mainly for funds).
    """
    t = _ticker(symbol)
    return {
        "symbol": symbol.upper(),
        "dividends": to_jsonable(t.dividends),
        "splits": to_jsonable(t.splits),
        "capital_gains": to_jsonable(t.capital_gains),
        "actions": to_jsonable(t.actions),
    }


# --------------------------------------------------------------------------- #
# Financial statements
# --------------------------------------------------------------------------- #
@mcp.tool()
@safe_tool
def get_financial_statements(
    symbol: str,
    statement: Literal["income", "balance", "cashflow", "all"] = "all",
    freq: Literal["annual", "quarterly"] = "annual",
) -> dict:
    """Get financial statements (income statement, balance sheet, cash flow).

    `statement` selects one statement or "all" (default). `freq` toggles annual
    vs quarterly. Values are keyed by line item, with periods as columns.
    Data goes back several years/quarters depending on Yahoo coverage.
    """
    t = _ticker(symbol)
    quarterly = freq == "quarterly"
    out: dict[str, Any] = {"symbol": symbol.upper(), "freq": freq}

    if statement in ("income", "all"):
        out["income_statement"] = to_jsonable(
            t.quarterly_income_stmt if quarterly else t.income_stmt
        )
    if statement in ("balance", "all"):
        out["balance_sheet"] = to_jsonable(
            t.quarterly_balance_sheet if quarterly else t.balance_sheet
        )
    if statement in ("cashflow", "all"):
        out["cashflow"] = to_jsonable(
            t.quarterly_cashflow if quarterly else t.cashflow
        )
    return out


@mcp.tool()
@safe_tool
def get_earnings(symbol: str) -> dict:
    """Get earnings calendar, upcoming/historical earnings dates and estimates.

    Returns the `calendar` (next earnings & dividend dates), `earnings_dates`
    (reported vs estimated EPS per period with surprise %), and the various
    forward estimate tables (EPS estimate, revenue estimate, EPS trend & growth).
    """
    t = _ticker(symbol)
    out: dict[str, Any] = {"symbol": symbol.upper()}
    out["calendar"] = to_jsonable(getattr(t, "calendar", None))
    for attr in (
        "earnings_dates", "earnings_estimate", "revenue_estimate",
        "eps_trend", "eps_revisions", "growth_estimates",
    ):
        try:
            out[attr] = to_jsonable(getattr(t, attr))
        except Exception as exc:  # noqa: BLE001
            out[attr] = {"error": str(exc)}
    return out


# --------------------------------------------------------------------------- #
# Analyst coverage
# --------------------------------------------------------------------------- #
@mcp.tool()
@safe_tool
def get_analyst_data(symbol: str) -> dict:
    """Get analyst recommendations, price targets and upgrade/downgrade history.

    Returns `recommendations` and `recommendations_summary` (buy/hold/sell
    counts over recent months), `analyst_price_targets` (current/high/low/mean
    target), and `upgrades_downgrades` (firm-by-firm rating changes).
    """
    t = _ticker(symbol)
    out: dict[str, Any] = {"symbol": symbol.upper()}
    for attr in (
        "recommendations", "recommendations_summary",
        "analyst_price_targets", "upgrades_downgrades",
    ):
        try:
            out[attr] = to_jsonable(getattr(t, attr))
        except Exception as exc:  # noqa: BLE001
            out[attr] = {"error": str(exc)}
    return out


# --------------------------------------------------------------------------- #
# Holders & insiders
# --------------------------------------------------------------------------- #
@mcp.tool()
@safe_tool
def get_holders(symbol: str) -> dict:
    """Get ownership breakdown: major, institutional, mutual fund and insiders.

    Returns `major_holders` (% insider/institutional), `institutional_holders`
    and `mutualfund_holders` (top holders with shares & value), plus insider
    data: `insider_purchases`, `insider_transactions`, `insider_roster_holders`.
    """
    t = _ticker(symbol)
    out: dict[str, Any] = {"symbol": symbol.upper()}
    for attr in (
        "major_holders", "institutional_holders", "mutualfund_holders",
        "insider_purchases", "insider_transactions", "insider_roster_holders",
    ):
        try:
            out[attr] = to_jsonable(getattr(t, attr))
        except Exception as exc:  # noqa: BLE001
            out[attr] = {"error": str(exc)}
    return out


@mcp.tool()
@safe_tool
def get_shares(symbol: str, start: Optional[str] = None, end: Optional[str] = None) -> dict:
    """Get historical shares outstanding over time (optionally a date range)."""
    t = _ticker(symbol)
    return {
        "symbol": symbol.upper(),
        "shares_outstanding": to_jsonable(t.get_shares_full(start=start, end=end)),
    }


# --------------------------------------------------------------------------- #
# Sustainability / ESG
# --------------------------------------------------------------------------- #
@mcp.tool()
@safe_tool
def get_sustainability(symbol: str) -> dict:
    """Get ESG / sustainability scores (environment, social, governance risk)."""
    return {
        "symbol": symbol.upper(),
        "sustainability": to_jsonable(_ticker(symbol).sustainability),
    }


# --------------------------------------------------------------------------- #
# Options
# --------------------------------------------------------------------------- #
@mcp.tool()
@safe_tool
def get_option_expirations(symbol: str) -> dict:
    """List the available option expiration dates for a symbol.

    Pass one of these dates to `get_option_chain` to get the calls/puts table.
    """
    return {"symbol": symbol.upper(), "expirations": list(_ticker(symbol).options)}


@mcp.tool()
@safe_tool
def get_option_chain(symbol: str, expiration: Optional[str] = None) -> dict:
    """Get the option chain (calls and puts) for a symbol and expiration date.

    `expiration` is a "YYYY-MM-DD" date from `get_option_expirations`; if omitted
    the nearest expiry is used. Each contract includes strike, bid/ask, last
    price, volume, open interest, implied volatility and in-the-money flag.
    """
    t = _ticker(symbol)
    if expiration is None:
        exps = list(t.options)
        if not exps:
            return {"error": "No options available for this symbol."}
        expiration = exps[0]
    chain = t.option_chain(expiration)
    return {
        "symbol": symbol.upper(),
        "expiration": expiration,
        "calls": to_jsonable(chain.calls),
        "puts": to_jsonable(chain.puts),
        "underlying": to_jsonable(getattr(chain, "underlying", None)),
    }


# --------------------------------------------------------------------------- #
# News & SEC filings
# --------------------------------------------------------------------------- #
@mcp.tool()
@safe_tool
def get_news(symbol: str, count: int = 10) -> dict:
    """Get recent news articles related to a symbol (title, publisher, link, time)."""
    news = _ticker(symbol).get_news(count=count)
    return {"symbol": symbol.upper(), "news": to_jsonable(news)}


@mcp.tool()
@safe_tool
def get_sec_filings(symbol: str) -> dict:
    """Get recent SEC filings (10-K, 10-Q, 8-K, etc.) with dates and document links."""
    return {"symbol": symbol.upper(), "sec_filings": to_jsonable(_ticker(symbol).sec_filings)}


# --------------------------------------------------------------------------- #
# Fund / ETF data
# --------------------------------------------------------------------------- #
@mcp.tool()
@safe_tool
def get_fund_data(symbol: str) -> dict:
    """Get ETF / mutual fund specifics: description, holdings, sector & asset
    allocation, top holdings, bond ratings and fund operations.

    Only meaningful for funds/ETFs (e.g. SPY, VTI, QQQ). For ordinary stocks
    this returns little or an error.
    """
    fd = _ticker(symbol).funds_data
    out: dict[str, Any] = {"symbol": symbol.upper()}
    for attr in (
        "description", "fund_overview", "fund_operations", "asset_classes",
        "top_holdings", "equity_holdings", "bond_holdings", "bond_ratings",
        "sector_weightings",
    ):
        try:
            out[attr] = to_jsonable(getattr(fd, attr))
        except Exception as exc:  # noqa: BLE001
            out[attr] = {"error": str(exc)}
    return out


# --------------------------------------------------------------------------- #
# Search / lookup
# --------------------------------------------------------------------------- #
@mcp.tool()
@safe_tool
def search(query: str, max_results: int = 10, news_count: int = 5) -> dict:
    """Search Yahoo Finance for symbols and related news by free-text query.

    Use this to resolve a company name (e.g. "Apple") to a ticker symbol, or to
    find related instruments and news. Returns matching `quotes` (symbol, name,
    exchange, type) and recent `news`.
    """
    s = yf.Search(query, max_results=max_results, news_count=news_count)
    return {
        "query": query,
        "quotes": to_jsonable(s.quotes),
        "news": to_jsonable(s.news),
    }


@mcp.tool()
@safe_tool
def lookup(
    query: str,
    lookup_type: Literal[
        "all", "stock", "etf", "mutualfund", "index", "future", "currency", "cryptocurrency"
    ] = "all",
    count: int = 25,
) -> dict:
    """Look up instruments matching a query, optionally filtered by asset type.

    More precise than `search` for enumerating instruments of a given type
    (e.g. all ETFs matching "gold"). Returns matching symbols with metadata.
    """
    lk = yf.Lookup(query)
    type_map = {
        "all": lk.all, "stock": lk.stock, "etf": lk.etf,
        "mutualfund": lk.mutualfund, "index": lk.index, "future": lk.future,
        "currency": lk.currency, "cryptocurrency": lk.cryptocurrency,
    }
    data = type_map[lookup_type]
    if callable(data):
        data = data(count=count)
    return {"query": query, "lookup_type": lookup_type, "results": to_jsonable(data)}


# --------------------------------------------------------------------------- #
# Sector / industry
# --------------------------------------------------------------------------- #
@mcp.tool()
@safe_tool
def get_sector(key: str) -> dict:
    """Get sector overview data by sector key (e.g. "technology", "healthcare",
    "financial-services", "energy", "consumer-cyclical").

    Returns the sector's overview, top companies, top ETFs/mutual funds,
    research reports and industry breakdown.
    """
    sec = yf.Sector(key)
    return {
        "key": key,
        "name": getattr(sec, "name", None),
        "overview": to_jsonable(getattr(sec, "overview", None)),
        "top_companies": to_jsonable(getattr(sec, "top_companies", None)),
        "top_etfs": to_jsonable(getattr(sec, "top_etfs", None)),
        "top_mutual_funds": to_jsonable(getattr(sec, "top_mutual_funds", None)),
        "industries": to_jsonable(getattr(sec, "industries", None)),
        "research_reports": to_jsonable(getattr(sec, "research_reports", None)),
    }


@mcp.tool()
@safe_tool
def get_industry(key: str) -> dict:
    """Get industry overview data by industry key (e.g. "semiconductors",
    "software-infrastructure", "biotechnology", "banks-diversified").

    Returns the industry overview, top performing/growth companies and top ETFs.
    """
    ind = yf.Industry(key)
    return {
        "key": key,
        "name": getattr(ind, "name", None),
        "sector_key": getattr(ind, "sector_key", None),
        "overview": to_jsonable(getattr(ind, "overview", None)),
        "top_companies": to_jsonable(getattr(ind, "top_companies", None)),
        "top_performing_companies": to_jsonable(getattr(ind, "top_performing_companies", None)),
        "top_growth_companies": to_jsonable(getattr(ind, "top_growth_companies", None)),
    }


# --------------------------------------------------------------------------- #
# Market status
# --------------------------------------------------------------------------- #
@mcp.tool()
@safe_tool
def get_market_status(market: str = "US") -> dict:
    """Get market status and summary for a region (e.g. "US", "GB", "ASIA",
    "EUROPE"). Returns whether the market is open and a summary of major indices.
    """
    m = yf.Market(market)
    return {
        "market": market,
        "status": to_jsonable(getattr(m, "status", None)),
        "summary": to_jsonable(getattr(m, "summary", None)),
    }


# --------------------------------------------------------------------------- #
# Screener
# --------------------------------------------------------------------------- #
@mcp.tool()
@safe_tool
def screen_predefined(
    query: Literal[
        "aggressive_small_caps", "day_gainers", "day_losers",
        "growth_technology_stocks", "most_actives", "most_shorted_stocks",
        "small_cap_gainers", "undervalued_growth_stocks",
        "undervalued_large_caps", "conservative_foreign_funds",
        "high_yield_bond", "portfolio_anchors", "solid_large_growth_funds",
        "solid_midcap_growth_funds", "top_mutual_funds",
    ] = "most_actives",
    count: int = 25,
) -> dict:
    """Run a predefined Yahoo Finance stock/fund screen.

    Handy preset screens like "day_gainers", "most_actives", "day_losers",
    "undervalued_growth_stocks", etc. Returns the matching quotes with key
    fields. For custom criteria use `screen_custom`.
    """
    res = yf.screen(query, count=count)
    return {"query": query, "count": count, "result": to_jsonable(res)}


@mcp.tool()
@safe_tool
def screen_custom(
    field: str,
    operator: Literal["gt", "lt", "gte", "lte", "eq", "btwn"],
    values: list[float],
    region: str = "us",
    sort_field: Optional[str] = None,
    sort_asc: bool = False,
    count: int = 25,
) -> dict:
    """Run a custom equity screen with a single numeric criterion + region.

    Example: field="intradaymarketcap", operator="gt", values=[1e9] finds
    companies with market cap over $1B. Common fields: intradaymarketcap,
    intradayprice, dayvolume, trailingpe, pegratio, epsgrowth, dividendyield,
    percentchange. `operator` "btwn" expects two values [low, high].
    Results are filtered to region (e.g. "us"). Sorted by `sort_field` if given.
    """
    from yfinance import EquityQuery

    region_q = EquityQuery("eq", ["region", region])
    criterion = EquityQuery(operator, [field, *values])
    q = EquityQuery("and", [region_q, criterion])
    kwargs: dict[str, Any] = {"size": count}
    if sort_field:
        kwargs["sortField"] = sort_field
        kwargs["sortAsc"] = sort_asc
    res = yf.screen(q, **kwargs)
    return {
        "field": field,
        "operator": operator,
        "values": values,
        "region": region,
        "result": to_jsonable(res),
    }


def main() -> None:
    """Console-script / module entry point.

    Transport is selected with MCP_TRANSPORT:
      * "stdio" (default) — for local MCP clients (Claude Desktop/Code).
      * "streamable-http" — serve over HTTP on MCP_HOST:MCP_PORT (deploy mode).
      * "sse" — legacy SSE HTTP transport.
    """
    transport = os.getenv("MCP_TRANSPORT", "stdio").lower()
    if transport == "stdio":
        mcp.run()
    else:
        mcp.run(transport=transport)


if __name__ == "__main__":
    main()
