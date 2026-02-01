"""Shared test fixtures."""

from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"
SAMPLE_PYTHON_DIR = FIXTURES_DIR / "sample_python"


@pytest.fixture
def sample_python_dir() -> Path:
    return SAMPLE_PYTHON_DIR


@pytest.fixture
def sample_services_code() -> str:
    return (SAMPLE_PYTHON_DIR / "services.py").read_text()


@pytest.fixture
def sample_models_code() -> str:
    return (SAMPLE_PYTHON_DIR / "models.py").read_text()


@pytest.fixture
def sample_utils_code() -> str:
    return (SAMPLE_PYTHON_DIR / "utils.py").read_text()
