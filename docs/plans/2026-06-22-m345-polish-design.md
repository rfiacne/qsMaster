# M3/M4/M5 Full Polish Design

**Date**: 2026-06-22
**Status**: Approved
**Approach**: C (Full Polish)

## Current State Summary

| Component | Status | What's missing |
|-----------|--------|---------------|
| M3 Review feedback | 95% done | Integration test for auto_convert loop |
| M4 OTel tracing | Wired into pipeline | API auth, rate limit, request logging |
| M5 Frontend | Multi-page SPA (2007 lines) | CORS hardening, Docker, CI, deployment docs |

## Phase 1: M3 — Review Feedback Verification

**Goal**: Verify and test the review→standard answer→early_exit retrieval loop end-to-end.

**Changes**:
- Add integration test: `tests/integration/test_review_feedback_loop.py`
  - Create review item → label "correct" with auto_convert → verify standard answer store has new entry → verify early_exit match finds it
- Add API contract test: `tests/contract/test_review_api.py`
  - POST `/qa/reviews/{id}/label` → verify response + side effects

**Files**: 2 new test files, ~150 lines

## Phase 2: M4 — API Gateway (Auth + Rate Limit + Logging)

**Goal**: Production-ready API with authentication, rate limiting, and request audit logging.

### 2.1 API Key Auth

- Config: `server.api_keys: list[str]` in settings (env var `QA_SERVER_API_KEYS`, comma-separated)
- Header: `X-API-Key` required for all `/api/v1/*` routes
- Bypass: `/health`, `/api/v1/qa/status` (read-only safe)
- FastAPI `Depends()` pattern, not global middleware (granular)

### 2.2 Rate Limiting

- Config: `server.rate_limit_rpm: int = 60` (requests per minute per API key)
- In-memory sliding window (no Redis dependency for single-instance deployment)
- Response headers: `X-RateLimit-Limit`, `X-RateLimit-Remaining`
- 429 response when exceeded

### 2.3 Request Logging

- Log every API request: method, path, API key (masked), latency, status code
- Uses existing `audit_logger` if available

### 2.4 Config Additions

```yaml
server:
  api_keys: []              # API 密钥列表（空=不鉴权，向后兼容）
  rate_limit_rpm: 60        # 每分钟请求限制（per API key）
  allowed_origins:          # CORS 允许的来源
    - "http://localhost:8001"
```

### 2.5 Files

- New: `src/qa/api/middleware.py` (~140 lines)
- Modified: `src/qa/api/server.py` (wire Depends + CORS from config)
- Modified: `src/qa/config/settings.py` (add ServerConfig fields)
- New: `tests/unit/test_middleware.py` (~100 lines)

## Phase 3: M5 — Deployment + Hardening

### 3.1 CORS Hardening

- Replace `allow_origins=["*"]` with `settings.server.allowed_origins`
- Default: `["http://localhost:8001"]` for dev, configurable for production

### 3.2 Dockerfile

- Multi-stage: builder (uv pip install) → runtime (python-slim)
- Expose 8001, health check via `/api/v1/qa/health`, non-root user
- ~30 lines

### 3.3 docker-compose.yml

- Service: qa-agent (build ., port 8001, volume for data/)
- ~25 lines

### 3.4 GitHub Actions CI

- Triggers: push to master, PR
- Steps: checkout → setup python → install deps → ruff → mypy → pytest
- Badge in README
- ~50 lines

### 3.5 Deployment Docs

- `docs/deployment.md`: Docker setup, env vars, production checklist
- ~80 lines

### 3.6 Files

- New: `Dockerfile`, `docker-compose.yml`, `.github/workflows/ci.yml`, `docs/deployment.md`
- Modified: `src/qa/api/server.py` (CORS), `README.md` (CI badge)

## Total Estimate

| Phase | Files | Lines | Risk |
|-------|-------|-------|------|
| M3 Tests | 2 new | ~150 | Low |
| M4 API Gateway | 1 new + 2 mod + 1 test | ~240 | Medium (auth bypass edge cases) |
| M5 Deployment | 4 new + 2 mod | ~250 | Low |
| **Total** | **~12 files** | **~640 lines** | |

## Backward Compatibility

- `server.api_keys: []` (empty) = no auth, existing deployments unaffected
- `server.allowed_origins` defaults include localhost for dev
- Rate limit only activates when `rate_limit_rpm > 0`
- All new config fields have safe defaults
