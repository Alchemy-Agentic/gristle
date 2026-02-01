"""Data models for the sample application."""

from dataclasses import dataclass


@dataclass
class User:
    """Represents a user in the system."""

    id: int
    name: str
    email: str
    is_active: bool = True


@dataclass
class Order:
    """Represents a customer order."""

    id: int
    user_id: int
    total: float
    status: str = "pending"
