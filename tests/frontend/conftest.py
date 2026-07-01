"""
Frontend testing configuration using pytest-playwright.
"""

import pytest
from playwright.sync_api import Page


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args):
    """Configure browser context for testing."""
    return {
        **browser_context_args,
        "viewport": {
            "width": 1920,
            "height": 1080,
        },
        "device_scale_factor": 1,
    }


@pytest.fixture
def authenticated_page(page: Page, base_url: str) -> Page:
    """
    Navigate to the application and wait for it to load.
    This fixture assumes the backend server is running.
    """
    page.goto(base_url)
    page.wait_for_load_state("networkidle")
    return page


@pytest.fixture(scope="session")
def base_url() -> str:
    """Base URL for the application."""
    return "http://localhost:8001"
