"""Tests for gristle.mcp.auth — ApiKeyVerifier."""

from __future__ import annotations

import pytest

from gristle.mcp.auth import ApiKeyVerifier


@pytest.fixture
def verifier():
    return ApiKeyVerifier(api_key="test-secret-key")


class TestApiKeyVerifier:
    @pytest.mark.asyncio
    async def test_valid_token_returns_access_token(self, verifier):
        result = await verifier.verify_token("test-secret-key")
        assert result is not None
        assert result.token == "test-secret-key"
        assert result.client_id == "gristle-client"
        assert result.scopes == []

    @pytest.mark.asyncio
    async def test_invalid_token_returns_none(self, verifier):
        result = await verifier.verify_token("wrong-key")
        assert result is None

    @pytest.mark.asyncio
    async def test_empty_token_returns_none(self, verifier):
        result = await verifier.verify_token("")
        assert result is None

    @pytest.mark.asyncio
    async def test_different_api_key(self):
        v = ApiKeyVerifier(api_key="other-key")
        assert await v.verify_token("other-key") is not None
        assert await v.verify_token("test-secret-key") is None

    @pytest.mark.asyncio
    async def test_token_must_match_exactly(self, verifier):
        # Partial match should fail
        assert await verifier.verify_token("test-secret-key ") is None
        assert await verifier.verify_token(" test-secret-key") is None
        assert await verifier.verify_token("TEST-SECRET-KEY") is None
