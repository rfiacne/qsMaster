"""
文档上传 API 契约测试 — /upload

测试:
  - POST /upload — 正常上传（mock pipeline）
  - POST /upload — 缺少元数据返回 400
  - POST /upload — 重复文件跳过
  - POST /upload — 无新文件时返回空结果
"""

from __future__ import annotations

import io
from unittest import mock


class TestUploadEndpoint:
    def test_upload_missing_metadata(self, client):
        """缺少必需元数据返回 400"""
        file_content = b"test content"
        resp = client.post(
            "/api/v1/qa/upload",
            files=[("files", ("test.txt", io.BytesIO(file_content), "text/plain"))],
            data={"source": "", "category": "", "effective_date": ""},
        )
        assert resp.status_code == 400
        assert "元数据" in resp.json()["detail"]

    def test_upload_with_valid_metadata(self, client):
        """带完整元数据的上传（mock pipeline 和 store）"""
        with (
            mock.patch("qa.api.routes.upload.get_store") as mock_get_store,
            mock.patch("qa.api.routes.upload.get_index_pipeline") as mock_get_pipeline,
            mock.patch("qa.api.routes.upload.reset_runtime_singletons"),
        ):
            # Mock store
            mock_store = mock.MagicMock()
            mock_store.has_file_md5.return_value = False
            mock_get_store.return_value = mock_store

            # Mock pipeline result
            mock_result = mock.MagicMock()
            mock_result.files_count = 1
            mock_result.documents_written = 3
            mock_result.documents_skipped = 0
            mock_result.chunk_count = 5
            mock_result.parent_count = 2
            mock_result.errors = []
            mock_result.total_time_ms = 100

            mock_pipeline = mock.MagicMock()
            mock_pipeline.run.return_value = mock_result
            mock_get_pipeline.return_value = mock_pipeline

            file_content = b"test document content"
            resp = client.post(
                "/api/v1/qa/upload",
                files=[("files", ("test.txt", io.BytesIO(file_content), "text/plain"))],
                data={
                    "source": "CSDC",
                    "category": "clearing_rule",
                    "effective_date": "2026-06-01",
                },
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["files_count"] == 1
            assert data["documents_written"] == 3
            assert data["segments"] == 5
            assert data["parents"] == 2

    def test_upload_duplicate_file_skipped(self, client):
        """重复文件（MD5 匹配）被跳过"""
        with (
            mock.patch("qa.api.routes.upload.get_store") as mock_get_store,
        ):
            mock_store = mock.MagicMock()
            mock_store.has_file_md5.return_value = True  # 模拟重复
            mock_get_store.return_value = mock_store

            file_content = b"duplicate content"
            resp = client.post(
                "/api/v1/qa/upload",
                files=[("files", ("dup.txt", io.BytesIO(file_content), "text/plain"))],
                data={
                    "source": "CSDC",
                    "category": "rule",
                    "effective_date": "2026-01-01",
                },
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["files_count"] == 0
            assert data["documents_skipped"] == 1

    def test_upload_with_meta_json(self, client):
        """通过 meta JSON 字符串传递元数据"""
        import json

        with (
            mock.patch("qa.api.routes.upload.get_store") as mock_get_store,
            mock.patch("qa.api.routes.upload.get_index_pipeline") as mock_get_pipeline,
            mock.patch("qa.api.routes.upload.reset_runtime_singletons"),
        ):
            mock_store = mock.MagicMock()
            mock_store.has_file_md5.return_value = False
            mock_get_store.return_value = mock_store

            mock_result = mock.MagicMock()
            mock_result.files_count = 1
            mock_result.documents_written = 1
            mock_result.documents_skipped = 0
            mock_result.chunk_count = 1
            mock_result.parent_count = 1
            mock_result.errors = []
            mock_result.total_time_ms = 50
            mock_pipeline = mock.MagicMock()
            mock_pipeline.run.return_value = mock_result
            mock_get_pipeline.return_value = mock_pipeline

            file_content = b"meta json test"
            meta_json = json.dumps(
                {
                    "source": "SSE",
                    "category": "trading_rule",
                    "effective_date": "2026-06-01",
                }
            )
            resp = client.post(
                "/api/v1/qa/upload",
                files=[("files", ("test.txt", io.BytesIO(file_content), "text/plain"))],
                data={"meta": meta_json},
            )
            assert resp.status_code == 200
