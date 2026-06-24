"""Tests for the user service."""

from __future__ import annotations

from app.services import create_user, get_user


def test_create_and_get_user():
    user = create_user("ada@example.com", "Ada")
    fetched = get_user(user.id)
    assert fetched is not None
    assert fetched.email == "ada@example.com"
