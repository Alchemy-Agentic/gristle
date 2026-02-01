"""Simple bearer token authentication for the Gristle MCP server."""

from __future__ import annotations

from mcp.server.auth.provider import AccessToken, TokenVerifier


class ApiKeyVerifier(TokenVerifier):
    """Validate bearer tokens against a static API key.

    Implements the ``TokenVerifier`` protocol expected by
    ``FastMCP(token_verifier=...)``.  When the incoming bearer token
    matches ``api_key``, the request is authorised; otherwise it is
    rejected with 401.
    """

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    async def verify_token(self, token: str) -> AccessToken | None:  # noqa: D401
        if token == self._api_key:
            return AccessToken(token=token, client_id="gristle-client", scopes=[])
        return None
