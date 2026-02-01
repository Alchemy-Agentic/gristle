"""Utility helpers."""

import os
from typing import Any


class Config:
    """Application configuration loader."""

    def __init__(self, env: str = "development"):
        self.env = env
        self._data: dict[str, Any] = {}

    def load(self) -> None:
        """Load configuration from environment."""
        self._data = {
            "debug": os.getenv("DEBUG", "false").lower() == "true",
            "db_url": os.getenv("DATABASE_URL", "sqlite:///app.db"),
        }

    def get(self, key: str, default: Any = None) -> Any:
        """Get a configuration value."""
        return self._data.get(key, default)


def format_currency(amount: float) -> str:
    """Format a number as currency."""
    return f"${amount:,.2f}"
