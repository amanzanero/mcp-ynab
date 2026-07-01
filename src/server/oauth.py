"""Minimal single-user OAuth 2.1 authorization server.

Satisfies the MCP remote-auth spec (metadata discovery, dynamic client
registration, PKCE) well enough for claude.ai's web connector, without any
real account system: "login" is just entering the shared MCP_AUTH_TOKEN on a
one-field consent page. All state is in-memory — a redeploy just means
reauthorizing, which is fine for a single-user deployment.
"""

import secrets
import time

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

AUTH_CODE_TTL_SECONDS = 300
ACCESS_TOKEN_TTL_SECONDS = 3600


class YnabOAuthProvider(OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]):
    def __init__(self, password: str) -> None:
        self.password = password
        self.clients: dict[str, OAuthClientInformationFull] = {}
        self.pending: dict[str, tuple[OAuthClientInformationFull, AuthorizationParams]] = {}
        self.auth_codes: dict[str, AuthorizationCode] = {}
        self.access_tokens: dict[str, AccessToken] = {}
        self.refresh_tokens: dict[str, RefreshToken] = {}

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return self.clients.get(client_id)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        assert client_info.client_id is not None
        self.clients[client_info.client_id] = client_info

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        nonce = secrets.token_urlsafe(32)
        self.pending[nonce] = (client, params)
        return f"/consent?nonce={nonce}"

    def complete_consent(self, nonce: str) -> str | None:
        """Called by the /consent POST handler once the password checks out.

        Returns the client's redirect URL (with the auth code attached), or
        None if the nonce is unknown/already used.
        """
        pending = self.pending.pop(nonce, None)
        if pending is None:
            return None
        client, params = pending
        assert client.client_id is not None
        code = secrets.token_urlsafe(32)
        self.auth_codes[code] = AuthorizationCode(
            code=code,
            scopes=params.scopes or [],
            expires_at=time.time() + AUTH_CODE_TTL_SECONDS,
            client_id=client.client_id,
            code_challenge=params.code_challenge,
            redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            resource=params.resource,
        )
        return construct_redirect_uri(str(params.redirect_uri), code=code, state=params.state)

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        return self.auth_codes.get(authorization_code)

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        del self.auth_codes[authorization_code.code]
        return self._issue_tokens(authorization_code.client_id, authorization_code.scopes)

    async def load_refresh_token(self, client: OAuthClientInformationFull, refresh_token: str) -> RefreshToken | None:
        token = self.refresh_tokens.get(refresh_token)
        if token is None or token.client_id != client.client_id:
            return None
        return token

    async def exchange_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: RefreshToken, scopes: list[str]
    ) -> OAuthToken:
        del self.refresh_tokens[refresh_token.token]
        return self._issue_tokens(refresh_token.client_id, scopes or refresh_token.scopes)

    async def load_access_token(self, token: str) -> AccessToken | None:
        access_token = self.access_tokens.get(token)
        if access_token is None:
            return None
        if access_token.expires_at and access_token.expires_at < time.time():
            del self.access_tokens[token]
            return None
        return access_token

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        self.access_tokens.pop(token.token, None)
        self.refresh_tokens.pop(token.token, None)

    def _issue_tokens(self, client_id: str, scopes: list[str]) -> OAuthToken:
        access = secrets.token_urlsafe(32)
        refresh = secrets.token_urlsafe(32)
        expires_at = int(time.time()) + ACCESS_TOKEN_TTL_SECONDS
        self.access_tokens[access] = AccessToken(token=access, client_id=client_id, scopes=scopes, expires_at=expires_at)
        self.refresh_tokens[refresh] = RefreshToken(token=refresh, client_id=client_id, scopes=scopes)
        return OAuthToken(
            access_token=access,
            refresh_token=refresh,
            expires_in=ACCESS_TOKEN_TTL_SECONDS,
            scope=" ".join(scopes) if scopes else None,
        )


CONSENT_FORM_HTML = """\
<!doctype html>
<html>
<head><title>Connect to YNAB MCP server</title></head>
<body style="font-family: sans-serif; max-width: 24rem; margin: 4rem auto;">
  <h2>Authorize access</h2>
  <p>Enter the server's access token to connect this client to your YNAB data.</p>
  {error}
  <form method="post" action="/consent">
    <input type="hidden" name="nonce" value="{nonce}">
    <input type="password" name="password" placeholder="Access token" autofocus
           style="width: 100%; padding: 0.5rem; font-size: 1rem;">
    <button type="submit" style="margin-top: 1rem; padding: 0.5rem 1rem;">Authorize</button>
  </form>
</body>
</html>
"""
