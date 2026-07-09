"""
集成测试 — 使用 G:/qsMaster/testfile 下的真实文档验证全链路

测试覆盖：
1. 文档加载（PDF/DOCX/XLSX 多格式解析）
2. 分层文档分割（HierarchicalDocumentSplitter）
3. 元数据验证（必需字段检查）
4. 检索 + 排序 + 去重
5. 混合检索的来源标注
6. Early Exit 精确匹配

注意：需要 haystack-ai 和 docling 等依赖包。
     嵌入和 LLM 生成需要远程 API，不在本测试中执行。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

# 确保 src 在路径中
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

# ─── 测试文档路径 ──────────────────────────────────

TESTFILE_DIR = Path("G:/qsMaster/testfile")

PDF_SAMPLE = TESTFILE_DIR / (  # noqa: E501
    "20260527--测试文1：中国结算深业〔2026〕20 号"
    "关于2026年5月30日开展深市交易结算系统全网测试的通知.pdf"
)
DOCX_SAMPLE = TESTFILE_DIR / "证券经纪服务协议-北京信汇泉私募基金管理有限公司-金元证券公司 (1).docx"
XLSX_SAMPLE = TESTFILE_DIR / "新增或修改的配置参数_新增20260610.xlsx"

REQUIRED_META = {
    "source": "CSDC",
    "category": "clearing_rule",
    "effective_date": "2026-06-01",
}


# ═══════════════════════════════════════════════════════════════
# 1. 文档加载测试
# ═══════════════════════════════════════════════════════════════


class TestDocumentLoading:
    """多格式文档解析"""

    @pytest.mark.skipif(not PDF_SAMPLE.exists(), reason="测试 PDF 文件不存在")
    @pytest.mark.timeout(60)
    def test_pdf_parsing(self):
        """PDF 文件应被成功解析为文本"""
        from haystack.components.converters import PyPDFToDocument

        converter = PyPDFToDocument()
        result = converter.run(sources=[str(PDF_SAMPLE)])
        docs = result["documents"]
        assert len(docs) >= 1
        text = docs[0].content or ""
        assert len(text) > 100, f"PDF 解析文本过短: {len(text)} 字符"
        # 验证关键内容存在
        assert any(kw in text for kw in ["全网测试", "结算系统", "深市"]), (
            "PDF 内容中未找到预期关键词"
        )

    @pytest.mark.skipif(not DOCX_SAMPLE.exists(), reason="测试 DOCX 文件不存在")
    def test_docx_parsing(self):
        """DOCX 文件应被成功解析"""
        import docx

        doc = docx.Document(str(DOCX_SAMPLE))
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        text = "\n".join(paragraphs)
        assert len(text) > 100, f"DOCX 解析文本过短: {len(text)} 字符"
        # 验证合同类文档的关键内容
        assert any(kw in text for kw in ["协议", "证券", "经纪", "基金"]), (
            "DOCX 内容中未找到预期关键词"
        )

    @pytest.mark.skipif(not XLSX_SAMPLE.exists(), reason="测试 XLSX 文件不存在")
    def test_xlsx_parsing(self):
        """XLSX 文件应被成功解析为表格文本"""
        from haystack.components.converters import XLSXToDocument

        converter = XLSXToDocument()
        result = converter.run(sources=[str(XLSX_SAMPLE)])
        docs = result["documents"]
        assert len(docs) >= 1
        text = docs[0].content or ""
        assert len(text) > 50, f"XLSX 解析文本过短: {len(text)} 字符"


# ═══════════════════════════════════════════════════════════════
# 2. 文档分割测试
# ═══════════════════════════════════════════════════════════════


class TestDocumentSplitting:
    """分层文档分割"""

    @pytest.fixture
    def splitter(self):
        from qa.pipelines.components.hierarchical_store import HierarchicalDocumentSplitter

        return HierarchicalDocumentSplitter(section_size=500, paragraph_size=100)

    def test_split_pdf_content(self, splitter):
        from haystack import Document

        doc = Document(
            id="test_doc",
            content=(  # noqa: E501
                "中国结算深圳分公司发布通知。关于2026年5月30日开展深市交易结算系统全网测试。"
            )
            * 20,
            meta={"source": "CSDC", "category": "clearing_rule", "effective_date": "2026-06-01"},
        )
        result = splitter.run([doc])
        parents = result["parents"]
        chunks = result["chunks"]
        assert len(parents) >= 1, "应有至少 1 个大块"
        assert len(chunks) >= len(parents), "小块数量应 >= 大块数量"
        # 验证层级结构
        for chunk in chunks:
            meta = chunk.meta or {}
            assert meta.get("level") == 2
            assert meta.get("parent_id") is not None

    def test_split_with_metadata_preserved(self, splitter):
        from haystack import Document

        doc = Document(
            id="doc2",
            content="测试内容段落。" * 30,
            meta={
                "source": "SSE",
                "category": "tech_manual",
                "effective_date": "2026-05-01",
                "version": "2.0",
                "tags": ["测试", "技术"],
            },
        )
        result = splitter.run([doc])
        for chunk in result["chunks"]:
            meta = chunk.meta or {}
            assert meta["source"] == "SSE"
            assert meta["category"] == "tech_manual"
            assert meta["effective_date"] == "2026-05-01"


# ═══════════════════════════════════════════════════════════════
# 3. 元数据验证测试
# ═══════════════════════════════════════════════════════════════


class TestMetadataValidation:
    """必需字段校验"""

    def test_valid_meta_passes(self):
        from qa.pipelines.indexing import validate_meta

        missing = validate_meta(REQUIRED_META)
        assert missing == []

    def test_missing_source(self):
        from qa.pipelines.indexing import validate_meta

        missing = validate_meta({"category": "rule", "effective_date": "2026-01-01"})
        assert "source" in missing

    def test_missing_category(self):
        from qa.pipelines.indexing import validate_meta

        missing = validate_meta({"source": "CSDC", "effective_date": "2026-01-01"})
        assert "category" in missing

    def test_missing_effective_date(self):
        from qa.pipelines.indexing import validate_meta

        missing = validate_meta({"source": "CSDC", "category": "rule"})
        assert "effective_date" in missing

    def test_all_missing(self):
        from qa.pipelines.indexing import validate_meta

        missing = validate_meta({})
        assert len(missing) == 3


# ═══════════════════════════════════════════════════════════════
# 4. 检索 + 排序测试
# ═══════════════════════════════════════════════════════════════


class TestRetrieval:
    """检索 + AutoMerging + 去重"""

    @pytest.fixture
    def store_manager(self):
        """使用 InMemoryDocumentStore 替代 turbovec"""
        from haystack import Document
        from haystack.document_stores.in_memory import InMemoryDocumentStore

        store = InMemoryDocumentStore(embedding_similarity_function="cosine")

        class FakeStoreManager:
            def __init__(self):
                self.chunk_store = store
                self.parent_store = store

            def retrieve(self, query_embedding, top_k=5, filters=None):
                # InMemory 不支持 embedding_retrieval，用 filter 模拟
                all_docs = self.chunk_store.filter_documents()
                # 按批次返回（模拟检索）
                return all_docs[:top_k]

            def get_parent_docs(self, parent_ids):
                return [d for d in self.parent_store.filter_documents() if d.id in parent_ids]

            def count_chunks(self):
                return len(self.chunk_store.filter_documents())

            def count_documents(self):
                return len(self.parent_store.filter_documents())

        mgr = FakeStoreManager()
        # 添加测试文档
        for i in range(3):
            parent = Document(
                id=f"p_{i}",
                content=(  # noqa: E501
                    f"大块内容第{i + 1}段。中国结算深圳分公司关于结算系统测试的通知。"
                    "测试内容包括交易系统、结算系统。"
                )
                * 5,
                meta={"file_path": f"test_{i}.pdf", "source": "CSDC", "category": "test"},
            )
            mgr.parent_store.write_documents([parent])
            for j in range(2):
                chunk = Document(
                    id=f"c_{i}_{j}",
                    content=f"小块内容 {i + 1}-{j + 1}。结算系统测试通知。",
                    meta={
                        "file_path": f"test_{i}.pdf",
                        "parent_id": f"p_{i}",
                        "source": "CSDC",
                        "category": "test",
                        "level": 2,
                    },
                )
                mgr.chunk_store.write_documents([chunk])
        return mgr

    def test_auto_merging(self, store_manager):
        from qa.pipelines.querying import QueryPipeline

        pipeline = QueryPipeline(
            store_manager=store_manager,
            top_k=5,
            auto_merge_threshold=0.3,
        )
        # 手动构造 chunk_results
        chunks = store_manager.chunk_store.filter_documents()
        context_docs = pipeline._merge_chunks(chunks)
        assert len(context_docs) > 0

    def test_source_dedup(self, store_manager):
        from qa.pipelines.querying import QueryPipeline

        pipeline = QueryPipeline(store_manager=store_manager)
        chunks = store_manager.chunk_store.filter_documents()
        sources = pipeline._build_sources(chunks)
        deduped = pipeline._dedup_sources(sources)
        # 即使有多个 chunk，相同文件名的应合并
        assert len(deduped) <= len(sources)


# ═══════════════════════════════════════════════════════════════
# 5. 混合检索来源标注
# ═══════════════════════════════════════════════════════════════


class TestSourceTypeAnnotation:
    """检索结果来源类型标注"""

    def test_hybrid_source_type(self):
        """验证 HybridRetriever 标注 source_type"""
        from haystack import Document

        from qa.pipelines.components.hybrid_retriever import HybridRetriever

        class FakeStore:
            def retrieve(self, query_embedding, top_k=10, filters=None):
                return [
                    Document(
                        id="a",
                        content="Python 版本的相关信息。",
                        score=0.9,
                        meta={"file_path": "doc1.pdf"},
                    ),
                    Document(
                        id="b",
                        content="Java 版本不相关的内容。",
                        score=0.5,
                        meta={"file_path": "doc2.pdf"},
                    ),
                ]

        hybrid = HybridRetriever(store_manager=FakeStore(), top_k=2)
        results = hybrid.retrieve(
            query_embedding=[0.1, 0.2],
            query_text="Python",
            top_k=2,
        )
        for r in results:
            meta = r.meta or {}
            assert "source_type" in meta, f"缺少 source_type: {r.id}"
            assert meta["source_type"] in ("vector", "bm25", "hybrid")


# ═══════════════════════════════════════════════════════════════
# 6. Early Exit 精确匹配
# ═══════════════════════════════════════════════════════════════


class TestEarlyExitIntegration:
    """Early Exit 精确匹配"""

    @pytest.fixture
    def std_store(self):
        import shutil as _shutil

        from qa.pipelines.components.early_exit import StandardAnswer, StandardAnswerStore

        tmpdir = tempfile.mkdtemp()
        store = StandardAnswerStore(store_path=tmpdir)
        store.load()
        store.add(
            StandardAnswer(
                question="中国结算深市全网测试时间？",
                answer="2026年5月30日",
                category="clearing_rule",
                source="seed",
            )
        )
        yield store
        _shutil.rmtree(tmpdir, ignore_errors=True)

    def test_exact_match(self, std_store):
        """精确匹配应返回标准答案"""
        from qa.pipelines.components.early_exit import EarlyExitMatcher

        matcher = EarlyExitMatcher(store_path=std_store.store_path, enabled=True)
        matcher.store = std_store
        result = matcher.match("中国结算深市全网测试时间？")
        assert result.matched is True
        assert result.match_type == "exact"
        assert "2026年5月30日" in result.answer.answer

    def test_normalized_match(self, std_store):
        """标准化后的精确匹配"""
        from qa.pipelines.components.early_exit import EarlyExitMatcher

        matcher = EarlyExitMatcher(store_path=std_store.store_path, enabled=True)
        matcher.store = std_store
        result = matcher.match("  中国结算深市全网测试时间？ ")
        assert result.matched is True
        assert "2026年5月30日" in result.answer.answer
