"""Domain models for the sample app."""

from __future__ import annotations

from pydantic import BaseModel


class User(BaseModel):
    """A registered user."""

    id: int
    email: str
    name: str | None = None
