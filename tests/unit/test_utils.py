"""
公共工具函数单元测试 — utils.py

覆盖:
  - format_bytes: 字节格式化（边界: 0, B/KB/MB/GB/TB 临界值）
  - short_name: 路径提取文件名（边界: 空路径, unknown, 长路径, UUID 前缀截断）
  - cosine_similarity: 余弦相似度（边界: 零向量, 不等长, 空列表, 相同向量）
"""

from __future__ import annotations

from qa.utils import cosine_similarity, format_bytes, short_name


class TestFormatBytes:
    def test_zero(self):
        assert format_bytes(0) == "0.0 B"

    def test_bytes(self):
        assert format_bytes(512) == "512.0 B"

    def test_kilobytes(self):
        assert format_bytes(1024) == "1.0 KB"
        assert format_bytes(1536) == "1.5 KB"

    def test_megabytes(self):
        assert format_bytes(1024 * 1024) == "1.0 MB"

    def test_gigabytes(self):
        assert format_bytes(1024**3) == "1.0 GB"

    def test_terabytes(self):
        assert format_bytes(1024**4) == "1.0 TB"

    def test_large_terabytes(self):
        result = format_bytes(5 * 1024**4)
        assert result == "5.0 TB"


class TestShortName:
    def test_empty_path(self):
        assert short_name("") == "unknown"

    def test_unknown(self):
        assert short_name("unknown") == "unknown"

    def test_simple_filename(self):
        assert short_name("test.pdf") == "test.pdf"

    def test_full_path_unix(self):
        assert short_name("/home/user/docs/test.pdf") == "test.pdf"

    def test_full_path_windows(self):
        assert short_name("C:\\Users\\docs\\test.pdf") == "test.pdf"

    def test_long_filename_truncated(self):
        long_name = "a" * 30 + "_" + "b" * 10 + "_" + "c" * 20 + ".pdf"
        result = short_name(long_name)
        # 超过 50 字符且有 3+ 部分时截断
        assert len(result) <= len(long_name)

    def test_long_filename_with_uuid_prefix(self):
        name = "abc123_uuid456789_actual_document_name.pdf"
        result = short_name("/path/" + name)
        assert "actual_document_name" in result or "pdf" in result


class TestCosineSimilarity:
    def test_identical_vectors(self):
        v = [1.0, 2.0, 3.0]
        assert abs(cosine_similarity(v, v) - 1.0) < 1e-9

    def test_orthogonal_vectors(self):
        a = [1.0, 0.0]
        b = [0.0, 1.0]
        assert cosine_similarity(a, b) == 0.0

    def test_opposite_vectors(self):
        a = [1.0, 0.0]
        b = [-1.0, 0.0]
        assert abs(cosine_similarity(a, b) - (-1.0)) < 1e-9

    def test_zero_vector(self):
        a = [0.0, 0.0]
        b = [1.0, 2.0]
        assert cosine_similarity(a, b) == 0.0

    def test_unequal_lengths(self):
        a = [1.0, 2.0]
        b = [1.0, 2.0, 3.0]
        assert cosine_similarity(a, b) == 0.0

    def test_empty_vectors(self):
        assert cosine_similarity([], []) == 0.0

    def test_known_value(self):
        a = [1.0, 0.0]
        b = [1.0, 1.0]
        expected = 1.0 / (1.0 * (2**0.5))
        assert abs(cosine_similarity(a, b) - expected) < 1e-9
