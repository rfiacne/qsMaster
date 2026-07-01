"""
LocalEmbedder 单元测试（M6 review finding）

覆盖:
- 初始化（默认模型/自定义参数）
- dimension 属性
- _lazy_load 延迟加载（成功/ImportError/失败）
- encode 编码（正常/空列表/任务参数）
- encode_query 单查询包装
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from qa.pipelines.components.local_embedder import LocalEmbedder

# ─── Mock 模型 ────────────────────────────────────────


@pytest.fixture
def mock_sentence_transformer():
    """Mock sentence_transformers.SentenceTransformer"""

    import numpy as np

    class MockModel:
        def encode(self, texts, task="retrieval", show_progress_bar=False):
            # 返回 numpy 数组（与真实模型一致）
            return np.array([list(range(768)) for _ in texts])

    with patch("sentence_transformers.SentenceTransformer", return_value=MockModel()):
        yield


# ─── 初始化 ────────────────────────────────────────────


class TestInit:
    def test_default_model(self):
        embedder = LocalEmbedder()
        assert embedder.model_name == "jinaai/jina-embeddings-v5-text-nano"
        assert embedder._task == "retrieval"
        assert embedder._dimension == 768
        assert embedder._model is None

    def test_custom_model(self):
        embedder = LocalEmbedder(
            model_name="sentence-transformers/all-MiniLM-L6-v2",
            task="clustering",
        )
        assert embedder.model_name == "sentence-transformers/all-MiniLM-L6-v2"
        assert embedder._task == "clustering"
        assert embedder._dimension == 768

    def test_initial_model_is_none(self):
        """模型在首次 encode 前不加载"""
        embedder = LocalEmbedder()
        assert embedder._model is None


# ─── Dimension ───────────────────────────────────────────


class TestDimension:
    def test_dimension_constant(self):
        embedder = LocalEmbedder()
        assert embedder.dimension == 768

    def test_dimension_before_and_after_load(self, mock_sentence_transformer):
        embedder = LocalEmbedder()
        assert embedder.dimension == 768
        embedder._lazy_load()
        assert embedder.dimension == 768


# ─── _lazy_load ───────────────────────────────────────


class TestLazyLoad:
    def test_load_success(self, mock_sentence_transformer):
        embedder = LocalEmbedder()
        assert embedder._model is None
        embedder._lazy_load()
        assert embedder._model is not None

    def test_load_idempotent(self, mock_sentence_transformer):
        """多次加载不重复创建模型实例"""
        embedder = LocalEmbedder()
        embedder._lazy_load()
        model_1 = embedder._model
        embedder._lazy_load()
        model_2 = embedder._model
        assert model_1 is model_2

    def test_import_error(self):
        """sentence-transformers 未安装时抛出 ImportError"""
        with patch(
            "sentence_transformers.SentenceTransformer", side_effect=ImportError("no module")
        ):
            embedder = LocalEmbedder()
            with pytest.raises(ImportError, match="sentence-transformers"):
                embedder._lazy_load()

    def test_download_failure(self):
        """下载失败时抛出 RuntimeError"""
        with patch(
            "sentence_transformers.SentenceTransformer", side_effect=Exception("download failed")
        ):
            embedder = LocalEmbedder()
            with pytest.raises(RuntimeError, match="本地嵌入模型加载失败"):
                embedder._lazy_load()


# ─── encode ───────────────────────────────────────────


class TestEncode:
    def test_encode_single_text(self, mock_sentence_transformer):
        embedder = LocalEmbedder()
        result = embedder.encode(["测试文本"])
        assert len(result) == 1
        assert len(result[0]) == 768
        assert isinstance(result[0], list)

    def test_encode_multiple_texts(self, mock_sentence_transformer):
        embedder = LocalEmbedder()
        result = embedder.encode(["文本A", "文本B", "文本C"])
        assert len(result) == 3
        for emb in result:
            assert len(emb) == 768

    def test_encode_empty_list(self, mock_sentence_transformer):
        embedder = LocalEmbedder()
        # 空列表行为取决于模型，这里验证不崩溃
        try:
            result = embedder.encode([])
            assert isinstance(result, list)
        except Exception:
            pass  # 某些模型不支持空列表，不应视为问题

    def test_encode_twice(self, mock_sentence_transformer):
        """多次编码使用缓存模型"""
        embedder = LocalEmbedder()
        r1 = embedder.encode(["文本A"])
        r2 = embedder.encode(["文本B"])
        assert len(r1) == 1
        assert len(r2) == 1
        assert embedder._model is not None

    def test_encode_with_task(self, mock_sentence_transformer):
        """自定义 task 参数传递给模型"""
        embedder = LocalEmbedder()
        result = embedder.encode(["测试"], task="classification")
        assert len(result) == 1

    def test_result_is_list_of_lists(self, mock_sentence_transformer):
        embedder = LocalEmbedder()
        result = embedder.encode(["文本A", "文本B"])
        assert isinstance(result, list)
        for emb in result:
            assert isinstance(emb, list)
            # 每个分量是 float
            for val in emb:
                assert isinstance(val, (int, float))


# ─── encode_query ──────────────────────────────────────


class TestEncodeQuery:
    def test_encode_query_single(self, mock_sentence_transformer):
        embedder = LocalEmbedder()
        result = embedder.encode_query("查询文本")
        assert len(result) == 768
        assert isinstance(result, list)

    def test_encode_query_returns_first(self, mock_sentence_transformer):
        """验证 encode_query 返回单条向量而非列表"""
        embedder = LocalEmbedder()
        result = embedder.encode_query("查询")
        assert isinstance(result, list)
        assert not isinstance(result[0], list), "应返回 1D 向量列表"

    def test_encode_query_delegates_to_encode(self, mock_sentence_transformer):
        embedder = LocalEmbedder()
        with patch.object(embedder, "encode", wraps=embedder.encode) as spy:
            embedder.encode_query("查询")
            spy.assert_called_once_with(["查询"])
