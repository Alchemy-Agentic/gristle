"""Business logic for the sample app."""

from __future__ import annotations

from app.models import User

_USERS: dict[int, User] = {}


def create_user(email: str, name: str | None = None) -> User:
    """Create and store a new user."""
    user_id = len(_USERS) + 1
    user = User(id=user_id, email=email, name=name)
    _USERS[user_id] = user
    return user


def get_user(user_id: int) -> User | None:
    """Look up a user by id."""
    return _USERS.get(user_id)
