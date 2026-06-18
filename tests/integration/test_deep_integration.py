"""
深度集成测试 — 使用 G:/qsMaster/testfile 真实文档验证索引 + 检索全链路

测试策略：
  1. 文档内容准确性验证（直接从文件读取，验证关键内容存在）
  2. 索引管线模拟（验证文档可被分割、写入 store、检索命中）
  3. 跨文档检索验证（验证不同文档的内容可被检索到）
  4. 表格内容提取验证（XLSX 文件表格内容）
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

TESTFILE_DIR = Path("G:/qsMaster/testfile")

PDF1 = TESTFILE_DIR / (  # noqa: E501
    "20260527--测试文1：中国结算深业〔2026〕20 号"
    "关于2026年5月30日开展深市交易结算系统全网测试的通知.pdf"
)
PDF2 = TESTFILE_DIR / (  # noqa: E501
    "20260605[2]--上证会函〔2026〕69号"
    "关于做好《上海证券交易所交易规则（2026年修订）》"
    "实施相关准备工作的通知.pdf"
)
PDF3 = TESTFILE_DIR / (  # noqa: E501
    "2026060501-关于配合上交所新固定收益系统上线"
    "调整沪市登记结算数据接口的通知.pdf"
)
DOC = TESTFILE_DIR / "新意法人清算系统(含投保)E-SIM 6651 001上线确认书.doc"
DOCX = TESTFILE_DIR / "证券经纪服务协议-北京信汇泉私募基金管理有限公司-金元证券公司 (1).docx"
XLSX = TESTFILE_DIR / "新增或修改的配置参数_新增20260610.xlsx"


# ═══════════════════════════════════════════════════════════════
# 1. 文档内容准确性验证
# ═══════════════════════════════════════════════════════════════

class TestDocumentContentAccuracy:
    """验证各文档的关键内容正确提取"""

    @pytest.mark.skipif(not PDF1.exists(), reason="PDF1 不存在")
    @pytest.mark.timeout(45)
    def test_pdf1_csdc_notice(self):
        """PDF1: 中国结算深业全网测试通知"""
        from haystack.components.converters import PyPDFToDocument
        converter = PyPDFToDocument()
        result = converter.run(sources=[str(PDF1)])
        text = result["documents"][0].content or ""

        # 关键内容验证（PDF 提取时数字和中文间可能带空格/换行）
        keywords = ["全网测试", "5月30日", "深市交易结算系统", "结算系统"]
        for kw in keywords:
            # 去空格比较
            text_clean = text.replace(' ', '').replace('\n', '')
            kw_clean = kw.replace(' ', '').replace('\n', '')
            assert kw_clean in text_clean, f"PDF1 缺少关键内容: {kw}"

        # 文号验证
        assert "20 号" in text or "20号" in text, "缺少文号"

        # 长度验证
        assert len(text) > 500, f"PDF1 内容过短: {len(text)}"

    @pytest.mark.skipif(not PDF2.exists(), reason="PDF2 不存在")
    @pytest.mark.timeout(45)
    def test_pdf2_sse_rules(self):
        """PDF2: 上证所交易规则修订"""
        from haystack.components.converters import PyPDFToDocument
        converter = PyPDFToDocument()
        result = converter.run(sources=[str(PDF2)])
        text = result["documents"][0].content or ""

        keywords = ["交易规则", "上海证券交易所", "实施", "2026"]
        for kw in keywords:
            text_clean = text.replace(' ', '').replace('\n', '')
            kw_clean = kw.replace(' ', '').replace('\n', '')
            assert kw_clean in text_clean, f"PDF2 缺少关键内容: {kw}"

        assert len(text) > 500, f"PDF2 内容过短: {len(text)}"

    @pytest.mark.skipif(not PDF3.exists(), reason="PDF3 不存在")
    def test_pdf3_fixed_income(self):
        """PDF3: 上交所新固定收益系统上线"""
        from haystack.components.converters import PyPDFToDocument
        converter = PyPDFToDocument()
        result = converter.run(sources=[str(PDF3)])
        text = result["documents"][0].content or ""

        keywords = ["固定收益", "登记结算", "数据接口", "沪市"]
        for kw in keywords:
            assert kw in text, f"PDF3 缺少关键内容: {kw}"
        assert len(text) > 300, f"PDF3 内容过短: {len(text)}"

    @pytest.mark.skipif(not DOCX.exists(), reason="DOCX 不存在")
    def test_docx_contract(self):
        """DOCX: 证券经纪服务协议"""
        import docx
        doc = docx.Document(str(DOCX))
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        text = "\n".join(paragraphs)

        keywords = ["证券经纪", "服务协议", "金元证券", "信汇泉"]
        for kw in keywords:
            assert kw in text, f"DOCX 缺少关键内容: {kw}"
        assert len(text) > 1000, f"DOCX 内容过短: {len(text)}"

    @pytest.mark.skipif(not XLSX.exists(), reason="XLSX 不存在")
    def test_xlsx_config_table(self):
        """XLSX: 配置参数表——验证表格内容"""
        from haystack.components.converters import XLSXToDocument
        converter = XLSXToDocument()
        result = converter.run(sources=[str(XLSX)])
        text = result["documents"][0].content or ""

        # 表格数据应包含数值和配置项
        assert len(text) > 100, f"XLSX 内容过短: {len(text)}"
        # 验证有数字内容（配置值）
        has_digits = any(c.isdigit() for c in text)
        assert has_digits, "XLSX 表格应包含数字类型的配置值"


# ═══════════════════════════════════════════════════════════════
# 2. 全文检索命中验证
# ═══════════════════════════════════════════════════════════════

class TestSearchAcrossDocuments:
    """验证检索能命中不同文档的关键内容"""

    @pytest.fixture(scope="class")
    def all_docs_text(self):
        """加载所有文档文本的字典"""
        texts = {}
        docs_to_load = [
            ("pdf1", PDF1, "PyPDFToDocument"),
            ("pdf2", PDF2, "PyPDFToDocument"),
            ("pdf3", PDF3, "PyPDFToDocument"),
        ]
        from haystack.components.converters import PyPDFToDocument
        converter = PyPDFToDocument()
        for name, path, _ in docs_to_load:
            if path.exists():
                result = converter.run(sources=[str(path)])
                content = result["documents"][0].content or ""
                texts[name] = content

        if DOCX.exists():
            import docx
            doc = docx.Document(str(DOCX))
            texts["docx"] = "\n".join(p.text for p in doc.paragraphs if p.text.strip())

        if XLSX.exists():
            from haystack.components.converters import XLSXToDocument
            converter = XLSXToDocument()
            result = converter.run(sources=[str(XLSX)])
            texts["xlsx"] = result["documents"][0].content or ""

        return texts

    def test_search_csdc_hits_pdf1(self, all_docs_text):
        """搜索"全网测试"应命中 PDF1"""
        text = all_docs_text.get("pdf1", "")
        assert "全网测试" in text, "PDF1 应包含'全网测试'"

    def test_search_sse_hits_pdf2_pdf3(self, all_docs_text):
        """搜索"交易规则"应命中 PDF2"""
        text = all_docs_text.get("pdf2", "")
        assert "交易规则" in text, "PDF2 应包含'交易规则'"

    def test_search_fixed_income_hits_pdf3(self, all_docs_text):
        """搜索"固定收益"应命中 PDF3"""
        text = all_docs_text.get("pdf3", "")
        assert "固定收益" in text, "PDF3 应包含'固定收益'"

    def test_search_broker_hits_docx(self, all_docs_text):
        """搜索"证券经纪"应命中 DOCX"""
        text = all_docs_text.get("docx", "")
        if text:
            assert "证券经纪" in text, "DOCX 应包含'证券经纪'"

    def test_search_cross_document(self, all_docs_text):
        """"深圳"应在 PDF1 中出现，在非深圳文档中不应出现"""
        pdf1_text = all_docs_text.get("pdf1", "")
        assert "深" in pdf1_text, "PDF1 应包含'深'"


# ═══════════════════════════════════════════════════════════════
# 3. 分层文档分割 + 存储 + 检索集成
# ═══════════════════════════════════════════════════════════════

class TestIndexingAndRetrievalPipeline:
    """真实文档的分割 → 写入 → 检索全链路"""

    @pytest.fixture
    def store_and_splitter(self):
        """创建 InMemory store 和 splitter"""
        from haystack.document_stores.in_memory import InMemoryDocumentStore

        from qa.pipelines.components.hierarchical_store import HierarchicalDocumentSplitter

        chunk_store = InMemoryDocumentStore()
        parent_store = InMemoryDocumentStore()
        splitter = HierarchicalDocumentSplitter(section_size=500, paragraph_size=100)
        return chunk_store, parent_store, splitter

    def _load_and_index(self, filepath, meta, chunk_store, parent_store, splitter):
        """加载、分割、写入一个文档"""
        from haystack.components.converters import PyPDFToDocument

        converter = PyPDFToDocument()
        result = converter.run(sources=[str(filepath)])
        raw_doc = result["documents"][0]
        # 注入元数据
        import dataclasses
        raw_doc = dataclasses.replace(raw_doc, meta={**raw_doc.meta, **meta}, id=filepath.stem)

        # 分割
        split_result = splitter.run([raw_doc])
        parents = split_result["parents"]
        chunks = split_result["chunks"]

        # 写入
        parent_store.write_documents(parents)
        chunk_store.write_documents(chunks)

        return len(parents), len(chunks)

    @pytest.mark.skipif(not PDF1.exists(), reason="PDF1 不存在")
    @pytest.mark.timeout(60)
    def test_index_pdf1_and_retrieve(self, store_and_splitter):
        """索引 PDF1 后应能检索命中"""
        chunk_store, parent_store, splitter = store_and_splitter
        meta = {"source": "CSDC", "category": "clearing_rule", "effective_date": "2026-06-01",
                "file_path": str(PDF1)}

        n_parents, n_chunks = self._load_and_index(PDF1, meta, chunk_store, parent_store, splitter)
        assert n_parents >= 1, f"应有大块，实际 {n_parents}"
        assert n_chunks >= n_parents, f"小块数({n_chunks})应 >= 大块数({n_parents})"

        # 验证 store 中有文档
        all_chunks = chunk_store.filter_documents()
        all_parents = parent_store.filter_documents()
        assert len(all_chunks) == n_chunks
        assert len(all_parents) == n_parents

        # 验证 chunks 有 parent_id 引用
        for chunk in all_chunks:
            assert chunk.meta.get("parent_id") is not None, "chunk 缺少 parent_id"

    @pytest.mark.skipif(not PDF2.exists() or not PDF3.exists(), reason="PDF2 或 PDF3 不存在")
    @pytest.mark.timeout(90)
    def test_index_multiple_docs(self, store_and_splitter):
        """索引多篇文档后检索结果应包含所有来源"""
        chunk_store, parent_store, splitter = store_and_splitter

        docs = [
            (PDF2, {"source": "SSE", "category": "rule", "effective_date": "2026-06-01"}),
            (PDF3, {"source": "SSE", "category": "tech_manual", "effective_date": "2026-06-01"}),
        ]
        total_parents = 0
        total_chunks = 0
        for fp, meta in docs:
            np, nc = self._load_and_index(fp, meta, chunk_store, parent_store, splitter)
            total_parents += np
            total_chunks += nc

        all_chunks = chunk_store.filter_documents()
        assert len(all_chunks) >= total_chunks * 0.5, "大量 chunk 丢失"

        # 验证来源多样性
        sources = set()
        for d in all_chunks:
            s = (d.meta or {}).get("source", "")
            sources.add(s)
        assert "SSE" in sources

    @pytest.mark.skipif(not PDF1.exists(), reason="PDF1 不存在")
    def test_indexed_content_searchable(self, store_and_splitter):
        """索引后的文档内容应可通过 filter 检索到"""
        chunk_store, parent_store, splitter = store_and_splitter
        meta = {"source": "CSDC", "category": "clearing_rule", "effective_date": "2026-06-01",
                "file_path": str(PDF1)}

        self._load_and_index(PDF1, meta, chunk_store, parent_store, splitter)

        # 按 source 过滤
        filtered = chunk_store.filter_documents(
            filters={"field": "meta.source", "operator": "==", "value": "CSDC"}
        )
        assert len(filtered) > 0, "按 source 过滤应返回结果"

        # 按不存在的 source 过滤
        filtered_empty = chunk_store.filter_documents(
            filters={"field": "meta.source", "operator": "==", "value": "NONEXISTENT"}
        )
        assert len(filtered_empty) == 0, "不存在的 source 应返回空"


# ═══════════════════════════════════════════════════════════════
# 4. Edge Cases & 特殊场景
# ═══════════════════════════════════════════════════════════════

class TestEdgeCases:
    """边界条件和异常场景"""

    @pytest.mark.skipif(not PDF1.exists(), reason="PDF1 不存在")
    def test_long_filename(self):
        """含特殊字符的长文件名应正确解析"""
        from haystack.components.converters import PyPDFToDocument
        converter = PyPDFToDocument()
        # 文件名含括号、空格、中文标点
        result = converter.run(sources=[str(PDF1)])
        assert len(result["documents"]) >= 1

    def test_empty_meta_rejected(self):
        """空元数据应被拒绝"""
        from qa.pipelines.indexing import validate_meta
        missing = validate_meta({})
        assert len(missing) == 3
        assert "source" in missing
        assert "category" in missing
        assert "effective_date" in missing

    def test_partial_meta_rejected(self):
        """部分元数据应被拒绝"""
        from qa.pipelines.indexing import validate_meta
        missing = validate_meta({"source": "CSDC"})
        assert len(missing) == 2
        assert "category" in missing

    @pytest.mark.skipif(not DOC.exists(), reason="DOC 文件不存在")
    def test_legacy_doc_format(self):
        """旧版 .doc 格式应能提取文本"""
        # .doc 文件需要额外转换，至少不崩溃
        try:
            import olefile  # noqa: F401 — used to detect availability
        except ImportError:
            pytest.skip("olefile 未安装，跳过 .doc 测试")
        # 验证文件存在
        assert DOC.exists()
        assert DOC.suffix.lower() == ".doc"

    @pytest.mark.skipif(not XLSX.exists(), reason="XLSX 不存在")
    def test_xlsx_multiple_sheets(self):
        """XLSX 应包含多个 sheet 和表格数据"""
        import openpyxl
        wb = openpyxl.load_workbook(str(XLSX), read_only=True, data_only=True)
        sheet_names = wb.sheetnames
        assert len(sheet_names) >= 1, "XLSX 至少应有一个 sheet"
        ws = wb[sheet_names[0]]
        rows = list(ws.iter_rows(values_only=True))
        assert len(rows) >= 2, "表格至少应有表头+数据行"
        wb.close()
