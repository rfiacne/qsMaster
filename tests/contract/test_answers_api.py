"""
标准答案 API 契约测试 — /answers CRUD + 别名

测试:
  - GET /answers — 列表 + 分页 + 筛选
  - POST /answers — 创建标准答案
  - DELETE /answers/{id} — 删除
  - PATCH /answers/{id}/status — 启用/禁用
  - POST /answers/{id}/aliases — 添加别名
  - 400/404 错误处理
"""

from __future__ import annotations


class TestListAnswers:
    def test_empty_list(self, client):
        resp = client.get("/api/v1/qa/answers")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["items"] == []

    def test_list_with_pagination(self, client):
        resp = client.get("/api/v1/qa/answers?page=1&page_size=10")
        assert resp.status_code == 200
        data = resp.json()
        assert "total" in data
        assert "page" in data
        assert "page_size" in data
        assert "items" in data


class TestAddAnswer:
    def test_add_valid_answer(self, client):
        resp = client.post(
            "/api/v1/qa/answers",
            json={"question": "什么是清算？", "answer": "清算是交易完成后的资金和证券划转过程"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "id" in data
        assert data["created"] is True
        assert data["question"] == "什么是清算？"

    def test_add_answer_with_category(self, client):
        resp = client.post(
            "/api/v1/qa/answers",
            json={
                "question": "T+1 是什么？",
                "answer": "交易日后第一个工作日完成交割",
                "category": "clearing_rule",
            },
        )
        assert resp.status_code == 200

    def test_add_answer_missing_question(self, client):
        resp = client.post(
            "/api/v1/qa/answers",
            json={"answer": "只有答案没有问题"},
        )
        assert resp.status_code == 400

    def test_add_answer_missing_answer(self, client):
        resp = client.post(
            "/api/v1/qa/answers",
            json={"question": "只有问题没有答案"},
        )
        assert resp.status_code == 400

    def test_add_answer_empty_strings(self, client):
        resp = client.post(
            "/api/v1/qa/answers",
            json={"question": "  ", "answer": "  "},
        )
        assert resp.status_code == 400


class TestDeleteAnswer:
    def test_delete_existing_answer(self, client):
        # 先创建
        add_resp = client.post(
            "/api/v1/qa/answers",
            json={"question": "待删除问题", "answer": "待删除答案"},
        )
        answer_id = add_resp.json()["id"]
        # 删除
        resp = client.delete(f"/api/v1/qa/answers/{answer_id}")
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True

    def test_delete_nonexistent_answer(self, client):
        resp = client.delete("/api/v1/qa/answers/nonexistent_id")
        assert resp.status_code == 404


class TestSetAnswerStatus:
    def test_disable_answer(self, client):
        add_resp = client.post(
            "/api/v1/qa/answers",
            json={"question": "状态测试", "answer": "状态答案"},
        )
        answer_id = add_resp.json()["id"]
        resp = client.patch(
            f"/api/v1/qa/answers/{answer_id}/status",
            json={"status": "disabled"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "disabled"

    def test_enable_answer(self, client):
        add_resp = client.post(
            "/api/v1/qa/answers",
            json={"question": "启用测试", "answer": "启用答案"},
        )
        answer_id = add_resp.json()["id"]
        # 先禁用
        client.patch(
            f"/api/v1/qa/answers/{answer_id}/status",
            json={"status": "disabled"},
        )
        # 再启用
        resp = client.patch(
            f"/api/v1/qa/answers/{answer_id}/status",
            json={"status": "enabled"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "enabled"

    def test_invalid_status_value(self, client):
        add_resp = client.post(
            "/api/v1/qa/answers",
            json={"question": "无效状态", "answer": "答案"},
        )
        answer_id = add_resp.json()["id"]
        resp = client.patch(
            f"/api/v1/qa/answers/{answer_id}/status",
            json={"status": "invalid"},
        )
        assert resp.status_code == 400

    def test_set_status_nonexistent(self, client):
        resp = client.patch(
            "/api/v1/qa/answers/nonexistent_id/status",
            json={"status": "disabled"},
        )
        assert resp.status_code == 404


class TestAddAlias:
    def test_add_alias_to_existing(self, client):
        add_resp = client.post(
            "/api/v1/qa/answers",
            json={"question": "原始问题", "answer": "原始答案"},
        )
        answer_id = add_resp.json()["id"]
        resp = client.post(
            f"/api/v1/qa/answers/{answer_id}/aliases",
            json={"alias_question": "别名问题"},
        )
        assert resp.status_code == 200
        assert resp.json()["alias_question"] == "别名问题"

    def test_add_alias_empty_question(self, client):
        add_resp = client.post(
            "/api/v1/qa/answers",
            json={"question": "原始问题", "answer": "原始答案"},
        )
        answer_id = add_resp.json()["id"]
        resp = client.post(
            f"/api/v1/qa/answers/{answer_id}/aliases",
            json={"alias_question": ""},
        )
        assert resp.status_code == 400

    def test_add_alias_nonexistent(self, client):
        resp = client.post(
            "/api/v1/qa/answers/nonexistent_id/aliases",
            json={"alias_question": "别名"},
        )
        assert resp.status_code == 404
