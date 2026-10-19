"""Shared pytest configuration."""

from __future__ import annotations

import shutil

import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "requires_mmseqs: needs the mmseqs2 binary on PATH (homology gates)",
    )


@pytest.fixture(scope="session")
def mmseqs_available() -> bool:
    return shutil.which("mmseqs") is not None


@pytest.fixture()
def require_mmseqs(mmseqs_available: bool) -> None:
    if not mmseqs_available:
        pytest.skip("mmseqs2 binary not on PATH")
