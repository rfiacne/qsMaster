"""Integration tests conftest — 加全局 timeout 避免卡死"""
import pytest

# 默认超时 15 秒
pytestmark = pytest.mark.timeout(15)

# 慢操作（PDF 索引管线）用 60 秒
def slow_test(timeout=60):
    return pytest.mark.timeout(timeout)


# def slow_test 在 conftest.py 中定义，pytest 自动发现
# 文件中使用: @pytest.mark.timeout(60)
