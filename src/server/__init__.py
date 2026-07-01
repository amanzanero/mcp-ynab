import os
import secrets

import uvicorn
from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from src.server._shared import mcp

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


def main():
    mcp.settings.host = "0.0.0.0"
    mcp.settings.port = int(os.environ.get("PORT", 8000))
    # Behind Railway's proxy the Host header won't be localhost; DNS-rebinding
    # protection is redundant here since MCP_AUTH_TOKEN is the real access control.
    mcp.settings.transport_security = TransportSecuritySettings(enable_dns_rebinding_protection=False)

    app = mcp.sse_app()
    token = os.environ.get("MCP_AUTH_TOKEN")
    if token:
        app = _BearerAuthMiddleware(app, token)
    app = _HealthCheckMiddleware(app)

    uvicorn.run(app, host=mcp.settings.host, port=mcp.settings.port)
