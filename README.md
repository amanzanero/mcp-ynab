# YNAB MCP Server

An MCP server that connects AI assistants to your [YNAB](https://www.ynab.com/) budget. Ask your budget questions YNAB can't answer.

**[mcp-ynab.com](https://mcp-ynab.com)** — Full setup guide, troubleshooting, and more.

> This is a fork of [pragprogrammer/mcp-ynab](https://github.com/pragprogrammer/mcp-ynab) with an added SSE + OAuth mode for running the server as a hosted remote MCP endpoint (e.g. on Railway) instead of only via local stdio. See [Hosted Deployment](#hosted-deployment) below.

## Features

- **30+ tools** — budgets, accounts, transactions, categories, payees, months, scheduled transactions, and analytics
- **Delta sync** — only fetches what changed since the last call (uses YNAB's server knowledge)
- **4-tier caching** — TTL cache, delta sync, retry with backoff, SQLite persistence
- **Search & analytics** — text search across transactions, per-category spending breakdowns, Sankey flow data
- **Bulk operations** — create or update multiple transactions in a single call
- **Dollar amounts** — accepts dollars in parameters, converts to YNAB milliunits internally

## Quick Start

```
uv tool run mcp-ynab
```

Requires a [YNAB personal access token](https://app.ynab.com/settings/developer) set as `YNAB_API_KEY`.

## Configuration

### Claude Desktop / ChatGPT

Add to your config file:

```json
{
  "mcpServers": {
    "ynab": {
      "command": "uv",
      "args": ["tool", "run", "mcp-ynab"],
      "env": {
        "YNAB_API_KEY": "your-api-key-here"
      }
    }
  }
}
```

### Claude Code

```bash
claude mcp add-json ynab --scope user '{"type":"stdio","command":"uv","args":["tool","run","mcp-ynab"],"env":{"YNAB_API_KEY":"your-api-key-here"}}'
```

See [mcp-ynab.com](https://mcp-ynab.com) for config file locations and troubleshooting.

## Available Tools

| Group | Tools |
|-------|-------|
| **User** | `get_user` |
| **Plans** | `list_plans`, `get_plan`, `get_plan_settings` |
| **Accounts** | `list_accounts`, `get_account`, `create_account` |
| **Categories** | `list_categories`, `get_category`, `create_category`, `update_category`, `create_category_group`, `update_category_group`, `get_category_for_month`, `update_category_for_month` |
| **Payees** | `list_payees`, `get_payee`, `update_payee` |
| **Payee Locations** | `list_payee_locations`, `get_payee_location`, `get_payee_locations_by_payee` |
| **Months** | `list_months`, `get_month` |
| **Money Movements** | `list_money_movements`, `get_money_movements_for_month`, `list_money_movement_groups`, `get_money_movement_groups_for_month` |
| **Transactions** | `list_transactions`, `get_transaction`, `get_transactions_by_account`, `get_transactions_by_category`, `get_transactions_by_month`, `get_transactions_by_payee`, `search_transactions`, `create_transaction`, `create_transactions`, `update_transaction`, `update_transactions`, `delete_transaction`, `import_transactions` |
| **Scheduled** | `list_scheduled_transactions`, `get_scheduled_transaction`, `create_scheduled_transaction`, `update_scheduled_transaction`, `delete_scheduled_transaction` |
| **Analytics** | `get_money_flow`, `get_spending_by_category` |

### Field selection

Every tool that returns a model accepts an optional `exclude_fields` list. By
default each tool returns a sensible subset of fields to keep token usage low.
See [FIELDS.md](./FIELDS.md) for per-model defaults and override examples.

## Hosted Deployment

The server can also run over SSE as a remote MCP endpoint (e.g. deployed on [Railway](https://railway.app/) via the included `railway.toml`) instead of local stdio. This is a single-user setup — there's no multi-tenant account system.

```bash
# runs the SSE server on $PORT (default 8000) instead of stdio
uv run python -m src.server
```

Environment variables (in addition to `YNAB_API_KEY`):

| Variable | Purpose |
|----------|---------|
| `PORT` | Port to bind (Railway sets this automatically) |
| `MCP_AUTH_TOKEN` | Shared bearer token that gates the `/sse` endpoint. Unset = unauthenticated. |
| `MCP_OAUTH_ENABLED` | `true` to enable an OAuth 2.1 flow (metadata discovery, dynamic client registration, PKCE) for claude.ai's web/mobile "Custom Connector", which can't use a static bearer header. Requires `MCP_AUTH_TOKEN`; the same token doubles as the password on the one-field consent page. |
| `PUBLIC_URL` | Public base URL used as the OAuth issuer. Falls back to Railway's auto-injected `RAILWAY_PUBLIC_DOMAIN`, then `http://localhost:$PORT`. |

Notes:
- `/health` is always unauthenticated so platform healthchecks succeed even with auth enabled.
- Plain-bearer mode (`MCP_AUTH_TOKEN` set, `MCP_OAUTH_ENABLED` unset) is what Claude Code/Desktop's header-based MCP config expects.
- With OAuth enabled, registered clients and issued access/refresh tokens are persisted to the same SQLite cache DB (`cache_db_path`, see [config.py](./src/config.py)), so they survive redeploys as long as that file is on a persistent volume. Only the in-flight authorize→consent handoff and short-lived auth codes are in-memory, so a redeploy mid-authorization just means retrying the connection.

## Development

```bash
# Install dependencies
uv sync

# Run tests
uv run pytest

# Run server standalone
uv run python -m src.server
```

Requires `YNAB_API_KEY` in `.env.local` for running the server.

## License

[AGPL-3.0](LICENSE)
