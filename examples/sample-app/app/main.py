"""FastAPI entry point for the sample app."""

from __future__ import annotations

from fastapi import FastAPI

from app.models import User
from app.services import create_user, get_user

app = FastAPI()


@app.post("/users")
def register(email: str, name: str | None = None) -> User:
    """Register a new user."""
    return create_user(email, name)


@app.get("/users/{user_id}")
def read_user(user_id: int) -> User | None:
    """Fetch a user by id."""
    return get_user(user_id)
