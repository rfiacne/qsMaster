"""
Unit test shared fixtures — auto-cleanup sys.modules after each test

Prevents test isolation bugs where module-level sys.modules pollution
in one test file breaks subsequent tests (e.g. haystack, psycopg2).
"""
from __future__ import annotations

import sys
from unittest import mock

import pytest


@pytest.fixture(autouse=True)
def mock_psycopg2(monkeypatch):
    """Mock psycopg2 for all unit tests — prevents DB connections and sys.modules pollution"""
    psycopg2_mock = mock.MagicMock()
    monkeypatch.setitem(sys.modules, "psycopg2", psycopg2_mock)
    monkeypatch.setitem(sys.modules, "psycopg2.pool", psycopg2_mock.pool)
    return psycopg2_mock


@pytest.fixture(autouse=True)
def mock_haystack(monkeypatch):
    """Mock haystack for unit tests — prevents sys.modules pollution"""
    haystack_mock = mock.MagicMock()
    monkeypatch.setitem(sys.modules, "haystack", haystack_mock)
    monkeypatch.setitem(sys.modules, "haystack.Document", haystack_mock.Document)
    return haystack_mock
