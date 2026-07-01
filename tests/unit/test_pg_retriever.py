"""
PgFullTextRetriever 单元测试

不依赖真实 PostgreSQL 实例，使用 mock psycopg2。
验证：连接管理、降级逻辑、查询构造、索引写入。

psycopg2 mock 通过 tests/unit/conftest.py 的 autouse fixture 注入，
避免模块级 sys.modules 污染影响其他测试（修复 #3：测试隔离破坏）。
"""

from __future__ import annotations

from unittest import mock

import pytest

from qa.pipelines.components.pg_retriever import PgFullTextRetriever


class TestPgRetrieverInit:
    def test_default_config(self):
        r = PgFullTextRetriever()
        assert r._config["host"] == "localhost"
        assert r._config["port"] == 5432
        assert r._config["dbname"] == "qa"
        assert r.available is False  # not yet initialized

    def test_custom_config(self):
        r = PgFullTextRetriever(
            host="10.0.0.1", port=5433, dbname="test", user="admin", password="secret"
        )
        assert r._config["host"] == "10.0.0.1"
        assert r._config["password"] == "secret"


class TestPgRetrieverConnection:
    def test_init_pool_failure_graceful(self, mock_psycopg2):
        """连接失败不应抛出异常，available=False"""
        mock_psycopg2.pool.ThreadedConnectionPool.side_effect = Exception("connection refused")
        r = PgFullTextRetriever()
        result = r._init_pool()
        assert result is False
        assert r.available is False

    def test_init_pool_success(self, mock_psycopg2):
        """连接成功时 available=True"""
        mock_psycopg2.pool.ThreadedConnectionPool.side_effect = None
        r = PgFullTextRetriever()
        # mock connection and cursor
        mock_conn = mock.MagicMock()
        mock_cur = mock.MagicMock()
        mock_conn.cursor.return_value = mock_cur
        mock_psycopg2.pool.ThreadedConnectionPool.return_value.getconn.return_value = mock_conn

        result = r._init_pool()
        assert result is True
        assert r.available is True
        # should have created table
        assert mock_cur.execute.call_count >= 3  # CREATE EXTENSION, CREATE TABLE, CREATE INDEX

    def test_get_conn_returns_none_on_failure(self):
        """获取连接失败时返回 None"""
        with mock.patch.object(PgFullTextRetriever, "_init_pool", return_value=False):
            r = PgFullTextRetriever()
            conn = r._get_conn()
            assert conn is None


class TestPgRetrieverSearch:
    @pytest.fixture(autouse=True)
    def setup(self, mock_psycopg2):
        self.r = PgFullTextRetriever()
        self._mock_psycopg2 = mock_psycopg2
        # Mock successful connection
        self.mock_conn = mock.MagicMock()
        self.mock_cur = mock.MagicMock()
        self.mock_conn.cursor.return_value = self.mock_cur
        mock_psycopg2.pool.ThreadedConnectionPool.return_value.getconn.return_value = self.mock_conn
        self.r._init_pool()
        yield

    def test_search_no_results(self):
        """查询返回空列表"""
        self.mock_cur.fetchall.return_value = []
        results = self.r.search("不存在的查询", top_k=5)
        assert results == []

    def test_search_with_results(self):
        """正常返回结果"""
        self.mock_cur.fetchall.return_value = [
            ("id1", "内容1", "path1.pdf", 0.85),
            ("id2", "内容2", "path2.pdf", 0.62),
        ]
        results = self.r.search("测试查询", top_k=5)
        assert results is not None
        assert len(results) == 2
        assert results[0]["id"] == "id1"
        assert results[0]["score"] == 0.85

    def test_search_db_error_fallback(self):
        """数据库异常返回 None（触发 BM25 降级）"""
        self.mock_cur.execute.side_effect = Exception("DB error")
        results = self.r.search("查询", top_k=5)
        assert results is None  # 降级信号

    def test_search_empty_query(self):
        """空查询返回空列表"""
        results = self.r.search("", top_k=5)
        assert results == []
        results = self.r.search("   ", top_k=5)
        assert results == []


class TestPgRetrieverIndex:
    @pytest.fixture(autouse=True)
    def setup(self, mock_psycopg2):
        self.r = PgFullTextRetriever()
        self.mock_conn = mock.MagicMock()
        self.mock_cur = mock.MagicMock()
        self.mock_conn.cursor.return_value = self.mock_cur
        mock_psycopg2.pool.ThreadedConnectionPool.return_value.getconn.return_value = self.mock_conn
        self.r._init_pool()
        yield

    def test_index_documents(self):
        docs = [("id1", "内容1", "p1.pdf", "CSDC", "规则")]
        count = self.r.index_documents(docs)
        assert count == 1

    def test_index_documents_batch(self):
        docs = [(f"id{i}", f"内容{i}", f"p{i}.pdf", "src", "cat") for i in range(5)]
        count = self.r.index_documents(docs)
        assert count == 5

    def test_index_empty(self):
        count = self.r.index_documents([])
        assert count == 0

    def test_delete_documents(self):
        self.mock_cur.rowcount = 2
        count = self.r.delete_documents(["id1", "id2"])
        assert count == 2

    def test_count(self):
        self.mock_cur.fetchone.return_value = (42,)
        assert self.r.count() == 42

    def test_clear(self):
        self.mock_cur.rowcount = 10
        count = self.r.clear()
        assert count == 10


class TestPgRetrieverDegradation:
    """降级逻辑"""

    def test_unavailable_returns_none(self):
        """PG 不可用时 search 返回 None"""
        with mock.patch.object(PgFullTextRetriever, "_init_pool", return_value=False):
            r = PgFullTextRetriever()
            assert r.search("anything") is None

    def test_unavailable_index_returns_zero(self):
        with mock.patch.object(PgFullTextRetriever, "_init_pool", return_value=False):
            r = PgFullTextRetriever()
            assert r.index_documents([("id", "c", "p", "s", "cat")]) == 0

    def test_close(self):
        r = PgFullTextRetriever()
        r.close()  # should not raise


class TestTokenize:
    """分词工具"""

    def test_english_words(self):
        tokens = PgFullTextRetriever._tokenize_query("CCASS settlement time")
        assert "ccass" in tokens
        assert "settlement" in tokens
        assert "time" in tokens

    def test_chinese_characters(self):
        tokens = PgFullTextRetriever._tokenize_query("清算规则")
        assert "清" in tokens
        assert "算" in tokens
        assert "清算" in tokens
        assert "算规" in tokens
        assert "规则" in tokens

    def test_mixed(self):
        tokens = PgFullTextRetriever._tokenize_query("T+1 清算")
        assert "t" in tokens
        assert "1" in tokens
        assert "清" in tokens

    def test_empty(self):
        assert PgFullTextRetriever._tokenize_query("") == []
        assert PgFullTextRetriever._tokenize_query("   ") == []

    def test_max_tokens(self):
        """最多返回 20 个 token"""
        tokens = PgFullTextRetriever._tokenize_query(
            "a b c d e f g h i j k l m n o p q r s t u v w x y z " * 5
        )
        assert len(tokens) <= 20
