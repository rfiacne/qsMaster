"""
会话管理 API 契约测试 — /sessions CRUD

测试:
  - GET /sessions — 会话列表
  - POST /sessions — 创建会话
  - GET /sessions/{id} — 会话详情
  - DELETE /sessions/{id} — 删除会话
  - POST /sessions/{id}/clear — 清空对话历史
  - 404 处理 — 不存在的会话
"""

from __future__ import annotations


class TestListSessions:
    def test_empty_list(self, client):
        resp = client.get("/api/v1/qa/sessions")
        assert resp.status_code == 200
        data = resp.json()
        assert "sessions" in data
        assert isinstance(data["sessions"], list)

    def test_list_with_limit(self, client):
        resp = client.get("/api/v1/qa/sessions?limit=5")
        assert resp.status_code == 200


class TestCreateSession:
    def test_create_empty_body(self, client):
        resp = client.post("/api/v1/qa/sessions", json=None)
        assert resp.status_code == 200
        data = resp.json()
        assert "id" in data
        assert len(data["id"]) > 0

    def test_create_with_title(self, client):
        resp = client.post("/api/v1/qa/sessions", json={"title": "测试会话"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "测试会话"
        assert "created_at" in data


class TestGetSession:
    def test_get_existing_session(self, client):
        # 先创建
        create_resp = client.post("/api/v1/qa/sessions", json={"title": "查询测试"})
        session_id = create_resp.json()["id"]
        # 再查询
        resp = client.get(f"/api/v1/qa/sessions/{session_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == session_id
        assert "turns" in data
        assert isinstance(data["turns"], list)

    def test_get_nonexistent_session(self, client):
        resp = client.get("/api/v1/qa/sessions/nonexistent_id")
        assert resp.status_code == 404


class TestDeleteSession:
    def test_delete_existing_session(self, client):
        create_resp = client.post("/api/v1/qa/sessions", json={})
        session_id = create_resp.json()["id"]
        resp = client.delete(f"/api/v1/qa/sessions/{session_id}")
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True

    def test_delete_nonexistent_session(self, client):
        resp = client.delete("/api/v1/qa/sessions/nonexistent_id")
        assert resp.status_code == 404


class TestClearSession:
    def test_clear_existing_session(self, client):
        # 创建并添加对话
        create_resp = client.post("/api/v1/qa/sessions", json={"title": "清空测试"})
        session_id = create_resp.json()["id"]
        # 清空
        resp = client.post(f"/api/v1/qa/sessions/{session_id}/clear")
        assert resp.status_code == 200
        data = resp.json()
        assert data["cleared"] is True
        assert data["id"] == session_id

    def test_clear_nonexistent_session(self, client):
        resp = client.post("/api/v1/qa/sessions/nonexistent_id/clear")
        assert resp.status_code == 404
