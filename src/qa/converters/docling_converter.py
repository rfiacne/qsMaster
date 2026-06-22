"""
文档转换器适配层 — 支持 PDF/DOCX/DOC/XLSX/XLS/MD/HTML/TXT 八种格式。

策略：
- PDF：Auto 模式多级 fallback 链路:
        Docling → OpenDataLoader(#1 benchmark) → PaddleOCR(扫描件) → PyMuPDF
        支持独立选择后端: docling / opendataloader / paddle / none
- DOCX/DOC：python-docx 解析，表格转 Markdown 格式
- XLSX/XLS：openpyxl/xlrd 逐 sheet 读取，每表转 Markdown
- MD：保留标题层级结构
- HTML：BeautifulSoup 提取正文
- TXT：UTF-8 纯文本读取
- JPG/PNG：PaddleOCR 识别图片中文字
"""

from __future__ import annotations

import logging
from pathlib import Path

from haystack import Document

logger = logging.getLogger(__name__)


# ─── 支持的文档类型 ─────────────────────────────────────────


SUPPORTED_EXTENSIONS: dict[str, str] = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".doc": "docx",  # 旧版 Word，与 docx 共享转换器
    ".xlsx": "xlsx",
    ".xls": "xls",
    ".md": "md",
    ".markdown": "md",
    ".html": "html",
    ".htm": "html",
    ".txt": "txt",
    ".jpg": "image",
    ".jpeg": "image",
    ".png": "image",
}


def detect_file_type(file_path: str) -> str | None:
    """检测文件类型（扩展名匹配）

    Returns:
        "pdf" | "docx" | "xlsx" | "xls" | "md" | "html" | "txt" | "image" | None
    """
    ext = Path(file_path).suffix.lower()
    return SUPPORTED_EXTENSIONS.get(ext)


# ─── PaddleOCR 后端 ─────────────────────────────────────────


class PaddleOCRBackend:
    """PaddleOCR 识别后端（扫描件 OCR）

    使用 PP-OCRv6 medium 模型（PaddleOCR 3.x / paddleocr>=3.7.0）。
    PP-OCRv6 统一支持 50 种语言，无需切换模型。
    仅在使用时延迟加载，不占用启动时间。
    """

    def __init__(self, lang: str = "ch"):
        self.lang = lang
        self._ocr = None

    def _lazy_init(self):
        if self._ocr is not None:
            return
        try:
            from paddleocr import PaddleOCR

            # PaddleOCR 3.x API（PP-OCRv6 默认）
            # use_textline_orientation 替代废弃的 use_angle_cls
            # 模型大小通过 ocr_version 控制，默认 medium
            self._ocr = PaddleOCR(
                use_textline_orientation=True,
                lang=self.lang,
                ocr_version="PP-OCRv6",
                engine="transformers",
            )
            logger.info("PaddleOCR PP-OCRv6 模型加载完成")
        except ImportError:
            raise ImportError(
                "PaddleOCR 不可用，请安装: pip install paddlepaddle paddleocr"
            )

    def recognize_page(self, image_bytes: bytes) -> str:
        """识别单页图片，返回按阅读顺序排列的文本"""
        self._lazy_init()
        import cv2
        import numpy as np

        nparr = np.frombuffer(image_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            return ""

        # PaddleOCR 3.x 使用 predict() 替代废弃的 ocr()
        results = self._ocr.predict(img)

        lines: list[str] = []
        for res in results:
            data = res.json["res"]
            texts = data.get("rec_texts", [])
            scores = data.get("rec_scores", [])
            for text, conf in zip(texts, scores):
                if conf > 0.5:  # 置信度阈值
                    lines.append(text.strip())

        return "\n".join(lines)


# ─── OpenDataLoader PDF 后端 ────────────────────────────────


class OpenDataLoaderPDFConverter:
    """OpenDataLoader PDF 转换器

    GitHub: https://github.com/opendataloader-project/opendataloader-pdf
    Benchmark #1 (0.907 overall), 支持 Markdown/JSON/HTML 输出。

    要求: Java 11+ 和 pip install opendataloader-pdf
    注意: 每次 convert() 启动 JVM 进程，建议批量传入多个文件。
    """

    def __init__(self, hybrid: str | None = None):
        """
        Args:
            hybrid: None=纯本地模式, "docling-fast"=混合模式(AI增强)
        """
        self.hybrid = hybrid
        self._available = False
        self._check_available()

    def _check_available(self) -> None:
        try:
            import opendataloader_pdf  # noqa: F401
            self._available = True
        except ImportError:
            self._available = False

    def convert(self, file_path: str, meta: dict | None = None) -> list[Document]:
        """使用 OpenDataLoader 转换 PDF"""
        if not self._available:
            raise ImportError(
                "opendataloader-pdf 不可用，请安装: pip install opendataloader-pdf\n"
                "要求: Java 11+ (运行 java -version 检查)"
            )

        import json
        import tempfile

        import opendataloader_pdf

        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"文件不存在: {file_path}")

        base_meta = {
            "file_path": str(path),
            "file_type": "pdf",
            "parser": "opendataloader",
            **(meta or {}),
        }

        # 在临时目录中执行转换
        with tempfile.TemporaryDirectory(prefix="odl_") as tmp_dir:
            kwargs = {
                "input_path": [str(path)],
                "output_dir": tmp_dir,
                "format": "markdown,json",
            }
            if self.hybrid:
                kwargs["hybrid"] = self.hybrid

            try:
                opendataloader_pdf.convert(**kwargs)
            except Exception as e:
                logger.error(f"OpenDataLoader 转换失败 ({path.name}): {e}")
                raise

            # 读取生成的 Markdown 文件
            md_files = sorted(Path(tmp_dir).rglob("*.md"))
            if not md_files:
                logger.warning(f"OpenDataLoader 未生成 Markdown 输出: {path.name}")
                return []

            # 读取 Markdown 内容
            full_text_parts: list[str] = []
            for md_file in md_files:
                text = md_file.read_text(encoding="utf-8")
                if text.strip():
                    full_text_parts.append(text)

            # 尝试读取 JSON 获取结构化信息（用于表格提取等）
            json_files = sorted(Path(tmp_dir).rglob("*.json"))
            if json_files and full_text_parts:
                try:
                    json_data = json.loads(json_files[0].read_text(encoding="utf-8"))
                    # 从 JSON 中提取表格内容补充
                    table_texts = self._extract_tables_from_json(json_data)
                    if table_texts:
                        full_text_parts.append("\n【表格内容】\n" + "\n---\n".join(table_texts))
                except (json.JSONDecodeError, IndexError):
                    pass

            full_text = "\n\n".join(full_text_parts)

            if not full_text.strip():
                logger.warning(f"OpenDataLoader 输出为空: {path.name}")
                return []

            logger.info(
                f"OpenDataLoader 转换完成: {path.name} "
                f"({len(full_text)} 字符, hybrid={self.hybrid})"
            )
            return [Document(content=full_text, meta=base_meta)]

    def _extract_tables_from_json(self, data: list) -> list[str]:
        """从 OpenDataLoader JSON 输出中提取表格 Markdown"""
        table_texts: list[str] = []
        if not isinstance(data, list):
            return table_texts

        for item in data:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "table":
                content = item.get("content", "")
                if content and content.strip():
                    table_texts.append(f"[表格 - 第{item.get('page number', '?')}页]\n{content}")
        return table_texts


# ─── PDF 转换器 ─────────────────────────────────────────────


class PDFConverter:
    """PDF 文档转换器

    三级策略：
    1. Docling（版面分析 + 表格提取，含内置 OCR）
    2. PaddleOCR（配置启用时，自动检测扫描件后识别）
    3. PyMuPDF 文本提取（最终 fallback）

    ocr_backend 参数:
      - "auto"（默认）: Docling 优先 → OpenDataLoader fallback → PaddleOCR 扫描件
      - "docling": 仅使用 Docling
      - "opendataloader": 使用 OpenDataLoader（#1 benchmark, 需 Java 11+）
      - "paddle": 强制使用 PaddleOCR 识别所有 PDF
      - "none": 不使用 OCR，PyMuPDF 纯文本提取
    """

    def __init__(self, ocr_enabled: bool = False, ocr_backend: str = "auto"):
        self.ocr_enabled = ocr_enabled
        self.ocr_backend = ocr_backend if ocr_enabled else "none"
        self._docling_available = False
        self._paddle: PaddleOCRBackend | None = None
        self._odl: OpenDataLoaderPDFConverter | None = None
        self._init_docling()
        self._init_opendataloader()

    def _init_opendataloader(self) -> None:
        """延迟初始化 OpenDataLoader（仅当后端选择它时）"""
        if self.ocr_backend in ("opendataloader", "auto"):
            try:
                self._odl = OpenDataLoaderPDFConverter()
                if self._odl._available:
                    logger.info("OpenDataLoader PDF 可用")
            except Exception:
                self._odl = None

    def _init_docling(self) -> None:
        """尝试初始化 Docling"""
        try:
            from docling.document_converter import DocumentConverter

            self._docling_available = True
            self._docling_converter = DocumentConverter()
            logger.info("Docling 初始化成功，将用于 PDF 解析")
        except ImportError:
            self._docling_available = False
            logger.info("Docling 不可用，使用 PyMuPDF/PaddleOCR 解析 PDF")

    def _get_paddle(self) -> PaddleOCRBackend:
        if self._paddle is None:
            self._paddle = PaddleOCRBackend()
        return self._paddle

    def convert(self, file_path: str, meta: dict | None = None) -> list[Document]:
        """转换 PDF 文件为 Haystack Document 列表"""
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"文件不存在: {file_path}")

        base_meta = {
            "file_path": str(path),
            "file_type": "pdf",
            **(meta or {}),
        }

        # 强制模式：特定后端
        if self.ocr_backend == "paddle":
            return self._convert_with_paddle(path, base_meta)
        if self.ocr_backend == "opendataloader":
            return self._convert_with_odl(path, base_meta)

        # Auto 模式：多级 fallback
        # 1) Docling 优先
        if self._docling_available:
            try:
                return self._convert_with_docling(path, base_meta)
            except Exception as e:
                logger.warning(f"Docling 失败 ({path.name}): {e}，尝试 OpenDataLoader")

        # 2) OpenDataLoader 次选（benchmark #1）
        if self._odl and self._odl._available:
            try:
                return self._convert_with_odl(path, base_meta)
            except Exception as e:
                logger.warning(f"OpenDataLoader 失败 ({path.name}): {e}")

        # 3) 扫描件 → PaddleOCR
        if self._is_scanned(path):
            return self._convert_with_paddle(path, base_meta)

        # 4) 最终 fallback
        return self._convert_with_fallback(path, base_meta)

    def _is_scanned(self, path: Path) -> bool:
        """判断 PDF 是否为扫描件（文本不足 100 字符）"""
        try:
            import fitz
        except ImportError:
            return False
        doc = fitz.open(path)
        total_chars = sum(len(page.get_text().strip()) for page in doc)
        doc.close()
        return total_chars < 100

    def _convert_with_docling(self, path: Path, meta: dict) -> list[Document]:
        """使用 Docling 转换 PDF"""
        try:
            result = self._docling_converter.convert(str(path))
            text = result.document.export_to_text()

            tables = []
            for table in result.document.tables:
                table_text = table.export_to_markdown(doc=result.document)
                if table_text.strip():
                    tables.append(table_text)

            full_text = text
            if tables:
                full_text += "\n\n【表格内容】\n" + "\n---\n".join(tables)

            return [Document(content=full_text, meta=meta)]

        except Exception as e:
            logger.error(f"Docling 转换失败 ({path.name}): {e}")
            # 检查是否为扫描件
            if self.ocr_enabled and self._is_scanned(path):
                return self._convert_with_paddle(path, meta)
            return self._convert_with_fallback(path, meta)

    def _convert_with_paddle(self, path: Path, meta: dict) -> list[Document]:
        """使用 PaddleOCR 识别扫描件 PDF"""
        try:
            import fitz
        except ImportError:
            raise ImportError("请安装 PyMuPDF: pip install PyMuPDF")

        paddle = self._get_paddle()
        doc = fitz.open(path)
        pages_text: list[str] = []

        for page_num in range(len(doc)):
            page = doc[page_num]
            pix = page.get_pixmap(dpi=300)
            img_bytes = pix.tobytes("png")
            text = paddle.recognize_page(img_bytes)
            if text.strip():
                pages_text.append(f"--- 第 {page_num + 1} 页 ---\n{text}")

        doc.close()

        if not pages_text:
            logger.warning(f"PaddleOCR 未能识别出文本: {path.name}")
            return []

        full_text = "\n\n".join(pages_text)
        logger.info(f"PaddleOCR 识别完成: {path.name} ({len(pages_text)} 页)")
        return [Document(content=full_text, meta=meta)]

    def _convert_with_odl(self, path: Path, meta: dict) -> list[Document]:
        """使用 OpenDataLoader 转换 PDF"""
        if self._odl is None:
            self._odl = OpenDataLoaderPDFConverter()
        return self._odl.convert(str(path), meta=meta)

    def _convert_with_fallback(self, path: Path, meta: dict) -> list[Document]:
        """使用 PyMuPDF 文本提取（纯文本 PDF fallback）"""
        try:
            import fitz
        except ImportError:
            raise ImportError("请安装 PyMuPDF: pip install PyMuPDF")

        doc = fitz.open(path)
        pages = []
        for page in doc:
            text = page.get_text()
            if text.strip():
                pages.append(text)
        doc.close()

        if not pages:
            logger.warning(f"PDF 无文本内容（可能是扫描件）: {path.name}")
            return []

        full_text = "\n\n---\n\n".join(pages)
        return [Document(content=full_text, meta=meta)]


# ─── DOCX/DOC 转换器 ─────────────────────────────────────────


class DOCXConverter:
    """Word 文档转换器（兼容 .docx 和 .doc）

    使用 python-docx 提取文本和表格，表格以 Markdown 格式输出。
    python-docx 对 .doc（OLE2）有基础支持。
    """

    def convert(self, file_path: str, meta: dict | None = None) -> list[Document]:
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"文件不存在: {file_path}")

        ext = path.suffix.lower()
        file_type = "doc" if ext == ".doc" else "docx"

        base_meta = {
            "file_path": str(path),
            "file_type": file_type,
            **(meta or {}),
        }

        try:
            import docx
        except ImportError:
            raise ImportError("请安装 python-docx: pip install python-docx")

        doc = docx.Document(path)

        # 提取段落文本
        paragraphs = []
        for para in doc.paragraphs:
            if para.text.strip():
                paragraphs.append(para.text)

        # 提取表格（转 Markdown 格式）
        tables_text = []
        for table in doc.tables:
            rows = []
            for row in table.rows:
                cells = [cell.text.strip().replace("\n", " ") for cell in row.cells]
                rows.append("| " + " | ".join(cells) + " |")
            if rows:
                header = rows[0]
                sep = "| " + " | ".join(["---"] * len(row.cells)) + " |"
                tables_text.append("\n".join([header, sep] + rows[1:]))

        full_text = "\n\n".join(paragraphs)
        if tables_text:
            full_text += "\n\n【表格内容】\n\n" + "\n\n---\n\n".join(tables_text)

        return [Document(content=full_text, meta=base_meta)]


# ─── XLSX/XLS 转换器 ─────────────────────────────────────────


class XLSXConverter:
    """Excel 工作簿转换器（兼容 .xlsx 和 .xls）

    - .xlsx: openpyxl 读取
    - .xls:  xlrd 读取
    每张工作表转 Markdown 表格，sheet 之间用标题分隔。
    """

    def convert(self, file_path: str, meta: dict | None = None) -> list[Document]:
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"文件不存在: {file_path}")

        ext = path.suffix.lower()
        file_type = "xlsx" if ext == ".xlsx" else "xls"

        base_meta = {
            "file_path": str(path),
            "file_type": file_type,
            **(meta or {}),
        }

        if ext == ".xlsx":
            return self._convert_xlsx(path, base_meta)
        else:
            return self._convert_xls(path, base_meta)

    def _convert_xlsx(self, path: Path, meta: dict) -> list[Document]:
        """使用 openpyxl 读取 .xlsx"""
        try:
            import openpyxl
        except ImportError:
            raise ImportError("请安装 openpyxl: pip install openpyxl")

        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        parts: list[str] = []

        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            parts.append(f"## Sheet: {sheet_name}")

            rows_text: list[str] = []
            for row in ws.iter_rows(values_only=True):
                cells = [
                    (str(cell) if cell is not None else "")
                    for cell in row
                ]
                if any(c.strip() for c in cells):
                    rows_text.append("| " + " | ".join(cells) + " |")

            if rows_text:
                # 添加表头分隔线（首行为表头）
                header = rows_text[0]
                col_count = header.count("|") - 1
                sep = "| " + " | ".join(["---"] * col_count) + " |"
                parts.append("\n".join([header, sep] + rows_text[1:]))

            parts.append("")  # sheet 间空行

        wb.close()

        full_text = "\n".join(parts)
        if not full_text.strip():
            logger.warning(f"Excel 文件无内容: {path.name}")
            return []

        return [Document(content=full_text, meta=meta)]

    def _convert_xls(self, path: Path, meta: dict) -> list[Document]:
        """使用 xlrd 读取 .xls（旧格式）"""
        try:
            import xlrd
        except ImportError:
            raise ImportError("请安装 xlrd: pip install xlrd")

        wb = xlrd.open_workbook(str(path))
        parts: list[str] = []

        for sheet_name in wb.sheet_names():
            ws = wb.sheet_by_name(sheet_name)
            parts.append(f"## Sheet: {sheet_name}")

            rows_text: list[str] = []
            for row_idx in range(ws.nrows):
                cells = [
                    (
                        str(ws.cell_value(row_idx, col_idx))
                        if ws.cell_type(row_idx, col_idx) != xlrd.XL_CELL_EMPTY
                        else ""
                    )
                    for col_idx in range(ws.ncols)
                ]
                if any(c.strip() for c in cells):
                    rows_text.append("| " + " | ".join(cells) + " |")

            if rows_text:
                header = rows_text[0]
                col_count = header.count("|") - 1
                sep = "| " + " | ".join(["---"] * col_count) + " |"
                parts.append("\n".join([header, sep] + rows_text[1:]))

            parts.append("")

        full_text = "\n".join(parts)
        if not full_text.strip():
            logger.warning(f"Excel 文件无内容: {path.name}")
            return []

        return [Document(content=full_text, meta=meta)]


# ─── Markdown 转换器 ─────────────────────────────────────────


class MarkdownConverter:
    """Markdown 文档转换器

    保留标题层级结构，确保分块时跨标题的内容不被割裂。
    """

    def convert(self, file_path: str, meta: dict | None = None) -> list[Document]:
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"文件不存在: {file_path}")

        base_meta = {
            "file_path": str(path),
            "file_type": "md",
            **(meta or {}),
        }

        text = path.read_text(encoding="utf-8")
        return [Document(content=text, meta=base_meta)]


# ─── HTML 转换器 ─────────────────────────────────────────────


class HTMLConverter:
    """HTML 文档转换器

    使用 BeautifulSoup 提取正文，去除导航/脚本/样式等非内容元素。
    fallback 到 Haystack HTMLToDocument。
    """

    def __init__(self):
        self._bs_available = False
        self._init_bs()

    def _init_bs(self) -> None:
        try:
            import bs4  # noqa: F401
            self._bs_available = True
        except ImportError:
            self._bs_available = False

    def convert(self, file_path: str, meta: dict | None = None) -> list[Document]:
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"文件不存在: {file_path}")

        base_meta = {
            "file_path": str(path),
            "file_type": "html",
            **(meta or {}),
        }

        if self._bs_available:
            return self._convert_with_bs(path, base_meta)
        else:
            return self._convert_with_haystack(path, base_meta)

    def _convert_with_bs(self, path: Path, meta: dict) -> list[Document]:
        """使用 BeautifulSoup 提取正文"""
        from bs4 import BeautifulSoup

        with open(path, encoding="utf-8") as f:
            soup = BeautifulSoup(f, "lxml")

        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()

        text = soup.get_text(separator="\n", strip=True)
        return [Document(content=text, meta=meta)]

    def _convert_with_haystack(self, path: Path, meta: dict) -> list[Document]:
        """使用 Haystack HTMLToDocument 作为 fallback"""
        from haystack.components.converters import HTMLToDocument
        from haystack.dataclasses import ByteStream

        converter = HTMLToDocument()
        byte_stream = ByteStream.from_file_path(str(path))
        result = converter.run(sources=[byte_stream], meta={"meta": meta})
        return result["documents"]


# ─── TXT 转换器 ─────────────────────────────────────────────


class TXTConverter:
    """纯文本文件转换器

    UTF-8 编码读取，保持原样输出。
    """

    def convert(self, file_path: str, meta: dict | None = None) -> list[Document]:
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"文件不存在: {file_path}")

        base_meta = {
            "file_path": str(path),
            "file_type": "txt",
            **(meta or {}),
        }

        # 尝试常见编码
        encodings = ["utf-8", "gbk", "gb2312", "gb18030", "utf-16"]
        for enc in encodings:
            try:
                text = path.read_text(encoding=enc)
                return [Document(content=text, meta=base_meta)]
            except (UnicodeDecodeError, UnicodeError):
                continue

        raise ValueError(f"无法解码文件（尝试了 utf-8/gbk/gb2312/gb18030/utf-16）: {file_path}")


# ─── 图片转换器 ─────────────────────────────────────────────


class ImageConverter:
    """图片文件转换器（JPG/PNG）

    使用 PaddleOCR 将图片中的文字识别提取为文本。
    自动检测编码 / 延迟加载 PaddleOCR 模型。
    """

    def __init__(self):
        self._paddle = None

    def _get_paddle(self):
        if self._paddle is None:
            self._paddle = PaddleOCRBackend()
        return self._paddle

    def convert(self, file_path: str, meta: dict | None = None) -> list[Document]:
        """转换图片为 Haystack Document（OCR 识别文字）"""
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"文件不存在: {file_path}")

        base_meta = {
            "file_path": str(path),
            "file_type": "image",
            **(meta or {}),
        }

        # 读取图片字节
        img_bytes = path.read_bytes()

        # PaddleOCR 识别
        paddle = self._get_paddle()
        text = paddle.recognize_page(img_bytes)

        if not text.strip():
            logger.warning(f"图片 OCR 未识别出文字: {path.name}")
            return []

        logger.info(f"图片 OCR 识别完成: {path.name} ({len(text)} 字符)")
        return [Document(content=text, meta=base_meta)]


# ─── 文件类型路由 ─────────────────────────────────────────────


class FileRouter:
    """文件类型自动路由 — 根据扩展名分发到对应的转换器"""

    def __init__(self, ocr_enabled: bool = False, ocr_backend: str = "auto"):
        self.pdf_converter = PDFConverter(
            ocr_enabled=ocr_enabled,
            ocr_backend=ocr_backend,
        )
        self.docx_converter = DOCXConverter()
        self.xlsx_converter = XLSXConverter()
        self.md_converter = MarkdownConverter()
        self.html_converter = HTMLConverter()
        self.txt_converter = TXTConverter()
        self.image_converter = ImageConverter()

    def convert(
        self, file_path: str, meta: dict | None = None
    ) -> tuple[str, list[Document]]:
        """自动转换文件

        Returns:
            (file_type, documents) 或 raise ValueError
        """
        file_type = detect_file_type(file_path)
        if file_type is None:
            raise ValueError(f"不支持的文件格式: {file_path}")

        converters = {
            "pdf": self.pdf_converter,
            "docx": self.docx_converter,
            "xlsx": self.xlsx_converter,
            "xls": self.xlsx_converter,
            "md": self.md_converter,
            "html": self.html_converter,
            "txt": self.txt_converter,
            "image": self.image_converter,
        }

        converter = converters[file_type]
        docs = converter.convert(file_path, meta)
        return file_type, docs

    def convert_many(
        self, file_paths: list[str], meta: dict | None = None
    ) -> dict[str, list[tuple[str, list[Document], str | None]]]:
        """批量转换多个文件

        Returns:
            {
                "success": [(file_type, [Document], file_path), ...],
                "skipped": [(file_path, reason), ...],
            }
        """
        result: dict[str, list] = {"success": [], "skipped": []}

        for fp in file_paths:
            try:
                file_type, docs = self.convert(fp, meta)
                result["success"].append((file_type, docs, fp))
            except (FileNotFoundError, ValueError) as e:
                result["skipped"].append((fp, str(e)))
                logger.warning(f"跳过文件 {fp}: {e}")

        return result  # type: ignore
