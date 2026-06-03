# Yahoo Finance MCP Server

An [MCP](https://modelcontextprotocol.io) server that exposes the **full Yahoo
Finance data surface** to AI agents, backed by the
[`yfinance`](https://github.com/ranaroussi/yfinance) library. No API key
required.

> **Note on the "Yahoo Finance API":** Yahoo retired its official public finance
> API years ago. This server uses `yfinance`, which wraps Yahoo's internal
> `query1/query2.finance.yahoo.com` endpoints (handling cookie/crumb auth for
> you). It is the most complete free way to access Yahoo's data, but it is
> unofficial — endpoints can change or rate-limit without notice.

## What the agent can see

Every major data category Yahoo Finance serves is exposed as a tool:

| Tool | Data |
| --- | --- |
| `get_quote` | Fast price snapshot (last/open/high/low, volume, market cap, 52-wk range) |
| `get_ticker_info` | Full company/security profile (`.info`): sector, ratios, margins, targets, summary |
| `get_isin` | ISIN identifier |
| `get_historical_prices` | OHLCV history by period or date range, any interval (1m → 3mo) |
| `download_multiple` | Batch historical prices for many symbols at once |
| `get_corporate_actions` | Dividends, splits, capital gains |
| `get_financial_statements` | Income statement / balance sheet / cash flow (annual & quarterly) |
| `get_earnings` | Earnings calendar & dates, EPS/revenue estimates, EPS trend & growth |
| `get_analyst_data` | Recommendations, price targets, upgrades/downgrades |
| `get_holders` | Major / institutional / mutual-fund holders + insider activity |
| `get_shares` | Historical shares outstanding |
| `get_sustainability` | ESG risk scores |
| `get_option_expirations` | Available option expiry dates |
| `get_option_chain` | Calls & puts (strike, IV, OI, volume, bid/ask) for an expiry |
| `get_news` | Recent related news articles |
| `get_sec_filings` | Recent SEC filings with document links |
| `get_fund_data` | ETF/mutual-fund holdings, allocations, sector weights, bond ratings |
| `search` | Resolve a name → symbol; matching quotes + news |
| `lookup` | Enumerate instruments by type (stock/etf/index/currency/crypto/…) |
| `get_sector` | Sector overview, top companies/ETFs, industries |
| `get_industry` | Industry overview, top performing/growth companies |
| `get_market_status` | Market open/closed status & index summary by region |
| `screen_predefined` | Preset screens (day_gainers, most_actives, …) |
| `screen_custom` | Custom numeric screen (e.g. market cap > $1B) |

Tickers use Yahoo symbols: `AAPL`, `MSFT`, `BTC-USD`, `^GSPC`, `EURUSD=X`.

## Installation

Requires Python 3.10+.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Running

The server speaks MCP over **stdio**:

```bash
yahoo-finance-mcp
# or
python -m yahoo_finance_mcp
```

### Claude Desktop / Claude Code config

Add to your MCP client config (e.g. `claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "yahoo-finance": {
      "command": "/absolute/path/to/yahoo-finance-mcp-server/.venv/bin/python",
      "args": ["-m", "yahoo_finance_mcp"]
    }
  }
}
```

Or, with Claude Code:

```bash
claude mcp add yahoo-finance -- /absolute/path/to/.venv/bin/python -m yahoo_finance_mcp
```

## Deployment (HTTP, port 8081)

For remote/agent access the server can run over **streamable HTTP** instead of
stdio. Transport and binding are controlled by env vars:

| Var | Default | Purpose |
| --- | --- | --- |
| `MCP_TRANSPORT` | `stdio` | `streamable-http` (or `sse`) to serve over HTTP |
| `MCP_HOST` | `0.0.0.0` | bind address |
| `MCP_PORT` | `8081` | listen port |

Run it directly:

```bash
MCP_TRANSPORT=streamable-http MCP_PORT=8081 python -m yahoo_finance_mcp
# endpoint: http://<host>:8081/mcp
```

### Automated deploy via SSH

`deploy/deploy.sh` ships the code to a remote host, installs it, and (re)starts
it on port 8081 with a health check. It authenticates over SSH using these
**GitHub repository secrets**:

| Secret | Meaning |
| --- | --- |
| `DEPLOY_SSH_HOST` | remote endpoint (host/IP) |
| `DEPLOY_SSH_PORT` | SSH port |
| `DEPLOY_SSH_USER` | SSH username |
| `DEPLOY_SSH_PASSWORD` | SSH password |

**Python is auto-provisioned.** The script needs Python ≥ 3.10. It first scans
the remote for any installed interpreter that qualifies (`python3.10`–`3.13`,
including `/usr/local/bin`). If none is found — e.g. on **Raspberry Pi OS
Bullseye**, which ships only Python 3.9.2 — it installs build deps and compiles
CPython (default `3.11.9`, override with `PYTHON_BUILD_VERSION`) to
`/usr/local` via `make altinstall`. This one-time bootstrap takes ~15–40 min on
a Pi; subsequent deploys detect the installed interpreter and skip it.

Building requires root: it works if the deploy user has passwordless sudo,
otherwise it falls back to `sudo -S` using `DEPLOY_SSH_PASSWORD`. You can still
set `PYTHON_BIN` to force a specific interpreter; it's used if it qualifies.

The `.github/workflows/deploy.yml` workflow runs the script on every push to
`main` (and on manual `workflow_dispatch`). To run it by hand:

```bash
DEPLOY_SSH_HOST=1.2.3.4 DEPLOY_SSH_PORT=22 \
DEPLOY_SSH_USER=pi DEPLOY_SSH_PASSWORD=secret \
PYTHON_BIN=python3.11 \
./deploy/deploy.sh
```

The script clones/updates the repo, builds a venv, stops any prior instance on
the port, starts the server detached (`setsid`, survives disconnect, logs to
`server.log`), then verifies `POST /mcp` returns `200`.

## Notes & limitations

- **Unofficial data source.** Yahoo may rate-limit or change responses. Tools
  return a structured `{"error": ...}` payload instead of crashing when a call
  fails, so the agent can react.
- **Intraday history is range-limited** by Yahoo (e.g. `1m` data only for the
  last few days).
- Data is provided for informational purposes; respect
  [Yahoo's Terms of Service](https://legal.yahoo.com/us/en/yahoo/terms/otos/index.html).
  Not investment advice.

## Development

```bash
pip install -e .
python -c "from yahoo_finance_mcp.server import mcp; print(len(mcp._tool_manager.list_tools()), 'tools')"
```
