import os
import secrets
import sys

import uvicorn
from mcp.server.auth.provider import ProviderTokenVerifier
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import AnyHttpUrl
from starlette.requests import Request
from starlette.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from src.server._shared import mcp
from src.server.oauth import CONSENT_FORM_HTML, YnabOAuthProvider

# Import domain modules to trigger @mcp.tool() registration
from src.server.user import get_user
from src.server.plans import list_plans, get_plan, get_plan_settings
from src.server.accounts import list_accounts, create_account, get_account
from src.server.transactions import (
    list_transactions, get_transaction, get_transactions_by_account,
    get_transactions_by_category, get_transactions_by_month, get_transactions_by_payee,
    search_transactions, create_transaction, create_transactions,
    update_transaction, delete_transaction, update_transactions,
    import_transactions,
)
from src.server.categories import (
    list_categories, get_category, create_category, update_category,
    create_category_group, update_category_group,
    get_category_for_month, update_category_for_month,
)
from src.server.payees import list_payees, get_payee, update_payee
from src.server.payee_locations import (
    list_payee_locations, get_payee_location, get_payee_locations_by_payee,
)
from src.server.money_movements import (
    list_money_movements, get_money_movements_for_month,
    list_money_movement_groups, get_money_movement_groups_for_month,
)
from src.server.months import list_months, get_month
from src.server.scheduled import (
    list_scheduled_transactions, get_scheduled_transaction,
    create_scheduled_transaction, update_scheduled_transaction,
    delete_scheduled_transaction,
)
from src.server.analytics import get_money_flow, get_spending_by_category

__all__ = [
    "get_user",
    "list_plans", "get_plan", "get_plan_settings",
    "list_accounts", "create_account", "get_account",
    "list_transactions", "get_transaction", "get_transactions_by_account",
    "get_transactions_by_category", "get_transactions_by_month", "get_transactions_by_payee",
    "search_transactions", "create_transaction", "create_transactions",
    "update_transaction", "delete_transaction", "update_transactions",
    "import_transactions",
    "list_categories", "get_category", "create_category", "update_category",
    "create_category_group", "update_category_group",
    "get_category_for_month", "update_category_for_month",
    "list_payees", "get_payee", "update_payee",
    "list_payee_locations", "get_payee_location", "get_payee_locations_by_payee",
    "list_money_movements", "get_money_movements_for_month",
    "list_money_movement_groups", "get_money_movement_groups_for_month",
    "list_months", "get_month",
    "list_scheduled_transactions", "get_scheduled_transaction",
    "create_scheduled_transaction", "update_scheduled_transaction",
    "delete_scheduled_transaction",
    "get_money_flow", "get_spending_by_category",
    "main",
]


class _BearerAuthMiddleware:
    """Gate every request behind a static bearer token (single-user deployments)."""

    def __init__(self, app: ASGIApp, token: str) -> None:
        self.app = app
        self.expected = f"Bearer {token}"

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope["headers"])
        authorization = headers.get(b"authorization", b"").decode()
        if not secrets.compare_digest(authorization, self.expected):
            await PlainTextResponse("Unauthorized", status_code=401)(scope, receive, send)
            return
        await self.app(scope, receive, send)


class _HealthCheckMiddleware:
    """Answer Railway's unauthenticated healthcheck without exposing the real MCP endpoint."""

    def __init__(self, app: ASGIApp, path: str = "/health") -> None:
        self.app = app
        self.path = path

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["path"] == self.path:
            await PlainTextResponse("ok")(scope, receive, send)
            return
        await self.app(scope, receive, send)


def _public_url(port: int) -> str:
    explicit = os.environ.get("PUBLIC_URL")
    if explicit:
        return explicit.rstrip("/")
    domain = os.environ.get("RAILWAY_PUBLIC_DOMAIN")
    if domain:
        return f"https://{domain}"
    return f"http://localhost:{port}"


def _configure_oauth(token: str, port: int) -> None:
    """Wire a minimal single-user OAuth server into `mcp` so claude.ai's web
    connector (which requires OAuth, unlike Claude Code's header-based config)
    can authorize. Gated by the same MCP_AUTH_TOKEN used for plain-bearer mode."""
    issuer = AnyHttpUrl(_public_url(port))
    provider = YnabOAuthProvider(password=token)

    mcp._auth_server_provider = provider
    mcp._token_verifier = ProviderTokenVerifier(provider)
    mcp.settings.auth = AuthSettings(
        issuer_url=issuer,
        resource_server_url=issuer,
        client_registration_options=ClientRegistrationOptions(enabled=True),
        revocation_options=RevocationOptions(enabled=True),
    )

    async def consent(request: Request) -> Response:
        if request.method == "GET":
            nonce = request.query_params.get("nonce", "")
            return HTMLResponse(CONSENT_FORM_HTML.format(nonce=nonce, error=""))

        form = await request.form()
        nonce = str(form.get("nonce", ""))
        password = str(form.get("password", ""))
        if not secrets.compare_digest(password, provider.password):
            return HTMLResponse(
                CONSENT_FORM_HTML.format(nonce=nonce, error="<p style='color:red'>Incorrect token.</p>"),
                status_code=401,
            )

        redirect_url = provider.complete_consent(nonce)
        if redirect_url is None:
            return PlainTextResponse("Authorization request expired. Please reconnect from your client.", status_code=400)
        return RedirectResponse(redirect_url, status_code=302)

    mcp.custom_route("/consent", methods=["GET", "POST"])(consent)


def main():
    mcp.settings.host = "0.0.0.0"
    mcp.settings.port = int(os.environ.get("PORT", 8000))
    # Behind Railway's proxy the Host header won't be localhost; DNS-rebinding
    # protection is redundant here since MCP_AUTH_TOKEN is the real access control.
    mcp.settings.transport_security = TransportSecuritySettings(enable_dns_rebinding_protection=False)

    token = os.environ.get("MCP_AUTH_TOKEN")
    oauth_enabled = os.environ.get("MCP_OAUTH_ENABLED", "").lower() in ("1", "true", "yes")
    if oauth_enabled:
        if not token:
            print("ERROR: MCP_OAUTH_ENABLED requires MCP_AUTH_TOKEN to be set", file=sys.stderr)
            sys.exit(1)
        _configure_oauth(token, mcp.settings.port)

    app = mcp.sse_app()
    if token and not oauth_enabled:
        app = _BearerAuthMiddleware(app, token)
    app = _HealthCheckMiddleware(app)

    uvicorn.run(app, host=mcp.settings.host, port=mcp.settings.port)
