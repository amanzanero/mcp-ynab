# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

**Landing Page (Astro):**
- `cd website && npm run dev` — Start dev server
- `cd website && npm run build` — Production build

**Python MCP Server:**
- `uv run pytest` — Run all tests
- `uv run pytest tests/test_foo.py::test_name` — Run a single test
- `uv run python -m src.server` — Run MCP server (SSE transport; see below)

**Environment:** Requires `YNAB_API_KEY` in `.env.local`

## Architecture

This repo contains two things:
1. **A Python MCP server** (`src/`) that connects AI assistants to the YNAB API
2. **A landing page** (`website/`) built with Astro + Tailwind, deployed to Vercel

This is a fork of [pragprogrammer/mcp-ynab](https://github.com/pragprogrammer/mcp-ynab). The fork's changes are all in `main()`/deployment: SSE transport, bearer-token and OAuth auth, and OAuth state persistence, to support running the server as a hosted remote MCP endpoint (Railway) rather than only stdio.

### Python MCP server (`src/`)

- **`server/`** — FastMCP server package, run standalone via `python -m src.server`. Tool definitions are split by domain across submodules (`accounts.py`, `transactions.py`, `categories.py`, etc.) and registered through `src/server/__init__.py`. Shared infrastructure (`mcp`, `cache`, `client`, `handle_errors`, `serialize` helpers, `DEFAULT_EXCLUDES`) lives in `src/server/_shared.py`. All tools use the `@handle_errors` decorator for uniform YNAB/HTTP error handling and lazy DB init.
  - `main()` runs the server over SSE (not stdio) via `mcp.sse_app()` + uvicorn, for hosted deployment (see `railway.toml`). `MCP_AUTH_TOKEN` gates `/sse` with a static bearer token; `MCP_OAUTH_ENABLED=true` swaps that for a minimal single-user OAuth 2.1 flow (`src/server/oauth.py`) for claude.ai's web connector, with `/consent` as the one-field login page. `/health` always bypasses auth for platform healthchecks.
- **`ynab_client.py`** — Async httpx client for YNAB API v1.
- **`cache/`** — 4-tier caching: TTL-based response cache, delta sync (server knowledge tracking), retry with exponential backoff, SQLite persistence.
- **`db/tables.py`** — SQLite tables via the cache layer's engine, including `oauth_client`/`oauth_access_token`/`oauth_refresh_token` for persisting OAuth state (registered clients, access/refresh tokens) across redeploys. In-flight authorize→consent handoffs and short-lived auth codes stay in-memory only.
- **`models/`** — Pydantic models, each module exports a `*_DEFAULT_EXCLUDE` set defining the fields hidden from MCP responses by default. The registry in `src/server/_shared.py` (`DEFAULT_EXCLUDES`) maps model classes to their default exclude sets. Every MCP tool accepts an optional `exclude_fields: list[str]` param that, when provided, fully replaces the default. See `FIELDS.md` for the per-model field reference.
- **`config.py`** — `Settings` via pydantic-settings. Cache DB path is platform-specific (`~/Library/Application Support/ynab-mcp-server/cache.db` on macOS); the same DB file backs OAuth persistence.

### Key conventions

- **Milliunits:** YNAB stores money as milliunits (1000 = $1.00). The Python server accepts dollars in tool parameters and converts to milliunits internally.
- **Month format:** YNAB months use first-of-month dates (`2026-03-01` for March 2026).
