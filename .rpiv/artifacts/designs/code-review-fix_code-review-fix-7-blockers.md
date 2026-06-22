---
date: 2026-06-22T10:58:59+0800
author: rfiacne
commit: 95d5f30
branch: master
repository: qsMaster
topic: code-review-fix-7-blockers
tags: [code-review-fix, api-gateway, middleware, settings]
status: ready
parent: .rpiv/artifacts/reviews/code-review_commit-95d5f30.md
last_updated: 2026-06-22T10:58:59+0800
last_updated_by: rfiacne
---

# Design: Code Review Fix — 7 Blockers from 95d5f30

## Summary

Surgical bug fixes for 7 blockers identified in code review of commit 95d5f30. CSV `@field_validator` in `ServerConfig` resolves pydantic-settings env var parsing (Q1/Q2). Middleware gets constant-time API key comparison (S1) and operator precedence fix (Q5). Server gets missing auth on `review_stats` (S2) and CORS wildcard guard (S3). CI gets missing `pytest-timeout` dependency (Q3).

## Requirements

- Q1: docker-compose env var defaults must not crash pydantic-settings on startup
- Q2: deployment docs env format must match implementation (CSV works with validator)
- Q3: CI must not fail due to missing pytest-timeout plugin
- S1: API key comparison must use constant-time comparison
- S2: `review_stats` endpoint must have auth when api_keys configured
- Q5: Rate-limit key selection must not fall to "unknown" when api_key present
- S3: CORS must not allow credentials with wildcard origins

## Current State Analysis

### Key Discoveries

- `src/qa/config/settings.py:116-122` — existing `@model_validator(mode="after")` pattern for validation
- `src/qa/config/settings.py:17` — imports `Field, model_validator` from pydantic (need to add `field_validator`)
- `src/qa/api/middleware.py:72` — `api_key not in configured_keys` uses non-constant-time `==`
- `src/qa/api/middleware.py:148` — `api_key or request.client.host if request.client else "unknown"` parses as `(api_key or client.host) if client else "unknown"`
- `src/qa/api/server.py` — `review_stats` endpoint lacks `Depends(check_rate_limit)`
- `src/qa/api/server.py` — CORS `allow_credentials=True` hardcoded with no wildcard guard
- `requirements-dev.txt` — missing `pytest-timeout` (declared only in pyproject.toml optional deps)

## Scope

### Building

- Q1/Q2: CSV `@field_validator` for `ServerConfig.api_keys` and `allowed_origins`
- Q3: Add `pytest-timeout>=2.0.0` to `requirements-dev.txt`
- S1: Replace `in` with `secrets.compare_digest` in `middleware.py`
- S2: Add `Depends(check_rate_limit)` to `review_stats` in `server.py`
- Q5: Add parentheses to fix operator precedence in `middleware.py`
- S3: Conditionally disable `allow_credentials` when origins include `"*"`

### Not Building

- Q6: Rate limiter singleton stale config (suggestion — deferred)
- Q7/Q8: Missing tests for `check_rate_limit` and `log_request_middleware` (suggestion — deferred)
- Q9: README CI badge placeholder URL (suggestion — deferred)
- Q10: `list_answers` parameter order (suggestion — deferred)
- Q11: README duplicate pipeline stages (pre-existing — deferred)

## Decisions

### Q1/Q2: CSV validator approach

**Ambiguity**: pydantic-settings v2 parses `list[str]` env vars as JSON arrays. CSV format (comma-separated) fails with `SettingsError`. Two options: (A) add `@field_validator` that accepts CSV, (B) fix docs to show JSON format only.

**Decision**: Option A — CSV validator. User-friendly (CSV is natural for env vars), fixes both compose defaults and docs without changing docs. Modeled after existing `@model_validator(mode="after")` pattern at `settings.py:116`.

### S1: Constant-time comparison

**Decision**: Use `secrets.compare_digest` for each configured key. Standard practice for secret comparison.

### S2: review_stats auth

**Decision**: Add `_auth=Depends(check_rate_limit)` to `review_stats`, matching all sibling review endpoints.

### S3: CORS wildcard guard

**Decision**: Conditionally set `allow_credentials=not has_wildcard`. Log warning when wildcard detected. Prevents credential leakage without blocking the configuration.

### Q5: Operator precedence

**Decision**: Add parentheses: `api_key or (request.client.host if request.client else "unknown")`.

### Q3: CI dependency

**Decision**: Add `pytest-timeout>=2.0.0` to `requirements-dev.txt`. Simplest fix — CI already installs requirements-dev.txt.

## Architecture

### src/qa/config/settings.py — MODIFY

```python
# Line 17: add field_validator to import
from pydantic import Field, field_validator, model_validator

# In ServerConfig, after allowed_origins field (line ~247):

    @field_validator("api_keys", "allowed_origins", mode="before")
    @classmethod
    def parse_csv_or_json(cls, v):
        """Accept CSV (comma-separated) or JSON arrays for env vars

        pydantic-settings v2 defaults to JSON for list fields.
        This validator adds CSV fallback for user-friendly env vars:
            QA_SERVER_API_KEYS=sk-1,sk-2  →  ["sk-1", "sk-2"]
            QA_SERVER_API_KEYS='["a","b"]' →  ["a", "b"]
            QA_SERVER_API_KEYS=             →  []
        """
        if isinstance(v, str):
            v = v.strip()
            if not v:
                return []
            # Try JSON first (pydantic default format)
            try:
                import json
                parsed = json.loads(v)
                if isinstance(parsed, list):
                    return [str(item) for item in parsed]
            except (json.JSONDecodeError, ValueError):
                pass
            # Fall back to CSV
            return [item.strip() for item in v.split(",") if item.strip()]
        return v
```

### src/qa/api/middleware.py — MODIFY

```python
# Line 18: add secrets import
import logging
import secrets
import time

# Line 72 (S1): replace non-constant-time comparison
# OLD:
#     if not api_key or api_key not in configured_keys:
# NEW:
    if not api_key or not any(secrets.compare_digest(api_key, k) for k in configured_keys):

# Line 160 (Q5): fix operator precedence
# OLD:
#     key = api_key or request.client.host if request.client else "unknown"
# NEW:
    key = api_key or (request.client.host if request.client else "unknown")
```

### src/qa/api/server.py — MODIFY

```python
# Lines 47-54 (S3): replace hardcoded allow_credentials=True
# OLD:
# app.add_middleware(
#     CORSMiddleware,
#     allow_origins=_get_cors_origins(),
#     allow_credentials=True,
#     allow_methods=["*"],
#     allow_headers=["*", "X-API-Key"],
# )
# NEW:
_origins = _get_cors_origins()
_has_wildcard = "*" in _origins
if _has_wildcard:
    logger.warning("CORS: allowed_origins contains '*' — allow_credentials disabled for safety")

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=not _has_wildcard,
    allow_methods=["*"],
    allow_headers=["*", "X-API-Key"],
)

# Line 634 (S2): add auth to review_stats
# OLD:
# async def review_stats():
# NEW:
async def review_stats(_auth=Depends(check_rate_limit)):
```

### requirements-dev.txt — MODIFY

```python
# Add after pytest-asyncio line:
pytest-timeout>=2.0.0
```

## Slices

### Slice 1: ServerConfig CSV validator

**Files**: `src/qa/config/settings.py`

#### Automated Verification:

- [ ] `QA_SERVER_API_KEYS="sk-1,sk-2" python -c "from qa.config.settings import ServerConfig; c=ServerConfig(); assert c.api_keys == ['sk-1', 'sk-2']"` passes
- [ ] `QA_SERVER_API_KEYS="" python -c "from qa.config.settings import ServerConfig; c=ServerConfig(); assert c.api_keys == []"` passes
- [ ] `QA_SERVER_ALLOWED_ORIGINS="http://a.com,http://b.com" python -c "from qa.config.settings import ServerConfig; c=ServerConfig(); assert c.allowed_origins == ['http://a.com', 'http://b.com']"` passes
- [ ] Existing tests pass: `python -m pytest tests/unit/ -v --tb=short`

#### Manual Verification:

- [ ] docker-compose up boots without SettingsError when QA_SERVER_API_KEYS unset
- [ ] CSV format `sk-1,sk-2` in .env file works without JSON quoting

### Slice 2: Middleware security + logic fixes

**Files**: `src/qa/api/middleware.py`

#### Automated Verification:

- [ ] `python -c "import secrets; assert secrets.compare_digest('abc', 'abc')"` passes (stdlib check)
- [ ] `python -c "api_key='abc'; request_client=None; key = api_key or (request_client.host if request_client else 'unknown'); assert key == 'abc'"` passes
- [ ] Existing tests pass: `python -m pytest tests/unit/test_middleware.py -v --tb=short`

#### Manual Verification:

- [ ] API key comparison does not short-circuit on first byte mismatch (timing)
- [ ] Rate-limit key uses API key when request.client is None

### Slice 3: Server endpoint + CORS guard

**Files**: `src/qa/api/server.py`

#### Automated Verification:

- [ ] `grep -n 'review_stats' src/qa/api/server.py` shows `Depends(check_rate_limit)` on the function signature
- [ ] `grep -n 'allow_credentials' src/qa/api/server.py` shows `not _has_wildcard` (not hardcoded `True`)
- [ ] Existing tests pass: `python -m pytest tests/contract/test_review_api.py -v --tb=short`
- [ ] Server imports cleanly: `PYTHONPATH=src python -c "from qa.api.server import app; print('OK')"`

#### Manual Verification:

- [ ] `curl http://localhost:8001/api/v1/qa/reviews/stats` returns 401 when api_keys configured
- [ ] Setting `allowed_origins: ["*"]` in config logs warning and disables credentials

### Slice 4: CI dependency fix

**Files**: `requirements-dev.txt`

#### Automated Verification:

- [ ] `pip install -r requirements-dev.txt` succeeds without errors
- [ ] `pytest --timeout=60 --co tests/integration/` does not report unrecognized arguments
- [ ] Existing tests pass: `python -m pytest tests/ --timeout=15 -v --tb=short`

#### Manual Verification:

- [ ] CI workflow `.github/workflows/ci.yml` integration test step passes

## Desired End State

```bash
# CSV env vars work without JSON:
QA_SERVER_API_KEYS=sk-1,sk-2 python -m qa.api.server  # boots successfully
QA_SERVER_ALLOWED_ORIGINS=http://a.com,http://b.com python -m qa.api.server  # boots

# API key comparison is constant-time:
curl -H "X-API-Key: wrong" http://localhost:8001/api/v1/qa/ask  # 401, no timing leak

# review_stats requires auth:
curl http://localhost:8001/api/v1/qa/reviews/stats  # 401 when api_keys configured

# CORS wildcard disables credentials:
# config: allowed_origins: ["*"] → credentials=False + warning logged

# CI passes:
pytest tests/integration/ --timeout=60  # no "unrecognized arguments" error
```

## File Map

```
src/qa/config/settings.py     # MODIFY — add @field_validator for CSV env var parsing
src/qa/api/middleware.py      # MODIFY — secrets.compare_digest + operator precedence fix
src/qa/api/server.py          # MODIFY — review_stats auth + CORS wildcard guard
requirements-dev.txt          # MODIFY — add pytest-timeout>=2.0.0
```

## Ordering Constraints

- Slice 1 must come first (foundation — settings.py changes)
- Slice 2 depends on Slice 1 (middleware imports settings)
- Slice 3 depends on Slice 2 (server imports middleware)
- Slice 4 is independent (can run in parallel with any)

## Verification Notes

- `QA_SERVER_API_KEYS="sk-1,sk-2"` must parse to `["sk-1", "sk-2"]` without SettingsError
- `QA_SERVER_API_KEYS=""` must parse to `[]` (empty list, no auth) without SettingsError
- `QA_SERVER_ALLOWED_ORIGINS="http://a.com"` must parse to `["http://a.com"]` without SettingsError
- `secrets.compare_digest` used for all API key comparisons
- `review_stats` endpoint has `Depends(check_rate_limit)` parameter
- CORS `allow_credentials` is `False` when `"*"` in `allowed_origins`
- `pytest-timeout` in `requirements-dev.txt`
- Existing tests pass: `python -m pytest tests/unit/test_middleware.py tests/contract/test_review_api.py -v`

## Performance Considerations

- `secrets.compare_digest` adds negligible overhead (O(n) where n = key length, typically <100 chars)
- CSV validator runs once at startup (field_validator mode="before")
- No runtime performance impact

## Pattern References

- `src/qa/config/settings.py:116-122` — existing `@model_validator(mode="after")` pattern
- `src/qa/api/server.py:223` — existing `Depends(check_rate_limit)` pattern on endpoints

## Developer Context

- Q: "Q1+Q2: CSV validator or JSON docs?" → A: "CSV validator (Recommended)"
- Q: "Which findings to address?" → A: "Critical + important only (7 blockers)"

## Design History

- Slice 1: ServerConfig CSV validator — approved as generated
- Slice 2: Middleware security + logic fixes — approved as generated
- Slice 3: Server endpoint + CORS guard — approved as generated
- Slice 4: CI dependency fix — approved as generated

## References

- Review artifact: `.rpiv/artifacts/reviews/code-review_commit-95d5f30.md`
- Commit: `95d5f30`
